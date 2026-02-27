from __future__ import annotations

import re


def normalize_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "si"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    return None


def sanitize_plain_text(text: str) -> str:
    out = text.replace("*", "").replace("`", "")
    out = re.sub(r"\s+\n", "\n", out)
    return out.strip()

