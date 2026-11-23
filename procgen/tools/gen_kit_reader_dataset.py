#!/usr/bin/env python3
"""Exact-Ground-Truth Synthetic Multimodal Dataset Factory for `kit-reader-8b` & `craft-critic-8b`.

Mirrors Voyager's `shot-reader-8b` synthetic procedural data recipe (Qwen3-VL-8B-Instruct + LoRA r64),
using our own 170+ parametric `procgen` modules where every 3D part bound, material slot, port,
control value, SDS topology metric, and MuJoCo physics label is known EXACTLY.

Generates two Qwen3-VL / HuggingFace multimodal JSONL datasets:
  1. `kit_reader.jsonl`:   [iso-beauty, opposite-beauty] -> Exact ground-truth ANATOMY_SPEC JSON
     (envelope, datums, parts with exact 3D bounds, materials, inferred FWN/SDS/open-bore workflows, ports, controls).
  2. `craft_critic.jsonl`: [iso-beauty, iso-matcap, iso-curvature] (+ optional mujoco-xray) ->
     Exact ground-truth topology, curvature & MuJoCo physical-realism audit JSON.

Usage:
  python3 tools/gen_kit_reader_dataset.py \\
      --kits powerhouse,waterworks,steelworks,machinery \\
      --samples-per-module 2 --size 512x384 \\
      --out /private/var/folders/.../kit_reader_dataset [--with-mujoco]
"""
import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "recipes" / "site"
PGM = Path(os.environ.get("PGM_BIN", ROOT / "build-release" / "pgm"))
MUJOCO_AUDIT = ROOT.parent / "bench" / "harness" / "mujoco_audit.py"

KIT_READER_SYSTEM = (
    "You are Kit Reader, a 3D mechanical & architectural reverse-engineering vision model. "
    "Given views of a procedural 3D asset, output its exact ground-truth ANATOMY_SPEC JSON "
    "(envelope_m, materials, ports, controls, SDS/FWN topology profile, and per-part 3D bounding boxes)."
)

CRAFT_CRITIC_SYSTEM = (
    "You are Craft Critic, a 3D hard-surface topology & physics auditor. "
    "Given beauty, matcap, and curvature renders of a 3D module, output its exact quantitative "
    "topology, curvature-fairness, and physical-realism audit JSON."
)


def parse_obj_part_bounds(obj_path: Path):
    """Parse unbaked OBJ into exact per-part {id, material, bounds_m, size_m, triangles}."""
    verts = []
    groups = []
    cur_name, cur_mat, cur_v = "part", "default", set()
    cur_tris = 0

    def flush():
        nonlocal cur_tris
        if cur_v:
            xs = [verts[i][0] for i in cur_v]
            ys = [verts[i][1] for i in cur_v]
            zs = [verts[i][2] for i in cur_v]
            lo = [round(min(xs), 4), round(min(ys), 4), round(min(zs), 4)]
            hi = [round(max(xs), 4), round(max(ys), 4), round(max(zs), 4)]
            sz = [round(hi[k] - lo[k], 4) for k in range(3)]
            groups.append({
                "id": cur_name,
                "material": cur_mat,
                "bounds_m": [lo, hi],
                "size_m": sz,
                "triangles": cur_tris,
            })
            cur_v.clear()
            cur_tris = 0

    with obj_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split()
                verts.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("o "):
                flush()
                cur_name = line[2:].strip() or "part"
            elif line.startswith("usemtl "):
                cur_mat = line[7:].strip() or "default"
            elif line.startswith("f "):
                toks = line[2:].strip().split()
                idxs = [int(t.split("/")[0]) - 1 for t in toks]
                cur_v.update(idxs)
                cur_tris += max(1, len(idxs) - 2)
    flush()
    return groups


def infer_workflows(doc_raw: dict) -> dict:
    """Extract ground-truth hard-surface workflow tags directly from the module's node graph."""
    nodes = doc_raw.get("nodes") or []
    ntypes = {n.get("type") for n in nodes if isinstance(n, dict)}
    subdiv_opts = set()
    for n in nodes:
        if isinstance(n, dict) and n.get("type") == "subdivide":
            opt = n.get("options") or {}
            for k in ("nonUniform", "interpolate", "subdivNormals", "g2Fair", "tunedEV", "rational", "adaptive", "edits"):
                if opt.get(k):
                    subdiv_opts.add(k)
    return {
        "has_sds": "subdivide" in ntypes,
        "has_fwn_bevel": "chamfer" in ntypes and "smoothNormals" in ntypes,
        "has_loft": "loft" in ntypes,
        "has_bridge_loops": "bridgeLoops" in ntypes,
        "has_quad_fill_hole": "quadFillHole" in ntypes,
        "has_transfer_normals": "transferNormals" in ntypes,
        "has_displace_details": "displaceDetails" in ntypes,
        "has_surface_details": bool(ntypes & {"detailStamp", "fastener", "rivetLine", "detailStroke", "detailSeam", "detailText"}),
        "frontier_sds_flags": sorted(subdiv_opts),
    }


def unbaked_copy(doc_path: Path, tmp_dir: Path) -> Path:
    raw = json.loads(doc_path.read_text())
    nodes = {n["id"]: n for n in raw.get("nodes", []) if isinstance(n, dict) and "id" in n}
    out_id = raw.get("output")
    cur = nodes.get(out_id)
    if cur and cur.get("type") == "bake" and cur.get("inputs"):
        raw["output"] = cur["inputs"][0]
        if "moduleImports" in raw and isinstance(raw["moduleImports"], list):
            base = doc_path.parent
            raw["moduleImports"] = [str((base / p).resolve()) if not Path(p).is_absolute() else p for p in raw["moduleImports"]]
        dst = tmp_dir / doc_path.name
        dst.write_text(json.dumps(raw))
        return dst
    return doc_path


def sample_control_sets(doc_raw: dict, count: int, rng: random.Random):
    """Yield `count` control dicts: first the default setting, then random interior/extreme settings."""
    ctrls = doc_raw.get("controls") or {}
    yield {}
    if not ctrls or count <= 1:
        return
    for _ in range(count - 1):
        s = {}
        for k, spec in ctrls.items():
            if not isinstance(spec, dict) or "min" not in spec or "max" not in spec:
                continue
            lo, hi = float(spec["min"]), float(spec["max"])
            if spec.get("integer"):
                s[k] = rng.randint(int(round(lo)), int(round(hi)))
            else:
                s[k] = round(rng.uniform(lo, hi), 3)
        yield s


def run_json(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="Generate exact-ground-truth Qwen3-VL LoRA dataset from procgen kits")
    ap.add_argument("--kits", default="powerhouse,waterworks,steelworks,machinery",
                    help="Comma-separated kit names under recipes/site/ (or 'all')")
    ap.add_argument("--samples-per-module", type=int, default=2, help="Control variations per module (default: 2)")
    ap.add_argument("--size", default="512x384", help="Render resolution WxH (default: 512x384)")
    ap.add_argument("--passes", default="beauty,matcap,curvature", help="Render passes to capture")
    ap.add_argument("--with-mujoco", action="store_true", help="Include exact MuJoCo physics ground truth")
    ap.add_argument("--include-bench-runs", type=Path, default=None,
                    help="Optional model-bench runs directory (harvests real agent successes & failure modes into craft_critic.jsonl)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, required=True, help="Output directory for images + JSONL files")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    passes = [p.strip() for p in args.passes.split(",") if p.strip()]
    kit_names = sorted(d.name for d in SITE.iterdir() if d.is_dir()) if args.kits == "all" else [k.strip() for k in args.kits.split(",") if k.strip()]

    out_dir = args.out.resolve()
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    reader_rows, critic_rows = [], []

    for kit in kit_names:
        kdir = SITE / kit
        if not kdir.is_dir():
            continue
        docs = sorted(p for p in kdir.glob("*.json") if p.name != "modules.json")
        for doc_path in docs:
            doc_raw = json.loads(doc_path.read_text())
            if "nodes" not in doc_raw:
                continue
            workflows = infer_workflows(doc_raw)
            for si, cset in enumerate(sample_control_sets(doc_raw, args.samples_per_module, rng)):
                tag = f"{kit}__{doc_path.stem}__s{si}"
                set_args = [x for k, v in cset.items() for x in ("--set", f"{k}={v}")]

                chk = run_json([str(PGM), "check", str(doc_path), *set_args])
                ins = run_json([str(PGM), "inspect", str(doc_path), *set_args])
                ev = run_json([str(PGM), "evaluate", str(doc_path), *set_args])
                if not (chk and chk.get("passed") and ins and ev):
                    print(f"  skip {tag} (check/inspect failed)")
                    continue

                # Export unbaked OBJ for exact per-part 3D bounding boxes.
                with tempfile.TemporaryDirectory(prefix="kr_obj_") as td:
                    ub = unbaked_copy(doc_path, Path(td))
                    obj_p = Path(td) / "parts.obj"
                    r_exp = subprocess.run([str(PGM), "export", str(ub), "--obj", str(obj_p), *set_args],
                                           capture_output=True, text=True)
                    parts = parse_obj_part_bounds(obj_p) if r_exp.returncode == 0 and obj_p.exists() else []

                # Render ground-truth views.
                rendered = {}
                for view in ("iso", "opposite"):
                    for pname in passes:
                        if view == "opposite" and pname != "beauty":
                            continue
                        rel = f"images/{tag}_{view}_{pname}.png"
                        abs_p = out_dir / rel
                        rr = subprocess.run([
                            str(PGM), "render", str(doc_path), "--view", view, "--pass", pname,
                            "--size", args.size, "--out", str(abs_p), *set_args
                        ], capture_output=True, text=True)
                        if rr.returncode == 0 and abs_p.exists():
                            rendered[f"{view}_{pname}"] = rel

                if "iso_beauty" not in rendered:
                    continue

                # Optional exact MuJoCo physics ground truth.
                phys = None
                if args.with_mujoco and MUJOCO_AUDIT.exists() and not cset:
                    mj_r = subprocess.run([
                        "uv", "run", "--with", "mujoco", "--with", "numpy", "--with", "coacd", "--with", "pillow",
                        "python3", str(MUJOCO_AUDIT), str(doc_path), "--fast"
                    ], env={**os.environ, "PGBENCH_PGM": str(PGM)}, capture_output=True, text=True, timeout=45)
                    if mj_r.returncode == 0 and mj_r.stdout.strip():
                        try:
                            phys = json.loads(mj_r.stdout)
                        except Exception:
                            phys = None

                b_lo, b_hi = ev["boundsZUp"]
                envelope = [round(b_hi[k] - b_lo[k], 4) for k in range(3)]
                # Consolidate parts cleanly (top 24 largest parts by bounding volume so JSON targets stay compact & learnable).
                parts.sort(key=lambda p: -(p["size_m"][0] * p["size_m"][1] * p["size_m"][2]))

                anatomy_target = {
                    "kit": kit,
                    "module": doc_path.stem,
                    "controls_applied": cset,
                    "envelope_m": envelope,
                    "bounds_z_up": [[round(v, 4) for v in b_lo], [round(v, 4) for v in b_hi]],
                    "materials": [m["slot"] for m in ins.get("materials", [])],
                    "ports": [
                        {"name": p["name"], "type": p["type"],
                         "origin": [round(v, 4) for v in p["origin"]],
                         "normal": [round(v, 3) for v in p["normal"]]}
                        for p in ins.get("ports", [])
                    ],
                    "workflows": workflows,
                    "parts": parts[:24],
                }

                # Row 1: Kit-Reader (beauty views -> exact 3D Anatomy Spec JSON)
                kr_imgs = [rendered["iso_beauty"]] + ([rendered["opposite_beauty"]] if "opposite_beauty" in rendered else [])
                reader_rows.append({
                    "id": tag,
                    "images": kr_imgs,
                    "messages": [
                        {"role": "system", "content": KIT_READER_SYSTEM},
                        {"role": "user", "content": [{"type": "image"} for _ in kr_imgs] + [
                            {"type": "text", "text": f"Reverse-engineer this 3D asset ({kit}/{doc_path.stem}) into its exact ANATOMY_SPEC JSON."}
                        ]},
                        {"role": "assistant", "content": json.dumps(anatomy_target)},
                    ],
                })

                # Row 2: Craft-Critic (beauty + matcap + curvature -> exact topology/curvature/physics audit JSON)
                sds = chk.get("sds") or {}
                critic_target = {
                    "module": doc_path.stem,
                    "clean_watertight": bool(chk.get("clean")),
                    "placed_triangles": ev.get("placedTriangles", 0),
                    "quad_ratio": round(float(sds.get("quadRatio", 0.0)), 4),
                    "sds_cages": sds.get("sdsCages", 0),
                    "multi_lod_prototypes": sds.get("multiLodPrototypes", 0),
                    "high_valence_poles": sds.get("highValencePoles", 0),
                    "pinched_poles": sds.get("pinchedPoles", 0),
                    "max_ev_curvature_rad": round(float(sds.get("maxEvCurvature", 0.0)), 4),
                    "workflows": workflows,
                }
                if phys:
                    critic_target["physics"] = {
                        "score": phys.get("physicsScore"),
                        "mass_kg": phys.get("totalMassKg"),
                        "overturning_sf": phys.get("overturningSafetyFactor"),
                        "buried": phys.get("buriedOrDuplicate"),
                        "cross_clips": phys.get("crossMaterialClips"),
                        "same_clips": phys.get("sameMaterialClips"),
                        "top_intersections": (phys.get("topIntersections") or [])[:3],
                    }
                cc_imgs = [rendered[k] for k in ("iso_beauty", "iso_matcap", "iso_curvature") if k in rendered]
                critic_rows.append({
                    "id": tag,
                    "images": cc_imgs,
                    "messages": [
                        {"role": "system", "content": CRAFT_CRITIC_SYSTEM},
                        {"role": "user", "content": [{"type": "image"} for _ in cc_imgs] + [
                            {"type": "text", "text": "Audit this module's SDS topology, surface curvature fairness, and mechanical realism as JSON."}
                        ]},
                        {"role": "assistant", "content": json.dumps(critic_target)},
                    ],
                })
                print(f"  ok {tag:42s} parts={len(parts):2d} quads={sds.get('quadRatio',0)*100:5.1f}% phys={phys.get('physicsScore') if phys else '-'}")

    # Optional: harvest real model-bench agent runs (both failure modes like buried nuts / jammed spin AND 100/100 successes!)
    bench_harvested = 0
    if args.include_bench_runs and args.include_bench_runs.exists():
        for rec_p in sorted(args.include_bench_runs.rglob("receipt.json")):
            rdir = rec_p.parent
            try:
                rec = json.loads(rec_p.read_text())
            except Exception:
                continue
            out_docs = list((rdir / "out").glob("*.json")) if (rdir / "out").is_dir() else []
            iso_b = rdir / "renders" / "iso-beauty.png"
            if not out_docs or not iso_b.exists():
                continue
            doc_path = out_docs[0]
            slug_tag = f"bench__{rec.get('task', 'task')}__{rdir.name}"
            # Copy beauty + render matcap & curvature cleanly into dataset images dir
            cc_imgs = []
            dst_b = f"images/{slug_tag}_iso_beauty.png"
            (out_dir / dst_b).write_bytes(iso_b.read_bytes())
            cc_imgs.append(dst_b)
            for pname in ("matcap", "curvature"):
                rel = f"images/{slug_tag}_iso_{pname}.png"
                rr = subprocess.run([
                    str(PGM), "render", str(doc_path), "--view", "iso", "--pass", pname,
                    "--size", args.size, "--out", str(out_dir / rel)
                ], capture_output=True, text=True)
                if rr.returncode == 0 and (out_dir / rel).exists():
                    cc_imgs.append(rel)
            xray = rdir / "renders" / "mujoco-xray.png"
            if xray.exists():
                dst_x = f"images/{slug_tag}_mujoco_xray.png"
                (out_dir / dst_x).write_bytes(xray.read_bytes())
                cc_imgs.append(dst_x)

            chk = run_json([str(PGM), "check", str(doc_path)]) or {}
            sds = chk.get("sds") or {}
            phys = rec.get("physics")
            if not phys and args.with_mujoco and MUJOCO_AUDIT.exists():
                mj_r = subprocess.run([
                    "uv", "run", "--with", "mujoco", "--with", "numpy", "--with", "coacd", "--with", "pillow",
                    "python3", str(MUJOCO_AUDIT), str(doc_path), "--fast"
                ], env={**os.environ, "PGBENCH_PGM": str(PGM)}, capture_output=True, text=True, timeout=45)
                if mj_r.returncode == 0 and mj_r.stdout.strip():
                    try:
                        phys = json.loads(mj_r.stdout)
                    except Exception:
                        phys = None
            target = {
                "source": "model-bench",
                "task": rec.get("task"),
                "slug": rdir.name,
                "valid": bool(rec.get("valid")),
                "clean_watertight": bool(chk.get("clean")),
                "quad_ratio": round(float(sds.get("quadRatio", 0.0)), 4),
                "sds_cages": sds.get("sdsCages", 0),
                "pinched_poles": sds.get("pinchedPoles", 0),
                "max_ev_curvature_rad": round(float(sds.get("maxEvCurvature", 0.0)), 4),
            }
            if isinstance(phys, dict) and "physicsScore" in phys:
                sp = phys.get("spinTest") or {}
                target["physics"] = {
                    "score": phys.get("physicsScore"),
                    "buried": phys.get("buriedOrDuplicate"),
                    "cross_clips": phys.get("crossMaterialClips"),
                    "same_clips": phys.get("sameMaterialClips"),
                    "spin_deg": sp.get("rotationDeg"),
                    "free_spin": sp.get("freeSpin"),
                    "top_intersections": (phys.get("topIntersections") or [])[:4],
                }
            critic_rows.append({
                "id": slug_tag,
                "images": cc_imgs,
                "messages": [
                    {"role": "system", "content": CRAFT_CRITIC_SYSTEM},
                    {"role": "user", "content": [{"type": "image"} for _ in cc_imgs] + [
                        {"type": "text", "text": "Audit this module's SDS topology, surface curvature fairness, and mechanical realism as JSON."}
                    ]},
                    {"role": "assistant", "content": json.dumps(target)},
                ],
            })
            bench_harvested += 1
            print(f"  bench {slug_tag:46s} valid={target['valid']} phys={(target.get('physics') or {}).get('score', '-')}")

    (out_dir / "kit_reader.jsonl").write_text("\n".join(json.dumps(r) for r in reader_rows) + ("\n" if reader_rows else ""))
    (out_dir / "craft_critic.jsonl").write_text("\n".join(json.dumps(r) for r in critic_rows) + ("\n" if critic_rows else ""))
    manifest = {
        "kits": kit_names,
        "kit_reader_samples": len(reader_rows),
        "craft_critic_samples": len(critic_rows),
        "bench_runs_harvested": bench_harvested,
        "resolution": args.size,
        "passes": passes,
        "with_mujoco": bool(args.with_mujoco),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Dataset complete:", json.dumps(manifest))


if __name__ == "__main__":
    main()
