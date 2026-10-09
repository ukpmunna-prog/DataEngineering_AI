"""ServiceNow Table API client with pagination and retries."""
import time
from collections.abc import Iterator

import requests

from src.common.config import TableConfig, get_logger

log = get_logger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class ServiceNowClient:
    def __init__(self, instance_url: str, username: str, password: str,
                 session: requests.Session = None, max_retries: int = 5, backoff: float = 2.0):
        self.base = instance_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.auth = (username, password)
        self.session.headers.update({"Accept": "application/json"})
        self.max_retries = max_retries
        self.backoff = backoff

    def _get(self, url: str, params: dict) -> requests.Response:
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=60)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == self.max_retries:
                    raise
                log.warning("network error, retrying", extra={"ctx": {"error": str(exc), "attempt": attempt}})
                time.sleep(self.backoff ** attempt)
                continue
            if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                wait = float(resp.headers.get("Retry-After", self.backoff ** attempt))
                log.warning("retryable status", extra={"ctx": {"status": resp.status_code, "wait": wait}})
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError("unreachable")

    def fetch_pages(self, cfg: TableConfig, since: str) -> Iterator[list]:
        """Yield pages of records updated at or after `since` ('YYYY-MM-DD HH:MM:SS', UTC).

        Ordered by the watermark column then sys_id so offset paging is stable.
        """
        query = f"{cfg.watermark_column}>={since}"
        if cfg.extra_query:
            query += f"^{cfg.extra_query}"
        query += f"^ORDERBY{cfg.watermark_column}^ORDERBYsys_id"
        url = f"{self.base}/api/now/table/{cfg.table}"
        offset = 0
        while True:
            params = {
                "sysparm_query": query,
                "sysparm_fields": ",".join(cfg.fields),
                "sysparm_limit": cfg.page_size,
                "sysparm_offset": offset,
                "sysparm_display_value": "false",
                "sysparm_exclude_reference_link": "true",
            }
            resp = self._get(url, params)
            records = resp.json().get("result", [])
            if not records:
                return
            yield records
            if len(records) < cfg.page_size:
                return
            offset += cfg.page_size
