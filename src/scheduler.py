from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from src.adapters.kwork_auth import KworkAuthError, KworkCredentials, ensure_logged_in
from src.browser.factory import close_browser_client, get_browser_client
from src.analyzer.examples_loader import load_response_examples
from src.analyzer.project_tier import resolve_acceptance_tier
from src.config import get_enabled_sources, get_settings
from src.pipeline.orchestrator import build_orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def _run_test_async() -> int:
    """Scan kwork listing, score up to 10 projects, TG-notify first fit one."""
    settings = get_settings()
    orchestrator = build_orchestrator(settings)
    sources = get_enabled_sources(settings.sources_config_path)
    if not sources:
        logger.error("No enabled sources")
        return 1

    source = sources[0]
    adapter = orchestrator._default_adapter(source)
    bot_task = asyncio.create_task(orchestrator.review_service.run_bot())
    notified = False
    scanned_count = 0
    match: tuple | None = None

    def _scan_and_score_first_fit():
        nonlocal scanned_count
        try:
            previews = adapter.scan_new()[:10]
            scanned_count = len(previews)
            logger.info("run-test: scanned %d previews", scanned_count)
            for preview in previews:
                logger.info("run-test: scoring %s — %s", preview.project_id, preview.title)
                full = adapter.read_full(preview.project_id)
                context = orchestrator.lightrag.get_scoring_context(full)
                examples = load_response_examples(settings.response_examples_dir)
                score = orchestrator.scorer.score(full, context, examples=examples)
                acceptance_tier = resolve_acceptance_tier(full, score, settings)
                logger.info(
                    "run-test: score=%s fit=%s tier=%s reason=%s",
                    score.score,
                    score.fit,
                    acceptance_tier,
                    score.reason,
                )
                if acceptance_tier is not None:
                    return full, score
            return None
        finally:
            orchestrator.scorer.close()
            orchestrator.response_generator.close()
            orchestrator.close()

    try:
        match = await asyncio.to_thread(_scan_and_score_first_fit)
        if match is not None:
            full, score = match
            await orchestrator.review_service.request_review(full, score)
            logger.info("run-test: Telegram review sent for %s", full.title)
            notified = True
            await asyncio.sleep(3)
            await orchestrator.review_service.tg_bot.notify(
                "✅ run-test завершён успешно\n"
                f"Скан: {scanned_count} проектов на kwork_dev_it\n"
                f"Отобран: {full.title}\n"
                f"Score: {score.score}/10 — {score.recommendation}\n"
                f"{full.url}\n"
                "Карточка approve/reject — выше ↑"
            )
        else:
            await orchestrator.review_service.tg_bot.notify(
                "ℹ️ run-test: подходящих проектов (score>=7) на первой странице не найдено"
            )
    except KworkAuthError as exc:
        await orchestrator.review_service.tg_bot.notify(f"⚠️ run-test: Kwork auth failed: {exc}")
        return 1
    except Exception:
        logger.exception("run-test failed")
        await orchestrator.review_service.tg_bot.notify("❌ run-test: ошибка, см. логи на сервере")
        return 1
    finally:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
        await orchestrator.review_service.tg_bot.close()
    return 0 if notified else 2


async def _run_once_async() -> int:
    settings = get_settings()
    orchestrator = build_orchestrator(settings)
    totals = await orchestrator.run_scan_cycle()
    logger.info("run-once complete: %s", totals)
    orchestrator.scorer.close()
    orchestrator.response_generator.close()
    orchestrator.close()
    return 0


async def _daemon_async() -> int:
    settings = get_settings()
    orchestrator = build_orchestrator(settings)
    shutdown = asyncio.Event()
    interval_sec = settings.scan_interval_minutes * 60

    def _request_shutdown(*_args: object) -> None:
        logger.info("shutdown requested")
        shutdown.set()

    if sys.platform == "win32":
        signal.signal(signal.SIGINT, _request_shutdown)
        signal.signal(signal.SIGTERM, _request_shutdown)
    else:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _request_shutdown)

    bot_task = asyncio.create_task(orchestrator.review_service.run_bot())

    try:
        while not shutdown.is_set():
            try:
                totals = await orchestrator.run_scan_cycle()
                logger.info("scan cycle: %s", totals)
            except Exception:
                logger.exception("scan cycle failed")

            try:
                await asyncio.wait_for(shutdown.wait(), timeout=interval_sec)
            except asyncio.TimeoutError:
                pass
    finally:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
        await orchestrator.review_service.tg_bot.close()
        orchestrator.scorer.close()
        orchestrator.response_generator.close()
        orchestrator.close()

    return 0


def cmd_browser_smoke() -> int:
    settings = get_settings()
    client = get_browser_client(settings)
    try:
        creds = None
        if pair := settings.kwork_credentials():
            creds = KworkCredentials(login=pair[0], password=pair[1])
        logger.info("BrowserMCP smoke: ensure kwork session")
        ensure_logged_in(client, creds)
        logger.info("BrowserMCP smoke: navigate kwork listing")
        client.navigate("https://kwork.ru/projects?c=11")
        snap = client.snapshot()
        logger.info("snapshot length=%s chars", len(snap))
        cards = client.evaluate(
            "document.querySelectorAll('a[href*=\"/projects/\"]').length"
        )
        logger.info("project links on page: %s", cards)
        print("OK: BrowserMCP connected. Open Chrome tab + extension Connect if links=0.")
        return 0
    except Exception as exc:
        logger.exception("BrowserMCP smoke failed: %s", exc)
        print(
            "FAIL: load extension from C:\\Python\\Projects\\BrowserMCP\\packages\\extension, "
            "open kwork.ru tab, click Connect on extension."
        )
        return 1
    finally:
        close_browser_client(client)


def cmd_run_test() -> int:
    return asyncio.run(_run_test_async())


def cmd_run_once() -> int:
    return asyncio.run(_run_once_async())


def cmd_daemon() -> int:
    return asyncio.run(_daemon_async())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="freelance-responder",
        description="Freelance Auto-Responder scheduler",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run-once", help="Run one scan cycle")
    sub.add_parser("daemon", help="Long-running scan daemon with TG bot")
    sub.add_parser(
        "run-test",
        help="Scan kwork, score first fit project, send Telegram review (deploy smoke)",
    )
    sub.add_parser(
        "browser-smoke",
        help="Test BrowserMCP: navigate kwork listing (extension must be connected)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run-test":
        return cmd_run_test()
    if args.command == "run-once":
        return cmd_run_once()
    if args.command == "daemon":
        return cmd_daemon()
    if args.command == "browser-smoke":
        return cmd_browser_smoke()

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
