from __future__ import annotations

import hashlib
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from src.evidence.fetcher import SourceContent, fetch_url
from src.evidence.models import EvidenceFact, EvidenceInsight, EvidenceSource
from src.evidence.researcher import quote_in_source, verify_fact_quote

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
_DISALLOW_RE = re.compile(r"(?:^|\n)\s*Disallow:\s*(\S+)", re.IGNORECASE)
_SITEMAP_DECL_RE = re.compile(r"(?:^|\n)\s*Sitemap:\s*(\S+)", re.IGNORECASE)
_PAGINATION_RE = re.compile(r"\?page|&page|/page/|PAGEN|\?p=", re.IGNORECASE)
_ITEM_PATH_RE = re.compile(r"\.(?:html?|php|aspx)$", re.IGNORECASE)
_SITEMAP_INDEX_RE = re.compile(r"<sitemapindex", re.IGNORECASE)

_PRICE_RE = re.compile(
    r"(?<!\d)(\d[\d\u00a0\u2009 ]{0,9})\s*(?:руб\.?|₽|RUB)",
    re.IGNORECASE,
)
_UNIT_RE = re.compile(
    r"(?<!\d)(\d{1,5}(?:[.,]\d{1,2})?)\s*(?:см|мм|м|кг|г|мл|л|шт)"
    r"(?![А-Яа-яЁёA-Za-z])"
)
_WORD_RE = re.compile(r"[А-Яа-яЁёA-Za-z]{3,}")
_SKU_RE = re.compile(
    r"(Артикул|Код товара|SKU|Арт\.)\s*[:№]?\s*([A-Za-z0-9][A-Za-z0-9/._-]{1,19})",
    re.IGNORECASE,
)
_IMG_RE = re.compile(r"<img[\s>]", re.IGNORECASE)
_META_DESC_RE = re.compile(r"<meta[^>]+name=[\"']?description", re.IGNORECASE)
_TITLE_RE = re.compile(r"<title[\s>]", re.IGNORECASE)
_OLD_PRICE_MARK_RE = re.compile(r"old[_-]?price|price[_-]?old|<del[\s>]|<s>", re.IGNORECASE)

_MAX_LABEL_LOOKBEHIND = 40


@dataclass
class ReconResult:
    sources: list[EvidenceSource] = field(default_factory=list)
    facts: list[EvidenceFact] = field(default_factory=list)
    insights: list[EvidenceInsight] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    texts: dict[str, str] = field(default_factory=dict)


@dataclass
class _Page:
    url: str
    text: str
    raw: str
    source_id: str


def _source_id(input_ref: str) -> str:
    return hashlib.sha256(input_ref.encode("utf-8")).hexdigest()[:16]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _site_root(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def is_recon_target(url: str) -> bool:
    """Target site for parsing recon: plain http(s) page, not a feed/file itself."""
    if not _site_root(url):
        return False
    path = (urlparse(url).path or "").lower()
    return not path.endswith((".xml", ".txt", ".json", ".pdf", ".zip"))


def _first_path_segment(url: str) -> str:
    path = (urlparse(url).path or "").strip("/")
    if not path:
        return ""
    return path.split("/", 1)[0]


def _parse_disallow(text: str) -> list[str]:
    out: list[str] = []
    for m in _DISALLOW_RE.finditer(text):
        rule = m.group(1).strip()
        if rule and rule not in out:
            out.append(rule)
    return out


def _declared_sitemap(text: str, root: str) -> str | None:
    for m in _SITEMAP_DECL_RE.finditer(text):
        raw = m.group(1).strip()
        path = (urlparse(raw).path or "").lower()
        if "sitemap" not in path and not path.endswith(".xml"):
            continue
        absolute = urljoin(root + "/", raw)
        if _host(absolute) != _host(root):
            continue
        return absolute
    return None


def _price_values(text: str) -> list[tuple[int, int, int]]:
    """(value, span_start, span_end) for every price-like token in page text."""
    out: list[tuple[int, int, int]] = []
    for m in _PRICE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if not digits:
            continue
        out.append((int(digits), m.start(), m.end()))
    return out


def _price_pairs(values: list[tuple[int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Adjacent (old > new) price pairs — the «старая цена + цена» layout."""
    pairs: list[tuple[int, int, int, int]] = []
    i = 0
    while i + 1 < len(values):
        old, start, _ = values[i]
        new, _, end = values[i + 1]
        if old > new > 0:
            pairs.append((old, new, start, end))
            i += 2
        else:
            i += 1
    return pairs


def _variant_options(text: str) -> tuple[str, list[str], str]:
    """Most repeated «label + number + unit» option axis: (label, values, quote)."""
    groups: dict[str, tuple[list[str], str]] = {}
    for m in _UNIT_RE.finditer(text):
        left_start = max(0, m.start() - _MAX_LABEL_LOOKBEHIND)
        left = text[left_start : m.start()]
        words = _WORD_RE.findall(left)
        if not words:
            continue
        label = " ".join(words[-2:])
        quote_start = left_start + left.rfind(words[-1])
        values, quote = groups.get(label.casefold(), ([], text[quote_start : m.end()]))
        if m.group(1) not in values:
            values.append(m.group(1))
        groups[label.casefold()] = (values, quote)
    if not groups:
        return "", [], ""
    best = sorted(groups.items(), key=lambda kv: (-len(kv[1][0]), kv[0]))[0]
    label_cf, (values, quote) = best
    if len(values) < 2:
        return "", [], ""
    return label_cf, values, quote


def _make_source(url: str, content: SourceContent) -> EvidenceSource:
    return EvidenceSource(
        id=_source_id(url),
        kind="url",
        role="data_source",
        input_ref=url,
        final_url=content.final_url,
        title=content.title,
        fetch_method="http",
        status="verified",
        http_status=content.status_code,
        content_type=content.content_type,
        retrieved_at=datetime.now(timezone.utc),
        content_hash=_content_hash(content.text or ""),
    )


def _add_fact(
    facts: list[EvidenceFact],
    *,
    source_id: str,
    kind: str,
    claim: str,
    quote: str,
    anchors: list[str],
    relevance: float,
    text: str,
) -> EvidenceFact | None:
    quote = (quote or "").strip()
    if not quote or not quote_in_source(quote, text):
        return None
    fact = EvidenceFact(
        id=hashlib.sha256(f"{source_id}:{kind}:{quote}".encode("utf-8")).hexdigest()[:16],
        source_id=source_id,
        claim=claim,
        quote=quote,
        anchors=anchors,
        relevance=relevance,
        verification="structured_value",
    )
    verified = verify_fact_quote(fact, text)
    facts.append(verified)
    return verified


def _sitemap_facts(
    page: _Page,
    result: ReconResult,
) -> tuple[list[str], EvidenceFact | None]:
    body = page.raw or page.text
    locs = [loc for loc in _LOC_RE.findall(body) if loc.lower().startswith("http")]
    if not locs:
        result.warnings.append(f"recon: no <loc> in {page.url}")
        return [], None

    items = [loc for loc in locs if _ITEM_PATH_RE.search(urlparse(loc).path or "")]
    rest = [loc for loc in locs if not _ITEM_PATH_RE.search(urlparse(loc).path or "")]
    segments = Counter(seg for seg in (_first_path_segment(u) for u in rest) if seg)

    item_fact = _add_fact(
        result.facts,
        source_id=page.source_id,
        kind="sitemap_total",
        claim=(
            f"В sitemap {len(locs)} URL, из них {len(items)} страниц-элементов "
            f"(*.html/*.php)"
        ),
        quote=items[0] if items else locs[0],
        anchors=[f"{len(locs)} URL", f"{len(items)} страниц"],
        relevance=0.95,
        text=page.text,
    )

    for seg, count in segments.most_common(2):
        if count < 2:
            continue
        example = next(u for u in rest if _first_path_segment(u) == seg)
        _add_fact(
            result.facts,
            source_id=page.source_id,
            kind=f"sitemap_segment_{seg}",
            claim=f"В sitemap {count} URL раздела /{seg}/",
            quote=example,
            anchors=[f"/{seg}/", f"{count} URL"],
            relevance=0.8,
            text=page.text,
        )

    return items, item_fact


def _robots_facts(page: _Page, result: ReconResult) -> tuple[list[str], EvidenceFact | None]:
    rules = _parse_disallow(page.raw or page.text)
    if not rules:
        return [], None
    blocked = next((r for r in rules if _PAGINATION_RE.search(r)), None)
    if blocked:
        claim = (
            f"robots.txt содержит {len(rules)} правил Disallow и закрывает пагинацию "
            f"правилом Disallow: {blocked}"
        )
        quote = f"Disallow: {blocked}"
        anchors = [f"Disallow: {blocked}"]
    else:
        claim = f"robots.txt содержит {len(rules)} правил Disallow"
        quote = f"Disallow: {rules[0]}"
        anchors = [f"Disallow: {rules[0]}"]
    fact = _add_fact(
        result.facts,
        source_id=page.source_id,
        kind="robots",
        claim=claim,
        quote=quote,
        anchors=anchors,
        relevance=0.9,
        text=page.text,
    )
    return rules, fact if blocked else None


def _item_page_facts(
    page: _Page,
    result: ReconResult,
) -> tuple[int, str, bool, EvidenceFact | None]:
    values = _price_values(page.text)
    pairs = _price_pairs(values)
    label, variants, variant_quote = _variant_options(page.text)
    has_old_price = bool(pairs) or bool(_OLD_PRICE_MARK_RE.search(page.raw))
    pair_note = (
        "есть пары «старая цена + цена»"
        if has_old_price
        else "без пары «старая цена + цена»"
    )
    priced_variants = (
        bool(variants)
        and (len(pairs) >= len(variants) or len(values) >= len(variants))
    )

    price_fact: EvidenceFact | None = None
    if variants:
        price_fact = _add_fact(
            result.facts,
            source_id=page.source_id,
            kind="item_variants",
            claim=(
                f"На карточке товара {len(variants)} вариантов опции «{label}», "
                f"{pair_note}"
            ),
            quote=variant_quote,
            anchors=[label, f"{len(variants)} вариантов"],
            relevance=0.95,
            text=page.text,
        )
    elif pairs:
        old, new, start, end = pairs[0]
        price_fact = _add_fact(
            result.facts,
            source_id=page.source_id,
            kind="item_prices",
            claim="На карточке товара есть пары «старая цена + цена»",
            quote=page.text[start:end],
            anchors=[f"{old} руб", f"{new} руб"],
            relevance=0.9,
            text=page.text,
        )

    sku_match = _SKU_RE.search(page.text)
    has_meta = bool(_TITLE_RE.search(page.raw)) and bool(_META_DESC_RE.search(page.raw))
    if sku_match:
        meta_note = "есть meta title и description" if has_meta else "meta description нет"
        _add_fact(
            result.facts,
            source_id=page.source_id,
            kind="item_structure",
            claim=(
                f"На карточке товара есть {sku_match.group(1)} {sku_match.group(2)}, "
                f"{meta_note}"
            ),
            quote=sku_match.group(0),
            anchors=[sku_match.group(2), sku_match.group(1)],
            relevance=0.85,
            text=page.text,
        )
    return len(variants), label, priced_variants, price_fact


def _build_insights(
    *,
    items_count: int,
    variants_count: int,
    variant_label: str,
    priced_variants: bool,
    pagination_rule: str | None,
    sitemap_fact: EvidenceFact | None,
    price_fact: EvidenceFact | None,
    robots_fact: EvidenceFact | None,
) -> list[EvidenceInsight]:
    insights: list[EvidenceInsight] = []
    if items_count:
        fact_ids = [f.id for f in (sitemap_fact, price_fact) if f is not None]
        if variants_count and priced_variants:
            variants_note = (
                f", а на карточке {variants_count} вариантов опции «{variant_label}» "
                f"с отдельной ценой"
            )
            consequence_extra = "; каждый ценовой вариант выгружается отдельной строкой"
        elif variants_count:
            variants_note = (
                f", а на карточке {variants_count} вариантов опции «{variant_label}»"
            )
            consequence_extra = ""
        else:
            variants_note = ""
            consequence_extra = ""
        insights.append(
            EvidenceInsight(
                fact_ids=fact_ids,
                finding=(
                    f"В sitemap {items_count} страниц-элементов{variants_note}"
                ),
                implementation_consequence=(
                    f"Объём обхода — {items_count} карточек пачками с докачкой после "
                    "обрыва"
                    + consequence_extra
                ),
                kind="scope",
            )
        )
    if pagination_rule:
        insights.append(
            EvidenceInsight(
                fact_ids=[f.id for f in (robots_fact, sitemap_fact) if f is not None],
                finding=(
                    f"robots.txt закрывает пагинацию правилом Disallow: {pagination_rule}"
                ),
                implementation_consequence=(
                    "Полный список URL берём из sitemap, а не обходом листингов с "
                    "параметрами пагинации"
                ),
                kind="architecture",
            )
        )
    return insights[:2]


def _pick_sample_url(items: list[str], root: str) -> str:
    same = [u for u in items if _host(u) == _host(root)]
    if not same:
        return ""
    return max(same, key=lambda u: ((urlparse(u).path or "").count("/"), len(u)))


def run_site_recon(
    base_url: str,
    *,
    timeout: float = 60.0,
    max_pages: int = 3,
    client=None,
    sample_url: str | None = None,
) -> ReconResult:
    """Deterministic structural probe: robots.txt + sitemap + sample item pages."""
    result = ReconResult()
    root = _site_root(base_url)
    if not root or max_pages <= 0:
        return result

    sample_budget = max_pages
    started = time.monotonic()
    hard_deadline = started + max(float(timeout), 1.0) * 3

    def _load(url: str, *, keep_raw: bool = False, count_sample: bool = False) -> _Page | None:
        nonlocal sample_budget
        if time.monotonic() >= hard_deadline:
            result.warnings.append("recon deadline reached")
            return None
        if count_sample:
            if sample_budget <= 0:
                return None
            sample_budget -= 1
        try:
            content = fetch_url(
                url,
                timeout=timeout,
                client=client,
                keep_raw=keep_raw,
            )
        except Exception:
            result.warnings.append(f"recon fetch failed {url}: exception")
            return None
        if content.error_code or not content.text:
            result.warnings.append(
                f"recon fetch failed {url}: {content.error_code or 'empty_content'}"
            )
            return None
        source = _make_source(url, content)
        result.sources.append(source)
        result.texts[source.id] = content.text
        return _Page(
            url=url,
            text=content.text,
            raw=content.raw_text or content.text,
            source_id=source.id,
        )

    robots_page = _load(f"{root}/robots.txt", keep_raw=True)
    rules: list[str] = []
    robots_fact: EvidenceFact | None = None
    sitemap_url = f"{root}/sitemap.xml"
    if robots_page is not None:
        rules, robots_fact = _robots_facts(robots_page, result)
        sitemap_url = _declared_sitemap(robots_page.raw, root) or sitemap_url

    sitemap_page = _load(sitemap_url, keep_raw=True)
    if sitemap_page is not None and _SITEMAP_INDEX_RE.search(sitemap_page.raw):
        child = next(iter(_LOC_RE.findall(sitemap_page.raw)), "")
        if child.lower().startswith("http") and _host(child) == _host(root):
            sitemap_page = _load(child, keep_raw=True) or sitemap_page

    items: list[str] = []
    sitemap_fact: EvidenceFact | None = None
    if sitemap_page is not None:
        items, sitemap_fact = _sitemap_facts(sitemap_page, result)

    item_url = sample_url or _pick_sample_url(items, root)
    variants_count = 0
    variant_label = ""
    priced_variants = False
    price_fact: EvidenceFact | None = None
    if item_url:
        item_page = _load(item_url, keep_raw=True, count_sample=True)
        if item_page is not None:
            variants_count, variant_label, priced_variants, price_fact = _item_page_facts(
                item_page, result
            )

    pagination_rule = next((r for r in rules if _PAGINATION_RE.search(r)), None)
    result.insights.extend(
        _build_insights(
            items_count=len(items),
            variants_count=variants_count,
            variant_label=variant_label,
            priced_variants=priced_variants,
            pagination_rule=pagination_rule,
            sitemap_fact=sitemap_fact,
            price_fact=price_fact,
            robots_fact=robots_fact,
        )
    )
    return result
