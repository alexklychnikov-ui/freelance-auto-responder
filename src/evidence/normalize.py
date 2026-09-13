from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser


_WS_RE = re.compile(r"[ \t\f\v]+")
_NL_RE = re.compile(r"\n{3,}")


class _HTMLTextExtractor(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "template"})
    _BLOCK = frozenset(
        {
            "p",
            "div",
            "br",
            "tr",
            "li",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "section",
            "article",
            "header",
            "footer",
            "main",
            "table",
            "thead",
            "tbody",
            "ul",
            "ol",
            "dl",
            "dt",
            "dd",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._parts: list[str] = []
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        t = tag.lower()
        if t in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if t == "title":
            self._in_title = True
            return
        if t in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if t == "title":
            self._in_title = False
            return
        if t in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
            return
        self._parts.append(data)


def _collapse_ws(text: str) -> str:
    lines = [_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    out = "\n".join(line for line in lines if line)
    return _NL_RE.sub("\n\n", out).strip()


@dataclass(frozen=True)
class NormalizedDocument:
    text: str
    title: str | None


def html_to_text(html: str) -> NormalizedDocument:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # Broken HTML — best-effort from whatever was parsed
        pass
    title = _collapse_ws(" ".join(parser._title_parts)) or None
    body = _collapse_ws("".join(parser._parts))
    if title and title not in body:
        text = f"{title}\n{body}".strip() if body else title
    else:
        text = body
    return NormalizedDocument(text=text, title=title)


def normalize_content(raw: str, content_type: str | None) -> NormalizedDocument:
    """HTML → text (strip script/style, collapse ws, keep title); plain text pass-through."""
    ct = (content_type or "").split(";")[0].strip().lower()
    if "html" in ct or (not ct and _looks_like_html(raw)):
        return html_to_text(raw)
    text = _collapse_ws(raw) if raw else ""
    return NormalizedDocument(text=text, title=None)


def _looks_like_html(raw: str) -> bool:
    sample = raw.lstrip()[:200].lower()
    return sample.startswith("<!doctype html") or sample.startswith("<html") or "<html" in sample
