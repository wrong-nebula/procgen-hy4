#!/usr/bin/env python3
"""Zero-LLM-cost ($0.00) local geometric offset repair (`local_repair.py`).

Uses `pgm batch-verify --dangling` (with pre-bake `face_sources`, `deltaToMain` gap vectors, and
dangling-support detection) + AST numeric constant/argument search on `build_bench_kit.py` to fix
small translation gaps, overhanging bracket margins, and port/ground offsets in <2s before escalating
to an LLM repair pass.
"""
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contract as ct  # noqa: E402


def _defect_score(bv: dict, cres: dict | None):
    chk_ok = bool((bv.get("check") or {}).get("passed")) and bool((bv.get("sweep") or {}).get("passed"))
    aud = bv.get("audit") or {}
    det_extra = max(0, int(aud.get("components") or 1) - int(aud.get("allowed") or 1))
    dang = len(aud.get("dangling") or [])
    hard = len((cres or {}).get("hard") or [])
    soft = len((cres or {}).get("soft") or [])
    return (0 if chk_ok else 1, hard, det_extra, dang, soft)


def _strip_stock(src_id: str) -> str:
    return re.sub(r"-stock$", "", src_id or "")


def _numeric_Literals_in_call(src_text: str, call_node: ast.Call):
    """Yield `(start_offset, end_offset, current_float)` for numeric constants inside `call_node`."""
    line_starts = [0]
    for ln in src_text.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(ln))

    def span_of(nd):
        return line_starts[nd.lineno - 1] + nd.col_offset, line_starts[nd.end_lineno - 1] + nd.end_col_offset

    out = []
    for sub in ast.walk(call_node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, (int, float)) and not isinstance(sub.value, bool):
            s, e = span_of(sub)
            out.append((s, e, float(sub.value)))
        elif isinstance(sub, ast.UnaryOp) and isinstance(sub.op, ast.USub) and isinstance(sub.operand, ast.Constant) and isinstance(sub.operand.value, (int, float)):
            s, e = span_of(sub)
            out.append((s, e, -float(sub.operand.value)))
    # Filter out nested constants that are children of a UnaryOp already recorded
    filtered = []
    for s, e, val in out:
        if not any(s >= ps and e <= pe and (s, e) != (ps, pe) for ps, pe, _ in out):
            filtered.append((s, e, val))
    return filtered


def _propose_patches(src_text: str, bv: dict, cres: dict | None):
    """Generate `(description, patched_source)` candidates from `audit` and `contract` diagnostics."""
    try:
        tree = ast.parse(src_text)
    except SyntaxError:
        return []

    aud = bv.get("audit") or {}
    detached = aud.get("detached") or []
    dangling = aud.get("dangling") or []
    misfits = (cres or {}).get("misfits") or []

    calls_by_id = []
    for nd in ast.walk(tree):
        if isinstance(nd, ast.Call):
            str_args = [a.value for a in nd.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            calls_by_id.append((nd, str_args))

    proposals = []

    # 0. Detail reach overflow (`Detail N is further than `reach` (0.250000 m) from the surface`)
    err_str = str(bv.get("error") or (bv.get("check") or {}).get("error") or "")
    m_reach = re.search(r":\s*([a-zA-Z0-9_.\-]+):\s*Detail\s+\d+\s+is\s+further\s+than\s+`?reach`?", err_str)
    if m_reach:
        bad_id = m_reach.group(1)
        for call_nd, sargs in calls_by_id:
            if any(sa == bad_id or (len(sa) >= 5 and bad_id.startswith(sa)) for sa in sargs):
                # Propose bumping or adding `reach=1.2` keyword argument
                line_starts = [0]
                for ln in src_text.splitlines(keepends=True):
                    line_starts.append(line_starts[-1] + len(ln))
                cs = line_starts[call_nd.lineno - 1] + call_nd.col_offset
                ce = line_starts[call_nd.end_lineno - 1] + call_nd.end_col_offset
                call_txt = src_text[cs:ce]
                if "reach=" in call_txt:
                    patched_call = re.sub(r"reach\s*=\s*[^,)]+", "reach=1.2", call_txt)
                else:
                    patched_call = call_txt[:-1] + ", reach=1.2)"
                proposals.append((f"widen detail reach on `{bad_id}` -> 1.2m", src_text[:cs] + patched_call + src_text[ce:]))

    # 1. Exact vector translation fixes from `audit.detached[*].deltaToMain`
    for det in detached:
        delta = det.get("deltaToMain")
        srcs = [_strip_stock(s) for s in (det.get("sources") or []) if s]
        if not delta or len(delta) != 3 or not srcs:
            continue
        for call_nd, sargs in calls_by_id:
            if any(any(target and (target == sa or sa.startswith(target[:10])) for target in srcs) for sa in sargs):
                lists_3 = []
                for kw in call_nd.keywords:
                    if kw.arg in ("at", "t", "translation") and isinstance(kw.value, (ast.List, ast.Tuple)) and len(kw.value.elts) == 3:
                        lists_3.append(kw.value)
                # Positional `g.box(id_, size, at)` -> args[2] is `at`!
                if len(call_nd.args) >= 3 and isinstance(call_nd.args[2], (ast.List, ast.Tuple)) and len(call_nd.args[2].elts) == 3:
                    lists_3.append(call_nd.args[2])
                for lnode in lists_3:
                    for axis_i, dval in enumerate(delta):
                        if abs(dval) < 1e-4:
                            continue
                        elt_lits = _numeric_Literals_in_call(src_text, lnode.elts[axis_i])
                        for s, e, cur in elt_lits:
                            step = dval + (0.001 if dval > 0 else -0.001)
                            for sign in (1.0, -1.0):
                                new_val = round(cur + sign * step, 5)
                                patched = src_text[:s] + repr(new_val) + src_text[e:]
                                proposals.append((f"seat detached {srcs[0]} axis {axis_i} ({cur} -> {new_val})", patched))

    # 2. Dangling overhanging support inward step search
    for dg in dangling:
        target = _strip_stock(dg.get("source") or "")
        if not target:
            continue
        for call_nd, sargs in calls_by_id:
            if any(target == sa or sa.startswith(target[:8]) for sa in sargs):
                for s, e, cur in _numeric_Literals_in_call(src_text, call_nd):
                    if 0.02 <= abs(cur) <= 6.0:
                        for shrink in (0.85, 0.72):
                            new_val = round(cur * shrink, 4)
                            patched = src_text[:s] + repr(new_val) + src_text[e:]
                            proposals.append((f"pull in dangling `{target}` literal {cur} -> {new_val}", patched))

    # 3. Port origin misfits (`{"base": port([x, y, z], ...)}` or `g.port("base", ..., [x, y, z])`)
    port_nodes = {}
    for nd in ast.walk(tree):
        if isinstance(nd, ast.Dict):
            for k_nd, v_nd in zip(nd.keys, nd.values):
                if isinstance(k_nd, ast.Constant) and isinstance(k_nd.value, str) and isinstance(v_nd, ast.Call):
                    if v_nd.args and isinstance(v_nd.args[0], (ast.List, ast.Tuple)) and len(v_nd.args[0].elts) == 3:
                        port_nodes[k_nd.value] = v_nd.args[0]
    for mf in misfits:
        m_port = re.search(r"port `([^`]+)` at \[([^\]]+)\]; contract: \[([^\]]+)\]", mf)
        if m_port:
            pname = m_port.group(1)
            got = [float(x) for x in m_port.group(2).split(",")]
            want = [None if x.strip() == "None" else float(x) for x in m_port.group(3).split(",")]
            lnodes = ([port_nodes[pname]] if pname in port_nodes else []) + [
                call_nd.args[2] for call_nd, sargs in calls_by_id
                if pname in sargs and len(call_nd.args) >= 3 and isinstance(call_nd.args[2], (ast.List, ast.Tuple)) and len(call_nd.args[2].elts) == 3
            ]
            for lnode in lnodes:
                for idx in range(3):
                    if want[idx] is not None and abs(got[idx] - want[idx]) > 0.015:
                        diff = want[idx] - got[idx]
                        for s, e, cur in _numeric_Literals_in_call(src_text, lnode.elts[idx]):
                            new_val = round(cur + diff, 4)
                            patched = src_text[:s] + repr(new_val) + src_text[e:]
                            proposals.append((f"align port `{pname}` axis {idx} by {diff:+.4f}m", patched))

    return proposals


def try_local_repair(box_dir: Path, module_name: str, contract_spec: dict | None, pgm_bin: str | Path, max_evals: int = 16) -> dict:
    """Attempt $0.00 AST + batch-verify local numeric offset repairs in `box_dir`."""
    builder = Path(box_dir) / "recipes" / "build_bench_kit.py"
    doc = Path(box_dir) / "recipes" / "site" / "bench" / f"{module_name}.json"
    if not builder.exists():
        return {"patched": False, "reason": "no_builder"}

    def build_and_eval(code_text: str):
        builder.write_text(code_text)
        r = subprocess.run(["python3", "recipes/build_bench_kit.py"], cwd=box_dir, capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or not doc.exists():
            return None, None, (1, 999, 999, 999, 999)
        bv, cres = ct.batch_verify(contract_spec, doc, pgm_bin, check_dangling=True)
        return bv, cres, _defect_score(bv, cres)

    orig_text = builder.read_text()
    bv0, cres0, score0 = build_and_eval(orig_text)
    if score0 == (0, 0, 0, 0, 0):
        return {"patched": False, "reason": "already_clean", "before_score": score0, "after_score": score0, "evals": 1}

    cur_text, cur_bv, cur_cres, cur_score = orig_text, bv0, cres0, score0
    applied = []
    evals = 1

    for _round in range(5):
        if cur_score == (0, 0, 0, 0, 0) or evals >= max_evals:
            break
        improved = False
        cur_err = str((cur_bv or {}).get("error") or ((cur_bv or {}).get("check") or {}).get("error") or "")
        for desc, cand_text in _propose_patches(cur_text, cur_bv or {}, cur_cres):
            if evals >= max_evals:
                break
            evals += 1
            bvc, cresc, sc = build_and_eval(cand_text)
            cand_err = str((bvc or {}).get("error") or ((bvc or {}).get("check") or {}).get("error") or "")
            progressed_reach = (
                "reach" in desc and sc[0] == 1 and cand_err != cur_err and "reach" in cand_err
            )
            if (sc[0] == 0 and sc < cur_score) or progressed_reach:
                cur_text, cur_bv, cur_cres, cur_score = cand_text, bvc, cresc, sc
                applied.append(desc)
                improved = True
                break
        if not improved:
            break

    builder.write_text(cur_text)
    if applied:
        subprocess.run(["python3", "recipes/build_bench_kit.py"], cwd=box_dir, capture_output=True, text=True, timeout=60)
    return {
        "patched": bool(applied),
        "applied": applied,
        "before_score": score0,
        "after_score": cur_score,
        "evals": evals,
        "cost_usd": 0.0,
    }


def _run_mj(mj_script: Path, doc: Path, pgm_bin: str | Path, fast: bool = True) -> dict | None:
    import os
    cmd = ["nice", "-n", "19", "uv", "run", "--offline", "--with", "mujoco", "--with", "numpy", "--with", "coacd", "--with", "pillow",
           "python3", str(mj_script), str(doc)] + (["--fast"] if fast else [])
    try:
        r = subprocess.run(cmd, env={**os.environ, "PGBENCH_PGM": str(pgm_bin), "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"},
                           capture_output=True, text=True, timeout=90)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except Exception:
        pass
    return None


def _propose_physics_patches(src_text: str, ph: dict):
    try:
        tree = ast.parse(src_text)
    except SyntaxError:
        return []
    proposals = []
    spin = ph.get("spinTest") or {}
    isects = ph.get("topIntersections") or []

    # 1. Bore clearance nudge when rotor is jammed or pin/shaft/bore clips appear
    bore_hit = bool(spin.get("jammed")) or any(
        re.search(r"(bore|bush|pin|shaft|axle|stem|spindle|nut|collar)", (x.get("partA", "") + " " + x.get("partB", "")), re.I)
        for x in isects
    )
    if bore_hit:
        for nd in ast.walk(tree):
            if isinstance(nd, ast.Assign):
                names = [t.id for t in nd.targets if isinstance(t, ast.Name)]
                if any("bore" in nm.lower() for nm in names):
                    for s, e, cur in _numeric_Literals_in_call(src_text, nd.value):
                        if 0.0 < cur < 0.25:
                            nv = round(cur + 0.0018, 5)
                            proposals.append((f"widen bore constant `{names[0]}` ({cur} -> {nv})", src_text[:s] + repr(nv) + src_text[e:]))
            elif isinstance(nd, ast.Call):
                for kw in nd.keywords:
                    if kw.arg and "bore" in kw.arg.lower():
                        for s, e, cur in _numeric_Literals_in_call(src_text, kw.value):
                            if 0.0 < cur < 0.25:
                                nv = round(cur + 0.0018, 5)
                                proposals.append((f"widen `{kw.arg}` ({cur} -> {nv})", src_text[:s] + repr(nv) + src_text[e:]))

    # 2. Flush-butt displacement nudges for buried/clipping parts in `topIntersections`
    calls_by_id = []
    for nd in ast.walk(tree):
        if isinstance(nd, ast.Call):
            sargs = [a.value for a in nd.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            calls_by_id.append((nd, sargs))

    for x in isects[:4]:
        depth_m = float(x.get("exactDepthMM") or 0.0) / 1000.0
        if depth_m <= 0.0005 or depth_m > 0.12:
            continue
        step = round(depth_m + 0.0005, 5)
        for raw_nm in (x.get("partA", ""), x.get("partB", "")):
            stem = re.sub(r"(\.\d+|-raw|-stock|-sds|-sm|-c|-b|-r)$", "", raw_nm)
            if len(stem) < 3:
                continue
            for call_nd, sargs in calls_by_id:
                if any(sa == stem or stem.startswith(sa) or sa.startswith(stem[:8]) for sa in sargs if len(sa) >= 3):
                    lists_3 = [kw.value for kw in call_nd.keywords
                               if kw.arg in ("at", "t", "translation") and isinstance(kw.value, (ast.List, ast.Tuple)) and len(kw.value.elts) == 3]
                    if len(call_nd.args) >= 3 and isinstance(call_nd.args[2], (ast.List, ast.Tuple)) and len(call_nd.args[2].elts) == 3:
                        lists_3.append(call_nd.args[2])
                    for lnode in lists_3:
                        for ax in range(3):
                            for s, e, cur in _numeric_Literals_in_call(src_text, lnode.elts[ax]):
                                for sgn in (1.0, -1.0):
                                    nv = round(cur + sgn * step, 5)
                                    proposals.append((f"flush-butt `{stem}` axis {ax} ({cur} -> {nv})", src_text[:s] + repr(nv) + src_text[e:]))
    return proposals


def try_physics_local_repair(box_dir: Path, module_name: str, contract_spec: dict | None,
                             pgm_bin: str | Path, max_evals: int = 8) -> dict:
    """Attempt $0.00 MuJoCo-guided AST bore/flush-butt offset repairs without degrading pgm/contract validity."""
    mj_script = Path(__file__).resolve().parent / "mujoco_audit.py"
    builder = Path(box_dir) / "recipes" / "build_bench_kit.py"
    doc = Path(box_dir) / "recipes" / "site" / "bench" / f"{module_name}.json"
    if not mj_script.exists() or not builder.exists() or not doc.exists():
        return {"patched": False, "reason": "unavailable"}

    orig_text = builder.read_text()
    bv0, cres0 = ct.batch_verify(contract_spec, doc, pgm_bin, check_dangling=True)
    dscore0 = _defect_score(bv0, cres0)
    ph0_full = _run_mj(mj_script, doc, pgm_bin, fast=False)
    if not ph0_full or float(ph0_full.get("physicsScore") or 0.0) >= 100.0:
        return {"patched": False, "reason": "physics_already_100",
                "before_physics": (ph0_full or {}).get("physicsScore")}

    def mj_tuple(ph: dict):
        clips = int(ph.get("buriedOrDuplicate") or 0) + int(ph.get("crossMaterialClips") or 0) + int(ph.get("sameMaterialClips") or 0)
        jam = 1 if (ph.get("spinTest") or {}).get("jammed") else 0
        return (jam, clips, -float(ph.get("physicsScore") or 0.0))

    cur_text, cur_ph = orig_text, ph0_full
    cur_t = mj_tuple(ph0_full)
    applied, evals = [], 0

    for desc, cand_text in _propose_physics_patches(cur_text, cur_ph):
        if evals >= max_evals:
            break
        evals += 1
        builder.write_text(cand_text)
        r = subprocess.run(["python3", "recipes/build_bench_kit.py"], cwd=box_dir, capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or not doc.exists():
            continue
        bvc, cresc = ct.batch_verify(contract_spec, doc, pgm_bin, check_dangling=True)
        if _defect_score(bvc, cresc) > dscore0:
            continue
        phc = _run_mj(mj_script, doc, pgm_bin, fast=True)
        if phc and mj_tuple(phc) < cur_t:
            phc_full = _run_mj(mj_script, doc, pgm_bin, fast=False)
            if phc_full and mj_tuple(phc_full) < cur_t:
                cur_text, cur_ph, cur_t = cand_text, phc_full, mj_tuple(phc_full)
                applied.append(desc)
                if float(phc_full.get("physicsScore") or 0.0) >= 100.0:
                    break

    builder.write_text(cur_text)
    subprocess.run(["python3", "recipes/build_bench_kit.py"], cwd=box_dir, capture_output=True, text=True, timeout=60)
    return {
        "patched": bool(applied),
        "applied": applied,
        "before_physics": ph0_full.get("physicsScore"),
        "after_physics": cur_ph.get("physicsScore"),
        "evals": evals,
        "cost_usd": 0.0,
    }
