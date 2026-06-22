"""Сбросить pending offer в status=pending на VPS или локально."""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "data" / "pending_offers"
VPS_BASE = Path("/opt/freelance-responder/data/pending_offers")


def reset(project_id: str, base: Path) -> bool:
    matches = list(base.glob(f"*_{project_id}.json"))
    if not matches:
        return False
    for path in matches:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["status"] = "pending"
        data["approved_at"] = None
        data.pop("draft_message_id", None)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("reset", path.name)
    return True


if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else ""
    if not pid:
        print("Usage: reset_pending_offer.py <project_id>")
        raise SystemExit(1)
    base = VPS_BASE if VPS_BASE.is_dir() else BASE
    if not reset(pid, base):
        print("not found", pid, "in", base)
        raise SystemExit(1)
