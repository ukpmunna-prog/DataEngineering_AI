#!/usr/bin/env bash
# Idempotent: create the S3 Vectors bucket and index only if they do not exist. Run by the pipeline.
# Dimension must match the embedding model (Titan Text Embeddings v2 = 1024).
# Needs an AWS CLI v2 recent enough to include the `s3vectors` command group.
set -euo pipefail
BUCKET="${1:?vector bucket name}"
INDEX="${2:?index name}"
REGION="${AWS_REGION:-eu-north-1}"

if aws s3vectors get-vector-bucket --vector-bucket-name "$BUCKET" --region "$REGION" >/dev/null 2>&1; then
  echo "vector bucket $BUCKET exists"
else
  aws s3vectors create-vector-bucket --vector-bucket-name "$BUCKET" --region "$REGION"
  echo "created vector bucket $BUCKET"
fi

if aws s3vectors get-index --vector-bucket-name "$BUCKET" --index-name "$INDEX" --region "$REGION" >/dev/null 2>&1; then
  echo "index $BUCKET/$INDEX exists"
else
  aws s3vectors create-index --vector-bucket-name "$BUCKET" --index-name "$INDEX" \
    --data-type float32 --dimension 1024 --distance-metric cosine --region "$REGION"
  echo "created index $BUCKET/$INDEX"
fi
