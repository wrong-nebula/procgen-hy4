#!/usr/bin/env python3
"""PostToolUse hook (every run): after a Bash call, if the builder changed since the last snapshot and its freshly
regenerated module passes pgm check, keep it as last_good.py; if it also passes sweep and audit (--sweep), keep it as
last_valid.py. collect() falls back to last_valid.py, then last_good.py, when the final builder is broken or invalid."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # sandbox root (this file lives in <box>/.bench/)


def passed(pgm, *args):
    try:
        out = subprocess.run([pgm, *args], capture_output=True, text=True, timeout=120).stdout
        return bool(json.loads(out.strip().splitlines()[-1]).get("passed"))
    except Exception:
        return False


try:
    sys.stdin.read()
    cfg = json.loads((HERE / ".bench.json").read_text())
    builder = HERE / "recipes" / "build_bench_kit.py"
    doc = HERE / "recipes" / "site" / "bench" / f"{cfg['module']}.json"
    seen = HERE / ".bench" / "seen.py"
    if builder.exists() and doc.exists() and doc.stat().st_mtime >= builder.stat().st_mtime:
        text = builder.read_bytes()
        if not (seen.exists() and seen.read_bytes() == text):     # nothing new since the last look: skip pgm
            shutil.copy2(builder, seen)
            pgm = str(HERE / "bin" / "pgm")
            if passed(pgm, "check", str(doc)):
                shutil.copy2(builder, HERE / ".bench" / "last_good.py")
                if passed(pgm, "sweep", str(doc)) and passed(pgm, "audit", str(doc), "--sweep"):
                    shutil.copy2(builder, HERE / ".bench" / "last_valid.py")
except Exception:
    pass
sys.exit(0)
