"""VivintOne project site and hardware contribution API.

Public routes accept bounded, non-secret hardware offers. Cognito-protected admin
routes manage the catalog, workflow, templates, and private shipping details.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import hashlib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

try:  # Lambda includes boto3; local validation imports helpers without AWS.
    import boto3
    from boto3.dynamodb.conditions import Key
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover - exercised only by dependency-free local checks
    boto3 = None
    Key = None
    ClientError = Exception

try:
    from aws_lambda_powertools import Logger, Metrics
    from aws_lambda_powertools.metrics import MetricUnit
except ImportError:  # pragma: no cover - local helper tests do not need Powertools
    Logger = Metrics = MetricUnit = None

try:
    import easypost
except ImportError:  # pragma: no cover - local helper tests do not call carriers
    easypost = None

try:
    from carriers import CarrierError, get_tracking, tracking_url
except ImportError:  # pragma: no cover - package-style local test import
    from .carriers import CarrierError, get_tracking, tracking_url


TABLE_NAME = os.getenv("TABLE_NAME", "")
SHIPPING_SECRET_ARN = os.getenv("SHIPPING_SECRET_ARN", "")
RECEIPTS_BUCKET = os.getenv("RECEIPTS_BUCKET", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "hardware@vivintone.mzio.dev")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "michael@mzio.dev")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
SITE_URL = os.getenv("SITE_URL", "https://vivintone.mzio.dev")
REGION = os.getenv("AWS_REGION", "us-east-1")
SMS_ENABLED = os.getenv("CONTRIBUTOR_SMS_ENABLED", "false").strip().casefold() == "true"

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()/#,+&-]{0,119}$")
URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)?$")
REQUEST_STATUSES = {"submitted", "reviewing", "on_hold", "info_requested", "approved", "declined", "received", "reimbursed", "closed"}
DECISIONS = {"approve", "decline", "hold", "request_info"}
PAYMENT_METHODS = {"venmo", "zelle", "paypal"}
RECEIPT_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}
LABEL_TYPES = {"image/png": ".png", "application/pdf": ".pdf"}
SUPPORTED_CARRIERS = {"USPS", "UPS", "FedEx"}
CONTRIBUTOR_TOKEN_DAYS = 180
DEFAULT_MAX_LABEL_AMOUNT = Decimal("50.00")
HARD_MAX_LABEL_AMOUNT = Decimal("100.00")
SETTINGS_SCHEMA_VERSION = 2
logger = Logger(service="vivintone-site") if Logger else None
metrics = Metrics(namespace="VivintOne", service="project-site") if Metrics else None

DEFAULT_PUBLIC_SETTINGS = {
    "title": "Help expand VivintOne hardware support",
    "intro": (
        "Offer an untested Vivint device for donation, loan, or remote testing. "
        "Every request is reviewed before shipping details are shared."
    ),
    "privacy": (
        "Do not submit Vivint credentials, serial numbers, QR codes, access keys, "
        "MAC addresses, alarm PINs, or shipping addresses."
    ),
    "acceptingOffers": True,
}

DEFAULT_TEMPLATES = {
    "receivedSubject": "VivintOne hardware request {reference} received",
    "receivedBody": (
        "Hi {name},\n\nThanks for offering {product} for VivintOne testing. "
        "Your request reference is {reference}. I will review the exact model and testing value "
        "before any shipping is arranged.\n\nDo not send a device until you receive an "
        "approval email with shipping instructions.\n\nMike\nVivintOne"
    ),
    "approvedSubject": "VivintOne hardware request {reference} approved",
    "approvedBody": (
        "Hi {name},\n\nYour offer for {product} has been approved.\n\n{message}\n\n"
        "Shipping instructions:\n{shipping}\n\nPlease remove the device from your Vivint "
        "account, factory-reset it when appropriate, and do not include documents or "
        "labels containing credentials, QR codes, or account information. Reply before "
        "shipping if anything is unclear.\n\nAfter shipping, submit the carrier receipt and "
        "reimbursement details through your private request link:\n{reimbursementUrl}\n\n"
        "Payment is sent only after the hardware is physically received.\n\n"
        "Mike\nVivintOne"
    ),
    "declinedSubject": "Update on VivintOne hardware request {reference}",
    "declinedBody": (
        "Hi {name},\n\nThank you for offering {product}. VivintOne cannot accept this "
        "hardware request right now.\n\n{message}\n\nPlease keep the device; no shipping "
        "information is needed. Your offer is still appreciated.\n\nMike\nVivintOne"
    ),
    "infoSubject": "More information needed for VivintOne request {reference}",
    "infoBody": "Hi {name},\n\nBefore I can review {product}, I need a little more information:\n\n{message}\n\nReply to this email without sending credentials, serial numbers, QR codes, or alarm details.\n\nMike\nVivintOne",
    "reimbursementSubject": "Shipping reimbursement received for {reference}",
    "reimbursementBody": "Hi {name},\n\nYour shipping reimbursement request for {product} was received under {reference}. Payment will be sent after I review it and physically confirm the hardware was received.\n\nMike\nVivintOne",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _response(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
        },
        "body": json.dumps(_json_safe(payload), separators=(",", ":")),
    }


def _text(value: Any, maximum: int, *, required: bool = False) -> str:
    normalized = " ".join(str(value or "").split())
    if required and not normalized:
        raise ValueError("required_field_missing")
    if len(normalized) > maximum:
        raise ValueError("field_too_long")
    return normalized


def _multiline(value: Any, maximum: int) -> str:
    normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(normalized) > maximum:
        raise ValueError("field_too_long")
    return normalized


def validate_submission(body: dict[str, Any], *, require_email: bool = True) -> dict[str, Any]:
    """Return a normalized public submission or raise a safe validation code."""
    if body.get("website"):
        raise ValueError("invalid_submission")
    if body.get("safeToSubmit") is not True or body.get("ownsHardware") is not True:
        raise ValueError("confirmation_required")

    name = _text(body.get("name"), 100, required=True)
    email = _text(body.get("email"), 254, required=require_email).lower()
    if email and not EMAIL_RE.fullmatch(email):
        raise ValueError("invalid_email")
    phone = _normalize_phone(body.get("phone")) if body.get("phone") else ""
    if phone and not PHONE_RE.fullmatch(phone):
        raise ValueError("invalid_phone")
    offer_type = _text(body.get("offerType"), 20, required=True)
    if offer_type not in {"donate", "loan", "remote_test", "unsure"}:
        raise ValueError("invalid_offer_type")

    model_number = _text(body.get("modelNumber"), 120, required=True)
    if not MODEL_RE.fullmatch(model_number):
        raise ValueError("invalid_model_number")

    quantity = int(body.get("quantity", 1))
    if quantity < 1 or quantity > 20:
        raise ValueError("invalid_quantity")

    return {
        "name": name,
        "email": email,
        "phone": phone,
        "country": _text(body.get("country"), 80, required=True),
        "offerType": offer_type,
        "catalogId": _text(body.get("catalogId"), 100),
        "productName": _text(body.get("productName"), 140, required=True),
        "modelNumber": model_number,
        "quantity": quantity,
        "condition": _text(body.get("condition"), 80, required=True),
        "factoryReset": _text(body.get("factoryReset"), 40, required=True),
        "removedFromAccount": _text(body.get("removedFromAccount"), 40, required=True),
        "accessories": _multiline(body.get("accessories"), 1000),
        "testingGoal": _multiline(body.get("testingGoal"), 2000),
        "notes": _multiline(body.get("notes"), 2000),
        "photosAvailable": bool(body.get("photosAvailable")),
    }


def _table():
    if boto3 is None:
        raise RuntimeError("boto3_unavailable")
    return boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)


def _ses():
    return boto3.client("sesv2", region_name=REGION)


def _secrets():
    return boto3.client("secretsmanager", region_name=REGION)


def _s3():
    return boto3.client("s3", region_name=REGION)


def _store_private_label(request_id: str, url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    hostname = str(parsed.hostname or "").lower()
    if parsed.scheme != "https" or not hostname.startswith("easypost-files.") or not hostname.endswith(".amazonaws.com"):
        raise RuntimeError("invalid_label_download_url")
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            content_type = str(response.headers.get_content_type()).lower()
            data = response.read(10 * 1024 * 1024 + 1)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("label_download_failed") from exc
    if len(data) > 10 * 1024 * 1024:
        raise RuntimeError("label_file_too_large")
    if content_type not in LABEL_TYPES:
        suffix = Path(parsed.path).suffix.lower()
        content_type = {".png": "image/png", ".pdf": "application/pdf"}.get(suffix, "")
    if content_type not in LABEL_TYPES:
        raise RuntimeError("unsupported_label_type")
    key = f"labels/{request_id}/label-{secrets.token_hex(12)}{LABEL_TYPES[content_type]}"
    _s3().put_object(Bucket=RECEIPTS_BUCKET, Key=key, Body=data, ContentType=content_type, ServerSideEncryption="AES256")
    return key


def _label_download_url(request_id: str, key: str) -> str:
    if not key.startswith(f"labels/{request_id}/"):
        raise RuntimeError("invalid_label_key")
    return _s3().generate_presigned_url("get_object", Params={"Bucket": RECEIPTS_BUCKET, "Key": key, "ResponseContentDisposition": f'attachment; filename="shipping-label-{request_id}"'}, ExpiresIn=900)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_email(value: Any) -> str:
    return str(value or "").strip().casefold()


def _normalize_phone(value: Any) -> str:
    raw = str(value or "").strip()
    digits = re.sub(r"[^0-9]", "", raw)
    return f"+{digits}" if digits else ""


def _claim_is_verified(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.casefold() == "true")


def _contributor_claims(event: dict[str, Any]) -> dict[str, str]:
    """Return only verified, normalized contributor identities from JWT claims."""
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    if not isinstance(claims, dict):
        raise PermissionError("verified_identity_required")
    identities: dict[str, str] = {}
    email = _normalize_email(claims.get("email"))
    if email and EMAIL_RE.fullmatch(email) and _claim_is_verified(claims.get("email_verified")):
        identities["email"] = email
    phone = _normalize_phone(claims.get("phone_number"))
    if phone and PHONE_RE.fullmatch(phone) and _claim_is_verified(claims.get("phone_number_verified")):
        identities["phone"] = phone
    if not identities:
        raise PermissionError("verified_identity_required")
    return identities


def _owner_key(kind: str, identity: str) -> str:
    return f"OWNER#{kind.upper()}#{_identity_hash(identity)}"


def _identity_hash(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _owns_request(item: dict[str, Any], identities: dict[str, str]) -> bool:
    """Constant-time match of a verified claim against normalized request identity."""
    matched = False
    request_email = _normalize_email(item.get("email"))
    request_phone = _normalize_phone(item.get("phone"))
    has_hashed_owners = bool(item.get("ownerEmailHash") or item.get("ownerPhoneHash"))
    if "email" in identities:
        expected_hash = str(item.get("ownerEmailHash") or "")
        if expected_hash:
            matched |= secrets.compare_digest(expected_hash, _identity_hash(identities["email"]))
        elif not has_hashed_owners:
            matched |= bool(request_email and secrets.compare_digest(request_email, identities["email"]))
    if "phone" in identities:
        expected_hash = str(item.get("ownerPhoneHash") or "")
        if expected_hash:
            matched |= secrets.compare_digest(expected_hash, _identity_hash(identities["phone"]))
        elif not has_hashed_owners:
            matched |= bool(request_phone and secrets.compare_digest(request_phone, identities["phone"]))
    return matched


def _valid_reimbursement_token(item: dict[str, Any], token: str) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,200}", token or ""):
        return False
    expires = str(item.get("reimbursementTokenExpiresAt") or "")
    if expires and expires <= _now():
        return False
    expected = str(item.get("reimbursementTokenHash") or "")
    supplied = _token_hash(token) if token else ""
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))


def _body(event: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_json") from exc
    if not isinstance(parsed, dict):
        raise ValueError("invalid_json")
    return parsed


def _admin_claims(event: dict[str, Any]) -> dict[str, str]:
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    username = claims.get("username") or claims.get("cognito:username")
    if username != ADMIN_USERNAME:
        raise PermissionError("admin_required")
    return claims


def _load_seed() -> list[dict[str, Any]]:
    return json.loads(Path(__file__).with_name("catalog_seed.json").read_text())


def _ensure_seed(table) -> None:
    for position, model in enumerate(_load_seed()):
        item = {
            "pk": "CATALOG",
            "sk": f"MODEL#{model['id']}",
            "kind": "catalog",
            "position": position,
            "updatedAt": _now(),
            **model,
        }
        try:
            table.put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
    _migrate_catalog_placeholders(table)
    _ensure_settings(table)


def _migrate_catalog_placeholders(table) -> None:
    """Replace retired seed placeholders without overwriting later catalog edits."""
    seed = {model["id"]: model for model in _load_seed()}
    migrations = {
        "panel-smart-hub-pro-gen2": {"Model identifier being confirmed"},
    }
    for model_id, old_values in migrations.items():
        key = {"pk": "CATALOG", "sk": f"MODEL#{model_id}"}
        item = table.get_item(Key=key).get("Item", {})
        if not item or item.get("modelNumber") not in old_values:
            continue
        position = item.get("position", 0)
        table.put_item(Item={**item, **seed[model_id], "position": position, "updatedAt": _now()})


def _ensure_settings(table) -> None:
    defaults = [
        ("PUBLIC", DEFAULT_PUBLIC_SETTINGS),
        ("TEMPLATES", DEFAULT_TEMPLATES),
    ]
    for key, values in defaults:
        try:
            table.put_item(
                Item={"pk": "SETTINGS", "sk": key, "kind": "settings", "schemaVersion": SETTINGS_SCHEMA_VERSION, "updatedAt": _now(), **values},
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
    _migrate_legacy_settings(table)


def _migrate_legacy_settings(table) -> None:
    """Replace retired public language once without overwriting later edits."""
    for key in ("PUBLIC", "TEMPLATES"):
        item = table.get_item(Key={"pk": "SETTINGS", "sk": key}).get("Item", {})
        if not item or int(item.get("schemaVersion", 0)) >= SETTINGS_SCHEMA_VERSION:
            continue
        if key == "TEMPLATES":
            for template_key, default_value in DEFAULT_TEMPLATES.items():
                if "Hardware Lab" in str(item.get(template_key, "")):
                    item[template_key] = default_value
        item["schemaVersion"] = SETTINGS_SCHEMA_VERSION
        item["updatedAt"] = _now()
        table.put_item(Item=item)


def _catalog(table) -> list[dict[str, Any]]:
    _ensure_seed(table)
    result = table.query(KeyConditionExpression=Key("pk").eq("CATALOG"))
    items = sorted(result.get("Items", []), key=lambda item: (item.get("position", 9999), item.get("productName", "")))
    return [{key: value for key, value in item.items() if key not in {"pk", "sk", "kind"}} for item in items]


def _setting(table, key: str, defaults: dict[str, Any]) -> dict[str, Any]:
    _ensure_settings(table)
    item = table.get_item(Key={"pk": "SETTINGS", "sk": key}).get("Item", {})
    return {**defaults, **{k: v for k, v in item.items() if k not in {"pk", "sk", "kind"}}}


def _render(template: str, values: dict[str, Any]) -> str:
    rendered = template
    for key in ("name", "reference", "product", "message", "shipping", "reimbursementUrl"):
        rendered = rendered.replace("{" + key + "}", str(values.get(key, "")))
    return rendered


def _send_email(to: str, subject: str, body: str, *, reply_to: str | None = None) -> None:
    payload: dict[str, Any] = {
        "FromEmailAddress": f"VivintOne <{FROM_EMAIL}>",
        "Destination": {"ToAddresses": [to]},
        "Content": {"Simple": {"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}}},
    }
    if reply_to:
        payload["ReplyToAddresses"] = [reply_to]
    _ses().send_email(**payload)


def _shipping_instructions() -> str:
    return str(_shipping_config().get("instructions") or "").strip()


def _put_shipping_instructions(value: str) -> None:
    config = _shipping_config()
    config["instructions"] = value
    _save_shipping_config(config)


def _shipping_config() -> dict[str, Any]:
    raw = _secrets().get_secret_value(SecretId=SHIPPING_SECRET_ARN).get("SecretString", "").strip()
    if not raw or raw == "CONFIGURE_IN_ADMIN":
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {"instructions": raw}


def _save_shipping_config(config: dict[str, Any]) -> None:
    _secrets().put_secret_value(SecretId=SHIPPING_SECRET_ARN, SecretString=json.dumps(config, separators=(",", ":")))


def _easypost_client():
    config = _shipping_config()
    key = str(config.get("easyPostApiKey") or "")
    if not key or easypost is None:
        raise RuntimeError("prepaid_labels_not_configured")
    return easypost.EasyPostClient(key)


def _request_item(table, request_id: str) -> dict[str, Any]:
    item = table.get_item(Key={"pk": f"REQUEST#{request_id}", "sk": "DETAIL"}, ConsistentRead=True).get("Item")
    if not item:
        raise LookupError("request_not_found")
    return item


def _create_request(
    table, body: dict[str, Any], *, owner_identities: dict[str, str] | None = None,
    require_email: bool = True, actor: str = "public",
) -> dict[str, Any]:
    public_settings = _setting(table, "PUBLIC", DEFAULT_PUBLIC_SETTINGS)
    if not public_settings.get("acceptingOffers", True):
        raise RuntimeError("submissions_paused")
    data = validate_submission(body, require_email=require_email)
    created_at = _now()
    request_id = f"VOH-{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(3).upper()}"
    expires = int((datetime.now(timezone.utc) + timedelta(days=730)).timestamp())
    item = {
        "pk": f"REQUEST#{request_id}",
        "sk": "DETAIL",
        "gsi1pk": "REQUESTS",
        "gsi1sk": f"{created_at}#{request_id}",
        "kind": "request",
        "requestId": request_id,
        "status": "submitted",
        "createdAt": created_at,
        "updatedAt": created_at,
        "expiresAt": expires,
        "adminNotes": "",
        **data,
    }
    if owner_identities is None:
        owner_identities = {"email": data["email"]}
        if data.get("phone"):
            owner_identities["phone"] = data["phone"]
    if owner_identities.get("email"):
        item["ownerEmailHash"] = _identity_hash(owner_identities["email"])
    if owner_identities.get("phone"):
        item["ownerPhoneHash"] = _identity_hash(owner_identities["phone"])
    table.put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
    for kind, identity in owner_identities.items():
        table.put_item(Item={
            "pk": _owner_key(kind, identity),
            "sk": f"REQUEST#{request_id}",
            "kind": "request_owner",
            "requestId": request_id,
            "createdAt": created_at,
            "expiresAt": expires,
        })
    if metrics:
        metrics.add_metric(name="HardwareRequestsCreated", unit=MetricUnit.Count, value=1)
    table.put_item(Item={
        "pk": f"REQUEST#{request_id}",
        "sk": f"EVENT#{created_at}#{secrets.token_hex(3)}",
        "kind": "audit",
        "action": "submitted",
        "actor": actor,
        "createdAt": created_at,
    })
    templates = _setting(table, "TEMPLATES", DEFAULT_TEMPLATES)
    values = {"name": data["name"], "reference": request_id, "product": data["productName"]}
    try:
        if data.get("email"):
            _send_email(data["email"], _render(templates["receivedSubject"], values), _render(templates["receivedBody"], values))
        admin_body = (
            f"New VivintOne hardware request {request_id}\n\n"
            f"{data['name']} offered {data['quantity']} × {data['productName']} ({data['modelNumber']}) "
            f"as {data['offerType']}.\n\nReview: {SITE_URL}/admin.html?request={request_id}"
        )
        _send_email(ADMIN_EMAIL, f"New VivintOne hardware request {request_id}", admin_body, reply_to=data.get("email") or None)
    except ClientError:
        table.update_item(
            Key={"pk": item["pk"], "sk": "DETAIL"},
            UpdateExpression="SET emailDelivery = :value",
            ExpressionAttributeValues={":value": "pending_or_failed"},
        )
    return {"ok": True, "reference": request_id, "status": "submitted"}


def _list_requests(table) -> list[dict[str, Any]]:
    result = table.query(
        IndexName="gsi1",
        KeyConditionExpression=Key("gsi1pk").eq("REQUESTS"),
        ScanIndexForward=False,
    )
    return [{key: value for key, value in item.items() if key not in {"pk", "sk", "gsi1pk", "gsi1sk"}} for item in result.get("Items", [])]


PORTAL_REQUEST_FIELDS = (
    "requestId", "status", "createdAt", "updatedAt", "offerType", "productName",
    "modelNumber", "quantity", "condition", "factoryReset", "removedFromAccount",
    "accessories", "testingGoal", "notes", "photosAvailable", "decisionMessage",
    "decidedAt", "reimbursementStatus", "reimbursementAmount", "reimbursementCurrency",
    "reimbursementSubmittedAt", "reimbursedAt", "shippingMethod", "shippingCarrier",
    "trackingNumber", "carrierStatus", "carrierStatusDescription", "carrierUpdatedAt",
    "trackingUrl", "trackingSubmittedAt", "prepaidLabelAllowed", "labelMaxAmount",
    "labelPurchaseState", "labelAmount", "labelPurchasedAt", "labelExpiresAt",
)
PORTAL_HISTORY_ACTIONS = {
    "submitted", "updated", "approve", "decline", "hold", "request_info",
    "tracking_submitted", "prepaid_label_requested", "prepaid_label_purchased",
    "prepaid_label_refunded", "reimbursement_not_requested",
    "reimbursement_submitted", "reimbursed",
}


def _portal_request(item: dict[str, Any]) -> dict[str, Any]:
    result = {key: item[key] for key in PORTAL_REQUEST_FIELDS if key in item}
    result["labelAvailable"] = bool(item.get("labelKey") and item.get("labelPurchaseState") == "purchased")
    return result


def _portal_request_history(table, request_id: str) -> list[dict[str, Any]]:
    """Return a contributor-safe projection of request audit events."""
    result = table.query(
        KeyConditionExpression=Key("pk").eq(f"REQUEST#{request_id}") & Key("sk").begins_with("EVENT#")
    )
    history = []
    for event in result.get("Items", []):
        action = str(event.get("action") or "")
        created_at = str(event.get("createdAt") or "")
        if action not in PORTAL_HISTORY_ACTIONS or not created_at:
            continue
        safe_event = {
            "action": action,
            "createdAt": created_at,
            "actorCategory": "contributor" if event.get("actor") in {"public", "contributor"} else "hardware_lab",
        }
        status = str(event.get("status") or "")
        if status in REQUEST_STATUSES:
            safe_event["status"] = status
        history.append(safe_event)
    return sorted(history, key=lambda event: event["createdAt"], reverse=True)


def _portal_owned_request(table, request_id: str, identities: dict[str, str]) -> dict[str, Any]:
    item = _request_item(table, request_id)
    if not _owns_request(item, identities):
        # Do not reveal whether another contributor's request exists.
        raise LookupError("request_not_found")
    return item


def _portal_requests(table, identities: dict[str, str]) -> list[dict[str, Any]]:
    request_ids: set[str] = set()
    for kind, identity in identities.items():
        result = table.query(KeyConditionExpression=Key("pk").eq(_owner_key(kind, identity)))
        request_ids.update(
            str(item.get("requestId")) for item in result.get("Items", []) if item.get("requestId")
        )
    requests = []
    for request_id in request_ids:
        try:
            item = _portal_owned_request(table, request_id, identities)
        except LookupError:
            continue
        requests.append(_portal_request(item))
    return sorted(requests, key=lambda item: str(item.get("createdAt") or ""), reverse=True)


def _verification_identity(body: dict[str, Any]) -> tuple[str, str]:
    identity_type = _text(body.get("identityType"), 10, required=True)
    raw_identity = _text(body.get("identity"), 254, required=True)
    if identity_type == "email":
        identity = _normalize_email(raw_identity)
        if not EMAIL_RE.fullmatch(identity):
            raise ValueError("invalid_email")
    elif identity_type == "phone":
        if not SMS_ENABLED:
            raise RuntimeError("phone_verification_unavailable")
        identity = raw_identity
        if not PHONE_RE.fullmatch(identity):
            raise ValueError("invalid_phone")
    else:
        raise ValueError("invalid_identity_type")
    return identity_type, identity


def _create_verification_intent(table, body: dict[str, Any]) -> dict[str, Any]:
    if body.get("website"):
        raise ValueError("invalid_submission")
    identity_type, identity = _verification_identity(body)
    token = secrets.token_urlsafe(32)
    token_hash = _token_hash(token)
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    expires_at = now_epoch + 600
    table.put_item(
        Item={
            "pk": f"VERIFICATION_INTENT#{token_hash}",
            "sk": "INTENT",
            "kind": "verification_intent",
            "intentTokenHash": token_hash,
            "identityType": identity_type,
            "identityHash": _identity_hash(identity),
            "createdAt": _now(),
            "expiresAt": expires_at,
        },
        ConditionExpression="attribute_not_exists(pk)",
    )
    return {"ok": True, "verificationIntentToken": token, "expiresIn": 600}


def _consume_verification_intent(table, token: str, identity_type: str, identity: str) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,200}", token or ""):
        return False
    try:
        table.delete_item(
            Key={"pk": f"VERIFICATION_INTENT#{_token_hash(token)}", "sk": "INTENT"},
            ConditionExpression="identityType = :kind AND identityHash = :identity AND expiresAt >= :now",
            ExpressionAttributeValues={
                ":kind": identity_type,
                ":identity": _identity_hash(identity),
                ":now": int(datetime.now(timezone.utc).timestamp()),
            },
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def _pre_signup(table, event: dict[str, Any]) -> dict[str, Any]:
    attributes = (event.get("request") or {}).get("userAttributes") or {}
    if not isinstance(attributes, dict):
        raise PermissionError("contributor_request_required")
    candidates: dict[str, str] = {}
    email = _normalize_email(attributes.get("email"))
    phone = _normalize_phone(attributes.get("phone_number"))
    if email and EMAIL_RE.fullmatch(email):
        candidates["email"] = email
    if phone and PHONE_RE.fullmatch(phone):
        candidates["phone"] = phone
    allowed = False
    for kind, identity in candidates.items():
        if kind == "phone" and not SMS_ENABLED:
            continue
        result = table.query(
            KeyConditionExpression=Key("pk").eq(_owner_key(kind, identity)),
            Limit=1,
        )
        allowed |= bool(result.get("Items"))
    if allowed:
        return event
    metadata = (event.get("request") or {}).get("clientMetadata") or {}
    intent_token = str(metadata.get("verificationIntentToken") or "") if isinstance(metadata, dict) else ""
    for kind, identity in candidates.items():
        if kind == "phone" and not SMS_ENABLED:
            continue
        if _consume_verification_intent(table, intent_token, kind, identity):
            return event
    raise PermissionError("contributor_request_required")


def _request_with_history(table, request_id: str) -> dict[str, Any]:
    detail = _request_item(table, request_id)
    result = table.query(
        KeyConditionExpression=Key("pk").eq(f"REQUEST#{request_id}") & Key("sk").begins_with("EVENT#")
    )
    return {
        "request": {key: value for key, value in detail.items() if key not in {"pk", "sk", "gsi1pk", "gsi1sk"}},
        "history": sorted(result.get("Items", []), key=lambda item: item.get("createdAt", ""), reverse=True),
    }


def _update_request(table, request_id: str, body: dict[str, Any], actor: str) -> dict[str, Any]:
    item = _request_item(table, request_id)
    status = _text(body.get("status", item.get("status")), 30, required=True)
    if status not in REQUEST_STATUSES:
        raise ValueError("invalid_status")
    if status == "reimbursed" and item.get("status") != "reimbursed":
        raise RuntimeError("use_reimbursement_payment_endpoint")
    if status == "received" and item.get("status") != "received" and body.get("physicalReceiptConfirmed") is not True:
        raise ValueError("physical_receipt_confirmation_required")
    notes = _multiline(body.get("adminNotes", item.get("adminNotes", "")), 6000)
    updated = _now()
    table.update_item(
        Key={"pk": item["pk"], "sk": "DETAIL"},
        UpdateExpression="SET #status = :status, adminNotes = :notes, updatedAt = :updated",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":status": status, ":notes": notes, ":updated": updated},
    )
    table.put_item(Item={
        "pk": item["pk"], "sk": f"EVENT#{updated}#{secrets.token_hex(3)}", "kind": "audit",
        "action": "updated", "status": status, "actor": actor, "createdAt": updated,
    })
    return _request_with_history(table, request_id)


def _decide(table, request_id: str, body: dict[str, Any], actor: str) -> dict[str, Any]:
    item = _request_item(table, request_id)
    decision = _text(body.get("decision"), 30, required=True)
    if decision not in DECISIONS:
        raise ValueError("invalid_decision")
    message = _multiline(body.get("message"), 4000)
    templates = _setting(table, "TEMPLATES", DEFAULT_TEMPLATES)
    status_map = {"approve": "approved", "decline": "declined", "hold": "on_hold", "request_info": "info_requested"}
    status = status_map[decision]
    shipping = ""
    reimbursement_token = ""
    if decision == "approve":
        shipping = _shipping_instructions()
        if not shipping:
            raise RuntimeError("shipping_instructions_required")
        reimbursement_token = secrets.token_urlsafe(32)
        prepaid_allowed = body.get("prepaidLabelAllowed") is True
        configured_cap = Decimal(str(_shipping_config().get("maxLabelAmount") or DEFAULT_MAX_LABEL_AMOUNT))
        requested_cap = min(configured_cap, HARD_MAX_LABEL_AMOUNT)
        if prepaid_allowed:
            try:
                requested_cap = Decimal(str(body.get("labelMaxAmount") or requested_cap))
            except InvalidOperation as exc:
                raise ValueError("invalid_label_max_amount") from exc
            if requested_cap <= 0 or requested_cap > min(configured_cap, HARD_MAX_LABEL_AMOUNT) or requested_cap.as_tuple().exponent < -2:
                raise ValueError("invalid_label_max_amount")
        subject_key, body_key = "approvedSubject", "approvedBody"
    elif decision == "decline":
        subject_key, body_key = "declinedSubject", "declinedBody"
    elif decision == "request_info":
        subject_key, body_key = "infoSubject", "infoBody"
    else:
        subject_key = body_key = ""

    values = {
        "name": item["name"], "reference": request_id, "product": item["productName"],
        "message": message or "No additional note was provided.", "shipping": shipping,
        "reimbursementUrl": f"{SITE_URL}/request.html?request={request_id}&token={reimbursement_token}",
    }
    if subject_key and item.get("email"):
        _send_email(item["email"], _render(templates[subject_key], values), _render(templates[body_key], values), reply_to=ADMIN_EMAIL)

    updated = _now()
    update_expression = "SET #status = :status, decisionMessage = :message, decidedAt = :updated, decidedBy = :actor, updatedAt = :updated"
    expression_values = {":status": status, ":message": message, ":updated": updated, ":actor": actor}
    if reimbursement_token:
        update_expression += ", reimbursementTokenHash = :token, reimbursementTokenExpiresAt = :tokenExpiry, reimbursementStatus = :reimbursement, prepaidLabelAllowed = :prepaidAllowed, labelMaxAmount = :labelMax"
        token_expiry = (datetime.now(timezone.utc) + timedelta(days=CONTRIBUTOR_TOKEN_DAYS)).isoformat(timespec="seconds").replace("+00:00", "Z")
        expression_values.update({":token": _token_hash(reimbursement_token), ":tokenExpiry": token_expiry, ":reimbursement": "awaiting_receipt", ":prepaidAllowed": prepaid_allowed, ":labelMax": requested_cap})
    table.update_item(
        Key={"pk": item["pk"], "sk": "DETAIL"},
        UpdateExpression=update_expression,
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues=expression_values,
    )
    table.put_item(Item={
        "pk": item["pk"], "sk": f"EVENT#{updated}#{secrets.token_hex(3)}", "kind": "audit",
        "action": decision, "status": status, "message": message,
        "actor": actor, "createdAt": updated,
    })
    return _request_with_history(table, request_id)


def _save_catalog(table, model_id: str, body: dict[str, Any]) -> dict[str, Any]:
    safe_id = re.sub(r"[^a-z0-9-]", "-", model_id.lower()).strip("-")
    if not safe_id or len(safe_id) > 100:
        raise ValueError("invalid_catalog_id")
    status = _text(body.get("status"), 40, required=True)
    if status not in {"verified_layers", "partial", "wanted", "model_verification_needed", "not_supported", "research"}:
        raise ValueError("invalid_catalog_status")
    source_url = _text(body.get("sourceUrl"), 500)
    if source_url and not URL_RE.fullmatch(source_url):
        raise ValueError("invalid_source_url")
    item = {
        "pk": "CATALOG", "sk": f"MODEL#{safe_id}", "kind": "catalog", "id": safe_id,
        "category": _text(body.get("category"), 80, required=True),
        "productName": _text(body.get("productName"), 140, required=True),
        "modelNumber": _text(body.get("modelNumber"), 140, required=True),
        "generation": _text(body.get("generation"), 80),
        "status": status,
        "hardwareWanted": bool(body.get("hardwareWanted")),
        "tested": _multiline(body.get("tested"), 3000),
        "needed": _multiline(body.get("needed"), 3000),
        "sourceUrl": source_url,
        "evidenceReviewedAt": _text(body.get("evidenceReviewedAt"), 20),
        "position": int(body.get("position", 999)),
        "updatedAt": _now(),
    }
    table.put_item(Item=item)
    return {key: value for key, value in item.items() if key not in {"pk", "sk", "kind"}}


def _admin_settings(table) -> dict[str, Any]:
    shipping = _shipping_config()
    return {
        "public": _setting(table, "PUBLIC", DEFAULT_PUBLIC_SETTINGS),
        "templates": _setting(table, "TEMPLATES", DEFAULT_TEMPLATES),
        "shippingInstructions": str(shipping.get("instructions") or ""),
        "shippingAddress": shipping.get("destinationAddress") or {},
        "easyPostConfigured": bool(shipping.get("easyPostApiKey")),
        "maxLabelAmount": str(shipping.get("maxLabelAmount") or DEFAULT_MAX_LABEL_AMOUNT),
        "autoRefundUnusedLabelsDays": int(shipping.get("autoRefundUnusedLabelsDays") or 0),
        "directCarrierConfigured": {carrier: bool((shipping.get("carrierCredentials") or {}).get(carrier, {}).get("clientId")) for carrier in ("usps", "ups", "fedex")},
    }


def _save_settings(table, body: dict[str, Any]) -> dict[str, Any]:
    public = body.get("public", {})
    templates = body.get("templates", {})
    normalized_public = {
        "title": _text(public.get("title"), 140, required=True),
        "intro": _multiline(public.get("intro"), 2000),
        "privacy": _multiline(public.get("privacy"), 2000),
        "acceptingOffers": bool(public.get("acceptingOffers")),
    }
    normalized_templates = {}
    for key in DEFAULT_TEMPLATES:
        maximum = 300 if key.endswith("Subject") else 8000
        normalized_templates[key] = _multiline(templates.get(key, DEFAULT_TEMPLATES[key]), maximum)
    updated = _now()
    table.put_item(Item={"pk": "SETTINGS", "sk": "PUBLIC", "kind": "settings", "schemaVersion": SETTINGS_SCHEMA_VERSION, "updatedAt": updated, **normalized_public})
    table.put_item(Item={"pk": "SETTINGS", "sk": "TEMPLATES", "kind": "settings", "schemaVersion": SETTINGS_SCHEMA_VERSION, "updatedAt": updated, **normalized_templates})
    shipping = _shipping_config()
    if "shippingInstructions" in body:
        shipping["instructions"] = _multiline(body.get("shippingInstructions"), 5000)
    if "shippingAddress" in body:
        candidate_address = body.get("shippingAddress") or {}
        if any(str(value or "").strip() for value in candidate_address.values()):
            shipping["destinationAddress"] = _validate_address(candidate_address)
    new_api_key = _text(body.get("easyPostApiKey"), 200)
    if new_api_key:
        shipping["easyPostApiKey"] = new_api_key
    if "maxLabelAmount" in body:
        try:
            maximum = Decimal(_text(body.get("maxLabelAmount"), 10, required=True))
        except InvalidOperation as exc:
            raise ValueError("invalid_max_label_amount") from exc
        if maximum <= 0 or maximum > HARD_MAX_LABEL_AMOUNT or maximum.as_tuple().exponent < -2:
            raise ValueError("invalid_max_label_amount")
        shipping["maxLabelAmount"] = str(maximum)
    if "autoRefundUnusedLabelsDays" in body:
        days = int(body.get("autoRefundUnusedLabelsDays") or 0)
        if days < 0 or days > 30:
            raise ValueError("invalid_auto_refund_days")
        shipping["autoRefundUnusedLabelsDays"] = days
    carrier_input = body.get("carrierCredentials") or {}
    credentials = shipping.get("carrierCredentials") or {}
    for carrier in ("usps", "ups", "fedex"):
        values = carrier_input.get(carrier) or {}
        client_id = _text(values.get("clientId"), 200)
        client_secret = _text(values.get("clientSecret"), 300)
        if client_id and client_secret:
            credentials[carrier] = {"clientId": client_id, "clientSecret": client_secret}
    shipping["carrierCredentials"] = credentials
    _save_shipping_config(shipping)
    return _admin_settings(table)


def _validate_address(value: dict[str, Any]) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("invalid_address")
    return {
        "name": _text(value.get("name"), 100, required=True),
        "company": _text(value.get("company"), 100),
        "street1": _text(value.get("street1"), 120, required=True),
        "street2": _text(value.get("street2"), 120),
        "city": _text(value.get("city"), 100, required=True),
        "state": _text(value.get("state"), 80, required=True),
        "zip": _text(value.get("zip"), 20, required=True),
        "country": _text(value.get("country", "US"), 2, required=True).upper(),
        "phone": _text(value.get("phone"), 30, required=True),
    }


def _require_contributor(
    table, request_id: str, body: dict[str, Any], identities: dict[str, str] | None = None
) -> dict[str, Any]:
    item = _request_item(table, request_id)
    if identities is not None:
        if not _owns_request(item, identities):
            raise LookupError("request_not_found")
    else:
        token = _text(body.get("token"), 200, required=True)
        if not _valid_reimbursement_token(item, token):
            raise PermissionError("invalid_or_expired_link")
    if item.get("status") not in {"approved", "received", "reimbursed", "closed"}:
        raise RuntimeError("request_not_approved")
    return item


def _register_tracking(table, request_id: str, body: dict[str, Any], identities: dict[str, str] | None = None) -> dict[str, Any]:
    item = _require_contributor(table, request_id, body, identities)
    if item.get("labelPurchaseState") in {"purchasing", "purchased"}:
        raise RuntimeError("prepaid_label_already_exists")
    carrier = _text(body.get("carrier"), 30, required=True)
    if carrier not in {"USPS", "UPS", "FedEx", "Other"}:
        raise ValueError("invalid_carrier")
    tracking_number = _text(body.get("trackingNumber"), 80, required=True)
    if not re.fullmatch(r"[A-Za-z0-9 -]{6,80}", tracking_number):
        raise ValueError("invalid_tracking_number")
    direct_url = tracking_url(carrier, tracking_number.replace(" ", ""))
    updated = _now()
    table.update_item(
        Key={"pk": item["pk"], "sk": "DETAIL"},
        UpdateExpression="SET shippingMethod = :method, shippingCarrier = :carrier, trackingNumber = :tracking, carrierStatus = :carrierStatus, trackingUrl = :url, trackingSubmittedAt = :updated, reimbursementRequested = :reimbursement, updatedAt = :updated",
        ExpressionAttributeValues={":method": "self_shipped", ":carrier": carrier, ":tracking": tracking_number.replace(" ", ""), ":carrierStatus": "registered", ":url": direct_url, ":reimbursement": body.get("reimbursementRequested") is True, ":updated": updated},
    )
    table.put_item(Item={"pk": item["pk"], "sk": f"EVENT#{updated}#{secrets.token_hex(3)}", "kind": "audit", "action": "tracking_submitted", "actor": "contributor", "createdAt": updated})
    return {"ok": True, "status": "registered", "trackingUrl": direct_url}


def _parcel(value: dict[str, Any]) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError("invalid_parcel")
    result = {}
    for key, maximum in {"length": 80, "width": 80, "height": 80, "weight": 2400}.items():
        try:
            number = Decimal(str(value.get(key)))
        except InvalidOperation as exc:
            raise ValueError("invalid_parcel") from exc
        if number <= 0 or number > maximum:
            raise ValueError("invalid_parcel")
        result[key] = float(number)
    return result


def _request_rates(table, request_id: str, body: dict[str, Any], identities: dict[str, str] | None = None) -> dict[str, Any]:
    item = _require_contributor(table, request_id, body, identities)
    if item.get("status") != "approved" or item.get("prepaidLabelAllowed") is not True:
        raise PermissionError("prepaid_label_not_authorized")
    if item.get("labelPurchaseState") in {"purchasing", "purchased"} or item.get("labelUrl") or item.get("labelPurchasedAt"):
        raise RuntimeError("prepaid_label_already_exists")
    if body.get("packageAccurate") is not True and body.get("shippingDetailsAccurate") is not True:
        raise ValueError("shipping_details_attestation_required")
    config = _shipping_config()
    destination = config.get("destinationAddress")
    if not destination:
        raise RuntimeError("prepaid_labels_not_configured")
    destination = _validate_address(destination)
    sender = _validate_address(body.get("fromAddress") or {})
    parcel = _parcel(body.get("parcel") or {})
    sender_summary = {key: sender[key] for key in ("city", "state", "zip", "country")}
    client = _easypost_client()
    verified_sender = client.address.create(**sender, verify=["delivery"])
    delivery_verification = getattr(getattr(verified_sender, "verifications", None), "delivery", None)
    if delivery_verification is not None and getattr(delivery_verification, "success", False) is not True:
        raise ValueError("sender_address_not_verified")
    shipment = client.shipment.create(to_address=destination, from_address=verified_sender, parcel=parcel)
    rates = sorted(
        (rate for rate in shipment.rates if str(rate.carrier) in SUPPORTED_CARRIERS and str(rate.currency).upper() == "USD"),
        key=lambda rate: Decimal(str(rate.rate)),
    )[:12]
    safe_rates = [{"id": rate.id, "carrier": str(rate.carrier), "service": str(rate.service), "amount": str(rate.rate), "currency": str(rate.currency), "deliveryDays": getattr(rate, "delivery_days", None)} for rate in rates]
    cap = min(Decimal(str(item.get("labelMaxAmount") or DEFAULT_MAX_LABEL_AMOUNT)), HARD_MAX_LABEL_AMOUNT)
    eligible = [rate for rate in safe_rates if Decimal(rate["amount"]) <= cap]
    updated = _now()
    if not eligible:
        table.update_item(Key={"pk": item["pk"], "sk": "DETAIL"}, UpdateExpression="SET labelPurchaseState = :review, prepaidRates = :rates, prepaidSenderSummary = :sender, prepaidParcel = :parcel, prepaidRequestedAt = :updated, updatedAt = :updated", ExpressionAttributeValues={":review": "review_required", ":rates": safe_rates, ":sender": sender_summary, ":parcel": parcel, ":updated": updated})
        try:
            _send_email(ADMIN_EMAIL, f"Prepaid label needs review for {request_id}", f"No eligible direct-carrier rate was within the authorized {cap} USD cap. No label was purchased.\n\nReview: {SITE_URL}/admin.html?request={request_id}", reply_to=item.get("email") or None)
        except ClientError:
            pass
        return {"ok": True, "status": "review_required", "reason": "no_eligible_rate_within_cap"}
    selected = eligible[0]
    table.update_item(
        Key={"pk": item["pk"], "sk": "DETAIL"},
        UpdateExpression="SET shippingMethod = :method, easyPostShipmentId = :shipment, prepaidRates = :rates, prepaidSenderSummary = :sender, prepaidParcel = :parcel, shippingDetailsAttestedAt = :updated, labelPurchaseState = :purchasing, selectedRateId = :rate, prepaidRequestedAt = :updated, updatedAt = :updated",
        ConditionExpression="#status = :approved AND prepaidLabelAllowed = :allowed AND (attribute_not_exists(labelPurchaseState) OR labelPurchaseState IN (:failed, :review))",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":method": "prepaid_label", ":shipment": shipment.id, ":rates": safe_rates, ":sender": sender_summary, ":parcel": parcel, ":updated": updated, ":purchasing": "purchasing", ":rate": selected["id"], ":approved": "approved", ":allowed": True, ":failed": "failed", ":review": "review_required"},
    )
    table.put_item(Item={"pk": item["pk"], "sk": f"EVENT#{updated}#{secrets.token_hex(3)}", "kind": "audit", "action": "prepaid_label_requested", "actor": "contributor", "createdAt": updated})
    return _buy_label(table, request_id, {"rateId": selected["id"], "confirmPurchase": True, "expectedAmount": selected["amount"], "expectedCarrier": selected["carrier"]}, "approved_automatic_purchase")


def _buy_label(table, request_id: str, body: dict[str, Any], actor: str) -> dict[str, Any]:
    item = _request_item(table, request_id)
    if item.get("status") != "approved" or item.get("prepaidLabelAllowed") is not True:
        raise PermissionError("prepaid_label_not_authorized")
    if body.get("confirmPurchase") is not True:
        raise ValueError("label_purchase_confirmation_required")
    rate_id = _text(body.get("rateId"), 100, required=True)
    rate = next((value for value in item.get("prepaidRates", []) if value.get("id") == rate_id), None)
    if not rate or not item.get("easyPostShipmentId"):
        raise ValueError("invalid_rate")
    amount = Decimal(str(rate["amount"]))
    cap = min(Decimal(str(item.get("labelMaxAmount") or DEFAULT_MAX_LABEL_AMOUNT)), HARD_MAX_LABEL_AMOUNT)
    manual_override = actor != "approved_automatic_purchase" and body.get("confirmOverCap") is True
    if amount > HARD_MAX_LABEL_AMOUNT or (amount > cap and not manual_override) or str(rate.get("currency", "")).upper() != "USD":
        raise RuntimeError("label_amount_exceeds_authorized_cap")
    if str(body.get("expectedAmount")) != str(rate["amount"]) or str(body.get("expectedCarrier")) != str(rate["carrier"]):
        raise ValueError("selected_rate_confirmation_mismatch")
    if item.get("labelPurchaseState") == "review_required" and manual_override:
        table.update_item(
            Key={"pk": item["pk"], "sk": "DETAIL"},
            UpdateExpression="SET labelPurchaseState = :purchasing, selectedRateId = :rate, labelOverrideApprovedBy = :actor, updatedAt = :updated",
            ConditionExpression="labelPurchaseState = :review",
            ExpressionAttributeValues={":purchasing": "purchasing", ":rate": rate_id, ":actor": actor, ":updated": _now(), ":review": "review_required"},
        )
    elif item.get("labelPurchaseState") != "purchasing":
        raise RuntimeError("prepaid_label_already_exists")
    try:
        shipment = _easypost_client().shipment.buy(item["easyPostShipmentId"], rate={"id": rate_id})
        label_key = _store_private_label(request_id, str(shipment.postage_label.label_url))
    except Exception:
        table.update_item(Key={"pk": item["pk"], "sk": "DETAIL"}, UpdateExpression="SET labelPurchaseState = :uncertain, updatedAt = :updated", ExpressionAttributeValues={":uncertain": "purchase_uncertain_review_required", ":updated": _now()})
        raise
    label_url = _label_download_url(request_id, label_key)
    carrier = str(rate["carrier"])
    direct_url = tracking_url(carrier, str(shipment.tracking_code))
    billing_warning = "USPS labels may be billed when created; void/refund unused labels promptly." if carrier == "USPS" else ""
    updated = _now()
    table.update_item(
        Key={"pk": item["pk"], "sk": "DETAIL"},
        UpdateExpression="SET shippingMethod = :method, shippingCarrier = :carrier, trackingNumber = :tracking, carrierStatus = :carrierStatus, trackingUrl = :trackingUrl, labelKey = :label, labelAmount = :amount, labelPurchaseState = :purchased, labelPurchasedAt = :updated, labelPurchasedBy = :actor, labelExpiresAt = :expires, labelBillingWarning = :warning, updatedAt = :updated REMOVE labelUrl",
        ExpressionAttributeValues={":method": "prepaid_label", ":carrier": carrier, ":tracking": str(shipment.tracking_code), ":carrierStatus": "pre_transit", ":trackingUrl": direct_url, ":label": label_key, ":amount": str(rate["amount"]), ":purchased": "purchased", ":updated": updated, ":actor": actor, ":expires": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(timespec="seconds").replace("+00:00", "Z"), ":warning": billing_warning},
    )
    table.put_item(Item={"pk": item["pk"], "sk": f"EVENT#{updated}#{secrets.token_hex(3)}", "kind": "audit", "action": "prepaid_label_purchased", "actor": actor, "createdAt": updated})
    warning_text = f"\n\nImportant: {billing_warning}" if billing_warning else ""
    if item.get("email"):
        _send_email(item["email"], f"Your prepaid shipping label for {request_id}", f"Hi {item['name']},\n\nYour prepaid {rate['carrier']} {rate['service']} label is ready:\n{label_url}\n\nTracking: {shipment.tracking_code}\n\nAttach the label securely and hand the package to the carrier. Tracking updates stay tied to {request_id}.{warning_text}\n\nMike\nVivintOne", reply_to=ADMIN_EMAIL)
    return {"ok": True, "labelUrl": label_url, "trackingNumber": str(shipment.tracking_code), "trackingUrl": direct_url, "carrier": carrier, "service": str(rate["service"]), "status": "label_purchased"}


def _refund_label(table, request_id: str, body: dict[str, Any], actor: str) -> dict[str, Any]:
    item = _request_item(table, request_id)
    if body.get("confirmRefund") is not True:
        raise ValueError("label_refund_confirmation_required")
    if item.get("labelPurchaseState") == "refunded":
        return _request_with_history(table, request_id)
    if item.get("labelPurchaseState") != "purchased" or not item.get("easyPostShipmentId"):
        raise RuntimeError("refundable_label_not_found")
    if item.get("carrierStatus") not in {"pre_transit", "registered", "unknown"}:
        raise RuntimeError("label_already_accepted_by_carrier")
    if item.get("labelKey"):
        _s3().delete_object(Bucket=RECEIPTS_BUCKET, Key=item["labelKey"])
    _easypost_client().shipment.refund(item["easyPostShipmentId"])
    updated = _now()
    table.update_item(Key={"pk": item["pk"], "sk": "DETAIL"}, UpdateExpression="SET labelPurchaseState = :refunded, labelRefundedAt = :updated, labelRefundedBy = :actor, updatedAt = :updated REMOVE labelUrl, labelKey", ExpressionAttributeValues={":refunded": "refunded", ":updated": updated, ":actor": actor})
    table.put_item(Item={"pk": item["pk"], "sk": f"EVENT#{updated}#{secrets.token_hex(3)}", "kind": "audit", "action": "prepaid_label_refunded", "actor": actor, "createdAt": updated})
    return _request_with_history(table, request_id)


def _refresh_label_url(table, request_id: str, body: dict[str, Any], identities: dict[str, str] | None = None) -> dict[str, Any]:
    item = _require_contributor(table, request_id, body, identities)
    if item.get("labelPurchaseState") != "purchased" or not item.get("labelKey"):
        raise LookupError("label_not_found")
    return {"ok": True, "labelUrl": _label_download_url(request_id, str(item["labelKey"])), "status": "label_purchased"}


def _apply_tracking_update(table, item: dict[str, Any], result: dict[str, str]) -> None:
    status = _text(result.get("status"), 40, required=True)
    description = _text(result.get("description"), 500)
    updated = _now()
    expression = "SET carrierStatus = :status, carrierStatusDescription = :description, carrierUpdatedAt = :updated, trackingUrl = :url, updatedAt = :updated"
    values = {":status": status, ":description": description, ":url": result.get("url", ""), ":updated": updated}
    first_delivery = status == "delivered" and item.get("carrierStatus") != "delivered"
    if first_delivery:
        expression += ", carrierDeliveredAt = :updated"
    table.update_item(Key={"pk": item["pk"], "sk": "DETAIL"}, UpdateExpression=expression, ExpressionAttributeValues=values)
    if first_delivery:
        note = " If you requested reimbursement, payment is normally sent within 12 hours after physical receipt is confirmed." if item.get("reimbursementStatus") == "submitted" else ""
        if item.get("email"):
            _send_email(item["email"], f"Carrier delivery update for {item['requestId']}", f"Hi {item['name']},\n\nThe carrier marked your package delivered. I will physically confirm the hardware was received before any requested reimbursement is sent.{note}\n\nMike\nVivintOne", reply_to=ADMIN_EMAIL)
        _send_email(ADMIN_EMAIL, f"Package delivered for {item['requestId']}", f"The carrier reports delivery. Confirm the hardware physically arrived before marking the request Received or releasing reimbursement.\n\nReview: {SITE_URL}/admin.html?request={item['requestId']}")


def _poll_direct_carriers(table) -> dict[str, Any]:
    config = _shipping_config()
    credentials = config.get("carrierCredentials") or {}
    items = []
    cursor = None
    for _page in range(5):
        query = {"IndexName": "gsi1", "KeyConditionExpression": Key("gsi1pk").eq("REQUESTS"), "Limit": 100}
        if cursor:
            query["ExclusiveStartKey"] = cursor
        response = table.query(**query)
        items.extend(response.get("Items", []))
        cursor = response.get("LastEvaluatedKey")
        if not cursor:
            break
    polled = updated_count = failed = refunded = 0
    auto_refund_days = int(config.get("autoRefundUnusedLabelsDays") or 0)
    for item in items:
        carrier = str(item.get("shippingCarrier") or "")
        number = str(item.get("trackingNumber") or "")
        if carrier not in SUPPORTED_CARRIERS or not number or item.get("carrierStatus") in {"delivered", "return_to_sender"} or item.get("status") in {"declined", "closed"}:
            continue
        polled += 1
        try:
            result = get_tracking(carrier, number, credentials)
            _apply_tracking_update(table, item, result)
            updated_count += 1
            purchased = str(item.get("labelPurchasedAt") or "")
            if auto_refund_days and item.get("labelPurchaseState") == "purchased" and result.get("status") == "pre_transit" and purchased:
                purchased_at = datetime.fromisoformat(purchased.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) - purchased_at >= timedelta(days=auto_refund_days):
                    _refund_label(table, item["requestId"], {"confirmRefund": True}, "scheduled_unused_label_refund")
                    refunded += 1
        except (CarrierError, RuntimeError, ValueError, ClientError) as exc:
            failed += 1
            table.update_item(Key={"pk": item["pk"], "sk": "DETAIL"}, UpdateExpression="SET trackingPollError = :error, trackingPolledAt = :updated", ExpressionAttributeValues={":error": str(exc), ":updated": _now()})
    return {"ok": True, "polled": polled, "updated": updated_count, "failed": failed, "refunded": refunded}


def _reimbursement_summary(table, request_id: str, token: str, identities: dict[str, str] | None = None) -> dict[str, Any]:
    item = _require_contributor(table, request_id, {"token": token}, identities)
    return {
        "ok": True,
        "request": {
            "requestId": request_id,
            "productName": item.get("productName"),
            "modelNumber": item.get("modelNumber"),
            "reimbursementStatus": item.get("reimbursementStatus", "awaiting_receipt"),
            "reimbursementAmount": item.get("reimbursementAmount"),
            "reimbursedAt": item.get("reimbursedAt"),
            "shippingMethod": item.get("shippingMethod"),
            "shippingCarrier": item.get("shippingCarrier"),
            "carrierStatus": item.get("carrierStatus"),
            "trackingUrl": item.get("trackingUrl"),
            "labelAvailable": bool(item.get("labelKey") and item.get("labelPurchaseState") == "purchased"),
        },
    }


def _waive_reimbursement(table, request_id: str, body: dict[str, Any], identities: dict[str, str] | None = None) -> dict[str, Any]:
    item = _require_contributor(table, request_id, body, identities)
    if item.get("reimbursementStatus") == "paid":
        raise RuntimeError("reimbursement_already_paid")
    if item.get("reimbursementStatus") == "not_requested":
        return {"ok": True, "reference": request_id, "status": "not_requested"}
    updated = _now()
    if item.get("receiptKey"):
        _s3().delete_object(Bucket=RECEIPTS_BUCKET, Key=item["receiptKey"])
    table.update_item(Key={"pk": item["pk"], "sk": "DETAIL"}, UpdateExpression="SET reimbursementStatus = :status, reimbursementWaivedAt = :updated, updatedAt = :updated REMOVE paymentDestination, receiptKey", ExpressionAttributeValues={":status": "not_requested", ":updated": updated})
    table.put_item(Item={"pk": item["pk"], "sk": f"EVENT#{updated}#{secrets.token_hex(3)}", "kind": "audit", "action": "reimbursement_not_requested", "actor": "contributor", "createdAt": updated})
    return {"ok": True, "reference": request_id, "status": "not_requested"}


def _receipt_upload(table, request_id: str, body: dict[str, Any], identities: dict[str, str] | None = None) -> dict[str, Any]:
    item = _require_contributor(table, request_id, body, identities)
    if item.get("reimbursementStatus") == "paid":
        raise RuntimeError("reimbursement_already_paid")
    if not item.get("trackingNumber"):
        raise RuntimeError("shipping_must_be_started_first")
    if item.get("reimbursementStatus") == "not_requested":
        raise RuntimeError("reimbursement_not_requested")
    content_type = _text(body.get("contentType"), 100, required=True)
    if content_type not in RECEIPT_TYPES:
        raise ValueError("unsupported_receipt_type")
    size = int(body.get("size", 0))
    if size < 1 or size > 10 * 1024 * 1024:
        raise ValueError("invalid_receipt_size")
    key = f"receipts/{request_id}/{secrets.token_hex(16)}{RECEIPT_TYPES[content_type]}"
    upload = _s3().generate_presigned_post(
        Bucket=RECEIPTS_BUCKET,
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, 10 * 1024 * 1024]],
        ExpiresIn=600,
    )
    if identities is not None:
        # Portal clients use a request-scoped opaque handle, not the stored DynamoDB key.
        return {"ok": True, "receiptId": key.rsplit("/", 1)[-1], "upload": upload}
    return {"ok": True, "receiptKey": key, "upload": upload}


def _submit_reimbursement(table, request_id: str, body: dict[str, Any], identities: dict[str, str] | None = None) -> dict[str, Any]:
    item = _require_contributor(table, request_id, body, identities)
    if item.get("reimbursementStatus") == "paid":
        raise RuntimeError("reimbursement_already_paid")
    if not item.get("trackingNumber"):
        raise RuntimeError("shipping_must_be_started_first")
    if item.get("reimbursementStatus") == "not_requested":
        raise RuntimeError("reimbursement_not_requested")
    if identities is not None:
        receipt_id = _text(body.get("receiptId"), 80, required=True)
        if not re.fullmatch(r"[a-f0-9]{32}\.(?:jpg|png|pdf)", receipt_id):
            raise ValueError("invalid_receipt")
        receipt_key = f"receipts/{request_id}/{receipt_id}"
    else:
        receipt_key = _text(body.get("receiptKey"), 300, required=True)
    if not receipt_key.startswith(f"receipts/{request_id}/"):
        raise ValueError("invalid_receipt")
    try:
        _s3().head_object(Bucket=RECEIPTS_BUCKET, Key=receipt_key)
    except ClientError as exc:
        raise ValueError("receipt_not_uploaded") from exc
    try:
        amount = Decimal(_text(body.get("amount"), 12, required=True))
    except InvalidOperation as exc:
        raise ValueError("invalid_amount") from exc
    if amount <= 0 or amount > Decimal("500.00") or amount.as_tuple().exponent < -2:
        raise ValueError("invalid_amount")
    method = _text(body.get("paymentMethod"), 20, required=True)
    if method not in PAYMENT_METHODS:
        raise ValueError("invalid_payment_method")
    destination = _text(body.get("paymentDestination"), 254, required=True)
    carrier = _text(body.get("carrier"), 30, required=True)
    if carrier not in {"USPS", "UPS", "FedEx", "Other"}:
        raise ValueError("invalid_carrier")
    updated = _now()
    table.update_item(
        Key={"pk": item["pk"], "sk": "DETAIL"},
        UpdateExpression="SET reimbursementStatus = :status, reimbursementAmount = :amount, reimbursementCurrency = :currency, paymentMethod = :method, paymentDestination = :destination, shippingCarrier = :carrier, receiptKey = :receipt, reimbursementSubmittedAt = :updated, updatedAt = :updated",
        ExpressionAttributeValues={":status": "submitted", ":amount": amount, ":currency": "USD", ":method": method, ":destination": destination, ":carrier": carrier, ":receipt": receipt_key, ":updated": updated},
    )
    table.put_item(Item={"pk": item["pk"], "sk": f"EVENT#{updated}#{secrets.token_hex(3)}", "kind": "audit", "action": "reimbursement_submitted", "actor": "contributor", "createdAt": updated})
    templates = _setting(table, "TEMPLATES", DEFAULT_TEMPLATES)
    values = {"name": item["name"], "reference": request_id, "product": item["productName"]}
    try:
        if item.get("email"):
            _send_email(item["email"], _render(templates["reimbursementSubject"], values), _render(templates["reimbursementBody"], values), reply_to=ADMIN_EMAIL)
        _send_email(ADMIN_EMAIL, f"Shipping reimbursement submitted for {request_id}", f"A {amount} USD {carrier} shipping reimbursement is ready for review. Payment stays locked until the hardware is marked physically received.\n\nReview: {SITE_URL}/admin.html?request={request_id}", reply_to=item.get("email") or None)
    except ClientError:
        pass
    return {"ok": True, "reference": request_id, "status": "submitted"}


def _receipt_download(table, request_id: str) -> dict[str, Any]:
    item = _request_item(table, request_id)
    key = item.get("receiptKey")
    if not key:
        raise LookupError("receipt_not_found")
    return {"ok": True, "url": _s3().generate_presigned_url("get_object", Params={"Bucket": RECEIPTS_BUCKET, "Key": key, "ResponseContentDisposition": f'attachment; filename="receipt-{request_id}"'}, ExpiresIn=300)}


def _mark_reimbursed(table, request_id: str, body: dict[str, Any], actor: str) -> dict[str, Any]:
    item = _request_item(table, request_id)
    if item.get("status") != "received":
        raise RuntimeError("hardware_must_be_physically_received_first")
    if item.get("reimbursementStatus") != "submitted":
        raise RuntimeError("reimbursement_not_ready")
    note = _multiline(body.get("note"), 2000)
    updated = _now()
    if item.get("receiptKey"):
        _s3().delete_object(Bucket=RECEIPTS_BUCKET, Key=item["receiptKey"])
    table.update_item(
        Key={"pk": item["pk"], "sk": "DETAIL"},
        UpdateExpression="SET #status = :status, reimbursementStatus = :paid, reimbursedAt = :updated, reimbursementPaidBy = :actor, reimbursementNote = :note, updatedAt = :updated REMOVE paymentDestination, receiptKey",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":status": "reimbursed", ":paid": "paid", ":updated": updated, ":actor": actor, ":note": note},
    )
    table.put_item(Item={"pk": item["pk"], "sk": f"EVENT#{updated}#{secrets.token_hex(3)}", "kind": "audit", "action": "reimbursed", "actor": actor, "createdAt": updated})
    try:
        if item.get("email"):
            _send_email(item["email"], f"Shipping reimbursement sent for {request_id}", f"Hi {item['name']},\n\nYour {item['reimbursementAmount']} USD shipping reimbursement for {item['productName']} has been marked paid through {item['paymentMethod'].title()}.\n\n{note}\n\nMike\nVivintOne", reply_to=ADMIN_EMAIL)
    except ClientError:
        pass
    return _request_with_history(table, request_id)


def _handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    table = _table()
    if event.get("source") == "vivintone.tracking" and event.get("action") == "poll_direct_carriers":
        return _poll_direct_carriers(table)
    route = event.get("routeKey", "")
    params = event.get("pathParameters") or {}
    try:
        if route == "GET /api/catalog":
            return _response(200, {"ok": True, "models": _catalog(table)})
        if route == "GET /api/public-settings":
            return _response(200, {"ok": True, **_setting(table, "PUBLIC", DEFAULT_PUBLIC_SETTINGS)})
        if route == "POST /api/verification-intents":
            return _response(201, _create_verification_intent(table, _body(event)))
        if route == "POST /api/requests":
            return _response(201, _create_request(table, _body(event)))
        if route == "GET /api/reimbursements/{requestId}":
            token = (event.get("queryStringParameters") or {}).get("token", "")
            return _response(200, _reimbursement_summary(table, params["requestId"], token))
        if route == "POST /api/reimbursements/{requestId}/upload":
            return _response(200, _receipt_upload(table, params["requestId"], _body(event)))
        if route == "POST /api/reimbursements/{requestId}":
            return _response(201, _submit_reimbursement(table, params["requestId"], _body(event)))
        if route == "POST /api/reimbursements/{requestId}/waive":
            return _response(200, _waive_reimbursement(table, params["requestId"], _body(event)))
        if route == "POST /api/shipments/{requestId}/tracking":
            return _response(200, _register_tracking(table, params["requestId"], _body(event)))
        if route == "POST /api/shipments/{requestId}/rates":
            return _response(200, _request_rates(table, params["requestId"], _body(event)))
        if route == "POST /api/shipments/{requestId}/label-url":
            return _response(200, _refresh_label_url(table, params["requestId"], _body(event)))
        if route.startswith("GET /api/portal/") or route.startswith("POST /api/portal/"):
            identities = _contributor_claims(event)
            if route == "GET /api/portal/requests":
                return _response(200, {"ok": True, "requests": _portal_requests(table, identities)})
            if route == "POST /api/portal/requests":
                body = _body(event)
                identity_type = _text(body.get("loginIdentityType"), 10, required=True)
                if identity_type == "phone" and not SMS_ENABLED:
                    raise RuntimeError("phone_verification_unavailable")
                if identity_type not in {"email", "phone"}:
                    raise ValueError("invalid_identity_type")
                verified_identity = identities.get(identity_type)
                if not verified_identity:
                    raise PermissionError("verified_identity_required")
                supplied_identity = body.get(identity_type)
                if supplied_identity:
                    normalized_supplied = (
                        _normalize_email(supplied_identity) if identity_type == "email"
                        else _normalize_phone(supplied_identity)
                    )
                    if not secrets.compare_digest(normalized_supplied, verified_identity):
                        raise PermissionError("verified_identity_mismatch")
                body[identity_type] = verified_identity
                return _response(201, _create_request(
                    table, body, owner_identities={identity_type: verified_identity},
                    require_email=identity_type == "email", actor="contributor",
                ))
            request_id = params.get("requestId", "")
            if route == "GET /api/portal/requests/{requestId}":
                item = _portal_owned_request(table, request_id, identities)
                history = _portal_request_history(table, request_id)
                return _response(200, {"ok": True, "request": _portal_request(item), "history": history})
            if route == "POST /api/portal/requests/{requestId}/tracking":
                return _response(200, _register_tracking(table, request_id, _body(event), identities))
            if route == "POST /api/portal/requests/{requestId}/rates":
                return _response(200, _request_rates(table, request_id, _body(event), identities))
            if route == "POST /api/portal/requests/{requestId}/label-url":
                return _response(200, _refresh_label_url(table, request_id, _body(event), identities))
            if route == "POST /api/portal/requests/{requestId}/reimbursement/upload":
                return _response(200, _receipt_upload(table, request_id, _body(event), identities))
            if route == "POST /api/portal/requests/{requestId}/reimbursement":
                return _response(201, _submit_reimbursement(table, request_id, _body(event), identities))
            if route == "POST /api/portal/requests/{requestId}/reimbursement/waive":
                return _response(200, _waive_reimbursement(table, request_id, _body(event), identities))
            return _response(404, {"ok": False, "error": "not_found"})
        claims = _admin_claims(event)
        actor = claims.get("username") or claims.get("cognito:username") or "admin"
        if route == "GET /api/admin/requests":
            return _response(200, {"ok": True, "requests": _list_requests(table)})
        if route == "GET /api/admin/requests/{requestId}":
            return _response(200, {"ok": True, **_request_with_history(table, params["requestId"])})
        if route == "PATCH /api/admin/requests/{requestId}":
            return _response(200, {"ok": True, **_update_request(table, params["requestId"], _body(event), actor)})
        if route == "POST /api/admin/requests/{requestId}/decision":
            return _response(200, {"ok": True, **_decide(table, params["requestId"], _body(event), actor)})
        if route == "POST /api/admin/requests/{requestId}/receipt-url":
            return _response(200, _receipt_download(table, params["requestId"]))
        if route == "POST /api/admin/requests/{requestId}/reimbursed":
            return _response(200, {"ok": True, **_mark_reimbursed(table, params["requestId"], _body(event), actor)})
        if route == "POST /api/admin/requests/{requestId}/label":
            return _response(200, {"ok": True, **_buy_label(table, params["requestId"], _body(event), actor)})
        if route == "POST /api/admin/requests/{requestId}/label/refund":
            return _response(200, {"ok": True, **_refund_label(table, params["requestId"], _body(event), actor)})
        if route == "GET /api/admin/catalog":
            return _response(200, {"ok": True, "models": _catalog(table)})
        if route in {"POST /api/admin/catalog", "PUT /api/admin/catalog/{modelId}"}:
            body = _body(event)
            model_id = params.get("modelId") or body.get("id", "")
            return _response(200, {"ok": True, "model": _save_catalog(table, model_id, body)})
        if route == "DELETE /api/admin/catalog/{modelId}":
            model_id = re.sub(r"[^a-z0-9-]", "-", params["modelId"].lower()).strip("-")
            if not model_id:
                raise ValueError("invalid_catalog_id")
            table.delete_item(Key={"pk": "CATALOG", "sk": f"MODEL#{model_id}"})
            return _response(200, {"ok": True})
        if route == "GET /api/admin/settings":
            return _response(200, {"ok": True, **_admin_settings(table)})
        if route == "PUT /api/admin/settings":
            return _response(200, {"ok": True, **_save_settings(table, _body(event))})
        return _response(404, {"ok": False, "error": "not_found"})
    except ValueError as exc:
        return _response(400, {"ok": False, "error": str(exc)})
    except PermissionError as exc:
        return _response(403, {"ok": False, "error": str(exc)})
    except LookupError as exc:
        return _response(404, {"ok": False, "error": str(exc)})
    except RuntimeError as exc:
        return _response(409, {"ok": False, "error": str(exc)})
    except ClientError:
        if logger:
            logger.exception("AWS service request failed")
        return _response(503, {"ok": False, "error": "service_unavailable"})
    except Exception:
        if logger:
            logger.exception("Unhandled request failure")
        return _response(500, {"ok": False, "error": "internal_error"})


if logger and metrics:
    handler = logger.inject_lambda_context(log_event=False)(
        metrics.log_metrics(capture_cold_start_metric=True)(_handler)
    )
else:
    handler = _handler


def presignup_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Cognito PreSignUp trigger: admit only identities with an existing request."""
    return _pre_signup(_table(), event)
