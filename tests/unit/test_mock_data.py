import json

import boto3
from moto import mock_aws

from src.agent.facts import FactsStore
from src.common.s3io import read_jsonl_prefix
from src.doc_builder.builder import build_documents
from tools.mock_data import generate, main


def test_deterministic():
    a = generate(seed=1)
    b = generate(seed=1)
    assert a == b


def test_shape_and_links():
    changes, incidents = generate(seed=42)
    ids = {c["sys_id"] for c in changes}
    assert len({c["number"] for c in changes}) == len(changes)
    assert all(i["caused_by"] in ids for i in incidents if i["caused_by"])
    assert any(i["caused_by"] for i in incidents)


def test_facts_store_sees_history_and_upcoming():
    changes, incidents = generate(seed=42)
    facts = FactsStore(changes, incidents)
    assert len(facts.upcoming_numbers()) == 6
    stats = facts.outcome_stats(cmdb_ci="core-banking-db-01")
    assert stats["total_closed_changes"] > 0
    assert stats["failed_or_caused_incident"] > 0
    # documents build for every change that is not cancelled-only noise
    assert build_documents(changes, incidents)


def test_cli_local_files(tmp_path):
    assert main(["--out", str(tmp_path)]) == 0
    files = list(tmp_path.rglob("part-00000.jsonl"))
    assert len(files) == 2
    for f in files:
        for line in f.read_text().splitlines():
            row = json.loads(line)
            assert row["_run_id"] == "mock-42"


@mock_aws
def test_cli_s3_roundtrip():
    s3 = boto3.client("s3", region_name="eu-north-1")
    s3.create_bucket(Bucket="raw", CreateBucketConfiguration={"LocationConstraint": "eu-north-1"})
    assert main(["--s3-bucket", "raw"]) == 0
    assert read_jsonl_prefix("raw", "raw/change_request/", s3)
    assert read_jsonl_prefix("raw", "raw/incident/", s3)
