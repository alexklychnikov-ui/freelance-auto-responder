from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.adapters.kwork_auth import is_logged_in
from src.browser.base import BrowserClient

KWORK_OFFERS_URL = "https://kwork.ru/offers"

KWORK_OFFERS_PARSE_JS = """
() => {
  const cards = [...document.querySelectorAll('.want-card')];
  return cards.map((card) => {
    const link = card.querySelector('a[href*="/projects/"]');
    const match = link?.href?.match(/\\/projects\\/(\\d+)/);
    const informers = [...card.querySelectorAll('.want-card__informers-item')]
      .map((node) => (node.textContent || '').replace(/\\s+/g, ' ').trim())
      .filter(Boolean);
    return {
      project_id: match ? match[1] : null,
      title: (link?.textContent || '').replace(/\\s+/g, ' ').trim(),
      informers,
    };
  }).filter((item) => item.project_id);
}
"""

_ORDER_RE = re.compile(r"покупатель сделал\s+(\d+)\s+заказ", re.I)
_WAITING_RE = re.compile(r"покупатель пока не сделал заказ", re.I)


@dataclass(frozen=True)
class KworkMyOfferStatus:
    project_id: str
    title: str
    informers: tuple[str, ...]
    buyer_orders: int | None = None
    waiting_for_order: bool = False

    @property
    def on_offers(self) -> bool:
        return True


def _status_from_informers(informers: list[str]) -> tuple[int | None, bool]:
    orders: int | None = None
    waiting = False
    for text in informers:
        order_match = _ORDER_RE.search(text)
        if order_match:
            orders = int(order_match.group(1))
            break
        if _WAITING_RE.search(text):
            waiting = True
    return orders, waiting


def parse_offers_items(raw: list[dict[str, Any]]) -> dict[str, KworkMyOfferStatus]:
    out: dict[str, KworkMyOfferStatus] = {}
    for item in raw:
        project_id = str(item.get("project_id") or "").strip()
        if not project_id:
            continue
        informers = [
            str(x).strip()
            for x in (item.get("informers") or [])
            if str(x).strip()
        ]
        orders, waiting = _status_from_informers(informers)
        out[project_id] = KworkMyOfferStatus(
            project_id=project_id,
            title=str(item.get("title") or "").strip(),
            informers=tuple(informers),
            buyer_orders=orders,
            waiting_for_order=waiting,
        )
    return out


def parse_offers_html(html: str) -> dict[str, KworkMyOfferStatus]:
    items: list[dict[str, Any]] = []
    for match in re.finditer(r'href="[^"]*?/projects/(\d+)"', html, re.I):
        project_id = match.group(1)
        start = max(0, match.start() - 120)
        end = min(len(html), match.end() + 1200)
        block = html[start:end]
        title_match = re.search(
            r'href="[^"]*?/projects/' + re.escape(project_id) + r'"[^>]*>([^<]+)<',
            block,
            re.I,
        )
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
        informers = [
            re.sub(r"\s+", " ", text).strip()
            for text in re.findall(
                r"want-card__informers-item[^>]*>(?:<[^>]+>)*([^<]+)",
                block,
                flags=re.I,
            )
            if text.strip()
        ]
        items.append(
            {
                "project_id": project_id,
                "title": title,
                "informers": informers,
            }
        )
    return parse_offers_items(items)


def journal_status_for_offer(offer: KworkMyOfferStatus) -> tuple[str, str]:
    if offer.buyer_orders:
        return (
            "Отказ",
            f"Покупатель сделал {offer.buyer_orders} заказ",
        )
    if offer.waiting_for_order:
        return ("Отправлен", "Жду ответа")
    hint = offer.informers[0] if offer.informers else "На бирже"
    return ("Отправлен", hint)


def fetch_my_offer_statuses(browser: BrowserClient) -> dict[str, KworkMyOfferStatus]:
    browser.navigate(KWORK_OFFERS_URL)
    if hasattr(browser, "wait_ms"):
        browser.wait_ms(4000)
    if not is_logged_in(browser):
        raise RuntimeError("not_logged_in: открой Kwork в браузере или задай kwork_storage.json")
    raw = browser.evaluate(KWORK_OFFERS_PARSE_JS)
    if not isinstance(raw, list):
        raise RuntimeError("offers_parse_failed: unexpected browser response")
    return parse_offers_items(raw)
