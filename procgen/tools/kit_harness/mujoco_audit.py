#!/usr/bin/env python3
"""MuJoCo 4.0 physical realism, concave-exact collision, kinematic spin-test & MJCF export suite.

Evaluates whether a procedural module is built like a real physical object:
  1. Exports every authored part pre-bake via `pgm export --obj` and splits multi-shell parts
     into individual closed solid bodies with realistic material densities.
  2. Concave-exact collision (CoACD convex decomposition when `--coacd` / default full mode,
     plus vectorized exact ray-parity overlap verification) separating:
       * `buried_or_duplicate`  — >= 80% smaller-part overlap (e.g. stacked/buried nuts)
       * `cross_material_clip`  — distinct materials clipping (> 8 mm & >= 15% overlap)
       * `same_material_clip`   — unbored same-material clipping (> 15 mm & >= 35% overlap)
       * `monolithic_seat`      — shallow same-material weld/cast fillet seat (allowed)
       * `concave_clear`        — pin in bore / rope in groove (convex hulls overlap, solids don't)
  3. Functional kinematic tests:
       * Static gravity settling & CoM-over-base check (1.0 s MuJoCo dynamics).
       * Lateral pull / overturning safety factor (horizontal pull = 25% of weight).
       * Rotating mechanism spin test (`mjJNT_HINGE` on auto-detected sheave/wheel/drum/pulley
         with CoACD-decomposed static parts — verifies free rotation without jamming).
  4. Visual X-ray collision debug render (`--render-collisions out.png`) highlighting buried
     parts in magenta, cross-material clips in orange-red, and CoM crosshair.
  5. Engine-ready physics export (`--export-mjcf out.xml`) + per-material mass & inertia breakdown.

Usage:
  uv run --with mujoco --with numpy --with coacd --with pillow python3 harness/mujoco_audit.py <doc.json> \\
      [--fast] [--render-collisions out.png] [--export-mjcf out.xml] [--summary]
"""
import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import mujoco
import numpy as np

try:
    import coacd
    HAS_COACD = True
except ImportError:
    HAS_COACD = False

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

ROOT = Path(__file__).resolve().parents[2]
PGM = Path(os.environ.get("PGBENCH_PGM", Path(__file__).resolve().parent.parent.parent / "build-release" / "pgm"))

DENSITY = {
    "limestone": 2400.0,
    "stone": 2400.0,
    "brick": 1900.0,
    "concrete": 2400.0,
    "iron": 7200.0,
    "patina": 7200.0,
    "steel": 7850.0,
    "bronze": 8800.0,
    "brass": 8500.0,
    "timber": 700.0,
    "wood": 700.0,
    "glass": 2500.0,
    "black": 7850.0,
}

ROTATING_RE = re.compile(r"(sheave|wheel|rim|drum|pulley|roller|gear|flywheel|knocker|ring)", re.I)
ROPE_RE = re.compile(r"(rope|cable|wire|lay|cord|chain)", re.I)


def density_for(slot: str) -> float:
    s = (slot or "").lower()
    for k, d in DENSITY.items():
        if k in s:
            return d
    return 7200.0


def unbaked_doc_copy(doc_path: Path, tmp_dir: Path) -> Path:
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


def parse_obj_parts(obj_path: Path):
    all_v = []
    cur_name = "part"
    cur_mat = "iron"
    raw_groups = []
    cur_faces = []

    def flush():
        if cur_faces:
            raw_groups.append((cur_name, cur_mat, list(cur_faces)))
            cur_faces.clear()

    with obj_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split()
                all_v.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("o "):
                flush()
                cur_name = line[2:].strip() or "part"
            elif line.startswith("usemtl "):
                cur_mat = line[7:].strip() or "iron"
            elif line.startswith("f "):
                toks = line[2:].strip().split()
                idx = [int(t.split("/")[0]) - 1 for t in toks]
                for i in range(1, len(idx) - 1):
                    cur_faces.append((idx[0], idx[i], idx[i + 1]))
    flush()
    if not all_v:
        return []
    V = np.asarray(all_v, dtype=np.float64)

    parts = []
    for name, mat, faces in raw_groups:
        if not faces:
            continue
        F = np.asarray(faces, dtype=np.int32)
        used = np.unique(F)
        remap = np.full(len(V), -1, dtype=np.int32)
        remap[used] = np.arange(len(used), dtype=np.int32)
        Fl = remap[F]
        Vl = V[used]

        n = len(Vl)
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b, c in Fl:
            ra, rb, rc = find(int(a)), find(int(b)), find(int(c))
            if ra != rb:
                parent[rb] = ra
            if ra != rc:
                parent[rc] = ra

        comps = {}
        for fi, (a, b, c) in enumerate(Fl):
            comps.setdefault(find(int(a)), []).append(fi)

        for ci, (_, f_idxs) in enumerate(comps.items()):
            Fc = Fl[f_idxs]
            u = np.unique(Fc)
            rm = np.full(n, -1, dtype=np.int32)
            rm[u] = np.arange(len(u), dtype=np.int32)
            Vc = Vl[u]
            Fcl = rm[Fc]
            v0, v1, v2 = Vc[Fcl[:, 0]], Vc[Fcl[:, 1]], Vc[Fcl[:, 2]]
            vol = float(np.sum(np.einsum("ij,ij->i", v0, np.cross(v1, v2))) / 6.0)
            if abs(vol) < 1e-10 or len(Vc) < 4:
                continue
            if vol < 0:
                Fcl = Fcl[:, [0, 2, 1]]
                vol = -vol
            lo = Vc.min(axis=0)
            hi = Vc.max(axis=0)
            aabb_vol = float(np.prod(np.maximum(hi - lo, 1e-6)))
            cname = name if len(comps) == 1 else f"{name}#i{ci}"
            parts.append({
                "name": cname,
                "material": mat,
                "density": density_for(mat),
                "verts": Vc,
                "tris": Fcl,
                "volume": vol,
                "aabb_fill": vol / aabb_vol,
                "mass": vol * density_for(mat),
                "lo": lo,
                "hi": hi,
                "centroid": Vc.mean(axis=0),
                "hulls": None,  # optional CoACD convex decomposition [(verts, tris), ...]
            })
    return parts


def decompose_concave_parts(parts, max_hulls=10):
    """Run CoACD on concave parts (low AABB fill or large face count) so MuJoCo dynamics see real bores/grooves."""
    if not HAS_COACD:
        return 0
    coacd.set_log_level("error")
    cache = {}
    decomposed = 0
    for p in parts:
        # Concave candidates: pierced plates, grooved sheaves, rings, pipes, curved straps (aabb_fill < 0.72 & enough tris).
        if p["aabb_fill"] >= 0.72 or len(p["tris"]) < 24 or p["volume"] < 1e-5:
            continue
        # Center-normalized geometry hash to deduplicate identical instances (e.g., east/west cheeks).
        rel = np.round((p["verts"] - p["centroid"]) * 1000.0).astype(np.int32)
        h = hashlib.sha1(rel.tobytes() + p["tris"].tobytes()).hexdigest()
        if h in cache:
            p["hulls"] = [(hv + p["centroid"], hf) for hv, hf in cache[h]]
            decomposed += 1
            continue
        try:
            mesh = coacd.Mesh(p["verts"] - p["centroid"], p["tris"])
            raw_hulls = coacd.run_coacd(
                mesh,
                threshold=0.07,
                max_convex_hull=max_hulls,
                preprocess_mode="auto",
                mcts_iterations=40,
                mcts_max_depth=2,
            )
            cleaned = []
            for hv, hf in raw_hulls:
                hv = np.asarray(hv, dtype=np.float64)
                hf = np.asarray(hf, dtype=np.int32)
                if len(hv) >= 4 and len(hf) >= 4:
                    cleaned.append((hv, hf))
            if cleaned:
                cache[h] = cleaned
                p["hulls"] = [(hv + p["centroid"], hf) for hv, hf in cleaned]
                decomposed += 1
        except Exception:
            pass
    return decomposed


def points_inside_mesh(pts: np.ndarray, verts: np.ndarray, tris: np.ndarray) -> np.ndarray:
    if len(pts) == 0 or len(tris) == 0:
        return np.zeros(len(pts), dtype=bool)
    v0 = verts[tris[:, 0]]
    v1 = verts[tris[:, 1]]
    v2 = verts[tris[:, 2]]
    e1 = v1 - v0
    e2 = v2 - v0
    d = np.array([1.0, 1.3729e-5, 2.7183e-5], dtype=np.float64)
    d /= np.linalg.norm(d)
    ty_min = np.minimum(np.minimum(v0[:, 1], v1[:, 1]), v2[:, 1]) - 1e-4
    ty_max = np.maximum(np.maximum(v0[:, 1], v1[:, 1]), v2[:, 1]) + 1e-4
    tz_min = np.minimum(np.minimum(v0[:, 2], v1[:, 2]), v2[:, 2]) - 1e-4
    tz_max = np.maximum(np.maximum(v0[:, 2], v1[:, 2]), v2[:, 2]) + 1e-4
    tx_max = np.maximum(np.maximum(v0[:, 0], v1[:, 0]), v2[:, 0]) + 1e-4

    h = np.cross(np.broadcast_to(d, e2.shape), e2)
    det = np.einsum("ij,ij->i", e1, h)
    valid_tri = np.abs(det) > 1e-14
    inv_det = np.zeros_like(det)
    inv_det[valid_tri] = 1.0 / det[valid_tri]

    inside = np.zeros(len(pts), dtype=bool)
    for i, p in enumerate(pts):
        cand = valid_tri & (tx_max >= p[0]) & (ty_min <= p[1]) & (ty_max >= p[1]) & (tz_min <= p[2]) & (tz_max >= p[2])
        if not np.any(cand):
            continue
        s = p - v0[cand]
        u = inv_det[cand] * np.einsum("ij,ij->i", s, h[cand])
        ok = (u >= 0.0) & (u <= 1.0)
        if not np.any(ok):
            continue
        s_ok = s[ok]
        e1_ok = e1[cand][ok]
        e2_ok = e2[cand][ok]
        inv_ok = inv_det[cand][ok]
        q = np.cross(s_ok, e1_ok)
        v = inv_ok * (q @ d)
        ok2 = (v >= 0.0) & (u[ok] + v <= 1.0)
        if not np.any(ok2):
            continue
        t = inv_ok[ok2] * np.einsum("ij,ij->i", e2_ok[ok2], q[ok2])
        inside[i] = (int(np.sum(t > 1e-7)) % 2) == 1
    return inside


def exact_overlap_metrics(pa, pb, n_samples=256):
    lo = np.maximum(pa["lo"], pb["lo"])
    hi = np.minimum(pa["hi"], pb["hi"])
    ext = hi - lo
    if np.any(ext <= 1e-5):
        return 0.0, 0.0, 0.0, None

    box_vol = float(ext[0] * ext[1] * ext[2])
    side = max(4, min(8, int(round(n_samples ** (1.0 / 3.0)))))
    rng = np.random.default_rng(42)
    gx = (np.arange(side) + 0.5) / side
    X, Y, Z = np.meshgrid(gx, gx, gx, indexing="ij")
    grid = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)
    grid = np.clip(grid + (rng.random(grid.shape) - 0.5) * (0.4 / side), 0.01, 0.99)
    pts = lo + grid * ext

    in_a = points_inside_mesh(pts, pa["verts"], pa["tris"])
    if not np.any(in_a):
        return 0.0, 0.0, 0.0, None
    in_ab = points_inside_mesh(pts[in_a], pb["verts"], pb["tris"])
    n_both = int(np.sum(in_ab))
    if n_both == 0:
        return 0.0, 0.0, 0.0, None

    overlap_pts = pts[in_a][in_ab]
    overlap_vol = box_vol * (n_both / len(pts))
    smaller_vol = min(pa["volume"], pb["volume"])
    overlap_frac = min(1.0, overlap_vol / max(smaller_vol, 1e-12))
    ov_span = overlap_pts.max(axis=0) - overlap_pts.min(axis=0)
    cell_step = ext / side
    est_depth = float(min(np.min(ext), np.min(ov_span + cell_step)))
    center = overlap_pts.mean(axis=0)
    return overlap_vol, overlap_frac, est_depth, center


def add_part_geoms(spec, body, idx, p, use_hulls=True, offset=None, friction=0.75):
    """Attach a part's mesh (or CoACD convex hull decomposition) to a MuJoCo body."""
    hulls = p["hulls"] if (use_hulls and p.get("hulls")) else [(p["verts"], p["tris"])]
    gnames = []
    for hi, (hv, hf) in enumerate(hulls):
        verts = (hv - offset) if offset is not None else hv
        m = spec.add_mesh()
        m.name = f"m_{idx}_{hi}"
        m.uservert = verts.astype(np.float32).ravel()
        m.userface = hf.astype(np.int32).ravel()
        g = body.add_geom()
        g.name = f"g_{idx}_{hi}"
        g.type = mujoco.mjtGeom.mjGEOM_MESH
        g.meshname = m.name
        g.density = p["density"]
        g.friction = [friction, 0.005, 0.0001]
        gnames.append(g.name)
    return gnames


def build_mujoco_scene(parts, welded=True, fix_base=False, use_hulls=True):
    spec = mujoco.MjSpec()
    spec.option.gravity = [0.0, 0.0, -9.81]
    spec.option.timestep = 0.002
    spec.option.integrator = mujoco.mjtIntegrator.mjINT_IMPLICITFAST

    all_lo = np.min([p["lo"] for p in parts], axis=0)
    z_floor = float(all_lo[2])

    floor = spec.worldbody.add_geom()
    floor.name = "floor"
    floor.type = mujoco.mjtGeom.mjGEOM_PLANE
    floor.pos = [0.0, 0.0, z_floor]
    floor.size = [5.0, 5.0, 0.1]
    floor.friction = [0.8, 0.005, 0.0001]

    if welded:
        root = spec.worldbody.add_body()
        root.name = "assembly"
        root.pos = [0.0, 0.0, 0.001]
        j = root.add_joint()
        j.type = mujoco.mjtJoint.mjJNT_FREE
        for idx, p in enumerate(parts):
            add_part_geoms(spec, root, idx, p, use_hulls=use_hulls, friction=0.8)
    else:
        for idx, p in enumerate(parts):
            b = spec.worldbody.add_body()
            b.name = f"b_{idx}"
            is_base = (p["lo"][2] <= z_floor + 0.008) and (p["volume"] > 0.01)
            if not (fix_base and is_base):
                j = b.add_joint()
                j.type = mujoco.mjtJoint.mjJNT_FREE
            add_part_geoms(spec, b, idx, p, use_hulls=use_hulls, friction=0.7)

    model = spec.compile()
    data = mujoco.MjData(model)
    return spec, model, data, z_floor


def run_spin_test(parts, use_hulls=True):
    """Auto-detect the primary rotating mechanism part (sheave/wheel/drum/pulley) and spin-test it on an mjJNT_HINGE."""
    candidates = []
    for idx, p in enumerate(parts):
        if ROPE_RE.search(p["name"]):
            continue
        if ROTATING_RE.search(p["name"]) and p["volume"] > 5e-4:
            candidates.append(idx)
    if not candidates:
        return None

    # Pick the largest-diameter wheel/sheave/rim part (not a narrow hub) so its thinnest axis is the true spin axis!
    main_idx = max(candidates, key=lambda i: float(np.max(parts[i]["hi"] - parts[i]["lo"])))
    main_p = parts[main_idx]
    center = main_p["centroid"]
    ext = main_p["hi"] - main_p["lo"]
    # Symmetry axis is the thinnest dimension of a disc/sheave/wheel.
    axis_idx = int(np.argmin(ext))
    axis = [0.0, 0.0, 0.0]
    axis[axis_idx] = 1.0
    radius = float(0.5 * max(ext[(axis_idx + 1) % 3], ext[(axis_idx + 2) % 3]))

    # Include sibling wheel sub-parts (spokes/hub) whose centroids lie within 0.8 * radius of the wheel center
    # AND whose span along the spin axis stays within the wheel's axial envelope (+4 cm margin).
    ax_lo = main_p["lo"][axis_idx] - 0.04
    ax_hi = main_p["hi"][axis_idx] + 0.04
    rotor_set = {main_idx}
    for idx, p in enumerate(parts):
        if idx == main_idx or ROPE_RE.search(p["name"]):
            continue
        if re.search(r"(sheave|wheel|rim|spoke|arm|hub|drum|gear|pulley)", p["name"], re.I):
            if (np.linalg.norm(p["centroid"] - center) < radius * 0.85
                    and p["lo"][axis_idx] >= ax_lo and p["hi"][axis_idx] <= ax_hi):
                rotor_set.add(idx)

    # Exclude pins/axles/bushings/ropes passing through the rotor center from static obstacles; test clearance
    # against cheeks, bedplate, straps, stones, frames!
    spec = mujoco.MjSpec()
    spec.option.gravity = [0.0, 0.0, -9.81]
    spec.option.timestep = 0.002
    spec.option.integrator = mujoco.mjtIntegrator.mjINT_IMPLICITFAST

    # Static frame body (fixed to world).
    frame = spec.worldbody.add_body()
    frame.name = "static_frame"
    for idx, p in enumerate(parts):
        if idx in rotor_set:
            continue
        if ROPE_RE.search(p["name"]) or re.search(r"(pin|axle|shaft|stem|spindle|bush|washer|cotter|collar|nut)", p["name"], re.I):
            continue
        add_part_geoms(spec, frame, idx, p, use_hulls=use_hulls, friction=0.3)

    # Rotor body mounted on a real MuJoCo hinge joint at `center` along `axis`!
    rotor = spec.worldbody.add_body()
    rotor.name = "rotor"
    rotor.pos = center.tolist()
    hj = rotor.add_joint()
    hj.name = "spin_hinge"
    hj.type = mujoco.mjtJoint.mjJNT_HINGE
    hj.axis = axis
    for idx in sorted(rotor_set):
        add_part_geoms(spec, rotor, idx, parts[idx], use_hulls=use_hulls, offset=center, friction=0.3)

    try:
        model = spec.compile()
        data = mujoco.MjData(model)
    except Exception as e:
        return {"rotor": main_p["name"], "error": str(e)}

    mujoco.mj_forward(model, data)
    init_contacts = int(data.ncon)
    j_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "spin_hinge")
    r_bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "rotor")
    dof = int(model.jnt_dofadr[j_id])
    qadr = int(model.jnt_qposadr[j_id])
    # Scale test torque to rotor's inertia so 1 kg brass wheels and 250 kg iron sheaves get ~15 rad/s^2.
    i_eff = max(0.05, float(np.max(model.body_inertia[r_bid])))
    model.dof_damping[dof] = 0.25 * i_eff
    torque = 15.0 * i_eff
    max_contacts = init_contacts
    for _ in range(500):
        data.qfrc_applied[dof] = torque
        mujoco.mj_step(model, data)
        if data.ncon > max_contacts:
            max_contacts = int(data.ncon)

    rot_deg = abs(math.degrees(float(data.qpos[qadr])))
    free_spin = rot_deg >= 180.0 and max_contacts == 0
    return {
        "rotor": main_p["name"],
        "rotorParts": len(rotor_set),
        "axis": ["X", "Y", "Z"][axis_idx],
        "rotationDeg": round(rot_deg, 1),
        "freeSpin": free_spin,
        "jammed": not free_spin,
        "initialFrameContacts": init_contacts,
        "maxFrameContacts": max_contacts,
    }


def render_collision_xray(parts, intersections, com, out_path: Path, width=960, height=720):
    """Render an isometric X-ray collision & CoM diagnostic PNG (pure NumPy + PIL, headless-safe)."""
    if not HAS_PIL:
        return False
    # Isometric orthographic basis (yaw -52 deg, pitch 28 deg matching pgm render --view iso).
    yaw, pitch = math.radians(-52.0), math.radians(28.0)
    to_cam = np.array([math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch)])
    fwd = -to_cam
    right = np.cross(fwd, np.array([0.0, 0.0, 1.0]))
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)

    all_v = np.vstack([p["verts"] for p in parts])
    ctr = 0.5 * (all_v.min(axis=0) + all_v.max(axis=0))
    proj_x = (all_v - ctr) @ right
    proj_y = (all_v - ctr) @ up
    span = max(proj_x.max() - proj_x.min(), proj_y.max() - proj_y.min(), 1e-4)
    scale = min(width, height - 48) * 0.82 / span

    def to_px(pts):
        d = pts - ctr
        px = width * 0.5 + (d @ right) * scale
        py = (height - 40) * 0.5 - (d @ up) * scale
        return np.stack([px, py], axis=-1)

    buried_names = {x["partA"] for x in intersections if x["severity"] == "buried_or_duplicate"} | \
                   {x["partB"] for x in intersections if x["severity"] == "buried_or_duplicate"}
    cross_names = {x["partA"] for x in intersections if x["severity"] == "cross_material_clip"} | \
                  {x["partB"] for x in intersections if x["severity"] == "cross_material_clip"}
    same_names = {x["partA"] for x in intersections if x["severity"] == "same_material_clip"} | \
                 {x["partB"] for x in intersections if x["severity"] == "same_material_clip"}

    img = Image.new("RGBA", (width, height), (30, 32, 36, 255))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Sort parts back-to-front along camera forward direction.
    order = sorted(range(len(parts)), key=lambda i: -float((parts[i]["centroid"] - ctr) @ fwd))
    for i in order:
        p = parts[i]
        nm = p["name"]
        if nm in buried_names:
            fill, edge = (235, 35, 165, 115), (255, 90, 210, 220)
        elif nm in cross_names:
            fill, edge = (245, 110, 25, 95), (255, 160, 60, 200)
        elif nm in same_names:
            fill, edge = (235, 200, 45, 70), (250, 220, 90, 160)
        else:
            fill, edge = (135, 155, 170, 34), (165, 185, 200, 72)
        px = to_px(p["verts"])
        # Subsample large meshes slightly so X-ray edges stay crisp and fast.
        tris = p["tris"]
        step = max(1, len(tris) // 600)
        for a, b, c in tris[::step]:
            poly = [(float(px[a, 0]), float(px[a, 1])),
                    (float(px[b, 0]), float(px[b, 1])),
                    (float(px[c, 0]), float(px[c, 1]))]
            draw.polygon(poly, fill=fill, outline=edge)

    # Draw exact overlap centroids as glowing red rings.
    for x in intersections[:25]:
        if x.get("_center") is None:
            continue
        cp = to_px(np.array([x["_center"]]))[0]
        cx, cy = float(cp[0]), float(cp[1])
        r = 7 if x["severity"] == "buried_or_duplicate" else 5
        col = (255, 50, 190, 255) if x["severity"] == "buried_or_duplicate" else (255, 120, 30, 255)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=col, width=2)

    # Draw CoM crosshair in cyan-green.
    com_px = to_px(np.array([com]))[0]
    cx, cy = float(com_px[0]), float(com_px[1])
    draw.line((cx - 12, cy, cx + 12, cy), fill=(50, 240, 160, 255), width=2)
    draw.line((cx, cy - 12, cx, cy + 12), fill=(50, 240, 160, 255), width=2)
    draw.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), outline=(50, 240, 160, 255), width=2)

    out_img = Image.alpha_composite(img, overlay).convert("RGB")
    d2 = ImageDraw.Draw(out_img)
    d2.rectangle((0, height - 34, width, height), fill=(22, 24, 28))
    d2.text((14, height - 24),
            f"MuJoCo X-Ray: MAGENTA=buried/dup ({len(buried_names)})  ORANGE=cross-mat clip ({len(cross_names)})  "
            f"YELLOW=same-mat clip ({len(same_names)})  GREEN+=CoM",
            fill=(220, 225, 230))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_img.save(out_path)
    return True


def audit_document(doc_path: Path, use_coacd=True, run_kinematic=True,
                   render_collisions_path: Path = None, export_mjcf_path: Path = None,
                   set_controls: dict = None):
    doc_path = doc_path.resolve()
    with tempfile.TemporaryDirectory(prefix="mj_audit_") as td:
        td_path = Path(td)
        eval_doc = unbaked_doc_copy(doc_path, td_path)
        obj_path = td_path / "model.obj"
        cmd = [str(PGM), "export", str(eval_doc), "--obj", str(obj_path)]
        for k, v in (set_controls or {}).items():
            cmd += ["--set", f"{k}={v}"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            return {"document": str(doc_path), "passed": False, "error": res.stderr or res.stdout}
        parts = parse_obj_parts(obj_path)
        if not parts:
            return {"document": str(doc_path), "passed": False, "error": "no solid parts exported"}

    # 0. Classify mountMode up front from declared ports + assembly scale.
    all_lo_m = np.min([p["lo"] for p in parts], axis=0)
    all_hi_m = np.max([p["hi"] for p in parts], axis=0)
    max_span = float(np.max(all_hi_m - all_lo_m))
    mount_type = "freestanding"
    mount_port = None
    try:
        doc_obj = json.loads(Path(doc_path).read_text())
        raw_ports = doc_obj.get("modulePorts") or doc_obj.get("ports") or {}
        plist = list(raw_ports.items()) if isinstance(raw_ports, dict) else [
            (p.get("name", f"p{i}"), p) for i, p in enumerate(raw_ports) if isinstance(p, dict)
        ]
        has_footing = False
        has_wall, has_roof = False, False
        for pname, pdef in plist:
            if not isinstance(pdef, dict):
                continue
            ptype = str(pdef.get("type") or "").lower()
            pl = str(pname).lower()
            nrm = pdef.get("normal")
            nz = float(nrm[2]) if isinstance(nrm, list) and len(nrm) == 3 and all(isinstance(v, (int, float)) for v in nrm) else 0.0
            if ptype in ("footing", "base", "ground", "floor") or pl in ("base", "footing"):
                has_footing = True
            elif ptype in ("roof", "slope", "sash", "skylight", "ridge", "ceiling") or any(k in pl for k in ("roof", "sash", "slope")):
                has_roof = True
                mount_port = mount_port or pdef
            elif ptype in ("wall", "mount", "door", "back", "jamb", "fascia", "face", "stile") or abs(nz) < 0.35:
                has_wall = True
                mount_port = mount_port or pdef
        if max_span >= 4.5 and len(parts) >= 35:
            mount_type = "architectural"
        elif not has_footing and has_roof:
            mount_type = "roof_mount"
        elif not has_footing and has_wall:
            mount_type = "wall_mount"
    except Exception:
        pass

    # 1. Optional CoACD concave convex decomposition.
    decomposed_parts = decompose_concave_parts(parts) if use_coacd else 0

    # 2. Pairwise MuJoCo contact detection + exact concave-aware semantic overlap classification.
    _, m_sep, d_sep, z_floor = build_mujoco_scene(parts, welded=False, fix_base=True, use_hulls=use_coacd)
    mujoco.mj_forward(m_sep, d_sep)

    gid_to_part = {}
    for gid in range(m_sep.ngeom):
        gname = mujoco.mj_id2name(m_sep, mujoco.mjtObj.mjOBJ_GEOM, gid)
        if gname and gname.startswith("g_"):
            gid_to_part[gid] = int(gname.split("_")[1])

    pair_convex_pen = {}
    for ci in range(d_sep.ncon):
        c = d_sep.contact[ci]
        if c.geom1 not in gid_to_part or c.geom2 not in gid_to_part:
            continue
        i, j = gid_to_part[c.geom1], gid_to_part[c.geom2]
        if i == j:
            continue
        key = (min(i, j), max(i, j))
        pen = max(0.0, float(-c.dist))
        if pen > pair_convex_pen.get(key, 0.0):
            pair_convex_pen[key] = pen

    intersections = []
    seated_pairs = 0
    concave_clear_pairs = 0
    monolithic_seats = 0
    structural_joinery = 0
    buried_cnt = 0
    cross_clip_cnt = 0
    same_clip_cnt = 0
    is_arch = (mount_type == "architectural")

    for (i, j), cpen in sorted(pair_convex_pen.items()):
        if cpen <= 0.002:
            seated_pairs += 1
            continue
        ov_vol, ov_frac, est_depth, ov_ctr = exact_overlap_metrics(parts[i], parts[j])
        if ov_vol == 0.0:
            concave_clear_pairs += 1
            continue
        same_mat = parts[i]["material"] == parts[j]["material"]
        buried_thresh = 0.85 if is_arch else 0.80
        cross_thresh = 0.40 if is_arch else 0.15
        same_thresh = 0.50 if is_arch else 0.35
        if ov_frac >= buried_thresh and est_depth > 0.008:
            sev = "buried_or_duplicate"
            buried_cnt += 1
        elif (not same_mat) and est_depth > 0.008 and ov_frac >= cross_thresh:
            sev = "cross_material_clip"
            cross_clip_cnt += 1
        elif same_mat and est_depth > 0.015 and ov_frac >= same_thresh:
            sev = "same_material_clip"
            same_clip_cnt += 1
        elif is_arch and ov_frac >= 0.12:
            structural_joinery += 1
            continue
        elif same_mat and est_depth <= 0.015:
            monolithic_seats += 1
            continue
        else:
            seated_pairs += 1
            continue

        intersections.append({
            "severity": sev,
            "partA": parts[i]["name"],
            "partB": parts[j]["name"],
            "matA": parts[i]["material"],
            "matB": parts[j]["material"],
            "exactDepthMM": round(est_depth * 1000.0, 2),
            "overlapPctSmaller": round(ov_frac * 100.0, 1),
            "_center": ov_ctr,
        })

    sev_rank = {"buried_or_duplicate": 0, "cross_material_clip": 1, "same_material_clip": 2}
    intersections.sort(key=lambda x: (sev_rank[x["severity"]], -x["overlapPctSmaller"], -x["exactDepthMM"]))

    # 3. Welded assembly gravitational stability + lateral overturning test (1.0 s + 0.5 s).
    spec_asm, m_asm, d_asm, _ = build_mujoco_scene(parts, welded=True, use_hulls=use_coacd)
    mujoco.mj_forward(m_asm, d_asm)
    total_mass = float(m_asm.body_mass[1])
    com_init = d_asm.subtree_com[1].copy()
    principal_inertia = [round(float(v), 4) for v in m_asm.body_inertia[1]]

    # Per-material mass breakdown.
    mass_by_mat = {}
    for p in parts:
        mass_by_mat[p["material"]] = round(mass_by_mat.get(p["material"], 0.0) + p["mass"], 2)

    base_pts = np.vstack([p["verts"] for p in parts if p["lo"][2] <= z_floor + 0.02])
    base_lo = base_pts[:, :2].min(axis=0)
    base_hi = base_pts[:, :2].max(axis=0)
    com_over_base = bool(base_lo[0] <= com_init[0] <= base_hi[0] and base_lo[1] <= com_init[1] <= base_hi[1])

    # Analytical overturning safety factor under a 25%-weight lateral pull at CoM height.
    min_edge_lever = float(min(com_init[0] - base_lo[0], base_hi[0] - com_init[0],
                               com_init[1] - base_lo[1], base_hi[1] - com_init[1]))
    com_height = max(0.01, float(com_init[2] - z_floor))
    overturning_sf = round(max(0.0, min_edge_lever / (0.25 * com_height)), 2)

    q0 = d_asm.qpos[:7].copy()
    for _ in range(500):
        mujoco.mj_step(m_asm, d_asm)
    q1 = d_asm.qpos[:7].copy()

    def quat_tilt(q):
        w, x, y, z = q[3], q[4], q[5], q[6]
        return math.degrees(math.acos(max(-1.0, min(1.0, 1.0 - 2.0 * (x * x + y * y)))))

    tilt_deg = quat_tilt(q1)
    settle_mm = float(np.linalg.norm(q1[:3] - q0[:3]) * 1000.0)
    stable = com_over_base and tilt_deg < 2.0 and settle_mm < 15.0

    # Lateral pull test (25% of weight in +Y for 0.5 s).
    pull_f = 0.25 * total_mass * 9.81
    for _ in range(250):
        d_asm.xfrc_applied[1, :3] = [0.0, pull_f, 0.0]
        mujoco.mj_step(m_asm, d_asm)
    d_asm.xfrc_applied[1, :] = 0.0
    lateral_tilt_deg = quat_tilt(d_asm.qpos[:7])

    # 4. Functional kinematic spin test (for freestanding/roof machinery sheaves/wheels/drums/pulleys;
    #    wall-mounted knockers pivot on a top hinge bar, not their ring center!).
    spin_res = run_spin_test(parts, use_hulls=use_coacd) if (run_kinematic and mount_type not in ("wall_mount", "architectural")) else None

    # 5. Optional MJCF XML export & X-ray collision debug render.
    if export_mjcf_path:
        export_mjcf_path.parent.mkdir(parents=True, exist_ok=True)
        export_mjcf_path.write_text(spec_asm.to_xml())
    if render_collisions_path:
        render_collision_xray(parts, intersections, com_init, render_collisions_path)

    # Mounting-plane flush check for wall_mount / roof_mount hardware.
    mount_seated = True
    if mount_type in ("wall_mount", "roof_mount") and isinstance(mount_port, dict):
        orig = mount_port.get("origin")
        nrm = mount_port.get("normal")
        if (isinstance(orig, list) and len(orig) == 3 and all(isinstance(v, (int, float)) for v in orig)
                and isinstance(nrm, list) and len(nrm) == 3 and all(isinstance(v, (int, float)) for v in nrm)):
            nv = np.asarray(nrm, dtype=np.float64)
            nlen = float(np.linalg.norm(nv))
            if nlen > 1e-6:
                nv /= nlen
                ov = np.asarray(orig, dtype=np.float64)
                min_plane_dist = min(float(np.min(np.abs((p["verts"] - ov) @ nv))) for p in parts)
                mount_seated = (min_plane_dist <= 0.025)

    # Calibrated physical realism score (0..100).
    score = 100.0
    score -= min(45.0, buried_cnt * (3.0 if is_arch else 5.0))
    score -= min(30.0, cross_clip_cnt * (2.5 if is_arch else 4.0))
    score -= min(15.0, same_clip_cnt * (1.0 if is_arch else 2.0))
    if mount_type in ("freestanding", "architectural"):
        if not stable:
            score -= 25.0
        if not com_over_base:
            score -= 15.0
    elif not mount_seated:
        score -= 15.0
    if spin_res and spin_res.get("jammed"):
        score -= 10.0
    score = max(0.0, round(score, 1))

    clean_intersections = [{k: v for k, v in x.items() if k != "_center"} for x in intersections[:10]]
    return {
        "document": str(doc_path),
        "mountType": mount_type,
        "mountSeated": mount_seated,
        "passed": ((stable if mount_type in ("freestanding", "architectural") else mount_seated)
                   and buried_cnt == 0 and cross_clip_cnt == 0),
        "physicsScore": score,
        "coacdDecomposed": decomposed_parts,
        "solidParts": len(parts),
        "totalMassKg": round(total_mass, 1),
        "massByMaterialKg": mass_by_mat,
        "principalInertia": principal_inertia,
        "comZUp": [round(float(v), 4) for v in com_init],
        "comOverBase": com_over_base,
        "overturningSafetyFactor": overturning_sf,
        "tiltDegAfter1s": round(tilt_deg, 3),
        "lateralTiltDeg": round(lateral_tilt_deg, 3),
        "settleMM": round(settle_mm, 2),
        "stableUnderGravity": stable,
        "spinTest": spin_res,
        "seatedPairs": seated_pairs,
        "monolithicSeats": monolithic_seats,
        "structuralJoinery": structural_joinery,
        "concaveClearPairs": concave_clear_pairs,
        "buriedOrDuplicate": buried_cnt,
        "crossMaterialClips": cross_clip_cnt,
        "sameMaterialClips": same_clip_cnt,
        "topIntersections": clean_intersections,
    }


def format_one_line(r: dict) -> str:
    if r.get("error"):
        return f"physics: error ({r['error']})"
    bits = [
        f"score={r['physicsScore']}/100",
        f"mass={r['totalMassKg']}kg",
        f"tilt={r['tiltDegAfter1s']:.1f}deg",
        f"SF={r['overturningSafetyFactor']}",
        f"buried={r['buriedOrDuplicate']}",
        f"crossClips={r['crossMaterialClips']}",
        f"sameClips={r['sameMaterialClips']}",
    ]
    sp = r.get("spinTest")
    if sp and "rotationDeg" in sp:
        bits.append(f"spin={sp['rotationDeg']:.0f}deg({'ok' if sp['freeSpin'] else 'JAMMED'})")
    top = r.get("topIntersections") or []
    if top:
        t0 = top[0]
        bits.append(f"worst={t0['partA']}x{t0['partB']}({t0['severity']},{t0['exactDepthMM']}mm)")
    return "physics: " + " ".join(bits)


def sweep_controls_audit(doc_path: Path, use_coacd=False):
    """Evaluate every continuous/integer control at [min, max] in MuJoCo to catch mid-stroke collisions or tipping."""
    raw = json.loads(doc_path.read_text())
    ctrls = raw.get("controls") or {}
    poses = []
    for name, spec in sorted(ctrls.items()):
        if not isinstance(spec, dict) or "min" not in spec or "max" not in spec:
            continue
        for tag, val in (("min", spec["min"]), ("max", spec["max"])):
            r = audit_document(doc_path, use_coacd=use_coacd, run_kinematic=False, set_controls={name: val})
            poses.append({
                "control": name,
                "end": tag,
                "value": val,
                "physicsScore": r.get("physicsScore", 0.0),
                "stable": r.get("stableUnderGravity", False),
                "buried": r.get("buriedOrDuplicate", 0),
                "crossClips": r.get("crossMaterialClips", 0),
                "sameClips": r.get("sameMaterialClips", 0),
                "tiltDeg": r.get("tiltDegAfter1s", 0.0),
            })
    if not poses:
        return {"posesTested": 0, "minScore": None, "allStable": True, "poses": []}
    worst = min(poses, key=lambda x: x["physicsScore"])
    return {
        "posesTested": len(poses),
        "minScore": worst["physicsScore"],
        "allStable": all(p["stable"] for p in poses),
        "worstPose": f"{worst['control']}={worst['value']}({worst['end']})",
        "poses": poses,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("docs", nargs="+")
    ap.add_argument("--fast", action="store_true", help="Skip CoACD & kinematic spin test (<1.5s interactive mode)")
    ap.add_argument("--sweep-controls", action="store_true", help="Also sweep every control at min/max poses in MuJoCo")
    ap.add_argument("--render-collisions", type=Path, default=None)
    ap.add_argument("--export-mjcf", type=Path, default=None)
    ap.add_argument("--summary", action="store_true", help="Print compact one-line physics summary")
    args = ap.parse_args()

    results = []
    for i, p in enumerate(args.docs):
        r = audit_document(
            Path(p),
            use_coacd=not args.fast,
            run_kinematic=not args.fast,
            render_collisions_path=args.render_collisions if i == 0 else None,
            export_mjcf_path=args.export_mjcf if i == 0 else None,
        )
        if args.sweep_controls and not r.get("error"):
            r["controlSweep"] = sweep_controls_audit(Path(p), use_coacd=False)
        results.append(r)
    if args.summary:
        for r in results:
            line = format_one_line(r)
            if r.get("controlSweep") and r["controlSweep"]["posesTested"] > 0:
                cs = r["controlSweep"]
                line += f" sweep={cs['posesTested']}poses(min={cs['minScore']},worst={cs['worstPose']})"
            print(line)
    else:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2))


if __name__ == "__main__":
    main()
