"""Write raw record pages to S3 as JSON Lines."""
import json
from datetime import datetime

import boto3


class RawWriter:
    def __init__(self, bucket: str, s3_client=None):
        self.bucket = bucket
        self.s3 = s3_client or boto3.client("s3")

    def write_page(self, prefix: str, run_id: str, part: int, records: list, loaded_at: datetime) -> str:
        key = f"{prefix}/dt={loaded_at:%Y-%m-%d}/run={run_id}/part-{part:05d}.jsonl"
        stamp = loaded_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        body = "\n".join(
            json.dumps({**r, "_loaded_at": stamp, "_run_id": run_id}, separators=(",", ":"))
            for r in records
        )
        self.s3.put_object(
            Bucket=self.bucket, Key=key, Body=body.encode("utf-8"),
            ContentType="application/x-ndjson", ServerSideEncryption="aws:kms",
        )
        return key
