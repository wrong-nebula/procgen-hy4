#!/usr/bin/env python3
"""bin/pgm in a memory sandbox: runs the real pgm (bin/pgm.real) unchanged and, when the command fails, adds the
matching lessons from memory/ to its JSON output as "hints" (id, title, fix). Costs nothing when nothing fails."""
import json
import subprocess
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BIN.parent / "memory"))

p = subprocess.run([str(BIN / "pgm.real"), *sys.argv[1:]], capture_output=True, text=True)
out, err = p.stdout, p.stderr
try:
    lines = out.rstrip("\n").splitlines()
    doc = json.loads(lines[-1]) if lines else None
    failed = p.returncode != 0 or (isinstance(doc, dict) and doc.get("passed") is False)
    if failed:
        import lessons  # noqa: E402
        hits = lessons.query(out + " " + err, n=2, min_score=10.0)   # only lessons whose patterns match the error
        if hits and isinstance(doc, dict):
            doc["hints"] = [{"id": l["id"], "title": l["title"], "fix": l["fix"]} for l in hits]
            lines[-1] = json.dumps(doc)
            out = "\n".join(lines) + "\n"
            with open(BIN.parent / "memory" / "hint_log.jsonl", "a") as f:
                f.write(json.dumps({"args": sys.argv[1:3], "hints": [l["id"] for l in hits]}) + "\n")
except Exception:
    pass
sys.stdout.write(out)
sys.stderr.write(err)
sys.exit(p.returncode)
