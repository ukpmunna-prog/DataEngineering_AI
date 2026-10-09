"""DynamoDB-backed watermark store."""
from datetime import UTC, datetime, timedelta

import boto3

FMT = "%Y-%m-%d %H:%M:%S"


class WatermarkStore:
    def __init__(self, table_name: str, dynamodb=None):
        self.table = (dynamodb or boto3.resource("dynamodb")).Table(table_name)

    def get(self, table: str, default: str) -> str:
        item = self.table.get_item(Key={"table_name": table}).get("Item")
        return item["watermark"] if item else default

    def set(self, table: str, watermark: str, run_id: str, records: int) -> None:
        self.table.put_item(Item={
            "table_name": table,
            "watermark": watermark,
            "last_run_id": run_id,
            "last_run_records": records,
            "updated_at": datetime.now(UTC).strftime(FMT),
        })


def apply_overlap(watermark: str, minutes: int) -> str:
    """Step the watermark back to catch late-arriving updates."""
    dt = datetime.strptime(watermark, FMT) - timedelta(minutes=minutes)
    return dt.strftime(FMT)
