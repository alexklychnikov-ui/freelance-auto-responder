import json
import httpx

base = "http://127.0.0.1:9621"
api_key = "Tm9pV2s8cL4eF7gH2bN6mP9qX3rY8tU1zJ5kR7wE0sA3dC6vB9nM"

paths = httpx.get(f"{base}/openapi.json", timeout=10).json().get("paths", {})
auth_paths = [p for p in paths if "login" in p.lower() or "auth" in p.lower()]
print("auth_paths", auth_paths[:20])

for header_name in ("X-API-Key", "Authorization", "api-key"):
    for val in (api_key, f"Bearer {api_key}"):
        r = httpx.post(
            f"{base}/query",
            json={"query": "test", "mode": "mix", "only_need_context": True},
            headers={header_name: val},
            timeout=15,
        )
        print(header_name, val[:20], "->", r.status_code, r.text[:120])

login_paths = [p for p in auth_paths if "login" in p.lower()]
for lp in login_paths[:3]:
    print("try login", lp, paths[lp].keys())
