import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import get_settings

path = "data/flru_storage.json"
if os.path.exists(path):
    d = json.load(open(path, encoding="utf-8"))
    cs = [c for c in d.get("cookies", []) if "fl.ru" in c.get("domain", "")]
    names = [c.get("name") for c in cs]
    print("file_ok", path)
    print("flru_cookies", len(cs))
    print("names", names)
    auth = [n for n in names if n and not n.startswith("_ym")]
    print("auth_like", auth)
else:
    print("missing", path)

s = get_settings()
print("env_flru_storage", repr(s.flru_storage_state))
