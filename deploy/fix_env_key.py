#!/usr/bin/env python3
from pathlib import Path

p = Path("/opt/freelance-responder/.env")
lines = []
for line in p.read_text().splitlines():
    if line.startswith("OPENAI_API_KEY="):
        v = line.split("=", 1)[1]
        if len(v) > 200:
            unit = "1ZVr9C-BQ_7Z3Kg_b"
            i = v.find(unit)
            if i > 0:
                v = v[: i + len(unit)]
        line = "OPENAI_API_KEY=" + v
    lines.append(line)
p.write_text("\n".join(lines) + "\n")
print("fixed, key len:", len([l for l in lines if l.startswith("OPENAI_API_KEY")][0].split("=", 1)[1]))
