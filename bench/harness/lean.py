#!/usr/bin/env python3
"""A lean modelling harness: a direct tool loop on OpenRouter's chat API, built for kit modules.

Versus running the model inside Claude Code: a short system prompt; typed tools for the modelling loop, chiefly
`preview` (build -> verify -> contract -> an annotated 2x2 contact sheet with axes, port arrows and a 1.75 m figure,
in one call); images are sent only with the next request and never kept in the history, so the history stays
byte-identical and prompt-cacheable; a visible budget; last-good and last-valid snapshots; `finish` refuses once
when the module is not valid; exact cost from OpenRouter's usage accounting (with a token-price fallback).

Plateau stop (harness letter P): the first valid build and then each changed build (at most every SCORE_EVERY
calls) are scored silently by a critic; the best-scoring builder is kept; at the first plateau (PLATEAU_N scores
without beating the best by PLATEAU_GAIN, after MIN_CALLS calls) the model is asked to finish.

Used by bench.py when the condition contains L (lean) or P (lean + plateau stop)."""
import base64
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

MAX_CALLS = 150
TIMEOUT_S = 45 * 60
SCORE_EVERY, PLATEAU_N, PLATEAU_GAIN, MIN_CALLS = 3, 2, 0.25, 12
CRITIC = os.environ.get("PGBENCH_CRITIC", "openai/gpt-6.1-sol")
WRITABLE = re.compile(r"^(recipes/build_bench_kit\.py|recipes/site/bench/.*|renders/.*|NOTES\.md|PLAN\.md|memory/.*)$")

SYSTEM = """You are an expert procedural modeller working in a kit-builder toolkit. You build one module by writing a
Python builder on kitlib (`recipes/build_bench_kit.py`), then check and look at it with the tools. Work in short loops:
write or edit the builder, call `preview` (it builds, verifies, checks the module's contract and shows you an annotated
contact sheet: four views with explicit camera poses, XYZ axes, port arrows and a 1.75 m human figure for scale), then
improve proportions, construction, detail and fidelity to the brief. Use `render` only for a specific extra view.

### Compact kitlib & Helper Cheat Sheet (ready to use immediately — use `lookup_helper` instead of reading whole files)
- **Coordinate frame**: Z is UP (`z = 0` is ground), `-Y` is FRONT, `+Y` is BACK, `-X` is LEFT/WEST, `+X` is RIGHT/EAST. Units: metres.
- **Kit & Module boilerplate**:
  `from kitlib import Kit, Graph, C, control, port, lt, le, gt, ge, iff, mn, mx, absx, sin, cos, sqrt, atan2, floor, ceil, PI, INDEX`
  `kit = Kit(ROOT, "recipes/site/bench", "site-bench")` -> `g = Graph()` -> `controls = {"width": control(6.0, 4.4, 8.8), ...}` -> `W = C("width")` -> `ports = {"base": port([0, 0, 0], [0, 0, -1], "footing")}` -> `kit.module("<module>", controls, g, out_id, ports)` -> `kit.close()`.
- **Parametric `X` expressions**: `C("name")` returns an `X` expression. NEVER use Python `if`/`and`/`or`/`bool`/`min`/`max`/`abs`/`math.*` on `X`! Use `lt`, `le`, `gt`, `ge`, `iff(cond, then_val, else_val)`, `mn`, `mx`, `absx`, `sin`, `cos`, `sqrt`, `atan2`, `floor`, `ceil`. Cache shared sub-expressions with `W2 = g.let("half_w", W * 0.5)`.
- **Core `Graph` ops**: `g.box(id, size, at)` (centred on `at`!), `g.mat(id, src, slot)`, `g.xf(id, src, t=..., r=..., s=...)`, `g.join(id, *part_ids)`, `g.rod(id, a, b, radius, cap="quad")`, `g.lathe(id, profile, sides=24, cap="quad")`, `g.annulus(id, section, sides=24)`, `g.pipe(id, axis, start, end, outer, bore, at=...)`, `g.profile_sweep(id, curve, profile=..., section=...)`, `g.array(id, src, start, step, count)`, `g.ring(id, src, count, step_angle)`, `g.chamfer(...)`, `g.support_loops(...)`, `g.loop_cut(...)`, `g.crease(...)`, `g.subdivide(...)`, `g.smooth(...)`, `g.shrinkwrap(...)`, `g.face_extrude(...)`, `g.bake(id, joined_id)` (`Kit.module` auto-bakes leaf modules).
- **Hard-surface SDS & FWN craft — the 70 / 30 Rule & Anatomy-First Discipline**:
  - **Anatomy & Clearance Datum Table FIRST**: Before writing geometry, define your coordinate constants cleanly at the top of the builder (plinth top Z, bedplate Z, cheek inner/outer X, wheel axial width + 5 mm clearance each side, shaft radius vs open bore radius `bore_r = pin_r + 0.003`). Never guess transforms or overlap solids blindly.
  - **70% Machined FWN (`filleted_box(..., mode="fwn")`, `drafted_plinth`, `bored_nut`, `bored_collar`, `bolted_flange`, `spoked_sheave`, `pillow_block_bearing`)**: flat plates, drafted stone blocks, machined flanges, hex fasteners, structural iron. Flat faces must stay dead flat with tight 2–3 segment bevels.
  - **30% Restrained Chaikin SDS (`cast_housing`, `sds_cylinder(..., lod_levels=[2, 1, 0])`, `g.loft(sections=...)`, `g.pierced_extrude`)**: cast-iron cheeks, domes, curved brackets, square-to-round transitions, bosses. Prefer `g.loft(sections=...)` for smooth flange-to-neck transitions and `g.pierced_extrude(profile, holes, depth)` for bored plates so pins clear bores without clipping. Use `selection={"plane": ...}` / `{"radius": ...}` / `{"zRange": ...}` on `g.crease` for selective variable sharpness.
  - **3 SDS rules**: (1) ALWAYS keep `limit=1, limit_normals=1` on `g.subdivide` so cages do NOT shrink and 3 mm `audit` contacts hold; (2) ALWAYS pass `cap="quad"` on SDS `cylinder`/`revolve`/`sweep`/`tube`/`loft` and `cap="inset"` on SDS `extrude`; (3) apply surface details (`g.fasteners`, `g.rivets`, `g.seam`, `g.stamp`, `g.text`) **AFTER** `g.subdivide` / `g.chamfer`. Inspect cages with `preview(wire_cage=True)` and surface continuity with `render(pass="matcap")` or `render(pass="zebra")`.
- **Zero-triangle surface details (`0x8007`) & lettering**: Build guide curves/points (`g.guide`, `g.points`, `g.field`, `g.outline`, `g.courses(id, at=..., r=..., size=[w, h], course=0.075, block=0.225)`), then attach shading-level details onto a surface (`g.seam(id, surface, guide)`, `g.stroke(id, surface, guide, profile="groove")`, `g.stamp(id, surface, pts, ...)`, `g.rivets(id, surface, curve, pitch)`, `g.fasteners(id, surface, guide)`, `g.ticks`, `g.text`), or use `g.brick_wall` / `g.ashlar_wall`. For 3D plaques/lettering use `from kittype import inscribe, sign_board, number_plaque` (and note `g.text` anchors at vertical centre, NOT baseline!).
- **Domain helper modules in `recipes/`** (query exact signatures anytime via `lookup_helper`):
  - `kitparts`: `cast_housing`, `sds_cylinder`, `filleted_box`, `curved_boss_patch`, `bored_nut`, `bored_collar`, `drafted_plinth`, `spoked_sheave`, `pillow_block_bearing`, `helical_spring`, `bolted_clevis`, `fit_arch`, `arched_wall`, `voussoirs`, `cornice`, `string_course`, `impost`, `plinth`, `coping`, `bolted_flange`, `spoked_wheel`, `tie_rod`.
  - `kitlib_arch` (attached to `Graph`): `clamp_to_wall` (keeps brackets/shoes strictly inside supporting wall span!), `g.box_on` (`z_base`-anchored box with 0.5 mm butt clearance), `g.hipped_roof`, `g.sash_window` (transparent glass + mullions), `g.external_stair`, `g.corner_quoins`, `g.window_surround`, `g.louvred_lantern`, `g.corbelled_chimney`, `g.panelled_door`.
  - `kitlib_mech` (attached to `Graph`, 100/100 MuJoCo open-bore SDS/FWN): `mech_bore(shaft_r)` (`shaft_r + 0.0018`), `g.plummer_block` (split journal pedestal + bronze bushing + oil cup), `g.bushed_sheave` (bronze-bushed spoked pulley/flywheel), `g.clevis_fork` (pierced linkage fork + pin/nut/cotter), `g.stuffing_box` (2-stud bronze packing gland), `g.arched_yoke` (twin-foot spindle bridge), `g.pierced_cheek` (cast side standard + outboard flange/ribs).
  - Also attached to `Graph`: `kitlib_masonry` (`g.brick_wall`, `g.ashlar_wall`, `g.arch_ring`), `kitlib_iron` (`g.rolled`, `g.truss`, `g.plate_girder`), `kitlib_glazing` (`g.glazed_vault`, `g.ridge_lantern`), `kitlib_orders` (`g.column`, `g.entablature`), `kitlib_ornament` (`g.scroll_bracket`), `kitlib_pipes` (`g.pipe_run`, `g.valve`), `kitlib_curves` (`g.plate`), `kitlib_tracery` (`g.tracery_window`), `kitlib_aero`.

Use the tools; there is no shell. Paths are relative to the working directory. Prefer `replace_function` when rewriting a whole Python helper/builder function cleanly. Every tool result ends with your remaining budget. Stop when the module is genuinely good: finish (with `finish`) once it is valid and further changes would not clearly improve it, leaving the builder in a state that passes `verify`. The task follows."""

MUJOCO_PROMPT = """
- **Physical Realism & MuJoCo Verification (ACTIVE)**:
  - Every `preview`/`verify` reports a `physics:` summary (`score/100`, real `mass` in kg, gravity `tilt`, overturning safety factor `SF`, `buried` duplicate parts, `crossClips`, `sameClips`). Aim for `buried=0`, `crossClips=0`, `tilt=0.0deg`, and `SF >= 1.5`.
  - Build assemblies like real mechanical objects: use `bored_nut` / `bored_collar` / `bolted_flange` / `G.pipe` so pins, bolts, and studs pass cleanly through open bores rather than solid cylinders; never bury duplicate fasteners inside castings.
  - Call `physics_check(xray=True)` before `finish` to run full CoACD concave collision + hinge spin-test (for sheaves/wheels/drums) and view the X-ray collision overlay (magenta = buried/duplicate, orange = cross-material clip, green `+` = CoM)."""

PHYSICS_TOOL = {
    "name": "physics_check",
    "description": "Run full MuJoCo 4.0 physics verification: CoACD concave-exact collision check, gravity settling, lateral overturning safety factor, rotating mechanism hinge spin-test (sheaves/wheels/drums), and optional X-ray collision PNG overlay + MJCF XML export.",
    "parameters": {
        "type": "object",
        "properties": {
            "xray": {"type": "boolean", "description": "Render and show an isometric X-ray collision overlay highlighting buried (magenta) and clipping (orange) parts plus CoM"},
            "export_mjcf": {"type": "boolean", "description": "Export MuJoCo MJCF XML alongside the module in recipes/site/bench/"}
        }
    }
}

TOOLS = [
    {"name": "read_file", "description": "Read a text file (or show an image file). Use offset/limit (lines) for long files.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "offset": {"type": "integer"},
                                                      "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "list_dir", "description": "List a directory (non-recursive).",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "grep", "description": "Search files under a directory for a regular expression; returns matching lines.",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
                    "required": ["pattern"]}},
    {"name": "lookup_helper", "description": "Search all kitlib/kitparts helper modules by keyword (e.g. 'sds', 'crease', 'subdivide', 'cast_housing', 'filleted_box', 'window', 'stair', 'arch', 'flange') and return exact Python signatures and summaries.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "write_file", "description": "Create or overwrite a file (only the builder, files under recipes/site/bench/, renders/, NOTES.md, PLAN.md).",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace an exact, unique piece of text in a file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"},
                                                      "new": {"type": "string"}}, "required": ["path", "old", "new"]}},
    {"name": "replace_function", "description": "AST-validated replacement of a top-level Python `def <function_name>(...):` in a writable .py file (or append if `create_if_missing` is true). Eliminates indentation/multi-match edit failures.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "function_name": {"type": "string"},
                                                      "code": {"type": "string"}, "create_if_missing": {"type": "boolean"}},
                    "required": ["path", "function_name", "code"]}},
    {"name": "preview", "description": "Build the module, verify it (check + SDS topology metrics, sweep, audit, contract) and show the annotated 2x2 contact sheet. Optional control values in set; set wire_cage=true to overlay SDS control cages & creases on the iso view. The main loop tool.",
     "parameters": {"type": "object", "properties": {"set": {"type": "object", "description": "control values, e.g. {\"pitch\": 6}"},
                                                      "wire_cage": {"type": "boolean", "description": "overlay SDS control cages & creases on the iso tile"}}}},
    {"name": "build", "description": "Only run recipes/build_bench_kit.py (regenerates recipes/site/bench/). Returns its output or traceback.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "verify", "description": "pgm check (with SDS quad/cage/LOD/pole stats), sweep and audit (--sweep), the module's contract, and an inspect summary (ports, controls, materials), without images.",
     "parameters": {"type": "object", "properties": {"set": {"type": "object"}}}},
    {"name": "pgm", "description": "Run the pgm tool with arguments, e.g. [\"nodes\", \"subdivide\"]. Returns its JSON.",
     "parameters": {"type": "object", "properties": {"args": {"type": "array", "items": {"type": "string"}}}, "required": ["args"]}},
    {"name": "render", "description": "Render one extra view and show it. view: iso|opposite|front|side|top; pass: beauty|clay|parts|matcap|zebra|curvature (use matcap/zebra/curvature to inspect bevel highlights, FWN flatness and SDS surface continuity); projection: perspective|ortho; or yaw/pitch (degrees); wire_cage: true to overlay SDS control cages & creases; lod: 0..3; control values in set.",
     "parameters": {"type": "object", "properties": {"view": {"type": "string"}, "pass": {"type": "string"}, "projection": {"type": "string"},
                                                      "yaw": {"type": "number"}, "pitch": {"type": "number"},
                                                      "wire_cage": {"type": "boolean"}, "lod": {"type": "integer"},
                                                      "set": {"type": "object"}}}},
    {"name": "finish", "description": "End the session: write NOTES.md (what you built, how it maps to the brief, what you would improve). Refused once if the module is not valid.",
     "parameters": {"type": "object", "properties": {"notes": {"type": "string"}}, "required": ["notes"]}},
]


def setargs(sets):
    return [x for k, v in (sets or {}).items() for x in ("--set", f"{k}={v}")]


class Session:
    def __init__(self, box, module, model, effort, key, log, plateau=False, brief="", contract=None, out_dir=None, progressive=False):
        self.box, self.module, self.model, self.effort, self.key, self.log = Path(box), module, model, effort, key, log
        self.out_dir = Path(out_dir) if out_dir else None
        self.progressive = progressive
        self.doc = self.box / "recipes" / "site" / "bench" / f"{module}.json"
        self.builder = self.box / "recipes" / "build_bench_kit.py"
        self.pgm_bin = str(self.box / "bin" / "pgm")
        self.contract = contract
        self.turns = self.calls = self.images = 0
        self.ckpt_shas = set()
        self.cost = 0.0
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "reasoning_tokens": 0}
        self.done = False
        self.finish_refused = False
        self.craft_nudged = False
        self.last_craft_gaps = []
        self.pending = []                 # (caption, png) images for the next request only
        self.t0 = time.time()
        self.plateau, self.brief = plateau, brief
        self.scores, self.best = [], None
        self.last_scored_text, self.last_scored_call = None, -99
        self.critic_cost = 0.0
        self.stop_asked, self.stop_call = False, None
        self.last_verify_ok = False
        self.last_sds = None
        self.local_repair = None
        (self.box / ".bench").mkdir(exist_ok=True)

    # ---------------------------------------------------------------- helpers
    def path(self, p):
        q = (self.box / p).resolve()
        if not str(q).startswith(str(self.box.resolve())):
            raise ValueError("path outside the working directory")
        return q

    def run(self, cmd, timeout=180):
        r = subprocess.run(cmd, cwd=self.box, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr)

    def pgmj(self, *args):
        code, out = self.run([self.pgm_bin, *map(str, args)])
        try:
            return json.loads(out.strip().splitlines()[-1])
        except Exception:
            return {"passed": False, "error": out[-600:]}

    def budget(self):
        left = MAX_CALLS - self.calls
        return f"\n[budget: {left} tool calls left of {MAX_CALLS}; {(time.time() - self.t0) / 60:.0f} of {TIMEOUT_S // 60} minutes used]"

    def do_build(self):
        code, out = self.run(["python3", "recipes/build_bench_kit.py"])
        return code == 0 and self.doc.exists(), out

    def verify_report(self, sets=None):
        """(valid, text): check, sweep, audit --sweep --dangling, contract and an inspect summary (single warm pgm call when available)."""
        import contract as ct
        import diag
        lines, pgm_ok, misfits, sds = [], True, [], None
        if ct.supports_batch_verify(self.pgm_bin) and self.doc.exists():
            bv, cres = ct.batch_verify(self.contract, self.doc, self.pgm_bin, sets=sets, check_dangling=True)
            for name in ("check", "sweep", "audit"):
                d = bv.get(name) or ({"passed": False, "error": bv["error"]} if name == "check" and bv.get("error") else {"passed": False})
                pgm_ok &= bool(d.get("passed"))
                if name == "check" and isinstance(d.get("sds"), dict):
                    sds = d["sds"]
                short = {"passed": bool(d.get("passed")), **{k: d[k] for k in ("error", "issues", "failures", "detached", "dangling", "hints") if k in d and d[k]}}
                lines.append(f"{name}: {json.dumps(short)[:1200]}")
            ok = pgm_ok
            if cres is not None:
                misfits = cres.get("misfits", [])
                ok &= cres["ok"]
                lines.append("contract: ok" if cres["ok"] else "contract misfits:\n  " + "\n  ".join(misfits[:12]))
            ins = bv.get("inspect") or {}
        else:
            for name, args in (("check", ["check", self.doc, *setargs(sets)]), ("sweep", ["sweep", self.doc]),
                               ("audit", ["audit", self.doc, "--sweep"])):
                d = self.pgmj(*args)
                pgm_ok &= bool(d.get("passed"))
                if name == "check" and isinstance(d.get("sds"), dict):
                    sds = d["sds"]
                short = {k: d[k] for k in ("passed", "error", "issues", "failures", "detached", "dangling", "hints") if k in d and d[k]}
                lines.append(f"{name}: {json.dumps(short)[:1200]}")
            ok = pgm_ok
            if self.contract:
                r = ct.check(self.contract, self.doc, self.pgm_bin, self.box)
                misfits = r.get("misfits", [])
                ok &= r["ok"]
                lines.append("contract: ok" if r["ok"] else "contract misfits:\n  " + "\n  ".join(misfits[:12]))
            ins = self.pgmj("inspect", self.doc, *setargs(sets))
        self.last_sds = sds
        sds_line = diag.format_sds_line(sds)
        if sds_line:
            lines.append(sds_line)
        if os.environ.get("PGBENCH_MUJOCO", "0").lower() in ("1", "true", "yes") and pgm_ok and self.doc.exists():
            mj_line = self.run_mujoco_summary()
            if mj_line:
                lines.append(mj_line)
        ports = "; ".join(f"{p['name']} ({p['type']}) at {[round(c, 2) for c in p['origin']]} facing {[round(c, 2) for c in p['normal']]}"
                          for p in ins.get("ports", [])) or "none"
        mats_list = [m["slot"] for m in ins.get("materials", [])]
        mats = ", ".join(mats_list)
        rec = ins.get("receipt") or {}
        n_det = int(rec.get("placedDetails") or 0)
        n_cages = int((sds or {}).get("sdsCages") or 0)
        n_lods = int((sds or {}).get("multiLodPrototypes") or 0)
        n_pinched = int((sds or {}).get("pinchedPoles") or 0)
        craft_gaps = []
        if n_cages < 2:
            craft_gaps.append(f"sdsCages={n_cages}<2 (use Frontier SDS with g2_fair=1/subdiv_normals=1/lod_levels=[2,1,0] on cast housings/cheeks/bosses)")
        if n_det < 2:
            craft_gaps.append(f"details={n_det}<2 (add post-SDS 0x8007 foundry lettering, rivets, stone joints, or rope lay)")
        if len(mats_list) < 4:
            craft_gaps.append(f"materials={len(mats_list)}<4 (contrast stone, iron/patina, steel, bronze)")
        if n_pinched > 8:
            craft_gaps.append(f"pinchedPoles={n_pinched} (add g2_fair=1 or quad/inset caps)")
        self.last_craft_gaps = craft_gaps
        craft_s = f"craft: ok (cages={n_cages} lods={n_lods} details={n_det} mats={len(mats_list)} pinched={n_pinched})" if not craft_gaps \
            else f"craft: POLISH_NEEDED ({'; '.join(craft_gaps)})"
        lines.append(craft_s)
        lines.append(f"ports: {ports}\nmaterials: {mats}")
        self.last_verify_ok = bool(ok)
        if ok:
            shutil.copy2(self.builder, self.box / ".bench" / "last_valid.py")
        if pgm_ok and self.doc.exists():
            shutil.copy2(self.doc, self.box / ".bench" / "last_valid.json")
        if self.progressive:
            lines.append(self.pace_disclosure(pgm_ok, misfits, ins))
        if pgm_ok and self.out_dir and self.builder.exists():
            import hashlib
            src_bytes = self.builder.read_bytes()
            sha = hashlib.sha256(src_bytes).hexdigest()[:12]
            if sha not in self.ckpt_shas:
                self.ckpt_shas.add(sha)
                cdir = self.out_dir / "checkpoints"
                cdir.mkdir(parents=True, exist_ok=True)
                fname = f"turn_{self.turns:03d}_call_{self.calls:03d}.py"
                (cdir / fname).write_bytes(src_bytes)
                rec = {"turn": self.turns, "calls": self.calls, "wall_s": round(time.time() - self.t0, 1),
                       "cost_usd": round(self.cost, 4), "reasoning_tokens": self.usage.get("reasoning_tokens", 0),
                       "sha": sha, "file": fname, "contract_ok": ok, "misfits": misfits,
                       "triangles": (ins.get("receipt") or {}).get("placedTriangles")}
                with open(cdir / "index.jsonl", "a") as cf:
                    cf.write(json.dumps(rec) + "\n")
        return ok, "\n".join(lines)

    def pace_disclosure(self, pgm_ok, misfits, ins):
        """Progressive disclosure across Stewart Brand's shearing layers: withhold fine surface-detail prompts
        until envelope, ports, and load-bearing skeleton settle."""
        env_or_port = [m for m in misfits if any(k in m for k in ("envelope", "port", "ground", "evaluate"))]
        if not pgm_ok or env_or_port:
            return ("[pace-layer 1/3 · SITE, ENVELOPE & PORTS] Settle closed manifold solids, ground seating, "
                    "declared envelope bounds, and port origins/normals first; withhold secondary details until Layer 1 passes.")
        if misfits:
            return ("[pace-layer 2/3 · BEARING SKELETON & CLEAR OPENINGS] Layer 1 settled ✓. Now resolve remaining "
                    "structural contact / clear-opening / control-sweep misfits before adding surface ornament.")
        n_mats = len(ins.get("materials", []))
        return (f"[pace-layer 3/3 · JOINERY, FASTENERS & INSCRIPTIONS] Layers 1–2 settled ✓ ({n_mats} material slots). "
                "You may now refine secondary joinery, FWN bevels / SDS creases & LODs, procedural fasteners/courses, and kittype lettering "
                "while keeping `verify` green.")

    def run_mujoco_summary(self):
        mj_script = Path(__file__).resolve().parent / "mujoco_audit.py"
        if not mj_script.exists():
            return None
        cmd = ["uv", "run", "--with", "mujoco", "--with", "numpy", "--with", "coacd", "--with", "pillow",
               "python3", str(mj_script), "--fast", "--summary", str(self.doc)]
        env = {**os.environ, "PGBENCH_PGM": self.pgm_bin}
        try:
            r = subprocess.run(cmd, cwd=self.box, env=env, capture_output=True, text=True, timeout=25)
            lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip().startswith("physics:")]
            return lines[-1] if lines else None
        except Exception:
            return None

    def snapshot_good(self):
        if self.pgmj("check", self.doc).get("passed"):
            shutil.copy2(self.builder, self.box / ".bench" / "last_good.py")

    # ---------------------------------------------------------------- tools
    def tool(self, name, a):
        if name == "read_file":
            p = self.path(a["path"])
            if p.suffix.lower() in (".png", ".jpg", ".jpeg"):
                self.pending.append((a["path"], p))
                return f"image {a['path']} (shown below)"
            lines = p.read_text(errors="replace").splitlines()
            off, lim = int(a.get("offset") or 0), int(a.get("limit") or 400)
            body = "\n".join(f"{i + 1}\t{l}" for i, l in enumerate(lines[off:off + lim], start=off))
            return body + (f"\n... ({len(lines) - off - lim} more lines; use offset)" if len(lines) > off + lim else "")
        if name == "lookup_helper":
            import ast
            words = [w.lower() for w in re.split(r"[\s,;]+", str(a.get("query") or "")) if w.strip()]
            scored = []
            for pyf in sorted((self.box / "recipes").glob("*.py")):
                if pyf.name.startswith(("test_", "build_")):
                    continue
                try:
                    src = pyf.read_text(errors="ignore")
                    tree = ast.parse(src)
                except Exception:
                    continue
                for nd in ast.walk(tree):
                    if isinstance(nd, ast.FunctionDef) and not nd.name.startswith("_"):
                        doc_s = (ast.get_docstring(nd) or "").strip().splitlines()
                        summary = doc_s[0][:160] if doc_s else ""
                        hay = f"{pyf.stem} {nd.name} {summary}".lower()
                        matched = sum(1 for w in words if w in hay) if words else 1
                        if matched > 0:
                            exact = int(any(w == nd.name.lower() for w in words))
                            sig = f"{nd.name}({ast.unparse(nd.args)})"
                            entry = f"- {pyf.stem}.{sig}" + (f"\n    {summary}" if summary else "")
                            scored.append((matched == len(words), exact, matched, entry))
            if not scored:
                return f"no helpers matched '{a.get('query')}' (try keywords like 'arch', 'window', 'stair', 'roof', 'flange', 'column', 'moulding', 'inscribe', 'truss')"
            scored.sort(key=lambda t: (-int(t[0]), -t[1], -t[2], t[3]))
            return "\n".join(t[3] for t in scored[:30])
        if name == "list_dir":
            p = self.path(a.get("path") or ".")
            return "\n".join(sorted(f.name + ("/" if f.is_dir() else "") for f in p.iterdir()))[:6000]
        if name == "grep":
            p = self.path(a.get("path") or ".")
            rx = re.compile(a["pattern"])
            hits = []
            files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file() and f.suffix in (".py", ".md", ".json", ".txt")]
            for f in files:
                for i, l in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                    if rx.search(l):
                        hits.append(f"{f.relative_to(self.box)}:{i}: {l[:200]}")
                        if len(hits) >= 80:
                            return "\n".join(hits) + "\n... (truncated)"
            return "\n".join(hits) or "no matches"
        if name in ("write_file", "edit_file", "replace_function"):
            rel = str(self.path(a["path"]).relative_to(self.box.resolve()))
            if not WRITABLE.match(rel):
                return "not allowed: you may only write the builder, recipes/site/bench/, renders/, NOTES.md, PLAN.md"
            p = self.path(a["path"])
            if name == "write_file":
                if p.suffix == ".py":
                    import ast
                    try:
                        ast.parse(a["content"])
                    except SyntaxError as se:
                        return f"write rejected — Python SyntaxError on line {se.lineno}: {se.msg}\n  {se.text or ''}"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(a["content"])
                return f"wrote {rel} ({len(a['content'].splitlines())} lines)"
            if name == "replace_function":
                import ast
                fn_name = a["function_name"]
                new_code = a["code"].rstrip() + "\n"
                try:
                    fn_tree = ast.parse(new_code)
                except SyntaxError as se:
                    return f"replace_function rejected — SyntaxError in new `{fn_name}` on line {se.lineno}: {se.msg}"
                if not p.exists():
                    return f"replace_function failed: {rel} does not exist yet (use write_file first)"
                src = p.read_text()
                tree = ast.parse(src)
                lines = src.splitlines(keepends=True)
                target = next((nd for nd in tree.body if isinstance(nd, (ast.FunctionDef, ast.AsyncFunctionDef)) and nd.name == fn_name), None)
                if target is None:
                    if not a.get("create_if_missing"):
                        avail = [nd.name for nd in tree.body if isinstance(nd, (ast.FunctionDef, ast.AsyncFunctionDef))]
                        return f"replace_function failed: `{fn_name}` not found in {rel} (top-level functions: {avail}; pass create_if_missing=true to append)"
                    candidate = src.rstrip() + "\n\n\n" + new_code
                else:
                    start = min([target.lineno] + [d.lineno for d in target.decorator_list])
                    end = target.end_lineno
                    candidate = "".join(lines[:start - 1]) + new_code + "".join(lines[end:])
                try:
                    ast.parse(candidate)
                except SyntaxError as se:
                    return f"replace_function rejected — resulting {rel} has SyntaxError on line {se.lineno}: {se.msg}"
                p.write_text(candidate)
                return f"replaced `{fn_name}` in {rel}"
            text = p.read_text()
            n = text.count(a["old"])
            if n != 1:
                return f"edit failed: the old text occurs {n} times (it must occur exactly once; consider `replace_function` for whole functions)"
            candidate = text.replace(a["old"], a["new"])
            if p.suffix == ".py":
                import ast
                try:
                    ast.parse(candidate)
                except SyntaxError as se:
                    return f"edit rejected — Python SyntaxError on line {se.lineno}: {se.msg}\n  {se.text or ''}"
            p.write_text(candidate)
            return f"edited {rel}"
        if name == "build":
            ok, out = self.do_build()
            if ok:
                self.snapshot_good()
                self.maybe_score()
            return ("ok\n" if ok else "build failed\n") + out[-4000:]
        if name == "verify":
            ok, text = self.verify_report(a.get("set"))
            return ("VALID\n" if ok else "NOT VALID\n") + text
        if name == "preview":
            built, out = self.do_build()
            last_valid_doc = self.box / ".bench" / "last_valid.json"
            if not built:
                if self.progressive and last_valid_doc.exists():
                    import diag
                    self.images += 1
                    png = self.box / "renders" / f"preview-{self.images:03d}.png"
                    frm = diag.envelope_frame(self.contract)
                    got, cap = diag.sheet(self.box / ".bench" / "__missing__.json", png, self.pgm_bin, frame=frm, fallback_doc=last_valid_doc)
                    if got:
                        self.pending.append((f"retained last-valid sheet {png.name}", png))
                    return "build failed (showing retained last-valid mesh above so spatial context stays pinned):\n" + out[-3500:] + ("\n" + cap if got else "")
                return "build failed\n" + out[-4000:]
            self.snapshot_good()
            ok, text = self.verify_report(a.get("set"))
            import diag
            self.images += 1
            png = self.box / "renders" / f"preview-{self.images:03d}.png"
            frm = diag.envelope_frame(self.contract) if self.progressive else None
            fb = last_valid_doc if (self.progressive and last_valid_doc.exists()) else None
            got, cap = diag.sheet(self.doc, png, self.pgm_bin, a.get("set"), frame=frm, fallback_doc=fb,
                                  sds=self.last_sds, wire_cage=bool(a.get("wire_cage")))
            if got:
                self.pending.append((f"contact sheet {png.name}", png))
            self.maybe_score()
            return ("VALID\n" if ok else "NOT VALID\n") + text + ("\n" + cap if got else "\nsheet failed: " + cap)
        if name == "pgm":
            code, out = self.run([self.pgm_bin, *[str(x) for x in a["args"]]])
            return out[-6000:]
        if name == "render":
            self.images += 1
            png = self.box / "renders" / f"lean-{self.images:03d}.png"
            cam = ["--yaw", str(a["yaw"]), "--pitch", str(a.get("pitch", 20))] if "yaw" in a else ["--view", a.get("view") or "iso"]
            proj = ["--projection", a["projection"]] if a.get("projection") in ("ortho", "perspective") else []
            extra = []
            if a.get("wire_cage"):
                extra.append("--wire-cage")
            if a.get("lod") is not None:
                extra += ["--lod", str(int(a["lod"]))]
            code, out = self.run([self.pgm_bin, "render", str(self.doc), "--out", str(png), *cam, *proj, *extra,
                                  "--pass", a.get("pass") or "beauty", "--size", "800x600", *setargs(a.get("set"))])
            if not png.exists():
                return "render failed: " + out[-1500:]
            self.pending.append((png.name, png))
            return f"rendered {png.relative_to(self.box)} ({' '.join(cam + proj + extra)}, {a.get('pass') or 'beauty'})"
        if name == "physics_check":
            if not self.doc.exists():
                return "physics_check failed: build the module first"
            mj_script = Path(__file__).resolve().parent / "mujoco_audit.py"
            cmd = ["uv", "run", "--with", "mujoco", "--with", "numpy", "--with", "coacd", "--with", "pillow",
                   "python3", str(mj_script), str(self.doc)]
            if a.get("sweep_controls", True):
                cmd.append("--sweep-controls")
            xray_png = None
            if a.get("xray", True):
                self.images += 1
                xray_png = self.box / "renders" / f"physics-xray-{self.images:03d}.png"
                cmd += ["--render-collisions", str(xray_png)]
            if a.get("export_mjcf"):
                cmd += ["--export-mjcf", str(self.doc.with_suffix(".xml"))]
            env = {**os.environ, "PGBENCH_PGM": self.pgm_bin}
            try:
                r = subprocess.run(cmd, cwd=self.box, env=env, capture_output=True, text=True, timeout=110)
                if xray_png and xray_png.exists():
                    self.pending.append((f"MuJoCo collision X-ray ({xray_png.name})", xray_png))
                out = (r.stdout or "").strip()
                if r.returncode != 0:
                    return f"physics_check error: {(r.stderr or out)[-1200:]}"
                return out[-4000:]
            except Exception as e:
                return f"physics_check failed: {e!r}"
        if name == "finish":
            built, out = self.do_build()
            ok, text = self.verify_report() if built else (False, "build failed\n" + out[-2000:])
            if not ok and built:
                import local_repair
                lr_res = local_repair.try_local_repair(self.box, self.module, self.contract, self.pgm_bin, max_evals=12)
                if lr_res.get("patched"):
                    self.local_repair = lr_res
                    self.snapshot_good()
                    ok, text = self.verify_report()
            if not ok and not self.finish_refused and self.calls < MAX_CALLS - 2:
                self.finish_refused = True
                return "finish refused once: the module is not valid yet. Fix it, then call finish again.\n" + text
            # One-shot craft-richness nudge when --anatomy/--mujoco is active so models don't stop at a bare "safe box"!
            mj_or_an = (os.environ.get("PGBENCH_MUJOCO", "0").lower() in ("1", "true", "yes")
                        or os.environ.get("PGBENCH_ANATOMY", "0").lower() in ("1", "true", "yes"))
            if ok and mj_or_an and self.last_craft_gaps and not self.craft_nudged and self.calls < MAX_CALLS - 8:
                self.craft_nudged = True
                return ("finish nudged once (craft richness): geometry & contract are valid, but you stopped early "
                        "before completing Tier 2/3 Victorian craft:\n  - " + "\n  - ".join(self.last_craft_gaps)
                        + "\nAdd these secondary mechanicals / Frontier SDS castings / post-SDS details while keeping "
                          "physics=100/100, preview once, then call `finish` again.\n" + text)
            (self.box / "NOTES.md").write_text(a.get("notes") or "")
            self.done = True
            return "finished"
        return f"unknown tool {name}"

    # ---------------------------------------------------------------- plateau
    def maybe_score(self, force=False):
        if not self.plateau or not self.builder.exists():
            return
        text = self.builder.read_text()
        first = not self.scores
        if text == self.last_scored_text or (not force and not first and self.calls - self.last_scored_call < SCORE_EVERY):
            return
        if not self.pgmj("check", self.doc).get("passed"):
            return
        if CRITIC.startswith("decision:"):
            import decision_gate
            imgs = {}
            for view, tag in (("iso", "iso-beauty"), ("opposite", "opposite-beauty")):
                png = self.box / ".bench" / f"score-{view}.png"
                self.run([self.pgm_bin, "render", str(self.doc), "--out", str(png), "--view", view, "--pass", "beauty", "--size", "640x480"])
                if png.exists():
                    imgs[tag] = png
            r = decision_gate.call_decisions(CRITIC.split(":", 1)[1], self.brief, imgs, key=self.key)
            if "error" in r or r.get("mean_score") is None:
                self.log.write(json.dumps({"score_error": r.get("error", "no score")[:300]}) + "\n")
                return
            mean = r["mean_score"]
            got = {"why": f"defects={r.get('defects')}"}
            self.critic_cost += r.get("cost") or 0
        else:
            content = [{"type": "text", "text": "THE BRIEF\n\n" + self.brief + "\n\nTHE RENDERS:"}]
            for view in ("iso", "opposite"):
                png = self.box / ".bench" / f"score-{view}.png"
                self.run([self.pgm_bin, "render", str(self.doc), "--out", str(png), "--view", view, "--pass", "beauty", "--size", "640x480"])
                if png.exists():
                    content += [{"type": "text", "text": view}, {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()}}]
            import calibrate
            body = {"model": CRITIC, "max_tokens": 600, "temperature": 0, "usage": {"include": True}, "reasoning": {"effort": "low"},
                    "messages": [{"role": "system", "content": calibrate.RUBRIC}, {"role": "user", "content": content}]}
            req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    resp = json.loads(r.read())
                got = json.loads(re.search(r"\{.*\}", resp["choices"][0]["message"]["content"], re.S).group(0))
                mean = sum(float(got[c]) for c in ("reads", "fidelity", "craft", "overall")) / 4
                self.critic_cost += (resp.get("usage") or {}).get("cost") or 0
            except Exception as e:
                self.log.write(json.dumps({"score_error": repr(e)[:300]}) + "\n")
                return
        self.last_scored_text, self.last_scored_call = text, self.calls
        self.scores.append((self.calls, mean, got.get("why", "")))
        if self.best is None or mean > self.best[0]:
            self.best = (mean, text)
            (self.box / ".bench" / "best.py").write_text(text)
        self.log.write(json.dumps({"score": mean, "calls": self.calls, "why": got.get("why", "")}) + "\n")

    def plateaued(self):
        if not self.plateau or self.calls < MIN_CALLS or len(self.scores) < PLATEAU_N + 1:
            return False
        best_before = max(m for _, m, _ in self.scores[:-PLATEAU_N])
        return all(m < best_before + PLATEAU_GAIN for _, m, _ in self.scores[-PLATEAU_N:])

    # ---------------------------------------------------------------- model
    def effective_effort(self):
        if self.effort == "adaptive" or os.environ.get("PGBENCH_ADAPTIVE_EFFORT") == "1":
            if self.turns <= 2 or not self.last_verify_ok:
                return "high"
            return "medium"
        return self.effort

    def chat(self, messages):
        msgs = messages
        if self.pending:      # images ride on this request only; the stored history never holds them
            import diag
            content = []
            for caption, png in self.pending[-4:]:
                content += [{"type": "text", "text": caption},
                            {"type": "image_url", "image_url": {"url": diag.encode_image_data_url(Path(png))}}]
            msgs = messages + [{"role": "user", "content": content}]
            self.pending = []
        active_tools = TOOLS + ([PHYSICS_TOOL] if os.environ.get("PGBENCH_MUJOCO", "0").lower() in ("1", "true", "yes") else [])
        body = {"model": self.model, "messages": msgs, "usage": {"include": True}, "max_tokens": 96000,
                "tools": [{"type": "function", "function": t} for t in active_tools]}
        eff = self.effective_effort()
        if eff:
            body["reasoning"] = {"effort": eff}
        req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        last = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    resp = json.loads(r.read())
                if "choices" not in resp:
                    raise RuntimeError(json.dumps(resp)[:400])
                return resp
            except Exception as e:
                last = e
                time.sleep(10 * (attempt + 1))
        raise RuntimeError(f"model call failed: {last!r}")

    def account(self, u):
        self.usage["prompt_tokens"] += u.get("prompt_tokens") or 0
        self.usage["completion_tokens"] += u.get("completion_tokens") or 0
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        self.usage["cached_tokens"] += cached
        self.usage["reasoning_tokens"] += (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        cost = u.get("cost")
        if cost is None:       # some providers omit it: price the tokens instead
            import bench
            cost = bench.token_cost(self.model, {"input_tokens": (u.get("prompt_tokens") or 0) - cached,
                                                 "cache_read_input_tokens": cached, "output_tokens": u.get("completion_tokens") or 0}) or 0
        self.cost += cost

    def loop(self, prompt):
        sys_prompt = SYSTEM + (MUJOCO_PROMPT if os.environ.get("PGBENCH_MUJOCO", "0").lower() in ("1", "true", "yes") else "")
        messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": prompt}]
        turns, exit_reason = 0, "max_calls"
        while self.calls < MAX_CALLS and time.time() - self.t0 < TIMEOUT_S and not self.done:
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
                fin_reason = resp["choices"][0].get("finish_reason")
                if fin_reason == "length":
                    messages.append({"role": "user", "content": "Your previous response hit the output token limit before completing a tool call. "
                                     "Write `recipes/build_bench_kit.py` incrementally (skeleton first with `write_file`, then refine parts with `edit_file`) "
                                     "rather than emitting one massive file in a single turn." + self.budget()})
                else:
                    messages.append({"role": "user", "content": "Continue with the tools, or call `finish` when the module is done." + self.budget()})
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
            elif turns == 34 and self.last_verify_ok and len(messages) > 20:
                # One-shot marathon stage compaction: trim old oversized tool outputs while keeping recent context intact
                for m in messages[2:-12]:
                    if m.get("role") == "tool" and len(m.get("content") or "") > 900:
                        m["content"] = m["content"][:320] + "\n... [earlier tool output compacted after valid checkpoint]"
            elif self.plateaued() and not self.stop_asked:
                self.stop_asked, self.stop_call = True, self.calls
                messages.append({"role": "user", "content": "The module has stopped improving. Make sure it passes `verify`, "
                                 "then call `finish` now with your notes." + self.budget()})
            elif self.stop_asked and self.calls - self.stop_call > 6:
                exit_reason = "plateau_stop"
                break
        if time.time() - self.t0 >= TIMEOUT_S:
            exit_reason = "timeout"
        restored = False
        if self.plateau:
            if self.do_build()[0]:
                self.maybe_score(force=True)                 # score the final version before choosing
            if self.best and self.builder.exists():
                final = self.builder.read_text()
                final_score = self.scores[-1][1] if self.scores and final == self.last_scored_text else None
                if final != self.best[1] and (final_score is None or final_score < self.best[0]):
                    self.builder.write_text(self.best[1])
                    restored = True
        res = {"exit": 0 if exit_reason in ("finished", "plateau_stop") else exit_reason, "exit_reason": exit_reason,
               "num_turns": turns, "tool_calls": self.calls, "renders_viewed": self.images, "usage": self.usage,
               "cost_usd_openrouter": round(self.cost, 4), "cost_usd_token_estimate": round(self.cost, 4),
               "wall_s": round(time.time() - self.t0, 1), "harness": "lean-plateau" if self.plateau else "lean",
               "progressive": bool(self.progressive), "outside_paths": [], "scores": self.scores,
               "critic_cost_usd": round(self.critic_cost, 4), "best_restored": restored,
               "stop_asked_at_call": self.stop_call, "finish_refused": self.finish_refused}
        if self.local_repair and self.local_repair.get("patched"):
            res["local_repair"] = self.local_repair
        return res


def run(box, module, model_id, effort, key, prompt, out_dir, plateau=False, brief="", contract=None, progressive=False, mini=False, spec=None, anatomy_spec=None):
    out_dir = Path(out_dir)
    use_mini = bool(mini or os.environ.get("PGBENCH_MINI_MODE", "0").lower() in ("1", "true", "yes"))
    if use_mini:
        import mini_mode
        return mini_mode.run_mini(
            box, module, model_id, key, prompt, out_dir,
            effort=effort, brief=brief, contract=contract,
            seed_scaffold=bool(spec), spec=spec, anatomy_spec=anatomy_spec,
        )
    with open(out_dir / "transcript.jsonl", "w") as log:
        s = Session(box, module, model_id, effort, key, log, plateau, brief, contract, out_dir=out_dir, progressive=progressive)
        try:
            return s.loop(prompt)
        except Exception as e:
            return {"exit": f"error: {e!r}"[:300], "num_turns": None, "tool_calls": s.calls, "renders_viewed": s.images,
                    "usage": s.usage, "cost_usd_openrouter": round(s.cost, 4), "cost_usd_token_estimate": round(s.cost, 4),
                    "wall_s": round(time.time() - s.t0, 1), "harness": "lean", "outside_paths": [],
                    "scores": s.scores, "critic_cost_usd": round(s.critic_cost, 4)}
