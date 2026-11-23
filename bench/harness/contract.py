#!/usr/bin/env python3
"""Machine-checked module contracts, declared in a task's task.json under "contract":

  "contract": {
    "controls": {"pitch": [3.6, 2.4, 6], ...},                      default, min, max
    "ports": {"west": {"type": "deck", "origin": ["-pitch/2", "-depth/2", "0"], "normal": [-1, 0, 0],
                       "dims": {"width": "depth"}}, ...},           expressions in the control names
    "envelope": {"x": ["-pitch/2 - 0.05", "pitch/2 + 0.05"], "y": [...], "z": [...]},   bounds stay inside
    "span": {"x": "0.95 * pitch"},                                  bounds span at least this much
    "clearances": {"archway": {"lo": ["-1.1", "-depth/2", "0.05"], "hi": ["1.1", "depth/2", "2.6"]}},
    "min_materials": 3
  }

checked at the defaults, at both ends of every control and at the corners of every pair of controls.
Returns {"ok": bool, "misfits": [...], "cases": n}. CLI: python3 contract.py <task.json> <doc.json> [pgm]"""
import itertools
import json
import math
import subprocess
import sys
from pathlib import Path

TOL = 0.02
SAFE = {k: getattr(math, k) for k in ("sin", "cos", "tan", "sqrt", "pi", "atan2", "floor", "ceil")}
SAFE.update(min=min, max=max, abs=abs)
_BATCH_VERIFY_CACHE = {}


def ev(expr, v):
    return float(expr) if isinstance(expr, (int, float)) else float(eval(str(expr), {"__builtins__": {}}, {**SAFE, **v}))


def pgm(binary, *args):
    out = subprocess.run([str(binary), *map(str, args)], capture_output=True, text=True, timeout=180).stdout.strip().splitlines()
    try:
        return json.loads(out[-1])
    except Exception:
        return {"passed": False}


def supports_batch_verify(binary):
    b = str(binary)
    if b not in _BATCH_VERIFY_CACHE:
        r = subprocess.run([b, "--help"], capture_output=True, text=True, timeout=30)
        _BATCH_VERIFY_CACHE[b] = "batch-verify" in (r.stdout + r.stderr)
    return _BATCH_VERIFY_CACHE[b]


def cases(controls):
    names = list(controls)
    out = [{}]
    for n in names:
        _, lo, hi = controls[n]
        out += [{n: lo}, {n: hi}]
    for a, b in itertools.combinations(names, 2):
        for x, y in itertools.product(controls[a][1:], controls[b][1:]):
            out.append({a: x, b: y})
    return out


def _eval_clearances(contract, v):
    raw = contract.get("clearances")
    if not raw:
        return []
    items = [{"name": k, **spec} for k, spec in raw.items()] if isinstance(raw, dict) else list(raw)
    out = []
    for item in items:
        lo = [ev(e, v) for e in item["lo"]]
        hi = [ev(e, v) for e in item["hi"]]
        out.append({"name": item.get("name", "clearance"),
                    "lo": [min(lo[i], hi[i]) for i in range(3)],
                    "hi": [max(lo[i], hi[i]) for i in range(3)]})
    return out


def _check_case_result(contract, where, v, ports_list, bounds, evaluated_ok=True, clearance_violations=None):
    misfits = []
    got = {p["name"]: p for p in (ports_list or [])}
    for name, want in contract.get("ports", {}).items():
        p = got.get(name)
        if not p:
            misfits.append(f"[{where}] port `{name}` is missing")
            continue
        if want.get("type") and p.get("type") != want["type"]:
            misfits.append(f"[{where}] port `{name}` has type {p.get('type')}; contract: {want['type']}")
        if "origin" in want:
            o = [None if e is None else ev(e, v) for e in want["origin"]]
            if any(b is not None and abs(a - b) > TOL for a, b in zip(p["origin"], o)):
                misfits.append(f"[{where}] port `{name}` at {[round(a, 3) for a in p['origin']]}; contract: {[None if a is None else round(a, 3) for a in o]}")
        if "normal" in want and any(abs(a - b) > 1e-3 for a, b in zip(p["normal"], [ev(e, v) for e in want["normal"]])):
            misfits.append(f"[{where}] port `{name}` faces {p['normal']}; contract: {want['normal']}")
        for k, e in (want.get("dims") or {}).items():
            have = (p.get("dimensions") or {}).get(k)
            if have is None or abs(have - ev(e, v)) > TOL:
                misfits.append(f"[{where}] port `{name}` {k} = {have}; contract: {round(ev(e, v), 3)}")
    if contract.get("envelope") or contract.get("span") or contract.get("ground_z"):
        if not evaluated_ok or not bounds:
            misfits.append(f"[{where}] the module does not evaluate")
            return misfits
        for i, axis in enumerate("xyz"):
            if axis in contract.get("envelope", {}):
                ent = contract["envelope"][axis]
                if isinstance(ent, (int, float)):
                    lo, hi = (-float(ent) * 0.5, float(ent) * 0.5) if axis != "z" else (0.0, float(ent))
                else:
                    lo, hi = (ev(e, v) for e in ent)
                if b_out := (bounds[0][i] < lo - TOL or bounds[1][i] > hi + TOL):
                    misfits.append(f"[{where}] {axis} spans [{bounds[0][i]:.2f}, {bounds[1][i]:.2f}]; contract envelope [{lo:.2f}, {hi:.2f}]")
            if axis in contract.get("span", {}):
                need = ev(contract["span"][axis], v)
                if bounds[1][i] - bounds[0][i] < need - TOL:
                    misfits.append(f"[{where}] {axis} extent {bounds[1][i] - bounds[0][i]:.2f}; contract asks for at least {need:.2f}")
        if isinstance(contract.get("ground_z"), list) and len(contract["ground_z"]) == 2:
            glo, ghi = float(contract["ground_z"][0]), float(contract["ground_z"][1])
            if bounds[0][2] < glo - TOL or bounds[0][2] > ghi + TOL:
                misfits.append(f"[{where}] ground z base is {bounds[0][2]:.3f}; contract ground_z [{glo:.3f}, {ghi:.3f}]")
    for cv in (clearance_violations or []):
        srcs = ", ".join(cv.get("sources") or [])
        by_src = f" ({srcs})" if srcs else ""
        misfits.append(f"[{where}] clearance `{cv.get('name', 'opening')}` is obstructed by {cv.get('intrudingTriangles', 1)} triangle(s){by_src}")
    return misfits


def _finalize_misfits(misfits, n_cases):
    seen, grouped = {}, []
    for m in misfits:
        key = m.split("] ", 1)[-1].split(";")[0].split(" at ")[0].split(" spans ")[0].split(" extent ")[0].split(" by ")[0]
        seen.setdefault(key, []).append(m)
    for key, ms in seen.items():
        grouped.append(ms[0] + (f"  (and {len(ms) - 1} more settings)" if len(ms) > 1 else ""))
    hard = [m for m in grouped if severity(m) == "hard"]
    return {"ok": not misfits, "hard_ok": not hard, "misfits": grouped, "hard": hard,
            "soft": [m for m in grouped if severity(m) == "soft"], "cases": n_cases}


def batch_verify(contract, doc, binary, sets=None, check_dangling=True):
    """Single-process warm verification (`pgm batch-verify`) returning `{bv, contract_result}`."""
    controls = (contract or {}).get("controls", {})
    all_cases = cases(controls) if contract and controls else ([{}] if contract else [])
    payload = []
    for case in all_cases:
        v = {n: case.get(n, d) for n, (d, _, _) in controls.items()}
        clrs = _eval_clearances(contract, v) if contract else []
        payload.append({"set": case, "clearances": clrs} if clrs else {"set": case})
    args = ["batch-verify", str(doc)]
    if check_dangling:
        args.append("--dangling")
    for k, val in (sets or {}).items():
        args += ["--set", f"{k}={val}"]
    if payload:
        args += ["--cases", json.dumps(payload)]
    bv = pgm(binary, *args)
    if not contract:
        return bv, None
    misfits = []
    ins = bv.get("inspect") or {}
    declared = ins.get("controls") or {}
    for n, (d, lo, hi) in controls.items():
        c = declared.get(n)
        if not c:
            misfits.append(f"control `{n}` is missing (contract: {d} in [{lo}, {hi}])")
        elif not (abs(c["value"] - d) < 1e-3 and abs(c.get("min", d) - lo) < 1e-3 and abs(c.get("max", d) - hi) < 1e-3):
            misfits.append(f"control `{n}` is {c['value']} in [{c.get('min')}, {c.get('max')}]; contract: {d} in [{lo}, {hi}]")
    mats = {m["slot"] for m in ins.get("materials", [])}
    if len(mats) < contract.get("min_materials", 0):
        misfits.append(f"{len(mats)} material slots ({', '.join(sorted(mats))}); the contract asks for at least {contract['min_materials']}")
    extra = bv.get("extraCases") or []
    for case, res in zip(all_cases, extra):
        v = {n: case.get(n, d) for n, (d, _, _) in controls.items()}
        where = "defaults" if not case else ", ".join(f"{k}={x}" for k, x in case.items())
        misfits.extend(_check_case_result(
            contract, where, v,
            res.get("ports", []),
            res.get("boundsZUp"),
            evaluated_ok=bool(res.get("passed")),
            clearance_violations=res.get("clearanceViolations"),
        ))
    return bv, _finalize_misfits(misfits, len(all_cases))


def check(contract, doc, binary, cwd=None):
    if supports_batch_verify(binary) and Path(doc).exists():
        _, res = batch_verify(contract, doc, binary, check_dangling=False)
        if res is not None:
            return res
    misfits = []
    controls = contract.get("controls", {})
    info = pgm(binary, "inspect", doc)
    declared = info.get("controls", {})
    for n, (d, lo, hi) in controls.items():
        c = declared.get(n)
        if not c:
            misfits.append(f"control `{n}` is missing (contract: {d} in [{lo}, {hi}])")
        elif not (abs(c["value"] - d) < 1e-3 and abs(c["min"] - lo) < 1e-3 and abs(c["max"] - hi) < 1e-3):
            misfits.append(f"control `{n}` is {c['value']} in [{c['min']}, {c['max']}]; contract: {d} in [{lo}, {hi}]")
    mats = {m["slot"] for m in info.get("materials", [])}
    if len(mats) < contract.get("min_materials", 0):
        misfits.append(f"{len(mats)} material slots ({', '.join(sorted(mats))}); the contract asks for at least {contract['min_materials']}")
    all_cases = cases(controls) if controls else [{}]
    for case in all_cases:
        v = {n: case.get(n, d) for n, (d, _, _) in controls.items()}
        where = "defaults" if not case else ", ".join(f"{k}={x}" for k, x in case.items())
        sets = [a for k, x in case.items() for a in ("--set", f"{k}={x}")]
        ins = pgm(binary, "inspect", doc, *sets)
        b = None
        eval_ok = True
        if contract.get("envelope") or contract.get("span") or contract.get("ground_z"):
            b = pgm(binary, "evaluate", doc, *sets).get("boundsZUp")
            eval_ok = bool(b)
        misfits.extend(_check_case_result(contract, where, v, ins.get("ports", []), b, evaluated_ok=eval_ok))
    return _finalize_misfits(misfits, len(all_cases))


HARD = ("is missing", "has type", "faces ", "does not evaluate", "control `", "is obstructed")


def severity(misfit):
    """hard: the module breaks its interface or blocks a declared negative-space clearance; soft: off in size or detail."""
    return "hard" if any(k in misfit for k in HARD) else "soft"


if __name__ == "__main__":
    spec = json.loads(Path(sys.argv[1]).read_text())
    binary = sys.argv[3] if len(sys.argv) > 3 else "pgm"
    r = check(spec["contract"], sys.argv[2], binary)
    print("contract: ok" if r["ok"] else "\n".join("MISFIT: " + m for m in r["misfits"]))
    sys.exit(0 if r["ok"] else 1)
