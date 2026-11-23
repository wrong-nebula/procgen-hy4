#!/usr/bin/env python3
"""Image embeddings with DINOv2 (ViT-S/14 by default; PGBENCH_DINO=vitb14|vitl14|vitg14 for larger ones; from torch.hub; weights cached in ~/.cache/torch), on Apple's GPU when
available. embed(paths) -> numpy array (n, 384) of L2-normalised CLS features; missing files give zero rows."""
import os
from pathlib import Path

import numpy as np
import torch
from PIL import Image

_MODEL = None
_HUB_LOCAL = Path.home() / ".cache/torch/hub/facebookresearch_dinov2_main"
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
ARCH = os.environ.get("PGBENCH_DINO", "vits14")
WSP_DELTA = os.environ.get("PGBENCH_DINO_WSP", "")
DIM = {"vits14": 384, "vitb14": 768, "vitl14": 1024, "vitg14": 1536}[ARCH]
MEAN, STD = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1), torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)


def model():
    global _MODEL
    if _MODEL is None:
        if _HUB_LOCAL.exists():
            _MODEL = torch.hub.load(str(_HUB_LOCAL), f"dinov2_{ARCH}", source="local", verbose=False)
        else:
            _MODEL = torch.hub.load("facebookresearch/dinov2", f"dinov2_{ARCH}", verbose=False)
        if WSP_DELTA:
            delta_path = Path(WSP_DELTA)
            if not delta_path.exists():
                delta_path = Path(__file__).resolve().parent / f"wsp_delta_{ARCH}.pt"
            if delta_path.exists():
                patch = torch.load(delta_path, map_location="cpu", weights_only=True)
                sd = _MODEL.state_dict()
                for k, v in patch.items():
                    if k in sd and sd[k].shape == v.shape:
                        sd[k] = v.to(dtype=sd[k].dtype)
                _MODEL.load_state_dict(sd, strict=True)
        _MODEL = _MODEL.to(DEVICE).eval()
    return _MODEL


def load(p, size=224):
    im = Image.open(p).convert("RGB")
    w, h = im.size
    s = min(w, h)
    im = im.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2)).resize((size, size), Image.BICUBIC)
    t = torch.from_numpy(np.asarray(im, dtype=np.float32) / 255.0).permute(2, 0, 1)
    return (t - MEAN) / STD


@torch.no_grad()
def embed(paths, batch=32, size=224):
    out = np.zeros((len(paths), DIM), dtype=np.float32)
    idx = [i for i, p in enumerate(paths) if p and Path(p).exists()]
    for k in range(0, len(idx), batch):
        chunk = idx[k:k + batch]
        x = torch.stack([load(paths[i], size) for i in chunk]).to(DEVICE)
        f = model()(x).float().cpu().numpy()
        f /= np.linalg.norm(f, axis=1, keepdims=True) + 1e-8
        out[chunk] = f
    return out
