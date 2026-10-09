import json

import boto3
import pytest
import requests
import responses
from moto import mock_aws

from src.common.config import TableConfig, load_table_config
from src.extractor.handler import run_extract
from src.extractor.s3_writer import RawWriter
from src.extractor.servicenow import ServiceNowClient
from src.extractor.watermark import WatermarkStore, apply_overlap

BASE = "https://dev.service-now.com"
URL = f"{BASE}/api/now/table/change_request"


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-2")


@pytest.fixture
def cfg():
    return TableConfig(table="change_request", fields=["sys_id", "number", "sys_updated_on"],
                       page_size=2, overlap_minutes=60, s3_prefix="raw/change_request")


def rec(i, ts):
    return {"sys_id": f"id{i}", "number": f"CHG{i:07d}", "sys_updated_on": ts}


def test_apply_overlap():
    assert apply_overlap("2026-10-03 12:00:00", 120) == "2026-10-03 10:00:00"


def test_load_real_configs():
    for name in ("change_request", "incident"):
        cfg = load_table_config(name, "config/tables")
        assert cfg.table == name and cfg.watermark_column in cfg.fields


def test_config_validation():
    with pytest.raises(ValueError):
        TableConfig(table="x", fields=["a"], watermark_column="sys_updated_on")
    with pytest.raises(ValueError):
        TableConfig(table="x", fields=["sys_updated_on"], page_size=0)


@responses.activate
def test_pagination_and_query(cfg):
    responses.add(responses.GET, URL, json={"result": [rec(1, "2026-10-01 01:00:00"), rec(2, "2026-10-01 02:00:00")]})
    responses.add(responses.GET, URL, json={"result": [rec(3, "2026-10-01 03:00:00")]})
    client = ServiceNowClient(BASE, "u", "p", backoff=0)
    pages = list(client.fetch_pages(cfg, "2026-10-01 00:00:00"))
    assert [len(p) for p in pages] == [2, 1]
    first = responses.calls[0].request.params
    assert "sys_updated_on>=2026-10-01 00:00:00" in first["sysparm_query"]
    assert first["sysparm_offset"] == "0"
    assert responses.calls[1].request.params["sysparm_offset"] == "2"


@responses.activate
def test_retry_on_429(cfg):
    responses.add(responses.GET, URL, status=429, headers={"Retry-After": "0"})
    responses.add(responses.GET, URL, json={"result": [rec(1, "2026-10-01 01:00:00")]})
    client = ServiceNowClient(BASE, "u", "p", backoff=0)
    assert len(list(client.fetch_pages(cfg, "2026-10-01 00:00:00"))) == 1
    assert len(responses.calls) == 2


@responses.activate
def test_non_retryable_raises(cfg):
    responses.add(responses.GET, URL, status=401)
    client = ServiceNowClient(BASE, "u", "p", backoff=0)
    with pytest.raises(requests.HTTPError):
        list(client.fetch_pages(cfg, "2026-10-01 00:00:00"))


@mock_aws
@responses.activate
def test_end_to_end_watermark_advances(cfg, monkeypatch):
    monkeypatch.setenv("INITIAL_WATERMARK", "2000-01-01 00:00:00")
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="raw", CreateBucketConfiguration={"LocationConstraint": "eu-west-2"})
    ddb = boto3.resource("dynamodb")
    ddb.create_table(TableName="wm", KeySchema=[{"AttributeName": "table_name", "KeyType": "HASH"}],
                     AttributeDefinitions=[{"AttributeName": "table_name", "AttributeType": "S"}],
                     BillingMode="PAY_PER_REQUEST")

    responses.add(responses.GET, URL, json={"result": [rec(1, "2026-10-01 01:00:00"), rec(2, "2026-10-01 02:00:00")]})
    responses.add(responses.GET, URL, json={"result": [rec(3, "2026-10-01 03:00:00")]})

    client = ServiceNowClient(BASE, "u", "p", backoff=0)
    store = WatermarkStore("wm", ddb)
    result = run_extract(cfg, client, RawWriter("raw", s3), store, run_id="r1")

    assert result["records"] == 3 and result["pages"] == 2 and result["complete"]
    assert store.get("change_request", "x") == "2026-10-01 03:00:00"
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="raw")["Contents"]]
    assert len(keys) == 2 and all("run=r1" in k for k in keys)
    body = s3.get_object(Bucket="raw", Key=sorted(keys)[0])["Body"].read().decode()
    row = json.loads(body.splitlines()[0])
    assert row["number"] == "CHG0000001" and row["_run_id"] == "r1"


@mock_aws
@responses.activate
def test_no_records_keeps_watermark(cfg):
    ddb = boto3.resource("dynamodb")
    ddb.create_table(TableName="wm", KeySchema=[{"AttributeName": "table_name", "KeyType": "HASH"}],
                     AttributeDefinitions=[{"AttributeName": "table_name", "AttributeType": "S"}],
                     BillingMode="PAY_PER_REQUEST")
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="raw", CreateBucketConfiguration={"LocationConstraint": "eu-west-2"})
    store = WatermarkStore("wm", ddb)
    store.set("change_request", "2026-10-01 03:00:00", "old", 3)
    responses.add(responses.GET, URL, json={"result": []})
    result = run_extract(cfg, ServiceNowClient(BASE, "u", "p", backoff=0), RawWriter("raw", s3), store)
    assert result["records"] == 0
    assert store.get("change_request", "x") == "2026-10-01 03:00:00"
