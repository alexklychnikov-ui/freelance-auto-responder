from __future__ import annotations

import re
from collections.abc import Sequence

GENERIC_ACTION_OPENERS: tuple[str, ...] = (
    "Реализую",
    "Создам",
    "Сделаю",
    "Разработаю",
    "Соберу",
)

_HELLO_RE = re.compile(
    r"^\s*(?:здравствуйте|добрый\s+(?:день|вечер|утро)|привет)[!.,]?\s*",
    re.I,
)
_OPENER_RE = re.compile(
    r"^(?:я\s+)?("
    + "|".join(re.escape(w) for w in GENERIC_ACTION_OPENERS)
    + r")\b",
    re.I,
)


def _lines_after_hello(text: str) -> list[str]:
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines:
        return []
    first = _HELLO_RE.sub("", lines[0]).strip()
    rest = lines[1:]
    if first:
        return [first, *rest]
    return rest


def detect_generic_action_opener(text: str) -> str | None:
    for line in _lines_after_hello(text):
        match = _OPENER_RE.match(line)
        if match:
            found = match.group(1)
            for opener in GENERIC_ACTION_OPENERS:
                if opener.lower() == found.lower():
                    return opener
            return found
        break
    return None


def has_generic_action_opener(text: str) -> bool:
    return detect_generic_action_opener(text) is not None


def missing_required_anchors(
    text: str,
    anchors: Sequence[str],
    *,
    evidence_available: bool,
) -> list[str]:
    if not evidence_available:
        return []
    haystack = text.casefold()
    missing: list[str] = []
    for anchor in anchors:
        needle = anchor.casefold()
        if needle and needle not in haystack:
            missing.append(anchor)
    return missing


def has_required_anchors(
    text: str,
    anchors: Sequence[str],
    *,
    evidence_available: bool,
) -> bool:
    return not missing_required_anchors(
        text,
        anchors,
        evidence_available=evidence_available,
    )
