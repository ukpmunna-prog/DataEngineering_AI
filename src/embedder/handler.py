"""Build documents from the raw zone, embed new/changed ones with Bedrock, upsert to the vector index.

Idempotent: vectors are keyed on sys_id, and a manifest of content hashes (plus the embedding
model id) in S3 means unchanged documents are not re-embedded.
Event: {} (optionally {"force": true} to re-embed everything).
"""
import os

from src.common.config import get_logger
from src.common.s3io import read_json, read_jsonl_prefix, write_json
from src.doc_builder.builder import build_documents
from src.embedder.bedrock import BedrockEmbedder
from src.embedder.store import S3VectorsStore

log = get_logger(__name__)
MANIFEST_KEY = "state/embed_manifest.json"


def embed_documents(docs: list, embedder, store, manifest: dict, force: bool = False) -> dict:
    """Returns the updated manifest. Embeds only documents whose hash (or model) changed."""
    new_manifest = dict(manifest)
    pending, skipped = [], 0
    for doc in docs:
        stamp = f"{embedder.model_id}:{doc['content_hash']}"
        if not force and manifest.get(doc["key"]) == stamp:
            skipped += 1
            continue
        pending.append((doc, stamp))

    items = []
    for doc, stamp in pending:
        meta = {**doc["metadata"], "embedding_model": embedder.model_id}
        items.append({"key": doc["key"], "vector": embedder.embed(doc["text"]), "metadata": meta})
        new_manifest[doc["key"]] = stamp
    if items:
        store.upsert(items)
    log.info("embedding done", extra={"ctx": {"embedded": len(items), "skipped": skipped}})
    return new_manifest


def handler(event, context=None):
    bucket = os.environ["RAW_BUCKET"]
    changes = read_jsonl_prefix(bucket, "raw/change_request/")
    incidents = read_jsonl_prefix(bucket, "raw/incident/")
    docs = build_documents(changes, incidents)

    embedder = BedrockEmbedder(model_id=os.environ.get("EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0"))
    store = S3VectorsStore(os.environ["VECTOR_BUCKET"], os.environ["VECTOR_INDEX"])
    manifest = read_json(bucket, MANIFEST_KEY, {})
    new_manifest = embed_documents(docs, embedder, store, manifest, force=bool(event.get("force")))
    write_json(bucket, MANIFEST_KEY, new_manifest)
    return {"documents": len(docs), "manifest_size": len(new_manifest)}
