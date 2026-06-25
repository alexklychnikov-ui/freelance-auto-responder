import json
import sys

path = f"data/prepared_responses/kwork_kwork_dev_it_{sys.argv[1]}.json"
d = json.load(open(path, encoding="utf-8"))
t = d.get("response_text", "")
print("price", d.get("price"))
print("days", d.get("delivery_days"))
print("len", len(t))
print("pars", "парс" in t.lower())
print("aiogram", "aiogram" in t.lower())
print("---")
print(t[:800])
