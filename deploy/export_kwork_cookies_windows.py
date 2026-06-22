"""Export kwork.ru cookies from local Chrome → Playwright storage_state JSON."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _same_site(value: str | None) -> str:
    if not value:
        return "Lax"
    v = value.lower()
    if v in ("none", "no_restriction"):
        return "None"
    if v == "strict":
        return "Strict"
    return "Lax"


def export_from_chrome(out_path: Path) -> int:
    try:
        import browser_cookie3
    except ImportError:
        print("pip install browser_cookie3")
        return 1

    seen: set[tuple[str, str, str]] = set()
    cookies: list[dict] = []

    for loader_name, loader in (
        ("chrome", browser_cookie3.chrome),
        ("edge", getattr(browser_cookie3, "edge", None)),
    ):
        if loader is None:
            continue
        try:
            jar = loader(domain_name="kwork.ru")
        except Exception as exc:
            print(f"skip {loader_name}: {exc}")
            continue

        for c in jar:
            if "kwork" not in (c.domain or ""):
                continue
            key = (c.name, c.domain or "", c.path or "/")
            if key in seen:
                continue
            seen.add(key)
            cookies.append(
                {
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain,
                    "path": c.path or "/",
                    "expires": float(c.expires) if c.expires else -1,
                    "httpOnly": False,
                    "secure": bool(c.secure),
                    "sameSite": _same_site(getattr(c, "_rest", {}).get("SameSite")),
                }
            )

    if not cookies:
        print("FAIL: no kwork.ru cookies in Chrome/Edge. Close Chrome and retry.")
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"cookies": cookies, "origins": []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"OK: {len(cookies)} cookies -> {out_path}")
    for c in cookies[:8]:
        print(f"  - {c['name']} ({c['domain']})")
    return 0


if __name__ == "__main__":
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/kwork_storage.json")
    sys.exit(export_from_chrome(dest))
