#!/usr/bin/env python3
"""Textured preview for checking texture fits (docs/TEXTURE_FITTING.md): rasterizes an OBJ written by
`pgm export --obj --zup` (vt in metres, v negated) with one texture on one material slot, orthographic, z-buffered,
lambert-shaded. Other materials are flat grey. Needs numpy and Pillow (procgen/.venv-features has both).

  pgm export doc.json --obj /tmp/x.obj --zup
  .venv-features/bin/python tools/texture_preview.py /tmp/x.obj texture.png pier-ashlar out.png 40 12 700
usage: texture_preview.py in.obj texture.png material out.png yaw_deg pitch_deg [size]"""
import math
import sys

import numpy as np
from PIL import Image

obj, tex_path, mat, out = sys.argv[1:5]
yaw, pitch = math.radians(float(sys.argv[5])), math.radians(float(sys.argv[6]))
S = int(sys.argv[7]) if len(sys.argv) > 7 else 800
V, VT, tris = [], [], []
cur = None
for line in open(obj):
    t = line.split()
    if not t:
        continue
    if t[0] == "v":
        V.append([float(x) for x in t[1:4]])
    elif t[0] == "vt":
        VT.append([float(t[1]), -float(t[2])])
    elif t[0] == "usemtl":
        cur = t[1]
    elif t[0] == "f":
        idx = [(int(a.split("/")[0]) - 1, int(a.split("/")[1]) - 1 if "/" in a else -1) for a in t[1:]]
        for i in range(1, len(idx) - 1):
            tris.append((idx[0], idx[i], idx[i + 1], cur))
V = np.array(V)
tex = np.asarray(Image.open(tex_path).convert("RGB"), dtype=np.float32) / 255
TH, TW = tex.shape[:2]
# camera: rotate about z by yaw, then tilt by pitch; orthographic, looking along +y of the camera
cz, sz, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
R = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
P = np.array([[1, 0, 0], [0, cp, sp], [0, -sp, cp]])
C = (P @ R @ V.T).T
lo, hi = C.min(0), C.max(0)
scale = 0.9 * S / max(hi[0] - lo[0], hi[2] - lo[2])
sx = (C[:, 0] - (lo[0] + hi[0]) / 2) * scale + S / 2
sy = S / 2 - (C[:, 2] - (lo[2] + hi[2]) / 2) * scale
depth = C[:, 1]
img = np.full((S, S, 3), 0.16, np.float32)
zb = np.full((S, S), np.inf)
light = np.array([0.4, -0.6, 0.7])
light /= np.linalg.norm(light)
for (a, ta), (b, tb), (c, tc), m in tris:
    n = np.cross(V[b] - V[a], V[c] - V[a])
    nl = np.linalg.norm(n)
    if nl < 1e-12:
        continue
    shade = 0.35 + 0.65 * max(0.0, float(np.dot(n / nl, light)))
    xs, ys = np.array([sx[a], sx[b], sx[c]]), np.array([sy[a], sy[b], sy[c]])
    x0, x1 = max(int(xs.min()), 0), min(int(xs.max()) + 1, S - 1)
    y0, y1 = max(int(ys.min()), 0), min(int(ys.max()) + 1, S - 1)
    if x1 < x0 or y1 < y0:
        continue
    gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
    d = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
    if abs(d) < 1e-9:
        continue
    w0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / d
    w1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / d
    w2 = 1 - w0 - w1
    inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
    if not inside.any():
        continue
    z = w0 * depth[a] + w1 * depth[b] + w2 * depth[c]
    sub = zb[y0:y1 + 1, x0:x1 + 1]
    vis = inside & (z < sub)
    if not vis.any():
        continue
    sub[vis] = z[vis]
    if m == mat and ta >= 0:
        u = w0 * VT[ta][0] + w1 * VT[tb][0] + w2 * VT[tc][0]
        v = w0 * VT[ta][1] + w1 * VT[tb][1] + w2 * VT[tc][1]
        px = ((u % 1.0) * (TW - 1)).astype(int)
        py = (np.clip(v, 0, 0.9999) * (TH - 1)).astype(int)
        col = tex[py[vis], px[vis]] * shade
    else:
        col = np.array([0.55, 0.55, 0.52]) * shade
    img[y0:y1 + 1, x0:x1 + 1][vis] = col
Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).save(out)
