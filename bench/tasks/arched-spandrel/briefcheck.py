#!/usr/bin/env python3
"""Brief check for the arched arcade bay: controls, ports and extents at the defaults and at the ends of
every range, plus pgm check, sweep and audit. Prints one misfit per line, or that none were found."""
import itertools
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOC = HERE / "recipes" / "site" / "bench" / "arched-bay.json"
PGM = str(HERE / "bin" / "pgm")
CONTROLS = {"pitch": (3.6, 2.4, 6.0), "height": (4.2, 3.0, 5.4), "depth": (2.2, 1.2, 4.0)}
TOL = 0.02


def pgm(*args):
    out = subprocess.run([PGM, *args], capture_output=True, text=True).stdout.strip().splitlines()
    try:
        return json.loads(out[-1])
    except Exception:
        return {"passed": False, "error": "no JSON from pgm " + " ".join(args)}


def sets(v):
    return [a for k, x in v.items() for a in ("--set", f"{k}={x}")]


def near(a, b, tol=TOL):
    return abs(a - b) <= tol


def main():
    misfits = []
    if not DOC.exists():
        print(f"MISFIT: {DOC.relative_to(HERE)} does not exist; run your builder first")
        return 1
    info = pgm("inspect", str(DOC))
    ctl = info.get("controls", {})
    for name, (dflt, lo, hi) in CONTROLS.items():
        c = ctl.get(name)
        if not c:
            misfits.append(f"control `{name}` is missing (brief: {dflt} in [{lo}, {hi}])")
            continue
        if not (near(c["value"], dflt, 1e-3) and near(c["min"], lo, 1e-3) and near(c["max"], hi, 1e-3)):
            misfits.append(f"control `{name}` is {c['value']} in [{c['min']}, {c['max']}]; brief: {dflt} in [{lo}, {hi}]")

    cases = [{}]
    for name, (_, lo, hi) in CONTROLS.items():
        cases += [{name: lo}, {name: hi}]
    cases += [dict(pitch=p, height=h) for p, h in itertools.product((2.4, 6.0), (3.0, 5.4))]
    for case in cases:
        v = {k: case.get(k, d) for k, (d, _, _) in CONTROLS.items()}
        p, h, d = v["pitch"], v["height"], v["depth"]
        where = "defaults" if not case else ", ".join(f"{k}={x}" for k, x in case.items())
        ins = pgm("inspect", str(DOC), *sets(case))
        ports = {q["name"]: q for q in ins.get("ports", [])}
        for name, x, nx in (("west", -p / 2, -1), ("east", p / 2, 1)):
            q = ports.get(name)
            if not q:
                misfits.append(f"[{where}] port `{name}` is missing")
                continue
            o, n = q["origin"], q["normal"]
            if not (near(o[0], x) and near(o[1], -d / 2) and near(o[2], 0)):
                misfits.append(f"[{where}] port `{name}` at {[round(a, 3) for a in o]}; brief: {[x, -d / 2, 0]}")
            if not (near(n[0], nx, 1e-3) and near(n[1], 0, 1e-3) and near(n[2], 0, 1e-3)):
                misfits.append(f"[{where}] port `{name}` faces {n}; brief: [{nx}, 0, 0]")
            if q.get("type") != "deck" or not near((q.get("dimensions") or {}).get("width", -1), d):
                misfits.append(f"[{where}] port `{name}` is {q.get('type')} {q.get('dimensions')}; brief: deck width {d}")
        ev = pgm("evaluate", str(DOC), *sets(case))
        b = ev.get("boundsZUp")
        if not b:
            misfits.append(f"[{where}] evaluate failed: {ev.get('error', '')[:200]}")
            continue
        (x0, y0, z0), (x1, y1, z1) = b
        if x0 < -p / 2 - 0.05 or x1 > p / 2 + 0.05:
            misfits.append(f"[{where}] bay spills past the pier centres: x in [{x0:.2f}, {x1:.2f}], pitch/2 = {p / 2}")
        if x1 - x0 < 0.95 * p:
            misfits.append(f"[{where}] bay is {x1 - x0:.2f} m wide; it should span the pitch ({p} m) with half piers")
        if y1 > 0.05:
            misfits.append(f"[{where}] geometry passes the building wall (y up to {y1:.2f}; wall at y = 0)")
        if y0 > -d + 0.05 or y0 < -d - 0.4:
            misfits.append(f"[{where}] street face at y = {y0:.2f}; brief: y = -depth = {-d}")
        if z0 > 0.02:
            misfits.append(f"[{where}] bottom at z = {z0:.2f}: the bay floats above the pavement (z = 0)")
        if z0 < -1.0:
            misfits.append(f"[{where}] bottom at z = {z0:.2f}: footings go more than 1 m below the pavement")
        if not near(z1, h, 0.15):
            misfits.append(f"[{where}] top at z = {z1:.2f}; brief: height = {h}")

    if not pgm("check", str(DOC)).get("passed"):
        misfits.append("pgm check fails")
    if not pgm("sweep", str(DOC)).get("passed"):
        misfits.append("pgm sweep fails")
    if not pgm("audit", str(DOC)).get("passed"):
        misfits.append("pgm audit fails (parts float apart)")

    import re
    grouped = {}
    for m in misfits:
        g = re.match(r"\[(.*?)\] (.*)", m)
        where, text = (g.group(1), g.group(2)) if g else ("", m)
        generic = re.sub(r"-?\d+\.\d+|-?\d+", "#", text)
        grouped.setdefault(generic, []).append((where, text))
    for items in grouped.values():
        where = [w for w, _ in items if w]
        suffix = f"  (at {len(where)} settings: {', '.join(where[:4])}{', ...' if len(where) > 4 else ''})" if where else ""
        print("MISFIT:", items[0][1] + suffix)
    if not misfits:
        print("briefcheck: no misfits (controls, ports and extents match the brief; check, sweep and audit pass)")
    return 1 if misfits else 0


if __name__ == "__main__":
    sys.exit(main())
