import hashlib
import io
import json

import pytest

from src.agent.agent import AssessmentError, ChangeRiskAgent
from src.agent.facts import FactsStore
from src.agent.tools import ToolRunner, validate_assessment
from src.common.pii import mask_pii
from src.doc_builder.builder import build_documents, outcome_of
from src.embedder.bedrock import BedrockEmbedder
from src.embedder.handler import embed_documents
from src.embedder.store import InMemoryVectorStore


def chg(n, sys_id, desc, ci="db-01", state="3", close="successful", start="2026-01-10 22:00:00",
        end="2026-01-10 23:00:00", updated="2026-01-11 00:00:00"):
    return {"sys_id": sys_id, "number": n, "short_description": desc, "description": desc, "state": state,
            "close_code": close, "cmdb_ci": ci, "business_service": "payments", "type": "normal",
            "category": "database", "risk": "3", "impact": "2", "start_date": start, "end_date": end,
            "sys_updated_on": updated, "close_notes": ""}


def inc(n, sys_id, caused_by="", ci="db-01"):
    return {"sys_id": sys_id, "number": n, "short_description": "db outage after patch", "caused_by": caused_by,
            "cmdb_ci": ci, "priority": "1", "opened_at": "2026-01-10 23:30:00", "sys_updated_on": "2026-01-11 00:00:00"}


class FakeEmbedder:
    model_id = "fake-embed"

    def __init__(self):
        self.calls = 0

    def embed(self, text):
        self.calls += 1
        vec = [0.0] * 16
        for word in text.lower().split():
            vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % 16] += 1.0
        return vec


CHANGES = [
    chg("CHG1", "s1", "patch postgres database kernel", close="successful"),
    chg("CHG2", "s2", "patch postgres database kernel version", close="successful"),
    chg("CHG3", "s3", "rotate tls certificates on load balancer", ci="lb-01", close="successful"),
    chg("CHG9", "s9", "patch postgres database kernel again", state="-2", close="",
        start="2026-11-01 22:00:00", end="2026-11-01 23:00:00"),
    chg("CHG10", "s10", "unrelated storage migration", ci="db-01", state="-2", close="",
        start="2026-11-01 22:30:00", end="2026-11-01 23:30:00"),
]
INCIDENTS = [inc("INC1", "i1", caused_by="s2")]


def test_pii_masking():
    text = mask_pii("mail bob@corp.com call +44 7700 900123 host 10.1.2.3 password: hunter2")
    assert "bob@corp.com" not in text and "10.1.2.3" not in text and "hunter2" not in text
    assert "[EMAIL]" in text and "[IP]" in text


def test_outcomes_and_docs():
    docs = {d["metadata"]["number"]: d for d in build_documents(CHANGES, INCIDENTS)}
    assert docs["CHG1"]["metadata"]["outcome"] == "successful"
    assert docs["CHG2"]["metadata"]["outcome"] == "caused_incident"
    assert "INC1" in docs["CHG2"]["text"]
    assert docs["CHG9"]["metadata"]["phase"] == "upcoming"
    assert docs["INC1"]["metadata"]["doc_type"] == "incident"
    assert outcome_of(chg("X", "x", "d", close="unsuccessful"), []) == "failed"


def test_latest_version_wins():
    old = chg("CHG1", "s1", "old text", updated="2026-01-01 00:00:00")
    new = chg("CHG1", "s1", "new text", updated="2026-02-01 00:00:00")
    docs = build_documents([old, new], [])
    assert len(docs) == 1 and "new text" in docs[0]["text"]


def test_embedding_is_incremental():
    embedder, store = FakeEmbedder(), InMemoryVectorStore()
    docs = build_documents(CHANGES, INCIDENTS)
    manifest = embed_documents(docs, embedder, store, {})
    first = embedder.calls
    assert first == len(docs)
    embed_documents(docs, embedder, store, manifest)
    assert embedder.calls == first  # nothing changed, nothing re-embedded
    embed_documents(docs, embedder, store, manifest, force=True)
    assert embedder.calls == 2 * first


def test_facts_stats_and_overlap():
    facts = FactsStore(CHANGES, INCIDENTS)
    stats = facts.outcome_stats(cmdb_ci="db-01")
    assert stats["total_closed_changes"] == 2 and stats["failed_or_caused_incident"] == 1
    assert stats["failure_rate"] == 0.5
    assert [o["number"] for o in facts.overlapping_changes("CHG9")] == ["CHG10"]
    assert facts.upcoming_numbers() == ["CHG10", "CHG9"]


def make_runner():
    facts, embedder, store = FactsStore(CHANGES, INCIDENTS), FakeEmbedder(), InMemoryVectorStore()
    embed_documents(build_documents(CHANGES, INCIDENTS), embedder, store, {})
    return ToolRunner(facts, embedder, store)


def test_search_only_returns_history_and_excludes_self():
    runner = make_runner()
    res = runner.run("search_similar_changes", {"number": "CHG9", "top_k": 5})
    numbers = [m["number"] for m in res["matches"]]
    assert "CHG9" not in numbers and "CHG10" not in numbers  # upcoming excluded
    assert numbers[0] in {"CHG1", "CHG2"}
    assert {"CHG1", "CHG2"} <= runner.seen_ids


def test_validation_rejects_uncited_ids():
    problems = validate_assessment(
        {"risk": "high", "confidence": 0.5, "summary": "s", "reasons": [], "recommended_checks": [],
         "evidence_ids": ["CHG404"]}, {"CHG1"}, "CHG9")
    assert any("not retrieved" in p for p in problems)


class FakeBedrock:
    """Scripted Converse responses: each entry is a list of toolUse blocks."""

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        blocks = self.script.pop(0)
        return {"output": {"message": {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": f"t{len(self.calls)}-{i}", "name": n, "input": a}}
            for i, (n, a) in enumerate(blocks)]}}}


GOOD = {"risk": "high", "confidence": 0.8, "summary": "Similar change caused an incident.",
        "similar_changes": [{"id": "CHG2", "outcome": "caused_incident", "similarity": 0.9}],
        "reasons": ["CHG2 caused INC1"], "recommended_checks": ["Verify backout"],
        "evidence_ids": ["CHG2", "INC1"]}


def test_agent_happy_path():
    bedrock = FakeBedrock([
        [("search_similar_changes", {"number": "CHG9"}), ("get_outcome_stats", {"cmdb_ci": "db-01"})],
        [("get_change", {"number": "CHG2"})],
        [("submit_assessment", GOOD)],
    ])
    out = ChangeRiskAgent(bedrock, "model-x", make_runner()).assess("CHG9")
    assert out["risk"] == "high" and out["change"] == "CHG9" and out["model_id"] == "model-x"
    assert out["tool_calls"][-1] == "submit_assessment"
    assert bedrock.calls[0]["toolConfig"]["toolChoice"] == {"any": {}}


def test_agent_rejects_hallucinated_citation_then_accepts_fix():
    bad = {**GOOD, "evidence_ids": ["CHG777"]}
    bedrock = FakeBedrock([
        [("search_similar_changes", {"number": "CHG9"}), ("get_change", {"number": "CHG2"})],
        [("submit_assessment", bad)],
        [("submit_assessment", GOOD)],
    ])
    out = ChangeRiskAgent(bedrock, "m", make_runner()).assess("CHG9")
    assert out["evidence_ids"] == ["CHG2", "INC1"]
    rejection = bedrock.calls[2]["messages"][4]["content"][0]["toolResult"]
    assert rejection["status"] == "error" and "CHG777" in rejection["content"][0]["text"]


def test_agent_gives_up_after_repeated_bad_submissions():
    bad = {**GOOD, "evidence_ids": ["CHG777"]}
    bedrock = FakeBedrock([[("submit_assessment", bad)]] * 3)
    with pytest.raises(AssessmentError):
        ChangeRiskAgent(bedrock, "m", make_runner()).assess("CHG9")


def test_titan_request_shape():
    class Client:
        def invoke_model(self, **kw):
            self.kw = kw
            return {"body": io.BytesIO(json.dumps({"embedding": [0.1, 0.2]}).encode())}

    client = Client()
    assert BedrockEmbedder(client).embed("hello") == [0.1, 0.2]
    body = json.loads(client.kw["body"])
    assert body["dimensions"] == 1024 and body["normalize"] is True
    assert client.kw["modelId"] == "amazon.titan-embed-text-v2:0"
