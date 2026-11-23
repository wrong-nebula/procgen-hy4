#!/usr/bin/env python3
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
