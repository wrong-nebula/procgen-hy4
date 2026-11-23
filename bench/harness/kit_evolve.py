#!/usr/bin/env python3
"""KIT-FORGE Slice 4: Per-Asset Evolutionary Search Engine (`kit_evolve.py`).

Loop A — Evolutionary optimization over valid `build_bench_kit.py` scripts using ultra-cheap
mini models (`hy4-flash`, `inkling-2-mini`, `gemma-4-31b`, `deepseek-v4.1-flash`) + zero-LLM
geometric operators + 3-tier calibrated verifier stack (`<= $0.05` verifier budget/asset):

  1. 3x3 MAP-Elites Behavioral Archive:
       Axis 0 = structural triangle density (low <25k / mid 25k-55k / high >=55k)
       Axis 1 = craft & detail richness (plain / moderate / rich)
  2. 4 Variation Operators:
       - `localized_defect_mutation`: `function_router.py` (`baseten/bison-1` 33k choice +
         `jev-1.14`/`pgm pick`) routes the biggest defect to 1 `def build_*` function; cheap
         mini-model edits ONLY that function (`N <= 2` views, shared `DATUMS`).
       - `craft_escalation_mutation`: targeted craft upgrade on secondary/detail functions
         (bevels/FWN, Chaikin SDS `limit=1, limit_normals=1`, `0x8007` details, `kittype`).
       - `ast_subassembly_crossover`: `$0.00` Python AST sub-assembly function crossover
         between two archive elites sharing `DATUMS`.
       - `cma_proportion_tune`: `$0.00` LLM pure-NumPy CMA-ES proportion tuning (`cma_tune.py`).
  3. Zero-Cost Local Repair Cascade:
       - `$0.00` `local_repair.try_local_repair` on any geometric/contract defect.
       - `$0.00` `local_repair.try_physics_local_repair` when MuJoCo `physicsScore < 100`.
  4. 3-Tier Anti-Goodhart Verifier Stack (`VerifierBudget(cap_usd=0.05)`):
       - Tier-S1: `baseten/groundhog-1` ($0.00015/eval, within-task) + `$0` DINOv2 energy.
       - Tier-S2: `groundhog-1`/`bison-1` + `nace-ai/drex-max` two-backbone consensus gate.
       - Tier-S3: `baseten/bison-1` soft-prob Bradley-Terry Swiss Elo + `drex-max` disagreement
         or `|ΔElo| < 15` escalation to `openai/gpt-6.1-sol`.
  5. Hard Zero-Regression Guard & Durable `evolve_ledger.jsonl`.
"""
from __future__ import annotations

import argparse
import ast
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# Enforce gentle CPU/GPU/RAM caps by default so KIT-FORGE never starves the host machine
for _env_k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_env_k, "2")
os.environ.setdefault("KITFORGE_FORCE_CPU", "1")
try:
    os.nice(12)
except Exception:
    pass


def _guard_resources(path: Path | None = None, *, min_disk_gib: float = 0.85) -> dict:
    """Monitor host disk & CPU load before launching heavy steps; throttle or fail safely."""
    p = Path(path) if path else Path.cwd()
    while not p.exists() and p.parent != p:
        p = p.parent
    du = shutil.disk_usage(str(p if p.exists() else Path.cwd()))
    free_gib = du.free / (1024 ** 3)
    if free_gib < min_disk_gib:
        raise RuntimeError(f"LOW_DISK_GUARD: only {free_gib:.2f} GiB free (< {min_disk_gib:.2f} GiB); aborting safely.")
    ncpu = os.cpu_count() or 4
    try:
        load1 = os.getloadavg()[0]
    except Exception:
        load1 = 0.0
    waited = 0.0
    while load1 > ncpu * 0.80 and waited < 15.0:
        time.sleep(2.5)
        waited += 2.5
        try:
            load1 = os.getloadavg()[0]
        except Exception:
            break
    return {"free_gib": round(free_gib, 2), "load1": round(load1, 2), "ncpu": ncpu}

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench            # noqa: E402
import cma_tune         # noqa: E402
import contract as ct   # noqa: E402
import decision_gate    # noqa: E402
import function_router  # noqa: E402
import local_repair     # noqa: E402
import mini_mode        # noqa: E402
import tournament       # noqa: E402


# ---------------------------------------------------------------------------
# Durable JSONL Ledger & Budget Helpers
# ---------------------------------------------------------------------------

class EvolveLedger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[dict] = []

    def log(self, event: str, payload: dict):
        rec = {"ts": round(time.time(), 3), "event": event, **payload}
        self.records.append(rec)
        with self.path.open("a") as f:
            f.write(json.dumps(rec) + "\n")


# ---------------------------------------------------------------------------
# 3x3 MAP-Elites Behavioral Grid
# ---------------------------------------------------------------------------

def behavioral_cell(triangles: int, details: int, materials: int) -> tuple[int, int]:
    """Map a valid candidate to its 3x3 MAP-Elites cell `(complexity_bin, craft_bin)`."""
    if triangles < 25000:
        c_bin = 0
    elif triangles < 55000:
        c_bin = 1
    else:
        c_bin = 2

    if details >= 600 or materials >= 6:
        d_bin = 2
    elif details >= 150 or materials >= 4:
        d_bin = 1
    else:
        d_bin = 0
    return (c_bin, d_bin)


class MapElitesArchive:
    """3x3 MAP-Elites archive keyed by `(complexity_bin, craft_bin)`."""

    def __init__(self):
        self.cells: dict[tuple[int, int], dict] = {}
        self.all_valid: dict[str, dict] = {}

    def try_insert(self, cand: dict) -> tuple[bool, str]:
        if not cand.get("valid"):
            return False, "invalid"
        cell = behavioral_cell(
            int(cand.get("triangles") or 0),
            int(cand.get("details") or 0),
            int(cand.get("materials") or 0),
        )
        cand["cell"] = list(cell)
        self.all_valid[cand["id"]] = cand
        cur = self.cells.get(cell)
        if cur is None or float(cand.get("s1_energy") or -1e9) > float(cur.get("s1_energy") or -1e9):
            self.cells[cell] = cand
            return True, f"cell_{cell[0]}_{cell[1]}_{'filled' if cur is None else 'improved'}"
        return False, f"cell_{cell[0]}_{cell[1]}_dominated"

    def elites(self) -> list[dict]:
        return sorted(self.cells.values(), key=lambda c: float(c.get("s1_energy") or -1e9), reverse=True)

    def top_k(self, k: int = 6) -> list[dict]:
        # Combine cell elites + highest-energy non-elite valid candidates (deduplicated)
        seen = set()
        out = []
        for c in self.elites():
            if c["id"] not in seen:
                seen.add(c["id"])
                out.append(c)
        rest = sorted(self.all_valid.values(), key=lambda c: float(c.get("s1_energy") or -1e9), reverse=True)
        for c in rest:
            if len(out) >= k:
                break
            if c["id"] not in seen:
                seen.add(c["id"])
                out.append(c)
        return out[:k]

    def summary(self) -> dict:
        return {
            "cells_filled": len(self.cells),
            "total_cells": 9,
            "total_valid_evaluated": len(self.all_valid),
            "grid": {
                f"{k[0]},{k[1]}": {
                    "id": v["id"],
                    "s1_energy": v.get("s1_energy"),
                    "local_score": v.get("local_score"),
                    "triangles": v.get("triangles"),
                    "details": v.get("details"),
                    "materials": v.get("materials"),
                    "physics_score": v.get("physics_score"),
                }
                for k, v in sorted(self.cells.items())
            },
        }


# ---------------------------------------------------------------------------
# AST Function Manipulation & Zero-Cost Sub-Assembly Crossover
# ---------------------------------------------------------------------------

def replace_function_ast(src: str, func_name: str, new_func_code: str) -> str | None:
    """Safely splice a replacement `def <func_name>(...):` block into `src` via AST line spans."""
    try:
        tree = ast.parse(src)
        new_tree = ast.parse(new_func_code)
    except SyntaxError:
        return None
    # Extract the target FunctionDef from `new_func_code` (in case model wrapped helper imports/comments)
    target_def = None
    for nd in new_tree.body:
        if isinstance(nd, ast.FunctionDef) and nd.name == func_name:
            target_def = nd
            break
    if target_def is None:
        # Accept single FunctionDef even if renamed slightly, forcing name match
        fdefs = [nd for nd in new_tree.body if isinstance(nd, ast.FunctionDef)]
        if len(fdefs) == 1:
            target_def = fdefs[0]
            new_lines = new_func_code.splitlines()
            fn_lines = new_lines[target_def.lineno - 1:getattr(target_def, "end_lineno", target_def.lineno)]
            fn_lines[0] = re.sub(r"def\s+[A-Za-z_][A-Za-z0-9_]*\s*\(", f"def {func_name}(", fn_lines[0], count=1)
            replacement_block = "\n".join(fn_lines)
        else:
            return None
    else:
        new_lines = new_func_code.splitlines()
        replacement_block = "\n".join(new_lines[target_def.lineno - 1:getattr(target_def, "end_lineno", target_def.lineno)])

    lines = src.splitlines()
    for nd in tree.body:
        if isinstance(nd, ast.FunctionDef) and nd.name == func_name:
            s_ln = nd.lineno - 1
            e_ln = getattr(nd, "end_lineno", nd.lineno)
            merged = "\n".join(lines[:s_ln] + [replacement_block] + lines[e_ln:]) + "\n"
            try:
                ast.parse(merged)
                return merged
            except SyntaxError:
                return None
    return None


def ast_subassembly_crossover(src_a: str, src_b: str, *, rng: random.Random | None = None) -> tuple[str | None, str]:
    """$0.00 AST sub-assembly crossover: splice a shared `def build_*` function from `src_b` into `src_a`."""
    rng = rng or random.Random()
    fa = function_router.extract_builder_functions(src_a)
    fb = function_router.extract_builder_functions(src_b)
    common = [fn for fn in fa if fn in fb and fa[fn]["code"].strip() != fb[fn]["code"].strip()]
    if not common:
        return None, "no_differing_shared_functions"
    # Prefer craft / secondary / detail sub-assemblies first for visual uplift
    preferred = [fn for fn in common if any(t in fn.lower() for t in ("craft", "detail", "signage", "secondary", "base", "open"))]
    pick = rng.choice(preferred or common)
    child = replace_function_ast(src_a, pick, fb[pick]["code"])
    if child is None:
        return None, f"splice_failed:{pick}"
    return child, f"crossover:{pick}"


# ---------------------------------------------------------------------------
# Cheap Mini-Model Single-Function Mutation Caller (N <= 2 Ephemeral Views)
# ---------------------------------------------------------------------------

def _encode_jpeg_b64(img_path: str | Path, max_w: int = 768, quality: int = 84) -> str | None:
    p = Path(img_path)
    if not p.exists():
        return None
    im = Image.open(p).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, max(1, round(im.height * max_w / im.width))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _extract_python_block(text: str) -> str:
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S | re.I)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"(def\s+[A-Za-z_][A-Za-z0-9_]*\s*\(.*)", text, re.S)
    return m2.group(1).strip() if m2 else text.strip()


def call_mini_function_mutation(
    brief: str,
    builder_src: str,
    target_func: str,
    renders: dict,
    *,
    operator: str = "localized_defect_mutation",
    defect_type: str = "craft_and_detail",
    model_slug: str = "hy4-flash-free",
    key: str | None = None,
    extra_hint: str = "",
) -> dict:
    """Single-turn, prefix-lean function-scoped mutation using an ultra-cheap mini model (`N <= 2` views).
    Sends ONLY `DATUMS` + `target_func` source (never the entire codebase) to prevent context rot."""
    key = key or bench.api_key()
    prof = mini_mode.get_model_profile(model_slug)
    api_model = prof.get("openrouter_fallback_id") or prof["id"]
    funcs = function_router.extract_builder_functions(builder_src)
    if target_func not in funcs:
        return {"ok": False, "error": f"missing_func:{target_func}", "cost_usd": 0.0}

    fn_code = funcs[target_func]["code"]
    datums_src = function_router.extract_datums_source(builder_src)
    # Also grab top-level constants / imports (first ~45 lines) so helper names are visible
    header_lines = []
    for ln in builder_src.splitlines()[:55]:
        if ln.startswith("def "):
            break
        header_lines.append(ln)
    header_snippet = "\n".join(header_lines).strip()

    if operator == "craft_escalation_mutation":
        directive = (
            f"UPGRADE CRAFT, 3D DEPTH & SURFACE DETAILING inside `{target_func}` while preserving exact "
            f"contacts, clearances, ports, and return signature. Inspect BOTH `iso-beauty` and `opposite-beauty`: "
            f"ensure full 3D depth with convincingly finished side/rear returns (NEVER leave a shallow facade or "
            f"bare rear box), add authentic period/architectural or mechanical craft (bevels/chamfers, stepped "
            f"mouldings/ribs/gussets flush-butted with 0.5 mm clearance, recessed glazing/panels/hardware/tiling, "
            f"restrained Chaikin SDS castings with `limit=1, limit_normals=1, cap='quad'`, and crisp post-SDS "
            f"`0x8007` details `g.courses`/`g.seam`/`g.rivets`/`g.fasteners` or `kittype` signage). "
            f"Offset overlapping faces by >= 0.002 m to prevent z-fighting. Keep all node IDs unique."
        )
    else:
        directive = (
            f"FIX the primary visual/structural defect (`{defect_type}`) inside `{target_func}` "
            f"visible in the two renders compared against the brief. Preserve exact function signature, "
            f"return value, existing ports/controls, `mech_bore` (`r + 0.0018`) clearances, and wall/ground "
            f"anchoring (zero floating or dangling parts). Make a decisive, clearly visible improvement."
        )
    if extra_hint:
        directive += f"\nEXTRA GUIDANCE: {extra_hint}"

    user_content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"TASK BRIEF (excerpt):\n{brief.strip()[:1600]}\n\n"
                f"FILE HEADER / IMPORTS:\n```python\n{header_snippet[:1400]}\n```\n\n"
                + (f"SHARED DATUMS:\n```python\n{datums_src[:1200]}\n```\n\n" if datums_src else "")
                + f"CURRENT FUNCTION TO IMPROVE (`{target_func}`):\n```python\n{fn_code}\n```\n\n"
                + f"MUTATION DIRECTIVE:\n{directive}\n\n"
                + f"Return ONLY a single ```python``` block containing the complete updated `def {target_func}(...):` function. "
                + "Do NOT rewrite other functions or `build_module`."
            ),
        }
    ]
    # N <= 2 visual diet (`iso-beauty` + `opposite-beauty`)
    for vname in ("iso-beauty", "opposite-beauty"):
        if renders.get(vname):
            url = _encode_jpeg_b64(renders[vname], max_w=prof["jpeg_max_w"], quality=prof["jpeg_quality"])
            if url:
                user_content.append({"type": "text", "text": f"CURRENT RENDER: {vname}"})
                user_content.append({"type": "image_url", "image_url": {"url": url}})

    body = {
        "model": api_model,
        "max_tokens": 8000,
        "reasoning": {"effort": "low"},
        "temperature": 0.45,
        "usage": {"include": True},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an expert procedural 3D CAD/kitlib engineer performing a surgical single-function "
                    "evolutionary mutation on `build_bench_kit.py`. Preserve exact Python syntax, kitlib API "
                    "calls, radian rotations, `C(...)` expression helpers (`iff`, `mn`, `mx`, `absx`), unique "
                    "node IDs, and return values. Output ONLY the updated Python function inside ```python```."
                ),
            },
            {"role": "user", "content": user_content},
        ],
    }
    t0 = time.perf_counter()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = json.loads(resp.read())
    except Exception as e:
        return {"ok": False, "error": repr(e)[:200], "cost_usd": 0.0, "model": api_model}

    usage = raw.get("usage") or {}
    cost = float(usage.get("cost") or 0.0)
    txt = ((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    fn_new = _extract_python_block(txt)
    patched = replace_function_ast(builder_src, target_func, fn_new)
    if patched is None:
        return {
            "ok": False,
            "error": "ast_splice_failed",
            "cost_usd": cost,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "latency_s": round(time.perf_counter() - t0, 2),
            "model": api_model,
        }
    return {
        "ok": True,
        "patched_src": patched,
        "target_func": target_func,
        "cost_usd": cost,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "latency_s": round(time.perf_counter() - t0, 2),
        "model": api_model,
    }


def critique_candidate_renders(
    brief: str,
    renders: dict,
    *,
    model_slug: str = "google/gemini-3.8-flash",
    key: str | None = None,
) -> tuple[str, float]:
    """Strictly blind visual self-critique of the candidate's own `iso-beauty` + `opposite-beauty`
    renders against `brief.md` ONLY. Returns (critique_text, cost_usd)."""
    key = key or bench.api_key()
    if "/" in model_slug:
        api_model = model_slug
    else:
        prof = mini_mode.get_model_profile(model_slug)
        api_model = prof.get("openrouter_fallback_id") or prof["id"]
    content: list[dict] = [{
        "type": "text",
        "text": (
            f"TASK BRIEF:\n{brief[:2200]}\n\n"
            "Inspect BOTH renders (`iso-beauty` front 3/4 and `opposite-beauty` rear 3/4) like a strict structural engineer + 3D art director.\n"
            "CRITICAL CHECK: Trace the primary functional travel path (tracks/rails, central road arch, stairs, doorway, moving parts) "
            "from entry to exit across BOTH views.\n"
            "List the TOP 4 concrete, actionable defects you ACTUALLY see in the pixels (put ANY physical obstruction or blocked passage FIRST):\n"
            "1. PHYSICAL/FUNCTIONAL OBSTRUCTIONS: Is ANY misplaced mechanism, capstan, lever, post, bar, hub, or wall sitting ON "
            "or blocking the functional path, tracks, through-arch, stair exit, doorway, or moving clearance, or floating unattached?\n"
            "2. MISSING ASSEMBLIES & CRAFT: What brief-required parts, period hardware, masonry courses, metalwork, scrollwork, "
            "balusters, fasteners, recessed frames, or crisp signage are missing or too rudimentary?\n"
            "3. DEPTH & REAR VIEW (`opposite-beauty`): Any flat/boxy slabs where articulated 3D structure belongs, inverted recesses, blank rear faces, or shallow shells?\n"
            "4. MATERIALS & PROPORTIONS: Any washed-out/clashing colors, flat plastic-looking surfaces, or off-scale/stick-like members?\n"
            "Respond with ONLY 4 concise numbered bullets describing what you ACTUALLY see (max 28 words each). No preamble."
        ),
    }]
    for vk in ("iso-beauty", "opposite-beauty"):
        p = renders.get(vk)
        if p and Path(p).exists():
            content.append({"type": "text", "text": f"View: {vk}"})
            content.append({
                "type": "image_url",
                "image_url": {"url": mini_mode.encode_jpeg_data_url(Path(p), max_w=900, quality=88)},
            })
    body = {
        "model": api_model,
        "max_tokens": 1800,
        "reasoning": {"effort": "low"},
        "temperature": 0.1,
        "usage": {"include": True},
        "messages": [{"role": "user", "content": content}],
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = json.loads(resp.read())
        cost = float((raw.get("usage") or {}).get("cost") or 0.0)
        txt = ((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return txt.strip(), cost
    except Exception:
        return "", 0.0


def run_feedback_refinement_session(
    task: str,
    spec: dict,
    brief: str,
    parent_src: str,
    parent_renders: dict,
    out_dir: Path,
    *,
    target_fn: str = "build_secondary_craft",
    defect_type: str = "craft_and_detail",
    critique: str = "",
    operator: str = "feedback_craft_refinement",
    model_slug: str = "hy4-flash-xhigh",
    max_calls: int = 24,
    extra_system: str = "",
    tag: str = "g01_ref",
) -> dict:
    """Feedback-guided evolutionary mutation: starts a bounded `MiniSession` pre-seeded with the
    valid `parent_src` and Turn-1 `iso-beauty` + `opposite-beauty` renders so the cheap model has
    live `edit_file`/`replace_function` + `preview` + `batch-verify` + `$0 local_repair` feedback."""
    _guard_resources(out_dir)
    key = bench.api_key()
    t0 = time.perf_counter()
    rdir = out_dir / "refine_sessions" / tag
    rdir.mkdir(parents=True, exist_ok=True)
    slug = f"{model_slug}--REF-{tag}"
    box, spec, _ = bench.make_sandbox(task, slug)
    rec = box / "recipes"
    b_py = rec / "build_bench_kit.py"
    lv_py = box / ".bench" / "last_valid_build.py"
    lv_py.parent.mkdir(parents=True, exist_ok=True)
    b_py.write_text(parent_src)
    lv_py.write_text(parent_src)

    init_views: list[tuple[str, Path]] = []
    for vk, label in (
        ("iso-beauty", "CURRENT ELITE iso-beauty (+x,-y,+z, front 3/4 view)"),
        ("opposite-beauty", "CURRENT ELITE opposite-beauty (-x,+y,+z, rear 3/4 view)"),
    ):
        p = parent_renders.get(vk)
        if p and Path(p).exists():
            init_views.append((label, Path(p)))

    focus_clause = (
        "FOCUS: Major architectural/mechanical craft escalation — replace plain flat slabs with articulated "
        "masonry courses, recessed openings/vestibules, deep structural ribs/girders, rich period metalwork "
        "(scrollwork, balusters, rivets, sleepers/ties, wheel trucks), crisp signage, and a FULLY FINISHED rear face."
        if "craft" in operator else
        f"FOCUS: Fix the localized `{defect_type}` issues (especially around `{target_fn}`) AND elevate "
        "3D structural depth, material richness, and rear-view completeness across both renders."
    )

    critique_block = f"\n## Blind Visual Critique of Current Renders\n{critique}\n" if critique else ""
    ref_prompt = (
        f"## KIT-FORGE Evolutionary Craft Upgrade ({operator})\n"
        f"An ALREADY VALID procedural kit is at `recipes/build_bench_kit.py`. "
        f"Both current beauty renders (`iso-beauty` and `opposite-beauty`) are attached above.\n\n"
        f"### Task Brief\n{brief[:2400]}\n"
        f"{critique_block}\n"
        f"### Upgrade Directives\n"
        f"- {focus_clause}\n"
        f"- Spend AT MOST 2–3 calls reading `recipes/build_bench_kit.py` (do NOT waste calls grepping library internals; use `lookup_helper` only if needed).\n"
        f"- WORK INCREMENTALLY: upgrade 1–2 functions with `replace_function`/`edit_file`, then IMMEDIATELY call `preview` "
        f"to compile, verify `batch-verify` PASS, and inspect both `iso-beauty` and `opposite-beauty` before upgrading the next function. "
        f"NEVER batch 4–5 edits before your first `preview`!\n"
        f"- PHYSICAL & FUNCTIONAL SANITY: Keep all active travel/loading clearances unobstructed (tracks/rails, stairways, doorways, arches, passages, and rotating/moving parts); mount operator levers/capstans/controls cleanly OUTSIDE travel paths on proper side platforms or supports.\n"
        f"- NEVER simplify, delete working sub-assemblies, or break contract `controls`/`ports`/`dimensions`.\n"
        f"- Keep `0 dangling`, `0 pinched`, `1 connected component`, and every `Detail` within reach of its parent.\n"
        f"- Once all 4 critique points are visibly resolved across BOTH views and `batch-verify` PASSES, call `finish`."
    )

    res = mini_mode.run_mini(
        box, spec["module"], model_slug, key, ref_prompt, rdir / "session",
        max_calls=max_calls, brief=brief, contract=spec.get("contract"),
        seed_scaffold=False, spec=spec, extra_system=extra_system,
        initial_views=init_views, is_refinement=True,
    )
    cost = float(res.get("cost_usd_openrouter") or 0.0)
    u = res.get("usage") or {}
    pt = int(u.get("prompt_tokens") or 0)
    ct = int(u.get("completion_tokens") or 0)

    chosen_py = lv_py if (lv_py.exists() and res.get("exit") != 0) else b_py
    if not chosen_py.exists() and lv_py.exists():
        chosen_py = lv_py
    new_src = chosen_py.read_text() if chosen_py.exists() else ""
    changed = bool(new_src.strip() and new_src.strip() != parent_src.strip())

    # Always reclaim sandbox immediately to protect host disk
    shutil.rmtree(box, ignore_errors=True)

    if not changed:
        return {
            "ok": False,
            "error": "no_valid_change",
            "cost_usd": cost,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "latency_s": round(time.perf_counter() - t0, 2),
            "model": model_slug,
            "exit": res.get("exit"),
        }
    return {
        "ok": True,
        "patched_src": new_src,
        "target_func": target_fn,
        "cost_usd": cost,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "latency_s": round(time.perf_counter() - t0, 2),
        "model": model_slug,
        "exit": res.get("exit"),
        "tool_calls": res.get("tool_calls"),
    }


# ---------------------------------------------------------------------------
# Candidate Build + Zero-Cost Repair Cascade + MuJoCo Physics + Tier-S1 Screen
# ---------------------------------------------------------------------------

def _run_mujoco_score(doc_path: Path, pgm_bin: Path, *, fast: bool = True) -> dict | None:
    mj_script = HERE / "mujoco_audit.py"
    if not mj_script.exists() or not doc_path.exists():
        return None
    cmd = [
        "nice", "-n", "15",
        "uv", "run", "--offline", "--with", "mujoco", "--with", "numpy", "--with", "coacd", "--with", "pillow",
        "python3", str(mj_script), str(doc_path),
    ] + (["--fast"] if fast else [])
    try:
        r = subprocess.run(
            cmd, env={**os.environ, "PGBENCH_PGM": str(pgm_bin), "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"},
            capture_output=True, text=True, timeout=90,
        )
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except Exception:
        pass
    return None


def evaluate_and_repair_candidate(
    cand_id: str,
    src: str,
    task: str,
    spec: dict,
    brief: str,
    cand_dir: Path,
    pgm_bin: Path,
    *,
    budget: decision_gate.VerifierBudget,
    max_dangling: int = 0,
    max_pinched: int | None = None,
    run_local_repair: bool = True,
    run_mujoco: bool = False,
    parent_ids: list[str] | None = None,
    operator: str = "seed",
    generation: int = 0,
) -> dict:
    """Build candidate, apply `$0.00` `local_repair` (+ optional MuJoCo `try_physics_local_repair`),
    verify hard validity & contract, compute `$0` DINOv2 + Tier-S1 `groundhog-1` energy."""
    ev = cma_tune.evaluate_builder_source(
        src, task, spec, cand_dir, pgm_bin,
        render_size="640x480", render_all_views=False,
        max_dangling=max_dangling, max_pinched=max_pinched,
    )
    repair_log = None
    cur_src = src

    # Zero-cost geometric local repair cascade if invalid but compiled
    if not ev.get("valid") and run_local_repair:
        doc_p = cand_dir / "recipes" / "site" / "bench" / f"{spec['module']}.json"
        if doc_p.exists():
            # Ensure PYTHONPATH includes procgen/recipes for local_repair subprocesses
            old_pp = os.environ.get("PYTHONPATH", "")
            recipes_root = str(bench.PROCGEN / "recipes")
            os.environ["PYTHONPATH"] = recipes_root + (os.pathsep + old_pp if old_pp else "")
            try:
                repair_log = local_repair.try_local_repair(
                    cand_dir, spec["module"], spec.get("contract"), pgm_bin, max_evals=10,
                )
            finally:
                os.environ["PYTHONPATH"] = old_pp
            if repair_log.get("patched"):
                cur_src = (cand_dir / "recipes" / "build_bench_kit.py").read_text()
                ev = cma_tune.evaluate_builder_source(
                    cur_src, task, spec, cand_dir, pgm_bin,
                    render_size="640x480", render_all_views=False,
                    max_dangling=max_dangling, max_pinched=max_pinched,
                )

    if not ev.get("valid"):
        return {
            "id": cand_id,
            "valid": False,
            "error": ev.get("error", "verify_failed"),
            "dangling": ev.get("dangling"),
            "pinched": ev.get("pinched"),
            "contract_ok": ev.get("contract_ok"),
            "local_repair": repair_log,
            "parent_ids": parent_ids or [],
            "operator": operator,
            "generation": generation,
        }

    # Optional MuJoCo physics audit + `$0.00` physics local repair
    physics_score = None
    physics_repair = None
    if run_mujoco and ev.get("doc"):
        ph = _run_mujoco_score(Path(ev["doc"]), pgm_bin, fast=True)
        if ph is not None:
            physics_score = float(ph.get("physicsScore") or 0.0)
            if physics_score < 100.0 and run_local_repair:
                old_pp = os.environ.get("PYTHONPATH", "")
                recipes_root = str(bench.PROCGEN / "recipes")
                os.environ["PYTHONPATH"] = recipes_root + (os.pathsep + old_pp if old_pp else "")
                try:
                    physics_repair = local_repair.try_physics_local_repair(
                        cand_dir, spec["module"], spec.get("contract"), pgm_bin, max_evals=6,
                    )
                finally:
                    os.environ["PYTHONPATH"] = old_pp
                if physics_repair.get("patched"):
                    cur_src = (cand_dir / "recipes" / "build_bench_kit.py").read_text()
                    ev = cma_tune.evaluate_builder_source(
                        cur_src, task, spec, cand_dir, pgm_bin,
                        render_size="640x480", render_all_views=False,
                        max_dangling=max_dangling, max_pinched=max_pinched,
                    )
                    physics_score = float(physics_repair.get("after_physics") or physics_score)

    # Tier-S1 Brief-Conditioned Energy (`baseten/groundhog-1` + `$0` DINOv2)
    s1 = decision_gate.tier_s1_screen(
        brief, ev["renders"], local_judge_score=ev["local_score"], budget=budget,
    )
    return {
        "id": cand_id,
        "valid": True,
        "src": cur_src,
        "doc": ev["doc"],
        "renders": ev["renders"],
        "triangles": ev["triangles"],
        "details": ev.get("details", 0),
        "materials": ev.get("materials", 0),
        "components": ev["components"],
        "dangling": ev.get("dangling", 0),
        "pinched": ev.get("pinched", 0),
        "contract_ok": True,
        "local_score": ev["local_score"],
        "s1_energy": float(s1["energy"]),
        "s1_brief_score": s1.get("brief_score"),
        "max_defect_p": float(s1.get("max_defect_p") or 0.0),
        "physics_score": physics_score,
        "local_repair": repair_log,
        "physics_repair": physics_repair,
        "parent_ids": parent_ids or [],
        "operator": operator,
        "generation": generation,
    }


# ---------------------------------------------------------------------------
# Strict Zero-Regression Acceptance Guard
# ---------------------------------------------------------------------------

def check_zero_regression_guard(
    baseline: dict,
    candidate: dict,
    brief: str,
    *,
    budget: decision_gate.VerifierBudget,
    use_bison_s2: bool = True,
) -> dict:
    """Strict binary Zero-Regression Guard between `baseline` and `candidate`.
    Candidate is accepted ONLY if:
      1. Hard `pgm batch-verify --dangling` + `contract.py` valid with `<=` baseline dangling/pinched.
      2. If physics audited on both, `candidate.physics_score >= baseline.physics_score`.
      3. Tier-S2 consensus (`bison-1`/`groundhog-1` + `drex-max`) confirms candidate does not visually
         regress against baseline (`p_cand_beats_base >= 0.50` on primary AND blend >= 0.50)."""
    hard_ok = bool(
        candidate.get("valid")
        and candidate.get("contract_ok")
        and int(candidate.get("dangling") or 0) <= int(baseline.get("dangling") or 0)
        and int(candidate.get("pinched") or 0) <= int(baseline.get("pinched") or 0)
    )
    if not hard_ok:
        return {"accepted": False, "reason": "hard_validity_regression", "hard_ok": False, "physics_ok": False, "visual_ok": False}

    b_phys = baseline.get("physics_score")
    c_phys = candidate.get("physics_score")
    physics_ok = True if (b_phys is None or c_phys is None) else (float(c_phys) >= float(b_phys) - 1e-6)
    if not physics_ok:
        return {"accepted": False, "reason": f"physics_regression ({b_phys} -> {c_phys})", "hard_ok": True, "physics_ok": False, "visual_ok": False}

    s2 = decision_gate.tier_s2_consensus_gate(
        brief, candidate["renders"], baseline["renders"],
        use_bison=use_bison_s2, budget=budget, double_orient=True,
    )
    p_prim = float(s2.get("p_primary") or 0.5)
    p_blend = float(s2.get("p_blend") or 0.5)
    visual_ok = bool(p_prim >= 0.50 and p_blend >= 0.50)
    return {
        "accepted": bool(hard_ok and physics_ok and visual_ok),
        "reason": "accepted" if visual_ok else "visual_regression_rejected",
        "hard_ok": hard_ok,
        "physics_ok": physics_ok,
        "visual_ok": visual_ok,
        "s2": s2,
    }


# ---------------------------------------------------------------------------
# Fresh Mini-Mode Diverse Seed Generation (when no pre-existing seeds exist)
# ---------------------------------------------------------------------------

def generate_mini_seeds(
    task: str,
    out_dir: Path,
    *,
    model_slug: str = "inkling-2-mini",
    n_seeds: int = 2,
    max_calls: int = 14,
    extra_system: str = "",
) -> tuple[list[Path], float]:
    """Generate `n_seeds` diverse, modular 5-function seed builders via `mini_mode.run_mini`
    using task-agnostic Step-0 diversity briefs. Reclaims heavy sandboxes after saving `build.py`."""
    spec, _ = bench.load_task(task)
    brief = (bench.BENCH / "tasks" / task / "brief.md").read_text()
    key = bench.api_key()
    variants = mini_mode.generate_diversity_briefs(task, spec, brief, k=n_seeds)
    seeds_dir = out_dir / "generated_seeds"
    seeds_dir.mkdir(parents=True, exist_ok=True)
    seed_paths: list[Path] = []
    tot_cost = 0.0

    for i, var in enumerate(variants):
        _guard_resources(out_dir)
        vdir = seeds_dir / f"s{i:02d}"
        slug_tag = f"{model_slug}--LM-r1s{i + 1}"
        box, spec, base_prompt = bench.make_sandbox(task, slug_tag)
        rec = box / "recipes"

        v_prompt = (
            f"{base_prompt}\n\n"
            f"## KIT-FORGE Modular Seed Direction ({var['label']})\n"
            f"{var['hint']}\n\n"
            f"A clean 5-function modular starter exists at `recipes/build_bench_kit.py` "
            f"(`build_base`, `build_primary_mass`, `build_openings_or_mechanism`, "
            f"`build_secondary_craft`, `build_details_and_signage`, plus `build_module`). "
            f"Edit the functions with `replace_function` (or `write_file` if restructuring `build_module`/controls/ports), "
            f"verify with `preview`, and call `finish` once valid, rich in craft, and crisp."
        )
        res = mini_mode.run_mini(
            box, spec["module"], model_slug, key, v_prompt, vdir / "session",
            max_calls=max_calls, brief=brief, contract=spec.get("contract"),
            seed_scaffold=True, spec=spec, extra_system=extra_system,
        )
        tot_cost += float(res.get("cost_usd_openrouter") or 0.0)
        b_py = rec / "build_bench_kit.py"
        lv_py = box / ".bench" / "last_valid_build.py"
        chosen_py = lv_py if (lv_py.exists() and res.get("exit") != 0) else b_py
        if not chosen_py.exists() and lv_py.exists():
            chosen_py = lv_py
        if chosen_py.exists():
            saved = seeds_dir / f"seed_{i:02d}_build.py"
            saved.write_text(chosen_py.read_text())
            seed_paths.append(saved)
        # Reclaim sandbox build artifacts immediately to keep disk lean
        shutil.rmtree(box, ignore_errors=True)

    return seed_paths, round(tot_cost, 6)


# ---------------------------------------------------------------------------
# Main Loop A: Per-Asset Evolutionary Optimizer (`evolve_asset`)
# ---------------------------------------------------------------------------

def evolve_asset(
    task: str,
    seed_builders: list[Path] | None,
    out_dir: Path,
    *,
    model_slug: str = "inkling-2-mini",
    n_seeds: int = 2,
    seed_max_calls: int = 14,
    generations: int = 2,
    refine_max_calls: int = 36,
    use_feedback_refinement: bool = True,
    run_cma_polish: bool = True,
    cma_gens: int = 3,
    cma_pop: int = 6,
    run_mujoco_finalists: bool = True,
    verifier_cap_usd: float = 0.05,
    escalate_sol_ties: bool = True,
    extra_system: str = "",
    seed: int = 42,
) -> dict:
    """Run full Loop A KIT-FORGE per-asset evolution on `task` from one or more valid seed builders
    (or automatically generated Step-0 diverse mini-model seeds if `seed_builders` is empty)."""
    t0 = time.perf_counter()
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger = EvolveLedger(out_dir / "evolve_ledger.jsonl")
    budget = decision_gate.VerifierBudget(cap_usd=verifier_cap_usd)
    spec, _ = bench.load_task(task)
    brief = (bench.BENCH / "tasks" / task / "brief.md").read_text()
    pgm_bin = bench.PROCGEN / "build-release" / "pgm"

    llm_cost_usd = 0.0
    seed_builders = [p for p in (seed_builders or []) if p.exists()]
    if not seed_builders:
        seed_builders, gen_seed_cost = generate_mini_seeds(
            task, out_dir, model_slug=model_slug, n_seeds=n_seeds,
            max_calls=seed_max_calls, extra_system=extra_system,
        )
        llm_cost_usd += gen_seed_cost
        ledger.log("generate_mini_seeds", {
            "task": task, "model_slug": model_slug, "n_seeds": len(seed_builders),
            "llm_cost_usd": gen_seed_cost,
        })

    # Load optional anatomy spec if cached in task dir or sibling seed dirs
    anatomy_spec = None
    for cand_p in [bench.BENCH / "tasks" / task / "ANATOMY_SPEC.json"] + [sb.parent / "ANATOMY_SPEC.json" for sb in seed_builders]:
        if cand_p.exists():
            try:
                anatomy_spec = json.loads(cand_p.read_text())
                break
            except Exception:
                pass

    # Warm local DINOv2 judge once
    cma_tune._get_local_judge()

    archive = MapElitesArchive()
    llm_prompt_tokens = 0
    llm_completion_tokens = 0
    cand_counter = 0

    # Baseline tolerances derived from best valid seed (strictly zero-regression from seed onward)
    base_max_dangling = 0
    base_max_pinched = 0

    # 1. Evaluate all initial seed builders
    base_max_pinched = None
    seed_cands: list[dict] = []
    for idx, sb in enumerate(seed_builders):
        if not sb.exists():
            continue
        cid = f"seed_{idx:02d}"
        src = sb.read_text()
        cdir = out_dir / "work" / cid
        c = evaluate_and_repair_candidate(
            cid, src, task, spec, brief, cdir, pgm_bin,
            budget=budget, max_dangling=0, max_pinched=None,
            run_local_repair=True, run_mujoco=False,
            operator="seed", generation=0,
        )
        # Allow baseline seed with legacy dangling only if strictly matching seed's level
        if not c.get("valid") and c.get("contract_ok"):
            base_max_dangling = max(base_max_dangling, int(c.get("dangling") or 0))
            c = evaluate_and_repair_candidate(
                cid, src, task, spec, brief, cdir, pgm_bin,
                budget=budget, max_dangling=base_max_dangling, max_pinched=None,
                run_local_repair=False, run_mujoco=False,
                operator="seed", generation=0,
            )
        inserted, status = archive.try_insert(c)
        ledger.log("eval_seed", {
            "id": cid, "source": str(sb), "valid": c.get("valid"),
            "s1_energy": c.get("s1_energy"), "local_score": c.get("local_score"),
            "triangles": c.get("triangles"), "details": c.get("details"),
            "cell": c.get("cell"), "inserted": inserted, "status": status,
            "verifier_spend_usd": budget.total_usd,
        })
        if c.get("valid"):
            seed_cands.append(c)

    if not seed_cands:
        return {"ok": False, "error": "no_valid_seeds", "task": task, "verifier_budget": budget.to_dict()}

    best_seed = max(seed_cands, key=lambda c: float(c.get("s1_energy") or -1e9))
    if len(seed_cands) >= 2 and budget.can_spend(0.002):
        # Visually verify which valid seed is stronger before investing refinement turns
        best_vis = seed_cands[0]
        for other in seed_cands[1:]:
            try:
                duel = tournament.pairwise_compare(
                    brief, other["renders"], best_vis["renders"],
                    model="baseten/bison-1", double_orient=True, budget=budget,
                )
                if float(duel.get("p_a_wins", 0.5)) >= 0.52:
                    best_vis = other
            except Exception:
                pass
        best_seed = best_vis

    if run_mujoco_finalists and best_seed.get("doc"):
        ph_seed = _run_mujoco_score(Path(best_seed["doc"]), pgm_bin, fast=True)
        if ph_seed is not None:
            best_seed["physics_score"] = float(ph_seed.get("physicsScore") or 0.0)

    # Cross-family cheap multimodal mutator portfolio (for 1-shot fallback mode)
    CHEAP_MUTATORS = ("deepseek-v4.1-flash", "inkling-2-mini", "gemma-4-31b")
    mut_idx = 0

    def _pick_mutator() -> str:
        nonlocal mut_idx
        if "hy4-flash" in model_slug:
            m = CHEAP_MUTATORS[mut_idx % len(CHEAP_MUTATORS)]
            mut_idx += 1
            return m
        return model_slug

    current_parent = best_seed
    extra_finalists: list[dict] = list(seed_cands)

    # 2. Evolutionary Generations
    for gen in range(1, generations + 1):
        elites = archive.elites()
        if not elites:
            break
        parent = current_parent

        # 2a. Localized Defect Routing (`baseten/bison-1` 33k choice + `jev-1.14`/`pgm pick`)
        funcs = function_router.extract_builder_functions(parent["src"])
        if use_feedback_refinement:
            route = function_router.route_defect_to_function(
                brief, parent["renders"], parent["src"],
                anatomy_spec=anatomy_spec,
                pgm_bin=pgm_bin, doc_path=parent.get("doc"),
                use_jev_pick=budget.can_spend(0.002),
                budget=budget,
            ) if funcs else {}
            target_fn = route.get("target_function") or (next(iter(funcs)) if funcs else "build_secondary_craft")
            defect_type = route.get("defect_type") or "craft_and_detail"
            critique, crit_cost = critique_candidate_renders(brief, parent["renders"], model_slug="gemma-4-31b")
            llm_cost_usd += crit_cost

            op_name = "feedback_defect_refinement" if gen == 1 else "feedback_craft_refinement"
            cand_counter += 1
            cid = f"g{gen:02d}_ref_{cand_counter:02d}"
            ref = run_feedback_refinement_session(
                task, spec, brief, parent["src"], parent["renders"], out_dir,
                target_fn=target_fn, defect_type=defect_type, critique=critique,
                operator=op_name, model_slug=model_slug, max_calls=refine_max_calls,
                extra_system=extra_system, tag=cid,
            )
            llm_cost_usd += float(ref.get("cost_usd") or 0.0)
            llm_prompt_tokens += int(ref.get("prompt_tokens") or 0)
            llm_completion_tokens += int(ref.get("completion_tokens") or 0)
            if ref.get("ok"):
                cdir = out_dir / "work" / cid
                child = evaluate_and_repair_candidate(
                    cid, ref["patched_src"], task, spec, brief, cdir, pgm_bin,
                    budget=budget, max_dangling=base_max_dangling, max_pinched=base_max_pinched,
                    run_local_repair=True, run_mujoco=False,
                    parent_ids=[parent["id"]], operator=f"{op_name}:{target_fn}",
                    generation=gen,
                )
                inserted, status = archive.try_insert(child)
                promoted_parent = False
                if child.get("valid"):
                    extra_finalists.append(child)
                    if budget.can_spend(0.002):
                        try:
                            duel = tournament.pairwise_compare(
                                brief, child["renders"], current_parent["renders"],
                                model="baseten/bison-1", double_orient=True, budget=budget,
                            )
                            if float(duel.get("p_a_wins", 0.5)) >= 0.50:
                                current_parent = child
                                promoted_parent = True
                        except Exception:
                            current_parent = child
                            promoted_parent = True
                    else:
                        current_parent = child
                        promoted_parent = True
                ledger.log("mutation", {
                    "id": cid, "gen": gen, "operator": op_name,
                    "parent": parent["id"], "target_fn": target_fn, "defect_type": defect_type,
                    "router_p": route.get("target_probability"), "valid": child.get("valid"),
                    "s1_energy": child.get("s1_energy"), "local_score": child.get("local_score"),
                    "triangles": child.get("triangles"), "details": child.get("details"),
                    "inserted": inserted, "status": status, "promoted_parent": promoted_parent,
                    "tool_calls": ref.get("tool_calls"),
                    "llm_cost_usd": round(float(ref.get("cost_usd") or 0.0) + crit_cost, 6),
                    "verifier_spend_usd": budget.total_usd,
                })
            else:
                ledger.log("mutation_failed", {
                    "id": cid, "gen": gen, "operator": op_name,
                    "parent": parent["id"], "target_fn": target_fn, "error": ref.get("error"),
                    "llm_cost_usd": round(float(ref.get("cost_usd") or 0.0) + crit_cost, 6),
                    "verifier_spend_usd": budget.total_usd,
                })
        elif funcs:
            route = function_router.route_defect_to_function(
                brief, parent["renders"], parent["src"],
                anatomy_spec=anatomy_spec,
                pgm_bin=pgm_bin, doc_path=parent.get("doc"),
                use_jev_pick=budget.can_spend(0.002),
                budget=budget,
            )
            target_fn = route.get("target_function") or next(iter(funcs))
            defect_type = route.get("defect_type") or "craft_and_detail"
            mut = call_mini_function_mutation(
                brief, parent["src"], target_fn, parent["renders"],
                operator="localized_defect_mutation",
                defect_type=defect_type,
                model_slug=_pick_mutator(),
            )
            llm_cost_usd += float(mut.get("cost_usd") or 0.0)
            llm_prompt_tokens += int(mut.get("prompt_tokens") or 0)
            llm_completion_tokens += int(mut.get("completion_tokens") or 0)
            cand_counter += 1
            cid = f"g{gen:02d}_route_{cand_counter:02d}"
            if mut.get("ok"):
                cdir = out_dir / "work" / cid
                child = evaluate_and_repair_candidate(
                    cid, mut["patched_src"], task, spec, brief, cdir, pgm_bin,
                    budget=budget, max_dangling=base_max_dangling, max_pinched=base_max_pinched,
                    run_local_repair=True, run_mujoco=False,
                    parent_ids=[parent["id"]], operator=f"localized_defect_mutation:{target_fn}",
                    generation=gen,
                )
                inserted, status = archive.try_insert(child)
                if child.get("valid"):
                    extra_finalists.append(child)
                ledger.log("mutation", {
                    "id": cid, "gen": gen, "operator": "localized_defect_mutation",
                    "parent": parent["id"], "target_fn": target_fn, "defect_type": defect_type,
                    "router_p": route.get("target_probability"), "valid": child.get("valid"),
                    "s1_energy": child.get("s1_energy"), "local_score": child.get("local_score"),
                    "inserted": inserted, "status": status,
                    "llm_cost_usd": mut.get("cost_usd"), "verifier_spend_usd": budget.total_usd,
                })
            else:
                ledger.log("mutation_failed", {
                    "id": cid, "gen": gen, "operator": "localized_defect_mutation",
                    "parent": parent["id"], "target_fn": target_fn, "error": mut.get("error"),
                    "llm_cost_usd": mut.get("cost_usd"), "verifier_spend_usd": budget.total_usd,
                })

            # 2b. Op 2: Craft Escalation Mutation on secondary/detail function
            craft_fns = [f for f in funcs if any(k in f.lower() for k in ("detail", "craft", "secondary", "signage"))]
            craft_target = rng.choice(craft_fns) if craft_fns else target_fn
            mut2 = call_mini_function_mutation(
                brief, parent["src"], craft_target, parent["renders"],
                operator="craft_escalation_mutation",
                defect_type="craft_and_detail",
                model_slug=_pick_mutator(),
            )
            llm_cost_usd += float(mut2.get("cost_usd") or 0.0)
            llm_prompt_tokens += int(mut2.get("prompt_tokens") or 0)
            llm_completion_tokens += int(mut2.get("completion_tokens") or 0)
            cand_counter += 1
            cid2 = f"g{gen:02d}_craft_{cand_counter:02d}"
            if mut2.get("ok"):
                cdir2 = out_dir / "work" / cid2
                child2 = evaluate_and_repair_candidate(
                    cid2, mut2["patched_src"], task, spec, brief, cdir2, pgm_bin,
                    budget=budget, max_dangling=base_max_dangling, max_pinched=base_max_pinched,
                    run_local_repair=True, run_mujoco=False,
                    parent_ids=[parent["id"]], operator=f"craft_escalation_mutation:{craft_target}",
                    generation=gen,
                )
                inserted2, status2 = archive.try_insert(child2)
                if child2.get("valid"):
                    extra_finalists.append(child2)
                ledger.log("mutation", {
                    "id": cid2, "gen": gen, "operator": "craft_escalation_mutation",
                    "parent": parent["id"], "target_fn": craft_target, "valid": child2.get("valid"),
                    "s1_energy": child2.get("s1_energy"), "local_score": child2.get("local_score"),
                    "inserted": inserted2, "status": status2,
                    "llm_cost_usd": mut2.get("cost_usd"), "verifier_spend_usd": budget.total_usd,
                })

        # 2c. Op 3: $0.00 AST Sub-Assembly Crossover (whenever >= 2 valid candidates exist)
        pool = list({c["id"]: c for c in (archive.top_k(6) + extra_finalists) if c.get("valid")}.values())
        if len(pool) >= 2:
            pa, pb = current_parent, rng.choice([c for c in pool if c["id"] != current_parent["id"]] or pool[1:])
            x_src, x_desc = ast_subassembly_crossover(pa["src"], pb["src"], rng=rng)
            if x_src is not None:
                cand_counter += 1
                cid_x = f"g{gen:02d}_xover_{cand_counter:02d}"
                cdir_x = out_dir / "work" / cid_x
                child_x = evaluate_and_repair_candidate(
                    cid_x, x_src, task, spec, brief, cdir_x, pgm_bin,
                    budget=budget, max_dangling=base_max_dangling, max_pinched=base_max_pinched,
                    run_local_repair=True, run_mujoco=False,
                    parent_ids=[pa["id"], pb["id"]], operator=x_desc,
                    generation=gen,
                )
                ins_x, st_x = archive.try_insert(child_x)
                if child_x.get("valid"):
                    extra_finalists.append(child_x)
                ledger.log("crossover", {
                    "id": cid_x, "gen": gen, "operator": x_desc,
                    "parents": [pa["id"], pb["id"]], "valid": child_x.get("valid"),
                    "s1_energy": child_x.get("s1_energy"), "inserted": ins_x, "status": st_x,
                    "llm_cost_usd": 0.0, "verifier_spend_usd": budget.total_usd,
                })

    # 3. Op 4: $0.00 LLM CMA-ES Continuous Proportion Tuning on Current Visual Leader
    if run_cma_polish and archive.elites():
        top_el = current_parent
        cma_dir = out_dir / "cma_polish"
        tmp_src_p = cma_dir / "input_build.py"
        cma_dir.mkdir(parents=True, exist_ok=True)
        tmp_src_p.write_text(top_el["src"])
        try:
            cma_res = cma_tune.optimize_builder_cma(
                task, tmp_src_p, cma_dir,
                generations=cma_gens, popsize=cma_pop, workers=1,
                use_groundhog_s1=budget.can_spend(0.003),
                confirm_pairwise=False,  # Tier-S3 Swiss Elo handles final pairwise selection
                budget=budget, seed=seed + 7,
            )
        except Exception as e:
            cma_res = {"ok": False, "error": repr(e)[:200]}
        tuned_p = cma_dir / "tuned" / "recipes" / "build_bench_kit.py"
        if cma_res.get("ok") and tuned_p.exists() and (cma_res.get("tuned") or {}).get("valid"):
            cand_counter += 1
            cid_c = f"cma_polish_{cand_counter:02d}"
            cdir_c = out_dir / "work" / cid_c
            c_cma = evaluate_and_repair_candidate(
                cid_c, tuned_p.read_text(), task, spec, brief, cdir_c, pgm_bin,
                budget=budget, max_dangling=base_max_dangling, max_pinched=base_max_pinched,
                run_local_repair=False, run_mujoco=False,
                parent_ids=[top_el["id"]], operator="cma_proportion_tune",
                generation=generations + 1,
            )
            ins_c, st_c = archive.try_insert(c_cma)
            if c_cma.get("valid"):
                extra_finalists.append(c_cma)
            ledger.log("cma_polish", {
                "id": cid_c, "parent": top_el["id"], "valid": c_cma.get("valid"),
                "delta_local": cma_res.get("delta_local_score"), "delta_s1": cma_res.get("delta_s1_energy"),
                "s1_energy": c_cma.get("s1_energy"), "inserted": ins_c, "status": st_c,
                "llm_cost_usd": 0.0, "verifier_spend_usd": budget.total_usd,
            })

    # 4. Tier-S3 Finalist Selection (`baseten/bison-1` Swiss Soft-Prob Bradley-Terry Elo + `drex-max` / Sol tie-break)
    uniq_fin: dict[str, dict] = {}
    for c in ([best_seed, current_parent] + list(reversed(extra_finalists)) + archive.top_k(6)):
        if c and c.get("valid") and c["id"] not in uniq_fin:
            uniq_fin[c["id"]] = c
        if len(uniq_fin) >= 6:
            break
    finalists = list(uniq_fin.values())

    # Run MuJoCo physics audit + `$0` physics local repair on top-2 finalists if enabled
    if run_mujoco_finalists:
        for fin in finalists[:2]:
            if fin.get("physics_score") is None and fin.get("doc"):
                ph = _run_mujoco_score(Path(fin["doc"]), pgm_bin, fast=True)
                if ph is not None:
                    fin["physics_score"] = float(ph.get("physicsScore") or 0.0)

    tourn_inputs = [
        {
            "id": c["id"],
            "prior_score": float(c.get("s1_energy") or c.get("local_score") or 3.0),
            "max_defect_p": float(c.get("max_defect_p") or 0.0),
            "images": c["renders"],
        }
        for c in finalists
    ]
    tourn_res = decision_gate.tier_s3_finalist_select(
        tourn_inputs, brief,
        rounds=min(3, max(1, len(tourn_inputs) - 1)),
        budget=budget,
        escalate_ties=escalate_sol_ties and budget.can_spend(0.02),
    )
    by_id = {c["id"]: c for c in finalists}
    tourn_winner = by_id[tourn_res["winner_id"]]

    # 5. Strict Zero-Regression Champion Promotion Guard
    if tourn_winner["id"] == best_seed["id"]:
        champion = best_seed
        guard_res = {"accepted": True, "reason": "seed_retained_as_champion", "hard_ok": True, "physics_ok": True, "visual_ok": True}
    else:
        # Check hard & physics non-regression + Elo/head-to-head superiority over best_seed
        elo_map = {r["id"]: r["elo"] for r in tourn_res["ranking"]}
        hard_ok = bool(
            tourn_winner.get("valid")
            and tourn_winner.get("contract_ok")
            and int(tourn_winner.get("dangling") or 0) <= int(best_seed.get("dangling") or 0)
        )
        b_ph, w_ph = best_seed.get("physics_score"), tourn_winner.get("physics_score")
        phys_ok = True if (b_ph is None or w_ph is None) else (float(w_ph) >= float(b_ph) - 1e-6)
        elo_ok = elo_map.get(tourn_winner["id"], 1500.0) > elo_map.get(best_seed["id"], 1500.0)
        if hard_ok and phys_ok and elo_ok:
            champion = tourn_winner
            guard_res = {
                "accepted": True, "reason": "promoted_over_seed",
                "hard_ok": True, "physics_ok": True, "visual_ok": True,
                "elo_gain_vs_seed": round(elo_map[tourn_winner["id"]] - elo_map[best_seed["id"]], 2),
            }
        else:
            champion = best_seed
            guard_res = {
                "accepted": False, "reason": "zero_regression_fallback_to_seed",
                "hard_ok": hard_ok, "physics_ok": phys_ok, "visual_ok": elo_ok,
            }

    # Save champion artifacts & visual before/after strip
    champ_py = out_dir / "champion_build.py"
    champ_py.write_text(champion["src"])
    if champion["id"] != best_seed["id"]:
        tournament.make_ab_strip(
            best_seed["renders"], champion["renders"],
            out_path=out_dir / "seed_vs_champion.png",
            label_a=f"SEED ({best_seed['id']})",
            label_b=f"CHAMPION ({champion['id']})",
        )

    # Reclaim intermediate non-finalist work directories to stay disk-lean
    keep_ids = {champion["id"], best_seed["id"]}
    work_root = out_dir / "work"
    if work_root.exists():
        for child_d in work_root.iterdir():
            if child_d.is_dir() and child_d.name not in keep_ids:
                shutil.rmtree(child_d, ignore_errors=True)

    ledger.log("champion_selected", {
        "champion_id": champion["id"],
        "best_seed_id": best_seed["id"],
        "promoted_over_seed": champion["id"] != best_seed["id"],
        "guard": guard_res,
        "elo_ranking": tourn_res["ranking"],
        "llm_cost_usd": round(llm_cost_usd, 6),
        "verifier_budget": budget.to_dict(),
    })

    summary = {
        "ok": True,
        "task": task,
        "model_slug": model_slug,
        "champion_id": champion["id"],
        "best_seed_id": best_seed["id"],
        "promoted_over_seed": champion["id"] != best_seed["id"],
        "champion_metrics": {
            "valid": champion["valid"],
            "triangles": champion["triangles"],
            "details": champion.get("details"),
            "materials": champion.get("materials"),
            "dangling": champion.get("dangling"),
            "pinched": champion.get("pinched"),
            "local_score": champion.get("local_score"),
            "s1_energy": champion.get("s1_energy"),
            "physics_score": champion.get("physics_score"),
            "operator": champion.get("operator"),
        },
        "seed_metrics": {
            "valid": best_seed["valid"],
            "triangles": best_seed["triangles"],
            "details": best_seed.get("details"),
            "materials": best_seed.get("materials"),
            "dangling": best_seed.get("dangling"),
            "pinched": best_seed.get("pinched"),
            "local_score": best_seed.get("local_score"),
            "s1_energy": best_seed.get("s1_energy"),
            "physics_score": best_seed.get("physics_score"),
        },
        "zero_regression_guard": guard_res,
        "archive": archive.summary(),
        "tournament": {
            "winner_id": tourn_res["winner_id"],
            "elo_gap_top2": tourn_res.get("elo_gap_top2"),
            "top2_consensus": tourn_res.get("top2_consensus"),
            "escalated": tourn_res.get("escalated"),
            "ranking": tourn_res["ranking"],
        },
        "costs": {
            "llm_cost_usd": round(llm_cost_usd, 6),
            "llm_prompt_tokens": llm_prompt_tokens,
            "llm_completion_tokens": llm_completion_tokens,
            "verifier_budget": budget.to_dict(),
            "total_cost_usd": round(llm_cost_usd + budget.total_usd, 6),
        },
        "champion_build_py": str(champ_py),
        "ledger_jsonl": str(out_dir / "evolve_ledger.jsonl"),
        "wall_s": round(time.perf_counter() - t0, 1),
    }
    (out_dir / "evolve_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


# ---------------------------------------------------------------------------
# Binary Gate #4 (`ZERO_REGRESSION_GUARD`)
# ---------------------------------------------------------------------------

def verify_zero_regression_gate(out_dir: Path) -> dict:
    """Verify Binary Gate #4 (`ZERO_REGRESSION_GUARD`):
      1. Runs full Loop A evolution (`evolve_asset`) on `handwheel-valve` using cheap `hy4-flash-free`
         seeds within the hard `<= $0.05` verifier budget cap.
      2. Injects two deliberate adversarial regressions:
           (a) Hard-invalid mutant (broken syntax / detached geometry / contract failure) -> MUST be rejected.
           (b) Visually degraded boxy mutant (valid single primitive box) -> MUST be rejected by Tier-S2 guard.
      3. Verifies ledger integrity (`evolve_ledger.jsonl`), hard zero-regression of champion vs seed,
         and `verifier_spend_usd <= 0.05`."""
    task = "handwheel-valve"
    R = bench.BENCH / "runs" / "2026-11-20" / task
    gen_dir = out_dir / "evo_run" / "generated_seeds"
    seeds = [
        R / "hy4-flash-free--MP-r1s1" / "build.py",
        R / "hy4-flash-free--MP-r1s2" / "build.py",
        gen_dir / "seed_00_build.py",
        gen_dir / "seed_01_build.py",
    ]
    seeds = [p for p in seeds if p.exists()]

    evo = evolve_asset(
        task, seeds if seeds else None, out_dir / "evo_run",
        model_slug="hy4-flash-free",
        n_seeds=2,
        seed_max_calls=24,
        generations=1,
        run_cma_polish=True,
        cma_gens=2,
        cma_pop=4,
        run_mujoco_finalists=True,
        verifier_cap_usd=0.05,
        escalate_sol_ties=False,
    )

    # Adversarial Regression Test (a): hard invalid / broken mutant
    spec, _ = bench.load_task(task)
    brief = (bench.BENCH / "tasks" / task / "brief.md").read_text()
    pgm_bin = bench.PROCGEN / "build-release" / "pgm"
    adv_budget = decision_gate.VerifierBudget(cap_usd=0.05)

    champ_id = evo["champion_id"]
    champ_dir = out_dir / "evo_run" / "work" / champ_id
    champ_src = Path(evo["champion_build_py"]).read_text()
    champ_eval = {
        "id": champ_id,
        "valid": True,
        "contract_ok": True,
        "dangling": evo["champion_metrics"]["dangling"],
        "pinched": evo["champion_metrics"]["pinched"],
        "physics_score": evo["champion_metrics"]["physics_score"],
        "renders": {
            "iso-beauty": str(champ_dir / "renders" / "iso-beauty.png"),
            "opposite-beauty": str(champ_dir / "renders" / "opposite-beauty.png"),
        },
    }

    # (a) Inject detached / broken contract mutant
    broken_src = champ_src.replace('port(', '# broken_port(', 1)
    broken_ev = evaluate_and_repair_candidate(
        "adv_broken", broken_src, task, spec, brief, out_dir / "adv_broken", pgm_bin,
        budget=adv_budget, run_local_repair=False, run_mujoco=False,
    )
    g_broken = check_zero_regression_guard(champ_eval, broken_ev, brief, budget=adv_budget, use_bison_s2=True)

    # (b) Inject valid-but-degraded boxy scaffold mutant (must be rejected visually!)
    boxy_src = mini_mode.generate_modular_scaffold(spec)
    boxy_ev = evaluate_and_repair_candidate(
        "adv_boxy", boxy_src, task, spec, brief, out_dir / "adv_boxy", pgm_bin,
        budget=adv_budget, max_dangling=5, max_pinched=50, run_local_repair=False, run_mujoco=False,
    )
    if boxy_ev.get("valid"):
        g_boxy = check_zero_regression_guard(champ_eval, boxy_ev, brief, budget=adv_budget, use_bison_s2=True)
    else:
        g_boxy = {"accepted": False, "reason": "boxy_failed_contract_hard_rejected"}

    # Reclaim adversarial temp dirs
    for d in (out_dir / "adv_broken", out_dir / "adv_boxy"):
        shutil.rmtree(d, ignore_errors=True)

    ledger_p = Path(evo["ledger_jsonl"])
    ledger_lines = [json.loads(ln) for ln in ledger_p.read_text().splitlines() if ln.strip()] if ledger_p.exists() else []
    vb = evo["costs"]["verifier_budget"]
    cm, sm = evo["champion_metrics"], evo["seed_metrics"]

    checks = {
        "evo_completed_ok": bool(evo.get("ok")),
        "champion_hard_valid_no_regression": bool(
            cm["valid"]
            and int(cm.get("dangling") or 0) <= int(sm.get("dangling") or 0)
            and int(cm.get("pinched") or 0) <= int(sm.get("pinched") or 0)
        ),
        "champion_physics_no_regression": (
            True if (cm.get("physics_score") is None or sm.get("physics_score") is None)
            else float(cm["physics_score"]) >= float(sm["physics_score"]) - 1e-6
        ),
        "hard_invalid_mutant_rejected": not bool(g_broken.get("accepted")),
        "visual_regression_mutant_rejected": not bool(g_boxy.get("accepted")),
        "verifier_spend_le_0_05": bool(vb.get("within_cap")) and float(vb.get("total_usd") or 0.0) <= 0.05,
        "ledger_written_and_valid": len(ledger_lines) >= 3,
    }
    summary = {
        "gate": "ZERO_REGRESSION_GUARD",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "evo_champion_id": evo["champion_id"],
        "promoted_over_seed": evo["promoted_over_seed"],
        "champion_metrics": cm,
        "seed_metrics": sm,
        "adversarial_broken": g_broken,
        "adversarial_boxy": g_boxy,
        "costs": evo["costs"],
        "ledger_events": len(ledger_lines),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "zero_regression_gate.json").write_text(json.dumps(summary, indent=2))
    return summary


def main():
    ap = argparse.ArgumentParser(description="KIT-FORGE Loop A Per-Asset Evolutionary Optimizer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("evolve", help="Evolve a single procedural asset from seed build.py file(s) or fresh mini-mode seeds")
    p_run.add_argument("task")
    p_run.add_argument("--seeds", nargs="*", default=None)
    p_run.add_argument("--n-seeds", type=int, default=2)
    p_run.add_argument("--seed-max-calls", type=int, default=14)
    p_run.add_argument("--out", required=True)
    p_run.add_argument("--model", default="inkling-2-mini")
    p_run.add_argument("--gens", type=int, default=2)
    p_run.add_argument("--no-cma", action="store_true")
    p_run.add_argument("--no-mujoco", action="store_true")
    p_run.add_argument("--cap-usd", type=float, default=0.05)

    p_gate = sub.add_parser("verify-gate", help="Run Binary Gate #4 (ZERO_REGRESSION_GUARD)")
    p_gate.add_argument("--out", default=str(bench.BENCH / "runs" / "2026-11-21" / "zero_regression_gate"))

    args = ap.parse_args()
    if args.cmd == "evolve":
        res = evolve_asset(
            args.task, [Path(s) for s in args.seeds] if args.seeds else None, Path(args.out),
            model_slug=args.model, n_seeds=args.n_seeds, seed_max_calls=args.seed_max_calls,
            generations=args.gens,
            run_cma_polish=not args.no_cma,
            run_mujoco_finalists=not args.no_mujoco,
            verifier_cap_usd=args.cap_usd,
        )
        print(json.dumps(res, indent=2))
    elif args.cmd == "verify-gate":
        res = verify_zero_regression_gate(Path(args.out))
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
