from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.adapters.flru import (
    FlruAdapter,
    FlruAuthError,
    _clean_submitted_description,
    cookie_header_from_storage,
    detect_flru_requires_paid_response,
    flru_page_looks_logged_out,
    flru_paid_response_tg_mark,
    is_flru_project_closed,
    parse_flru_money_fields,
    parse_listing_from_html,
    parse_project_from_html,
    parse_submitted_offer_from_text,
    read_submitted_offer_text,
)
from src.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"
PID = "5514795"


def test_is_flru_project_closed_phrases() -> None:
    assert is_flru_project_closed("Заказчик выбрал исполнителя: Никита Петров")
    assert is_flru_project_closed("Исполнитель определён")
    assert is_flru_project_closed("Исполнитель определен")
    assert not is_flru_project_closed("Откликнуться на проект")


def test_parse_project_closed_ignores_related_aside() -> None:
    html = """
    <html><body>
    <h1>Нужен парсер</h1>
    <div class="description">Сделать парсер каталога Озон под ключ</div>
    <aside>
      <a href="/projects/5517512/x.html">Похожий</a>
      <span>Заказчик выбрал исполнителя: Иван Иванов</span>
    </aside>
    <h2>Другие заказы по специализации</h2>
    <div>Заказчик выбрал исполнителя: Пётр Петров</div>
    </body></html>
    """
    raw = parse_project_from_html(html, project_id="5516755")
    assert raw["closed"] is False


def test_parse_project_closed_when_banner_in_main() -> None:
    html = """
    <html><body>
    <h1>Нужен парсер</h1>
    <div class="notice">Заказчик выбрал исполнителя: Никита Петров</div>
    <div class="description">Сделать парсер каталога Озон под ключ</div>
    <h2>Другие заказы по специализации</h2>
    <div>Открытый похожий заказ</div>
    </body></html>
    """
    raw = parse_project_from_html(html, project_id="5516755")
    assert raw["closed"] is True


def test_parse_listing_from_html_fixture() -> None:
    html = (FIXTURES / "flru_listing.html").read_text(encoding="utf-8")
    cards = parse_listing_from_html(html)
    ids = {c["project_id"] for c in cards}
    assert PID in ids
    assert "5514779" in ids


def test_parse_project_from_html_fixture() -> None:
    html = (FIXTURES / "flru_project.html").read_text(encoding="utf-8")
    raw = parse_project_from_html(html, project_id=PID)
    assert "озон" in raw["title"].lower() or "парсинг" in raw["title"].lower()
    assert "озон" in (raw["full_description"] or "").lower()


def test_parse_project_offers_prefers_otkliknulis_over_related_otvet() -> None:
    html = (FIXTURES / "flru_project_stats.html").read_text(encoding="utf-8")
    raw = parse_project_from_html(html, project_id="5516755")
    assert raw["offers_count"] == 36


def test_parse_project_offers_otklik_only_inside_stats_not_related() -> None:
    html = """
    <html><body>
    <h1>Заказ</h1>
    <aside>
      <a href="/projects/5517512/x.html">Похожий</a>
      <span>3 отклика</span>
      <span>35 ответов</span>
    </aside>
    <section>
      <h3>Статистика откликов</h3>
      <div>от 20 000 — до 80 000 · 12 откликов</div>
    </section>
    </body></html>
    """
    raw = parse_project_from_html(html, project_id="5516755")
    assert raw["offers_count"] == 12


def test_parse_listing_responses_count_from_card() -> None:
    html = (FIXTURES / "flru_listing.html").read_text(encoding="utf-8")
    cards = {c["project_id"]: c for c in parse_listing_from_html(html)}
    assert cards[PID]["responses_count"] == 2


def _make_adapter(**kwargs) -> FlruAdapter:
    settings = Settings(
        openai_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal="j.xlsx",
        flru_storage_state="data/flru_storage.json",
        _env_file=None,
    )
    defaults = {
        "source_key": "flru_orders",
        "listing_url": "https://www.fl.ru/projects/?kind=1",
        "settings": settings,
        "browser": MagicMock(),
        "filters": {"for_all": True, "skip_closed": True},
    }
    defaults.update(kwargs)
    return FlruAdapter(**defaults)


def test_submit_response_manual_only() -> None:
    adapter = _make_adapter()
    result = adapter.submit_response(PID, "text", "500")
    assert result.success is False
    assert "manual_only" in (result.message or "")


def test_listing_url_appends_for_all() -> None:
    adapter = _make_adapter()
    assert adapter._listing_url() == "https://www.fl.ru/projects/?kind=1&for_all=1"


def test_parse_flru_money_range_ruble_sign() -> None:
    desired, max_b = parse_flru_money_fields("Оплата: 5 000 ₽ – 10 000 ₽")
    assert desired == "5000 руб"
    assert max_b == "10000 руб"


def test_parse_project_budget_range_from_html() -> None:
    html = """
    <html><body>
    <h1>ТЗ Word без цены</h1>
    <div class="description">Нужен бот. Бюджет в карточке, в файле денег нет.</div>
    <div>Оплата: 5 000 ₽ – 10 000 ₽</div>
    </body></html>
    """
    raw = parse_project_from_html(html, project_id="5530543")
    assert raw["desired_budget"] == "5000 руб"
    assert raw["max_budget"] == "10000 руб"


def test_parse_listing_budget_range_ruble() -> None:
    html = """
    <a href="/projects/5530543/bot.html">Бот</a>
    <span>Оплата: 5 000 ₽ – 10 000 ₽</span>
    <span>2 ответа</span>
    """
    cards = parse_listing_from_html(html, skip_closed=False)
    assert len(cards) == 1
    assert cards[0]["budget_text"] is not None
    assert "5000" in cards[0]["budget_text"].replace(" ", "")
    assert "10000" in cards[0]["budget_text"].replace(" ", "")


def test_detect_flru_requires_paid_response() -> None:
    paid_html = (
        "<div>Вы можете купить 1 отклик, чтобы ответить на этот заказ.</div>"
        "<button>Купить 1 отклик за 80 ₽</button>"
        "<p>покупать отклики только пакетами</p>"
    )
    free_html = (
        "<div>Не требуется оплата отклика</div>"
        "<span>опция «Для всех»</span>"
    )
    assert detect_flru_requires_paid_response(paid_html) is True
    assert detect_flru_requires_paid_response(free_html) is False
    assert detect_flru_requires_paid_response("<div>обычный текст</div>") is None
    mark = flru_paid_response_tg_mark(title="Тест", url="https://www.fl.ru/projects/1/")
    assert "платный отклик" in mark
    assert "Тест" in mark


def test_cookie_header_from_storage(tmp_path) -> None:
    path = tmp_path / "flru_storage.json"
    path.write_text(
        '{"cookies":[{"name":"sid","value":"1","domain":".fl.ru"},'
        '{"name":"__ddg8_","value":"stale","domain":".fl.ru"},'
        '{"name":"ya","value":"2","domain":".yandex.ru"}],"origins":[]}',
        encoding="utf-8",
    )
    assert cookie_header_from_storage(path) == "sid=1"
    assert cookie_header_from_storage(None) == ""


def test_auth_fetch_skips_request_get_uses_page() -> None:
    browser = MagicMock()
    browser.request_get.side_effect = TimeoutError(
        "APIRequestContext.get: Timeout 25000ms exceeded"
    )
    adapter = _make_adapter(browser=browser)
    page_fetch = MagicMock(return_value="<html>page</html>")
    adapter._page_auth_fetch_html = page_fetch

    html = adapter._auth_fetch_html("https://www.fl.ru/projects/?for_all=1")

    assert html == "<html>page</html>"
    page_fetch.assert_called_once_with("https://www.fl.ru/projects/?for_all=1")
    browser.request_get.assert_not_called()


def test_auth_fetch_error_is_single_line() -> None:
    adapter = _make_adapter()
    adapter._page_auth_fetch_html = MagicMock(
        side_effect=RuntimeError(
            "Timeout 25000ms exceeded\nCall log:\n  - navigating to url"
        )
    )

    with pytest.raises(RuntimeError) as ei:
        adapter._auth_fetch_html("https://www.fl.ru/projects/?for_all=1")

    msg = str(ei.value)
    assert "flru_auth_fetch_failed:" in msg
    assert "\n" not in msg
    assert "Call log" not in msg


def test_flru_browser_uses_flru_proxy_then_tg_fallback(monkeypatch) -> None:
    adapter = _make_adapter(browser=None)
    adapter.settings.flru_proxy = ""
    adapter.settings.tg_user_proxy = "socks5://127.0.0.1:1080"
    factory = MagicMock(return_value=MagicMock())
    monkeypatch.setattr("src.adapters.flru.get_browser_client", factory)

    adapter._get_browser()

    factory.assert_called_once_with(
        adapter.settings,
        storage_state_path="data/flru_storage.json",
        proxy_url="socks5://127.0.0.1:1080",
    )


def test_scan_new_allows_guest_fallback_by_default(monkeypatch) -> None:
    adapter = _make_adapter()
    monkeypatch.setattr(
        adapter,
        "_scan_via_auth_fetch",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("auth down")),
    )
    monkeypatch.setattr(
        adapter,
        "_scan_via_auth_http",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("auth http down")),
    )
    guest_cards = [MagicMock(project_id=PID)]
    http_scan = MagicMock(return_value=guest_cards)
    monkeypatch.setattr(adapter, "_scan_via_http", http_scan)

    cards = adapter.scan_new()

    assert cards is guest_cards
    http_scan.assert_called_once_with(skip_closed=True)


def test_scan_new_failed_fetch_uses_auth_http_not_guest(monkeypatch) -> None:
    adapter = _make_adapter()
    monkeypatch.setattr(
        adapter,
        "_scan_via_auth_fetch",
        lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("Failed to fetch (href=https://www.fl.ru/messages/)")
        ),
    )
    auth_http_cards = [MagicMock(project_id=PID)]
    auth_http = MagicMock(return_value=auth_http_cards)
    guest = MagicMock()
    monkeypatch.setattr(adapter, "_scan_via_auth_http", auth_http)
    monkeypatch.setattr(adapter, "_scan_via_http", guest)

    cards = adapter.scan_new(allow_guest_fallback=False)

    assert cards is auth_http_cards
    auth_http.assert_called_once_with(skip_closed=True)
    guest.assert_not_called()


def test_scan_new_rejects_guest_fallback_on_auth_failure(monkeypatch) -> None:
    adapter = _make_adapter()
    monkeypatch.setattr(
        adapter,
        "_scan_via_auth_fetch",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Failed to fetch")),
    )
    monkeypatch.setattr(
        adapter,
        "_scan_via_auth_http",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("auth http down")),
    )
    http_scan = MagicMock()
    monkeypatch.setattr(adapter, "_scan_via_http", http_scan)

    with pytest.raises(
        FlruAuthError, match="flru_auth_fetch_failed: Failed to fetch"
    ):
        adapter.scan_new(allow_guest_fallback=False)

    http_scan.assert_not_called()


def test_scan_new_rejects_guest_fallback_on_empty_auth_scan(monkeypatch) -> None:
    adapter = _make_adapter()
    monkeypatch.setattr(adapter, "_scan_via_auth_fetch", MagicMock(return_value=[]))
    monkeypatch.setattr(adapter, "_scan_via_auth_http", MagicMock(return_value=[]))
    http_scan = MagicMock()
    monkeypatch.setattr(adapter, "_scan_via_http", http_scan)

    with pytest.raises(FlruAuthError, match="flru_auth_fetch_empty"):
        adapter.scan_new(allow_guest_fallback=False)

    http_scan.assert_not_called()


def test_scan_new_ensures_for_all_checkbox(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.adapters.flru.fetch_flru_html",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("http down")),
    )
    browser = MagicMock()
    browser.evaluate.side_effect = [
        "https://www.fl.ru/projects/?kind=1&for_all=1",  # _ensure_logged_in
        {"ok": True, "clicked": True},  # _ensure_for_all_filter
        [{"project_id": PID, "url": f"https://www.fl.ru/projects/{PID}/", "title": "t"}],
    ]
    adapter = _make_adapter(browser=browser)
    monkeypatch.setattr(
        adapter,
        "_scan_via_auth_fetch",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("auth down")),
    )
    monkeypatch.setattr(
        adapter,
        "_scan_via_auth_http",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("auth http down")),
    )
    monkeypatch.setattr(
        adapter,
        "_scan_via_http",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("http down")),
    )
    cards = adapter.scan_new()
    assert len(cards) == 1
    assert cards[0].project_id == PID
    browser.navigate.assert_called_with(
        "https://www.fl.ru/projects/?kind=1&for_all=1"
    )


def test_fetch_flru_html_passes_proxy_to_httpx(monkeypatch) -> None:
    captured: dict = {}

    class FakeResp:
        url = "https://www.fl.ru/projects/?kind=1"
        text = "<html></html>"

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            return FakeResp()

    monkeypatch.setattr("src.adapters.flru.httpx.Client", FakeClient)
    monkeypatch.setattr("src.adapters.flru.cookie_header_from_storage", lambda p: "")

    from src.adapters.flru import fetch_flru_html

    html = fetch_flru_html(
        "https://www.fl.ru/projects/?kind=1",
        use_cookies=False,
        proxy_url="socks5://127.0.0.1:12335",
    )
    assert html == "<html></html>"
    assert captured.get("proxy") == "socks5://127.0.0.1:12335"


def test_warm_auth_origin_uses_site_root_not_messages() -> None:
    browser = MagicMock()
    browser.evaluate.return_value = "https://www.fl.ru/"
    adapter = _make_adapter(browser=browser)

    adapter._warm_auth_origin(browser)

    browser.navigate.assert_called_once_with("https://www.fl.ru/")
    assert "/messages/" not in browser.navigate.call_args[0][0]


def test_listing_pages_clamp_and_filter_override() -> None:
    adapter = _make_adapter()
    adapter.settings.flru_listing_pages = 3
    assert adapter._listing_pages() == 3
    adapter.settings.flru_listing_pages = 99
    assert adapter._listing_pages() == 5
    adapter.settings.flru_listing_pages = 0
    assert adapter._listing_pages() == 1
    adapter.filters["listing_pages"] = 2
    assert adapter._listing_pages() == 2


def test_merge_raw_cards_dedupes_preserving_order() -> None:
    adapter = _make_adapter()
    page1 = [
        {"project_id": "11111", "title": "a"},
        {"project_id": "22222", "title": "b"},
    ]
    page2 = [
        {"project_id": "22222", "title": "b-dup"},
        {"project_id": "33333", "title": "c"},
    ]
    merged = adapter._merge_raw_cards([page1, page2])
    assert [c["project_id"] for c in merged] == ["11111", "22222", "33333"]
    assert merged[1]["title"] == "b"


def test_scan_via_auth_fetch_warms_inbox_once(monkeypatch) -> None:
    browser = MagicMock()
    adapter = _make_adapter(browser=browser)
    adapter.settings.flru_listing_pages = 3
    fetched_urls: list[str] = []

    def fake_fetch(_browser, url: str) -> str:
        fetched_urls.append(url)
        if "page=2" in url:
            return '<a href="/projects/22222/x">Two</a>'
        if "page=3" in url:
            return '<a href="/projects/11111/x">Dup</a><a href="/projects/33333/x">Three</a>'
        return '<a href="/projects/11111/x">One</a>'

    warm = MagicMock()
    monkeypatch.setattr(adapter, "_warm_auth_origin", warm)
    monkeypatch.setattr(adapter, "_same_origin_fetch_html", fake_fetch)
    monkeypatch.setattr(
        "src.adapters.flru.parse_listing_from_html",
        lambda html, skip_closed=True: [
            {
                "project_id": m,
                "url": f"https://www.fl.ru/projects/{m}/",
                "title": m,
            }
            for m in re.findall(r"/projects/(\d+)/", html)
        ],
    )

    cards = adapter._scan_via_auth_fetch(skip_closed=True)

    warm.assert_called_once_with(browser)
    browser.navigate.assert_not_called()
    assert len(fetched_urls) == 3
    assert "page=" not in fetched_urls[0]
    assert "page=2" in fetched_urls[1]
    assert "page=3" in fetched_urls[2]
    assert [c.project_id for c in cards] == ["11111", "22222", "33333"]


def test_parse_submitted_offer_from_fixture_text() -> None:
    html = (FIXTURES / "flru_submitted_offer.html").read_text(encoding="utf-8")
    text = re.sub(r"<[^>]+>", "\n", html)
    text = re.sub(r"\n{2,}", "\n", text)
    snap = parse_submitted_offer_from_text(text)
    assert snap.ok
    assert snap.price == "40000"
    assert snap.delivery_days == 7
    assert "40 000" in snap.description or "40000" in snap.description.replace(" ", "")
    assert snap.description.startswith("Иван, здравствуйте!")
    assert "парсер каталога" in snap.description
    assert "Редактировать" not in snap.description
    assert "Отказаться" not in snap.description
    assert "Иван Тестов" not in snap.description
    assert "30.07.2026" not in snap.description
    assert "Чат" not in snap.description
    assert "Другие заказы" not in snap.description
    assert "логотип" not in snap.description
    assert "Статистика откликов" not in snap.description
    assert "до 80 000" not in snap.description


def test_parse_submitted_offer_plain_probe_structure() -> None:
    text = (
        "Нужен бот\nБюджет: от 48 000\nСтатистика откликов\nот 20 000 — до 80 000\n"
        "Ваш отклик\nРедактировать\nОтказаться от заказа\n"
        "Александр Клычников\n30.07.2026 в 15:52\n"
        "Александр, здравствуйте!\n"
        "Сделаю интеграцию под ваш ТЗ. Срок работ устраивает.\n"
        "Стоимость работ от 40 000 ₽. Готов обсудить детали.\n"
        "Срок: 7 дней\nСтоимость работ: 40 000 ₽\n"
        "Чат\nДругие заказы по специализации\nЗаказ про космос\n"
    )
    snap = parse_submitted_offer_from_text(text)
    assert snap.ok
    assert snap.price == "40000"
    assert snap.delivery_days == 7
    assert "40 000" in snap.description
    assert "Александр, здравствуйте!" in snap.description
    assert "Александр Клычников" not in snap.description
    assert "Другие заказы" not in snap.description
    assert "Статистика откликов" not in snap.description
    assert "космос" not in snap.description
    assert "48 000" not in snap.description


def test_parse_submitted_offer_missing_block() -> None:
    snap = parse_submitted_offer_from_text("Нужен бот без секции отклика")
    assert not snap.ok
    assert snap.error == "block_missing"


def test_flru_page_looks_logged_out_guest_nav() -> None:
    guest = (
        "Регистрация / Вход / Откликнуться\n"
        "Нужен парсер каталога\n"
        "Бюджет: 5 000 ₽\n"
        "Откликнуться\n"
    )
    assert flru_page_looks_logged_out(guest) is True
    snap = parse_submitted_offer_from_text(guest)
    assert not snap.ok
    assert snap.error == "not_logged_in"


def test_flru_page_looks_logged_out_logged_in_no_offer() -> None:
    logged_in = (
        "Личный кабинет\nВыйти\n"
        "Нужен парсер каталога\n"
        "Бюджет: 5 000 ₽\n"
        "Откликнуться\n"
    )
    assert flru_page_looks_logged_out(logged_in) is False
    snap = parse_submitted_offer_from_text(logged_in)
    assert not snap.ok
    assert snap.error == "block_missing"


def test_flru_page_looks_logged_out_false_when_offer_present() -> None:
    text = (
        "Регистрация\nВход\n"  # noise elsewhere should not matter
        "Ваш отклик\nРедактировать\n"
        "Иван, здравствуйте! Сделаю парсер за неделю, от 40 000 ₽.\n"
        "Срок: 7 дней\nСтоимость работ: 40 000 ₽\n"
    )
    assert flru_page_looks_logged_out(text) is False


def test_read_submitted_offer_text_guest_not_logged_in() -> None:
    browser = MagicMock()
    browser.evaluate.side_effect = [
        (
            "Регистрация / Вход / Откликнуться\n"
            "Нужен бот для Telegram\n"
            "Откликнуться на проект\n"
        ),
    ]
    snap = read_submitted_offer_text(browser, "5516239")
    assert not snap.ok
    assert snap.error == "not_logged_in"
    browser.navigate.assert_called_once()


def test_read_submitted_offer_text_prefers_inner_text() -> None:
    browser = MagicMock()
    browser.evaluate.side_effect = [
        (
            "Ваш отклик\nРедактировать\nОтказаться от заказа\n"
            "Иван Тестов\n30.07.2026 в 15:52\n"
            "Иван, здравствуйте! Сделаю парсер за неделю, от 40 000 ₽.\n"
            "Срок: 7 дней\nСтоимость работ: 40 000 ₽\nЧат\n"
        ),
    ]
    snap = read_submitted_offer_text(browser, "5515806")
    assert snap.ok
    assert snap.price == "40000"
    assert snap.delivery_days == 7
    assert "парсер" in snap.description
    assert "Редактировать" not in snap.description
    assert "Иван Тестов" not in snap.description
    browser.navigate.assert_called_once()
    browser.wait_ms.assert_called()


def test_parse_submitted_offer_strips_mashed_chrome() -> None:
    text = (
        "Ваш отклик\n"
        "Редактировать Отказаться от заказа\n"
        "Александр Клычников 30.07.2026 в 15:52 Александр, здравствуйте! "
        "Срок выполнения — 7–10 дней. Стоимость проекта — от 40 000 ₽.\n"
        "Срок: 7 дней Стоимость работ: 40 000 ₽ Чат\n"
    )
    snap = parse_submitted_offer_from_text(text)
    assert snap.ok
    assert snap.price == "40000"
    assert snap.delivery_days == 7
    assert snap.description.startswith("Александр, здравствуйте!")
    assert "Редактировать" not in snap.description
    assert "Стоимость работ" not in snap.description
    assert "Чат" not in snap.description


def test_parse_submitted_offer_keeps_chat_agents_compound() -> None:
    """Regression 5518223: «чат» in «чат-агенты» must not truncate the body."""
    body = (
        "Александр, здравствуйте! Сделаю RAG на гибридный поиск), чат-агенты. "
        "Логика маршрутизации и этапы внедрения ниже."
    )
    text = (
        "Ваш отклик\nРедактировать\nОтказаться от заказа\n"
        "Иван Тестов\n16.08.2026 в 13:19\n"
        f"{body}\n"
        "Срок: 10 дней\nСтоимость работ: 80 000 ₽\n"
        "Чат\nДругие заказы по специализации\nЗаказ про логотип\n"
    )
    cleaned = _clean_submitted_description(
        f"{body}\nСрок: 10 дней\nСтоимость работ: 80 000 ₽\nЧат"
    )
    assert "чат-агенты" in cleaned
    assert "Логика маршрутизации" in cleaned
    assert not cleaned.rstrip().endswith("поиск),")
    assert "Чат" not in cleaned
    assert "Стоимость работ" not in cleaned

    snap = parse_submitted_offer_from_text(text)
    assert snap.ok
    assert "чат-агенты" in snap.description
    assert "Логика маршрутизации" in snap.description
    assert "гибридный поиск), чат-агенты. Логика" in snap.description
    assert not snap.description.rstrip().endswith("поиск),")
    assert "Чат" not in snap.description
    assert "Другие заказы" not in snap.description
    assert "логотип" not in snap.description


def test_read_submitted_offer_text_bad_id() -> None:
    snap = read_submitted_offer_text(MagicMock(), "abc")
    assert not snap.ok
    assert snap.error == "bad_project_id"
