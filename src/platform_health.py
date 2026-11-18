"""Throttle platform-down alerts so soft-fail loops don't spam TG."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from src.config import Settings

logger = logging.getLogger(__name__)

_DEFAULT_COOLDOWN_SEC = 30 * 60


def _alerts_path(settings: Settings) -> Path:
    return Path(settings.database_path).parent / "platform_alerts.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("platform_alerts_read_failed path=%s err=%s", path, exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def should_send_platform_down_alert(
    settings: Settings,
    platform: str,
    *,
    cooldown_sec: int = _DEFAULT_COOLDOWN_SEC,
) -> bool:
    """True once per cooldown window per platform (first fail after silence)."""
    path = _alerts_path(settings)
    data = _load(path)
    key = str(platform)
    now = time.time()
    last = float((data.get(key) or {}).get("last_alert_at") or 0)
    if now - last < max(60, int(cooldown_sec)):
        return False
    data[key] = {
        "last_alert_at": now,
        "status": "down",
    }
    _save(path, data)
    return True


def mark_platform_up(settings: Settings, platform: str) -> bool:
    """Clear down state. Returns True if platform was previously marked down."""
    path = _alerts_path(settings)
    data = _load(path)
    key = str(platform)
    prev = data.get(key) or {}
    was_down = prev.get("status") == "down"
    data[key] = {"last_alert_at": prev.get("last_alert_at") or 0, "status": "up"}
    _save(path, data)
    return bool(was_down)
