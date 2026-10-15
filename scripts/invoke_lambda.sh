#!/usr/bin/env bash
# Invoke a Lambda synchronously and fail the step if it returns a function error.
# Usage: bash scripts/invoke_lambda.sh <function-name> '<json payload>'
set -euo pipefail
FN="${1:?function name}"
PAYLOAD="${2:-{\}}"
REGION="${AWS_REGION:-eu-north-1}"
OUT="$(mktemp)"
aws lambda invoke --function-name "$FN" --region "$REGION" --cli-binary-format raw-in-base64-out \
  --cli-read-timeout 900 --payload "$PAYLOAD" "$OUT" --query '[StatusCode,FunctionError]' --output text | tee /dev/stderr | grep -q "None" \
  || { echo "FAIL: $FN returned an error"; cat "$OUT"; exit 1; }
echo "--- $FN result ---"; cat "$OUT"; echo
