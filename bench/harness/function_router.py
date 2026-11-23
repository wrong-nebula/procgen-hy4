#!/usr/bin/env python3
"""KIT-FORGE Multimodal Code-to-Function Defect Router (`function_router.py`).

Exploits `baseten/bison-1`'s 33k vision+text context on OpenRouter in ONE System One
`type: "choice"` decision call (~$0.0006):
  - Passes `iso-beauty` + `opposite-beauty` + `brief.md` + `ANATOMY_SPEC.json` +
    the current `build_bench_kit.py` over the script's `def build_*` function names
    to obtain a calibrated probability distribution over which function causes the
    biggest visual / proportion / missing-part defect.
  - Combines with `typesafe/jev-1.14` 2D bbox -> `pgm pick` when a visible defect
    is localized on existing geometry; falls back cleanly to `bison-1` function
    routing for missing parts or global proportion flaws where `pgm pick` hits nothing.
  - Returns ONLY the top-1 routed function source + `DATUMS` so ultra-cheap models
    (`hy4-flash`, `inkling-2-mini`) perform surgical `replace_function` edits.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench          # noqa: E402
import decision_gate  # noqa: E402

DEFECT_TYPE_CRITERIA = {
    "missing_part": "A required component, sub-assembly, or architectural/mechanical feature from the brief is completely missing or severely underdeveloped.",
    "proportion_or_silhouette": "Overall scale, aspect ratio, massing, or silhouette reads boxy, awkward, or unfaithful to the brief.",
    "blocked_opening_or_clearance": "A doorway, arch, bore, window, or moving mechanism clearance is obstructed, buried, or visibly colliding.",
    "floating_or_gap": "Parts float in mid-air, overhang without wall/ground support, or leave unintended gaps/slits between walls/jambs.",
    "craft_and_detail": "Structure is present and unobstructed, but craft, bevels/SDS curvature, material contrast, or 0x8007 surface detailing/signage is plain or rough.",
}


def extract_builder_functions(src: str) -> dict[str, dict]:
    """Parse `build_bench_kit.py` via AST and return metadata + authored node IDs per `build_*` function."""
    tree = ast.parse(src)
    lines = src.splitlines()
    all_funcs: dict[str, dict] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        name = node.name
        if name in ("TUNE", "main"):
            continue
        start = node.lineno
        end = getattr(node, "end_lineno", start)
        code = "\n".join(lines[start - 1:end])
        doc = ast.get_docstring(node) or ""
        # Collect string literal first-args passed to `g.<method>("node_id", ...)` so `pgm pick` maps back
        node_ids: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                if sub.args and isinstance(sub.args[0], ast.Constant) and isinstance(sub.args[0].value, str):
                    node_ids.add(sub.args[0].value)
        all_funcs[name] = {
            "name": name,
            "lineno": start,
            "end_lineno": end,
            "doc": doc.splitlines()[0][:140] if doc else "",
            "code": code,
            "node_ids": sorted(node_ids),
        }

    # Prefer `build_*` functions excluding the top-level orchestrator `build_module` when sub-assemblies exist
    sub_builds = {k: v for k, v in all_funcs.items() if k.startswith("build_") and k != "build_module"}
    if sub_builds:
        return sub_builds
    return all_funcs


def extract_datums_source(src: str) -> str:
    """Extract the `DATUMS = ...` assignment block (if present) for focused function-edit prompts."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ""
    lines = src.splitlines()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "DATUMS" for t in node.targets):
                end = getattr(node, "end_lineno", node.lineno)
                return "\n".join(lines[node.lineno - 1:end])
    return ""


def _map_picked_parts_to_functions(picked_parts: list[dict], funcs: dict[str, dict]) -> dict[str, int]:
    """Map `pgm pick` pre-bake source names back to the `def build_*` function that authored them."""
    hits: dict[str, int] = {}
    for p in picked_parts:
        src_name = str(p.get("source") or "")
        if not src_name:
            continue
        for fname, meta in funcs.items():
            matched = False
            for nid in meta["node_ids"]:
                if nid and (nid == src_name or src_name.startswith(nid + "_") or nid in src_name):
                    matched = True
                    break
            if not matched and src_name in meta["code"]:
                matched = True
            if matched:
                hits[fname] = hits.get(fname, 0) + 1
    return hits


def route_defect_to_function(
    brief: str,
    image_paths: dict,
    builder_src: str,
    *,
    anatomy_spec: dict | None = None,
    pgm_bin: str | Path | None = None,
    doc_path: str | Path | None = None,
    use_jev_pick: bool = True,
    budget: decision_gate.VerifierBudget | None = None,
    key: str | None = None,
    router_model: str = "baseten/bison-1",
    box_model: str = "typesafe/jev-1.14",
) -> dict:
    """Route the single highest-impact visual/proportion/missing-part defect to ONE `def build_*` function.

    Strategy:
      1. Run ONE `baseten/bison-1` multimodal `choice` call over all `def build_*` functions
         (passing `iso-beauty` + `opposite-beauty` + `brief` + `ANATOMY_SPEC.json` + `build_bench_kit.py`).
      2. If `use_jev_pick` and `pgm_bin`/`doc_path` are available, also check `typesafe/jev-1.14`
         bbox -> `pgm pick`. When `pgm pick` hits a specific authored part on an existing solid defect
         (`blocked_opening_or_clearance` or `floating_or_gap`), fuse its evidence with `bison-1`;
         when the issue is `missing_part` or `proportion_or_silhouette` (where `pgm pick` has nothing
         to hit), rely purely on `bison-1`'s calibrated multimodal code-to-function probability!
    """
    key = key or bench.api_key()
    funcs = extract_builder_functions(builder_src)
    if not funcs:
        return {"error": "no_builder_functions_found", "target_function": None, "probabilities": {}, "cost_usd": 0.0}

    # Build compact state well inside bison-1's 33k token window (~8-12k chars total)
    code_budget = 14000
    code_snip = builder_src[:code_budget] + ("\n# ... [truncated]" if len(builder_src) > code_budget else "")
    anat_snip = json.dumps(anatomy_spec, indent=1)[:3000] if anatomy_spec else "(none)"

    state = [
        {
            "type": "text",
            "text": (
                "MODELING BRIEF:\n" + brief.strip()[:2600]
                + "\n\nANATOMY_SPEC.json:\n" + anat_snip
                + "\n\nCURRENT PYTHON BUILDER (`recipes/build_bench_kit.py`):\n```python\n" + code_snip + "\n```\n\n"
                + "RENDERS OF THE CURRENT 3D MODULE:"
            ),
        }
    ]
    for view in ("iso-beauty", "opposite-beauty"):
        p = image_paths.get(view)
        if p and Path(p).exists():
            state.append({"type": "text", "text": f"View: {view}"})
            state.append({"type": "image_url", "image_url": {"url": decision_gate.encode_png(Path(p), max_w=768)}})

    fn_criteria = {}
    for fname, meta in funcs.items():
        desc = meta["doc"] or f"Function `{fname}` (lines {meta['lineno']}-{meta['end_lineno']})"
        if meta["node_ids"]:
            desc += f" [authors: {', '.join(meta['node_ids'][:6])}]"
        fn_criteria[fname] = desc

    questions = {
        "target_function": {
            "type": "choice",
            "instructions": (
                "Compare the rendered 3D module (`iso-beauty` and `opposite-beauty`) against the brief, "
                "ANATOMY_SPEC, and Python code. Identify the SINGLE `def build_*` function whose edit "
                "will fix the largest remaining visual, missing-part, proportion, clearance, or craft defect."
            ),
            "criteria": fn_criteria,
        },
        "defect_type": {
            "type": "choice",
            "instructions": "Classify the primary visual or functional defect shown in the renders.",
            "criteria": DEFECT_TYPE_CRITERIA,
        },
    }

    total_cost = 0.0
    bison_probs: dict[str, float] = {}
    defect_type = "craft_and_detail"
    bison_err = None

    est_bison = 0.0007
    if budget is None or budget.can_spend(est_bison):
        raw, ms, err = decision_gate.post_decisions(
            {"model": router_model, "state": state, "questions": questions},
            key=key,
            timeout=90,
        )
        if err or raw is None:
            bison_err = err or "empty_response"
        else:
            c = float((raw.get("usage") or {}).get("cost") or 0.0)
            total_cost += c
            if budget is not None:
                budget.record(router_model, c)
            ans = raw.get("answers") or {}
            tf = ans.get("target_function") or {}
            probs = tf.get("probabilities") or {}
            for fn in funcs:
                bison_probs[fn] = float(probs.get(fn, 0.0))
            if sum(bison_probs.values()) <= 0 and tf.get("choice") in funcs:
                bison_probs[tf["choice"]] = 1.0
            dt = ans.get("defect_type") or {}
            if isinstance(dt.get("choice"), str):
                defect_type = dt["choice"]
    else:
        bison_err = "budget_capped"

    # Optional `typesafe/jev-1.14` + `pgm pick` localized back-projection
    jev_box = None
    picked_parts: list[dict] = []
    pick_hits: dict[str, int] = {}
    if (
        use_jev_pick
        and pgm_bin
        and doc_path
        and Path(doc_path).exists()
        and image_paths.get("iso-beauty")
        and Path(image_paths["iso-beauty"]).exists()
        and defect_type in ("blocked_opening_or_clearance", "floating_or_gap", "craft_and_detail")
    ):
        est_jev = 0.00035
        if budget is None or budget.can_spend(est_jev):
            jev_res = decision_gate.call_decisions(
                box_model, brief, {"iso-beauty": image_paths["iso-beauty"]},
                include_box=True, key=key, budget=budget,
            )
            total_cost += float(jev_res.get("cost") or 0.0)
            jev_box = jev_res.get("blocked_opening_box")
            if isinstance(jev_box, dict) and isinstance(jev_box.get("box"), dict):
                picked_parts = decision_gate.localize_defect_parts(pgm_bin, doc_path, jev_box, view="iso")
                pick_hits = _map_picked_parts_to_functions(picked_parts, funcs)

    # Fuse probabilities: bison-1 primary + pgm pick boost when localized solid defect hits an authored function
    fused: dict[str, float] = {}
    if bison_probs and sum(bison_probs.values()) > 0:
        s = sum(bison_probs.values())
        for fn in funcs:
            fused[fn] = bison_probs.get(fn, 0.0) / s
    else:
        for fn in funcs:
            fused[fn] = 1.0 / len(funcs)

    used_source = "bison_1_multimodal_code_router"
    if pick_hits and defect_type in ("blocked_opening_or_clearance", "floating_or_gap"):
        tot_h = sum(pick_hits.values())
        for fn in funcs:
            p_pick = pick_hits.get(fn, 0) / tot_h
            fused[fn] = 0.65 * fused[fn] + 0.35 * p_pick
        s = sum(fused.values())
        fused = {fn: round(v / max(s, 1e-9), 5) for fn, v in fused.items()}
        used_source = "bison_1_plus_jev_pgm_pick"
    else:
        fused = {fn: round(v, 5) for fn, v in fused.items()}

    ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
    top_fn, top_p = ranked[0]
    datums_src = extract_datums_source(builder_src)

    return {
        "target_function": top_fn,
        "confidence": top_p,
        "defect_type": defect_type,
        "routing_source": used_source,
        "probabilities": dict(ranked),
        "bison_probabilities": {k: round(v, 5) for k, v in bison_probs.items()},
        "pick_hits": pick_hits,
        "picked_parts": picked_parts[:4],
        "jev_box": jev_box,
        "function_meta": {
            "lineno": funcs[top_fn]["lineno"],
            "end_lineno": funcs[top_fn]["end_lineno"],
            "doc": funcs[top_fn]["doc"],
        },
        "focused_code": (datums_src + "\n\n" if datums_src else "") + funcs[top_fn]["code"],
        "cost_usd": round(total_cost, 6),
        "error": bison_err,
    }


def main():
    ap = argparse.ArgumentParser(description="KIT-FORGE Multimodal Code-to-Function Defect Router")
    ap.add_argument("task")
    ap.add_argument("run_dir", help="Run directory containing build.py, renders/, out/<module>.json")
    ap.add_argument("--no-jev", action="store_true")
    args = ap.parse_args()

    rdir = Path(args.run_dir)
    spec, _ = bench.load_task(args.task)
    brief = (bench.BENCH / "tasks" / args.task / "brief.md").read_text()
    builder_p = rdir / "build.py"
    if not builder_p.exists():
        builder_p = rdir / "recipes" / "build_bench_kit.py"
    src = builder_p.read_text()
    imgs = {v: rdir / "renders" / f"{v}.png" for v in ("iso-beauty", "opposite-beauty") if (rdir / "renders" / f"{v}.png").exists()}
    doc_p = rdir / "out" / f"{spec['module']}.json"
    anat = None
    if (rdir / "ANATOMY_SPEC.json").exists():
        anat = json.loads((rdir / "ANATOMY_SPEC.json").read_text())
    pgm = bench.PROCGEN / "build-release" / "pgm"

    res = route_defect_to_function(
        brief, imgs, src, anatomy_spec=anat,
        pgm_bin=pgm, doc_path=doc_p if doc_p.exists() else None,
        use_jev_pick=not args.no_jev,
    )
    out = {k: v for k, v in res.items() if k != "focused_code"}
    out["focused_code_lines"] = len(res.get("focused_code", "").splitlines())
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
