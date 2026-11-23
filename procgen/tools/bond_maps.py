#!/usr/bin/env python3
"""Bake masonry bond materials (albedo, height + occlusion + roughness, normal) for the engine's image
materials and parallax occlusion mapping. Built by Opus 5.5 (medium).

One seamless repeat cell per bond, written as below. The layout is masonry_bonds.bond_pattern's (the same
cells brick_wall / ashlar_wall lay their stones on), repeated to a square cell by masonry_bonds.cell(), with
the cell origin at UV (0, 0) = the wall frame's x = 0, z = 0, so map joints and geometry joints coincide
(Opus 5.5 medium; test_bond_maps.GeometryAgrees checks it):
  <slot>-albedo.png     sRGB colour, per-unit variation from a seeded hash, mortar, darkened arrises
  <slot>-height-ao.png  r = height (1 = the face, 0 = `relief` metres below it), g = occlusion, b = roughness
  <slot>-normal.png     tangent space, +Y up (OpenGL: green > 0.5 on the upper side of a bump), from the height
  bond-materials.mats   matc image-material records (engine/docs/MATERIAL_FORMAT.md, "Image materials"),
                        tile = cell size in metres
  bond-materials.json   per slot: tile, relief (POM depth, metres), pixel density, files

  python3 procgen/tools/bond_maps.py                         # all six into procgen/out/bond-materials
  python3 procgen/tools/bond_maps.py --density 1024 --bonds brick-flemish --sheet /tmp/bond-sheet.png
  matc --builtin out.emat --images procgen/out/bond-materials/bond-materials.mats \\
       --image-root procgen/out/bond-materials

Each record names the normal map (`normal <slot>-normal.png y-up`: the engine is y-down and flips green
on import) and the relief (`parallax <metres>`: the real depth between height 1 and 0). Both records are
new on the engine branch; --no-normal-record leaves them out for older matc builds, which then derive the
normal from the height (normal-strength = 32 * relief / tile gives that the physical slope).
Each cell is baked at --density px/m but at least --min-px (1024) pixels across; matc resamples to the
pack size (512 for now). Needs numpy and Pillow.
"""
import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
PROCGEN = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROCGEN, "recipes"))
import masonry_bonds  # noqa: E402

DEFAULT_OUT = os.path.join(PROCGEN, "out", "bond-materials")

# Colours (sRGB 0..1), near the engine's built-in "masonry" and "stone" so the slots sit with them.
PALETTES = {
    "brick": dict(unit=(0.58, 0.28, 0.20), alt=(0.66, 0.36, 0.22), dark=(0.36, 0.21, 0.18),
                  mortar=(0.70, 0.67, 0.61), rough=0.84, mortar_rough=0.94, value=(0.82, 1.10)),
    "ashlar-coursed": dict(unit=(0.76, 0.72, 0.64), alt=(0.72, 0.70, 0.66), dark=None,
                           mortar=(0.69, 0.66, 0.60), rough=0.74, mortar_rough=0.90, value=(0.90, 1.06)),
    "ashlar-rusticated": dict(unit=(0.70, 0.65, 0.57), alt=(0.66, 0.64, 0.60), dark=None,
                              mortar=(0.64, 0.61, 0.56), rough=0.78, mortar_rough=0.92, value=(0.88, 1.07)),
}
# Face relief per family, metres: (set-back range, tilt range, surface noise amplitude).
FACE = {"brick": (0.0006, 0.0004, 0.0004), "ashlar-coursed": (0.0003, 0.0002, 0.0003),
        "ashlar-rusticated": (0.0010, 0.0005, 0.0008)}


def _family(name):
    return "brick" if name.startswith("brick") else name


def _noise(N, n, seed, sx=0, sy=0):
    """Tileable value noise over an N x N cell with n x n lattice cells; (sx, sy) shifts the origin in pixels."""
    g = np.random.default_rng(seed).random((n, n))
    t = lambda k, s: ((np.arange(N) + s) % N + 0.5) * n / N
    tx, ty = t(N, sx), t(N, sy)
    x0, y0 = np.floor(tx).astype(int), np.floor(ty).astype(int)
    fx, fy = tx - x0, ty - y0
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    x0, y0 = x0 % n, y0 % n
    x1, y1 = (x0 + 1) % n, (y0 + 1) % n
    a = g[np.ix_(y0, x0)] * (1 - fx) + g[np.ix_(y0, x1)] * fx
    b = g[np.ix_(y1, x0)] * (1 - fx) + g[np.ix_(y1, x1)] * fx
    return a * (1 - fy)[:, None] + b * fy[:, None]


def _fbm(N, size_m, per_m, octaves, seed, sx=0, sy=0):
    out, amp, tot = np.zeros((N, N)), 1.0, 0.0
    for o in range(octaves):
        n = max(1, int(round(size_m * per_m * 2 ** o)))
        out += amp * _noise(N, n, seed * 131 + o, sx, sy)
        tot += amp
        amp *= 0.5
    return out / tot


def _box_blur(a, r):
    if r <= 0:
        return a
    out = a.copy()
    for axis in (0, 1):
        acc = np.zeros_like(out)
        for k in range(-r, r + 1):
            acc += np.roll(out, k, axis=axis)
        out = acc / (2 * r + 1)
    return out


def normal_from_height(height_m, texel_m, scale=1.0):
    """Tangent-space normals (N x N x 3, unit length, +Y up the image) from a height field in metres
    (row 0 is the top of the image), by a wrapping Sobel."""
    h = height_m
    r = lambda dy, dx: np.roll(np.roll(h, -dy, axis=0), -dx, axis=1)     # value at (row + dy, col + dx)
    gx = ((r(-1, 1) + 2 * r(0, 1) + r(1, 1)) - (r(-1, -1) + 2 * r(0, -1) + r(1, -1))) / (8 * texel_m)
    g_down = ((r(1, -1) + 2 * r(1, 0) + r(1, 1)) - (r(-1, -1) + 2 * r(-1, 0) + r(-1, 1))) / (8 * texel_m)
    g_up = -g_down                                       # +Y is up the image
    n = np.stack([-gx * scale, -g_up * scale, np.ones_like(h)], axis=-1)
    return n / np.linalg.norm(n, axis=-1, keepdims=True)


def encode_normal(n):
    return np.clip(np.rint((n * 0.5 + 0.5) * 255), 0, 255).astype(np.uint8)


def decode_normal(rgb):
    n = rgb[..., :3].astype(np.float64) / 255 * 2 - 1
    return n


MIN_PX = 1024          # source pixels per cell at least (the pack is 512^2; matc resamples)


def cell_pixels(bond, density, min_px=MIN_PX):
    return max(8, min_px, int(round(bond.size * density)))


def render(name, density=512, seed=1, shift=(0, 0), min_px=MIN_PX):
    """Float fields of one repeat cell: dict(albedo N x N x 3 sRGB, height 0..1, depth metres, occlusion,
    roughness, normal N x N x 3, mortar bool mask, px_per_m). `shift` (pixels, x and y) moves the
    cell's origin, so render(shift=(a, b)) == np.roll(render(), (-b, -a)) when the cell is seamless."""
    b = masonry_bonds.cell(name)
    fam = _family(name)
    pal, (sb_max, tilt_max, face_amp) = PALETTES[fam], FACE[fam]
    N = cell_pixels(b, density, min_px)
    s = N / b.size                                         # pixels per metre (actual)
    sx, sy = shift
    inside = np.full((N, N), -np.inf)                      # distance into the face, metres
    uid = np.full((N, N), -1, dtype=int)
    dout = np.full((N, N), np.inf)                         # distance to the nearest face, in joints
    face_off = np.zeros((N, N))                            # set-back + tilt of the unit under each pixel
    pad = b.joint * 1.5
    params = []
    for i, u in enumerate(b.units):
        rng = random.Random(f"{seed}:{name}:{i}")
        p = dict(value=rng.uniform(*pal["value"]), mix=rng.random() ** 2, sb=rng.uniform(0, sb_max),
                 tx=rng.uniform(-1, 1) * tilt_max, ty=rng.uniform(-1, 1) * tilt_max,
                 rough=rng.uniform(-0.05, 0.05), dark=fam == "brick" and name == "brick-flemish"
                 and u.kind == "header" and rng.random() < 0.8, spot=rng.random())
        params.append(p)
        for ox in (-b.size, 0.0, b.size):
            for oy in (-b.size, 0.0, b.size):
                # face in image metres (y down), cell origin shifted by (sx, sy) pixels
                x0 = u.x + ox - sx / s
                x1 = x0 + u.w
                yd1 = b.size - (u.y + oy) - sy / s
                yd0 = yd1 - u.h
                c0, c1 = int(math.floor((x0 - pad) * s)), int(math.ceil((x1 + pad) * s))
                r0, r1 = int(math.floor((yd0 - pad) * s)), int(math.ceil((yd1 + pad) * s))
                if c1 < 0 or c0 >= N or r1 < 0 or r0 >= N:
                    continue
                c0, c1, r0, r1 = max(c0, 0), min(c1, N), max(r0, 0), min(r1, N)
                X = (np.arange(c0, c1) + 0.5) / s
                Y = (np.arange(r0, r1) + 0.5) / s
                dx = np.minimum(X - x0, x1 - X)[None, :]
                dy = np.minimum(Y - yd0, yd1 - Y)[:, None]
                d = np.minimum(dx, dy)
                ox_ = np.maximum(np.maximum(x0 - X, X - x1), 0)[None, :]
                oy_ = np.maximum(np.maximum(yd0 - Y, Y - yd1), 0)[:, None]
                do = np.hypot(ox_, oy_)
                sub = (slice(r0, r1), slice(c0, c1))
                take = d > inside[sub]
                inside[sub] = np.where(take, d, inside[sub])
                uid[sub] = np.where(take, i, uid[sub])
                tilt = (p["tx"] * ((X - (x0 + x1) / 2) / (u.w / 2))[None, :]
                        + p["ty"] * ((Y - (yd0 + yd1) / 2) / (u.h / 2))[:, None])
                face_off[sub] = np.where(take, p["sb"] + tilt + tilt_max * 2, face_off[sub])
                dout[sub] = np.minimum(dout[sub], do)
    unit = inside > 0
    d = np.where(unit, inside, 0.0)

    # depth below the face, metres
    e = b.edge_size
    if b.edge == "round":
        q = np.clip(e - d, 0, e)
        arris = e - np.sqrt(np.maximum(e * e - q * q, 0))
    else:                                                 # 45 degree chamfer
        arris = np.clip(e - d, 0, e)
    grain = _fbm(N, b.size, 60, 3, seed * 7 + 1, sx, sy)
    tool = _fbm(N, b.size, 12, 3, seed * 7 + 2, sx, sy)
    face_depth = arris + face_off + face_amp * (0.6 * tool + 0.4 * grain)
    half = b.joint / 2
    concave = min(0.001, 0.2 * b.joint) * np.clip(np.where(np.isfinite(dout), dout, half) / half, 0, 1)
    mortar_depth = b.recess + concave + 0.0004 * grain
    depth = np.where(unit, face_depth, mortar_depth)
    height = np.clip(1 - depth / b.relief, 0, 1)

    # colour
    stain = _fbm(N, b.size, 3, 3, seed * 7 + 3, sx, sy)
    P = np.array([[*(pal["dark"] if p["dark"] else
                     np.array(pal["unit"]) * (1 - p["mix"]) + np.array(pal["alt"]) * p["mix"])] for p in params])
    V = np.array([p["value"] for p in params])
    col = P[np.maximum(uid, 0)] * V[np.maximum(uid, 0)][..., None]
    edge_dark = 1 - 0.14 * np.clip(1 - d / (e + 0.003), 0, 1)
    col = col * edge_dark[..., None]
    mortar_col = np.array(pal["mortar"]) * (0.92 + 0.12 * grain)[..., None]
    albedo = np.where(unit[..., None], col, mortar_col)
    albedo = albedo * ((0.94 + 0.12 * grain) * (0.93 + 0.10 * stain))[..., None]
    albedo = np.clip(albedo, 0, 1)

    occl = _box_blur(1 - 0.55 * np.clip(depth / b.relief, 0, 1) ** 0.8, max(1, int(round(0.002 * s))))
    R = np.array([p["rough"] for p in params])
    rough = np.where(unit, pal["rough"] + R[np.maximum(uid, 0)] + 0.04 * (tool - 0.5), pal["mortar_rough"])
    normal = normal_from_height(-depth, 1 / s)
    return dict(albedo=albedo, height=height, depth=depth, occlusion=np.clip(occl, 0, 1),
                roughness=np.clip(rough, 0, 1), normal=normal, mortar=~unit, px_per_m=s, N=N, bond=b)


def to_images(f):
    q = lambda a: np.clip(np.rint(a * 255), 0, 255).astype(np.uint8)
    albedo = Image.fromarray(q(f["albedo"]), "RGB")
    hao = Image.fromarray(np.stack([q(f["height"]), q(f["occlusion"]), q(f["roughness"])], -1), "RGB")
    normal = Image.fromarray(encode_normal(f["normal"]), "RGB")
    return albedo, hao, normal


def normal_strength(b):
    return round(32 * b.relief / b.size, 3)


def mats_text(names, density, records=True, min_px=MIN_PX):
    """The manifest. records=False leaves out `normal` and `parallax` (new on the engine branch) for
    older matc builds, which then derive the normal from the height."""
    lines = ["mats 1",
             "# Built by Opus 5.5 (medium) with procgen/tools/bond_maps.py; regenerate rather than edit.",
             "# Masonry bond materials for POM: height r (1 = face), occlusion g, roughness b in <slot>-height-ao.png.",
             "# tile = the bond's repeat cell in metres (mesh UVs in metres). parallax = metres between height 1 and 0.",
             "# <slot>-normal.png is tangent space +Y up: `normal ... y-up` makes matc flip it to the engine's y-down.",
             "# normal-strength = 32 * relief / tile, so a height-derived normal (older matc) has the physical slope.",
             f"# Baked at {density:g} px per metre, at least {min_px} px per cell"
             + ("" if records else "; normal and parallax records left out (--no-normal-record)") + ".", ""]
    for n in names:
        b = masonry_bonds.cell(n)
        lines += [f"# {n}: {b.size:g} m cell ({b.repeat[0]} x {b.repeat[1]} of bond_pattern's "
                  f"{b.natural[0]:g} x {b.natural[1]:g} m), {len(b.units)} units, {b.joint * 1000:g} mm joints, "
                  f"{b.edge} arris {b.edge_size * 1000:g} mm, relief {b.relief * 1000:g} mm",
                  f"material {n}",
                  f"albedo {n}-albedo.png",
                  f"height {n}-height-ao.png 0",
                  f"occlusion {n}-height-ao.png 1",
                  f"roughness {n}-height-ao.png 2"]
        if records:
            lines += [f"normal {n}-normal.png y-up", f"parallax {b.relief:g}"]
        lines += ["metallic 0",
                  f"tile {b.size:g}",
                  f"normal-strength {normal_strength(b):g}",
                  "tintable",
                  "end", ""]
    return "\n".join(lines)


MATS_KEYS = {"material", "alias", "albedo", "height", "occlusion", "roughness", "metallic", "tile",
             "normal-strength", "tintable", "zone", "end", "normal", "parallax"}


def parse_mats(text, image_root=None):
    """Parse a .mats manifest the way matc's addImageMaterials reads it; raise ValueError on what it refuses.
    Returns {name: {key: value}}."""
    mats, cur, header = {}, None, False
    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        t = line.split()
        if not header:
            if t != ["mats", "1"]:
                raise ValueError(f"line {ln}: expected 'mats 1' header")
            header = True
            continue
        k = t[0]
        if k not in MATS_KEYS:
            raise ValueError(f"line {ln}: unknown record {k}")
        if k == "material":
            if cur is not None or len(t) != 2:
                raise ValueError(f"line {ln}: material <name>, outside a material")
            cur = mats.setdefault(t[1], {})
            continue
        if cur is None:
            raise ValueError(f"line {ln}: {k} outside a material")
        if k == "end":
            cur = None
        elif k in ("height", "occlusion") or (k == "roughness" and len(t) == 3):
            if len(t) != 3 or t[2] not in ("0", "1", "2", "3"):
                raise ValueError(f"line {ln}: {k} needs a file and a channel 0..3")
            cur[k] = (t[1], int(t[2]))
        elif k == "albedo":
            cur[k] = (t[1], None)
        elif k == "normal":                    # normal <file> [y-up | y-down]; none = y-down (kept as is)
            if len(t) not in (2, 3) or (len(t) == 3 and t[2] not in ("y-up", "y-down")):
                raise ValueError(f"line {ln}: normal needs a file and optionally y-up or y-down")
            cur[k] = (t[1], t[2] if len(t) == 3 else "y-down")
        elif k in ("tile", "metallic", "normal-strength", "roughness", "parallax"):
            if len(t) != 2:
                raise ValueError(f"line {ln}: {k} needs one value")
            v = float(t[1])
            if k in ("tile", "parallax") and v <= 0:
                raise ValueError(f"line {ln}: {k} needs a size in metres")
            cur[k] = v
        elif k == "tintable":
            cur[k] = True
        else:
            cur.setdefault(k, []).append(t[1:])
        if image_root and k in ("albedo", "height", "occlusion", "roughness", "normal") and isinstance(cur.get(k), tuple):
            if not os.path.isfile(os.path.join(image_root, cur[k][0])):
                raise ValueError(f"line {ln}: missing image {cur[k][0]}")
    if not header:
        raise ValueError("missing 'mats 1' header")
    if cur is not None:
        raise ValueError("material without end")
    return mats


def find_matc():
    """matc from $MATC, this worktree's engine build or the main checkout's (none is built in git)."""
    if os.environ.get("MATC"):
        return os.environ["MATC"]
    root = os.path.dirname(PROCGEN)
    cands = [root]
    if "/.claude/worktrees/" in root:
        cands.append(root.split("/.claude/worktrees/")[0])
    for c in cands:
        base = os.path.join(c, "engine", "build")
        if os.path.isdir(base):
            for preset in sorted(os.listdir(base)):
                p = os.path.join(base, preset, "tools", "matbuild", "matc")
                if os.path.isfile(p) and os.access(p, os.X_OK):
                    return p
    return shutil.which("matc")


def run_matc(matc, mats_path, image_root, out_emat):
    r = subprocess.run([matc, "--builtin", out_emat, "--images", mats_path, "--image-root", image_root],
                       capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def check_with_matc(matc, names, density, image_root, min_px=MIN_PX):
    """Run matc on the manifest with the normal/parallax records; if matc refuses one of them as an
    unknown record (an older build), rewrite the manifest without them and run again.
    Returns (records_used, exit code, output)."""
    mats_path = os.path.join(image_root, "bond-materials.mats")
    emat = os.path.join(image_root, "bond-materials.emat")
    for records in (True, False):
        with open(mats_path, "w") as fh:
            fh.write(mats_text(names, density, records, min_px))
        code, out = run_matc(matc, mats_path, image_root, emat)
        refused = "unknown record 'normal'" in out or "unknown record 'parallax'" in out
        if not (records and refused):
            return records, (1 if refused else code), out
    return records, code, out


def contact_sheet(rendered, path, tile=256):
    pad, label = 8, 18
    W = 3 * tile + 4 * pad + 150
    H = len(rendered) * (tile + pad) + pad + label
    sheet = Image.new("RGB", (W, H), (32, 32, 34))
    dr = ImageDraw.Draw(sheet)
    for j, t in enumerate(("albedo", "height", "normal (+Y up)")):
        dr.text((150 + pad + j * (tile + pad), 4), t, fill=(220, 220, 220))
    for i, (name, imgs) in enumerate(rendered):
        y = label + pad + i * (tile + pad)
        b = masonry_bonds.cell(name)
        dr.text((pad, y + 4), name, fill=(230, 230, 230))
        dr.text((pad, y + 20), f"{b.size:g} m cell", fill=(170, 170, 170))
        dr.text((pad, y + 34), f"relief {b.relief * 1000:g} mm", fill=(170, 170, 170))
        albedo, hao, normal = imgs
        for j, im in enumerate((albedo, hao.getchannel(0).convert("RGB"), normal)):
            sheet.paste(im.resize((tile, tile), Image.LANCZOS), (150 + pad + j * (tile + pad), y))
    sheet.save(path)
    return sheet


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=DEFAULT_OUT, help="image root to write (default procgen/out/bond-materials)")
    ap.add_argument("--density", type=float, default=512, help="pixels per metre (default 512)")
    ap.add_argument("--min-px", type=int, default=MIN_PX, help=f"pixels per cell at least (default {MIN_PX})")
    ap.add_argument("--normal-record", dest="records", action="store_true", default=True,
                    help="emit `normal <slot>-normal.png y-up` and `parallax <m>` (default)")
    ap.add_argument("--no-normal-record", dest="records", action="store_false",
                    help="leave both out, for matc builds that predate them")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--bonds", default=",".join(masonry_bonds.BONDS), help="comma-separated bond slots")
    ap.add_argument("--sheet", default=None, help="contact sheet PNG (albedo, height, normal per bond)")
    ap.add_argument("--preview", default=None, help="small contact sheet PNG (e.g. for git)")
    ap.add_argument("--matc", action="store_true", help="run matc --images on the result if a build is found")
    a = ap.parse_args(argv)
    names = [n.strip() for n in a.bonds.split(",") if n.strip()]
    os.makedirs(a.out, exist_ok=True)
    rendered, meta = [], {}
    for n in names:
        f = render(n, a.density, a.seed, min_px=a.min_px)
        imgs = to_images(f)
        for suffix, im in zip(("albedo", "height-ao", "normal"), imgs):
            im.save(os.path.join(a.out, f"{n}-{suffix}.png"), optimize=True)
        b = f["bond"]
        meta[n] = dict(tile_m=b.size, relief_m=b.relief, px=f["N"], px_per_m=round(f["px_per_m"], 3),
                       joint_m=b.joint, edge=b.edge, edge_m=b.edge_size, mortar_recess_m=b.recess,
                       normal_strength=normal_strength(b), units=len(b.units),
                       files=dict(albedo=f"{n}-albedo.png", height_ao=f"{n}-height-ao.png",
                                  normal=f"{n}-normal.png"),
                       height_ao_channels="r height (1 = face, 0 = relief below), g occlusion, b roughness",
                       normal_convention="tangent space, +X right, +Y up the image, +Z out")
        rendered.append((n, imgs))
        print(f"{n}: {f['N']} px, {b.size:g} m, {f['px_per_m']:.1f} px/m")
    mats_path = os.path.join(a.out, "bond-materials.mats")
    with open(mats_path, "w") as fh:
        fh.write(mats_text(names, a.density, a.records, a.min_px))
    with open(os.path.join(a.out, "bond-materials.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    with open(mats_path) as fh:
        parse_mats(fh.read(), a.out)
    print("wrote", mats_path)
    if a.sheet:
        contact_sheet(rendered, a.sheet)
        print("sheet", a.sheet)
    if a.preview:
        contact_sheet(rendered, a.preview, tile=64)
        print("preview", a.preview)
    if a.matc:
        m = find_matc()
        if not m:
            print("matc: no build found (set MATC=...)")
        else:
            if a.records:
                used, code, out = check_with_matc(m, names, a.density, a.out, a.min_px)
            else:
                used, (code, out) = False, run_matc(m, mats_path, a.out, os.path.join(a.out, "bond-materials.emat"))
            print(f"matc ({m}): exit {code}, normal/parallax records {'accepted' if used else 'left out'}\n{out}")
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
