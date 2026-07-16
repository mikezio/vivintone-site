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

    def test_contributor_pool_is_separate_passwordless_pool(self):
        admin_pool = TEMPLATE[TEMPLATE.index("  AdminUserPool:"):TEMPLATE.index("  AdminScope:")]
        contributor_pool = TEMPLATE[
            TEMPLATE.index("  ContributorUserPool:"):TEMPLATE.index("  ContributorPreSignUpPermission:")
        ]

        self.assertIn("UserPoolTier: PLUS", admin_pool)
        self.assertIn("MfaConfiguration: 'ON'", admin_pool)
        self.assertIn("AllowAdminCreateUserOnly: true", admin_pool)
        self.assertNotIn("AllowedFirstAuthFactors", admin_pool)

        self.assertIn("UserPoolTier: ESSENTIALS", contributor_pool)
        self.assertIn("UsernameAttributes: [email, phone_number]", contributor_pool)
        self.assertIn("AutoVerifiedAttributes: !If", contributor_pool)
        self.assertIn("- [email, phone_number]", contributor_pool)
        self.assertIn("- [email]", contributor_pool)
        self.assertIn("MfaConfiguration: 'OFF'", contributor_pool)
        self.assertIn("AllowedFirstAuthFactors: !If", contributor_pool)
        self.assertIn("- [PASSWORD, EMAIL_OTP, SMS_OTP]", contributor_pool)
        self.assertIn("- [PASSWORD, EMAIL_OTP]", contributor_pool)
        self.assertIn("EmailSendingAccount: DEVELOPER", contributor_pool)
        self.assertIn("LambdaConfig:", contributor_pool)
        self.assertIn("PreSignUp: !GetAtt ContributorPreSignUpFunction.Arn", contributor_pool)

    def test_contributor_client_only_enables_choice_based_auth(self):
        contributor_client = TEMPLATE[
            TEMPLATE.index("  ContributorUserPoolClient:"):TEMPLATE.index("  Api:")
        ]
        self.assertIn("UserPoolId: !Ref ContributorUserPool", contributor_client)
        self.assertIn("GenerateSecret: false", contributor_client)
        self.assertIn(
            "ExplicitAuthFlows: [ALLOW_USER_AUTH, ALLOW_REFRESH_TOKEN_AUTH]",
            contributor_client,
        )
        explicit_flows = re.search(r"ExplicitAuthFlows: \[([^]]+)\]", contributor_client).group(1)
        self.assertEqual(
            {flow.strip() for flow in explicit_flows.split(",")},
            {"ALLOW_USER_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"},
        )
        self.assertNotIn("ALLOW_USER_PASSWORD_AUTH", contributor_client)
        self.assertNotIn("ALLOW_USER_SRP_AUTH", contributor_client)

    def test_contributor_routes_use_id_token_authorizer(self):
        expected_routes = {
            "GET /api/portal/requests",
            "POST /api/portal/requests",
            "GET /api/portal/requests/{requestId}",
            "POST /api/portal/requests/{requestId}/tracking",
            "POST /api/portal/requests/{requestId}/rates",
            "POST /api/portal/requests/{requestId}/label-url",
            "POST /api/portal/requests/{requestId}/reimbursement/upload",
            "POST /api/portal/requests/{requestId}/reimbursement",
            "POST /api/portal/requests/{requestId}/reimbursement/waive",
        }
        portal_resources = re.findall(
            r"  Portal\w+Route:\n    Type: AWS::ApiGatewayV2::Route\n    Properties: \{([^\n]+)\}",
            TEMPLATE,
        )
        self.assertEqual(len(portal_resources), len(expected_routes))
        self.assertEqual(
            {re.search(r"RouteKey: '([^']+)'", properties).group(1) for properties in portal_resources},
            expected_routes,
        )
        for properties in portal_resources:
            self.assertIn("AuthorizationType: JWT", properties)
            self.assertIn("AuthorizerId: !Ref ContributorAuthorizer", properties)
            self.assertNotIn("AuthorizationScopes", properties)

        contributor_authorizer = TEMPLATE[
            TEMPLATE.index("  ContributorAuthorizer:"):TEMPLATE.index("  ApiRole:")
        ]
        self.assertIn("Audience: [!Ref ContributorUserPoolClient]", contributor_authorizer)
        self.assertIn("/${ContributorUserPool}'", contributor_authorizer)

    def test_contributor_trigger_and_sms_permissions_are_scoped(self):
        self.assertIn("ContributorSmsEnabledCondition: !Equals [!Ref ContributorSmsEnabled, 'true']", TEMPLATE)
        self.assertIn("ContributorSmsRole:\n    Type: AWS::IAM::Role\n    Condition: ContributorSmsEnabledCondition", TEMPLATE)
        self.assertIn("SmsConfiguration: !If", TEMPLATE)
        self.assertIn("Principal: cognito-idp.amazonaws.com", TEMPLATE)
        self.assertIn("SourceArn: !GetAtt ContributorUserPool.Arn", TEMPLATE)
        self.assertIn("'sts:ExternalId': !Sub 'vivintone-contributors-", TEMPLATE)
        trigger_role = TEMPLATE[
            TEMPLATE.index("  ContributorPreSignUpRole:"):TEMPLATE.index("  ContributorPreSignUpFunction:")
        ]
        self.assertIn("Action: [dynamodb:DeleteItem, dynamodb:Query]", trigger_role)
        self.assertNotIn("dynamodb:GetItem", trigger_role)
        self.assertNotIn("dynamodb:UpdateItem", trigger_role)
        self.assertIn("Resource: !GetAtt HardwareTable.Arn", trigger_role)
        self.assertNotIn("/index/*", trigger_role)
        self.assertNotIn("ContributorPreSignUpPermission:\n    DependsOn: ContributorUserPool", TEMPLATE)

    def test_verified_submission_routes_and_lambda_environment(self):
        verification_route = re.search(
            r"  VerificationIntentRoute:\n    Type: AWS::ApiGatewayV2::Route\n    Properties: \{([^\n]+)\}",
            TEMPLATE,
        ).group(1)
        self.assertIn("RouteKey: 'POST /api/verification-intents'", verification_route)
        self.assertNotIn("AuthorizationType", verification_route)

        portal_create_route = re.search(
            r"  PortalCreateRequestRoute:\n    Type: AWS::ApiGatewayV2::Route\n    Properties: \{([^\n]+)\}",
            TEMPLATE,
        ).group(1)
        self.assertIn("RouteKey: 'POST /api/portal/requests'", portal_create_route)
        self.assertIn("AuthorizationType: JWT", portal_create_route)
        self.assertIn("AuthorizerId: !Ref ContributorAuthorizer", portal_create_route)
        self.assertNotIn("AuthorizationScopes", portal_create_route)

        api_function = TEMPLATE[TEMPLATE.index("  ApiFunction:"):TEMPLATE.index("  ApiFunctionLogGroup:")]
        trigger_function = TEMPLATE[
            TEMPLATE.index("  ContributorPreSignUpFunction:"):TEMPLATE.index("  ContributorPreSignUpFunctionLogGroup:")
        ]
        for function in (api_function, trigger_function):
            self.assertIn("CONTRIBUTOR_SMS_ENABLED: !Ref ContributorSmsEnabled", function)

    def test_otp_entry_points_are_rate_limited_at_both_edges(self):
        edge_waf = TEMPLATE[TEMPLATE.index("  WebAcl:"):TEMPLATE.index("  Distribution:")]
        self.assertIn("SearchString: /api/requests", edge_waf)
        self.assertIn("Name: VerificationIntentRateLimit", edge_waf)
        self.assertIn("SearchString: /api/verification-intents", edge_waf)
        self.assertIn("Limit: 10", edge_waf)

        cognito_waf = TEMPLATE[
            TEMPLATE.index("  ContributorAuthWebAcl:"):TEMPLATE.index("  Api:")
        ]
        self.assertIn("Scope: REGIONAL", cognito_waf)
        self.assertIn("Limit: 30", cognito_waf)
        self.assertIn("EvaluationWindowSec: 300", cognito_waf)
        self.assertIn("Type: AWS::WAFv2::WebACLAssociation", cognito_waf)
        self.assertIn("ResourceArn: !GetAtt ContributorUserPool.Arn", cognito_waf)
        self.assertIn("WebACLArn: !GetAtt ContributorAuthWebAcl.Arn", cognito_waf)
        self.assertNotIn("CAPTCHA", cognito_waf)
        self.assertNotIn("ATP", cognito_waf)

    def test_cost_budget_and_focused_operational_alarms(self):
        budget = TEMPLATE[TEMPLATE.index("  MonthlyGrossCostBudget:"):TEMPLATE.index("  OperationsAlarmTopic:")]
        self.assertIn("BudgetLimit: { Amount: 25, Unit: USD }", budget)
        self.assertIn("BudgetType: COST", budget)
        self.assertIn("IncludeCredit: false", budget)
        self.assertIn("IncludeRefund: false", budget)
        self.assertIn("NotificationType: ACTUAL, Threshold: 50", budget)
        self.assertIn("NotificationType: ACTUAL, Threshold: 80", budget)
        self.assertIn("NotificationType: ACTUAL, Threshold: 100", budget)
        self.assertIn("NotificationType: FORECASTED, Threshold: 100", budget)
        self.assertEqual(budget.count("Address: !Ref AdminEmail"), 4)

        self.assertEqual(TEMPLATE.count("Type: AWS::SNS::Topic"), 1)
        expected_alarms = {
            "ApiFunctionErrorsAlarm": ("Errors", "ApiFunction"),
            "ApiFunctionThrottlesAlarm": ("Throttles", "ApiFunction"),
            "ContributorPreSignUpErrorsAlarm": ("Errors", "ContributorPreSignUpFunction"),
            "ContributorPreSignUpThrottlesAlarm": ("Throttles", "ContributorPreSignUpFunction"),
            "DirectCarrierPollErrorsAlarm": ("Errors", "DirectCarrierPollFunction"),
            "DirectCarrierPollDeadLetterAlarm": ("ApproximateNumberOfMessagesVisible", "DirectCarrierPollDeadLetterQueue.QueueName"),
        }
        self.assertEqual(TEMPLATE.count("Type: AWS::CloudWatch::Alarm"), len(expected_alarms))
        for logical_id, (metric, dimension_target) in expected_alarms.items():
            start = TEMPLATE.index(f"  {logical_id}:")
            next_resource = re.search(r"\n  \w+:\n    Type:", TEMPLATE[start + 1 :])
            end = start + 1 + next_resource.start() if next_resource else len(TEMPLATE)
            alarm = TEMPLATE[start:end]
            self.assertIn(f"MetricName: {metric}", alarm)
            self.assertIn(dimension_target, alarm)
            self.assertIn("TreatMissingData: notBreaching", alarm)
            self.assertIn("AlarmActions: [!Ref OperationsAlarmTopic]", alarm)

    def test_deploy_writes_contributor_runtime_config(self):
        self.assertIn('CONTRIBUTOR_POOL_ID="$(value ContributorUserPoolId)"', DEPLOY)
        self.assertIn('CONTRIBUTOR_CLIENT_ID="$(value ContributorUserPoolClientId)"', DEPLOY)
        self.assertIn('--arg region "$AWS_REGION"', DEPLOY)
        self.assertIn("contributorPoolId:$contributorPoolId", DEPLOY)
        self.assertIn("contributorClientId:$contributorClientId", DEPLOY)
        self.assertIn('--argjson contributorSmsEnabled "$CONTRIBUTOR_SMS_ENABLED"', DEPLOY)
        self.assertIn("contributorSmsEnabled:$contributorSmsEnabled", DEPLOY)
        self.assertIn('aws s3 sync dist "s3://${SITE_BUCKET}"', DEPLOY)
        self.assertIn("Contributor verification: email OTP", DEPLOY)

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
        handler_routes = {
            route
            for route in re.findall(r'"((?:GET|POST|PUT|PATCH|DELETE) /api/[^"]+)"', HANDLER)
            if not route.endswith("/")
        }
        template_routes = set(re.findall(r"RouteKey: '([^']+)'", TEMPLATE))
        self.assertEqual(template_routes, handler_routes)


if __name__ == "__main__":
    unittest.main()
