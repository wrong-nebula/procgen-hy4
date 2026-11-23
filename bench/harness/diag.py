#!/usr/bin/env python3
"""Diagnostic contact sheet: one 2x2 image (iso beauty, opposite beauty, front ortho clay, iso parts) of a module
with orientation and scale made visible as real geometry in a preview copy of its document:
  - axes from the origin, 1 m long: x terracotta, y green (grass), z white (opal);
  - a copper arrow on every port, along its normal (0.6 m);
  - a 1.75 m human figure standing on the ground beside the module, on its front (-y) side;
and a caption with the bounds and the ports. The module itself is not changed.

  python3 harness/diag.py <doc.json> <out.png> [pgm] [--set name=value ...]"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

PANELS = [("iso", "beauty", None, "iso (+x, -y, +z)"),
          ("opposite", "beauty", None, "opposite (-x, +y, +z)"),
          ("front", "clay", "ortho", "front ortho (-y)"),
          ("iso", "parts", None, "parts (+x, -y, +z)")]


def encode_image_data_url(path, max_w=1280, quality=88):
    """Encode an image as a crisp JPEG data URL (~10x smaller than PNG, preserving sharp diagnostic edges)."""
    import base64
    import io
    im = Image.open(path).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def pgm(binary, *args):
    out = subprocess.run([str(binary), *map(str, args)], capture_output=True, text=True, timeout=180).stdout.strip().splitlines()
    try:
        return json.loads(out[-1])
    except Exception:
        return {"passed": False}


def rod(i, a, b, r):
    return {"id": i, "type": "sweep", "options": {"path": [list(a), list(b)], "radius": r, "sides": 8}}


def box(i, size, at):
    return [{"id": i + "-s", "type": "cube", "options": {"size": list(size)}},
            {"id": i, "type": "transform", "inputs": [i + "-s"], "options": {"translation": list(at)}}]


def mat(i, src, slot):
    return {"id": i, "type": "material", "inputs": [src], "options": {"slot": slot}}


def envelope_frame(contract):
    """Derive a pixel-locked Z-up framing box ((x0, y0, z0), (x1, y1, z1)) from a task contract's envelope."""
    if not contract or "envelope" not in contract:
        return None
    import contract as ct
    defaults = {k: v[0] for k, v in (contract.get("controls") or {}).items() if isinstance(v, (list, tuple)) and v}
    env = contract["envelope"]
    def eval_bound(e):
        return float(e) if isinstance(e, (int, float)) else ct.ev(e, defaults)
    def span(axis, default_w=4.0, center0=True):
        s = env.get(axis)
        if not s:
            return (-default_w * 0.5, default_w * 0.5) if center0 else (0.0, default_w)
        if isinstance(s, (int, float)):
            w = float(s)
            return (-w * 0.5, w * 0.5) if center0 else (0.0, w)
        if isinstance(s, list) and len(s) == 2:
            a, b = eval_bound(s[0]), eval_bound(s[1])
            return (min(a, b), max(a, b))
        return (-default_w * 0.5, default_w * 0.5) if center0 else (0.0, default_w)
    x0, x1 = span("x", 4.0, True)
    y0, y1 = span("y", 4.0, True)
    z0, z1 = span("z", 4.0, False)
    if "ground_z" in contract and isinstance(contract["ground_z"], list):
        z0 = min(z0, eval_bound(contract["ground_z"][0]))
    return ((x0, y0, z0), (x1, y1, z1))


_FRAME_FLAG_CACHE = {}


def supports_frame_flag(binary):
    b = str(binary)
    if b not in _FRAME_FLAG_CACHE:
        r = pgm(binary, "render", "--help")
        _FRAME_FLAG_CACHE[b] = "--frame" in (r.get("usage") or "")
    return _FRAME_FLAG_CACHE[b]


def preview_doc(doc, ports, bounds, frame=None):
    d = json.loads(Path(doc).read_text())
    n = []
    extras = []
    for axis, slot, tip in (("x", "terracotta", (1, 0, 0)), ("y", "grass", (0, 1, 0)), ("z", "opal", (0, 0, 1))):
        n.append(rod(f"pv-ax-{axis}-r", (0, 0, 0), tip, 0.018))
        n.append(rod(f"pv-ax-{axis}-h", tip, tuple(c * 1.12 for c in tip), 0.045))
        n.append({"id": f"pv-ax-{axis}-j", "type": "join", "inputs": [f"pv-ax-{axis}-r", f"pv-ax-{axis}-h"]})
        n.append(mat(f"pv-ax-{axis}", f"pv-ax-{axis}-j", slot))
        extras.append(f"pv-ax-{axis}")
    for k, p in enumerate(ports):
        o, nv = p["origin"], p["normal"]
        a = [o[i] + nv[i] * 0.45 for i in range(3)]
        b = [o[i] + nv[i] * 0.6 for i in range(3)]
        n += [rod(f"pv-port{k}-r", o, a, 0.025), rod(f"pv-port{k}-h", a, b, 0.06),
              {"id": f"pv-port{k}-j", "type": "join", "inputs": [f"pv-port{k}-r", f"pv-port{k}-h"]},
              mat(f"pv-port{k}", f"pv-port{k}-j", "copper")]
        extras.append(f"pv-port{k}")
    (x0, y0, z0), (x1, y1, z1) = bounds
    if frame is not None:
        (fx0, fy0, fz0), (fx1, fy1, fz1) = frame
        fx = max(x1, fx1) + 0.45
        fy = min(y0, fy0) - 0.1
        fz = max(0.0, fz0) if fz0 > -0.05 else 0.0
    else:
        fx, fy, fz = x1 + 0.45, y0 - 0.1, max(0.0, z0) if z0 > -0.05 else 0.0
    parts = []
    for name, size, at in (("leg-l", (0.13, 0.16, 0.86), (fx - 0.09, fy, fz + 0.43)), ("leg-r", (0.13, 0.16, 0.86), (fx + 0.09, fy, fz + 0.43)),
                           ("body", (0.4, 0.22, 0.62), (fx, fy, fz + 1.17)), ("head", (0.2, 0.22, 0.25), (fx, fy, fz + 1.625))):
        n += box(f"pv-fig-{name}", size, at)
        parts.append(f"pv-fig-{name}")
    n += [{"id": "pv-fig-j", "type": "join", "inputs": parts}, mat("pv-fig", "pv-fig-j", "black")]
    extras.append("pv-fig")
    n.append({"id": "pv-all", "type": "join", "inputs": [d["output"], *extras]})
    d["nodes"] = d["nodes"] + n
    d["output"] = "pv-all"
    d.pop("modulePorts", None)
    return d


def format_sds_line(sds):
    if not isinstance(sds, dict):
        return ""
    q = round(100.0 * float(sds.get("quadRatio") or 0.0))
    return (f"sds: quads {q}% ({sds.get('quadFaces', 0)}q/{sds.get('triFaces', 0)}t/{sds.get('ngonFaces', 0)}n)   "
            f"sdsCages {sds.get('sdsCages', 0)}   creasedEdges {sds.get('creasedEdges', 0)}   "
            f"lods {sds.get('multiLodPrototypes', 0)}   poles>=6 {sds.get('highValencePoles', 0)}")


def sheet(doc, out_png, binary="pgm", sets=None, frame=None, fallback_doc=None, sds=None, wire_cage=False):
    sets = sets or {}
    setargs = [a for k, v in sets.items() for a in ("--set", f"{k}={v}")]
    ins = pgm(binary, "inspect", doc, *setargs) if Path(doc).exists() else {"passed": False}
    ev = pgm(binary, "evaluate", doc, *setargs) if Path(doc).exists() else {"passed": False, "error": "missing doc"}
    bounds = ev.get("boundsZUp")
    retained_provisional = False
    primary_err = str(ev.get("error", ""))[:300]
    if not bounds and fallback_doc and Path(fallback_doc).exists():
        ins = pgm(binary, "inspect", fallback_doc)
        ev = pgm(binary, "evaluate", fallback_doc)
        bounds = ev.get("boundsZUp")
        if bounds:
            doc = fallback_doc
            setargs = []
            retained_provisional = True
    if not bounds:
        return None, "the module does not evaluate: " + primary_err
    if sds is None and Path(doc).exists():
        sds = pgm(binary, "check", doc, *setargs).get("sds")
    ports = ins.get("ports", [])
    frame_args = []
    if frame is not None and supports_frame_flag(binary):
        (fx0, fy0, fz0), (fx1, fy1, fz1) = frame
        # Include the pinned scale figure on +x / -y so it never triggers overflow zoom shifts
        frame_args = ["--frame", f"{min(fx0, -1.15):.3f},{min(fy0 - 0.35, -0.5):.3f},{min(fz0, 0.0):.3f},{max(fx1 + 0.75, 1.15):.3f},{max(fy1, 1.15):.3f},{max(fz1, 1.85):.3f}"]
    with tempfile.TemporaryDirectory(dir=Path(doc).parent) as tmp:
        pdoc = Path(tmp) / "preview.json"
        pdoc.write_text(json.dumps(preview_doc(doc, ports, bounds, frame=frame)))
        tiles = []
        for idx, (view, pas, proj, label) in enumerate(PANELS):
            png = Path(tmp) / f"{label.split()[0]}-{pas}.png"
            args = ["render", pdoc, "--out", png, "--view", view, "--pass", pas, "--size", "640x480", *frame_args, *setargs]
            if proj:
                args += ["--projection", proj]
            if wire_cage and idx == 0:
                args += ["--wire-cage"]
                label = "iso wire-cage (+x, -y, +z)"
            r = pgm(binary, *args)
            if not png.exists():                       # annotations failed to evaluate: fall back to the module itself
                args[1] = doc
                pgm(binary, *args)
            tiles.append((label, Image.open(png).convert("RGB") if png.exists() else Image.new("RGB", (640, 480), (40, 40, 40))))
    sds_line = format_sds_line(sds)
    W, H = 1280, 960 + (90 if sds_line else 70)
    img = Image.new("RGB", (W, H), (24, 24, 24))
    dr = ImageDraw.Draw(img)
    for k, (label, tile) in enumerate(tiles):
        x, y = 640 * (k % 2), 480 * (k // 2)
        img.paste(tile.resize((640, 480)), (x, y))
        dr.rectangle([x, y, x + 215, y + 22], fill=(0, 0, 0))
        dr.text((x + 6, y + 5), label, fill=(240, 240, 240))
    if retained_provisional:
        dr.rectangle([0, 0, W, 28], fill=(92, 54, 18))
        dr.text((12, 7), "PROVISIONAL — RETAINED LAST VALID MESH (current build failed; fix error below)", fill=(255, 224, 166))
    (x0, y0, z0), (x1, y1, z1) = bounds
    cap1 = (f"bounds x {x0:.2f}..{x1:.2f} ({x1 - x0:.2f} m)   y {y0:.2f}..{y1:.2f} ({y1 - y0:.2f} m)   z {z0:.2f}..{z1:.2f} ({z1 - z0:.2f} m)"
            f"   triangles {ev.get('placedTriangles')}   materials {len(ins.get('materials', []))}")
    cap2 = "ports: " + ("; ".join(f"{p['name']} ({p['type']}) at {[round(c, 2) for c in p['origin']]} facing {[round(c, 2) for c in p['normal']]}" for p in ports) or "none")
    cap3 = "key: axes from the origin, 1 m: x terracotta, y green, z white; copper arrows = ports along their normal; black figure = 1.75 m person on the front (-y) side"
    caps = (cap1, sds_line, cap2, cap3) if sds_line else (cap1, cap2, cap3)
    for i, c in enumerate(caps):
        dr.text((8, 965 + 20 * i), c[:230], fill=(220, 220, 220))
    img.save(out_png)
    prefix = "[RETAINED LAST VALID MESH — current build failed]\n" if retained_provisional else ""
    text_lines = [cap1] + ([sds_line] if sds_line else []) + [cap2]
    return out_png, prefix + "\n".join(text_lines)


if __name__ == "__main__":
    a = sys.argv[1:]
    wire = "--wire-cage" in a
    a = [x for x in a if x != "--wire-cage"]
    sets = dict(x.split("=", 1) for i, x in enumerate(a) if i and a[i - 1] == "--set")
    binary = a[2] if len(a) > 2 and not a[2].startswith("--") else "pgm"
    png, text = sheet(a[0], a[1], binary, sets, wire_cage=wire)
    print(text if png else "failed: " + text)
