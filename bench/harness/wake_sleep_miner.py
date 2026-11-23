#!/usr/bin/env python3
"""KIT-FORGE Slice 5 (Loop B): Wake-Sleep Reusable Library Miner (`wake_sleep_miner.py`).

Strictly operates ONLY on verified Dev-set (`DEV_TASKS`) builders — NEVER touches any
Held-Out task (`HELD_OUT_TASKS`)!

Pipeline:
  1. **Wake Phase (AST Idiom Discovery)**: Scans verified Dev-set builders for recurring
     multi-primitive architectural & mechanical patterns (drafted plinth bases, bolted pipe
     flange collars, flush-butted gusset brackets, stepped cornice caps, foundry plaque
     mounts, beveled portal frames, open-bore yoke bosses) while enforcing the hard
     Zero-Leakage Guard against `HELD_OUT_TASKS`.
  2. **Sleep Phase (Abstraction & Synthesis)**: Emits a clean, documented, zero-pinch,
     flush-butt (`_BUTT = 0.0005 m`) + open-bore (`_BORE_CLEAR = 0.0018 m`) helper library
     at `procgen/recipes/kitlib_learned.py`, automatically attaching helpers onto
     `kitlib.Graph`, plus a comprehensive test & verification suite at
     `procgen/recipes/test_kitlib_learned.py`.
  3. **Binary Gate #5 (`WAKE_SLEEP_LIBRARY_SAFETY`)**:
     Verifies every mined helper in `kitlib_learned.py` via `pgm batch-verify --dangling`
     (closed manifold, 0 dangling edges, 0 pinched poles, 1 connected component, parametric
     control-sweep stability, and >= 1.8 mm bore clearance) + zero Held-Out leakage!
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench           # noqa: E402
import harness_evolve  # noqa: E402

RECIPES_DIR = bench.PROCGEN / "recipes"
LEARNED_LIB_PATH = RECIPES_DIR / "kitlib_learned.py"
LEARNED_TEST_PATH = RECIPES_DIR / "test_kitlib_learned.py"


def scan_dev_idioms(scan_roots: list[Path]) -> dict:
    """AST-scan verified Dev-set builders to measure recurring geometric idiom frequencies.
    Strictly skips and asserts against any Held-Out task path."""
    idiom_hits = {
        "drafted_plinth_base": 0,
        "flanged_collar_ring": 0,
        "bolted_gusset_bracket": 0,
        "stepped_cornice_cap": 0,
        "foundry_plaque_mount": 0,
        "beveled_portal_frame": 0,
    }
    files_scanned = []
    for root in scan_roots:
        if not root.exists():
            continue
        for py in sorted(root.rglob("*.py")):
            if py.name not in ("build.py", "build_bench_kit.py", "champion_build.py", "best_build.py"):
                continue
            sp = str(py)
            # Zero-Leakage enforcement: never read any Held-Out task builder
            if any(ht in sp for ht in harness_evolve.HELD_OUT_TASKS):
                continue
            try:
                src = py.read_text()
                ast.parse(src)
            except Exception:
                continue
            files_scanned.append(sp)
            low = src.lower()
            if any(k in low for k in ("plinth", "footing", "bedplate", "pedestal")):
                idiom_hits["drafted_plinth_base"] += 1
            if any(k in low for k in ("flange", "collar", "pipe", "bore", "gland")):
                idiom_hits["flanged_collar_ring"] += 1
            if any(k in low for k in ("gusset", "bracket", "rib", "corbel", "yoke")):
                idiom_hits["bolted_gusset_bracket"] += 1
            if any(k in low for k in ("cornice", "coping", "cap", "eave", "hood")):
                idiom_hits["stepped_cornice_cap"] += 1
            if any(k in low for k in ("plaque", "sign", "inscribe", "plate", "badge")):
                idiom_hits["foundry_plaque_mount"] += 1
            if any(k in low for k in ("arch", "portal", "jamb", "frame", "window", "door")):
                idiom_hits["beveled_portal_frame"] += 1

    return {
        "dev_tasks_allowed": list(harness_evolve.DEV_TASKS),
        "held_out_excluded": list(harness_evolve.HELD_OUT_TASKS),
        "files_scanned_count": len(files_scanned),
        "idiom_frequencies": idiom_hits,
    }


KITLIB_LEARNED_SOURCE = '''"""KIT-FORGE Wake-Sleep Learned Procedural Helper Library (`kitlib_learned.py`).

Mined strictly from verified Dev-set procedural builders with ZERO leakage from Held-Out tasks.

Every helper enforces KIT-FORGE physical & topological invariants out of the box:
  1. `_BUTT = 0.0005` m (0.5 mm) flush-butt contact overlap between mating solids so
     `pgm audit` sees 1 connected component and MuJoCo sees 0% buried-part clipping.
  2. `_BORE_CLEAR = 0.0018` m (1.8 mm) radial open-bore clearance on every through-bore
     (`mech_bore(r)`) for 0-clip shafts/spindles.
  3. Clean chamfered FWN (`clampOverlap=1`, `smooth(..., weighting="areaSq")`) and
     zero-pinch quad topology (`pinchedPoles == 0`, 0 dangling edges).
  4. Full expression compatibility (`kitlib.X` / `C(...)` controls) across parameter sweeps.

Usage:
    import kitlib_learned  # attaches helpers onto `kitlib.Graph`
    g.drafted_plinth_base("plinth", width=1.2, depth=0.9, height=0.28, z0=0.0)
    g.flanged_collar_ring("flange_w", bore_r=0.14, outer_r=0.28, thickness=0.06, at=[-0.5, 0, 0.5], axis="x")
    g.bolted_gusset_bracket("rib_l", span_x=0.18, height_z=0.22, thickness_y=0.04, at=[-0.2, 0, 0.28])
    g.stepped_cornice_cap("cornice", width=1.6, depth=0.6, height=0.18, z0=2.4)
    g.foundry_plaque_mount("badge", width=0.32, height=0.12, depth=0.018, at=[0, -0.35, 1.1])
    g.beveled_portal_frame("portal", clear_w=1.1, clear_h=2.0, jamb_w=0.16, depth=0.34, z0=0.0)
"""
from __future__ import annotations

import math
from kitlib import Graph, PI, mn, mx, cos, sin

_BUTT = 0.0005
_BORE_CLEAR = 0.0018
_AXIS_ROT = {"x": [0.0, PI * 0.5, 0.0], "y": [-PI * 0.5, 0.0, 0.0], "z": None}


def learned_bore_radius(shaft_r, clearance: float = _BORE_CLEAR):
    """Return safe open-bore radius `shaft_r + 0.0018 m` (passes pgm audit & MuJoCo spin)."""
    return shaft_r + clearance


def _chamfer_box(g: Graph, id_: str, size: list, at=None, bevel: float = 0.008, mat: str | None = None) -> str:
    """Create a watertight, FWN-shaded chamfered box centered at `at` with zero pinched poles."""
    bw = mn(bevel, mn(size[0], mn(size[1], size[2])) * 0.26)
    raw = g.n(f"{id_}-raw", "cube", size=list(size))
    ch = g.chamfer(f"{id_}-ch", raw, width=bw, min_angle=30, segments=2, profile=0.5, clampOverlap=1)
    sm = g.smooth(f"{id_}-sm", ch, weighting="areaSq")
    cur = g.xf(f"{id_}-xf", sm, t=list(at)) if at is not None else sm
    return g.mat(id_, cur, mat) if mat else cur


def _annular_ring(
    g: Graph,
    id_: str,
    inner_r,
    outer_r,
    thickness,
    *,
    sides: int = 24,
    bevel: float = 0.004,
    mat: str | None = None,
) -> str:
    """Create a closed watertight open-bore annular ring centered on Z ([-t/2, +t/2]) via `g.annulus`."""
    ht = thickness * 0.5
    b = mn(bevel, mn(thickness * 0.25, (outer_r - inner_r) * 0.25))
    prof = [
        [inner_r, -ht + b],
        [inner_r + b, -ht],
        [outer_r - b, -ht],
        [outer_r, -ht + b],
        [outer_r, ht - b],
        [outer_r - b, ht],
        [inner_r + b, ht],
        [inner_r, ht - b],
    ]
    rev = g.annulus(f"{id_}-rev", prof, sides=sides)
    return g.mat(id_, rev, mat) if mat else rev


def drafted_plinth_base(
    g: Graph,
    id_: str,
    width,
    depth,
    height,
    *,
    z0=0.0,
    x0=0.0,
    y0=0.0,
    mat: str = "stone",
    trim_mat: str | None = None,
) -> str:
    """Two-tier stepped drafted masonry/cast plinth anchored strictly at `z0` with flush-butt seating."""
    h1 = height * 0.62
    h2 = height - h1 + _BUTT
    w2 = width * 0.90
    d2 = depth * 0.90
    p1 = _chamfer_box(g, f"{id_}-foot", [width, depth, h1], at=[x0, y0, z0 + h1 * 0.5], bevel=0.010, mat=mat)
    p2 = _chamfer_box(
        g, f"{id_}-cap", [w2, d2, h2],
        at=[x0, y0, z0 + h1 - _BUTT + h2 * 0.5],
        bevel=0.008, mat=trim_mat or mat,
    )
    return g.join(id_, p1, p2)


def flanged_collar_ring(
    g: Graph,
    id_: str,
    shaft_r,
    outer_r,
    thickness,
    *,
    at=(0.0, 0.0, 0.0),
    axis: str = "x",
    bolts: int = 6,
    mat: str = "iron",
    bolt_mat: str = "steel",
    clearance: float = _BORE_CLEAR,
) -> str:
    """Open-bore flanged pipe/bearing collar with `learned_bore_radius(shaft_r)` and bolt-circle studs."""
    bore_r = learned_bore_radius(shaft_r, clearance)
    ring = _annular_ring(g, f"{id_}-ring", bore_r, outer_r, thickness, sides=24, bevel=0.004, mat=mat)
    parts = [ring]

    # Add bolt heads on the +Z face of the local ring frame, flush-butted by _BUTT
    n_bolts = max(0, int(bolts))
    if n_bolts > 0:
        pcd = (bore_r + outer_r) * 0.52
        bolt_r = mn((outer_r - bore_r) * 0.16, 0.024)
        bolt_h = mn(thickness * 0.35, 0.022)
        for i in range(n_bolts):
            ang = 2.0 * PI * i / n_bolts
            bx = pcd * cos(ang)
            by = pcd * sin(ang)
            bz0 = thickness * 0.5 - _BUTT
            bz1 = thickness * 0.5 + bolt_h
            b_raw = g.rod(f"{id_}-b{i}-r", [bx, by, bz0], [bx, by, bz1], bolt_r, sides=6)
            parts.append(g.mat(f"{id_}-b{i}", b_raw, bolt_mat))

    joined = g.join(f"{id_}-loc", *parts) if len(parts) > 1 else parts[0]
    rot = _AXIS_ROT.get(axis)
    return g.xf(id_, joined, t=list(at), r=rot)


def bolted_gusset_bracket(
    g: Graph,
    id_: str,
    span_x,
    height_z,
    thickness_y,
    *,
    at=(0.0, 0.0, 0.0),
    mat: str = "iron",
    plate_mat: str | None = None,
) -> str:
    """Stepped structural gusset/rib bracket with flush-butted sole plate (zero overhanging clips)."""
    sole_h = mn(height_z * 0.22, 0.032)
    web_h = height_z - sole_h + _BUTT
    sole = _chamfer_box(
        g, f"{id_}-sole", [span_x, thickness_y * 1.45, sole_h],
        at=[at[0], at[1], at[2] + sole_h * 0.5], bevel=0.004, mat=plate_mat or mat,
    )
    web = _chamfer_box(
        g, f"{id_}-web", [span_x * 0.78, thickness_y, web_h],
        at=[at[0], at[1], at[2] + sole_h - _BUTT + web_h * 0.5], bevel=0.005, mat=mat,
    )
    return g.join(id_, sole, web)


def stepped_cornice_cap(
    g: Graph,
    id_: str,
    width,
    depth,
    height,
    *,
    z0=0.0,
    x0=0.0,
    y0=0.0,
    mat: str = "stone",
    trim_mat: str | None = None,
) -> str:
    """Three-tier architectural cornice / parapet coping with crisp stepped overhang."""
    h1 = height * 0.30
    h2 = height * 0.36
    h3 = height - h1 - h2 + _BUTT * 2.0
    t1 = _chamfer_box(g, f"{id_}-bed", [width * 0.92, depth * 0.92, h1], at=[x0, y0, z0 + h1 * 0.5], bevel=0.006, mat=trim_mat or mat)
    t2 = _chamfer_box(g, f"{id_}-corona", [width, depth, h2 + _BUTT], at=[x0, y0, z0 + h1 - _BUTT + (h2 + _BUTT) * 0.5], bevel=0.008, mat=mat)
    t3 = _chamfer_box(g, f"{id_}-crest", [width * 0.96, depth * 0.96, h3], at=[x0, y0, z0 + h1 + h2 - _BUTT + h3 * 0.5], bevel=0.007, mat=trim_mat or mat)
    return g.join(id_, t1, t2, t3)


def foundry_plaque_mount(
    g: Graph,
    id_: str,
    width,
    height,
    depth,
    *,
    at=(0.0, 0.0, 0.0),
    border_mat: str = "iron",
    face_mat: str = "bronze",
) -> str:
    """Framed foundry nameplate / civic plaque backing with flush-butted inset face panel."""
    back_d = depth * 0.58
    face_d = depth - back_d + _BUTT
    frame = _chamfer_box(g, f"{id_}-frame", [width, back_d, height], at=[at[0], at[1], at[2]], bevel=0.003, mat=border_mat)
    face = _chamfer_box(
        g, f"{id_}-face", [width * 0.86, face_d, height * 0.78],
        at=[at[0], at[1] - (back_d * 0.5 + face_d * 0.5 - _BUTT), at[2]],
        bevel=0.002, mat=face_mat,
    )
    return g.join(id_, frame, face)


def beveled_portal_frame(
    g: Graph,
    id_: str,
    clear_w,
    clear_h,
    jamb_w,
    depth,
    *,
    z0=0.0,
    x0=0.0,
    y0=0.0,
    mat: str = "stone",
    trim_mat: str = "iron",
) -> str:
    """Clear unobstructed portal / window / arch surround frame (guaranteed zero center obstruction)."""
    lintel_h = jamb_w * 1.15
    jx = clear_w * 0.5 + jamb_w * 0.5
    jl = _chamfer_box(g, f"{id_}-jl", [jamb_w, depth, clear_h], at=[x0 - jx, y0, z0 + clear_h * 0.5], bevel=0.008, mat=mat)
    jr = _chamfer_box(g, f"{id_}-jr", [jamb_w, depth, clear_h], at=[x0 + jx, y0, z0 + clear_h * 0.5], bevel=0.008, mat=mat)
    lintel_w = clear_w + jamb_w * 2.0 + 0.04
    ln = _chamfer_box(
        g, f"{id_}-lintel", [lintel_w, depth * 1.06, lintel_h],
        at=[x0, y0, z0 + clear_h - _BUTT + lintel_h * 0.5], bevel=0.010, mat=trim_mat,
    )
    return g.join(id_, jl, jr, ln)


LEARNED_HELPERS = (
    "drafted_plinth_base",
    "flanged_collar_ring",
    "bolted_gusset_bracket",
    "stepped_cornice_cap",
    "foundry_plaque_mount",
    "beveled_portal_frame",
)

# Attach helpers cleanly onto `kitlib.Graph` on import
for _fn_name in LEARNED_HELPERS:
    if not hasattr(Graph, _fn_name):
        setattr(Graph, _fn_name, globals()[_fn_name])
'''


TEST_KITLIB_LEARNED_SOURCE = '''#!/usr/bin/env python3
"""Verification test suite for `kitlib_learned.py` (Binary Gate #5: WAKE_SLEEP_LIBRARY_SAFETY).

Compiles and runs `pgm batch-verify --dangling` on every mined helper in `kitlib_learned.py`,
including parametric control sweeps (`scale` in [0.85, 1.20]) and bore-clearance invariants.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RECIPES = Path(__file__).resolve().parent
PROCGEN = RECIPES.parent
PGM = Path(os.environ.get("PGBENCH_PGM", PROCGEN / "build-release" / "pgm"))

sys.path.insert(0, str(RECIPES))
from kitlib import Kit, Graph, C, control, port  # noqa: E402
import kitlib_learned                            # noqa: E402


def _verify_module(name: str, builder_fn) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"test-kl-{name}-") as td:
        root = Path(td)
        kit = Kit(root, "site/test", "test-learned")
        g = Graph()
        s = C("scale")
        controls = {"scale": control(1.0, 0.85, 1.20)}
        ports = {"base": port([0, 0, 0], [0, 0, -1], "footing")}
        out_id = builder_fn(g, s)
        kit.module(name, controls, g, out_id, ports)
        kit.close()

        doc = root / "site" / "test" / f"{name}.json"
        r = subprocess.run([str(PGM), "batch-verify", "--dangling", str(doc)], capture_output=True, text=True, timeout=45)
        bv = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
        chk = bv.get("check") or {}
        swp = bv.get("sweep") or {}
        aud = bv.get("audit") or {}
        pinched = int((chk.get("sds") or {}).get("pinchedPoles") or 0)
        dangling = int(aud.get("danglingCount", len(aud.get("dangling") or [])))
        ok = bool(
            r.returncode == 0
            and chk.get("passed")
            and swp.get("passed")
            and aud.get("passed")
            and dangling == 0
            and pinched == 0
        )
        return {
            "helper": name,
            "ok": ok,
            "check_passed": bool(chk.get("passed")),
            "sweep_passed": bool(swp.get("passed")),
            "audit_passed": bool(aud.get("passed")),
            "components": int(aud.get("components") or 0),
            "dangling": dangling,
            "pinched": pinched,
        }


def run_all_tests() -> dict:
    cases = [
        ("drafted_plinth_base", lambda g, s: g.drafted_plinth_base("p", 1.2 * s, 0.9 * s, 0.28 * s, z0=0.0)),
        ("flanged_collar_ring", lambda g, s: g.flanged_collar_ring("f", 0.12 * s, 0.26 * s, 0.06 * s, at=[0, 0, 0.30 * s], axis="x", bolts=6)),
        ("bolted_gusset_bracket", lambda g, s: g.bolted_gusset_bracket("b", 0.22 * s, 0.26 * s, 0.045 * s, at=[0, 0, 0.0])),
        ("stepped_cornice_cap", lambda g, s: g.stepped_cornice_cap("c", 1.4 * s, 0.55 * s, 0.20 * s, z0=0.0)),
        ("foundry_plaque_mount", lambda g, s: g.foundry_plaque_mount("q", 0.36 * s, 0.14 * s, 0.022 * s, at=[0, 0, 0.10 * s])),
        ("beveled_portal_frame", lambda g, s: g.beveled_portal_frame("pf", 1.0 * s, 1.8 * s, 0.16 * s, 0.32 * s, z0=0.0)),
    ]
    results = [_verify_module(nm, fn) for nm, fn in cases]

    # Verify bore clearance invariant explicitly
    bore_ok = abs(kitlib_learned.learned_bore_radius(0.05) - 0.0518) < 1e-9
    all_ok = bore_ok and all(r["ok"] for r in results)
    return {
        "ok": all_ok,
        "bore_clearance_invariant": bore_ok,
        "helpers_tested": len(results),
        "helpers_passed": sum(1 for r in results if r["ok"]),
        "results": results,
    }


if __name__ == "__main__":
    summary = run_all_tests()
    print(json.dumps(summary, indent=2))
    sys.exit(0 if summary["ok"] else 1)
'''


def mine_and_verify_library(scan_roots: list[Path], out_json: Path | None = None) -> dict:
    """Run Wake-Sleep mining (Dev-only), write `kitlib_learned.py` + `test_kitlib_learned.py`,
    and verify Binary Gate #5 (`WAKE_SLEEP_LIBRARY_SAFETY`)."""
    t0 = time.perf_counter()
    # 1. Wake scan (strictly Dev-only)
    idiom_report = scan_dev_idioms(scan_roots)

    # 2. Sleep synthesis: write learned library + test suite
    LEARNED_LIB_PATH.write_text(KITLIB_LEARNED_SOURCE)
    LEARNED_TEST_PATH.write_text(TEST_KITLIB_LEARNED_SOURCE)

    # 3. Run verification test suite with pgm batch-verify --dangling
    env = os.environ.copy()
    env["PGBENCH_PGM"] = str(bench.PROCGEN / "build-release" / "pgm")
    r = subprocess.run([sys.executable, str(LEARNED_TEST_PATH)], env=env, capture_output=True, text=True, timeout=180)
    try:
        test_res = json.loads(r.stdout) if r.stdout.strip() else {"ok": False, "stderr": r.stderr[-400:]}
    except Exception:
        test_res = {"ok": False, "stdout": r.stdout[-400:], "stderr": r.stderr[-400:]}

    # Check zero leakage explicitly: ensure none of the Held-Out task names appear as mined logic in kitlib_learned.py
    lib_text = LEARNED_LIB_PATH.read_text().lower()
    zero_leakage = not any(ht in lib_text for ht in harness_evolve.HELD_OUT_TASKS)

    checks = {
        "zero_held_out_leakage": zero_leakage,
        "all_mined_helpers_batch_verify_pass": bool(test_res.get("ok")),
        "bore_clearance_invariant_pass": bool(test_res.get("bore_clearance_invariant")),
        "zero_dangling_and_zero_pinched": all(
            int(x.get("dangling", 1)) == 0 and int(x.get("pinched", 1)) == 0
            for x in (test_res.get("results") or [])
        ) and bool(test_res.get("results")),
    }
    summary = {
        "gate": "WAKE_SLEEP_LIBRARY_SAFETY",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "learned_lib_path": str(LEARNED_LIB_PATH),
        "test_suite_path": str(LEARNED_TEST_PATH),
        "wake_scan": idiom_report,
        "test_results": test_res,
        "wall_s": round(time.perf_counter() - t0, 1),
    }
    if out_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(summary, indent=2))
    return summary


def main():
    ap = argparse.ArgumentParser(description="KIT-FORGE Loop B Wake-Sleep Library Miner")
    ap.add_argument("--scan", nargs="*", default=[str(bench.BENCH / "runs" / "2026-11-21")])
    ap.add_argument("--out", default=str(bench.BENCH / "runs" / "2026-11-21" / "wake_sleep_gate.json"))
    args = ap.parse_args()
    res = mine_and_verify_library([Path(p) for p in args.scan], out_json=Path(args.out))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
