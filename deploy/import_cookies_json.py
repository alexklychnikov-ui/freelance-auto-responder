"""Import Cookie-Editor / EditThisCookie JSON export → Playwright storage_state."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def convert(raw: list | dict, out_path: Path) -> int:
    items = raw if isinstance(raw, list) else raw.get("cookies", [])
    cookies: list[dict] = []
    for c in items:
        domain = c.get("domain") or c.get("host") or ""
        if "kwork" not in domain and "kwork" not in str(c.get("host", "")):
            continue
        if not domain.startswith(".") and domain and not domain.startswith("kwork"):
            domain = "." + domain.lstrip(".")
        cookies.append(
            {
                "name": c["name"],
                "value": c["value"],
                "domain": domain or ".kwork.ru",
                "path": c.get("path", "/"),
                "expires": float(c.get("expirationDate", c.get("expires", -1)) or -1),
                "httpOnly": bool(c.get("httpOnly", False)),
                "secure": bool(c.get("secure", True)),
                "sameSite": "Lax",
            }
        )
    if not cookies:
        print("FAIL: no kwork cookies in file")
        return 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"cookies": cookies, "origins": []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"OK: {len(cookies)} cookies -> {out_path}")
    print("names:", [c["name"] for c in cookies])
    return 0


if __name__ == "__main__":
    src = Path(sys.argv[1])
    dest = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/kwork_storage.json")
    data = json.loads(src.read_text(encoding="utf-8"))
    sys.exit(convert(data, dest))
