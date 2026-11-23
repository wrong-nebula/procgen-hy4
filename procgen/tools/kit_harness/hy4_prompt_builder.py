#!/usr/bin/env python3
"""Fix #4 + #6: Hy4-Flash-Optimized Subagent Prompt & Model Tier Selector.

Automatically selects:
  - `opencode/hy4-flash-free#high`   ONLY for kinematic/open-bore rounds (`--kinematic`)
  - `opencode/hy4-flash-free#medium` for standard architectural/detail/relief rounds (~40% fewer reasoning tokens!)

Generates a compact, zero-bloat prompt pointing to:
  1. The tiny 35-line `anchors.json` (instead of 147 KB old scripts)
  2. A single 768px JPEG quadrant reference crop (instead of 2048px PNGs)
  3. ONE fail-fast `verify_round.py --stamp ...` command (enforcing real disk write + all 5 gates!)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

PROCGEN_DIR = Path(__file__).resolve().parents[2]  # <repo>/procgen


def select_model(kinematic: bool, provider: str = "hy4") -> str:
    if provider == "neon":
        return "openrouter/google/gemini-4-neon#low"
    return "opencode/hy4-flash-free#high" if kinematic else "opencode/hy4-flash-free#medium"


def build_prompt(
    *,
    round_idx: int,
    total_rounds: int,
    target_script: str,
    doc_path: str,
    anchors_path: str,
    ref_crop_path: str,
    render_path: str,
    stamp_path: str,
    task_bullets: str,
    check_spin: bool,
    base_script: str = "",
) -> str:
    spin_flag = " --check-spin" if check_spin else ""
    read_step = (
        f"1. Read `{anchors_path}` (exact wall planes, opening bboxes & forbidden kinematic zones).\n"
        f"2. View `{ref_crop_path}` (768px target crop).\n"
        + (f"3. Copy/extend `{base_script}` -> `{target_script}` (do NOT rewrite untouched sections; use `cp` + targeted `edit`!)." if base_script else f"3. Write standalone plugin `{target_script}` exporting `apply(g, ctx, parts)`.")
    )
    return f"""You are executing **Round {round_idx} of {total_rounds}** inside `{PROCGEN_DIR}`.

### Efficiency & Verification Rules (STRICT)
- Keep edits surgical (use `cp` + targeted `edit` or standalone plugin write — never rewrite untouched geometry!).
- Use `BUTT = 0.0005` between all touching solids, `r_bore = r_shaft + 0.0018` for open-bore bearings/hubs, and `gap >= 0.004` in front of glazing.
- Run ONLY the single unified verifier command below (it runs build + `pgm check/audit/sweep` + MuJoCo + render + writes `{stamp_path}` in one call!).
- NEVER return until `{stamp_path}` exists on disk and prints `ALL GATES PASS`.

### Inputs
{read_step}

### Round {round_idx} Sculpting Focus
{task_bullets}

### Single Unified Verification Command (RUN THIS!)
```bash
cd "{PROCGEN_DIR}"
python3 tools/kit_harness/verify_round.py \\
  --script "{target_script}" \\
  --doc "{doc_path}" \\
  --render "{render_path}" \\
  --stamp "{stamp_path}"{spin_flag}
```
Inspect `{render_path}` with `read` once `ALL GATES PASS`, then return a 3-bullet summary + the exact 1-line verifier output."""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--total", type=int, default=6)
    ap.add_argument("--kinematic", action="store_true")
    ap.add_argument("--provider", default="hy4", choices=["hy4", "neon"])
    ap.add_argument("--target-script", required=True)
    ap.add_argument("--base-script", default="")
    ap.add_argument("--doc", required=True)
    ap.add_argument("--anchors", required=True)
    ap.add_argument("--ref-crop", required=True)
    ap.add_argument("--render", required=True)
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--task", required=True)
    args = ap.parse_args()

    out = {
        "model": select_model(args.kinematic, args.provider),
        "prompt": build_prompt(
            round_idx=args.round,
            total_rounds=args.total,
            target_script=args.target_script,
            base_script=args.base_script,
            doc_path=args.doc,
            anchors_path=args.anchors,
            ref_crop_path=args.ref_crop,
            render_path=args.render,
            stamp_path=args.stamp,
            task_bullets=args.task,
            check_spin=args.kinematic,
        ),
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
