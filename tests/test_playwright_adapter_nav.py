from __future__ import annotations

import json

from src.browser.playwright_adapter import (
    _is_transient_nav_error,
    build_ipv4_host_resolver_rules,
    load_storage_state,
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


def test_load_storage_state_repairs_trailer(tmp_path) -> None:
    path = tmp_path / "flru_storage.json"
    good = {"cookies": [{"name": "a", "value": "1"}], "origins": []}
    path.write_text(json.dumps(good) + "]}]}", encoding="utf-8")
    loaded = load_storage_state(path)
    assert loaded == good
    # file rewritten clean
    assert json.loads(path.read_text(encoding="utf-8")) == good
