"""Direct carrier tracking adapters used by the scheduled VivintOne poller."""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

_TOKENS: dict[str, tuple[str, float]] = {}


class CarrierError(RuntimeError):
    pass


def _request(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, body: Any = None, form: dict[str, str] | None = None) -> dict[str, Any]:
    request_headers = {"accept": "application/json", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        request_headers["content-type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        request_headers["content-type"] = "application/x-www-form-urlencoded"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=request_headers, method=method), timeout=10) as response:
            return json.loads(response.read().decode())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CarrierError("carrier_request_failed") from exc


def _cached_token(key: str) -> str | None:
    token = _TOKENS.get(key)
    return token[0] if token and token[1] > time.time() + 60 else None


def _store_token(key: str, payload: dict[str, Any]) -> str:
    token = str(payload.get("access_token") or "")
    if not token:
        raise CarrierError("carrier_authentication_failed")
    _TOKENS[key] = (token, time.time() + int(payload.get("expires_in", 3600)))
    return token


def _normalize(text: str) -> str:
    value = text.lower()
    if "delivered" in value:
        return "delivered"
    if "out for delivery" in value:
        return "out_for_delivery"
    if "return" in value and "sender" in value:
        return "return_to_sender"
    if any(term in value for term in ("exception", "failed", "unable", "delay")):
        return "exception"
    if any(term in value for term in ("transit", "departed", "arrived", "moving", "accepted", "acceptance", "picked up", "on the way")):
        return "in_transit"
    if any(term in value for term in ("label", "pre-shipment", "shipment ready")):
        return "pre_transit"
    return "unknown"


def tracking_url(carrier: str, number: str) -> str:
    encoded = urllib.parse.quote(number, safe="")
    return {
        "USPS": f"https://tools.usps.com/go/TrackConfirmAction?tLabels={encoded}",
        "UPS": f"https://www.ups.com/track?tracknum={encoded}",
        "FedEx": f"https://www.fedex.com/fedextrack/?trknbr={encoded}",
    }.get(carrier, "")


def _usps(number: str, credentials: dict[str, str]) -> dict[str, str]:
    token = _cached_token("usps") or _store_token("usps", _request("https://apis.usps.com/oauth2/v3/token", method="POST", body={"grant_type": "client_credentials", "client_id": credentials["clientId"], "client_secret": credentials["clientSecret"]}))
    result = _request("https://apis.usps.com/tracking/v3r2/tracking", method="POST", headers={"authorization": f"Bearer {token}"}, body=[{"trackingNumber": number}])
    detail = result[0] if isinstance(result, list) and result else {}
    description = str(detail.get("statusSummary") or detail.get("status") or detail.get("statusCategory") or "Unknown")
    return {"status": _normalize(f"{detail.get('statusCategory', '')} {description}"), "description": description}


def _ups(number: str, credentials: dict[str, str]) -> dict[str, str]:
    basic = base64.b64encode(f"{credentials['clientId']}:{credentials['clientSecret']}".encode()).decode()
    token = _cached_token("ups") or _store_token("ups", _request("https://onlinetools.ups.com/security/v1/oauth/token", method="POST", headers={"authorization": f"Basic {basic}"}, form={"grant_type": "client_credentials"}))
    result = _request(f"https://onlinetools.ups.com/api/track/v1/details/{urllib.parse.quote(number, safe='')}?locale=en_US&returnSignature=false&returnMilestones=false&returnPOD=false", headers={"authorization": f"Bearer {token}", "transId": str(uuid.uuid4()), "transactionSrc": "vivintone"})
    shipments = result.get("trackResponse", {}).get("shipment", [])
    package = (shipments[0].get("package") or [{}])[0] if shipments else {}
    current = package.get("currentStatus") or {}
    description = str(current.get("simplifiedTextDescription") or current.get("description") or package.get("statusDescription") or "Unknown")
    return {"status": _normalize(description), "description": description}


def _fedex(number: str, credentials: dict[str, str]) -> dict[str, str]:
    token = _cached_token("fedex") or _store_token("fedex", _request("https://apis.fedex.com/oauth/token", method="POST", form={"grant_type": "client_credentials", "client_id": credentials["clientId"], "client_secret": credentials["clientSecret"]}))
    result = _request("https://apis.fedex.com/track/v1/trackingnumbers", method="POST", headers={"authorization": f"Bearer {token}", "x-customer-transaction-id": str(uuid.uuid4())}, body={"includeDetailedScans": False, "trackingInfo": [{"trackingNumberInfo": {"trackingNumber": number}}]})
    complete = result.get("output", {}).get("completeTrackResults", [])
    track = (complete[0].get("trackResults") or [{}])[0] if complete else {}
    latest = track.get("latestStatusDetail") or {}
    description = str(latest.get("description") or latest.get("statusByLocale") or latest.get("code") or "Unknown")
    status = "delivered" if latest.get("code") == "DL" else _normalize(description)
    return {"status": status, "description": description}


def get_tracking(carrier: str, number: str, credentials: dict[str, Any]) -> dict[str, str]:
    provider = credentials.get(carrier.lower()) or {}
    if not provider.get("clientId") or not provider.get("clientSecret"):
        raise CarrierError("carrier_credentials_not_configured")
    try:
        if carrier == "USPS":
            result = _usps(number, provider)
        elif carrier == "UPS":
            result = _ups(number, provider)
        elif carrier == "FedEx":
            result = _fedex(number, provider)
        else:
            raise CarrierError("carrier_not_supported_for_automatic_tracking")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise CarrierError("carrier_response_invalid") from exc
    return {**result, "url": tracking_url(carrier, number)}
