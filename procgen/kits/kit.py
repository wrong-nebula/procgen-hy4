#!/usr/bin/env python3
"""Optimization kit driver (Thermite-style hill climbing, no external deps).

A kit (kits/<name>/kit.json) fixes what "better" means for one operation:
an objective (a benchmark metric to minimize), hard constraints (tests,
sanitizer run, per-case quality checks), and guard cases that must not
regress. Each candidate change is scored the same way and only accepted when
it beats the baseline by more than the noise margin with every constraint met.

  kit.py score   <kit> <label>          build + verify + bench, save runs/<label>.json
  kit.py compare <kit> <base> <cand>    accept/reject candidate against baseline
"""
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # procgen/


def sh(cmd, capture=False):
    r = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.STDOUT if capture else None)
    return r.returncode, (r.stdout if capture else "")


def load_kit(name):
    d = ROOT / "kits" / name
    return d, json.loads((d / "kit.json").read_text())


def score(name, label):
    d, kit = load_kit(name)
    record = {"kit": name, "label": label, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "constraints": {}}
    for step in ("build", "verify"):
        for i, cmd in enumerate(kit[step]):
            code, out = sh(cmd, capture=True)
            record["constraints"][f"{step}[{i}]"] = code == 0
            if code != 0:
                print(f"{' '.join(cmd)} failed:\n" + "\n".join(out.splitlines()[-30:]))
                record["ok"] = False
                return save(d, label, record)
    trials = []
    for _ in range(kit.get("trials", 3)):
        code, out = sh(kit["bench"], capture=True)
        cases = {c["name"]: c for c in json.loads(out)}
        record["constraints"]["bench_quality"] = record["constraints"].get("bench_quality", True) and code == 0 and all(
            c["pass"] for c in cases.values())
        trials.append(cases)
    obj = kit["objective"]
    record["objective"] = [t[obj["case"]][obj["metric"]] for t in trials]
    record["guards"] = {g: [t[g][obj["metric"]] for t in trials] for g in kit.get("guards", [])}
    record["quality"] = {n: {k: v for k, v in c.items() if k not in ("median_ns", "min_ns")} for n, c in trials[-1].items()}
    record["ok"] = all(record["constraints"].values())
    return save(d, label, record)


def save(d, label, record):
    (d / "runs").mkdir(exist_ok=True)
    path = d / "runs" / f"{label}.json"
    path.write_text(json.dumps(record, indent=2))
    med = statistics.median(record["objective"]) if record.get("objective") else float("nan")
    print(f"[{record['kit']}:{label}] ok={record['ok']} objective_median={med / 1e6:.3f} ms -> {path.relative_to(ROOT)}")
    return 0 if record["ok"] else 1


def compare(name, base_label, cand_label):
    d, kit = load_kit(name)
    base = json.loads((d / "runs" / f"{base_label}.json").read_text())
    cand = json.loads((d / "runs" / f"{cand_label}.json").read_text())
    min_gain = kit.get("min_gain", 0.03)
    guard_tol = kit.get("guard_tolerance", 0.05)
    reasons = []
    if not cand["ok"]:
        reasons.append("constraint failed: " + ", ".join(k for k, v in cand["constraints"].items() if not v))
    b, c = statistics.median(base["objective"]), statistics.median(cand["objective"])
    gain = (b - c) / b
    if gain < min_gain:
        reasons.append(f"gain {gain:+.1%} below required {min_gain:.0%}")
    if max(cand["objective"]) >= min(base["objective"]):
        reasons.append("trial ranges overlap (not separable from noise)")
    # Guards compare best trials: scheduler noise only ever adds time, so the
    # minimum is the stable estimate for short cases (medians of 3 swing ~30%).
    for g, vals in cand.get("guards", {}).items():
        bg = min(base["guards"][g])
        cg = min(vals)
        if cg > bg * (1 + guard_tol):
            reasons.append(f"guard {g} regressed {cg / bg - 1:+.1%}")
    verdict = "ACCEPT" if not reasons else "REJECT"
    print(f"{verdict} {cand_label} vs {base_label}: {b / 1e6:.3f} ms -> {c / 1e6:.3f} ms ({gain:+.1%})")
    for r in reasons:
        print("  -", r)
    return 0 if verdict == "ACCEPT" else 1


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "score":
        sys.exit(score(sys.argv[2], sys.argv[3]))
    if len(sys.argv) >= 5 and sys.argv[1] == "compare":
        sys.exit(compare(sys.argv[2], sys.argv[3], sys.argv[4]))
    print(__doc__)
    sys.exit(2)
