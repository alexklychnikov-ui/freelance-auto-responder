from __future__ import annotations

import json
from pathlib import Path

from src.evidence.collector import EvidenceService
from src.evidence.models import (
    EvidenceBundle,
    EvidenceFact,
    EvidenceInsight,
    EvidenceSource,
)
from src.evidence.usage import (
    EvidenceUsageValidator,
    compact_verified_evidence,
    evidence_usage_issues,
)
from src.models import ProjectFull

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "evidence"
PENDING_3252339 = (
    Path(__file__).resolve().parents[1] / "data" / "pending_3252339.json"
)
BAD_RESPONSE = (FIXTURES / "3252339_bad_template_response.txt").read_text(encoding="utf-8")

GOOD_RESPONSE = (
    "Здравствуйте!\n"
    "По лоту 184729 на аналоге видна начальная цена 1 250 000 ₽ и дата 2026-10-15 — "
    "упор на поиск и выдачу документов, без ЛК и агентов.\n"
    "Соберу MVP поиска по извещению и вложениям под структуру ГИС, без клона кабинета.\n"
    "Срок — 10–14 дней. Стоимость — от 32 700 ₽.\n"
    "Напишите регион и 2–3 категории торгов — уточню объём парсинга."
)

TZ_3252339_FALLBACK = (
    "ЗдравствуйтеНужно разработать аналог сайт торги-россии.рф, интересует только "
    "поиск и выдача торгов с документами, без личного кабинета и прочего. "
    "Там парсится отсюда https://torgi.gov.ru"
)
ROSSII_URL = "https://торги-россии.рф/"
GOV_URL = "https://torgi.gov.ru"


def _bundle_with_eligible(*, gov_status: str = "unavailable") -> EvidenceBundle:
    return EvidenceBundle(
        status="partial",
        required=True,
        project_hash="h",
        sources=[
            EvidenceSource(
                id="src-rossii",
                kind="url",
                role="reference",
                input_ref="https://торги-россии.рф/",
                status="verified",
            ),
            EvidenceSource(
                id="src-gov",
                kind="url",
                role="data_source",
                input_ref="https://torgi.gov.ru",
                status=gov_status,  # type: ignore[arg-type]
            ),
        ],
        facts=[
            EvidenceFact(
                id="f1",
                source_id="src-rossii",
                claim="На странице указан лот №184729",
                quote="Лот №184729",
                anchors=["184729"],
                relevance=0.9,
                eligible_for_response=True,
            ),
            EvidenceFact(
                id="f2",
                source_id="src-rossii",
                claim="Указана цена 1 250 000 ₽",
                quote="1 250 000 ₽",
                anchors=["1 250 000", "1 250 000 ₽"],
                relevance=0.8,
                eligible_for_response=True,
            ),
            EvidenceFact(
                id="f3",
                source_id="src-rossii",
                claim="Указана дата 2026-10-15",
                quote="2026-10-15",
                anchors=["2026-10-15"],
                relevance=0.7,
                eligible_for_response=True,
            ),
            EvidenceFact(
                id="skip",
                source_id="src-rossii",
                claim="не для ответа",
                anchors=["SKIP"],
                eligible_for_response=False,
            ),
        ],
        insights=[
            EvidenceInsight(
                fact_ids=["f1"],
                finding="На аналоге есть ЛК и агенты",
                implementation_consequence="MVP без ЛК: только поиск и выдача документов",
                kind="scope",
            )
        ],
    )


def test_no_eligible_facts_returns_empty() -> None:
    bundle = EvidenceBundle(
        status="failed",
        required=True,
        sources=[
            EvidenceSource(
                id="s1",
                kind="url",
                role="data_source",
                input_ref="https://torgi.gov.ru",
                status="unavailable",
            )
        ],
        facts=[
            EvidenceFact(
                id="x",
                source_id="s1",
                claim="x",
                anchors=["x"],
                eligible_for_response=False,
            )
        ],
    )
    assert evidence_usage_issues("Здравствуйте! Сделаю сайт.", bundle) == []
    assert evidence_usage_issues(BAD_RESPONSE, None) == []


def test_generic_opener_with_evidence() -> None:
    issues = evidence_usage_issues(
        "Здравствуйте! Сделаю сайт под ваше ТЗ.",
        _bundle_with_eligible(),
    )
    assert any(i.startswith("evidence:generic_opener:") for i in issues)
    assert "evidence:no_anchors_used" in issues


def test_anchors_present_no_usage_issues() -> None:
    issues = evidence_usage_issues(GOOD_RESPONSE, _bundle_with_eligible())
    assert issues == []


def test_unverified_inspect_claim() -> None:
    text = (
        "Здравствуйте!\n"
        "По лоту 184729 и цене 1 250 000 ₽ вижу структуру поиска.\n"
        "Я открыл torgi.gov.ru и проверил выдачу извещений.\n"
        "Срок — 10 дней. Стоимость — от 30 000 ₽."
    )
    issues = evidence_usage_issues(text, _bundle_with_eligible(gov_status="unavailable"))
    assert any(i.startswith("evidence:unverified_inspect:torgi.gov.ru") for i in issues)


def test_external_url_issue() -> None:
    text = (
        "Здравствуйте!\n"
        "Лот 184729 / 1 250 000 ₽ — без ЛК.\n"
        "Смотрите https://example.com/docs для деталей.\n"
        "Срок — 7 дней. Стоимость — от 20 000 ₽."
    )
    issues = evidence_usage_issues(text, _bundle_with_eligible())
    assert "evidence:external_url" in issues


def test_compact_verified_evidence_shape() -> None:
    compact = compact_verified_evidence(_bundle_with_eligible())
    assert compact is not None
    assert len(compact["facts"]) == 3
    assert all("quote" not in f for f in compact["facts"])
    assert all(set(f) == {"claim", "anchors", "relevance"} for f in compact["facts"])
    assert compact["insights"]
    assert {s["id"] for s in compact["sources"]} >= {"src-rossii", "src-gov"}
    assert compact_verified_evidence(None) is None


def test_pronoun_generic_opener_with_evidence() -> None:
    issues = evidence_usage_issues(
        "Здравствуйте!\nЯ разработаю сайт. Лот 184729, цена 1 250 000 ₽.",
        _bundle_with_eligible(),
    )
    assert any(i.startswith("evidence:generic_opener:") for i in issues)


def test_insufficient_anchors_when_two_available() -> None:
    text = (
        "Здравствуйте!\n"
        "По лоту 184729 вижу структуру поиска без ЛК.\n"
        "Срок — 10 дней. Стоимость — от 30 000 ₽."
    )
    issues = evidence_usage_issues(text, _bundle_with_eligible())
    assert "evidence:insufficient_anchors" in issues
    assert "evidence:no_anchors_used" not in issues


def test_looked_at_lot_not_unverified_inspect() -> None:
    text = (
        "Здравствуйте!\n"
        "Посмотрел лот 184729 и цену 1 250 000; данные планирую брать с torgi.gov.ru без ЛК.\n"
        "Срок — 10 дней. Стоимость — от 30 000 ₽."
    )
    issues = evidence_usage_issues(text, _bundle_with_eligible(gov_status="unavailable"))
    assert not any(i.startswith("evidence:unverified_inspect:") for i in issues)


def test_validator_wrapper() -> None:
    v = EvidenceUsageValidator()
    assert v.issues(BAD_RESPONSE, _bundle_with_eligible())
    assert v.issues(GOOD_RESPONSE, _bundle_with_eligible()) == []


def test_golden_bad_template_vs_good_sample() -> None:
    bundle = _bundle_with_eligible()
    bad_issues = evidence_usage_issues(BAD_RESPONSE, bundle)
    assert bad_issues
    assert any("generic_opener" in i for i in bad_issues)
    assert "evidence:no_anchors_used" in bad_issues
    assert evidence_usage_issues(GOOD_RESPONSE, bundle) == []


def _tz_3252339_from_pending_or_fixture() -> str:
    if PENDING_3252339.is_file():
        raw = json.loads(PENDING_3252339.read_text(encoding="utf-8"))
        desc = (raw.get("project") or {}).get("full_description") or ""
        if str(desc).strip():
            return str(desc)
    return TZ_3252339_FALLBACK


def test_golden_offline_3252339_collect_compact_usage() -> None:
    """Offline integration: pending TZ + both HTML fixtures → compact + usage gates."""
    desc = _tz_3252339_from_pending_or_fixture()
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3252339",
        url="https://kwork.ru/projects/3252339",
        title="Разработать сайт",
        full_description=desc,
        desired_budget="до 30 000 ₽",
    )
    rossii = (FIXTURES / "3252339_torgi_rossii_lot.html").read_text(encoding="utf-8")
    gov = (FIXTURES / "3252339_torgi_gov_public.html").read_text(encoding="utf-8")
    bundle = EvidenceService().collect(
        project,
        fetch=False,
        injected_texts={
            ROSSII_URL: rossii,
            GOV_URL: gov,
            "торги-россии.рф": rossii,
            "torgi.gov.ru": gov,
        },
        max_urls=3,
    )
    assert bundle.required is True
    assert bundle.status in ("complete", "partial")
    assert any(s.status == "verified" for s in bundle.sources)
    assert any(f.eligible_for_response for f in bundle.facts)

    compact = compact_verified_evidence(bundle)
    assert compact is not None
    assert compact["facts"]
    assert all("quote" not in f for f in compact["facts"])
    assert all("anchors" in f for f in compact["facts"])

    bad_issues = evidence_usage_issues(BAD_RESPONSE, bundle)
    assert bad_issues
    assert evidence_usage_issues(GOOD_RESPONSE, bundle) == []


def test_short_numeric_anchors_do_not_satisfy_usage_gate() -> None:
    bundle = EvidenceBundle(
        status="complete",
        required=True,
        project_hash="x",
        sources=[
            EvidenceSource(
                id="s1",
                kind="url",
                role="data_source",
                input_ref="https://shop.example/",
                fetch_method="http",
                status="verified",
            )
        ],
        facts=[
            EvidenceFact(
                id="f1",
                source_id="s1",
                claim="На карточке 2 вариантов",
                quote="Длина 120 см",
                anchors=["2", "0"],
                verification="structured_value",
                eligible_for_response=True,
            ),
            EvidenceFact(
                id="f2",
                source_id="s1",
                claim="В sitemap 8 URL",
                quote="https://shop.example/item.html",
                anchors=["8 URL", "3 страниц"],
                verification="structured_value",
                eligible_for_response=True,
            ),
        ],
    )
    weak = "Срок 10 дней, стоимость 20 000 руб — сделаю парсер."
    issues = evidence_usage_issues(weak, bundle)
    assert "evidence:no_anchors_used" in issues or "evidence:insufficient_anchors" in issues

    good = "В sitemap 8 URL и 3 страниц товаров — обойду по sitemap."
    assert evidence_usage_issues(good, bundle) == []
