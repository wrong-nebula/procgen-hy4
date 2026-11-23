#!/usr/bin/env python3
"""Zero-WebGL DepthPro2-S Depth + Analytical Normal Diagnostic Sheet for procgen kits.

Renders a kit from 4 canonical views (iso, opposite, front, side) in beauty + clay via `pgm render`,
runs local Apple Silicon MPS `apple/DepthPro2-S` to extract metric depth & analytical 3D camera-space
normals, computes relief/flat-face metrics, and writes a 4x4 diagnostic sheet:
  Columns: [1. Beauty | 2. Clay | 3. DepthPro2 Metric | 4. DepthPro2 Normals]
  Rows:    [iso, opposite, front, side]

Usage:
  python3 tools/kit_harness/depth_normal_sheet.py <doc.json|pack.emesh> --out sheet_dn.png [--size 512]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from local_depthpro2 import depth_to_cam_normals, infer_metric_depth, load_model

HARNESS_DIR = Path(__file__).resolve().parent
DEFAULT_PGM = HARNESS_DIR.parent.parent / "build-release/pgm"
VIEWS = ["iso", "opposite", "front", "side"]


def colorize_depth(depth: np.ndarray, fg_mask: np.ndarray) -> Image.Image:
    out = np.full((*depth.shape, 3), 24, dtype=np.uint8)
    if fg_mask.any():
        vals = depth[fg_mask]
        lo, hi = float(np.percentile(vals, 2)), float(np.percentile(vals, 98))
        t = np.clip((hi - depth) / max(hi - lo, 1e-4), 0.0, 1.0)
        # Perceptual warm-near / cool-far magma-like ramp
        r = np.clip(0.08 + 1.15 * (t ** 0.85), 0, 1)
        g = np.clip(0.05 + 0.95 * (t ** 1.55), 0, 1)
        b = np.clip(0.28 + 0.65 * np.sin(t * np.pi * 0.85), 0, 1)
        rgb = (np.stack([r, g, b], axis=-1) * 255.0).astype(np.uint8)
        out[fg_mask] = rgb[fg_mask]
    return Image.fromarray(out)


def colorize_normals(nrm: np.ndarray, fg_mask: np.ndarray) -> Image.Image:
    rgb = np.clip((nrm * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)
    rgb[~fg_mask] = np.array([24, 26, 30], dtype=np.uint8)
    return Image.fromarray(rgb)


def relief_metrics(nrm: np.ndarray, fg_mask: np.ndarray) -> dict[str, float]:
    if fg_mask.sum() < 100:
        return {"normal_grad_mean": 0.0, "high_detail_pct": 0.0, "flat_pct": 100.0}
    gx = np.linalg.norm(nrm[:, 2:] - nrm[:, :-2], axis=-1) * 0.5
    gy = np.linalg.norm(nrm[2:, :] - nrm[:-2, :], axis=-1) * 0.5
    g = np.zeros(nrm.shape[:2], dtype=np.float32)
    g[:, 1:-1] += gx
    g[1:-1, :] += gy
    vals = g[fg_mask]
    return {
        "normal_grad_mean": round(float(np.mean(vals)), 4),
        "high_detail_pct": round(float((vals > 0.14).mean() * 100.0), 1),
        "flat_pct": round(float((vals < 0.035).mean() * 100.0), 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("doc", help="Path to kit .json or .emesh")
    ap.add_argument("--out", required=True, help="Output 4x4 diagnostic PNG path")
    ap.add_argument("--pgm", default=str(DEFAULT_PGM), help="Path to pgm binary")
    ap.add_argument("--size", type=int, default=512, help="Per-tile size in pixels")
    ap.add_argument("--metrics-json", default="", help="Optional JSON path for surface-relief metrics")
    args = ap.parse_args()

    model, dev = load_model()

    S = args.size
    pad = 8
    header = 32
    sheet = Image.new("RGB", (4 * S + 5 * pad, 4 * S + 5 * pad + header), (18, 20, 24))
    draw = ImageDraw.Draw(sheet)
    col_labels = ["1. Beauty", "2. Clay", "3. DepthPro2 Metric", "4. DepthPro2 Normals"]
    for c, lbl in enumerate(col_labels):
        draw.text((pad + c * (S + pad) + 8, 8), lbl, fill=(235, 228, 210))

    all_metrics: dict[str, dict[str, float]] = {}
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        for r, view in enumerate(VIEWS):
            beauty_p = td_p / f"{view}_beauty.png"
            clay_p = td_p / f"{view}_clay.png"
            for p_mode, dst in [("beauty", beauty_p), ("clay", clay_p)]:
                cmd = [args.pgm, "render", args.doc, "--out", str(dst), "--view", view,
                       "--pass", p_mode, "--size", f"{S}x{S}"]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode != 0:
                    raise RuntimeError(f"pgm render failed ({view}/{p_mode}): {res.stderr or res.stdout}")

            beauty_im = Image.open(beauty_p).convert("RGB")
            clay_im = Image.open(clay_p).convert("RGB")
            # Background mask from clay render (uniform background corner color)
            clay_arr = np.asarray(clay_im, dtype=np.float32)
            bg = clay_arr[4, 4]
            fg_mask = np.linalg.norm(clay_arr - bg[None, None, :], axis=-1) > 10.0

            depth = infer_metric_depth(model, dev, clay_p, fov_deg=36.0)
            if depth.shape != (S, S):
                depth = np.asarray(Image.fromarray(depth).resize((S, S), Image.Resampling.BILINEAR), dtype=np.float32)
            nrm = depth_to_cam_normals(depth, fov_deg=36.0)
            m = relief_metrics(nrm, fg_mask)
            all_metrics[view] = m

            dep_im = colorize_depth(depth, fg_mask)
            nrm_im = colorize_normals(nrm, fg_mask)

            y0 = header + pad + r * (S + pad)
            for c, im in enumerate([beauty_im, clay_im, dep_im, nrm_im]):
                x0 = pad + c * (S + pad)
                sheet.paste(im, (x0, y0))
            draw.text((pad + 8, y0 + 6), f"{view} | detail={m['high_detail_pct']}% flat={m['flat_pct']}%",
                      fill=(255, 215, 120))

    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_p)
    if args.metrics_json:
        Path(args.metrics_json).write_text(json.dumps(all_metrics, indent=2))
    print(json.dumps({"passed": True, "out": str(out_p), "metrics": all_metrics}))


if __name__ == "__main__":
    main()
