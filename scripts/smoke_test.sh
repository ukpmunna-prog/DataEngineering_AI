#!/usr/bin/env bash
# Post-deploy checks. Fails the pipeline if the deployed stack is not usable.
set -euo pipefail
ENV="${1:?env name}"
VECTOR_BUCKET="${2:?vector bucket}"
VECTOR_INDEX="${3:?vector index}"
REGION="${AWS_REGION:-eu-north-1}"
NAME="sn-change-intel-${ENV}"
fail=0

for fn in extractor embedder agent; do
  state="$(aws lambda get-function-configuration --function-name "${NAME}-${fn}" --region "$REGION" \
    --query 'State' --output text)"
  if [[ "$state" == "Active" ]]; then echo "ok   lambda ${NAME}-${fn} is Active"; else echo "FAIL lambda ${NAME}-${fn} state=$state"; fail=1; fi
done

if aws s3vectors get-index --vector-bucket-name "$VECTOR_BUCKET" --index-name "$VECTOR_INDEX" --region "$REGION" >/dev/null 2>&1; then
  echo "ok   vector index $VECTOR_BUCKET/$VECTOR_INDEX"
else
  echo "FAIL vector index $VECTOR_BUCKET/$VECTOR_INDEX not found"; fail=1
fi

stages="$(aws secretsmanager describe-secret --secret-id "${NAME}/servicenow" --region "$REGION" \
  --query 'length(VersionIdsToStages)' --output text 2>/dev/null || echo 0)"
if [[ "$stages" != "0" && "$stages" != "None" ]]; then
  echo "ok   ServiceNow credentials present"
else
  echo "WARN ServiceNow credentials not set yet (add SN_USERNAME and SN_PASSWORD to the GitHub environment)"
fi

exit "$fail"
