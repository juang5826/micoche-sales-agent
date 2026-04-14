from __future__ import annotations

import re
from dataclasses import dataclass


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


_ESCALATION_MARKER = re.compile(r"\[ESCALAR\]", re.IGNORECASE)
_MARKDOWN_CHARS = re.compile(r"[*`#~_]")
_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"\b\d{10,13}\b")
_ALLOWED_PHONES = {"573134246298"}
_MAX_RESPONSE_CHARS = 800


def sanitize_plain_text(text: str) -> str:
    out = _MARKDOWN_CHARS.sub("", text)
    # Limpia espacios/tabs al final de cada linea, pero preserva los saltos
    # de linea dobles que el prompt usa para separar bloques de info.
    out = re.sub(r"[ \t]+\n", "\n", out)
    # Colapsa secuencias de mas de 2 saltos de linea a solo 2 (evita que el
    # modelo deje 3 o 4 lineas vacias seguidas).
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


@dataclass
class FilteredResponse:
    text: str
    should_escalate: bool


def filter_agent_output(raw: str) -> FilteredResponse:
    should_escalate = bool(_ESCALATION_MARKER.search(raw))
    text = _ESCALATION_MARKER.sub("", raw).strip()
    text = sanitize_plain_text(text)
    text = _URL_PATTERN.sub("", text).strip()
    text = _filter_phones(text)
    if len(text) > _MAX_RESPONSE_CHARS:
        text = text[:_MAX_RESPONSE_CHARS].rsplit(" ", 1)[0] + "..."
    return FilteredResponse(text=text, should_escalate=should_escalate)


def _filter_phones(text: str) -> str:
    def _replace(match: re.Match) -> str:
        phone = match.group(0)
        return phone if phone in _ALLOWED_PHONES else ""
    return _PHONE_PATTERN.sub(_replace, text).strip()
