#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_NAME="${STACK_NAME:-vivintone-site}"
AWS_REGION="${AWS_REGION:-us-east-1}"
ROOT_DOMAIN="${ROOT_DOMAIN:-mzio.dev}"
SITE_SUBDOMAIN="${SITE_SUBDOMAIN:-vivintone}"
ADMIN_EMAIL="${ADMIN_EMAIL:-michael@mzio.dev}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
CONTRIBUTOR_SMS_ENABLED="${CONTRIBUTOR_SMS_ENABLED:-false}"

if [[ "$CONTRIBUTOR_SMS_ENABLED" != "true" && "$CONTRIBUTOR_SMS_ENABLED" != "false" ]]; then
  echo "CONTRIBUTOR_SMS_ENABLED must be true or false." >&2
  exit 1
fi

for command in aws npm python zip jq; do
  command -v "$command" >/dev/null || { echo "Missing required command: $command" >&2; exit 1; }
done

if [[ "$AWS_REGION" != "us-east-1" ]]; then
  echo "CloudFront certificates and CLOUDFRONT-scope WAF resources must be deployed in us-east-1." >&2
  exit 1
fi

aws sts get-caller-identity --region "$AWS_REGION" >/dev/null
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")"
HOSTED_ZONE_ID="$(aws route53 list-hosted-zones-by-name --dns-name "$ROOT_DOMAIN" --query "HostedZones[?Name=='${ROOT_DOMAIN}.']|[0].Id" --output text | sed 's|/hostedzone/||')"
if [[ -z "$HOSTED_ZONE_ID" || "$HOSTED_ZONE_ID" == "None" ]]; then
  echo "No Route 53 hosted zone found for $ROOT_DOMAIN" >&2
  exit 1
fi

ARTIFACT_BUCKET="${ARTIFACT_BUCKET:-vivintone-deployments-${ACCOUNT_ID}-${AWS_REGION}}"
if ! aws s3api head-bucket --bucket "$ARTIFACT_BUCKET" 2>/dev/null; then
  if [[ "$AWS_REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$ARTIFACT_BUCKET" --region "$AWS_REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$ARTIFACT_BUCKET" --region "$AWS_REGION" --create-bucket-configuration "LocationConstraint=$AWS_REGION" >/dev/null
  fi
fi
aws s3api put-public-access-block --bucket "$ARTIFACT_BUCKET" --public-access-block-configuration 'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'
aws s3api put-bucket-encryption --bucket "$ARTIFACT_BUCKET" --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-bucket-versioning --bucket "$ARTIFACT_BUCKET" --versioning-configuration Status=Enabled

cd "$ROOT"
npm ci --ignore-scripts
npm run test
npm run check
aws cloudformation validate-template --region "$AWS_REGION" --template-body file://infra/template.yaml >/dev/null

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
python -m pip install --disable-pip-version-check --no-compile -q -r functions/api/requirements.txt -t "$BUILD_DIR/lambda"
cp functions/api/*.py functions/api/catalog_seed.json "$BUILD_DIR/lambda/"
find "$BUILD_DIR/lambda" -type d -name __pycache__ -prune -exec rm -rf {} +
(cd "$BUILD_DIR/lambda" && zip -qr "$BUILD_DIR/function.zip" . -x '*.pyc')
ARTIFACT_HASH="$(sha256sum "$BUILD_DIR/function.zip" | cut -d' ' -f1)"
ARTIFACT_KEY="vivintone-site/${ARTIFACT_HASH}.zip"
aws s3 cp "$BUILD_DIR/function.zip" "s3://${ARTIFACT_BUCKET}/${ARTIFACT_KEY}" --only-show-errors

aws cloudformation deploy \
  --region "$AWS_REGION" \
  --stack-name "$STACK_NAME" \
  --template-file infra/template.yaml \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    HostedZoneId="$HOSTED_ZONE_ID" \
    RootDomain="$ROOT_DOMAIN" \
    SiteSubdomain="$SITE_SUBDOMAIN" \
    AdminEmail="$ADMIN_EMAIL" \
    AdminUsername="$ADMIN_USERNAME" \
    ContributorSmsEnabled="$CONTRIBUTOR_SMS_ENABLED" \
    ArtifactBucket="$ARTIFACT_BUCKET" \
    ArtifactKey="$ARTIFACT_KEY"

outputs="$(aws cloudformation describe-stacks --region "$AWS_REGION" --stack-name "$STACK_NAME" --query 'Stacks[0].Outputs' --output json)"
value() { jq -r --arg key "$1" '.[] | select(.OutputKey==$key) | .OutputValue' <<<"$outputs"; }
SITE_URL="$(value SiteUrl)"
SITE_BUCKET="$(value SiteBucketName)"
DISTRIBUTION_ID="$(value DistributionId)"
AUTHORITY="$(value CognitoAuthority)"
CLIENT_ID="$(value CognitoClientId)"
CONTRIBUTOR_POOL_ID="$(value ContributorUserPoolId)"
CONTRIBUTOR_CLIENT_ID="$(value ContributorUserPoolClientId)"
CONTRIBUTOR_SMS_ENABLED="$(value ContributorSmsEnabled)"

jq -n \
  --arg authority "$AUTHORITY" \
  --arg clientId "$CLIENT_ID" \
  --arg region "$AWS_REGION" \
  --arg contributorPoolId "$CONTRIBUTOR_POOL_ID" \
  --arg contributorClientId "$CONTRIBUTOR_CLIENT_ID" \
  --argjson contributorSmsEnabled "$CONTRIBUTOR_SMS_ENABLED" \
  --arg redirectUri "$SITE_URL/admin.html" \
  --arg postLogoutRedirectUri "$SITE_URL/" \
  '{authority:$authority,clientId:$clientId,region:$region,contributorPoolId:$contributorPoolId,contributorClientId:$contributorClientId,contributorSmsEnabled:$contributorSmsEnabled,redirectUri:$redirectUri,postLogoutRedirectUri:$postLogoutRedirectUri}' > dist/config.json

aws s3 sync dist "s3://${SITE_BUCKET}" --delete --cache-control 'no-cache,no-store,must-revalidate' --only-show-errors
aws s3 cp dist/assets "s3://${SITE_BUCKET}/assets" --recursive --cache-control 'public,max-age=31536000,immutable' --only-show-errors
aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths '/*' >/dev/null

echo "Deployed: $SITE_URL"
echo "Admin:    $SITE_URL/admin.html"
echo "Portal:   $SITE_URL/portal.html"
echo "Contributor verification: email OTP (SMS OTP enabled: $CONTRIBUTOR_SMS_ENABLED)"
echo "Next: sign in with $ADMIN_USERNAME, set a permanent password/MFA, then configure shipping and EasyPost in Settings."
