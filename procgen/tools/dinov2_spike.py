#!/usr/bin/env python3
"""DINOv2 spike: do DINOv2 features say anything useful about the modeler's kits? (docs/experiments/dinov2-spike.md)

Optional and offline: runs only in its own venv (procgen/.venv-features, torch + transformers) with the
facebook/dinov2-with-registers-small weights in procgen/.features/hf. Nothing in the modeler or the gate uses it.

Measures, on clay renders of every module (procgen/.features/renders, made by `pgm render --pass clay`):
  1. render to render: is a module nearest to itself after a small control change (near-duplicate detection)?
  2. render to atlas render: does a module retrieve the old engine's render of the same piece among all atlas images?
  3. render to drawing: does a module retrieve the reference plate that shows the same object among all 394 plates?
in three modes: raw (CLS token), edges (both images reduced to line drawings first), patch (mean best patch match).
Prints one JSON object.

  HF_HOME=.features/hf .venv-features/bin/python tools/dinov2_spike.py [--model ID] [--size PX] [--dtype float32|float16]

Writes the report to .features/spike-<model>[-<size>][-<dtype>].json as well (the small model at 224 px in float32
keeps .features/spike.json).
"""
import argparse
import glob
import hashlib
import json
import os
import resource
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageFilter, ImageOps
from transformers import AutoModel

HERE = Path(__file__).resolve().parent.parent
MAIN = HERE.parents[3] if (HERE.parents[3] / "References").is_dir() else Path("/nonexistent")
ATLAS = MAIN / "References" / "assets"
PLATES = MAIN.parent / "references" / "plates"
RENDERS = HERE / ".features" / "renders"
CACHE = HERE / ".features" / "spike"
SIZE = 224                      # letterbox size; --size overrides
MODEL = "facebook/dinov2-with-registers-small"

# Pairs checked by eye (both images looked at): module -> images that show the same kind of object.
ATLAS_PAIRS = {
    "gantry-crane": ["shipyard/gantry.jpg"], "deck-capstan": ["shipyard/capstan.jpg"], "roller-fairlead": ["shipyard/fairlead.jpg"],
    "quay-bollard": ["shipyard/mooring.jpg"], "lock-gates": ["shipyard/dock-gate.jpg"], "lock-cabin": ["shipyard/survey-signal.jpg"],
    "rail-viaduct-bay": ["shipyard/survey-viaduct.jpg"], "quay-wall-bay": ["shipyard/quay.jpg"], "quay-stair": ["shipyard/dock-access.jpg"],
    "rowboat": ["shipyard/tender.jpg"], "rail-hall": ["shipyard/steelworks-arrival.jpg"],
    "tree": ["flora/maximum-FL13.jpg", "flora/maximum-FL41.jpg"],
}
PLATE_PAIRS = {"tram-car": ["s114", "s45", "s50"], "steamer": ["s104", "s182", "s25"], "rowboat": ["s104"], "tree": ["s178", "s114"],
               "hull-on-stocks": ["s25"]}


def letterbox(img, bg):
    img = img.convert("RGB")
    img.thumbnail((SIZE, SIZE), Image.LANCZOS)
    canvas = Image.new("RGB", (SIZE, SIZE), bg)
    canvas.paste(img, ((SIZE - img.width) // 2, (SIZE - img.height) // 2))
    return canvas


def edges(img):
    """A line drawing of the image: dark strokes on white, whatever the source (clay render or pen drawing)."""
    g = ImageOps.grayscale(img).filter(ImageFilter.FIND_EDGES)
    g = ImageOps.autocontrast(g, cutoff=1)
    return ImageOps.invert(g).convert("RGB")


class Embedder:
    def __init__(self, model_id, dtype):
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.dtype = getattr(torch, dtype)
        t0 = time.perf_counter()
        self.model = AutoModel.from_pretrained(model_id, dtype=self.dtype).to(self.device).eval()
        self.load_s = time.perf_counter() - t0
        self.registers = int(getattr(self.model.config, "num_register_tokens", 0) or 0)
        self.params = sum(p.numel() for p in self.model.parameters())
        self.peak_mps = 0
        self.mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
        self.std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
        self.ms = []

    @torch.no_grad()
    def __call__(self, images):
        x = torch.from_numpy(np.stack([np.asarray(i, dtype=np.float32) / 255 for i in images])).permute(0, 3, 1, 2)
        x = ((x - self.mean) / self.std).to(self.device, self.dtype)
        t0 = time.perf_counter()
        out = self.model(pixel_values=x)
        cls = out.pooler_output.float().cpu().numpy()
        patches = out.last_hidden_state[:, 1 + self.registers:, :].float().cpu().numpy()   # drop CLS and the registers
        self.ms.append((time.perf_counter() - t0) * 1000 / len(images))
        if self.device == "mps":
            self.peak_mps = max(self.peak_mps, torch.mps.driver_allocated_memory())
        unit = lambda a: a / np.linalg.norm(a, axis=-1, keepdims=True)
        return unit(cls), unit(patches).astype(np.float16)


def embed_all(emb, paths, mode, bg):
    tag = chr(0).join([emb.model_id, str(SIZE), str(emb.dtype)] + paths)
    key = CACHE / f"{mode}-{hashlib.sha1(tag.encode()).hexdigest()[:12]}.npz"
    if key.exists():
        z = np.load(key)
        return z["cls"], z["patch"]
    cls, patch = [], []
    for i in range(0, len(paths), 16):
        imgs = [letterbox(Image.open(p), bg) for p in paths[i:i + 16]]
        if mode == "edges":
            imgs = [edges(im) for im in imgs]
        c, p = emb(imgs)
        cls.append(c)
        patch.append(p)
    cls, patch = np.concatenate(cls), np.concatenate(patch)
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez(key, cls=cls, patch=patch)
    return cls, patch


def patch_score(a, b):
    """Mean over a's patches of the best cosine with b's patches, averaged both ways."""
    s = a.astype(np.float32) @ b.astype(np.float32).T
    return float((s.max(1).mean() + s.max(0).mean()) * 0.5)


def ranks(query_cls, query_patch, cand_cls, cand_patch, wanted, mode):
    """Rank (1 = best) of each wanted candidate index among all candidates."""
    if mode == "patch":
        scores = np.array([patch_score(query_patch, cp) for cp in cand_patch])
    else:
        scores = cand_cls @ query_cls
    order = np.argsort(-scores)
    pos = {int(j): r + 1 for r, j in enumerate(order)}
    return [pos[w] for w in wanted]


def auc(pos, neg):
    pos, neg = np.asarray(pos), np.asarray(neg)
    if not len(pos) or not len(neg):
        return None
    return float(((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean()))


def summary(rs, n):
    rs = np.asarray(rs)
    return {"queries": int(len(rs)), "candidates": n, "medianRank": float(np.median(rs)), "top1": int((rs <= 1).sum()),
            "top5": int((rs <= 5).sum()), "top10": int((rs <= 10).sum()), "chanceMedian": (n + 1) / 2, "ranks": [int(r) for r in rs]}


def main():
    global SIZE
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--size", type=int, default=SIZE)
    ap.add_argument("--dtype", default="float32", choices=["float32", "float16"])
    args = ap.parse_args()
    SIZE = args.size
    if not RENDERS.is_dir():
        print(json.dumps({"passed": False, "error": f"no renders in {RENDERS}; run pgm render --pass clay first"}))
        return 1
    emb = Embedder(args.model, args.dtype)
    emb.model_id = args.model
    renders = sorted(p for p in glob.glob(str(RENDERS / "*.png")) if not p.endswith("-nudge.png"))
    names = [Path(p).stem for p in renders]
    nudges = {Path(p).stem[:-6]: p for p in glob.glob(str(RENDERS / "*-nudge.png"))}
    atlas = sorted(glob.glob(str(ATLAS / "**" / "*.jpg"), recursive=True) + glob.glob(str(ATLAS / "**" / "*.png"), recursive=True))
    plates = sorted(p for p in glob.glob(str(PLATES / "**" / "s[0-9]*_*"), recursive=True)
                    if p.lower().endswith((".jpg", ".jpeg", ".png", ".webp")))
    plate_id = [Path(p).name.split("_")[0] for p in plates]
    report = {"passed": True, "model": args.model, "device": emb.device, "size": SIZE, "dtype": args.dtype,
              "registers": emb.registers, "params": emb.params,
              "images": {"renders": len(renders), "nudged": len(nudges), "atlas": len(atlas), "plates": len(plates)}}
    for mode in ("raw", "edges", "patch"):
        m = "raw" if mode == "patch" else mode
        rc, rp = embed_all(emb, renders, m, (42, 42, 42))
        nk = [k for k in names if k in nudges]
        nc, np_ = embed_all(emb, [nudges[k] for k in nk], m, (42, 42, 42))
        ac, ap = embed_all(emb, atlas, m, (255, 255, 255))
        pc, pp = embed_all(emb, plates, m, (255, 255, 255))
        res = {}
        # 1. near duplicates: the nudged render should find its own module first among all module renders
        r1, same, other = [], [], []
        for i, k in enumerate(nk):
            j = names.index(k)
            r1 += ranks(nc[i], np_[i], rc, rp, [j], mode)
            sc = rc @ nc[i] if mode != "patch" else np.array([patch_score(np_[i], q) for q in rp])
            same.append(float(sc[j]))
            other.append(float(np.max(np.delete(sc, j))))
        res["nearDuplicate"] = summary(r1, len(renders)) | {"aucSameVsNearestOther": auc(same, other)}
        # 2. atlas renders (old engine) of the same piece
        r2 = []
        for k, wanted in ATLAS_PAIRS.items():
            if k not in names:
                continue
            idx = [i for i, p in enumerate(atlas) if any(p.endswith(w) for w in wanted)]
            j = names.index(k)
            r2 += ranks(rc[j], rp[j], ac, ap, idx, mode)
        res["atlasRender"] = summary(r2, len(atlas))
        # 3. reference plates showing the same object
        r3 = []
        for k, wanted in PLATE_PAIRS.items():
            j = names.index(k)
            idx = [i for i, s in enumerate(plate_id) if s in wanted]
            r3 += ranks(rc[j], rp[j], pc, pp, idx, mode)
        res["platesPlate"] = summary(r3, len(plates))
        report[mode] = res
    report["msPerImage"] = round(float(np.median(emb.ms)), 1) if emb.ms else None
    report["loadSeconds"] = round(emb.load_s, 1)
    report["peakMpsMB"] = round(emb.peak_mps / 2**20) if emb.peak_mps else None
    report["maxRssMB"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20)
    short = args.model.split("/")[-1]
    name = "spike" if (args.model, SIZE, args.dtype) == (MODEL, 224, "float32") else \
        f"spike-{short}" + (f"-{SIZE}" if SIZE != 224 else "") + (f"-{args.dtype}" if args.dtype != "float32" else "")
    (HERE / ".features" / f"{name}.json").write_text(json.dumps(report))
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
