import pathlib
import secrets
import sys
import unittest
import json
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import handler
from carriers import _normalize, tracking_url


class FakeTable:
    def __init__(self, item):
        self.item = item
        self.updates = []
        self.puts = []

    def get_item(self, **_kwargs):
        return {"Item": self.item}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)

    def put_item(self, **kwargs):
        self.puts.append(kwargs)

    def query(self, **_kwargs):
        return {"Items": []}


class OwnerTable(FakeTable):
    def __init__(self, item=None, allowed_owner_keys=None):
        super().__init__(item or {})
        self.allowed_owner_keys = set(allowed_owner_keys or [])

    def query(self, **kwargs):
        expression = kwargs.get("KeyConditionExpression")
        values = getattr(expression, "_values", ())
        matched = len(values) > 1 and values[1] in self.allowed_owner_keys
        return {"Items": [{"requestId": self.item.get("requestId", "VOH-1")}] if matched else []}


class HistoryTable(FakeTable):
    def __init__(self, events):
        super().__init__({})
        self.events = events

    def query(self, **_kwargs):
        return {"Items": self.events}


class IntentTable(FakeTable):
    def __init__(self):
        super().__init__({})
        self.records = {}

    def put_item(self, **kwargs):
        super().put_item(**kwargs)
        item = kwargs["Item"]
        self.records[(item["pk"], item["sk"])] = item

    def query(self, **_kwargs):
        return {"Items": []}

    def delete_item(self, **kwargs):
        record = self.records.get((kwargs["Key"]["pk"], kwargs["Key"]["sk"]))
        values = kwargs["ExpressionAttributeValues"]
        valid = bool(
            record
            and record["identityType"] == values[":kind"]
            and record["identityHash"] == values[":identity"]
            and record["expiresAt"] >= values[":now"]
        )
        if not valid:
            raise handler.ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException"}}, "DeleteItem"
            )
        del self.records[(kwargs["Key"]["pk"], kwargs["Key"]["sk"])]


class BackendHardeningTests(unittest.TestCase):
    def contributor_event(self, route, *, request_id="VOH-1", email="owner@example.com", verified="true", body=None):
        return {
            "routeKey": route,
            "pathParameters": {"requestId": request_id},
            "body": json.dumps(body or {}),
            "requestContext": {"authorizer": {"jwt": {"claims": {
                "email": email, "email_verified": verified,
            }}}},
        }

    def approved_item(self, **extra):
        token = "a" * 43
        item = {
            "pk": "REQUEST#VOH-1", "sk": "DETAIL", "requestId": "VOH-1",
            "status": "approved", "reimbursementTokenHash": handler._token_hash(token),
            "reimbursementTokenExpiresAt": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        }
        item.update(extra)
        return token, item

    def test_token_is_bounded_constant_time_and_expires(self):
        token, item = self.approved_item()
        self.assertTrue(handler._valid_reimbursement_token(item, token))
        self.assertFalse(handler._valid_reimbursement_token(item, token + "!"))
        item["reimbursementTokenExpiresAt"] = "2020-01-01T00:00:00Z"
        self.assertFalse(handler._valid_reimbursement_token(item, token))

    def test_receipt_upload_requires_shipping_but_not_delivery(self):
        token, item = self.approved_item()
        with self.assertRaisesRegex(RuntimeError, "shipping_must_be_started"):
            handler._receipt_upload(FakeTable(item), "VOH-1", {"token": token, "contentType": "image/png", "size": 10})
        item["trackingNumber"] = "1Z123456"
        with patch.object(handler, "_s3") as s3:
            s3.return_value.generate_presigned_post.return_value = {"url": "upload"}
            result = handler._receipt_upload(FakeTable(item), "VOH-1", {"token": token, "contentType": "image/png", "size": 10})
        self.assertTrue(result["ok"])

    def test_reimbursement_waiver_is_idempotent(self):
        token, item = self.approved_item(reimbursementStatus="not_requested")
        result = handler._waive_reimbursement(FakeTable(item), "VOH-1", {"token": token})
        self.assertEqual(result["status"], "not_requested")

    def test_prepaid_label_requires_admin_authorization(self):
        token, item = self.approved_item(prepaidLabelAllowed=False)
        with self.assertRaisesRegex(PermissionError, "not_authorized"):
            handler._request_rates(FakeTable(item), "VOH-1", {"token": token, "shippingDetailsAccurate": True})

    def test_label_purchase_enforces_authorized_cap(self):
        _, item = self.approved_item(
            prepaidLabelAllowed=True, labelMaxAmount="50.00", labelPurchaseState="purchasing",
            easyPostShipmentId="shp_1", prepaidRates=[{"id": "rate_1", "carrier": "UPS", "amount": "50.01", "currency": "USD"}],
        )
        with self.assertRaisesRegex(RuntimeError, "exceeds_authorized_cap"):
            handler._buy_label(FakeTable(item), "VOH-1", {"rateId": "rate_1", "confirmPurchase": True, "expectedAmount": "50.01", "expectedCarrier": "UPS"}, "admin")

    def test_public_label_result_is_allowlisted(self):
        _, item = self.approved_item(
            prepaidLabelAllowed=True, labelMaxAmount="50.00", labelPurchaseState="purchasing",
            easyPostShipmentId="shp_1", prepaidRates=[{"id": "rate_1", "carrier": "UPS", "service": "Ground", "amount": "12.00", "currency": "USD"}],
            email="a@example.com", name="A", adminNotes="must never leak",
        )
        table = FakeTable(item)
        bought = SimpleNamespace(postage_label=SimpleNamespace(label_url="https://label.example/1"), tracking_code="1Z123456")
        client = SimpleNamespace(shipment=SimpleNamespace(buy=lambda *_args, **_kwargs: bought))
        with patch.object(handler, "_easypost_client", return_value=client), patch.object(handler, "_store_private_label", return_value="labels/VOH-1/label.png"), patch.object(handler, "_label_download_url", return_value="https://s3.example/signed"), patch.object(handler, "_send_email"):
            result = handler._buy_label(table, "VOH-1", {"rateId": "rate_1", "confirmPurchase": True, "expectedAmount": "12.00", "expectedCarrier": "UPS"}, "automatic")
        self.assertEqual(set(result), {"ok", "labelUrl", "trackingNumber", "trackingUrl", "carrier", "service", "status"})
        self.assertNotIn("adminNotes", result)

    def test_generic_patch_cannot_bypass_reimbursement_gate(self):
        _, item = self.approved_item(status="received")
        with self.assertRaisesRegex(RuntimeError, "payment_endpoint"):
            handler._update_request(FakeTable(item), "VOH-1", {"status": "reimbursed"}, "admin")

    def test_paid_reimbursement_deletes_sensitive_artifacts(self):
        _, item = self.approved_item(
            status="received", reimbursementStatus="submitted", receiptKey="receipts/VOH-1/file.png",
            reimbursementAmount="12.00", paymentMethod="venmo", email="a@example.com", name="A", productName="Panel",
        )
        table = FakeTable(item)
        with patch.object(handler, "_s3") as s3, patch.object(handler, "_send_email"), patch.object(handler, "_request_with_history", return_value={}):
            handler._mark_reimbursed(table, "VOH-1", {}, "admin")
        s3.return_value.delete_object.assert_called_once()
        self.assertIn("REMOVE paymentDestination, receiptKey", table.updates[0]["UpdateExpression"])

    def test_direct_carrier_normalization_and_urls(self):
        self.assertEqual(_normalize("Out for delivery"), "out_for_delivery")
        self.assertEqual(_normalize("Delivered at front door"), "delivered")
        self.assertIn("ups.com", tracking_url("UPS", "1Z 123"))

    def test_exact_scheduled_event_contract(self):
        with patch.object(handler, "_table", return_value=object()), patch.object(handler, "_poll_direct_carriers", return_value={"ok": True, "polled": 0}) as poll:
            result = handler._handler({"source": "vivintone.tracking", "action": "poll_direct_carriers"}, None)
        self.assertTrue(result["ok"])
        poll.assert_called_once()

    def test_verified_contributor_claims_and_constant_time_ownership(self):
        identities = handler._contributor_claims(self.contributor_event("GET /api/portal/requests"))
        self.assertEqual(identities, {"email": "owner@example.com"})
        self.assertTrue(handler._owns_request({"email": "Owner@Example.com"}, identities))
        self.assertFalse(handler._owns_request({"email": "other@example.com"}, identities))
        phone_event = self.contributor_event("GET /api/portal/requests")
        phone_event["requestContext"]["authorizer"]["jwt"]["claims"] = {
            "phone_number": "+1 (212) 555-1212", "phone_number_verified": True,
        }
        self.assertEqual(handler._contributor_claims(phone_event), {"phone": "+12125551212"})

    def test_unverified_or_missing_identity_is_denied(self):
        with self.assertRaisesRegex(PermissionError, "verified_identity_required"):
            handler._contributor_claims(self.contributor_event("GET /api/portal/requests", verified="false"))
        with self.assertRaisesRegex(PermissionError, "verified_identity_required"):
            handler._contributor_claims({"requestContext": {"authorizer": {"jwt": {"claims": {}}}}})

    def test_wrong_verified_identity_cannot_read_request(self):
        item = {"requestId": "VOH-1", "email": "owner@example.com"}
        with self.assertRaisesRegex(LookupError, "request_not_found"):
            handler._portal_owned_request(FakeTable(item), "VOH-1", {"email": "other@example.com"})

    def test_request_submission_creates_hashed_owner_indexes(self):
        table = FakeTable({})
        body = {
            "safeToSubmit": True, "ownsHardware": True, "name": "Owner",
            "email": "Owner@Example.com", "phone": "+1 212 555 1212", "country": "US",
            "offerType": "donate", "productName": "Panel", "modelNumber": "VS-1",
            "quantity": 1, "condition": "used", "factoryReset": "yes",
            "removedFromAccount": "yes",
        }
        with patch.object(handler, "_setting", side_effect=lambda _t, _k, defaults: defaults), patch.object(handler, "_send_email"):
            result = handler._create_request(table, body)
        owner_items = [put["Item"] for put in table.puts if put["Item"].get("kind") == "request_owner"]
        self.assertEqual(len(owner_items), 2)
        serialized = json.dumps(owner_items)
        self.assertNotIn("owner@example.com", serialized.lower())
        self.assertNotIn("12125551212", serialized)
        self.assertTrue(all(item["requestId"] == result["reference"] for item in owner_items))

    def test_presignup_allows_existing_owner_and_denies_unknown_identity(self):
        identity = "owner@example.com"
        event = {"triggerSource": "PreSignUp_SignUp", "request": {"userAttributes": {"email": identity}}, "response": {}}
        table = OwnerTable(allowed_owner_keys={handler._owner_key("email", identity)})
        self.assertIs(handler._pre_signup(table, event), event)
        with self.assertRaisesRegex(PermissionError, "contributor_request_required"):
            handler._pre_signup(OwnerTable(), event)

    def test_presignup_export_uses_shared_table(self):
        event = {"request": {"userAttributes": {"email": "owner@example.com"}}, "response": {}}
        owner_key = handler._owner_key("email", "owner@example.com")
        with patch.object(handler, "_table", return_value=OwnerTable(allowed_owner_keys={owner_key})):
            self.assertIs(handler.presignup_handler(event, None), event)

    def test_portal_receipt_uses_opaque_handle_while_legacy_keeps_key(self):
        token, item = self.approved_item(trackingNumber="1Z123456")
        item["email"] = "owner@example.com"
        with patch.object(handler, "_s3") as s3, patch.object(handler.secrets, "token_hex", return_value="a" * 32):
            s3.return_value.generate_presigned_post.return_value = {"url": "https://upload.example", "fields": {}}
            portal = handler._receipt_upload(FakeTable(item), "VOH-1", {"contentType": "image/png", "size": 10}, {"email": "owner@example.com"})
            legacy = handler._receipt_upload(FakeTable(item), "VOH-1", {"token": token, "contentType": "image/png", "size": 10})
        self.assertEqual(portal["receiptId"], f"{'a' * 32}.png")
        self.assertNotIn("receiptKey", portal)
        self.assertIn("receiptKey", legacy)

    def test_portal_route_contracts_and_detail_allowlist(self):
        item = {
            "requestId": "VOH-1", "email": "owner@example.com", "status": "approved",
            "productName": "Panel", "receiptKey": "receipts/private", "labelKey": "labels/private",
            "paymentDestination": "secret", "adminNotes": "secret", "address": {"street1": "secret"},
            "reimbursementTokenHash": "secret",
        }
        routes = {
            "GET /api/portal/requests": ("_portal_requests", {"requests": []}, 200),
            "GET /api/portal/requests/{requestId}": ("_portal_owned_request", item, 200),
            "POST /api/portal/requests/{requestId}/tracking": ("_register_tracking", {"ok": True}, 200),
            "POST /api/portal/requests/{requestId}/rates": ("_request_rates", {"ok": True}, 200),
            "POST /api/portal/requests/{requestId}/label-url": ("_refresh_label_url", {"ok": True}, 200),
            "POST /api/portal/requests/{requestId}/reimbursement/upload": ("_receipt_upload", {"ok": True}, 200),
            "POST /api/portal/requests/{requestId}/reimbursement": ("_submit_reimbursement", {"ok": True}, 201),
            "POST /api/portal/requests/{requestId}/reimbursement/waive": ("_waive_reimbursement", {"ok": True}, 200),
        }
        for route, (helper_name, helper_result, expected_status) in routes.items():
            with self.subTest(route=route), patch.object(handler, "_table", return_value=FakeTable(item)), patch.object(handler, helper_name, return_value=helper_result):
                response = handler._handler(self.contributor_event(route), None)
            self.assertEqual(response["statusCode"], expected_status)
            payload = json.loads(response["body"])
            if route == "GET /api/portal/requests/{requestId}":
                self.assertEqual(payload["history"], [])
            self.assertNotIn("paymentDestination", json.dumps(payload))
            self.assertNotIn("receiptKey", json.dumps(payload))
            self.assertNotIn("adminNotes", json.dumps(payload))
            self.assertNotIn("reimbursementTokenHash", json.dumps(payload))

    def test_portal_history_is_allowlisted_and_actor_is_categorized(self):
        events = [
            {
                "action": "approve", "createdAt": "2026-07-16T12:00:00Z", "actor": "admin@example.com",
                "status": "approved", "message": "private arbitrary detail", "adminNotes": "secret",
                "email": "owner@example.com", "phone": "+12125551212", "receiptKey": "receipts/private",
                "paymentDestination": "secret", "address": {"street1": "secret"}, "token": "secret",
            },
            {"action": "tracking_submitted", "createdAt": "2026-07-16T13:00:00Z", "actor": "contributor"},
            {"action": "internal_secret_action", "createdAt": "2026-07-16T14:00:00Z", "actor": "admin"},
        ]
        history = handler._portal_request_history(HistoryTable(events), "VOH-1")
        self.assertEqual(history, [
            {"action": "tracking_submitted", "createdAt": "2026-07-16T13:00:00Z", "actorCategory": "contributor"},
            {"action": "approve", "createdAt": "2026-07-16T12:00:00Z", "actorCategory": "hardware_lab", "status": "approved"},
        ])
        serialized = json.dumps(history)
        for sensitive in ("admin@example.com", "owner@example.com", "+12125551212", "receiptKey", "paymentDestination", "street1", "token", "private arbitrary detail"):
            self.assertNotIn(sensitive, serialized)

    def test_portal_request_projection_omits_all_sensitive_fields(self):
        item = {
            "requestId": "VOH-1", "status": "approved", "productName": "Panel",
            "email": "owner@example.com", "phone": "+12125551212", "name": "Owner",
            "adminNotes": "secret", "paymentDestination": "secret", "receiptKey": "receipts/private",
            "labelKey": "labels/private", "labelPurchaseState": "purchased",
            "reimbursementTokenHash": "secret", "reimbursementTokenExpiresAt": "secret",
            "prepaidSenderSummary": {"city": "Secret City"}, "shippingAddress": {"street1": "Secret"},
            "address": {"street1": "Secret"}, "otherContributor": "secret",
        }
        projected = handler._portal_request(item)
        self.assertEqual(projected, {
            "requestId": "VOH-1", "status": "approved", "productName": "Panel",
            "labelPurchaseState": "purchased", "labelAvailable": True,
        })

    def test_verification_intent_hashes_identity_and_token_with_ten_minute_ttl(self):
        table = IntentTable()
        token = "v" * 43
        before = int(datetime.now(timezone.utc).timestamp())
        with patch.object(handler.secrets, "token_urlsafe", return_value=token):
            response = handler._create_verification_intent(
                table, {"identityType": "email", "identity": "Owner@Example.com"}
            )
        item = table.puts[0]["Item"]
        self.assertEqual(response, {"ok": True, "verificationIntentToken": token, "expiresIn": 600})
        self.assertEqual(item["intentTokenHash"], handler._token_hash(token))
        self.assertEqual(item["identityHash"], handler._identity_hash("owner@example.com"))
        self.assertGreaterEqual(item["expiresAt"], before + 600)
        self.assertLessEqual(item["expiresAt"], before + 601)
        serialized = json.dumps(item).lower()
        self.assertNotIn("owner@example.com", serialized)
        self.assertNotIn(token.lower(), serialized)

    def test_verification_intent_route_and_honeypot_contract(self):
        event = {"routeKey": "POST /api/verification-intents", "body": json.dumps({
            "identityType": "email", "identity": "owner@example.com",
        })}
        with patch.object(handler, "_table", return_value=IntentTable()), patch.object(handler.secrets, "token_urlsafe", return_value="i" * 43):
            response = handler._handler(event, None)
        self.assertEqual(response["statusCode"], 201)
        self.assertEqual(set(json.loads(response["body"])), {"ok", "verificationIntentToken", "expiresIn"})
        with self.assertRaisesRegex(ValueError, "invalid_submission"):
            handler._create_verification_intent(IntentTable(), {
                "identityType": "email", "identity": "owner@example.com", "website": "bot",
            })

    def test_email_intent_is_single_use_and_wrong_identity_does_not_consume(self):
        table = IntentTable()
        token = "e" * 43
        with patch.object(handler.secrets, "token_urlsafe", return_value=token):
            handler._create_verification_intent(table, {"identityType": "email", "identity": "owner@example.com"})
        wrong = {
            "request": {
                "userAttributes": {"email": "wrong@example.com"},
                "clientMetadata": {"verificationIntentToken": token},
            },
            "response": {},
        }
        with self.assertRaisesRegex(PermissionError, "contributor_request_required"):
            handler._pre_signup(table, wrong)
        correct = {
            "request": {
                "userAttributes": {"email": "owner@example.com"},
                "clientMetadata": {"verificationIntentToken": token},
            },
            "response": {},
        }
        self.assertIs(handler._pre_signup(table, correct), correct)
        with self.assertRaisesRegex(PermissionError, "contributor_request_required"):
            handler._pre_signup(table, correct)

    def test_expired_verification_intent_is_denied_and_not_consumed(self):
        table = IntentTable()
        token = "x" * 43
        token_hash = handler._token_hash(token)
        item = {
            "pk": f"VERIFICATION_INTENT#{token_hash}", "sk": "INTENT",
            "identityType": "email", "identityHash": handler._identity_hash("owner@example.com"),
            "expiresAt": int(datetime.now(timezone.utc).timestamp()) - 1,
        }
        table.records[(item["pk"], item["sk"])] = item
        self.assertFalse(handler._consume_verification_intent(table, token, "email", "owner@example.com"))
        self.assertIn((item["pk"], item["sk"]), table.records)

    def test_phone_intent_respects_feature_flag_and_phone_binding(self):
        with patch.object(handler, "SMS_ENABLED", False):
            with self.assertRaisesRegex(RuntimeError, "phone_verification_unavailable"):
                handler._create_verification_intent(IntentTable(), {"identityType": "phone", "identity": "+12125551212"})
        table = IntentTable()
        token = "p" * 43
        with patch.object(handler, "SMS_ENABLED", True), patch.object(handler.secrets, "token_urlsafe", return_value=token):
            handler._create_verification_intent(table, {"identityType": "phone", "identity": "+12125551212"})
            wrong = {
                "request": {
                    "userAttributes": {"phone_number": "+12125550000"},
                    "clientMetadata": {"verificationIntentToken": token},
                }, "response": {},
            }
            with self.assertRaisesRegex(PermissionError, "contributor_request_required"):
                handler._pre_signup(table, wrong)
            correct = {
                "request": {
                    "userAttributes": {"phone_number": "+12125551212"},
                    "clientMetadata": {"verificationIntentToken": token},
                }, "response": {},
            }
            self.assertIs(handler._pre_signup(table, correct), correct)

    def test_authenticated_email_and_phone_request_creation_contract(self):
        base_body = {
            "safeToSubmit": True, "ownsHardware": True, "name": "Owner", "country": "US",
            "offerType": "donate", "productName": "Panel", "modelNumber": "VS-1",
            "quantity": 1, "condition": "used", "factoryReset": "yes", "removedFromAccount": "yes",
        }
        email_event = self.contributor_event(
            "POST /api/portal/requests", body={**base_body, "loginIdentityType": "email"}
        )
        with patch.object(handler, "SMS_ENABLED", False), patch.object(handler, "_table", return_value=FakeTable({})), patch.object(handler, "_create_request", return_value={"ok": True, "reference": "VOH-1", "status": "submitted"}) as create:
            response = handler._handler(email_event, None)
        self.assertEqual(response["statusCode"], 201)
        self.assertEqual(create.call_args.args[1]["email"], "owner@example.com")
        self.assertEqual(create.call_args.kwargs["owner_identities"], {"email": "owner@example.com"})

        phone_event = self.contributor_event(
            "POST /api/portal/requests", body={**base_body, "loginIdentityType": "phone"}
        )
        phone_event["requestContext"]["authorizer"]["jwt"]["claims"] = {
            "phone_number": "+12125551212", "phone_number_verified": "true",
        }
        with patch.object(handler, "SMS_ENABLED", True), patch.object(handler, "_table", return_value=FakeTable({})), patch.object(handler, "_create_request", return_value={"ok": True, "reference": "VOH-2", "status": "submitted"}) as create:
            response = handler._handler(phone_event, None)
        self.assertEqual(response["statusCode"], 201)
        self.assertEqual(create.call_args.args[1]["phone"], "+12125551212")
        self.assertEqual(create.call_args.kwargs["owner_identities"], {"phone": "+12125551212"})
        self.assertFalse(create.call_args.kwargs["require_email"])

    def test_phone_only_request_is_stored_without_email_and_only_phone_owns_it(self):
        table = FakeTable({})
        body = {
            "safeToSubmit": True, "ownsHardware": True, "name": "Owner", "phone": "+12125551212",
            "country": "US", "offerType": "donate", "productName": "Panel", "modelNumber": "VS-1",
            "quantity": 1, "condition": "used", "factoryReset": "yes", "removedFromAccount": "yes",
        }
        with patch.object(handler, "_setting", side_effect=lambda _t, _k, defaults: defaults), patch.object(handler, "_send_email") as send:
            result = handler._create_request(
                table, body, owner_identities={"phone": "+12125551212"},
                require_email=False, actor="contributor",
            )
        detail = next(put["Item"] for put in table.puts if put["Item"].get("sk") == "DETAIL")
        owners = [put["Item"] for put in table.puts if put["Item"].get("kind") == "request_owner"]
        self.assertEqual(detail["email"], "")
        self.assertNotIn("ownerEmailHash", detail)
        self.assertEqual(detail["ownerPhoneHash"], handler._identity_hash("+12125551212"))
        self.assertEqual(len(owners), 1)
        self.assertTrue(handler._owns_request(detail, {"phone": "+12125551212"}))
        self.assertFalse(handler._owns_request(detail, {"email": "notice@example.com"}))
        self.assertEqual(result["status"], "submitted")
        # Only the admin notification is sent for a phone-only contributor.
        self.assertEqual(send.call_count, 1)

    def test_authenticated_identity_mismatch_and_disabled_phone_are_denied(self):
        body = {"loginIdentityType": "email", "email": "other@example.com"}
        event = self.contributor_event("POST /api/portal/requests", body=body)
        with patch.object(handler, "_table", return_value=FakeTable({})), patch.object(handler, "_create_request") as create:
            response = handler._handler(event, None)
        self.assertEqual(response["statusCode"], 403)
        self.assertEqual(json.loads(response["body"])["error"], "verified_identity_mismatch")
        create.assert_not_called()

        phone_event = self.contributor_event("POST /api/portal/requests", body={"loginIdentityType": "phone"})
        phone_event["requestContext"]["authorizer"]["jwt"]["claims"] = {
            "phone_number": "+12125551212", "phone_number_verified": True,
        }
        with patch.object(handler, "SMS_ENABLED", False), patch.object(handler, "_table", return_value=FakeTable({})):
            response = handler._handler(phone_event, None)
        self.assertEqual(response["statusCode"], 409)
        self.assertEqual(json.loads(response["body"])["error"], "phone_verification_unavailable")

    def test_legacy_public_submission_still_requires_email(self):
        body = {
            "safeToSubmit": True, "ownsHardware": True, "name": "Owner", "country": "US",
            "offerType": "donate", "productName": "Panel", "modelNumber": "VS-1",
            "condition": "used", "factoryReset": "yes", "removedFromAccount": "yes",
        }
        with self.assertRaisesRegex(ValueError, "required_field_missing"):
            handler.validate_submission(body)


if __name__ == "__main__":
    unittest.main()
