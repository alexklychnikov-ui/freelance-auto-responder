import json
from pathlib import Path

d = json.loads(Path("data/kwork_storage.json").read_text(encoding="utf-8"))
print("cookies", len(d["cookies"]))
for c in d["cookies"]:
    print(c["name"], c["domain"])
print("origins", d.get("origins"))
