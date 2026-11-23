#!/usr/bin/env python3
"""Zero-cost local procedural-kit judge powered by WeightCode-edited DINOv2 + multi-task ridge.

Usage:
  # Build & export the tiny WeightCode delta (wsp_delta_<arch>.pt) + judge_model_wsp.npz:
  python3 harness/local_judge.py build [--arch vitb14] [--program auto|a1|hy4]

  # Score any run locally for $0 (images + receipt term):
  PGBENCH_DATE=2026-11-17 python3 harness/local_judge.py score <task> <slug>
"""
import argparse
import gc
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS))
import bench       # noqa: E402
import calibrate   # noqa: E402
import weightcode_judge as wj  # noqa: E402

VIEWS = ("iso-beauty", "iso-clay", "opposite-beauty", "front-clay", "parts")
NPZ_PATH = HARNESS / "judge_model_wsp.npz"


def _apply_program(model, X_tr, M_tr, y_tr, t_tr, program: str):
    hy4_py = HARNESS / "wsp_best_hy4.py"
    if program == "hy4" or (program == "auto" and hy4_py.exists()):
        import importlib.util
        import hy4_weightcode_synth as hws
        spec = importlib.util.spec_from_file_location("wsp_best_hy4", hy4_py)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        helpers = hws.make_helpers()
        sd, chg = mod.weight_program(model, X_tr, M_tr, y_tr, t_tr, helpers)
        return sd, "wsp_best_hy4", int(chg)
    sd, info = wj.wsp_late_ls_damper(model, X_tr, M_tr, y_tr, t_tr, 0.04, 0.30, 0.18)
    return sd, "wsp_late_ls_damper", int(info["changed"])


def build_judge(arch: str = "vitb14", program: str = "auto", lam: float = 1000.0):
    """Export compact WSP weight patch (`wsp_delta_<arch>.pt`) and distilled ridge judge (`judge_model_wsp.npz`)."""
    data83, _ = wj.load_datasets()
    X83, M83, y83, t83 = data83

    m = wj.load_dinov2_local(arch)
    base_sd = {k: v.detach().cpu().clone() for k, v in m.state_dict().items()}

    # 1. Run WeightCode program on the 83 human-scored entries (3 tasks, zero gradients)
    new_sd, prog_name, _ = _apply_program(m, X83, M83, y83, t83, program)

    # Save ONLY modified tensors as a compact delta file (~6-12 KB for LayerScale!)
    patch = {}
    changed_entries = 0
    for k in base_sd:
        if not torch.equal(new_sd[k], base_sd[k]):
            patch[k] = new_sd[k].clone()
            changed_entries += int((new_sd[k] != base_sd[k]).sum().item())
    delta_path = HARNESS / f"wsp_delta_{arch}.pt"
    torch.save(patch, delta_path)
    print(f"Saved WeightCode delta ({prog_name}, {changed_entries:,} weights, {delta_path.stat().st_size/1024:.1f} KB) -> {delta_path}", flush=True)

    # 2. Load edited weights & embed ALL 150+ multi-task gallery entries (human + drex-max)
    m.load_state_dict(new_sd)
    G = calibrate.G
    man = json.loads((G / "2026-11-17" / "manifest.json").read_text())
    key = json.loads((G / "key.json").read_text())["tasks"]
    scores_human = json.loads((G / "scores.json").read_text()).get("2026-11-17", {})
    drex = json.loads((G / "critic_scores" / "decision_all_drex_max.json").read_text())

    by_task_tris = {}
    raw_items = []
    for t in man["tasks"]:
        tid = t["id"]
        for e in t["entries"]:
            meta = key[tid][e["label"]]
            slug = meta["slug"]
            k = f"{tid}/{slug}"
            if k not in drex or "error" in drex[k]:
                continue
            imgs = [str(G / e["images"][v]) if v in e["images"] and (G / e["images"][v]).exists() else None for v in VIEWS]
            if sum(p is not None for p in imgs) < 4:
                continue
            tris = meta.get("triangles") or 1
            by_task_tris.setdefault(tid, []).append(tris)
            raw_items.append((tid, slug, e["label"], imgs, meta, drex[k]))

    med_tris = {tid: sorted(ts)[len(ts) // 2] for tid, ts in by_task_tris.items()}

    # Load images in fp16 & embed with WeightCode-edited DINOv2
    import embed
    N = len(raw_items)
    X_all = torch.zeros((N, len(VIEWS), 3, 224, 224), dtype=torch.float16)
    M_all = torch.zeros((N, len(VIEWS)), dtype=torch.bool)
    y_distil = np.zeros(N, dtype=np.float32)
    y_human = np.full(N, np.nan, dtype=np.float32)
    weights = np.ones(N, dtype=np.float32)
    R = np.zeros((N, 2), dtype=np.float32)
    tasks_arr = []

    for i, (tid, slug, label, imgs, meta, dr) in enumerate(raw_items):
        tasks_arr.append(tid)
        for j, p in enumerate(imgs):
            if p:
                X_all[i, j] = embed.load(p, 224).half()
                M_all[i, j] = True
        h_obj = scores_human.get(tid, {}).get(label)
        h_mean = (sum(h_obj[c] for c in calibrate.CRITERIA) / 4.0
                  if h_obj and all(c in h_obj for c in calibrate.CRITERIA) else None)
        d_raw = sum(dr[c] for c in calibrate.CRITERIA) / 4.0
        valid = bool(meta.get("valid"))
        tris = meta.get("triangles") or 1
        r_term = math.log10(max(tris, 1) / max(med_tris[tid], 1))
        d_rg = 1.0 if not valid else min(5.0, max(1.0, d_raw + 0.5 * r_term))
        R[i] = [r_term, 1.0 if valid else 0.0]
        if h_mean is not None:
            y_human[i] = h_mean
            y_distil[i] = h_mean
            weights[i] = 2.5
        else:
            y_distil[i] = d_rg

    batch = 8 if "vitb" in arch or "vitl" in arch else 16
    E = wj.forward_cls(m, X_all, M_all, batch=batch).reshape(N, -1)
    F_mat = np.hstack([E, R * 8.0])

    # Evaluate LOTO across the 4 human-scored tasks (on the edited backbone)
    tasks_np = np.array(tasks_arr)
    hmask = ~np.isnan(y_human)
    htasks = sorted(set(tasks_np[hmask]))
    pred_loto = np.full(N, np.nan)
    per_task = {}
    for ht in htasks:
        te = hmask & (tasks_np == ht)
        if te.sum() < 4:
            continue
        tr = tasks_np != ht
        mu = F_mat[tr].mean(0)
        sd = F_mat[tr].std(0) + 1e-6
        Xt, Xe = (F_mat[tr] - mu) / sd, (F_mat[te] - mu) / sd
        ym = float(np.average(y_distil[tr], weights=weights[tr]))
        K = Xt @ Xt.T + np.diag(lam / np.maximum(weights[tr], 1e-3))
        a = np.linalg.solve(K, y_distil[tr] - ym)
        p = ym + (Xe @ Xt.T) @ a
        pred_loto[te] = p
        per_task[ht] = calibrate.pairwise(list(y_human[te]), list(p))

    vte = ~np.isnan(pred_loto)
    pw_loto = calibrate.pairwise(list(y_human[vte]), list(pred_loto[vte]))
    sp_loto = calibrate.spearman(list(y_human[vte]), list(pred_loto[vte]))

    # Fit final production judge on all N entries
    mu = F_mat.mean(0)
    sd = F_mat.std(0) + 1e-6
    Xt = (F_mat - mu) / sd
    ym = float(np.average(y_distil, weights=weights))
    K = Xt @ Xt.T + np.diag(lam / np.maximum(weights, 1e-3))
    a = np.linalg.solve(K, y_distil - ym)

    np.savez(
        NPZ_PATH,
        Xs=Xt.astype(np.float32),
        a=a.astype(np.float32),
        ym=np.float32(ym),
        mu=mu.astype(np.float32),
        sd=sd.astype(np.float32),
        lam=np.float32(lam),
        dino=arch,
        program=prog_name,
        delta_file=delta_path.name,
        med_tris_json=json.dumps(med_tris),
    )
    pt_str = " | ".join(f"{k}={v:.0%}" for k, v in per_task.items())
    print(f"Distilled WSP Judge ({arch}, {prog_name}, {N} entries / {len(set(tasks_arr))} tasks):", flush=True)
    print(f"  LOTO Pairwise={pw_loto:.1%} Spearman={sp_loto:+.3f} || {pt_str}", flush=True)
    print(f"  Saved -> {NPZ_PATH}", flush=True)

    del m, base_sd, new_sd
    gc.collect()
    if wj.DEVICE == "mps":
        torch.mps.empty_cache()


def score_run(task: str, slug: str, date: str | None = None) -> dict:
    """Score a single model-bench run locally ($0) using the exported WeightCode DINOv2 judge."""
    assert NPZ_PATH.exists(), f"Missing {NPZ_PATH}; run `python3 harness/local_judge.py build` first."
    z = np.load(NPZ_PATH, allow_pickle=False)
    arch = str(z["dino"])
    delta_file = str(z["delta_file"])
    med_tris = json.loads(str(z["med_tris_json"]))

    os.environ["PGBENCH_DINO"] = arch
    os.environ["PGBENCH_DINO_WSP"] = str(HARNESS / delta_file)
    import importlib
    import embed
    importlib.reload(embed)

    run_dir = bench.BENCH / "runs" / (date or bench.TODAY) / task / slug
    rec = json.loads((run_dir / "receipt.json").read_text())
    rdir = run_dir / "renders"
    paths = [str(rdir / f"{v}.png") if (rdir / f"{v}.png").exists() else None for v in VIEWS]
    emb = embed.embed(paths, batch=8).reshape(1, -1)

    tris = rec.get("triangles") or 1
    med = med_tris.get(task) or float(np.median(list(med_tris.values())))
    r_term = math.log10(max(tris, 1) / max(med, 1))
    valid = bool(rec.get("valid"))
    feat = np.hstack([emb, np.array([[r_term * 8.0, (1.0 if valid else 0.0) * 8.0]], dtype=np.float32)])

    xs = (feat - z["mu"]) / z["sd"]
    raw_score = float(z["ym"] + (xs @ z["Xs"].T) @ z["a"])
    score = 1.0 if not valid else float(np.clip(raw_score, 1.0, 5.0))
    return {
        "task": task,
        "slug": slug,
        "local_wsp_score": round(score, 3),
        "raw_score": round(raw_score, 3),
        "valid": valid,
        "triangles": tris,
        "backbone": f"dinov2_{arch}+wsp",
        "cost_usd": 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--arch", default="vitb14", choices=("vits14", "vitb14"))
    b.add_argument("--program", default="auto", choices=("auto", "a1", "hy4"))
    b.add_argument("--lam", type=float, default=1000.0)
    s = sub.add_parser("score")
    s.add_argument("task")
    s.add_argument("slug")
    s.add_argument("--date", default=None)
    args = ap.parse_args()
    if args.cmd == "build":
        build_judge(arch=args.arch, program=args.program, lam=args.lam)
    else:
        print(json.dumps(score_run(args.task, args.slug, args.date), indent=2))


if __name__ == "__main__":
    main()
