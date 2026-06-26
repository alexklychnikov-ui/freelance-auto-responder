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
