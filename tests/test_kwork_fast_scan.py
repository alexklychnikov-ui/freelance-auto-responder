from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import Settings, get_settings
from src.models import ProjectPreview
from src.pipeline.orchestrator import PipelineOrchestrator
from src.store.early_ping_seen import EarlyPingSeenStore
from src.store.repository import ProjectRepository


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        openai_api_key="test-key",
        openai_base_url="https://api.example.com/openai/v1",
        telegram_bot_token="token",
        telegram_chat_id="1",
        response_journal=str(tmp_path / "journal.xlsx"),
        database_path=str(tmp_path / "test.db"),
        kwork_fast_scan_enabled=True,
        kwork_fast_scan_seconds=90,
        yandex_early_scan_enabled=True,
        flru_early_scan_enabled=True,
        process_concurrency=1,
        early_ping_seen_db=str(tmp_path / "early_ping_seen.db"),
        early_listings_dir=str(tmp_path / "early"),
        kwork_inbox_mirror_enabled=False,
        flru_inbox_mirror_enabled=False,
        yandex_inbox_mirror_enabled=False,
        scan_bootstrap_skip_pipeline=False,
        _env_file=None,
    )


def test_settings_kwork_fast_defaults(env_vars: None) -> None:
    s = get_settings()
    assert s.kwork_fast_scan_enabled is True
    assert s.kwork_fast_scan_seconds == 90
    assert s.yandex_early_scan_enabled is True
    assert s.flru_early_scan_enabled is True
    assert s.process_concurrency == 1
    assert s.early_scan_enabled_for("kwork") is True
    assert s.early_scan_enabled_for("yandex_uslugi") is True
    assert s.early_scan_enabled_for("flru") is True
    assert s.early_scan_enabled_for("telegram") is False
    assert s.any_early_scan_enabled() is True


def test_settings_early_scan_helpers_respect_flags() -> None:
    s = Settings(
        openai_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal="j.xlsx",
        kwork_fast_scan_enabled=False,
        yandex_early_scan_enabled=True,
        flru_early_scan_enabled=False,
        _env_file=None,
    )
    assert s.early_scan_enabled_for("kwork") is False
    assert s.early_scan_enabled_for("yandex_uslugi") is True
    assert s.early_scan_enabled_for("flru") is False
    assert s.any_early_scan_enabled() is True

    s2 = Settings(
        openai_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal="j.xlsx",
        kwork_fast_scan_enabled=False,
        yandex_early_scan_enabled=False,
        flru_early_scan_enabled=False,
        _env_file=None,
    )
    assert s2.any_early_scan_enabled() is False


@pytest.fixture
def env_vars(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")
    monkeypatch.setenv("RESPONSE_JOURNAL", str(tmp_path / "journal.xlsx"))
    get_settings.cache_clear()


def test_early_ping_seen_mark_if_new(tmp_path: Path) -> None:
    store = EarlyPingSeenStore(tmp_path / "ping.db")
    assert store.mark_if_new("kwork", "100") is True
    assert store.mark_if_new("kwork", "100") is False
    assert store.mark_if_new("kwork", "101") is True


def _minimal_orchestrator(settings: Settings) -> PipelineOrchestrator:
    repo = ProjectRepository(settings.database_path)
    mock_tg = MagicMock()
    mock_tg.notify = AsyncMock()
    mock_tg.notify_early_listing = AsyncMock()
    review = MagicMock()
    review.tg_bot = mock_tg
    review.expire_stale_pending = MagicMock()
    return PipelineOrchestrator(
        settings=settings,
        repository=repo,
        review_service=review,
        scorer=MagicMock(),
        response_generator=MagicMock(),
        lightrag=MagicMock(),
        journal=MagicMock(),
        prepared_store=MagicMock(),
        adapter_factory=MagicMock(),
    )


@pytest.mark.asyncio
async def test_process_worker_runs_once(settings: Settings) -> None:
    orch = _minimal_orchestrator(settings)
    preview = ProjectPreview(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="42",
        url="https://kwork.ru/projects/42",
        title="Test",
    )
    source = MagicMock()
    source.id = "kwork_dev_it"
    source.platform = "kwork"

    process_mock = AsyncMock(return_value="scored")
    orch._process_new_project = process_mock  # type: ignore[method-assign]

    orch.enqueue_process(source, preview)
    shutdown = asyncio.Event()
    worker = asyncio.create_task(orch.run_process_worker(shutdown))
    await asyncio.sleep(0.05)
    shutdown.set()
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    process_mock.assert_awaited_once()
    args = process_mock.await_args
    assert args.args[1].project_id == "42"


def test_build_early_listing_keyboard() -> None:
    from src.telegram_bot.bot import (
        CALLBACK_EARLY_GO,
        CALLBACK_EARLY_SKIP,
        build_early_listing_keyboard,
        parse_callback_data,
    )

    kb = build_early_listing_keyboard("kwork", "kwork_c15", "3265601")
    row = kb.inline_keyboard[0]
    assert row[0].text == "✅ Откликнуться"
    assert row[1].text == "⏭ Пропустить"
    go = parse_callback_data(row[0].callback_data or "")
    skip = parse_callback_data(row[1].callback_data or "")
    assert go is not None and go[0] == CALLBACK_EARLY_GO
    assert go[1:] == ("kwork", "kwork_c15", "3265601")
    assert skip is not None and skip[0] == CALLBACK_EARLY_SKIP


@pytest.mark.asyncio
async def test_finalize_early_listing_footer_differentiates(
    settings: Settings, tmp_path: Path
) -> None:
    from src.store.early_listings import EarlyListing

    settings.early_listings_dir = str(tmp_path / "early")
    orch = _minimal_orchestrator(settings)
    orch.review_service.tg_bot.edit_early_listing_result = AsyncMock()

    def _save_awaiting(
        project_id: str, *, budget_warning: str | None = None
    ) -> EarlyListing:
        listing = EarlyListing(
            platform="kwork",
            source_key="kwork_dev_it",
            project_id=project_id,
            url=f"https://kwork.ru/projects/{project_id}",
            title="T",
            telegram_message_id=int(project_id),
            status="awaiting",
            budget_warning=budget_warning,
        )
        orch.early_listings.save(listing)
        return listing

    orch.repository.insert_new(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title="T",
    )
    orch.repository.update_status(
        "kwork", "kwork_dev_it", "1", "skipped", fit=False, score=3.0
    )
    _save_awaiting("1")
    await orch._finalize_early_listing_after_process(
        "kwork", "kwork_dev_it", "1", outcome="stack_reject"
    )
    footer = orch.review_service.tg_bot.edit_early_listing_result.await_args.kwargs[
        "footer"
    ]
    assert footer == "⏭ Стек не подходит (score 3/10)"

    orch.repository.insert_new(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="2",
        url="https://kwork.ru/projects/2",
        title="T",
    )
    orch.repository.update_status(
        "kwork", "kwork_dev_it", "2", "skipped", fit=True, score=8.0
    )
    _save_awaiting("2")
    await orch._finalize_early_listing_after_process(
        "kwork", "kwork_dev_it", "2", outcome="budget_reject"
    )
    footer = orch.review_service.tg_bot.edit_early_listing_result.await_args.kwargs[
        "footer"
    ]
    assert footer == "⏭ Бюджет: оценка выше потолка"

    orch.repository.insert_new(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3",
        url="https://kwork.ru/projects/3",
        title="T",
    )
    orch.repository.update_status(
        "kwork", "kwork_dev_it", "3", "notified", fit=True, score=8.0
    )
    _save_awaiting("3", budget_warning="оценка 96k vs потолок 15k")
    await orch._finalize_early_listing_after_process(
        "kwork", "kwork_dev_it", "3", outcome="notified"
    )
    footer = orch.review_service.tg_bot.edit_early_listing_result.await_args.kwargs[
        "footer"
    ]
    assert footer == "✅ Подходит (score 8) — ⚠️ бюджет оценка 96k vs потолок 15k"


def test_enqueue_process_dedupes(settings: Settings) -> None:
    orch = _minimal_orchestrator(settings)
    preview = ProjectPreview(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="42",
        url="https://kwork.ru/projects/42",
        title="Test",
    )
    source = MagicMock()
    source.id = "kwork_dev_it"
    source.platform = "kwork"

    assert orch.enqueue_process(source, preview) is True
    assert orch.enqueue_process(source, preview) is False
    assert orch._process_queue.qsize() == 1


@pytest.mark.asyncio
async def test_run_scan_cycle_skips_early_enabled_platforms(
    settings: Settings,
) -> None:
    settings.yandex_early_scan_enabled = False
    settings.flru_early_scan_enabled = False
    orch = _minimal_orchestrator(settings)
    kwork_preview = ProjectPreview(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title="K",
    )
    yandex_preview = ProjectPreview(
        platform="yandex_uslugi",
        source_key="yandex_uslugi_it",
        project_id="abc",
        url="https://yandex.ru/uslugi/order/abc",
        title="Y",
    )

    def scan_side_effect(source: object) -> list[ProjectPreview]:
        if getattr(source, "platform", "") == "kwork":
            return [kwork_preview]
        return [yandex_preview]

    orch._scan_listings = MagicMock(side_effect=scan_side_effect)  # type: ignore[method-assign]
    orch._process_new_project = AsyncMock(return_value="skipped")  # type: ignore[method-assign]

    sources = [
        MagicMock(
            id="kwork_dev_it",
            platform="kwork",
            enabled=True,
            bootstrap=False,
        ),
        MagicMock(
            id="yandex_uslugi_it",
            platform="yandex_uslugi",
            enabled=True,
            bootstrap=False,
        ),
    ]

    with patch(
        "src.pipeline.orchestrator.get_enabled_sources",
        return_value=sources,
    ):
        totals = await orch.run_scan_cycle()

    scan_calls = [c.args[0].platform for c in orch._scan_listings.call_args_list]
    assert "kwork" not in scan_calls
    assert "yandex_uslugi" in scan_calls
    assert totals["seen"] == 1


@pytest.mark.asyncio
async def test_run_scan_cycle_skips_yandex_and_flru_when_early_enabled(
    settings: Settings,
) -> None:
    orch = _minimal_orchestrator(settings)
    orch._scan_listings = MagicMock(return_value=[])  # type: ignore[method-assign]
    orch._process_new_project = AsyncMock(return_value="skipped")  # type: ignore[method-assign]

    sources = [
        MagicMock(id="kwork_dev_it", platform="kwork", enabled=True, bootstrap=False),
        MagicMock(
            id="yandex_uslugi_it",
            platform="yandex_uslugi",
            enabled=True,
            bootstrap=False,
        ),
        MagicMock(id="flru_orders", platform="flru", enabled=True, bootstrap=False),
    ]

    with patch(
        "src.pipeline.orchestrator.get_enabled_sources",
        return_value=sources,
    ):
        await orch.run_scan_cycle()

    assert orch._scan_listings.call_count == 0


@pytest.mark.asyncio
async def test_run_early_scan_cycle_covers_yandex(
    settings: Settings,
) -> None:
    settings.kwork_fast_scan_enabled = False
    settings.flru_early_scan_enabled = False
    orch = _minimal_orchestrator(settings)
    orch.review_service.tg_bot.notify_early_listing = AsyncMock(return_value=99)

    preview = ProjectPreview(
        platform="yandex_uslugi",
        source_key="yandex_uslugi_it",
        project_id="uuid-abc",
        url="https://yandex.ru/uslugi/order/uuid-abc",
        title="Ya job",
    )
    orch._scan_listings = MagicMock(return_value=[preview])  # type: ignore[method-assign]

    sources = [
        MagicMock(
            id="yandex_uslugi_it",
            platform="yandex_uslugi",
            enabled=True,
            bootstrap=False,
        ),
        MagicMock(id="kwork_dev_it", platform="kwork", enabled=True, bootstrap=False),
    ]

    with patch(
        "src.pipeline.orchestrator.get_enabled_sources",
        return_value=sources,
    ), patch(
        "src.pipeline.orchestrator.is_kwork_paused",
        return_value=False,
    ):
        totals = await orch.run_early_scan_cycle()

    assert totals["pinged"] == 1
    assert totals["new"] == 1
    orch.review_service.tg_bot.notify_early_listing.assert_awaited_once()
    call_kw = orch.review_service.tg_bot.notify_early_listing.await_args.kwargs
    assert call_kw["platform"] == "yandex_uslugi"
    assert call_kw["project_id"] == "uuid-abc"
    # non-digit id → last preview id string
    state = orch.repository.get_scan_state("yandex_uslugi_it")
    assert state is not None
    assert state.last_known_project_id == "uuid-abc"


@pytest.mark.asyncio
async def test_run_kwork_fast_scan_cycle_alias(
    settings: Settings,
) -> None:
    orch = _minimal_orchestrator(settings)
    orch.run_early_scan_cycle = AsyncMock(return_value={"seen": 1})  # type: ignore[method-assign]
    out = await orch.run_kwork_fast_scan_cycle()
    assert out == {"seen": 1}
    orch.run_early_scan_cycle.assert_awaited_once()
