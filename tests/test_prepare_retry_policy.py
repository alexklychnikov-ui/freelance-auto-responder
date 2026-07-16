from __future__ import annotations

from src.pipeline.orchestrator import _PREPARE_FORM_ONLY_RETRY


def test_prepare_form_only_retry_tokens() -> None:
    assert "prepare_milestone_click_failed" in _PREPARE_FORM_ONLY_RETRY
    assert "prepare_stages_not_visible" in _PREPARE_FORM_ONLY_RETRY
    assert "not_logged_in" not in _PREPARE_FORM_ONLY_RETRY


def test_is_milestone_card_selected_logic() -> None:
    from src.adapters import kwork as kwork_mod

    class FakeBrowser:
        def __init__(self, value: bool) -> None:
            self._value = value

        def evaluate(self, _js: str) -> bool:
            return self._value

    assert kwork_mod._is_milestone_card_selected(FakeBrowser(True)) is True
    assert kwork_mod._is_milestone_card_selected(FakeBrowser(False)) is False


def test_wait_payment_block_requires_items_not_hidden_stages() -> None:
    from src.adapters import kwork as kwork_mod

    class FakeBrowser:
        def __init__(self, counts: list[int]) -> None:
            self._counts = counts
            self._idx = 0

        def evaluate(self, _js: str) -> int:
            if self._idx < len(self._counts):
                value = self._counts[self._idx]
                self._idx += 1
                return value
            return self._counts[-1] if self._counts else 0

        def wait_ms(self, _ms: int) -> None:
            return None

    assert kwork_mod._wait_payment_block_ready(FakeBrowser([0, 0, 2])) is True
    assert kwork_mod._wait_payment_block_ready(FakeBrowser([0, 0, 0]), attempts=3) is False


def test_infer_payment_mode_pure() -> None:
    from src.adapters.kwork import _infer_payment_mode

    assert _infer_payment_mode(payment_items=2, stages_enabled=False) == "stages"
    assert _infer_payment_mode(payment_items=0, stages_enabled=True) == "stages"
    assert (
        _infer_payment_mode(
            payment_items=0, stages_enabled=False, has_price_input=True
        )
        == "lump"
    )
    assert (
        _infer_payment_mode(
            payment_items=0, stages_enabled=False, has_price_input=False
        )
        == "lump"
    )


def test_detect_payment_mode_via_helpers(monkeypatch) -> None:
    from src.adapters import kwork as kwork_mod

    monkeypatch.setattr(kwork_mod, "_payment_items_count", lambda _b: 0)
    monkeypatch.setattr(kwork_mod, "_stages_block_enabled", lambda _b: False)
    monkeypatch.setattr(kwork_mod, "_has_custom_price_input", lambda _b: True)
    assert kwork_mod._detect_payment_mode(object()) == "lump"

    monkeypatch.setattr(kwork_mod, "_payment_items_count", lambda _b: 2)
    assert kwork_mod._detect_payment_mode(object()) == "stages"

    monkeypatch.setattr(kwork_mod, "_payment_items_count", lambda _b: 0)
    monkeypatch.setattr(kwork_mod, "_stages_block_enabled", lambda _b: True)
    assert kwork_mod._detect_payment_mode(object()) == "stages"


def test_stages_block_enabled_requires_visible_display(monkeypatch) -> None:
    from src.adapters import kwork as kwork_mod

    monkeypatch.setattr(kwork_mod, "_stages_block_display", lambda _b: "none")
    assert kwork_mod._stages_block_enabled(object()) is False

    monkeypatch.setattr(kwork_mod, "_stages_block_display", lambda _b: None)
    assert kwork_mod._stages_block_enabled(object()) is False

    monkeypatch.setattr(kwork_mod, "_stages_block_display", lambda _b: "block")
    monkeypatch.setattr(kwork_mod, "_stages_section_visible", lambda _b, min_rows=1: True)
    assert kwork_mod._stages_block_enabled(object()) is True

    monkeypatch.setattr(kwork_mod, "_stages_section_visible", lambda _b, min_rows=1: False)
    assert kwork_mod._stages_block_enabled(object()) is False


def test_prime_payment_ui_accepts_lump_when_price_set(monkeypatch) -> None:
    from src.adapters import kwork as kwork_mod

    class FakeBrowser:
        def wait_ms(self, _ms: int) -> None:
            return None

    monkeypatch.setattr(kwork_mod, "_payment_items_count", lambda _b: 0)
    monkeypatch.setattr(kwork_mod, "_stages_block_enabled", lambda _b: False)
    monkeypatch.setattr(kwork_mod, "_fill_price", lambda _b, _p: True)
    monkeypatch.setattr(kwork_mod, "_detect_payment_mode", lambda _b: "lump")
    monkeypatch.setattr(kwork_mod, "_read_custom_price_value", lambda _b: "1000")

    assert kwork_mod._prime_payment_ui(FakeBrowser(), "1000") is True


def test_prime_payment_ui_still_ready_when_cards_appear(monkeypatch) -> None:
    from src.adapters import kwork as kwork_mod

    class FakeBrowser:
        def wait_ms(self, _ms: int) -> None:
            return None

    calls = {"n": 0}

    def count(_b: object) -> int:
        calls["n"] += 1
        return 2 if calls["n"] > 2 else 0

    monkeypatch.setattr(kwork_mod, "_payment_items_count", count)
    monkeypatch.setattr(kwork_mod, "_stages_block_enabled", lambda _b: False)
    monkeypatch.setattr(kwork_mod, "_fill_price", lambda _b, _p: True)

    assert kwork_mod._prime_payment_ui(FakeBrowser(), "1000") is True


def test_read_payment_diag_includes_mode_fields() -> None:
    from src.adapters import kwork as kwork_mod

    class FakeBrowser:
        def evaluate(self, _js: str) -> dict:
            return {
                "paymentItems": 0,
                "hasMilestoneCard": False,
                "milestoneActive": False,
                "stagePriceInputs": 0,
                "stagesVisible": 0,
                "stagesBlockDisplay": "none",
                "hasCustomPrice": True,
                "customPriceValue": "1000",
                "url": "https://kwork.ru/projects/3217710/offer",
            }

    diag = kwork_mod._read_payment_diag(FakeBrowser())
    assert diag["mode"] == "lump"
    assert diag["stagesBlockDisplay"] == "none"
    assert diag["stagesVisible"] == 0
    assert diag["customPriceValue"] == "1000"
