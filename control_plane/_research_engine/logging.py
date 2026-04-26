# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Research Engine, proprietary and private.

"""Structured logging helpers for stage calls."""

from __future__ import annotations

import json
import logging
import os
from typing import Any


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"))


def get_logger(name: str = "crowe_research") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        fmt = os.environ.get("RESEARCH_LOG_FORMAT", "json").lower()
        if fmt == "text":
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            )
        else:
            handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
        level = os.environ.get("RESEARCH_LOG_LEVEL", "INFO").upper()
        logger.setLevel(getattr(logging, level, logging.INFO))
    return logger


def extract_usage_tokens(usage: Any) -> dict[str, int]:
    """Pull token counts from an Anthropic Usage object into a flat dict.

    Cache fields can be absent or None depending on whether caching was used.
    """
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        "cache_creation_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    }
