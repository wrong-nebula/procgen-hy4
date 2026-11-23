#!/usr/bin/env python3
"""Step 0 'Anatomy Spec' pre-pass for model-bench (inspired by VibePrevisBench / shot-reader-8b).

Before a coding agent writes `build_bench_kit.py`, this lightweight pre-pass reads the task brief,
contract (envelope + ports + controls), and any optional reference plates/photos, and emits a
strict, mechanically validated `ANATOMY_SPEC.json`:
  - Exact coordinate datum table (plinth Z, bedplate Z, shaft axis Z, cheek spacing, bore vs shaft radii)
  - Hierarchical part tree with 3D bounding boxes, material slots, and 70/30 FWN vs Frontier SDS workflows
  - Kinematic clearance pairs (verifying bore_r - shaft_r in [1.0, 2.8] mm for BOTH pgm audit & MuJoCo!)
  - Post-SDS 0x8007 surface details checklist

Supports BOTH OpenRouter frontier/cheap models AND a self-hosted fine-tuned `kit-reader-8b` vLLM endpoint
(`--endpoint http://localhost:8000/v1`).

Usage:
  python3 harness/anatomy_spec.py --task reeving-block --out /tmp/ANATOMY_SPEC.json [--images ref1.png ...]
"""
import argparse
import base64
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]

ANATOMY_SCHEMA = """{
  "schema_version": 1,
  "module": "<module-name>",
  "envelope_m": [width_x, depth_y, height_z],
  "datums": {
    "z_base": 0.0,
    "z_plinth_top": 0.24,
    "z_bedplate_top": 0.30,
    "z_axis": 0.68,
    "r_shaft": 0.035,
    "r_bore": 0.0368,
    "axial_stack_x": "sheave_half_w < cheek_inner < cheek_outer <= boss_outer <= washer_outer <= collar_outer <= nut_outer (strictly non-overlapping intervals)"
  },
  "parts": [
    {
      "id": "plinth",
      "tier": 1,
      "role": "drafted ashlar foundation stone",
      "material": "limestone",
      "workflow": "fwn",
      "recommended_op": "kitparts.drafted_plinth",
      "bounds_m": [[-0.50, -0.75, 0.0], [0.50, 0.75, 0.24]],
      "mates_with": ["bedplate"],
      "clearance_mm": 0.0
    }
  ],
  "tier2_secondary_mechanicals": [
    {"id": "oil-cup", "role": "turned bronze lubricator atop bearing boss", "material": "bronze", "recommended_op": "kitparts.sds_cylinder"},
    {"id": "cheek-ribs", "role": "tapered cast stiffening ribs & gussets on cheek faces", "material": "patina", "recommended_op": "G.subdivide(g2_fair=1)"},
    {"id": "cotter-pin", "role": "split steel retaining cotter outside axle nut", "material": "steel", "recommended_op": "G.rod(cap='quad')"},
    {"id": "rope-lay", "role": "helical wire-rope strand lay along haul cable", "material": "steel", "recommended_op": "G.ticks / G.stroke"}
  ],
  "kinematics": [
    {
      "control": "lead",
      "moving_parts": ["sheave"],
      "axis": "X",
      "clearance_parts": ["cheek-e", "cheek-w", "bedplate"],
      "min_clearance_mm": 6.0
    }
  ],
  "details_post_sds": [
    {"target": "bedplate", "op": "G.fasteners", "desc": "washer/rivet ring applied AFTER chamfer/subdivide"},
    {"target": "cheek-e", "op": "G.text", "desc": "embossed foundry mark 'DOCK 4' applied AFTER subdivide"},
    {"target": "plinth", "op": "G.courses + G.seam", "desc": "drafted top margin & coursed ashlar joints"}
  ],
  "physics_invariants": [
    "Every shaft/stud of radius R passes through an open bore of radius R + 0.0018 m (1.8 mm: passes 3 mm pgm audit AND 0-clip MuJoCo).",
    "Never pass t=/r=/s= directly to G.n(...); always position primitives via G.xf.",
    "Rotating wheels/sheaves keep >= 5 mm axial clearance from cheeks and >= 20 mm radial clearance from bedplate.",
    "FLUSH-BUTT RULE: Seat stiffening ribs, foot flanges, gussets, bushings, and guard ties flush against parent faces (0.0-1.0 mm contact), NEVER embedded > 2 mm inside a parent solid (avoids MuJoCo buried_or_duplicate penalties).",
    "AXIAL STACK RULE: Order shaft hardware in strictly non-overlapping axial intervals (sheave -> gap -> cheek -> boss -> thrust_washer -> collar -> hex_nut -> cotter) with 0.0-1.0 mm axial seating, never overlapping.",
    "EXPLICIT MATERIALS: Assign an explicit material slot to EVERY part (never leave metal parts on default stone/unassigned)."
  ]
}"""

SYSTEM_PROMPT = f"""You are a senior Victorian mechanical engineer & Class-A 3D CAD architect writing a Step 0
Anatomy & Clearance Specification (`ANATOMY_SPEC.json`) BEFORE any Python geometry code is written.

Given a module brief, contract (envelope, ports, controls), and optional reference images:
1. Derive a mathematically self-consistent 3D coordinate datum table (Z-up, metres, ground at z=0).
2. Plan the real physical load path & part hierarchy so NO solids falsely clip or float:
   - Prefer `kitlib_mech` attached `Graph` helpers (`G.plummer_block`, `G.bushed_sheave`, `G.clevis_fork`,
     `G.stuffing_box`, `G.arched_yoke`, `G.pierced_cheek`, `mech_bore(shaft_r)`) plus `kitparts` open-bore
     hardware (`kitparts.bored_nut`, `kitparts.bored_collar`, `kitparts.pillow_block_bearing`,
     `kitparts.spoked_sheave`, `kitparts.bolted_flange`, `G.pipe`, `G.pierced_extrude`) wherever a shaft,
     pin, or stud passes through a collar, hub, nut, or cheek!
   - CRITICAL CLEARANCE RULE: Set every open bore radius to `shaft_r + 0.0018` (1.8 mm clearance) so it
     simultaneously passes `pgm audit` (<= 3.0 mm connectivity) AND MuJoCo solid collision (0.0 mm overlap)!
3. Enforce the **3-Tier Craft Budget** (do NOT produce a bare-bones "safe box" just to pass physics!):
   - **Tier 1 (Primary Load Path):** plinth, bedplate, load-bearing frame/cheeks/body, main rotor/spindle (`100/100` MuJoCo, free spin).
   - **Tier 2 (`tier2_secondary_mechanicals`, REQUIRED >= 4 items):** authentic Victorian secondary hardware seated cleanly with zero clipping — stiffening ribs/gussets, bronze grease/oil cups, packing glands, split cotter pins, bolted keeper straps/bonnets, brass foundry plaques, helical rope lay or springs.
   - **Tier 3 (70/30 FWN-vs-Frontier-SDS + Post-SDS Details):**
     * 70% crisp machined FWN (`drafted_plinth`, `filleted_box(..., mode="fwn")`, `subdivide(..., non_uniform=1)`, `bored_nut`, `bored_collar`, `bolted_flange`).
     * 30% restrained frontier Chaikin SDS (**at least 2 distinct SDS cages** with `crease_method="chaikin", limit=1, limit_normals=1, g2_fair=1, subdiv_normals=1, lod_levels=[2, 1, 0]`, plus `G.loft`, `G.bridge_loops`, `G.quad_fill_hole`, `kitparts.saddle_collar` + `G.transfer_normals` where cast curvature belongs).
     * At least 3 post-SDS `0x8007` details (`G.fasteners`, `G.rivets`, `G.seam`, `G.text`, `G.stamp`, `G.displace_details`), always applied AFTER `subdivide`/`chamfer`, across >= 4 distinct material slots.

Output ONLY a single valid JSON object matching this exact schema (no markdown fences, no commentary):
{ANATOMY_SCHEMA}
"""


def encode_image(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if ext in ("jpg", "jpeg") else "png"
    return f"data:image/{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def load_api_key() -> str:
    k = os.environ.get("PGBENCH_OPENROUTER_KEY")
    if k:
        return k.strip()
    p = Path.home() / ".config" / "pgbench" / "openrouter.key"
    if p.exists():
        return p.read_text().strip()
    raise RuntimeError("No OpenRouter key found (set PGBENCH_OPENROUTER_KEY or ~/.config/pgbench/openrouter.key)")


def validate_anatomy_spec(spec: dict, contract: dict = None) -> list:
    """Run mechanical & dimensional sanity checks on a generated ANATOMY_SPEC.json."""
    warnings = []
    if not isinstance(spec, dict):
        return ["Spec is not a JSON object"]
    env = spec.get("envelope_m")
    if not (isinstance(env, list) and len(env) == 3 and all(isinstance(v, (int, float)) and v > 0 for v in env)):
        warnings.append("Missing or invalid envelope_m [wx, dy, hz]")
    datums = spec.get("datums") or {}
    # Check shaft vs bore clearance pairs in datums!
    r_sh = datums.get("r_shaft") or datums.get("r_pin") or datums.get("r_axle")
    r_br = datums.get("r_bore") or datums.get("bore_r")
    if isinstance(r_sh, (int, float)) and isinstance(r_br, (int, float)):
        gap_mm = (r_br - r_sh) * 1000.0
        if gap_mm <= 0.2:
            warnings.append(f"Bore clearance too tight ({gap_mm:.2f} mm): will clip in MuJoCo! Set r_bore = r_shaft + 0.0018.")
        elif gap_mm > 2.9:
            warnings.append(f"Bore clearance too wide ({gap_mm:.2f} mm): may fail 3.0 mm pgm audit! Set r_bore = r_shaft + 0.0018.")
    parts = spec.get("parts") or []
    if len(parts) < 6:
        warnings.append(f"Too few planned parts ({len(parts)}); decompose into plinth, bedplate, frame, mechanism, fasteners, secondary hardware.")
    t2 = spec.get("tier2_secondary_mechanicals") or []
    if len(t2) < 4:
        warnings.append(f"Include at least 4 Tier-2 secondary mechanicals (oil cups, ribs/gussets, cotter/glands, foundry plaque, rope lay); got {len(t2)}.")
    sds_parts = [p for p in parts if "sds" in str(p.get("workflow", "")).lower() or "subdivide" in str(p.get("recommended_op", "")).lower()]
    if len(sds_parts) < 2:
        warnings.append(f"Plan at least 2 distinct Frontier SDS castings (got {len(sds_parts)}) so cast bodies/bosses/cheeks aren't bare boxes.")
    dets = spec.get("details_post_sds") or []
    if len(dets) < 2:
        warnings.append(f"Schedule at least 2 post-SDS 0x8007 details (foundry mark, rivets/fasteners, stone tooling); got {len(dets)}.")
    # Check part bounds stay reasonable and non-inverted.
    for pt in parts:
        b = pt.get("bounds_m")
        if isinstance(b, list) and len(b) == 2 and len(b[0]) == 3 and len(b[1]) == 3:
            for ax in range(3):
                if b[0][ax] >= b[1][ax]:
                    warnings.append(f"Part '{pt.get('id')}' has inverted bounds on axis {ax}")
    if contract and isinstance(contract, dict):
        c_env = contract.get("envelope")
        if c_env and isinstance(env, list) and len(env) == 3:
            for i, key in enumerate(("x", "y", "z")):
                rng = c_env.get(key)
                if isinstance(rng, list) and len(rng) == 2 and all(isinstance(v, (int, float)) for v in rng):
                    if not (rng[0] * 0.95 <= env[i] <= rng[1] * 1.05):
                        warnings.append(f"Planned envelope[{key}]={env[i]}m outside contract range {rng}")
    return warnings


def generate_anatomy_spec(brief: str, module: str, contract: dict = None, images=(),
                          model: str = None, endpoint: str = None, effort: str = "low") -> dict:
    model = model or os.environ.get("PGBENCH_ANATOMY_MODEL", "google/gemini-4-hydrogen")
    endpoint = endpoint or os.environ.get("PGBENCH_ANATOMY_ENDPOINT", "https://openrouter.ai/api/v1/chat/completions")

    user_text = f"MODULE: {module}\n\nBRIEF:\n{brief}\n"
    if contract:
        user_text += "\nCONTRACT (envelope, ports, controls):\n" + json.dumps(contract, indent=2) + "\n"
    if images:
        user_text += f"\nREFERENCE IMAGES ATTACHED: {len(images)} view(s). Match their proportions, mechanical anatomy, and structural load path.\n"

    content = [{"type": "text", "text": user_text}]
    for img in images:
        p = Path(img)
        if p.exists():
            content.append({"type": "image_url", "image_url": {"url": encode_image(p)}})

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content if len(content) > 1 else user_text},
        ],
        "max_tokens": 8000,
        "temperature": 0.2,
    }
    headers = {"Content-Type": "application/json"}
    if "openrouter.ai" in endpoint:
        headers["Authorization"] = "Bearer " + load_api_key()
        body["usage"] = {"include": True}
        if effort:
            body["reasoning"] = {"effort": effort}
    elif os.environ.get("OPENAI_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["OPENAI_API_KEY"]

    req = urllib.request.Request(endpoint, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = json.loads(resp.read())

    text = (raw["choices"][0]["message"].get("content") or "").strip()
    # Strip optional markdown code fences.
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    json_str = m.group(1) if m else text[text.find("{"):text.rfind("}") + 1]
    spec = json.loads(json_str)
    warnings = validate_anatomy_spec(spec, contract)
    cost = (raw.get("usage") or {}).get("cost")
    return {
        "spec": spec,
        "warnings": warnings,
        "model": model,
        "cost_usd": round(float(cost), 5) if cost is not None else None,
    }


def format_for_prompt(spec: dict, warnings: list = ()) -> str:
    """Format ANATOMY_SPEC.json concisely for injection at the top of a model-bench agent prompt."""
    lines = [
        "## Step 0 Anatomy & Clearance Plan (ANATOMY_SPEC.json)",
        "Follow these pre-verified datums, open-bore clearances, and 70/30 FWN-vs-SDS workflows when writing `build_bench_kit.py`:",
        "```json",
        json.dumps(spec, indent=2),
        "```",
    ]
    if warnings:
        lines.append("Sanity-check adjustments to apply while implementing:")
        for w in warnings:
            lines.append(f"- {w}")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Generate a validated Step 0 ANATOMY_SPEC.json for a model-bench task")
    ap.add_argument("--task", help="model-bench task name (e.g. reeving-block, handwheel-valve)")
    ap.add_argument("--brief", type=Path, help="Optional explicit brief markdown file")
    ap.add_argument("--module", help="Explicit module name (default: from task)")
    ap.add_argument("--images", nargs="*", default=[], help="Optional reference photos/plates")
    ap.add_argument("--model", default=None, help="Model ID (default: google/gemini-4-hydrogen)")
    ap.add_argument("--endpoint", default=None, help="OpenAI-compatible endpoint (OpenRouter or local vLLM kit-reader-8b)")
    ap.add_argument("--effort", default="low", help="Reasoning effort (low|medium|high)")
    ap.add_argument("--out", type=Path, default=None, help="Write ANATOMY_SPEC.json to this path")
    args = ap.parse_args()

    contract = None
    if args.task:
        tdir = BENCH / "tasks" / args.task
        task_json = json.loads((tdir / "task.json").read_text())
        module = args.module or task_json["module"]
        brief = (tdir / "brief.md").read_text()
        contract = task_json.get("contract")
    elif args.brief:
        brief = args.brief.read_text()
        module = args.module or args.brief.stem
    else:
        raise SystemExit("Pass --task <name> or --brief <file.md>")

    res = generate_anatomy_spec(brief, module, contract=contract, images=args.images,
                                model=args.model, endpoint=args.endpoint, effort=args.effort)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(res["spec"], indent=2) + "\n")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
