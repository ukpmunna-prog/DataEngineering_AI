#!/usr/bin/env bash
# Writes the ServiceNow credentials into Secrets Manager from the GitHub environment secrets.
# The values never enter git or Terraform state. Skips quietly if the secrets are not set yet.
set -euo pipefail
SECRET_ID="${1:?secret name}"
REGION="${AWS_REGION:-eu-north-1}"

if [[ -z "${SN_USERNAME:-}" || -z "${SN_PASSWORD:-}" ]]; then
  echo "SN_USERNAME / SN_PASSWORD not set in this GitHub environment; leaving the secret unchanged"
  exit 0
fi

payload="$(jq -cn --arg u "$SN_USERNAME" --arg p "$SN_PASSWORD" '{username:$u,password:$p}')"
aws secretsmanager put-secret-value --secret-id "$SECRET_ID" --secret-string "$payload" \
  --region "$REGION" >/dev/null
echo "ServiceNow credentials stored in $SECRET_ID"
