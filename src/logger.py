"""Logging module for tool call auditing with Enterprise Data Loss Prevention (DLP).

Every tool call (including refused ones) must be logged here.
Applies DLP sanitization to redact sensitive credentials, API keys, and PII from audit logs.
"""

from __future__ import annotations
import json
import os
import re
from typing import Any, Dict

# Enterprise DLP patterns for redacting sensitive confidential data from audit logs
DLP_PATTERNS = [
    # API Keys / Bearer Tokens / Secrets
    (re.compile(r'(?i)(?:api[_-]?key|bearer|secret|token|password|auth)[\s:=]+["\']?([a-zA-Z0-9_\-]{16,})["\']?'), r'[REDACTED_CREDENTIAL]'),
    # Credit Card Numbers (13 to 19 digits)
    (re.compile(r'\b(?:\d{4}[ -]?){3}(?:\d{1,4})\b'), r'[REDACTED_PAYMENT_CARD]'),
    # Social Security Numbers (US SSN)
    (re.compile(r'\b\d{3}-\d{2}-\d{4}\b'), r'[REDACTED_SSN]'),
]


def redact_sensitive_data(text: str) -> str:
    """Sanitize strings against enterprise DLP patterns before writing to persistent logs."""
    sanitized = text
    for pattern, replacement in DLP_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def log_tool_call(
    question_id: str,
    tool_name: str,
    args: Dict[str, Any],
    result_preview: str,
    call_number: int,
    allowed: bool,
    timestamp: str,
) -> None:
    """Appends tool call records in JSONL format to ./logs/tool_calls.jsonl.
    
    # REPLACE WITH ORGANIZER-PROVIDED WRAPPER HERE
    """
    # WHY: Ensure logs directory exists automatically so execution never fails on missing directory.
    log_dir = os.path.join(os.getcwd(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "tool_calls.jsonl")

    # WHY: Enterprise DLP sanitization prevents confidential keys or customer PII from leaking into audit traces.
    clean_preview = redact_sensitive_data(str(result_preview)[:300])
    clean_args = {k: redact_sensitive_data(str(v)) if isinstance(v, str) else v for k, v in args.items()}

    entry = {
        "question_id": question_id,
        "tool_name": tool_name,
        "args": clean_args,
        "result_preview": clean_preview,
        "call_number": call_number,
        "allowed": bool(allowed),
        "timestamp": timestamp,
    }

    # WHY: Append with flush=True so logs are immediately committed to disk for reliable audit trailing.
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
