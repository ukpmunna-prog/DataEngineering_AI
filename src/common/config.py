"""Shared configuration loading and logging for sn-change-intel."""
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class TableConfig:
    table: str
    fields: list
    watermark_column: str = "sys_updated_on"
    overlap_minutes: int = 120
    page_size: int = 1000
    extra_query: str = ""
    s3_prefix: str = ""

    def __post_init__(self):
        if not self.table or not self.fields:
            raise ValueError("table and fields are required")
        if self.watermark_column not in self.fields:
            raise ValueError(f"watermark_column {self.watermark_column} must be listed in fields")
        if not 1 <= self.page_size <= 10000:
            raise ValueError("page_size must be between 1 and 10000")


def load_table_config(table: str, config_dir: str | None = None) -> TableConfig:
    base = Path(config_dir or os.environ.get("CONFIG_DIR", "config/tables"))
    path = base / f"{table}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No config for table '{table}' at {path}")
    raw = yaml.safe_load(path.read_text())
    raw.setdefault("s3_prefix", f"raw/{raw['table']}")
    return TableConfig(**raw)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        extra = getattr(record, "ctx", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload, default=str)


def get_logger(name: str = "sn-change-intel") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
        logger.propagate = False
    return logger
