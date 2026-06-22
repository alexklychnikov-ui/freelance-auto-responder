from dotenv import dotenv_values
from httpx import Client

cfg = dotenv_values("/opt/freelance-responder/.env")
key = (cfg.get("OPENAI_API_KEY") or "").strip()
base = (cfg.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").strip()
model = (cfg.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
print("key_len", len(key))
print("base", base)
print("model", model)
r = Client(timeout=60).post(
    f"{base.rstrip('/')}/chat/completions",
    headers={"Authorization": f"Bearer {key}"},
    json={
        "model": model,
        "messages": [{"role": "user", "content": "Reply with OK only"}],
        "max_tokens": 5,
    },
)
print("status", r.status_code)
print("body", r.text[:400])
