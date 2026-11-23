#!/usr/bin/env python3
"""Four-axis efficiency receipts for model-bench runs (inspired by @pierre/words benchmarks):
  1. Speed       — one-shot evaluation & .emesh pack latency + wall-seconds per turn
  2. Work        — cumulative turns, tool calls, reasoning tokens, and cost/turn to >= 4.50
  3. Structure   — unique congruent-deduped prototypes vs instances (catches unshared triangle soup)
  4. Wire        — .emesh binary pack byte size, bytes per placed triangle, and surface detail count

Usage:
  PGBENCH_DATE=2026-11-17 python3 bench/harness/efficiency.py handwheel-valve
  PGBENCH_DATE=2026-11-17 python3 bench/harness/efficiency.py handwheel-valve gemini-4-carbon gemini-4-neon-medium--LH-r1
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("PGBENCH_DATE", "2026-11-17")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402


def measure_build_pack(task: str, run_dir: Path) -> dict | None:
    """Build `run_dir/build.py` in a temporary scratch directory (or reuse doc json if cached)
    and measure `pgm evaluate` + `pgm export --emesh` stats."""
    build_py = run_dir / "build.py"
    if not build_py.exists():
        return None

    spec_path = bench.BENCH / "tasks" / task / "task.json"
    spec = json.loads(spec_path.read_text())
    pgm = bench.PROCGEN / "build-release" / "pgm"
    if not pgm.exists():
        return None

    tmp_root = Path("/private/var/folders/z5/6rm_mqdx6nz17tgp5kwpksb80000gn/T/opencode")
    if not tmp_root.exists():
        tmp_root = Path(tempfile.gettempdir())
    work = Path(tempfile.mkdtemp(prefix="pgeff_", dir=str(tmp_root)))
    try:
        # Minimal recipe tree so kitlib + moduleImports resolve cleanly
        shutil.copytree(bench.PROCGEN / "recipes", work / "recipes")
        (work / "recipes" / "site" / "bench").mkdir(parents=True, exist_ok=True)
        (work / "recipes" / "site" / "bench" / "modules.json").write_text(
            json.dumps({"name": "bench", "imports": spec.get("imports", ["../../arch"]), "modules": {}})
        )
        shutil.copy2(build_py, work / "recipes" / "build_bench_kit.py")
        r_build = subprocess.run(
            ["python3", "recipes/build_bench_kit.py"],
            cwd=work, capture_output=True, text=True, timeout=30
        )
        doc_path = work / "recipes" / "site" / "bench" / f"{spec['module']}.json"
        if r_build.returncode != 0 or not doc_path.exists():
            return {"build_error": (r_build.stderr or r_build.stdout)[:300]}

        t0 = time.perf_counter()
        r_ev = subprocess.run(
            [str(pgm), "evaluate", str(doc_path)],
            cwd=work, capture_output=True, text=True, timeout=30
        )
        eval_wall_ms = (time.perf_counter() - t0) * 1000.0
        ev = json.loads(r_ev.stdout) if r_ev.returncode == 0 else {}

        emesh_file = work / "out.emesh"
        t1 = time.perf_counter()
        r_ex = subprocess.run(
            [str(pgm), "export", str(doc_path), "--emesh", str(emesh_file)],
            cwd=work, capture_output=True, text=True, timeout=30
        )
        pack_wall_ms = (time.perf_counter() - t1) * 1000.0
        ex = (json.loads(r_ex.stdout).get("emesh") or {}) if r_ex.returncode == 0 else {}

        # If the root output node is `bake`, also measure pre-bake prototype reuse & unbaked .emesh size!
        doc_json = json.loads(doc_path.read_text())
        out_id = doc_json.get("output")
        nodes_by_id = {n.get("id"): n for n in (doc_json.get("nodes") or []) if isinstance(n, dict)}
        root_node = nodes_by_id.get(out_id) or {}
        is_baked = root_node.get("type") == "bake" and bool(root_node.get("inputs"))
        prebake = None
        if is_baked:
            unbaked_doc = dict(doc_json)
            unbaked_doc["output"] = root_node["inputs"][0]
            unbaked_path = work / "recipes" / "site" / "bench" / f"{spec['module']}_unbaked.json"
            unbaked_path.write_text(json.dumps(unbaked_doc))
            unbaked_emesh = work / "out_unbaked.emesh"
            r_u = subprocess.run(
                [str(pgm), "export", str(unbaked_path), "--emesh", str(unbaked_emesh)],
                cwd=work, capture_output=True, text=True, timeout=30
            )
            if r_u.returncode == 0:
                uex = json.loads(r_u.stdout).get("emesh") or {}
                prebake = {
                    "deduped_meshes": uex.get("meshes"),
                    "source_prototypes": uex.get("sourcePrototypes"),
                    "instances": uex.get("instances"),
                    "prototype_vertices": uex.get("vertices"),
                    "emesh_bytes": uex.get("bytes"),
                }

        return {
            "eval_ms": round(float(ev.get("evaluationMS") or eval_wall_ms), 2),
            "pack_ms": round(pack_wall_ms, 2),
            "evaluated_nodes": ev.get("evaluatedNodes"),
            "baked_output": is_baked,
            "source_prototypes": ex.get("sourcePrototypes", ev.get("prototypes")),
            "deduped_meshes": ex.get("meshes", ev.get("prototypes")),
            "instances": ex.get("instances", ev.get("instances")),
            "batches": ex.get("batches"),
            "prototype_vertices": ex.get("vertices", ev.get("prototypeVertices")),
            "placed_triangles": ex.get("placedTriangles", ev.get("placedTriangles")),
            "details": ex.get("details", ev.get("placedDetails", 0)),
            "two_sided_meshes": ex.get("twoSidedMeshes", 0),
            "emesh_bytes": ex.get("bytes"),
            "prebake": prebake,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def compute_run_efficiency(task: str, slug: str, date: str | None = None, persist: bool = True, force: bool = False) -> dict:
    d = date or bench.TODAY
    run_dir = bench.BENCH / "runs" / d / task / slug
    rec_path = run_dir / "receipt.json"
    if not rec_path.exists():
        raise FileNotFoundError(f"Missing receipt: {rec_path}")
    rec = json.loads(rec_path.read_text())

    cached = rec.get("efficiency")
    if not force and cached and cached.get("wire", {}).get("emesh_bytes") is not None and "baked_output" in cached.get("structure", {}):
        return cached
    pack = measure_build_pack(task, run_dir) or {}

    ckpt_idx = run_dir / "checkpoints" / "index.jsonl"
    ckpts = [json.loads(l) for l in ckpt_idx.read_text().splitlines() if l.strip()] if ckpt_idx.exists() else []
    valid_ckpts = [c for c in ckpts if not c.get("hard_misfits") and not c.get("build_failed")]
    hit_45 = [c for c in valid_ckpts if (c.get("funnel_score") or 0) >= 4.50]

    turns = rec.get("num_turns") or 0
    wall_s = rec.get("wall_s") or 0.0
    cost_usd = rec.get("cost_usd_openrouter") or rec.get("cost_usd_token_estimate") or 0.0
    usage = rec.get("usage") or {}

    meshes = pack.get("deduped_meshes") or rec.get("prototypes") or 0
    insts = pack.get("instances") or rec.get("instances") or 0
    tris = pack.get("placed_triangles") or rec.get("triangles") or 0
    ebytes = pack.get("emesh_bytes")
    pre = pack.get("prebake") or {}
    auth_meshes = pre.get("deduped_meshes") or meshes
    auth_insts = pre.get("instances") or insts
    auth_reuse = round(auth_insts / auth_meshes, 2) if auth_meshes else 0.0
    unbaked_bytes = pre.get("emesh_bytes") if pre else ebytes
    bpt = round(ebytes / tris, 2) if (ebytes and tris) else None

    eff = {
        "speed": {
            "eval_ms": pack.get("eval_ms"),
            "pack_ms": pack.get("pack_ms"),
            "sec_per_turn": round(wall_s / turns, 2) if turns else None,
        },
        "work": {
            "turns": turns,
            "tool_calls": rec.get("tool_calls"),
            "wall_s": wall_s,
            "cost_usd": round(float(cost_usd), 4) if cost_usd is not None else None,
            "reasoning_tokens": usage.get("reasoning_tokens"),
            "valid_checkpoints": len(valid_ckpts),
            "first_valid_turn": valid_ckpts[0]["turn"] if valid_ckpts else None,
            "turn_to_4_50": hit_45[0]["turn"] if hit_45 else None,
            "cost_to_4_50": hit_45[0]["cost_usd"] if hit_45 else None,
            "wall_to_4_50": hit_45[0]["wall_s"] if hit_45 else None,
        },
        "structure": {
            "evaluated_nodes": pack.get("evaluated_nodes") or rec.get("evaluated_nodes"),
            "baked_output": bool(pack.get("baked_output")),
            "deduped_meshes": meshes,
            "instances": insts,
            "batches": pack.get("batches"),
            "authoring_prototypes": auth_meshes,
            "authoring_instances": auth_insts,
            "instance_reuse_ratio": auth_reuse,
            "prototype_vertices": pre.get("prototype_vertices") or pack.get("prototype_vertices"),
            "placed_triangles": tris,
            "two_sided_meshes": pack.get("two_sided_meshes", 0),
            "unshared_soup_warning": bool(tris > 25000 and auth_reuse < 1.5),
        },
        "wire": {
            "emesh_bytes": ebytes,
            "emesh_kb": round(ebytes / 1024.0, 1) if ebytes is not None else None,
            "unbaked_emesh_kb": round(unbaked_bytes / 1024.0, 1) if unbaked_bytes is not None else None,
            "bytes_per_placed_triangle": bpt,
            "details": pack.get("details", 0),
        },
    }
    if persist:
        rec["efficiency"] = eff
        rec_path.write_text(json.dumps(rec, indent=1) + "\n")
    return eff


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: efficiency.py <task> [slug ...]")
    task = sys.argv[1]
    tdir = bench.BENCH / "runs" / bench.TODAY / task
    slugs = sys.argv[2:] or sorted(p.name for p in tdir.iterdir() if (p / "receipt.json").exists())
    print(f"{'slug':32} {'turns':>5} {'t>=4.5':>6} {'cost':>7} {'evalMS':>7} {'protos':>6} {'insts':>6} {'reuse':>6} {'tris':>7} {'unbakKB':>8} {'packKB':>8} {'soup?':>6}")
    print("-" * 120)
    for slug in slugs:
        e = compute_run_efficiency(task, slug)
        sp, wk, st, wr = e["speed"], e["work"], e["structure"], e["wire"]
        t45 = str(wk["turn_to_4_50"]) if wk["turn_to_4_50"] is not None else "-"
        cst = f"${wk['cost_usd']:.2f}" if wk["cost_usd"] is not None else "-"
        ems = f"{sp['eval_ms']:.1f}" if sp["eval_ms"] is not None else "-"
        ukb = f"{wr['unbaked_emesh_kb']:.1f}" if wr["unbaked_emesh_kb"] is not None else "-"
        ekb = f"{wr['emesh_kb']:.1f}" if wr["emesh_kb"] is not None else "-"
        soup = "WARN" if st["unshared_soup_warning"] else "ok"
        print(f"{slug:32} {wk['turns']:5d} {t45:>6} {cst:>7} {ems:>7} {st['authoring_prototypes']:6d} {st['authoring_instances']:6d} {st['instance_reuse_ratio']:6.2f} {st['placed_triangles']:7d} {ukb:>8} {ekb:>8} {soup:>6}")


if __name__ == "__main__":
    main()
