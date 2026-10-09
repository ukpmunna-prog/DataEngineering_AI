"""Titan Text Embeddings v2 via Amazon Bedrock."""
import json
import time

import boto3
from botocore.exceptions import ClientError

DEFAULT_MODEL = "amazon.titan-embed-text-v2:0"
THROTTLE_CODES = {"ThrottlingException", "ServiceUnavailableException", "ModelTimeoutException"}


class BedrockEmbedder:
    def __init__(self, client=None, model_id: str = DEFAULT_MODEL, dimensions: int = 1024,
                 max_retries: int = 5, backoff: float = 1.5):
        self.client = client or boto3.client("bedrock-runtime")
        self.model_id = model_id
        self.dimensions = dimensions
        self.max_retries = max_retries
        self.backoff = backoff

    def embed(self, text: str) -> list:
        body = json.dumps({"inputText": text[:20000], "dimensions": self.dimensions, "normalize": True})
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.client.invoke_model(modelId=self.model_id, body=body,
                                                contentType="application/json", accept="application/json")
                return json.loads(resp["body"].read())["embedding"]
            except ClientError as exc:
                code = exc.response["Error"]["Code"]
                if code not in THROTTLE_CODES or attempt == self.max_retries:
                    raise
                time.sleep(self.backoff ** attempt)
        raise RuntimeError("unreachable")
