"""Print the latest assessment per change from the raw bucket. Usage: summarize_assessments.py <bucket>"""
import json
import sys

import boto3


def main(bucket: str) -> int:
    s3 = boto3.client("s3")
    latest = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="assessments/"):
        for obj in page.get("Contents", []):
            number = obj["Key"].split("/")[1]
            if number not in latest or obj["Key"] > latest[number]:
                latest[number] = obj["Key"]
    if not latest:
        print("no assessments found")
        return 1
    for number, key in sorted(latest.items()):
        a = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        print(f"{number:<12} risk={a.get('risk_level', a.get('risk', '?')):<9} "
              f"confidence={a.get('confidence', '?')}  {key}")
        print(json.dumps(a, indent=2)[:3000])
        print("-" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
