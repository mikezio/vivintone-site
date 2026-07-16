import pathlib
import secrets
import sys
import unittest
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


class BackendHardeningTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
