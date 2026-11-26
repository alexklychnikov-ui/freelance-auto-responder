"""Platform up/suspect/down alerts with confirm window + cooldown."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from src.config import Settings

logger = logging.getLogger(__name__)

_DEFAULT_CONFIRM_SEC = 120
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


def _entry(data: dict[str, Any], key: str) -> dict[str, Any]:
    raw = data.get(key)
    return raw if isinstance(raw, dict) else {}


def note_platform_failure(
    settings: Settings,
    platform: str,
    *,
    confirm_sec: int = _DEFAULT_CONFIRM_SEC,
    cooldown_sec: int = _DEFAULT_COOLDOWN_SEC,
) -> bool:
    """Record a failure. True only when a user-visible DOWN alert should be sent.

    State machine per platform:
    - up → suspect (no TG)
    - suspect within confirm_sec → stay suspect (no TG)
    - suspect confirmed → down + TG unless still in cooldown since last_alert
    - already down + alert_sent within cooldown → no TG
    - already down outside cooldown → TG again
    """
    path = _alerts_path(settings)
    data = _load(path)
    key = str(platform)
    now = time.time()
    prev = _entry(data, key)
    status = str(prev.get("status") or "up")
    alert_sent = bool(prev.get("alert_sent"))
    last_alert_at = float(prev.get("last_alert_at") or 0)
    suspect_since = float(prev.get("suspect_since") or 0)
    cooldown = max(60, int(cooldown_sec))
    confirm = max(1, int(confirm_sec))

    if status == "up" or status not in ("suspect", "down"):
        data[key] = {
            "status": "suspect",
            "suspect_since": now,
            "last_alert_at": last_alert_at,
            "alert_sent": False,
        }
        _save(path, data)
        return False

    if status == "suspect":
        if suspect_since <= 0:
            data[key] = {
                "status": "suspect",
                "suspect_since": now,
                "last_alert_at": last_alert_at,
                "alert_sent": alert_sent,
            }
            _save(path, data)
            return False
        if now - suspect_since < confirm:
            return False
        if last_alert_at > 0 and now - last_alert_at < cooldown:
            data[key] = {
                "status": "down",
                "suspect_since": suspect_since,
                "last_alert_at": last_alert_at,
                "alert_sent": alert_sent,
            }
            _save(path, data)
            return False
        data[key] = {
            "status": "down",
            "suspect_since": suspect_since,
            "last_alert_at": now,
            "alert_sent": True,
        }
        _save(path, data)
        return True

    # status == "down"
    if alert_sent and now - last_alert_at < cooldown:
        return False
    data[key] = {
        "status": "down",
        "suspect_since": suspect_since or now,
        "last_alert_at": now,
        "alert_sent": True,
    }
    _save(path, data)
    return True


def mark_platform_up(settings: Settings, platform: str) -> bool:
    """Clear failure state. True only if a DOWN alert was previously sent."""
    path = _alerts_path(settings)
    data = _load(path)
    key = str(platform)
    prev = _entry(data, key)
    was_alerted = bool(prev.get("alert_sent"))
    data[key] = {
        "status": "up",
        "suspect_since": 0,
        "last_alert_at": float(prev.get("last_alert_at") or 0),
        "alert_sent": False,
    }
    _save(path, data)
    return was_alerted
