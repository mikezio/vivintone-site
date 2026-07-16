import json
import unittest
from decimal import Decimal
from pathlib import Path

from functions.api import handler


VALID_SUBMISSION = {
    "safeToSubmit": True,
    "ownsHardware": True,
    "name": "  Test   Contributor ",
    "email": "TEST@example.com",
    "country": "US",
    "offerType": "donate",
    "productName": "Doorbell Camera Pro",
    "modelNumber": "VS-DBC350-WHT",
    "quantity": 1,
    "condition": "working",
    "factoryReset": "yes",
    "removedFromAccount": "yes",
}


class FakeTable:
    def __init__(self):
        self.items = []

    def put_item(self, **kwargs):
        self.items.append(kwargs["Item"])
        return {}


class FakeSettingsTable:
    def __init__(self, items):
        self.items = {(item["pk"], item["sk"]): dict(item) for item in items}

    def get_item(self, Key):
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item, **_kwargs):
        self.items[(Item["pk"], Item["sk"])] = dict(Item)
        return {}


class SubmissionSchemaTests(unittest.TestCase):
    def test_valid_submission_is_normalized(self):
        result = handler.validate_submission(dict(VALID_SUBMISSION))
        self.assertEqual(result["name"], "Test Contributor")
        self.assertEqual(result["email"], "test@example.com")
        self.assertEqual(result["quantity"], 1)

    def test_secret_honeypot_is_rejected(self):
        body = {**VALID_SUBMISSION, "website": "https://spam.invalid"}
        with self.assertRaisesRegex(ValueError, "invalid_submission"):
            handler.validate_submission(body)

    def test_safety_confirmations_are_required(self):
        for field in ("safeToSubmit", "ownsHardware"):
            with self.subTest(field=field):
                body = {**VALID_SUBMISSION, field: False}
                with self.assertRaisesRegex(ValueError, "confirmation_required"):
                    handler.validate_submission(body)

    def test_email_offer_quantity_and_model_are_bounded(self):
        invalid = (
            ("email", "not-an-email", "invalid_email"),
            ("offerType", "sell", "invalid_offer_type"),
            ("quantity", 21, "invalid_quantity"),
            ("modelNumber", "secret<script>", "invalid_model_number"),
        )
        for field, value, error in invalid:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, error):
                    handler.validate_submission({**VALID_SUBMISSION, field: value})


class CatalogSchemaTests(unittest.TestCase):
    def test_retired_smart_hub_placeholder_is_migrated_without_overwriting_other_rows(self):
        table = FakeSettingsTable([
            {
                "pk": "CATALOG", "sk": "MODEL#panel-smart-hub-pro-gen2", "kind": "catalog",
                "id": "panel-smart-hub-pro-gen2", "position": 7,
                "productName": "Old name", "modelNumber": "Model identifier being confirmed",
            },
            {
                "pk": "CATALOG", "sk": "MODEL#camera-dbc350", "kind": "catalog",
                "id": "camera-dbc350", "position": 8,
                "productName": "Customized camera", "modelNumber": "CUSTOM",
            },
        ])
        handler._migrate_catalog_placeholders(table)
        panel = table.items[("CATALOG", "MODEL#panel-smart-hub-pro-gen2")]
        camera = table.items[("CATALOG", "MODEL#camera-dbc350")]
        self.assertEqual(panel["productName"], "Vivint Smart Hub Pro 2")
        self.assertEqual(panel["modelNumber"], "VS-SHP200-001 / VS-SHP200-002")
        self.assertEqual(panel["generation"], "Gen 2 / Revision B")
        self.assertEqual(panel["position"], 7)
        self.assertEqual(camera["productName"], "Customized camera")

    def test_seed_catalog_has_unique_schema_conforming_entries(self):
        models = json.loads(Path("functions/api/catalog_seed.json").read_text())
        required_strings = {"id", "category", "productName", "modelNumber", "status", "tested", "needed"}
        allowed_statuses = {
            "verified_layers", "partial", "wanted", "model_verification_needed",
            "not_supported", "research",
        }
        self.assertGreaterEqual(len(models), 1)
        self.assertEqual(len({model["id"] for model in models}), len(models))
        for model in models:
            with self.subTest(model=model.get("id")):
                self.assertTrue(required_strings.issubset(model))
                self.assertTrue(all(isinstance(model[key], str) and model[key].strip() for key in required_strings))
                self.assertRegex(model["id"], r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
                self.assertIn(model["status"], allowed_statuses)
                self.assertIsInstance(model["hardwareWanted"], bool)

    def test_catalog_helper_normalizes_id_and_persists_safe_fields(self):
        table = FakeTable()
        result = handler._save_catalog(table, "Doorbell PRO / 2", {
            "category": "Camera",
            "productName": "Doorbell Pro 2",
            "modelNumber": "DBC-2",
            "status": "research",
            "hardwareWanted": True,
            "tested": "Transport",
            "needed": "Hardware",
            "sourceUrl": "https://example.com/evidence",
            "position": 4,
        })
        self.assertEqual(result["id"], "doorbell-pro---2")
        self.assertNotIn("pk", result)
        self.assertEqual(table.items[0]["pk"], "CATALOG")

    def test_catalog_helper_rejects_unsafe_source_url(self):
        with self.assertRaisesRegex(ValueError, "invalid_source_url"):
            handler._save_catalog(FakeTable(), "model", {
                "category": "Panel", "productName": "Panel", "modelNumber": "P1",
                "status": "research", "sourceUrl": "javascript:alert(1)",
            })


class ApiHelperTests(unittest.TestCase):
    def test_legacy_hardware_lab_templates_are_migrated_without_replacing_custom_copy(self):
        table = FakeSettingsTable([
            {"pk": "SETTINGS", "sk": "PUBLIC", "kind": "settings", "title": "Custom title"},
            {
                "pk": "SETTINGS", "sk": "TEMPLATES", "kind": "settings",
                "receivedBody": "The VivintOne Hardware Lab will review this.",
                "approvedBody": "My already customized approval message.",
            },
        ])
        handler._migrate_legacy_settings(table)
        public = table.items[("SETTINGS", "PUBLIC")]
        templates = table.items[("SETTINGS", "TEMPLATES")]
        self.assertEqual(public["title"], "Custom title")
        self.assertEqual(public["schemaVersion"], handler.SETTINGS_SCHEMA_VERSION)
        self.assertNotIn("Hardware Lab", templates["receivedBody"])
        self.assertEqual(templates["approvedBody"], "My already customized approval message.")
        self.assertEqual(templates["schemaVersion"], handler.SETTINGS_SCHEMA_VERSION)

    def test_json_body_requires_an_object(self):
        for raw in ("not json", "[]", '"text"'):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "invalid_json"):
                    handler._body({"body": raw})

    def test_response_serializes_decimal_without_cache(self):
        response = handler._response(200, {"whole": Decimal("2"), "fraction": Decimal("2.50")})
        self.assertEqual(response["headers"]["cache-control"], "no-store")
        self.assertEqual(json.loads(response["body"]), {"whole": 2, "fraction": 2.5})

    def test_reimbursement_tokens_use_constant_time_hash_comparison(self):
        token = "private-token-with-at-least-32-characters"
        item = {"reimbursementTokenHash": handler._token_hash(token)}
        self.assertTrue(handler._valid_reimbursement_token(item, token))
        self.assertFalse(handler._valid_reimbursement_token(item, "wrong"))
        self.assertFalse(handler._valid_reimbursement_token({}, token))

    def test_admin_claims_require_configured_username(self):
        event = {"requestContext": {"authorizer": {"jwt": {"claims": {"username": handler.ADMIN_USERNAME}}}}}
        self.assertEqual(handler._admin_claims(event)["username"], handler.ADMIN_USERNAME)
        event["requestContext"]["authorizer"]["jwt"]["claims"]["username"] = "someone-else"
        with self.assertRaisesRegex(PermissionError, "admin_required"):
            handler._admin_claims(event)


if __name__ == "__main__":
    unittest.main()
