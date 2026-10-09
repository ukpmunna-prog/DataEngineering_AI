"""Vector store abstraction. S3 Vectors in production, in-memory for tests and local runs."""
import math
from typing import Protocol

import boto3

PUT_BATCH = 500


class VectorStore(Protocol):
    def upsert(self, items: list) -> None: ...
    def query(self, vector: list, top_k: int, filter: dict | None = None) -> list: ...


class S3VectorsStore:
    """Amazon S3 Vectors. Index must be created with the same dimension and cosine metric."""

    def __init__(self, bucket: str, index: str, client=None):
        self.bucket, self.index = bucket, index
        self.client = client or boto3.client("s3vectors")

    def upsert(self, items: list) -> None:
        for i in range(0, len(items), PUT_BATCH):
            batch = items[i:i + PUT_BATCH]
            self.client.put_vectors(
                vectorBucketName=self.bucket, indexName=self.index,
                vectors=[{"key": it["key"], "data": {"float32": it["vector"]}, "metadata": it["metadata"]}
                         for it in batch],
            )

    def query(self, vector: list, top_k: int, filter: dict | None = None) -> list:
        kwargs = {"vectorBucketName": self.bucket, "indexName": self.index,
                  "queryVector": {"float32": vector}, "topK": top_k,
                  "returnMetadata": True, "returnDistance": True}
        if filter:
            kwargs["filter"] = filter
        resp = self.client.query_vectors(**kwargs)
        return [{"key": v["key"], "distance": v.get("distance"), "metadata": v.get("metadata", {})}
                for v in resp.get("vectors", [])]


class InMemoryVectorStore:
    def __init__(self):
        self.items = {}

    def upsert(self, items: list) -> None:
        for it in items:
            self.items[it["key"]] = it

    def query(self, vector: list, top_k: int, filter: dict | None = None) -> list:
        scored = []
        for it in self.items.values():
            if filter and not _matches(it["metadata"], filter):
                continue
            scored.append({"key": it["key"], "distance": 1 - _cosine(vector, it["vector"]),
                           "metadata": it["metadata"]})
        return sorted(scored, key=lambda r: r["distance"])[:top_k]


def _cosine(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _matches(meta: dict, flt: dict) -> bool:
    if "$and" in flt:
        return all(_matches(meta, f) for f in flt["$and"])
    for field, cond in flt.items():
        if isinstance(cond, dict):
            if "$in" in cond and meta.get(field) not in cond["$in"]:
                return False
            if "$eq" in cond and meta.get(field) != cond["$eq"]:
                return False
        elif meta.get(field) != cond:
            return False
    return True
