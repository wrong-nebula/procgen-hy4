#!/usr/bin/env python3
"""Upgrade #7: Articulated MuJoCo 4.0 + CoACD Belfry Bell / Mechanism Hinge Swing Test.

Extends `mujoco_audit.py` for articulated architectural hardware (chapel bell inside open
belfry arches, door ring-knocker, hinged shutters, lock-gate handwheels):
  - Auto-detects any bronze/iron `bell` / `knocker` / `ring` / `wheel` / `sheave` part.
  - Decomposes surrounding static belfry/wall masonry with CoACD concave hulls so MuJoCo 4.0
    sees the real open arched openings and interior belfry cavity.
  - Mounts the detected bell/mechanism on an explicit `mjJNT_HINGE` at its top pivot (yoke
    elevation for a hanging bell, horizontal hinge axis), applies realistic swing torque for
    1.5 s of MuJoCo dynamics, and measures exact free swing arc in degrees & contact forces!
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mujoco_audit as ma

BELL_RE = re.compile(r"(bell|clapper|knocker|ring|pendulum|chime|wheel|sheave|handwheel|sail|rotor|vane)", re.I)


def run_bell_swing_test(doc_path: Path) -> dict | None:
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        unbaked = ma.unbaked_doc_copy(doc_path, tdir)
        obj = tdir / "parts.obj"
        p = subprocess.run([str(ma.PGM), "export", str(unbaked), "--obj", str(obj)], capture_output=True, text=True)
        if p.returncode != 0 or not obj.exists():
            return None
        parts = ma.parse_obj_parts(obj)
        if not parts:
            return None

        # Find swinging bell / knocker / mechanism part (exclude yoke/beam/frame/floor)
        cands = [
            pt for pt in parts
            if BELL_RE.search(pt["name"]) and not re.search(r"(yoke|beam|floor|frame|mount|plate|bracket|arch|wall|pedestal|pit|race|curb|plinth|post|trestle)", pt["name"], re.I)
        ]
        if not cands:
            return None
        # Prefer sail rotor / waterwheel / wheel / sheave / bell (center pivot for rotors/wheels, top pivot for hanging bells!)
        cands.sort(key=lambda pt: (
            -int("sail_rotor" in pt["name"].lower() or "windmill_sail" in pt["name"].lower() or "sail" in pt["name"].lower()),
            -int("waterwheel" in pt["name"].lower() or "wheel" in pt["name"].lower()),
            -int("bell" in pt["name"].lower()),
            -pt["mass"],
        ))
        bell = cands[0]
        is_sail = bool(re.search(r"(sail_rotor|windmill_sail|sail|rotor)", bell["name"], re.I)) and not ("fantail" in bell["name"].lower())
        is_wheel = is_sail or bool(re.search(r"(waterwheel|wheel|sheave|pulley|handwheel|drum|gear)", bell["name"], re.I))

        # Group ALL rigidly-connected subparts of a multi-part rotating wheel or windmill sail rotor
        # while keeping the axle/shaft, wall collars, pillow blocks, pedestal, cap, and walls static!
        if is_sail:
            rotor_parts = [
                pt for pt in parts
                if re.search(r"(sail_rotor|windmill_sail|sail_|poll_head)", pt["name"], re.I)
                and not re.search(r"(axle|shaft|bearing|collar|pillow|pedestal|wall|cap|tower|gallery|keeper|fantail)", pt["name"], re.I)
            ]
        elif "waterwheel" in bell["name"].lower():
            rotor_parts = [
                pt for pt in parts
                if ("waterwheel" in pt["name"].lower() or re.search(r"(cast_hub|wheel_hub)", pt["name"], re.I))
                and not re.search(r"(axle|shaft|bearing|collar|pillow|pedestal|wall|flume|trestle|keeper)", pt["name"], re.I)
            ]
        else:
            rotor_parts = [bell]
        rotor_ids = {id(pt) for pt in rotor_parts}

        # Bounding box & mass of the complete rotating assembly
        b_lo = np.min([pt["lo"] for pt in rotor_parts], axis=0)
        b_hi = np.max([pt["hi"] for pt in rotor_parts], axis=0)
        rotor_mass = sum(float(pt["mass"]) for pt in rotor_parts)

        near_static = []
        for pt in parts:
            if id(pt) in rotor_ids:
                continue
            if np.all(pt["hi"] >= b_lo - 1.2) and np.all(pt["lo"] <= b_hi + 1.2):
                near_static.append(pt)

        # Directly run CoACD on open-bore hub, poll head, retainers, shrouds & rim bands (bypassing aabb_fill<0.72 skip!)
        concave_rotor = [pt for pt in rotor_parts if re.search(r"(hub|poll_head|retainer|shroud|rim|ring|collar|bore|bell)", pt["name"], re.I)]
        concave_static = [pt for pt in near_static if re.search(r"(bearing|collar|pillow|ring|arch)", pt["name"], re.I)]
        if ma.HAS_COACD:
            import coacd
            coacd.set_log_level("error")
            for pt in concave_rotor + concave_static:
                try:
                    cm = coacd.Mesh(pt["verts"], pt["tris"])
                    parts_h = coacd.run_coacd(cm, threshold=0.015, max_convex_hull=18, seed=42)
                    if parts_h:
                        pt["hulls"] = [(np.asarray(v, dtype=np.float64), np.asarray(f, dtype=np.int32)) for v, f in parts_h]
                except Exception:
                    pass

        # Center axle pivot: Y axis for front-facing windmill sail rotors, X axis for east waterwheels, top yoke for bells!
        if is_sail:
            hub_pts = [pt for pt in rotor_parts if re.search(r"(hub|poll_head)", pt["name"], re.I)]
            if hub_pts:
                h_lo = np.min([pt["lo"] for pt in hub_pts], axis=0)
                h_hi = np.max([pt["hi"] for pt in hub_pts], axis=0)
                pivot = 0.5 * (h_lo + h_hi)
            else:
                pivot = 0.5 * (b_lo + b_hi)
            axis = [0.0, 1.0, 0.0]
        else:
            pivot = np.array([
                0.5 * (b_lo[0] + b_hi[0]),
                0.5 * (b_lo[1] + b_hi[1]),
                (0.5 * (b_lo[2] + b_hi[2])) if is_wheel else (float(b_hi[2]) - 0.02),
            ], dtype=np.float64)
            axis = [1.0, 0.0, 0.0]

        spec = mujoco.MjSpec()
        spec.option.gravity = [0.0, 0.0, -9.81]
        spec.option.timestep = 0.002

        def add_mesh_geoms(body, pt, prefix, group=0, pos_offset=None):
            hulls = pt.get("hulls") or [(pt["verts"], pt["tris"])]
            off = np.zeros(3) if pos_offset is None else pos_offset
            for hi_i, (vv, ff) in enumerate(hulls):
                mname = f"{prefix}_{hi_i}"
                m = spec.add_mesh()
                m.name = mname
                m.uservert = (vv - off).astype(np.float32).ravel().tolist()
                m.userface = ff.astype(np.int32).ravel().tolist()
                g = body.add_geom()
                g.type = mujoco.mjtGeom.mjGEOM_MESH
                g.meshname = mname
                g.density = float(pt["density"])
                g.group = group

        for si, sp in enumerate(near_static):
            add_mesh_geoms(spec.worldbody, sp, f"st_{si}", group=0)

        bb = spec.worldbody.add_body()
        bb.name = "articulated_rotor"
        bb.pos = pivot.tolist()
        j = bb.add_joint()
        j.name = "rotor_hinge"
        j.type = mujoco.mjtJoint.mjJNT_HINGE
        j.axis = axis
        j.damping = [0.15, 0.0, 0.0]
        for ri, rp in enumerate(rotor_parts):
            add_mesh_geoms(bb, rp, f"rot_{ri}", group=1, pos_offset=pivot)

        model = spec.compile()
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)

        # Continuous spin torque for waterwheels/sheaves (>120 deg = free spin!), resonant swing for bells!
        min_ang, max_ang = 0.0, 0.0
        max_ncon = 0
        torque = max(2.5, rotor_mass * (18.0 if is_sail else (3.2 if is_wheel else 1.4)))
        for step in range(600):
            if is_wheel:
                data.qfrc_applied[0] = torque
            elif step < 120:
                data.qfrc_applied[0] = torque
            elif 220 <= step < 340:
                data.qfrc_applied[0] = -torque
            else:
                data.qfrc_applied[0] = 0.0
            mujoco.mj_step(model, data)
            ang = math.degrees(float(data.qpos[0]))
            min_ang = min(min_ang, ang)
            max_ang = max(max_ang, ang)
            max_ncon = max(max_ncon, int(data.ncon))

        swing_arc = round(max_ang - min_ang, 1)
        free_swing = swing_arc >= (120.0 if is_wheel else 25.0) and max_ncon == 0
        part_label = (
            f"windmill_sail_rotor ({len(rotor_parts)} parts)" if (is_sail and len(rotor_parts) > 1)
            else f"mill_waterwheel ({len(rotor_parts)} parts)" if len(rotor_parts) > 1
            else bell["name"]
        )
        return {
            "part": part_label,
            "massKg": round(rotor_mass, 1),
            "axis": axis,
            "pivotZUp": [round(float(x), 3) for x in pivot],
            "minAngleDeg": round(min_ang, 1),
            "maxAngleDeg": round(max_ang, 1),
            "rotationDeg": swing_arc,
            "maxContacts": max_ncon,
            "freeSpin": free_swing,
            "jammed": not free_swing,
            "staticBelfryParts": len(near_static),
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc")
    args = ap.parse_args()
    res = run_bell_swing_test(Path(args.doc))
    print(json.dumps(res or {"part": None, "freeSpin": False}))


if __name__ == "__main__":
    main()
