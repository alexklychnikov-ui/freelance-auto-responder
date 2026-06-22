import json
import sys
from pathlib import Path

path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    "/opt/freelance-responder/data/prepared_responses/kwork_kwork_dev_it_3202099.json"
)
data = json.loads(path.read_text(encoding="utf-8"))
data["journal_exported"] = False
path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print("reset", path.name)
