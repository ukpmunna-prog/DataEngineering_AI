"""Best-effort PII masking applied before any text is embedded or sent to an LLM.

Regex masking is a safety net, not a guarantee. Review with your data governance team.
"""
import re

_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[EMAIL]"),
    (re.compile(r"https?://[^\s:@/]+:[^\s@/]+@\S+"), "[URL_WITH_CREDENTIALS]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP]"),
    (re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)"), "[PHONE]"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\b\s*[:=]\s*\S+"), r"\1=[REDACTED]"),
]


def mask_pii(text: str) -> str:
    if not text:
        return ""
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text
