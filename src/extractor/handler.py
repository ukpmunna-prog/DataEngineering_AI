"""Lambda / container entrypoint: extract one ServiceNow table incrementally into S3.

Event: {"table": "change_request"}
"""
import json
import os
import time
import uuid
from datetime import UTC, datetime

import boto3

from src.common.config import get_logger, load_table_config
from src.extractor.s3_writer import RawWriter
from src.extractor.servicenow import ServiceNowClient
from src.extractor.watermark import WatermarkStore, apply_overlap

log = get_logger(__name__)
INITIAL_WATERMARK = os.environ.get("INITIAL_WATERMARK", "2000-01-01 00:00:00")


def _credentials() -> tuple:
    secret = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["SN_SECRET_ARN"])
    data = json.loads(secret["SecretString"])
    return data["username"], data["password"]


def run_extract(cfg, client, writer, store, deadline=None, run_id=None) -> dict:
    """Core logic, separated from AWS wiring so it can be tested directly."""
    run_id = run_id or uuid.uuid4().hex[:12]
    loaded_at = datetime.now(UTC).replace(tzinfo=None)
    previous = store.get(cfg.table, INITIAL_WATERMARK)
    since = apply_overlap(previous, cfg.overlap_minutes)
    log.info("extract start", extra={"ctx": {"table": cfg.table, "since": since, "run_id": run_id}})

    total, part, max_seen, complete = 0, 0, previous, True
    for page in client.fetch_pages(cfg, since):
        writer.write_page(cfg.s3_prefix, run_id, part, page, loaded_at)
        part += 1
        total += len(page)
        page_max = max(r[cfg.watermark_column] for r in page if r.get(cfg.watermark_column))
        max_seen = max(max_seen, page_max)
        if deadline and time.time() > deadline:
            complete = False
            log.warning("deadline reached, stopping early", extra={"ctx": {"table": cfg.table}})
            break

    # Watermark moves only after pages are durably written. Early stop still advances to
    # the max seen because pages are ordered ascending, so the next run resumes from there.
    if total:
        store.set(cfg.table, max_seen, run_id, total)
    log.info("extract done", extra={"ctx": {
        "table": cfg.table, "records": total, "complete": complete, "watermark": max_seen}})
    return {"table": cfg.table, "run_id": run_id, "records": total, "pages": part,
            "watermark": max_seen, "complete": complete}


def handler(event, context=None):
    table = event["table"]
    cfg = load_table_config(table)
    user, pwd = _credentials()
    client = ServiceNowClient(os.environ["SN_INSTANCE_URL"], user, pwd)
    writer = RawWriter(os.environ["RAW_BUCKET"])
    store = WatermarkStore(os.environ["WATERMARK_TABLE"])
    budget = int(os.environ.get("MAX_RUNTIME_SECONDS", "780"))
    return run_extract(cfg, client, writer, store, deadline=time.time() + budget)
