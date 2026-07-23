import os, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config import get_settings
s = get_settings()
db = s.database_path or "data/responder.db"
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
rows = con.execute(
    "select project_id, title, status, fit, score from projects where platform=? order by first_seen_at desc limit 15",
    ("yandex_uslugi",),
).fetchall()
print("count", len(rows))
for r in rows:
    t = (r["title"] or "")[:60]
    print(f"{r['project_id'][:8]} | {r['status']} | fit={r['fit']} score={r['score']} | {t}")
scan = con.execute(
    "select * from scan_state where source_key=?", ("yandex_uslugi_it",)
).fetchone()
print("scan_state:", dict(scan) if scan else None)
