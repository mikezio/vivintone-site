# VivintOne Project Site

Public project site, evidence-based compatibility catalog, hardware contribution
intake, and private maintainer review workflow for VivintOne.

The site is intentionally separate from Mike Ziolkowski's `mzio.dev` portfolio.
Its production hostname is `vivintone.mzio.dev`; the portfolio does not link to
it.

Voluntary project support is available through
[Buy Me a Coffee](https://buymeacoffee.com/mzio) and
[GitHub Sponsors](https://github.com/sponsors/mikezio). These contributions are
separate from hardware shipping and reimbursements and do not purchase device
compatibility, support priority, or access to private research.

## Architecture

- Static site: private S3 origin behind CloudFront
- Public API: API Gateway HTTP API and Lambda
- Requests/catalog/settings: encrypted DynamoDB table
- Maintainer login: Cognito managed login with authorization-code + PKCE
- Contributor login: a separate Cognito Essentials user pool with passwordless
  email codes and an account-gated SMS-code option
- Admin API: API Gateway JWT authorizer plus maintainer-email defense in depth
- Contributor API: a dedicated API Gateway JWT authorizer that accepts the
  contributor ID token so verified email or phone claims reach the Lambda;
  contributor identities cannot authenticate to maintainer routes
- Email: SES, with shipping instructions held in Secrets Manager
- Prepaid labels: EasyPost is used only for rate shopping and purchasing a
  carrier label after the maintainer authorizes one label and a per-request cap
- Carrier tracking: EventBridge invokes the Lambda every 30 minutes to poll the
  official USPS, UPS, and FedEx APIs using credentials in Secrets Manager
- Abuse controls: CloudFront WAF managed rules, rate limiting, strict validation,
  a honeypot, and same-origin API routing; a separate regional WAF rate limit
  also protects direct Cognito sign-up and authentication calls

There is no always-on server or single-purpose container.

## Maintainer experience

The admin area manages the request queue, device catalog, compatibility status,
public guidance, email templates, and private shipping instructions. Routine
content and workflow changes do not require editing or redeploying code.

## Request lifecycle

Before submitting an offer, a contributor verifies one contact method. A phone
number is the recommended identity when SMS is enabled, and email is always the
fallback; providing both is not required. The chosen verified phone number or
email address becomes the contributor's passwordless portal login and is bound
to the submitted request. The public verification-intent endpoint starts this
flow, and the authenticated portal endpoint accepts the request only with the
resulting contributor ID token.

Each offer receives a stable `VOH-YYYYMMDD-XXXXXX` reference. Approval creates
a private, expiring contributor link, and the request also becomes available in
the contributor portal after the contributor signs in with an email one-time
code. From either experience, the contributor can:

- generate one prepaid label within the maintainer-authorized spending cap; or
- ship independently, register tracking, and explicitly request or waive
  reimbursement.

Label creation is guarded by request approval, an expiring capability token,
address and package validation, a measurement attestation, a one-label lock,
USD-only rate selection, and both per-request and global amount caps. Rates
outside the cap stop for review without buying postage. Unused eligible labels
can be voided/refunded, and the scheduled poller can request that automatically
after the configured interval.

Receipt submission and carrier delivery do not release money. Reimbursement is
available only for self-paid postage and remains locked until the maintainer
marks the hardware physically received. Payment destination data and the receipt
are removed after payment while the amount, method, date, and audit event remain.

## Repository layout

- `public/` — public hardware lab and authenticated admin application
- `functions/api/` — Lambda API and initial model catalog
- `infra/template.yaml` — complete CloudFormation stack
- `scripts/deploy.sh` — package, deploy, configure, upload, and invalidate
- `tests/` — validation for catalog and request rules

## Deployment

AWS SSO must be active for the deployment profile. Then run:

```bash
./scripts/deploy.sh
```

The edge stack must be deployed in `us-east-1`, where CloudFront ACM
certificates and CloudFront-scope WAF resources are managed. The deployment
script enforces this requirement. The AWS account must also have SES production
access in `us-east-1` to deliver mail to unverified contributors; the stack
creates and DKIM-verifies the sending domain identity.

Contributor email codes use that verified SES domain. Phone OTP is disabled by
default and the portal must keep its phone option hidden while
`contributorSmsEnabled` is false. Enabling it requires the account to be out of
the Amazon SNS SMS sandbox for production recipients, with an approved
origination identity where the destination country requires one. The current AWS
SMS channel is not approved, so do not claim phone sign-in is live. After AWS
approves the channel and account-level SMS limits and origination are configured,
deploy with `CONTRIBUTOR_SMS_ENABLED=true`; Cognito will then create and assume a
dedicated role for these transactional messages.

CloudFront rate-limits public request and verification-intent traffic. Because
the browser calls Cognito directly for passwordless codes, the contributor user
pool also has a regional AWS WAF web ACL that blocks an IP after 30 Cognito
requests in five minutes. This avoids a direct-Cognito bypass of the edge limit;
AWS WAF is billed separately from Cognito and CloudFront.

The stack also creates a $25 monthly account-level gross-cost budget. Credits
and refunds are excluded so underlying service burn remains visible. Budget
email goes directly to `AdminEmail` at 50%, 80%, and 100% actual spend and at
100% forecasted spend. Focused CloudWatch alarms cover API and contributor
PreSignUp errors/throttles, scheduled carrier-poll errors, and visible messages
in the poller's dead-letter queue. Those alarms share one SNS email subscription;
after the first deployment, the administrator must confirm the AWS notification
subscription email before operational alarms can be delivered.

Older approval and reimbursement links remain a supported recovery path: they
continue to open the legacy one-request experience until they expire, even when
portal sign-in is unavailable. The same request appears in the portal when its
verified contact signs in. Keep the original link private because it remains a
bearer credential until it expires.

The script asks only for deployment-safe parameters. The shipping address and
instructions are entered later through the authenticated admin settings screen
and are stored in AWS Secrets Manager, not this repository.

Real prepaid labels additionally require an EasyPost production key and billing
method. Automated carrier status requires OAuth credentials for whichever of
USPS, UPS, and FedEx will be used. All of these are write-only admin settings.
The public contributor experience and lifecycle email remain VivintOne-branded.

## Security and privacy

Never add credentials, device serials, QR codes, access keys, addresses, or
shipping information to this repository. Public submissions explicitly reject
Vivint credentials and other secrets. Approval emails load shipping instructions
only at send time and do not copy them into the request record.

Copyright 2026 Mike Ziolkowski. Released under the MIT License.
