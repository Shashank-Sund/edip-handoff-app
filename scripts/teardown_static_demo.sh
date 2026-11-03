#!/usr/bin/env bash
# Remove the public static demo: empties its bucket and deletes the stack public-demo-edip-handoff (us-east-1).
# Touches nothing else.
set -euo pipefail
REGION=us-east-1
STACK=public-demo-edip-handoff
export AWS_REGION=$REGION AWS_DEFAULT_REGION=$REGION

bucket=$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text 2>/dev/null || true)
if [[ -n "$bucket" && "$bucket" != "None" ]]; then
  aws s3 rm --only-show-errors "s3://$bucket" --recursive
fi
aws cloudformation delete-stack --stack-name "$STACK"
echo "deleting $STACK (CloudFront takes several minutes)..."
aws cloudformation wait stack-delete-complete --stack-name "$STACK"
echo "deleted"
