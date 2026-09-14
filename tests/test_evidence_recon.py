from __future__ import annotations

import pytest

from src.evidence.collector import EvidenceService
from src.evidence.fetcher import SourceContent
from src.evidence.normalize import normalize_content
from src.evidence.recon import is_recon_target, run_site_recon
from src.evidence.researcher import quote_in_source
from src.models import ProjectFull

SITE = "https://shop.example"

ROBOTS = """User-agent: *
Disallow: *?page*
Disallow: /cart/
Disallow: /search/

Host: https://shop.example
Sitemap: https://shop.example
"""

ROBOTS_NO_PAGINATION = """User-agent: *
Disallow: /cart/
"""


def _urlset(locs: list[str]) -> str:
    body = "\n".join(f"<url><loc>{loc}</loc></url>" for loc in locs)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n</urlset>\n"
    )


SITEMAP = _urlset(
    [
        f"{SITE}/",
        f"{SITE}/catalog/karnizi/",
        f"{SITE}/catalog/shtori/",
        f"{SITE}/tags/belyy/",
        f"{SITE}/tags/plastik/",
        f"{SITE}/catalog/karnizi/item-1.html",
        f"{SITE}/catalog/karnizi/item-2.html",
        f"{SITE}/catalog/shtori/item-3.html",
    ]
)

SITEMAP_INDEX = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    f"<sitemap><loc>{SITE}/sitemap-1.xml</loc></sitemap>\n"
    "</sitemapindex>\n"
)

ITEM_PAGE = """<!doctype html>
<html><head>
<title>Карниз потолочный однорядный, 120 см</title>
<meta name="description" content="Карниз потолочный, купить по выгодной цене">
</head><body>
<h1>Карниз потолочный однорядный</h1>
<div class="sku">Артикул 04229</div>
<img src="/img/1.jpg"><img src="/img/2.jpg">
<div class="variant">
  <div class="opt">Длина карниза 120 см</div>
  <div class="p"><span class="old_price">250руб.</span><span class="price">223 руб.</span></div>
</div>
<div class="variant">
  <div class="opt">Длина карниза 150 см</div>
  <div class="p"><span class="old_price">330руб.</span><span class="price">289 руб.</span></div>
</div>
<div class="variant">
  <div class="opt">Длина карниза 200 см</div>
  <div class="p"><span class="old_price">400руб.</span><span class="price">348 руб.</span></div>
</div>
</body></html>
"""

PAGES = {
    f"{SITE}/robots.txt": ("text/plain; charset=utf-8", ROBOTS),
    f"{SITE}/sitemap.xml": ("application/xml", SITEMAP),
    f"{SITE}/catalog/karnizi/item-1.html": ("text/html; charset=utf-8", ITEM_PAGE),
}


def _fake_fetcher(pages: dict[str, tuple[str, str]], calls: list[str]):
    def _fetch(url, *, timeout=60.0, client=None, keep_raw=False, **kwargs):
        calls.append(url)
        entry = pages.get(url)
        if entry is None:
            return SourceContent(
                final_url=url,
                status_code=404,
                content_type=None,
                text=None,
                error_code="http_error",
            )
        content_type, body = entry
        doc = normalize_content(body, content_type)
        return SourceContent(
            final_url=url,
            status_code=200,
            content_type=content_type,
            text=doc.text,
            error_code=None,
            title=doc.title,
            raw_text=body if keep_raw else None,
        )

    return _fetch


@pytest.fixture()
def recon_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr("src.evidence.recon.fetch_url", _fake_fetcher(PAGES, calls))
    return calls


def _fact(result, kind_anchor: str):
    return next((f for f in result.facts if kind_anchor in f.claim), None)


def test_recon_counts_sitemap_urls_by_type(recon_calls: list[str]) -> None:
    result = run_site_recon(SITE, max_pages=3)

    total = _fact(result, "В sitemap 8 URL")
    assert total is not None
    assert "3 страниц-элементов" in total.claim
    assert total.anchors == ["8 URL", "3 страниц"]
    assert total.verification == "structured_value"

    catalog = _fact(result, "раздела /catalog/")
    tags = _fact(result, "раздела /tags/")
    assert catalog is not None and "В sitemap 2 URL" in catalog.claim
    assert tags is not None and "В sitemap 2 URL" in tags.claim


def test_recon_detects_blocked_pagination(recon_calls: list[str]) -> None:
    result = run_site_recon(SITE, max_pages=3)

    robots = _fact(result, "robots.txt")
    assert robots is not None
    assert "3 правил Disallow" in robots.claim
    assert "Disallow: *?page*" in robots.anchors
    assert all(len(a) >= 4 for a in robots.anchors)
    assert any(
        i.kind == "architecture" and "*?page*" in i.finding for i in result.insights
    )
    assert any(
        "sitemap" in i.implementation_consequence for i in result.insights
    )


def test_recon_extracts_price_variants_and_sku(recon_calls: list[str]) -> None:
    result = run_site_recon(SITE, max_pages=3)

    variants = _fact(result, "вариантов опции")
    assert variants is not None
    assert "3 вариантов опции «длина карниза»" in variants.claim
    assert "есть пары «старая цена + цена»" in variants.claim
    assert "ценовых значений" not in variants.claim
    assert variants.anchors == ["длина карниза", "3 вариантов"]
    assert all(len(a) >= 4 for a in variants.anchors)

    structure = _fact(result, "Артикул 04229")
    assert structure is not None
    assert "изображений" not in structure.claim
    assert "есть meta title и description" in structure.claim
    assert structure.anchors == ["04229", "Артикул"]

    scope = next(i for i in result.insights if i.kind == "scope")
    assert "с отдельной ценой" in scope.finding
    assert "отдельной строкой" in scope.implementation_consequence


def test_recon_facts_quote_real_source_text(recon_calls: list[str]) -> None:
    result = run_site_recon(SITE, max_pages=3)

    assert result.facts
    texts = result.texts
    for fact in result.facts:
        assert fact.eligible_for_response is True
        assert quote_in_source(fact.quote, texts[fact.source_id])


def test_recon_follows_sitemap_index_and_still_loads_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    pages = dict(PAGES)
    pages[f"{SITE}/sitemap.xml"] = ("application/xml", SITEMAP_INDEX)
    pages[f"{SITE}/sitemap-1.xml"] = ("application/xml", SITEMAP)
    monkeypatch.setattr("src.evidence.recon.fetch_url", _fake_fetcher(pages, calls))

    result = run_site_recon(SITE, max_pages=1)

    assert calls[:3] == [
        f"{SITE}/robots.txt",
        f"{SITE}/sitemap.xml",
        f"{SITE}/sitemap-1.xml",
    ]
    assert any(c.endswith(".html") for c in calls)
    assert _fact(result, "В sitemap 8 URL") is not None
    assert _fact(result, "вариантов опции") is not None


def test_recon_fail_open_when_sitemap_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    pages = {f"{SITE}/robots.txt": ("text/plain", ROBOTS)}
    monkeypatch.setattr("src.evidence.recon.fetch_url", _fake_fetcher(pages, calls))

    result = run_site_recon(SITE, max_pages=3)

    assert any("sitemap.xml" in w for w in result.warnings)
    assert [s.input_ref for s in result.sources] == [f"{SITE}/robots.txt"]
    assert _fact(result, "robots.txt") is not None
    assert all(s.status == "verified" for s in result.sources)


def test_recon_disabled_with_zero_pages(recon_calls: list[str]) -> None:
    result = run_site_recon(SITE, max_pages=0)

    assert recon_calls == []
    assert result.facts == []
    assert result.sources == []
    assert result.insights == []


def test_recon_respects_sample_page_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_pages limits sample item pages; robots/sitemap always fetched."""
    calls: list[str] = []
    monkeypatch.setattr("src.evidence.recon.fetch_url", _fake_fetcher(PAGES, calls))

    result = run_site_recon(SITE, max_pages=1)

    assert calls[0] == f"{SITE}/robots.txt"
    assert calls[1] == f"{SITE}/sitemap.xml"
    assert any(c.endswith(".html") for c in calls)
    assert _fact(result, "вариантов опции") is not None


def test_recon_insight_omits_priced_claim_without_price_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = """<!doctype html><html><body>
    <div>Длина карниза 120 см</div>
    <div>Длина карниза 150 см</div>
    <div>Длина карниза 200 см</div>
    </body></html>"""
    calls: list[str] = []
    pages = dict(PAGES)
    pages[f"{SITE}/catalog/karnizi/item-1.html"] = ("text/html; charset=utf-8", item)
    pages[f"{SITE}/catalog/karnizi/item-2.html"] = ("text/html; charset=utf-8", item)
    pages[f"{SITE}/catalog/shtori/item-3.html"] = ("text/html; charset=utf-8", item)
    monkeypatch.setattr("src.evidence.recon.fetch_url", _fake_fetcher(pages, calls))

    result = run_site_recon(SITE, max_pages=1, sample_url=f"{SITE}/catalog/karnizi/item-1.html")
    scope = next(i for i in result.insights if i.kind == "scope")
    assert "с отдельной ценой" not in scope.finding
    assert "отдельной строкой" not in scope.implementation_consequence


def test_recon_without_pagination_rule_has_no_architecture_insight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    pages = dict(PAGES)
    pages[f"{SITE}/robots.txt"] = ("text/plain", ROBOTS_NO_PAGINATION)
    monkeypatch.setattr("src.evidence.recon.fetch_url", _fake_fetcher(pages, calls))

    result = run_site_recon(SITE, max_pages=3)

    assert all(i.kind != "architecture" for i in result.insights)
    assert _fact(result, "правил Disallow") is not None


def test_is_recon_target_skips_feeds_and_non_http() -> None:
    assert is_recon_target(f"{SITE}/catalog/") is True
    assert is_recon_target(f"{SITE}/sitemap.xml") is False
    assert is_recon_target(f"{SITE}/robots.txt") is False
    assert is_recon_target("ftp://shop.example/") is False


def _project(desc: str, title: str = "Спарсить контент с сайта") -> ProjectFull:
    return ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3252551",
        url="https://kwork.ru/projects/3252551",
        title=title,
        full_description=desc,
    )


PARSE_TZ = (
    "Нужно спарсить контент с сайта https://shop.example и выгрузить в таблицу: "
    "название, цена, артикул, фото."
)
NON_PARSE_TZ = (
    "Нужно сделать редизайн лендинга по образцу https://shop.example, "
    "аналогичный стиль и блоки."
)


def _patch_collector_fetch(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    pages = dict(PAGES)
    pages[SITE] = ("text/html; charset=utf-8", ITEM_PAGE)
    fetcher = _fake_fetcher(pages, calls)
    monkeypatch.setattr("src.evidence.collector.fetch_url", fetcher)
    monkeypatch.setattr("src.evidence.recon.fetch_url", fetcher)
    return calls


def test_collect_runs_recon_for_parsing_task(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_collector_fetch(monkeypatch)

    bundle = EvidenceService().collect(_project(PARSE_TZ), recon_max_pages=3)

    assert f"{SITE}/robots.txt" in calls
    assert f"{SITE}/sitemap.xml" in calls
    refs = {s.input_ref for s in bundle.sources}
    assert f"{SITE}/robots.txt" in refs
    assert f"{SITE}/sitemap.xml" in refs
    assert any("В sitemap 8 URL" in f.claim for f in bundle.facts)
    assert any(i.kind == "architecture" for i in bundle.insights)
    assert bundle.status in ("complete", "partial")


def test_collect_skips_recon_for_non_parsing_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_collector_fetch(monkeypatch)

    bundle = EvidenceService().collect(
        _project(NON_PARSE_TZ, title="Редизайн лендинга"),
        recon_max_pages=3,
    )

    assert calls == [SITE]
    assert all("robots.txt" not in s.input_ref for s in bundle.sources)


def test_collect_skips_recon_for_collect_applications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_collector_fetch(monkeypatch)
    desc = (
        "Бот должен собирать заявки от клиентов с сайта https://shop.example "
        "и слать их менеджеру в Telegram."
    )

    bundle = EvidenceService().collect(
        _project(desc, title="Telegram-бот для заявок"),
        recon_max_pages=3,
    )

    assert calls == [SITE]
    assert all("robots.txt" not in s.input_ref for s in bundle.sources)


def test_collect_skips_recon_when_budget_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_collector_fetch(monkeypatch)

    bundle = EvidenceService().collect(_project(PARSE_TZ), recon_max_pages=0)

    assert calls == [SITE]
    assert all("sitemap" not in s.input_ref for s in bundle.sources)
