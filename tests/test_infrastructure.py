import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "infra/template.yaml").read_text()
DEPLOY = (ROOT / "scripts/deploy.sh").read_text()
HANDLER = (ROOT / "functions/api/handler.py").read_text()


class InfrastructureContractTests(unittest.TestCase):
    def test_cognito_client_is_public_code_flow_for_pkce(self):
        self.assertIn("GenerateSecret: false", TEMPLATE)
        self.assertIn("AllowedOAuthFlows: [code]", TEMPLATE)
        self.assertIn("AllowedOAuthFlowsUserPoolClient: true", TEMPLATE)

    def test_receipts_are_private_encrypted_and_expiring(self):
        self.assertIn("BlockPublicPolicy: true", TEMPLATE)
        self.assertIn("ExpireReimbursementReceipts", TEMPLATE)
        self.assertIn("ExpirationInDays: 180", TEMPLATE)
        self.assertIn("aws:SecureTransport: 'false'", TEMPLATE)

    def test_direct_carrier_schedule_contract_is_stable(self):
        self.assertIn("ScheduleExpression: rate(30 minutes)", TEMPLATE)
        self.assertIn("events.amazonaws.com", TEMPLATE)
        self.assertIn("'{\"source\":\"vivintone.tracking\",\"action\":\"poll_direct_carriers\"}'", TEMPLATE)
        self.assertNotIn("EasyPostWebhookRoute", TEMPLATE)

    def test_lambda_package_includes_local_modules(self):
        self.assertIn("cp functions/api/*.py functions/api/catalog_seed.json", DEPLOY)
        self.assertIn("cloudformation validate-template", DEPLOY)

    def test_cloudfront_uses_managed_cache_policy_ids(self):
        self.assertIn("CachePolicyId: 658327ea-f89d-4fab-a63d-7e88639e58f6", TEMPLATE)
        self.assertIn("CachePolicyId: 4135ea2d-6df8-44a3-9df3-4b5a84be39ad", TEMPLATE)
        self.assertNotIn("413f1608-43d4-4c27-9936-63d7e8ef1e85", TEMPLATE)

    def test_cloudformation_exposes_every_handler_route(self):
        handler_routes = set(re.findall(r'"((?:GET|POST|PUT|PATCH|DELETE) /api/[^"]+)"', HANDLER))
        template_routes = set(re.findall(r"RouteKey: '([^']+)'", TEMPLATE))
        self.assertEqual(template_routes, handler_routes)


if __name__ == "__main__":
    unittest.main()
