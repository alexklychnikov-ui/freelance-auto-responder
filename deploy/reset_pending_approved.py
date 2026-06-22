import json
import sys
from pathlib import Path

path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    "data/pending_offers/kwork_kwork_dev_it_3202099.json"
)
data = json.loads(path.read_text(encoding="utf-8"))
data["status"] = "approved"
path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print("OK", path, data["status"])
