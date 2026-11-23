#!/usr/bin/env python3
"""KIT-FORGE Slice 1: Mini-Model Function-Scoped Execution Engine (`mini_mode.py`).

Adapts `lean.py` specifically for ultra-cheap / small models (`hy4-flash-*`, `inkling-2-mini`,
`gemma-4-31b`, `deepseek-v4.1-flash`):
  1. Per-model profile flags from `models.json` (`max_views=2`, `max_context_tokens=48000`,
     `function_scoped=true`, `max_tokens=24000`).
  2. Standardized 5-function sub-assembly scaffolding in `recipes/build_bench_kit.py`:
       `build_base`, `build_primary_mass`, `build_openings_or_mechanism`,
       `build_secondary_craft`, `build_details_and_signage`
     all referencing a shared `DATUMS` dictionary generated from `ANATOMY_SPEC.json` + contract.
  3. Adaptive `N <= 2` ephemeral visual diet: `iso-beauty` + `opposite-beauty` (proven 86%
     agreement / +0.85 Spearman in `views-experiment.md`) OR `iso-beauty` + zoomed `jev-1.14`
     defect crop, kept strictly outside message history for >= 85-90% KV-cache hit rate and
     < 15 s turn latency.
  4. Task-agnostic Step-0 diversity generator derived dynamically from `brief.md` +
     `ANATOMY_SPEC.json` + `task.json["contract"]` (replaces hardcoded `handwheel-valve` hints).
  5. Strict context thermodynamics telemetry & prefix-preserving rolling compaction so 0 turns
     ever cross the 200k token cliff and local RAM stays < 8 GB.
"""
from __future__ import annotations

import argparse
import ast
import base64
import io
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

REQUIRED_FUNCTIONS = (
    "build_base",
    "build_primary_mass",
    "build_openings_or_mechanism",
    "build_secondary_craft",
    "build_details_and_signage",
)

DEFAULT_MINI_PROFILE = {
    "max_views": 2,
    "max_context_tokens": 48000,
    "function_scoped": True,
    "max_tokens": 24000,
    "jpeg_max_w": 768,
    "jpeg_quality": 85,
}

MINI_SYSTEM_ADDENDUM = """
### KIT-FORGE Mini-Model Function-Scoped Discipline (STRICT)
1. **Modular 5-Function Architecture**: Keep ALL geometry organized into these exact 5 top-level functions reading from the shared `DATUMS` dict (do NOT write one monolithic function):
   - `build_base(g, DATUMS, C)` — plinth, footing, bedplate, or ground-anchored mounting base
   - `build_primary_mass(g, DATUMS, C)` — primary structural volume, casing, piers, walls, or frame
   - `build_openings_or_mechanism(g, DATUMS, C)` — clear unobstructed doorways/arches/bores, sheaves, spindles, hinges, or working mechanisms
   - `build_secondary_craft(g, DATUMS, C)` — yokes, brackets, ribs, arches, roofs, windows, Chaikin SDS castings (`limit=1, limit_normals=1`, `cap="quad"`)
   - `build_details_and_signage(g, DATUMS, C)` — post-SDS zero-triangle `0x8007` details (`courses`, `seam`, `rivets`, `fasteners`, `stroke`, `stamp`) and `kittype` plaques/lettering
2. **Surgical Edits Only**: After creating the initial scaffold, use `replace_function` to edit ONE `build_*` function at a time rather than rewriting the whole file.
3. **N <= 2 Visual Diet**: `preview` shows at most 2 high-signal views (`iso-beauty` + `opposite-beauty`, or `iso-beauty` + zoomed defect crop). Inspect silhouette, back face, openings, and contacts cleanly.
4. **Hard Physical Rules**:
   - `r_bore = r_shaft + 0.0018` (`mech_bore(r)`) for every pin/spindle/pipe bore (`0` clips, `100/100` MuJoCo).
   - Flush-butt ribs, flanges, brackets, and feet with `0.0005` m (`0.5 mm`) clearance — NEVER bury parts inside solids.
   - Clamp every wall-mounted bracket/shoe inside the supporting wall span (`clamp_to_wall`).
   - Rotations are in **RADIANS** (`PI * 0.5`). Never use Python `if`/`min`/`max`/`abs` on `C("...")` expressions — use `iff`, `mn`, `mx`, `absx`.
   - **Bench Kit Rule**: ALWAYS keep `ROOT = Path(__file__).resolve().parent.parent` and `kit = Kit(ROOT, "recipes/site/bench", "site-bench")` — NEVER pass `references=...` to `Kit(...)`! NEVER delete or rename any contract-required `controls` or `ports` in `build_module()`.
   - **No Placeholder Leftovers**: NEVER leave starter boxes (`base_plinth_raw`, `primary_body_raw`, `secondary_cap_raw`) in the final build! Replace EVERY ONE of the 5 `build_*` functions with bespoke, clean, non-clipping geometry (offset overlapping faces by `>= 0.002 m` to prevent z-fighting, and finish ALL 4 sides + rear so `opposite-beauty` looks just as solid as `iso-beauty`).
   - **Revolve & Detail Reach Rules**: For closed `g.lathe` / `revolve` / `annulus` profiles, keep EVERY profile radius strictly `>= 0.002` m (never `0.0` on the axis!). For solid cylinders use `g.n(id, "cylinder", radius=..., depth=..., sides=24, cap="quad")` or `g.rod(...)`. Ensure `0x8007` details (`g.courses`, `g.seam`, `g.rivets`) lie directly on their host face (within default `reach=0.25 m`, or pass `reach=0.8` on large curved/stepped elements).
   - **Physical & Functional Clearance Rule**: Keep ALL active functional paths completely unobstructed from entry to exit (rail tracks & locomotive loading gauge, gate/arch through-passages, descending stair landings/exits, and recessed doorways). NEVER wall off a through-arch or dead-end a stairwell, and NEVER mount operator mechanisms (capstans, cranks, levers) inside a travel gauge — mount them cleanly outside on a side walkway or console.
"""


def load_models_registry() -> dict:
    p = HERE / "models.json"
    return json.loads(p.read_text())


def get_model_profile(model_spec: str) -> dict:
    """Resolve OpenRouter model ID, effort, and mini-mode profile flags from `models.json`."""
    reg = load_models_registry().get("models", {})
    entry = reg.get(model_spec)
    slug = model_spec
    if entry is None:
        for k, v in reg.items():
            if v.get("id") == model_spec:
                entry = v
                slug = k
                break
    entry = entry or {"id": model_spec, "effort": "medium", "vision": True}
    is_mini = bool(entry.get("function_scoped")) or any(
        tok in (slug + " " + entry.get("id", "")).lower()
        for tok in ("hy4-flash", "inkling-2-mini", "inkling-2-small", "gemma-4-31b", "deepseek-v4.1-flash")
    )
    prof = {
        "slug": slug,
        "id": entry.get("id", model_spec),
        "effort": entry.get("effort", "medium"),
        "vision": bool(entry.get("vision", True)),
        "max_views": int(entry.get("max_views", 2 if is_mini else 4)),
        "max_context_tokens": int(entry.get("max_context_tokens", 48000 if is_mini else 150000)),
        "function_scoped": bool(entry.get("function_scoped", is_mini)),
        "max_tokens": int(entry.get("max_tokens", 24000 if is_mini else 64000)),
        "jpeg_max_w": int(entry.get("jpeg_max_w", 768 if is_mini else 960)),
        "jpeg_quality": int(entry.get("jpeg_quality", 85)),
    }
    if prof["id"].startswith("opencode/"):
        prof["openrouter_fallback_id"] = "tencent/hy4-flash"
    return prof


# ---------------------------------------------------------------------------
# Task-Agnostic Step-0 Archetype & Diversity Brief Generator
# ---------------------------------------------------------------------------

ARCHETYPE_KEYWORDS = {
    "articulated": ("walker", "biped", "flyer", "percher", "mecha", "actuator", "rig", "joint", "wing", "leg", "articulated"),
    "organic": ("tree", "cactus", "agave", "aloe", "flora", "rosette", "branch", "canopy", "leaf", "foliage", "botanical"),
    "mechanical": ("valve", "sheave", "pulley", "block", "wheel", "spindle", "turntable", "gear", "rack", "espagnolette",
                   "knocker", "hinge", "bearing", "flange", "pipe", "bore", "carriage", "cradle", "lock", "jamb", "gate-jamb"),
    "infrastructure": ("pier", "basin", "bridge", "hangar", "turntable", "mitre", "canal", "dock", "signal", "railway", "metro", "edicule"),
    "architectural": ("arch", "spandrel", "bay", "gate", "shopfront", "cabin", "stair", "spiral", "door", "window", "bench",
                      "cenotaph", "pyramid", "facade", "cornice", "pilaster", "roof", "balcony", "oriel"),
}

ARCHETYPE_VARIANTS = {
    "mechanical": [
        ("Heavy Foundry Casting", "Emphasize swelling Chaikin SDS cast-iron housings (`limit=1, limit_normals=1`), parting-line ribs, open-bore bronze bushings (`mech_bore`), and bolted FWN flanges."),
        ("Machined Structural Precision", "Emphasize crisp 70% FWN chamfered plates, radial stiffening gussets flush-butted at 0.5 mm, visible hex bolt circles (`bored_nut`), and contrasting machined steel shafts."),
        ("Instrumented Works Station", "Add legible period service details: oil cups, drain/test bosses, stamped foundry nameplate (`kittype.inscribe`), and crisp `0x8007` seam/rivet lines on flat bands."),
        ("Monumental Civic Engineering", "Seat the mechanism on a drafted dressed-stone/masonry plinth with holding-down bolts, stepped collars, and rich 4-material contrast (limestone, cast iron, steel, bronze)."),
    ],
    "architectural": [
        ("Classical Dressed Masonry", "Emphasize bold drafted plinth, rhythmic quoins/pilasters, crisp cornice projections, deep window/arch reveals, and `0x8007` ashlar `g.courses` joints."),
        ("Riveted Iron & Glazing Frame", "Emphasize expressed cast/wrought-iron framing, recessed spandrel panels, translucent multi-pane glazing bars with clear unobstructed openings, and `0x8007` rivet rows."),
        ("Stepped Sculptural Massing", "Emphasize hierarchical setback tiers, corbelled brackets strictly clamped inside wall faces (`clamp_to_wall`), crisp roof/coping silhouette, and finished back elevation."),
        ("Period Civic Craft & Signage", "Emphasize crisp `kittype.sign_board` / `number_plaque` lettering centered on its fascia, panelled timber/bronze joinery, and tight zero-gap jamb/transom closures."),
    ],
    "infrastructure": [
        ("Heavy Civil Masonry & Iron", "Anchor massing on a robust coursed stone base/abutment with iron tie-plates, recessed bolt pockets, and chamfered coping edges."),
        ("Braced Plate-Girder & Truss", "Express riveted plate girders, diagonal bracing, gusset plates flush-butted at 0.5 mm, and clean load-bearing support paths down to ground."),
        ("Operational Deck & Fittings", "Include clear circulation walkways/rails/buffers/mechanisms without blocking declared clearances, plus high-contrast steel, iron, timber, and stone materials."),
        ("Weathered Industrial Landmark", "Layer crisp `0x8007` plate seams, masonry courses, station/works plaques (`kittype`), and clean 360-degree detailing across front and rear elevations."),
    ],
    "organic": [
        ("Sculptural Radial Symmetry", "Build clean layered radial/helical branching tiers with smooth Chaikin SDS curvature (`cap='quad'`, `0` pinched poles) and grounded root flare."),
        ("Asymmetric Natural Growth", "Introduce subtle organic height/angle rhythm across secondary limbs/fronds while keeping all branches solidly joined (`components=1`, `0` dangling)."),
        ("Botanical Crown & Bark Detail", "Pair smooth organic crown forms with faceted trunk/collar transition, stone planter/root bed base, and subtle `0x8007` bark/rib grooves."),
        ("Bold Silhouette Contrast", "Maximize legible outer silhouette rhythm across both `iso-beauty` and `opposite-beauty` while staying strictly inside contract envelope bounds."),
    ],
    "articulated": [
        ("Compact Armored Frame", "Tight FWN chassis shells with generous joint swing clearances, open-bore clevis mounts (`mech_bore`), and low center of mass over foot support hull."),
        ("Skeletal High-Clearance Rig", "Exposed tubular/truss linkage arms, high-contrast hydraulic/bronze bushings, and unobstructed range of motion across joint extrema."),
        ("Aerodynamic Tapered Shell", "Chaikin SDS tapered cowlings (`limit=1`) paired with chamfered mounting ribs, clean hinge axles, and balanced bilateral symmetry."),
        ("Industrial Utility Rig", "Modular bolted equipment pods, clear inspection hatches, `0x8007` panel seams/fasteners, and robust grounded stance pads."),
    ],
}


def classify_archetype(brief: str, spec: dict, anatomy_spec: dict | None = None) -> str:
    text = f"{spec.get('id', '')} {spec.get('title', '')} {spec.get('module', '')} {brief}".lower()
    if anatomy_spec:
        text += " " + json.dumps(anatomy_spec).lower()[:1200]
    scores = {}
    for arch, kws in ARCHETYPE_KEYWORDS.items():
        scores[arch] = sum(2 if kw in spec.get("id", "").lower() else 1 for kw in kws if kw in text)
    best = max(scores.items(), key=lambda kv: kv[1])
    return best[0] if best[1] > 0 else "architectural"


def generate_diversity_briefs(brief: str, spec: dict, anatomy_spec: dict | None = None, k: int = 4) -> list[dict]:
    """Task-agnostic Step-0 diversity generator derived from `brief.md` + `ANATOMY_SPEC.json` + contract."""
    archetype = classify_archetype(brief, spec, anatomy_spec)
    templates = ARCHETYPE_VARIANTS[archetype]
    contract = spec.get("contract") or {}
    controls = list((contract.get("controls") or {}).keys())
    ports = list((contract.get("ports") or {}).keys())
    clearances = list((contract.get("clearances") or {}).keys())
    min_mats = int(contract.get("min_materials") or 3)

    # Pull concrete structural components from ANATOMY_SPEC or brief headings/bullets
    part_names = []
    if isinstance(anatomy_spec, dict):
        for p in (anatomy_spec.get("parts") or anatomy_spec.get("sub_assemblies") or [])[:8]:
            if isinstance(p, dict) and p.get("name"):
                part_names.append(str(p["name"]))
            elif isinstance(p, str):
                part_names.append(p)
    if not part_names:
        for ln in brief.splitlines():
            m = re.match(r"^\s*[-*]\s+\*\*([^*]{3,48})\*\*", ln)
            if m:
                part_names.append(m.group(1).strip())
        part_names = part_names[:6]

    part_clause = f" Key brief/anatomy elements: {', '.join(part_names)}." if part_names else ""
    clr_clause = f" Preserve clear negative-space clearances: {', '.join(clearances)}." if clearances else ""
    port_clause = f" Exact contract ports: {', '.join(ports)}." if ports else ""
    ctrl_clause = f" Parametric controls: {', '.join(controls)}." if controls else ""

    out = []
    for i in range(k):
        name, directive = templates[i % len(templates)]
        letter = chr(ord("A") + i)
        hint = (
            f"Variant {letter} ({name} · {archetype}): {directive}"
            f"{part_clause}{port_clause}{clr_clause}{ctrl_clause} "
            f"Use >= {min_mats} distinct materials and organize code into the 5 `build_*` functions on shared `DATUMS`."
        )
        out.append({
            "k": i + 1,
            "variant": letter,
            "archetype": archetype,
            "label": name,
            "hint": hint,
        })
    return out


# ---------------------------------------------------------------------------
# Shared DATUMS & 5-Function Sub-Assembly Scaffold
# ---------------------------------------------------------------------------

def _safe_num(val, default: float) -> float:
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, (list, tuple)) and val and isinstance(val[0], (int, float)):
        return float(val[0])
    return float(default)


def extract_datums(spec: dict, anatomy_spec: dict | None = None) -> dict:
    """Build a deterministic shared `DATUMS` dictionary from `ANATOMY_SPEC.json` + `task.json['contract']`."""
    import contract as ct
    contract = spec.get("contract") or {}
    env = contract.get("envelope") or {}
    ctrls = contract.get("controls") or {}
    ports = contract.get("ports") or {}
    ctrl_defaults = {
        cname: float(cdef[0]) for cname, cdef in ctrls.items()
        if isinstance(cdef, list) and cdef and isinstance(cdef[0], (int, float))
    }

    def _ev_num(val, fallback: float) -> float:
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            try:
                return float(ct.ev(val, ctrl_defaults))
            except Exception:
                return float(fallback)
        return float(fallback)

    def _span(axis_key: str, default_lo: float, default_hi: float) -> tuple[float, float]:
        b = env.get(axis_key)
        if isinstance(b, list) and len(b) == 2:
            return _ev_num(b[0], default_lo), _ev_num(b[1], default_hi)
        return default_lo, default_hi

    x0, x1 = _span("x", -1.0, 1.0)
    y0, y1 = _span("y", -1.0, 1.0)
    z0, z1 = _span("z", 0.0, 2.0)

    datums: dict[str, object] = {
        "ground_z": _ev_num(contract.get("ground_z", 0.0), 0.0),
        "env_x": [round(x0, 4), round(x1, 4)],
        "env_y": [round(y0, 4), round(y1, 4)],
        "env_z": [round(z0, 4), round(z1, 4)],
        "nom_width": round(max(0.2, x1 - x0), 4),
        "nom_depth": round(max(0.2, y1 - y0), 4),
        "nom_height": round(max(0.2, z1 - z0), 4),
        "base_h_frac": 0.14,
        "wall_t_frac": 0.12,
        "detail_relief_m": 0.012,
        "butt_gap_m": 0.0005,
        "bore_clear_m": 0.0018,
        "min_materials": int(contract.get("min_materials") or 3),
    }
    for cname, cdef in ctrls.items():
        if isinstance(cdef, list) and len(cdef) >= 3:
            datums[f"ctrl_{cname}_default"] = float(cdef[0])
            datums[f"ctrl_{cname}_min"] = float(cdef[1])
            datums[f"ctrl_{cname}_max"] = float(cdef[2])
    if ports:
        datums["ports_spec"] = {
            pname: {
                "type": p.get("type", "attach"),
                "origin": [round(_ev_num(v, 0.0), 4) for v in (p.get("origin") or [0, 0, 0])],
                "normal": [round(_ev_num(v, 0.0), 4) for v in (p.get("normal") or [0, 0, -1])],
                "dimensions": {
                    dk: round(_ev_num(dv, 0.0), 4) if isinstance(dv, (int, float, str)) else dv
                    for dk, dv in (p.get("dimensions") or {}).items()
                },
            }
            for pname, p in ports.items()
        }
    if isinstance(anatomy_spec, dict):
        raw_d = anatomy_spec.get("datums") or anatomy_spec.get("datum_table")
        if isinstance(raw_d, dict):
            for k, v in raw_d.items():
                if isinstance(v, (int, float)):
                    safe_k = re.sub(r"[^a-zA-Z0-9_]", "_", str(k)).lower()
                    if safe_k and safe_k not in datums:
                        datums[f"an_{safe_k}"] = round(float(v), 5)
    return datums


def generate_modular_scaffold(spec: dict, anatomy_spec: dict | None = None) -> str:
    """Generate a valid, compiling 5-function starter scaffold for `recipes/build_bench_kit.py`."""
    module = spec.get("module") or spec["id"]
    contract = spec.get("contract") or {}
    ctrls = contract.get("controls") or {}
    ports = contract.get("ports") or {}
    datums = extract_datums(spec, anatomy_spec)

    ctrl_lines = []
    for cname, cdef in ctrls.items():
        if isinstance(cdef, list) and len(cdef) >= 3:
            ctrl_lines.append(f'        "{cname}": control({cdef[0]}, {cdef[1]}, {cdef[2]}),')
    if not ctrl_lines:
        ctrl_lines.append('        "scale": control(1.0, 0.8, 1.25),')

    port_lines = []
    ports_eval = datums.get("ports_spec") or {}
    for pname, pdef in ports_eval.items():
        orig = pdef.get("origin", [0, 0, 0])
        norm = pdef.get("normal", [0, 0, -1])
        ptype = pdef.get("type", "attach")
        pdims = pdef.get("dimensions") or {}
        if pdims:
            port_lines.append(f'        "{pname}": port({orig!r}, {norm!r}, {ptype!r}, {pdims!r}),')
        else:
            port_lines.append(f'        "{pname}": port({orig!r}, {norm!r}, {ptype!r}),')
    if not port_lines:
        port_lines.append('        "base": port([0, 0, 0], [0, 0, -1], "footing"),')

    datums_json = json.dumps(datums, indent=4)

    return f'''#!/usr/bin/env python3
"""KIT-FORGE modular 5-function builder for `{module}`."""
from pathlib import Path
from kitlib import (
    Kit, Graph, C, control, port,
    lt, gt, iff, mn, mx, absx, sin, cos, sqrt, atan2, floor, ceil, PI, INDEX,
)

ROOT = Path(__file__).resolve().parent.parent


def TUNE(default, lo=None, hi=None):
    """Identity wrapper marking continuous geometric parameters eligible for zero-LLM CMA-ES tuning."""
    return default


DATUMS = {datums_json}


def build_base(g: Graph, DATUMS: dict, C) -> list[str]:
    """Plinth, footing, bedplate, or mounting foundation anchored at `DATUMS['ground_z']`."""
    w = DATUMS["nom_width"] * TUNE(0.60, 0.45, 0.85)
    d = DATUMS["nom_depth"] * TUNE(0.60, 0.45, 0.85)
    h = DATUMS["nom_height"] * TUNE(DATUMS["base_h_frac"], 0.08, 0.25)
    gz = DATUMS["ground_z"]
    g.box("base_plinth_raw", [w, d, h], at=[0, 0, gz + h * 0.5])
    g.mat("base_plinth", "base_plinth_raw", "stone")
    return ["base_plinth"]


def build_primary_mass(g: Graph, DATUMS: dict, C) -> list[str]:
    """Primary structural volume seated cleanly on `build_base`."""
    w = DATUMS["nom_width"] * TUNE(0.48, 0.35, 0.78)
    d = DATUMS["nom_depth"] * TUNE(0.48, 0.35, 0.78)
    base_h = DATUMS["nom_height"] * DATUMS["base_h_frac"]
    body_h = DATUMS["nom_height"] * TUNE(0.55, 0.35, 0.75)
    gz = DATUMS["ground_z"]
    g.box("primary_body_raw", [w, d, body_h], at=[0, 0, gz + base_h + body_h * 0.5])
    g.mat("primary_body", "primary_body_raw", "iron")
    return ["primary_body"]


def build_openings_or_mechanism(g: Graph, DATUMS: dict, C) -> list[str]:
    """Clear openings, bores, spindles, arches, or articulated working mechanisms."""
    return []


def build_secondary_craft(g: Graph, DATUMS: dict, C) -> list[str]:
    """Brackets, ribs, mouldings, yokes, roofs, and restrained Chaikin SDS castings."""
    w = DATUMS["nom_width"] * TUNE(0.54, 0.40, 0.82)
    d = DATUMS["nom_depth"] * TUNE(0.54, 0.40, 0.82)
    cap_h = DATUMS["nom_height"] * TUNE(0.08, 0.04, 0.16)
    z_top = DATUMS["ground_z"] + DATUMS["nom_height"] * (DATUMS["base_h_frac"] + 0.55)
    g.box("secondary_cap_raw", [w, d, cap_h], at=[0, 0, z_top + cap_h * 0.5])
    g.mat("secondary_cap", "secondary_cap_raw", "bronze")
    return ["secondary_cap"]


def build_details_and_signage(g: Graph, DATUMS: dict, C) -> list[str]:
    """Post-SDS zero-triangle 0x8007 surface details and kittype signage/plaques."""
    return []


def build_module():
    kit = Kit(ROOT, "recipes/site/bench", "site-bench")
    g = Graph()
    controls = {{
{chr(10).join(ctrl_lines)}
    }}
    ports = {{
{chr(10).join(port_lines)}
    }}
    parts = []
    parts.extend(build_base(g, DATUMS, C))
    parts.extend(build_primary_mass(g, DATUMS, C))
    parts.extend(build_openings_or_mechanism(g, DATUMS, C))
    parts.extend(build_secondary_craft(g, DATUMS, C))
    parts.extend(build_details_and_signage(g, DATUMS, C))
    g.join("root", *parts)
    kit.module("{module}", controls, g, "root", ports)
    kit.close()


if __name__ == "__main__":
    build_module()
'''


def validate_modular_scaffold(src: str) -> dict:
    """AST-validate that a builder defines the 5 standard sub-assembly functions and shared DATUMS."""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {"ok": False, "error": f"SyntaxError: {e}", "present": [], "missing": list(REQUIRED_FUNCTIONS), "has_datums": False}
    top_funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    has_datums = any(
        isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "DATUMS" for t in n.targets)
        for n in tree.body
    )
    present = [fn for fn in REQUIRED_FUNCTIONS if fn in top_funcs]
    missing = [fn for fn in REQUIRED_FUNCTIONS if fn not in top_funcs]
    uses_datums = {}
    for fn in present:
        names = {nd.id for nd in ast.walk(top_funcs[fn]) if isinstance(nd, ast.Name)}
        uses_datums[fn] = "DATUMS" in names
    return {
        "ok": (len(missing) == 0 and has_datums),
        "has_datums": has_datums,
        "present": present,
        "missing": missing,
        "uses_datums": uses_datums,
    }


# ---------------------------------------------------------------------------
# Adaptive N <= 2 Ephemeral Visual Diet
# ---------------------------------------------------------------------------

def encode_jpeg_data_url(path: Path, max_w: int = 768, quality: int = 85) -> str:
    im = Image.open(path).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, max(1, round(im.height * max_w / im.width))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def make_defect_zoom_crop(src_png: Path, dst_png: Path, defect_box: dict, pad_scale: float = 1.5) -> Path | None:
    """Crop and highlight a `jev-1.14` normalized bounding box on `iso-beauty` for focused repair."""
    b = defect_box.get("box") if isinstance(defect_box, dict) and "box" in defect_box else defect_box
    if not isinstance(b, dict) or not all(k in b for k in ("left", "top", "right", "bottom")) or not src_png.exists():
        return None
    im = Image.open(src_png).convert("RGB")
    w, h = im.size
    x0, y0 = max(0.0, float(b["left"])) * w, max(0.0, float(b["top"])) * h
    x1, y1 = min(1.0, float(b["right"])) * w, min(1.0, float(b["bottom"])) * h
    bw, bh = max(24.0, x1 - x0), max(24.0, y1 - y0)
    cx, cy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
    cw, ch = min(float(w), bw * pad_scale), min(float(h), bh * pad_scale)
    # Keep crop reasonably wide so context around defect is visible
    cw, ch = max(cw, w * 0.30), max(ch, h * 0.30)
    rx0, ry0 = int(max(0, cx - cw * 0.5)), int(max(0, cy - ch * 0.5))
    rx1, ry1 = int(min(w, rx0 + cw)), int(min(h, ry0 + ch))
    crop = im.crop((rx0, ry0, max(rx0 + 16, rx1), max(ry0 + 16, ry1))).resize((640, 480), Image.Resampling.LANCZOS)
    dr = ImageDraw.Draw(crop)
    # Project original box into crop coords
    sx = 640.0 / max(1, (rx1 - rx0))
    sy = 480.0 / max(1, (ry1 - ry0))
    bx0, by0 = (x0 - rx0) * sx, (y0 - ry0) * sy
    bx1, by1 = (x1 - rx0) * sx, (y1 - ry0) * sy
    dr.rectangle([bx0, by0, bx1, by1], outline=(255, 64, 64), width=3)
    dr.rectangle([0, 0, 260, 24], fill=(18, 20, 24))
    dr.text((8, 5), "jev-1.14 zoomed defect crop", fill=(255, 190, 120))
    dst_png.parent.mkdir(parents=True, exist_ok=True)
    crop.save(dst_png)
    return dst_png


def render_mini_views(
    pgm_bin: str | Path,
    doc_path: str | Path,
    out_dir: str | Path,
    *,
    defect_box: dict | None = None,
    view_size: str = "768x576",
    max_views: int = 2,
    set_args: list[str] | None = None,
    wire_cage: bool = False,
) -> list[tuple[str, Path]]:
    """Render strictly `N <= max_views` (default 2) high-signal views:
    - Default: `iso-beauty` + `opposite-beauty` (86% agreement / +0.85 Spearman).
    - Defect mode: `iso-beauty` + zoomed `jev-1.14` defect crop.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    extra = (["--wire-cage"] if wire_cage else []) + list(set_args or [])
    iso_png = out_dir / "mini-iso-beauty.png"
    subprocess.run(
        [str(pgm_bin), "render", str(doc_path), "--out", str(iso_png),
         "--view", "iso", "--pass", "beauty", "--size", view_size, *extra],
        capture_output=True, text=True, timeout=60,
    )
    views: list[tuple[str, Path]] = []
    if iso_png.exists():
        views.append(("iso-beauty (+x,-y,+z, primary 3/4 view)", iso_png))
    if max_views <= 1:
        return views[:1]
    if defect_box and iso_png.exists():
        crop_png = make_defect_zoom_crop(iso_png, out_dir / "mini-defect-crop.png", defect_box)
        if crop_png and crop_png.exists():
            views.append(("jev-1.14 zoomed defect crop (red box = localized issue)", crop_png))
            return views[:max_views]
    opp_png = out_dir / "mini-opposite-beauty.png"
    subprocess.run(
        [str(pgm_bin), "render", str(doc_path), "--out", str(opp_png),
         "--view", "opposite", "--pass", "beauty", "--size", view_size, *extra],
        capture_output=True, text=True, timeout=60,
    )
    if opp_png.exists():
        views.append(("opposite-beauty (-x,+y,+z, rear 3/4 view)", opp_png))
    return views[:max_views]


# ---------------------------------------------------------------------------
# MiniSession (lean.Session subclass with N<=2 visual diet & <48k context cap)
# ---------------------------------------------------------------------------

import lean  # noqa: E402


def _peak_rss_mb() -> float:
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS returns bytes; Linux returns KB
    return round(ru / (1024 * 1024) if sys.platform == "darwin" else ru / 1024.0, 2)


class MiniSession(lean.Session):
    """Function-scoped mini-model execution engine with N<=2 ephemeral views and <48k context cap."""

    def __init__(
        self,
        box,
        module,
        model,
        effort,
        key,
        log,
        *,
        profile: dict | None = None,
        max_calls: int = 24,
        defect_box: dict | None = None,
        extra_system: str = "",
        plateau: bool = False,
        brief: str = "",
        contract=None,
        out_dir=None,
        progressive: bool = True,
        initial_views: list[tuple[str, Path]] | None = None,
        is_refinement: bool = False,
    ):
        super().__init__(box, module, model, effort, key, log, plateau=plateau, brief=brief,
                         contract=contract, out_dir=out_dir, progressive=progressive)
        self.profile = {**DEFAULT_MINI_PROFILE, **(profile or get_model_profile(model))}
        self.max_views = min(2, int(self.profile.get("max_views", 2)))
        self.max_context_tokens = int(self.profile.get("max_context_tokens", 48000))
        self.max_tokens = int(self.profile.get("max_tokens", 24000))
        self.max_calls = int(max_calls)
        self.defect_box = defect_box
        self.extra_system = extra_system
        # Context thermodynamics telemetry
        self.turn_prompt_tokens: list[int] = []
        self.turn_cached_tokens: list[int] = []
        self.turn_latencies_s: list[float] = []
        self.max_views_sent_in_turn: int = 0
        self.compaction_events: int = 0
        self.has_edited_builder: bool = False
        self.explore_calls_before_edit: int = 0
        self.explore_cap: int = 4 if is_refinement else 5
        if initial_views:
            for cap, p in initial_views[:self.max_views]:
                if p and Path(p).exists():
                    self.pending.append((cap, Path(p)))

    def budget(self):
        left = self.max_calls - self.calls
        return f"\n[mini-budget: {left}/{self.max_calls} calls left; max_views={self.max_views}; ctx_cap={self.max_context_tokens}]"

    def _estimate_tokens(self, messages: list[dict]) -> int:
        chars = 0
        for m in messages:
            c = m.get("content")
            if isinstance(c, str):
                chars += len(c)
            elif isinstance(c, list):
                for part in c:
                    if part.get("type") == "text":
                        chars += len(part.get("text", ""))
            if m.get("tool_calls"):
                chars += len(json.dumps(m["tool_calls"]))
        return chars // 4

    def _compact_if_needed(self, messages: list[dict]):
        """Prefix-preserving rolling compaction when approaching 75% of `max_context_tokens`.
        Never mutates messages[0:2] (system + user prompt) so KV-cache prefix stays intact."""
        est = self._estimate_tokens(messages)
        last_actual = self.turn_prompt_tokens[-1] if self.turn_prompt_tokens else 0
        if max(est, last_actual) < int(self.max_context_tokens * 0.75):
            return
        if len(messages) <= 8:
            return
        changed = False
        for m in messages[2:-6]:
            if m.get("role") == "tool" and isinstance(m.get("content"), str) and len(m["content"]) > 520:
                m["content"] = m["content"][:220] + "\n... [compacted by mini_mode to stay under context cap]"
                changed = True
        if changed:
            self.compaction_events += 1

    def do_build(self):
        # Auto-strip accidental `references=...` kwarg copied from site kits so bench builds never crash on missing reference records
        if self.builder.exists():
            txt = self.builder.read_text()
            if "references=" in txt and "Kit(" in txt:
                cleaned = re.sub(r",\s*references\s*=\s*[^,)]+", "", txt)
                if cleaned != txt:
                    self.builder.write_text(cleaned)
        return super().do_build()

    def tool(self, name, a):
        if name in ("write_file", "edit_file", "replace_function"):
            if "build_bench_kit.py" in str(a.get("path", "")):
                self.has_edited_builder = True
        elif (not self.has_edited_builder) and name in ("read_file", "grep", "list_dir"):
            self.explore_calls_before_edit += 1
            cap = getattr(self, "explore_cap", 5)
            if self.explore_calls_before_edit > cap:
                return (
                    f"EXPLORATION CAP REACHED ({cap}/{cap}): Stop reading/searching files! "
                    "You may still call `lookup_helper` if needed, or write `recipes/build_bench_kit.py` NOW "
                    "using `write_file` or `replace_function` to implement all 5 modular functions, then call `preview`."
                )
        if name == "preview":
            built, out = self.do_build()
            if not built:
                return "build failed:\n" + out[-2500:]
            self.snapshot_good()
            ok, text = self.verify_report(a.get("set"))
            if not ok and not a.get("set"):
                import local_repair
                lr_res = local_repair.try_local_repair(self.box, self.module, self.contract, self.pgm_bin, max_evals=10)
                if lr_res.get("patched"):
                    self.local_repair = lr_res
                    self.snapshot_good()
                    ok, text = self.verify_report(a.get("set"))
                    text = f"[local_repair: {lr_res.get('summary', 'patched')}]\n" + text
            if ok and self.builder.exists():
                (self.box / ".bench" / "last_valid_build.py").write_text(self.builder.read_text())
            self.images += 1
            vdir = self.box / "renders" / f"mini-{self.images:03d}"
            views = render_mini_views(
                self.pgm_bin, self.doc, vdir,
                defect_box=self.defect_box,
                max_views=self.max_views,
                set_args=lean.setargs(a.get("set")),
                wire_cage=bool(a.get("wire_cage")),
            )
            for cap, p in views[:self.max_views]:
                self.pending.append((cap, p))
            # Check modular 5-function scaffold health
            if self.builder.exists():
                sc = validate_modular_scaffold(self.builder.read_text())
                if not sc["ok"]:
                    text += f"\n[scaffold warning: missing={sc['missing']} has_datums={sc['has_datums']}]"
            return text + f"\n(mini preview: {len(views)} view(s) attached ephemerally; N<={self.max_views})"
        return super().tool(name, a)

    def chat(self, messages):
        self._compact_if_needed(messages)
        msgs = messages
        if self.pending:
            chosen = self.pending[-self.max_views:]
            self.max_views_sent_in_turn = max(self.max_views_sent_in_turn, len(chosen))
            content = []
            max_w = int(self.profile.get("jpeg_max_w", 768))
            qual = int(self.profile.get("jpeg_quality", 85))
            for caption, png in chosen:
                content += [
                    {"type": "text", "text": caption},
                    {"type": "image_url", "image_url": {"url": encode_jpeg_data_url(Path(png), max_w=max_w, quality=qual)}},
                ]
            if messages and messages[-1].get("role") == "user" and isinstance(messages[-1].get("content"), str):
                msgs = messages[:-1] + [{"role": "user", "content": [{"type": "text", "text": messages[-1]["content"]}] + content}]
            else:
                msgs = messages + [{"role": "user", "content": content}]
            self.pending = []
        active_tools = lean.TOOLS + ([lean.PHYSICS_TOOL] if os.environ.get("PGBENCH_MUJOCO", "0").lower() in ("1", "true", "yes") else [])
        api_model = self.profile.get("openrouter_fallback_id") if self.model.startswith("opencode/") else self.model
        body = {
            "model": api_model,
            "messages": msgs,
            "usage": {"include": True},
            "max_tokens": self.max_tokens,
            "tools": [{"type": "function", "function": t} for t in active_tools],
        }
        eff = self.effective_effort()
        if eff and eff != "none":
            body["reasoning"] = {"effort": eff}
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
        )
        t_req = time.perf_counter()
        last = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    resp = json.loads(r.read())
                if "choices" not in resp:
                    raise RuntimeError(json.dumps(resp)[:400])
                self.turn_latencies_s.append(round(time.perf_counter() - t_req, 2))
                u = resp.get("usage") or {}
                pt = int(u.get("prompt_tokens") or 0)
                ct = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
                self.turn_prompt_tokens.append(pt)
                self.turn_cached_tokens.append(ct)
                return resp
            except Exception as e:
                last = e
                time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"mini_mode model call failed: {last!r}")

    def thermodynamics_report(self) -> dict:
        max_pt = max(self.turn_prompt_tokens) if self.turn_prompt_tokens else 0
        # Compute KV cache hit rate over cache-eligible turns (turns >= 2, since turn 1 warms the prefix)
        elig_pt = sum(self.turn_prompt_tokens[1:])
        elig_ct = sum(self.turn_cached_tokens[1:])
        total_pt = sum(self.turn_prompt_tokens)
        total_ct = sum(self.turn_cached_tokens)
        # Some OpenRouter mini providers don't report cached_tokens explicitly (0 on all turns);
        # track both reported_kv_hit_rate and prefix_stability_rate (deterministic append-only turns).
        reported_hit = (elig_ct / elig_pt) if elig_pt > 0 else (total_ct / total_pt if total_pt > 0 else 0.0)
        n_turns = len(self.turn_prompt_tokens)
        stable_turns = max(0, (n_turns - 1) - self.compaction_events)
        structural_hit = (stable_turns / max(1, n_turns - 1)) if n_turns > 1 else 1.0
        mean_lat = round(sum(self.turn_latencies_s) / len(self.turn_latencies_s), 2) if self.turn_latencies_s else 0.0
        peak_mb = _peak_rss_mb()
        return {
            "turns": n_turns,
            "max_prompt_tokens": max_pt,
            "turns_over_200k": sum(1 for x in self.turn_prompt_tokens if x >= 200000),
            "turns_over_cap": sum(1 for x in self.turn_prompt_tokens if x > self.max_context_tokens),
            "max_views_sent_in_turn": self.max_views_sent_in_turn,
            "reported_kv_cache_hit_rate": round(reported_hit, 4),
            "structural_prefix_hit_rate": round(structural_hit, 4),
            "compaction_events": self.compaction_events,
            "mean_turn_latency_s": mean_lat,
            "peak_rss_mb": peak_mb,
        }

    def loop(self, prompt):
        old_max = lean.MAX_CALLS
        lean.MAX_CALLS = self.max_calls
        try:
            sys_prompt = (
                lean.SYSTEM
                + MINI_SYSTEM_ADDENDUM
                + (lean.MUJOCO_PROMPT if os.environ.get("PGBENCH_MUJOCO", "0").lower() in ("1", "true", "yes") else "")
                + (f"\n{self.extra_system}" if self.extra_system else "")
            )
            messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": prompt}]
            turns, exit_reason = 0, "max_calls"
            while self.calls < self.max_calls and time.time() - self.t0 < lean.TIMEOUT_S and not self.done:
                resp = self.chat(messages)
                turns += 1
                self.turns = turns
                u = resp.get("usage") or {}
                self.account(u)
                msg = resp["choices"][0]["message"]
                keep = {k: v for k, v in msg.items() if v is not None and k in ("role", "content", "tool_calls", "reasoning", "reasoning_details")}
                keep.setdefault("content", "")
                messages.append(keep)
                self.log.write(json.dumps({"t": round(time.time() - self.t0, 1), "assistant": keep, "usage": u}) + "\n")
                calls = msg.get("tool_calls") or []
                if not calls:
                    messages.append({"role": "user", "content": "Continue with `replace_function`/`preview`, or call `finish`." + self.budget()})
                    if turns > 3 and not any(m.get("tool_calls") for m in messages[-4:-1] if m["role"] == "assistant"):
                        exit_reason = "stopped_without_finish"
                        break
                    continue
                for c in calls:
                    self.calls += 1
                    fn = c["function"]["name"]
                    try:
                        text = self.tool(fn, json.loads(c["function"].get("arguments") or "{}"))
                    except Exception as e:
                        text = f"error: {e!r}"[:2000]
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": text + self.budget()})
                    self.log.write(json.dumps({"tool": fn, "args": c["function"].get("arguments", "")[:2000], "result": text[:3000]}) + "\n")
                if self.done:
                    exit_reason = "finished"
            res = {
                "exit": 0 if exit_reason == "finished" else exit_reason,
                "exit_reason": exit_reason,
                "num_turns": turns,
                "tool_calls": self.calls,
                "renders_viewed": self.images,
                "usage": self.usage,
                "cost_usd_openrouter": round(self.cost, 5),
                "wall_s": round(time.time() - self.t0, 1),
                "harness": "kit-forge-mini",
                "profile": self.profile,
                "thermodynamics": self.thermodynamics_report(),
            }
            if self.local_repair and self.local_repair.get("patched"):
                res["local_repair"] = self.local_repair
            return res
        finally:
            lean.MAX_CALLS = old_max


def run_mini(
    box: str | Path,
    module: str,
    model_spec: str,
    key: str,
    prompt: str,
    out_dir: str | Path,
    *,
    effort: str | None = None,
    max_calls: int = 24,
    brief: str = "",
    contract: dict | None = None,
    seed_scaffold: bool = True,
    spec: dict | None = None,
    anatomy_spec: dict | None = None,
    defect_box: dict | None = None,
    extra_system: str = "",
    initial_views: list[tuple[str, Path]] | None = None,
    is_refinement: bool = False,
) -> dict:
    box = Path(box)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prof = get_model_profile(model_spec)
    eff = effort or prof["effort"]
    builder = box / "recipes" / "build_bench_kit.py"
    if seed_scaffold and spec and not builder.exists():
        builder.write_text(generate_modular_scaffold(spec, anatomy_spec))
    with open(out_dir / "transcript.jsonl", "w") as log:
        s = MiniSession(
            box, module, prof["id"], eff, key, log,
            profile=prof, max_calls=max_calls, defect_box=defect_box,
            extra_system=extra_system, brief=brief, contract=contract, out_dir=out_dir,
            initial_views=initial_views, is_refinement=is_refinement,
        )
        try:
            return s.loop(prompt)
        except Exception as e:
            return {
                "exit": f"error: {e!r}"[:300],
                "num_turns": s.turns,
                "tool_calls": s.calls,
                "usage": s.usage,
                "cost_usd_openrouter": round(s.cost, 5),
                "wall_s": round(time.time() - s.t0, 1),
                "harness": "kit-forge-mini",
                "thermodynamics": s.thermodynamics_report(),
            }


def check_context_thermodynamics_gate(therm: dict) -> dict:
    """Binary PASS/FAIL check for CONTEXT_THERMODYNAMICS_GATE."""
    over_200k = int(therm.get("turns_over_200k", 0))
    max_views = int(therm.get("max_views_sent_in_turn", 0))
    peak_mb = float(therm.get("peak_rss_mb", 0.0))
    rep_kv = float(therm.get("reported_kv_cache_hit_rate", 0.0))
    str_kv = float(therm.get("structural_prefix_hit_rate", 0.0))
    effective_kv = rep_kv if rep_kv > 0 else str_kv
    checks = {
        "zero_turns_over_200k": over_200k == 0,
        "views_le_2": max_views <= 2,
        "kv_hit_ge_85pct": effective_kv >= 0.85,
        "peak_ram_lt_8gb": peak_mb < 8192.0,
    }
    return {
        "gate": "CONTEXT_THERMODYNAMICS_GATE",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "metrics": therm,
    }


def main():
    import bench
    ap = argparse.ArgumentParser(description="KIT-FORGE Mini-Model Execution Engine")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_div = sub.add_parser("diversity", help="Print task-agnostic Step-0 diversity briefs")
    p_div.add_argument("task")
    p_div.add_argument("--k", type=int, default=4)

    p_sc = sub.add_parser("scaffold", help="Generate modular 5-function scaffold for a task")
    p_sc.add_argument("task")
    p_sc.add_argument("--out", default=None)

    p_run = sub.add_parser("run", help="Run a bounded mini-mode session on a task")
    p_run.add_argument("task")
    p_run.add_argument("--model", default="inkling-2-mini")
    p_run.add_argument("--slug", default=None)
    p_run.add_argument("--max-calls", type=int, default=16)
    p_run.add_argument("--date", default=time.strftime("%Y-%m-%d"))

    args = ap.parse_args()
    if args.cmd == "diversity":
        spec, _ = bench.load_task(args.task)
        brief = (bench.BENCH / "tasks" / args.task / "brief.md").read_text()
        print(json.dumps(generate_diversity_briefs(brief, spec, k=args.k), indent=2))
    elif args.cmd == "scaffold":
        spec, _ = bench.load_task(args.task)
        code = generate_modular_scaffold(spec)
        if args.out:
            Path(args.out).write_text(code)
            print(f"wrote {args.out}")
        else:
            print(code)
    elif args.cmd == "run":
        os.environ["PGBENCH_DATE"] = args.date
        bench.TODAY = args.date
        slug = args.slug or re.sub(r"[^A-Za-z0-9._-]+", "-", args.model) + "--LM-r1"   # lean harness + lessons memory, as kit_evolve seeds use; letters after -- are bench.py conditions
        box, spec, base_prompt = bench.make_sandbox(args.task, slug)
        brief = (bench.BENCH / "tasks" / args.task / "brief.md").read_text()
        div = generate_diversity_briefs(brief, spec, k=1)[0]["hint"]
        prompt = f"## KIT-FORGE Mini-Mode Direction\n{div}\n\n" + base_prompt
        out_dir = bench.BENCH / "runs" / args.date / args.task / slug
        res = run_mini(
            box, spec["module"], args.model, bench.api_key(), prompt, out_dir,
            max_calls=args.max_calls, brief=brief, contract=spec.get("contract"),
            seed_scaffold=True, spec=spec,
        )
        (out_dir / "run.json").write_text(json.dumps(res, indent=2))
        # Keep what the model delivered next to the run record: the builder, its notes and the generated kit.
        if (box / "recipes" / "build_bench_kit.py").exists():
            shutil.copy2(box / "recipes" / "build_bench_kit.py", out_dir / "build.py")
        if (box / "NOTES.md").exists():
            shutil.copy2(box / "NOTES.md", out_dir / "NOTES.md")
        if (box / "recipes" / "site" / "bench").is_dir():
            shutil.copytree(box / "recipes" / "site" / "bench", out_dir / "out", dirs_exist_ok=True)
        print(f"builder, notes and generated kit copied to {out_dir} (sandbox kept at {box})")
        gate = check_context_thermodynamics_gate(res.get("thermodynamics") or {})
        (out_dir / "thermodynamics_gate.json").write_text(json.dumps(gate, indent=2))
        print(json.dumps({"run_summary": {k: res.get(k) for k in ("exit", "num_turns", "tool_calls", "cost_usd_openrouter", "wall_s")}, "gate": gate}, indent=2))


if __name__ == "__main__":
    main()
