"""Score upcoming changes and write JSON assessments to S3.

Events:
  {"change_number": "CHG0012345"}   score one change
  {"score_upcoming": true}          score every upcoming change
Env: RAW_BUCKET, VECTOR_BUCKET, VECTOR_INDEX, BEDROCK_MODEL_ID (an inference profile or model id
enabled in your account), optional EMBED_MODEL_ID.
"""
import os
from datetime import UTC, datetime

import boto3

from src.agent.agent import AssessmentError, ChangeRiskAgent
from src.agent.facts import FactsStore
from src.agent.prompts import PROMPT_VERSION
from src.agent.tools import ToolRunner
from src.common.config import get_logger
from src.common.s3io import read_jsonl_prefix, write_json
from src.embedder.bedrock import DEFAULT_MODEL, BedrockEmbedder
from src.embedder.store import S3VectorsStore

log = get_logger(__name__)


def handler(event, context=None):
    bucket = os.environ["RAW_BUCKET"]
    facts = FactsStore(read_jsonl_prefix(bucket, "raw/change_request/"), read_jsonl_prefix(bucket, "raw/incident/"))
    runtime = boto3.client("bedrock-runtime")
    embedder = BedrockEmbedder(runtime, os.environ.get("EMBED_MODEL_ID", DEFAULT_MODEL))
    vectors = S3VectorsStore(os.environ["VECTOR_BUCKET"], os.environ["VECTOR_INDEX"])
    model_id = os.environ["BEDROCK_MODEL_ID"]

    numbers = facts.upcoming_numbers() if event.get("score_upcoming") else [event["change_number"]]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    done, failed = [], []
    for number in numbers:
        agent = ChangeRiskAgent(runtime, model_id, ToolRunner(facts, embedder, vectors))
        try:
            assessment = agent.assess(number)
        except AssessmentError as exc:
            log.error("assessment failed", extra={"ctx": {"change": number, "error": str(exc)}})
            failed.append(number)
            continue
        write_json(bucket, f"assessments/{number}/{PROMPT_VERSION}-{stamp}.json", assessment)
        done.append(number)
    return {"assessed": done, "failed": failed}
