# VivintOne Project Site

Public project site, evidence-based compatibility catalog, hardware contribution
intake, and private maintainer review workflow for VivintOne.

The site is intentionally separate from Mike Ziolkowski's `mzio.dev` portfolio.
Its production hostname is `vivintone.mzio.dev`; the portfolio does not link to
it.

## Architecture

- Static site: private S3 origin behind CloudFront
- Public API: API Gateway HTTP API and Lambda
- Requests/catalog/settings: encrypted DynamoDB table
- Maintainer login: Cognito managed login with authorization-code + PKCE
- Admin API: API Gateway JWT authorizer plus maintainer-email defense in depth
- Email: SES, with shipping instructions held in Secrets Manager
- Prepaid labels: EasyPost is used only for rate shopping and purchasing a
  carrier label after the maintainer authorizes one label and a per-request cap
- Carrier tracking: EventBridge invokes the Lambda every 30 minutes to poll the
  official USPS, UPS, and FedEx APIs using credentials in Secrets Manager
- Abuse controls: CloudFront WAF managed rules, rate limiting, strict validation,
  a honeypot, and same-origin API routing

There is no always-on server or single-purpose container.

## Maintainer experience

The admin area manages the request queue, device catalog, compatibility status,
public guidance, email templates, and private shipping instructions. Routine
content and workflow changes do not require editing or redeploying code.

## Request lifecycle

Each offer receives a stable `VOH-YYYYMMDD-XXXXXX` reference. Approval creates
a private, expiring contributor link. From there, the contributor can either:

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
