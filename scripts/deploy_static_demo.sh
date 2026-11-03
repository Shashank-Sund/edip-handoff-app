#!/usr/bin/env bash
# Build and publish the public static demo (synthetic data, runs in the browser) to S3 behind CloudFront.
#
#   scripts/deploy_static_demo.sh            build, scan, deploy the stack, upload, invalidate
#   SKIP_BUILD=1 scripts/deploy_static_demo.sh   reuse app/client/dist/demo
#
# Uses the AWS CLI default profile, region us-east-1, stack public-demo-edip-handoff (deploy/aws/static-demo.cfn.yaml).
# Set EDIP_AWS_ACCOUNT to refuse any other account. Touches only this stack and its bucket.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGION=us-east-1
STACK=public-demo-edip-handoff
OUT="$ROOT/app/client/dist/demo"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
export AWS_REGION=$REGION AWS_DEFAULT_REGION=$REGION

account=$(aws sts get-caller-identity --query Account --output text)
if [[ -n "${EDIP_AWS_ACCOUNT:-}" && "$account" != "$EDIP_AWS_ACCOUNT" ]]; then
  echo "AWS account is not EDIP_AWS_ACCOUNT; stopping." >&2
  exit 1
fi

# 1. Build: the client with the in-browser API, then the data it loads.
if [[ -z "${SKIP_BUILD:-}" ]]; then
  (cd "$ROOT/app/client" && npm run build:demo)
  "$PY" "$ROOT/app/fixtures/static_demo.py" --out "$OUT/data"
fi
[[ -f "$OUT/index.html" && -f "$OUT/data/drafts.jsonl.gz" && -f "$OUT/data/templates.json" ]] || { echo "no demo build in $OUT" >&2; exit 1; }

# 2. Scan everything that will be published: generic secrets and hosts plus the private blocklist (whole words,
#    gz files decompressed), and no server calls in the bundle.
find "$OUT" -type f -print0 | xargs -0 "$PY" "$ROOT/scripts/scan_sensitive.py"
if grep -rIlE 'cloud\.databricks\.com|azuredatabricks\.net|gcp\.databricks\.com|\bdapi[0-9a-f]{32}\b' "$OUT"; then
  echo "workspace host or token in the bundle; stopping." >&2
  exit 1
fi

# 3. CSP hashes for the inline scripts of index.html.
hashes=$("$PY" - "$OUT/index.html" <<'PY'
import base64, hashlib, re, sys
html = open(sys.argv[1], encoding="utf-8").read()
inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
print(" ".join("'sha256-%s'" % base64.b64encode(hashlib.sha256(s.encode()).digest()).decode() for s in inline))
PY
)

# 4. The stack: bucket, origin access control, function, headers policy, distribution.
aws cloudformation deploy --stack-name "$STACK" --template-file "$ROOT/deploy/aws/static-demo.cfn.yaml" \
  --parameter-overrides "InlineScriptHashes=$hashes" \
  --tags project=public-demos app=edip-handoff --no-fail-on-empty-changeset
out() { aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
bucket=$(out BucketName)
dist=$(out DistributionId)
url=$(out Url)

# 5. Upload: hashed assets cached for a year, everything else revalidated. drafts.jsonl.gz goes up as plain
#    bytes (no Content-Encoding) and the page decompresses it.
aws s3 sync --only-show-errors "$OUT/assets" "s3://$bucket/assets" --delete --cache-control "public, max-age=31536000, immutable"
aws s3 cp --only-show-errors "$OUT/data/drafts.jsonl.gz" "s3://$bucket/data/drafts.jsonl.gz" --content-type application/octet-stream \
  --cache-control "public, max-age=300, must-revalidate"
aws s3 sync --only-show-errors "$OUT/data" "s3://$bucket/data" --delete --exclude drafts.jsonl.gz --content-type application/json \
  --cache-control "public, max-age=300, must-revalidate"
aws s3 sync --only-show-errors "$OUT" "s3://$bucket" --delete --exclude "assets/*" --exclude "data/*" --cache-control "no-cache"
aws cloudfront create-invalidation --distribution-id "$dist" --paths /index.html "/data/*" --query Invalidation.Id --output text

echo "static demo: $url"
