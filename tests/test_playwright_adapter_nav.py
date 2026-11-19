from __future__ import annotations

import json
from unittest.mock import MagicMock

from src.browser.playwright_adapter import (
    PlaywrightBrowserAdapter,
    _is_transient_nav_error,
    build_ipv4_host_resolver_rules,
    load_storage_state,
    strip_ddos_guard_cookies,
)


def test_transient_nav_detects_err_timed_out() -> None:
    assert _is_transient_nav_error(
        Exception("Page.goto: net::ERR_TIMED_OUT at https://uslugi.yandex.ru/")
    )
    assert _is_transient_nav_error(
        Exception("Page.goto: Timeout 60000ms exceeded.")
    )
    assert not _is_transient_nav_error(Exception("Selector not found"))


def test_ipv4_resolver_rules_contain_map(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.browser.playwright_adapter._resolve_ipv4",
        lambda host: "87.250.250.220" if "yandex" in host else None,
    )
    rules = build_ipv4_host_resolver_rules(("uslugi.yandex.ru", "nope.example"))
    assert "MAP uslugi.yandex.ru 87.250.250.220" in rules
    assert "nope.example" not in rules


def test_proxy_disables_host_resolver_rules(monkeypatch) -> None:
    builder = MagicMock(side_effect=AssertionError("must not resolve direct hosts"))
    monkeypatch.setattr(
        "src.browser.playwright_adapter.build_ipv4_host_resolver_rules",
        builder,
    )
    adapter = PlaywrightBrowserAdapter(proxy_url="socks5://127.0.0.1:1080")

    assert adapter._launch_args() == ["--disable-ipv6"]
    builder.assert_not_called()


def test_load_storage_state_repairs_trailer(tmp_path) -> None:
    path = tmp_path / "flru_storage.json"
    good = {"cookies": [{"name": "a", "value": "1"}], "origins": []}
    path.write_text(json.dumps(good) + "]}]}", encoding="utf-8")
    loaded = load_storage_state(path)
    assert loaded == good
    # file rewritten clean
    assert json.loads(path.read_text(encoding="utf-8")) == good


def test_strip_ddos_guard_cookies_keeps_session() -> None:
    state = {
        "cookies": [
            {"name": "PHPSESSID", "value": "s", "domain": ".fl.ru"},
            {"name": "__ddg8_", "value": "x", "domain": ".fl.ru"},
            {"name": "__ddg9_", "value": "y", "domain": ".fl.ru"},
            {"name": "id", "value": "1", "domain": ".fl.ru"},
        ],
        "origins": [],
    }
    cleaned = strip_ddos_guard_cookies(state)
    names = [c["name"] for c in cleaned["cookies"]]
    assert names == ["PHPSESSID", "id"]
    assert state["cookies"][1]["name"] == "__ddg8_"  # original untouched


def test_load_storage_state_strips_ddg(tmp_path) -> None:
    path = tmp_path / "flru_storage.json"
    raw = {
        "cookies": [
            {"name": "pwd", "value": "p", "domain": ".fl.ru"},
            {"name": "__ddg1_", "value": "z", "domain": ".fl.ru"},
        ],
        "origins": [],
    }
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_storage_state(path)
    assert loaded is not None
    assert [c["name"] for c in loaded["cookies"]] == ["pwd"]
