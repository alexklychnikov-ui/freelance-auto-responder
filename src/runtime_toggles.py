"""Runtime platform toggles (persist across daemon restarts, hot-read each cycle)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from src.config import Settings

logger = logging.getLogger(__name__)

_DEFAULT_TOGGLES: dict[str, Any] = {"kwork_paused": False}


def toggles_file_path(path_or_settings: Path | str | Settings) -> Path:
    if isinstance(path_or_settings, Settings):
        return Path(path_or_settings.database_path).parent / "platform_toggles.json"
    return Path(path_or_settings)


def get_toggles(path_or_settings: Path | str | Settings) -> dict[str, Any]:
    path = toggles_file_path(path_or_settings)
    if not path.exists():
        return dict(_DEFAULT_TOGGLES)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("platform_toggles_read_failed path=%s err=%s", path, exc)
        return dict(_DEFAULT_TOGGLES)
    if not isinstance(raw, dict):
        return dict(_DEFAULT_TOGGLES)
    out = dict(_DEFAULT_TOGGLES)
    out.update(raw)
    return out


def is_kwork_paused(path_or_settings: Path | str | Settings) -> bool:
    return bool(get_toggles(path_or_settings).get("kwork_paused", False))


def set_kwork_paused(
    path_or_settings: Path | str | Settings, *, paused: bool
) -> bool:
    path = toggles_file_path(path_or_settings)
    data = get_toggles(path)
    data["kwork_paused"] = bool(paused)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
    return bool(data["kwork_paused"])
