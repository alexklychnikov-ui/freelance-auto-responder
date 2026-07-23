from __future__ import annotations

from typing import Any

from src.adapters.kwork import (
    READ_OFFER_DESCRIPTION_JS,
    OfferFormSnapshot,
    _parse_delivery_days_label,
    read_submitted_offer_text,
)
from src.adapters.kwork_offers import (
    offer_comment_to_plaintext,
    parse_state_data_offer_comments,
)


class FakeOfferBrowser:
    def __init__(
        self,
        *,
        logged_in: bool = True,
        state_data: dict[str, Any] | None = None,
        form_payload: dict[str, Any] | None = None,
    ) -> None:
        self.logged_in = logged_in
        self.state_data = state_data
        self.form_payload = form_payload or {}
        self.last_url: str | None = None
        self.evaluate_calls: list[str] = []

    def navigate(self, url: str) -> None:
        self.last_url = url

    def wait_ms(self, ms: int) -> None:
        pass

    def evaluate(self, js: str) -> Any:
        self.evaluate_calls.append(js)
        if "text === 'Вход'" in js or 'href === \'/login\'' in js or 'a[href="/seller"]' in js:
            return self.logged_in
        if "location.pathname" in js and "/offers" in js and "stateData" not in js:
            return bool(self.last_url and "/offers" in self.last_url)
        if "stateData" in js and "wantId" in js:
            if self.state_data is None:
                return {"ok": False, "reason": "no_stateData_offers", "items": []}
            return self.state_data
        if "hasForm" in js and "listingRedirect" in js:
            return {
                "url": self.last_url or "",
                "hasForm": self.form_payload.get("hasForm", True),
                "listingRedirect": False,
                "projectClosed": False,
            }
        if "fromTaLen" in js or "fromEdLen" in js:
            return {
                "url": self.last_url or "",
                "hasForm": self.form_payload.get("hasForm", True),
                "description": self.form_payload.get("description", ""),
                "fromTaLen": self.form_payload.get("fromTaLen", 0),
                "fromEdLen": self.form_payload.get("fromEdLen", 0),
            }
        if "descLen" in js or "deadlineLabel" in js:
            return {
                "url": self.last_url or "",
                "description": self.form_payload.get("description", ""),
                "descLen": len(self.form_payload.get("description", "") or ""),
                "descPreview": (self.form_payload.get("description", "") or "")[:100],
                "price": self.form_payload.get("price", ""),
                "title": "",
                "deadline": self.form_payload.get("deadline", ""),
                "deadlineLabel": self.form_payload.get("deadlineLabel", ""),
            }
        return self.logged_in


def test_parse_delivery_days_label() -> None:
    assert _parse_delivery_days_label("10 дней") == 10
    assert _parse_delivery_days_label("14") == 14
    assert _parse_delivery_days_label("") is None


def test_offer_comment_to_plaintext_unescapes() -> None:
    raw = "Здравствуйте! Нужен чат&mdash;бот<br>и API."
    text = offer_comment_to_plaintext(raw)
    assert "—" in text or "mdash" not in text
    assert "Здравствуйте!" in text
    assert "<br>" not in text


def test_parse_state_data_offer_comments() -> None:
    raw = {
        "ok": True,
        "items": [
            {
                "wantId": "3217871",
                "comment": "Здравствуйте! Понимаю, что вам нужен чат-бот&hellip;",
                "price": "15000",
                "days": 14,
            }
        ],
    }
    parsed = parse_state_data_offer_comments(raw)
    assert "3217871" in parsed
    assert parsed["3217871"].comment.startswith("Здравствуйте!")
    assert "&hellip;" not in parsed["3217871"].comment
    assert parsed["3217871"].price == "15000"
    assert parsed["3217871"].delivery_days == 14


def test_read_submitted_offer_text_primary_statedata() -> None:
    browser = FakeOfferBrowser(
        state_data={
            "ok": True,
            "items": [
                {
                    "wantId": "3217871",
                    "comment": "Здравствуйте! Понимаю, что вам нужен чат-бот " + ("x" * 40),
                    "price": None,
                    "days": None,
                }
            ],
        }
    )
    snap = read_submitted_offer_text(browser, "3217871")
    assert snap.ok is True
    assert snap.description.startswith("Здравствуйте! Понимаю")
    assert browser.last_url == "https://kwork.ru/offers"
    assert "new_offer" not in (browser.last_url or "")


def test_read_submitted_offer_text_fallback_new_offer_form() -> None:
    browser = FakeOfferBrowser(
        state_data={"ok": True, "items": []},
        form_payload={
            "hasForm": True,
            "description": "A" * 120,
            "price": "8000",
            "deadlineLabel": "10 дней",
        },
    )
    snap = read_submitted_offer_text(browser, "3217871")
    assert snap.ok is True
    assert snap.description == "A" * 120
    assert snap.price == "8000"
    assert snap.delivery_days == 10
    assert browser.last_url == "https://kwork.ru/new_offer?project=3217871"
    assert any("trumbowyg-editor" in js for js in browser.evaluate_calls)


def test_read_submitted_offer_text_uses_preloaded_comments_map() -> None:
    browser = FakeOfferBrowser(state_data={"ok": True, "items": []})
    snap = read_submitted_offer_text(
        browser,
        "3217871",
        comments={"3217871": "Batch comment " + ("y" * 50)},
    )
    assert snap.ok is True
    assert snap.description.startswith("Batch comment")
    # Should not need /offers navigation when map provided.
    assert browser.last_url is None


def test_read_submitted_offer_text_not_logged_in() -> None:
    browser = FakeOfferBrowser(
        logged_in=False,
        state_data={"ok": True, "items": []},
        form_payload={"hasForm": True, "description": "x" * 60},
    )
    snap = read_submitted_offer_text(browser, "1")
    assert snap.ok is False


def test_check_offer_form_unavailable_prefers_not_logged_in() -> None:
    from src.adapters.kwork import _check_offer_form_available

    class GuestListingBrowser(FakeOfferBrowser):
        def evaluate(self, js: str):
            if "hasForm" in js and "listingRedirect" in js:
                return {
                    "url": "https://kwork.ru/projects",
                    "hasForm": False,
                    "listingRedirect": True,
                    "projectClosed": False,
                }
            return super().evaluate(js)

    browser = GuestListingBrowser(logged_in=False)
    result = _check_offer_form_available(browser, "3218308")
    assert result is not None
    assert result.success is False
    assert "not_logged_in" in (result.message or "")
    assert "offer_form_unavailable" not in (result.message or "")


def test_read_offer_description_js_returns_full_string_contract() -> None:
    assert "description" in READ_OFFER_DESCRIPTION_JS
    assert "slice(0, 100)" not in READ_OFFER_DESCRIPTION_JS
    assert "trumbowyg-editor" in READ_OFFER_DESCRIPTION_JS


def test_offer_form_snapshot_defaults() -> None:
    snap = OfferFormSnapshot(description="")
    assert snap.ok is False
    assert snap.price is None
