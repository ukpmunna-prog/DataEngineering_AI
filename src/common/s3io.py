"""Helpers for reading the raw zone (JSON Lines) and small state files from S3."""
import json

import boto3


def read_jsonl_prefix(bucket: str, prefix: str, s3=None) -> list:
    s3 = s3 or boto3.client("s3")
    rows = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read().decode("utf-8")
            rows.extend(json.loads(line) for line in body.splitlines() if line.strip())
    return rows


def read_json(bucket: str, key: str, default, s3=None):
    s3 = s3 or boto3.client("s3")
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return default


def write_json(bucket: str, key: str, data, s3=None) -> None:
    s3 = s3 or boto3.client("s3")
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(data, indent=2).encode(),
                  ContentType="application/json", ServerSideEncryption="aws:kms")
