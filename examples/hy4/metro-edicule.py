#!/usr/bin/env python3
"""Art Nouveau metro entrance: a Guimard-style ecule with stair, balustrade,
lamps, arched sign and a convex glazed dragonfly canopy.

Five-function modular builder.  All geometry is derived from the two controls
(width, length) through the shared DATUMS table.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "procgen" / "recipes"))

from kitlib import (
    Kit, Graph, C, control, port, ref, review,
    lt, gt, iff, mn, mx, absx, sin, cos, sqrt, atan2, floor, ceil, PI, INDEX,
)
from kitparts import filleted_box, sds_cylinder
from kittype import arch_sign_board

ROOT = Path(__file__).resolve().parent.parent

DATUMS = {
    "ground_z": 0.0,
    "curb_t": 0.20,
    "curb_h": 0.16,
    "pave_t": 0.12,
    "rail_z": 0.96,
    "lower_rail_z": 0.42,
    "post_r": 0.042,
    "eave_z": 2.74,
    "ridge_z": 3.26,
    "mid_z": 2.88,
    "lamp_arm_z": 2.86,
    "lamp_z": 2.54,
    "sign_z_ends": 2.86,
    "sign_rise": 0.34,
    "sign_band": 0.58,
    "step_rise": 0.17,
    "step_count": 8,
    "butt_gap_m": 0.0005,
    "bore_clear_m": 0.0018,
    "min_materials": 4,
}

REFS = {
    "metro-edicule": ref(
        atlas=["IW04", "IW14", "N04", "N05"],
        sources=[],
        plates=["s14"],
        notes="Guimard-style cast-iron and glass station entrance; lamp arms, whiplash panels and convex canopy are design interpretations, not measured source dimensions.",
        other=["brief: Art Nouveau metro entrance"],
        review=review(
            "The stair flight is a regular run of treads rather than a genuine Guimard riser detail, and the glass canopy is a faceted approximation of the curved dragonfly wing.",
            improve=["Model swept glass panels and cast leaf bosses at higher fidelity [brief]"],
            by="KIT-FORGE bench builder",
            seen="iso beauty, opposite beauty, front, clay and parts renders",
        ),
    ),
}


def build_base(g: Graph, DATUMS: dict, C) -> list[str]:
    """Limestone curbs, paved aprons and a coursed retaining shaft around the stair."""
    W, L = C("width"), C("length")
    hw, hl = g.let("hw_base", W * 0.5), g.let("hl_base", L * 0.5)
    t, ch, pt = DATUMS["curb_t"], DATUMS["curb_h"], DATUMS["pave_t"]
    parts = []

    # Raised limestone curbs on the three closed sides of the opening.
    for s, sx in (("l", -1.0), ("r", 1.0)):
        filleted_box(g, f"curb_{s}_geom", [t, L + 2 * t, ch],
                     at=[sx * (hw + t * 0.5), 0, ch * 0.5], width=0.014, mode="fwn")
        g.mat(f"curb_{s}", f"curb_{s}_geom", "chipped-limestone")
        parts.append(f"curb_{s}")
        # Pavement strips outside each curb, top face at pavement datum.
        g.box(f"pave_{s}_raw", [0.86, L + 2 * t, pt],
              at=[sx * (hw + t + 0.43), 0, -pt * 0.5])
        g.mat(f"pave_{s}", f"pave_{s}_raw", "chipped-limestone")
        parts.append(f"pave_{s}")

    filleted_box(g, "curb_far_geom", [W + 2 * t, t, ch],
                 at=[0, hl + t * 0.5, ch * 0.5], width=0.014, mode="fwn")
    g.mat("curb_far", "curb_far_geom", "chipped-limestone")
    parts.append("curb_far")
    g.box("pave_far_raw", [W + 2 * t + 1.72, 0.80, pt],
          at=[0, hl + t + 0.40, -pt * 0.5])
    g.mat("pave_far", "pave_far_raw", "chipped-limestone")
    parts.append("pave_far")

    # Entrance apron kept clear in the middle so the deck port stays open.
    for s, sx in (("l", -1.0), ("r", 1.0)):
        g.box(f"apron_{s}_raw", [hw + t + 0.86, 0.74, pt],
              at=[sx * (hw + t + 0.86) * 0.5, -hl - t - 0.37, -pt * 0.5])
        g.mat(f"apron_{s}", f"apron_{s}_raw", "chipped-limestone")
        parts.append(f"apron_{s}")

    # Kerb coping strips, slightly proud of the stone, in dark cast iron.
    for s, sx in (("l", -1.0), ("r", 1.0)):
        g.box(f"kerb_edge_{s}_raw", [0.055, L + 2 * t, 0.026],
              at=[sx * (hw + 0.028), 0, ch + 0.013])
        g.mat(f"kerb_edge_{s}", f"kerb_edge_{s}_raw", "iron")
        parts.append(f"kerb_edge_{s}")

    # Coursed ashlar retaining walls beneath the curbs: the stair now descends
    # into a real shaft rather than floating past the edge of the pavement.
    wall_h = 1.62
    for s, sx in (("l", -1.0), ("r", 1.0)):
        g.box(f"shaft_wall_{s}_raw", [t, L + 0.10, wall_h],
              at=[sx * (hw + t * 0.5), 0.05, -wall_h * 0.5])
        g.mat(f"shaft_wall_{s}", f"shaft_wall_{s}_raw", "chipped-limestone")
        parts.append(f"shaft_wall_{s}")
        for k in range(4):
            z = -0.30 - 0.34 * k
            g.box(f"shaft_course_{s}_{k}_raw", [0.012, L + 0.02, 0.014],
                  at=[sx * (hw - 0.005), 0.05, z])
            g.mat(f"shaft_course_{s}_{k}", f"shaft_course_{s}_{k}_raw", "iron")
            parts.append(f"shaft_course_{s}_{k}")
            for j in range(6):
                y = -hl + 0.20 + j * (L - 0.30) / 5 + (0.14 if k % 2 else 0.0)
                g.box(f"shaft_joint_{s}_{k}_{j}_raw", [0.012, 0.012, 0.32],
                      at=[sx * (hw - 0.005), y, z - 0.16])
                g.mat(f"shaft_joint_{s}_{k}_{j}", f"shaft_joint_{s}_{k}_{j}_raw", "iron")
                parts.append(f"shaft_joint_{s}_{k}_{j}")
    g.box("shaft_wall_far_raw", [W + 2 * t, t, wall_h],
          at=[0, hl + t * 0.5, -wall_h * 0.5])
    g.mat("shaft_wall_far", "shaft_wall_far_raw", "chipped-limestone")
    parts.append("shaft_wall_far")
    for k in range(4):
        z = -0.30 - 0.34 * k
        g.box(f"shaft_course_far_{k}_raw", [W + 0.06, 0.012, 0.014],
              at=[0, hl - 0.005, z])
        g.mat(f"shaft_course_far_{k}", f"shaft_course_far_{k}_raw", "iron")
        parts.append(f"shaft_course_far_{k}")

    # The exterior of the buried shaft is also coursed: the rear elevation is
    # a finished ashlar plinth, not a blank concrete block.
    for s, sx in (("l", -1.0), ("r", 1.0)):
        for k in range(5):
            z = -0.16 - 0.31 * k
            g.box(f"shaft_ext_course_{s}_{k}_raw", [0.012, L + 2 * t, 0.014],
                  at=[sx * (hw + t + 0.005), 0, z])
            g.mat(f"shaft_ext_course_{s}_{k}", f"shaft_ext_course_{s}_{k}_raw", "iron")
            parts.append(f"shaft_ext_course_{s}_{k}")
            for j in range(5):
                y = -hl + 0.20 + j * (L - 0.60) / 4 + (0.12 if k % 2 else 0.0)
                g.box(f"shaft_ext_joint_{s}_{k}_{j}_raw", [0.012, 0.012, 0.29],
                      at=[sx * (hw + t + 0.005), y, z - 0.15])
                g.mat(f"shaft_ext_joint_{s}_{k}_{j}", f"shaft_ext_joint_{s}_{k}_{j}_raw", "iron")
                parts.append(f"shaft_ext_joint_{s}_{k}_{j}")
    for k in range(5):
        z = -0.16 - 0.31 * k
        g.box(f"shaft_ext_course_far_{k}_raw", [W + 2 * t + 0.02, 0.012, 0.014],
              at=[0, hl + t + 0.005, z])
        g.mat(f"shaft_ext_course_far_{k}", f"shaft_ext_course_far_{k}_raw", "iron")
        parts.append(f"shaft_ext_course_far_{k}")
        for j in range(5):
            x = -hw - t + 0.20 + j * (W + 2 * t - 0.90) / 4 + (0.12 if k % 2 else 0.0)
            g.box(f"shaft_ext_joint_far_{k}_{j}_raw", [0.012, 0.012, 0.29],
                  at=[x, hl + t + 0.005, z - 0.15])
            g.mat(f"shaft_ext_joint_far_{k}_{j}", f"shaft_ext_joint_far_{k}_{j}_raw", "iron")
            parts.append(f"shaft_ext_joint_far_{k}_{j}")
    return parts


def build_primary_mass(g: Graph, DATUMS: dict, C) -> list[str]:
    """The first flight: 0.30 m going / 0.17 m rise, ending on a dark landing."""
    W, L = C("width"), C("length")
    hw, hl = g.let("hw_primary", W * 0.5), g.let("hl_primary", L * 0.5)
    n, rise, going = DATUMS["step_count"], DATUMS["step_rise"], 0.30
    drop = rise * n
    start_y = g.let("flight_start_y", -hl + 0.25)
    end_y = g.let("flight_end_y", start_y + n * going)
    parts = []

    g.box("step_raw", [W - 0.05, going + 0.012, 0.21],
          at=[0, 0, -0.105])
    g.mat("step_proto", "step_raw", "chipped-limestone")
    g.array("steps", "step_proto", [0, start_y, 0], [0, going, -rise], n)
    parts.append("steps")

    # Lower landing continues the flight toward the +Y stair port.
    land_len = g.let("landing_len", L - 2.65)
    g.box("lower_landing_raw", [W - 0.06, land_len, 0.14],
          at=[0, (end_y + hl) * 0.37979, -drop - 0.07])
    g.mat("lower_landing", "lower_landing_raw", "iron")
    parts.append("lower_landing")

    # Inclined iron stringers follow only the treaded portion of the flight.
    slope = -atan2(drop, n * going)
    for s, sx in (("l", -1.0), ("r", 1.0)):
        g.box(f"stringer_{s}_raw", [0.055, n * going + 0.12, 0.30], at=[0, 0, 0])
        g.xf(f"stringer_{s}_tilt", f"stringer_{s}_raw", r=[slope, 0, 0])
        g.xf(f"stringer_{s}_place", f"stringer_{s}_tilt",
             t=[sx * (hw - 0.02), start_y + n * going * 0.5, -drop * 0.5 - 0.02])
        g.mat(f"stringer_{s}", f"stringer_{s}_place", "patina")
        parts.append(f"stringer_{s}")
    return parts


def build_openings_or_mechanism(g: Graph, DATUMS: dict, C) -> list[str]:
    """Balustrade posts and moulded rails; entrance remains open at -Y."""
    W, L = C("width"), C("length")
    hw, hl = g.let("hw_rail", W * 0.5), g.let("hl_rail", L * 0.5)
    rz, ch, pr = DATUMS["rail_z"], DATUMS["curb_h"], DATUMS["post_r"]
    parts = []

    # One post prototype: square shoe, tapered shaft, collar, and finial.
    g.box("post_shoe_raw", [0.13, 0.13, 0.07], at=[0, 0, ch + 0.035])
    g.mat("post_shoe", "post_shoe_raw", "iron")
    g.rod("post_stem_raw", [0, 0, ch + 0.06], [0, 0, rz - 0.01], pr, cap="quad")
    g.mat("post_stem", "post_stem_raw", "patina")
    sds_cylinder(g, "post_collar_raw", 0.066, 0.045,
                 at=[0, 0, ch + 0.40], sides=12, lod_levels=[2, 1, 0])
    g.mat("post_collar", "post_collar_raw", "iron")
    g.rod("post_finial_raw", [0, 0, rz - 0.04], [0, 0, rz + 0.075], 0.046, cap="quad")
    g.mat("post_finial", "post_finial_raw", "patina")
    g.join("post_proto", "post_shoe", "post_stem", "post_collar", "post_finial")

    # Five posts down each long side, four across the closed end.
    step_y = g.let("post_pitch_y", (L - 0.34) / 4)
    step_x = g.let("post_pitch_x", (W - 0.34) / 3)
    for s, sx in (("l", -1.0), ("r", 1.0)):
        g.xf(f"posts_{s}_raw", "post_proto", t=[sx * hw, -hl + 0.17, 0])
        g.mat(f"posts_{s}_base", f"posts_{s}_raw", "patina")
        g.array(f"posts_{s}", f"posts_{s}_base", [0, 0, 0], [0, step_y, 0], 5)
        parts.append(f"posts_{s}")
    g.xf("posts_far_raw", "post_proto", t=[-hw + 0.17, hl - 0.04, 0])
    g.mat("posts_far_base", "posts_far_raw", "patina")
    g.array("posts_far", "posts_far_base", [0, 0, 0], [step_x, 0, 0], 4)
    parts.append("posts_far")

    # Two moulded rails per elevation: a round bar and a flat cap.
    for s, sx in (("l", -1.0), ("r", 1.0)):
        for lvl, z, rad in (("top", rz, 0.036), ("low", DATUMS["lower_rail_z"], 0.022)):
            g.rod(f"rail_{s}_{lvl}_raw", [sx * hw, -hl, z], [sx * hw, hl, z], rad, cap="quad")
            g.mat(f"rail_{s}_{lvl}", f"rail_{s}_{lvl}_raw", "patina")
            parts.append(f"rail_{s}_{lvl}")
        g.box(f"rail_cap_{s}_raw", [0.10, L + 0.10, 0.030],
              at=[sx * hw, 0, rz + 0.046])
        g.mat(f"rail_cap_{s}", f"rail_cap_{s}_raw", "iron")
        parts.append(f"rail_cap_{s}")
    for lvl, z, rad in (("top", rz, 0.036), ("low", DATUMS["lower_rail_z"], 0.022)):
        g.rod(f"rail_far_{lvl}_raw", [-hw, hl, z], [hw, hl, z], rad, cap="quad")
        g.mat(f"rail_far_{lvl}", f"rail_far_{lvl}_raw", "patina")
        parts.append(f"rail_far_{lvl}")
    g.box("rail_cap_far_raw", [W + 0.10, 0.10, 0.030], at=[0, hl, rz + 0.046])
    g.mat("rail_cap_far", "rail_cap_far_raw", "iron")
    parts.append("rail_cap_far")
    return parts


def build_secondary_craft(g: Graph, DATUMS: dict, C) -> list[str]:
    """Whiplash leaves, freestanding lamp standards, arched sign, convex canopy."""
    W, L = C("width"), C("length")
    hw, hl = g.let("hw_craft", W * 0.5), g.let("hl_craft", L * 0.5)
    rz, ch = DATUMS["rail_z"], DATUMS["curb_h"]
    parts = []

    # --- Organic stem-and-leaf infill panels -------------------------------
    # Authored directly in the two balustrade planes: no intermediate rotations.
    def whiplash(prefix, axis, fixed, start, bay):
        z0, z1 = ch + 0.11, rz - 0.09
        pts = [
            (0.02 * bay, z0), (0.30 * bay, z0 + (z1 - z0) * 0.80),
            (0.53 * bay, z0 + (z1 - z0) * 0.26),
            (0.77 * bay, z0 + (z1 - z0) * 0.74), (0.98 * bay, z1),
        ]

        def P(u, z):
            if axis == "y":
                return [fixed, start + u, z]
            return [start + u, fixed, z]

        segs = []
        # Catmull-Rom whiplash: a continuous cast stem instead of straight
        # zigzag bracing, sampled into short rods that stay inside the panel.
        def sample(i, t, axis_index):
            a = pts[max(0, i - 1)][axis_index]
            b = pts[i][axis_index]
            c = pts[i + 1][axis_index]
            d = pts[min(len(pts) - 1, i + 2)][axis_index]
            return 0.5 * ((2 * b) + (-a + c) * t
                          + (2 * a - 5 * b + 4 * c - d) * t * t
                          + (-a + 3 * b - 3 * c + d) * t * t * t)

        for i in range(len(pts) - 1):
            for j in range(6):
                t0, t1 = j / 6.0, (j + 1) / 6.0
                pa = (sample(i, t0, 0), sample(i, t0, 1))
                pb = (sample(i, t1, 0), sample(i, t1, 1))
                na, nb = f"{prefix}_stem{i}_{j}_raw", f"{prefix}_stem{i}_{j}"
                g.rod(na, P(pa[0], pa[1]), P(pb[0], pb[1]), 0.015, cap="quad")
                g.mat(nb, na, "iron")
                segs.append(nb)
        # Closed spindle leaves, leaning alternately along the whiplash stem.
        for i, (u, z) in enumerate(pts[1:4]):
            ln = f"{prefix}_leaf{i}"
            if axis == "y":
                g.rod(f"{ln}_raw", [0, -0.10, 0], [0, 0.10, 0], 0.049, cap="quad")
                g.xf(f"{ln}_lean", f"{ln}_raw", r=[(-0.68 if i % 2 == 0 else 0.68), 0, 0])
            else:
                g.rod(f"{ln}_raw", [-0.10, 0, 0], [0.10, 0, 0], 0.049, cap="quad")
                g.xf(f"{ln}_lean", f"{ln}_raw", r=[0, (-0.68 if i % 2 == 0 else 0.68), 0])
            g.xf(f"{ln}_place", f"{ln}_lean", t=P(u, z))
            g.mat(ln, f"{ln}_place", "patina")
            segs.append(ln)
        # Slim uprights at the bay ends tie the infill into both rails and posts.
        for k, u in enumerate((0.0, bay)):
            na, nb = f"{prefix}_tie{k}_raw", f"{prefix}_tie{k}"
            g.rod(na, P(u, z0 - 0.03), P(u, rz + 0.02), 0.014, cap="quad")
            g.mat(nb, na, "iron")
            segs.append(nb)
        g.join(prefix, *segs)
        parts.append(prefix)

    bay_y = g.let("bay_y", (L - 0.34) / 4)
    for side, sx in (("l", -1.0), ("r", 1.0)):
        for i in range(4):
            whiplash(f"panel_{side}_{i}", "y", sx * hw,
                     -hl + 0.18 + i * bay_y, bay_y)

    bay_x = g.let("bay_x", (W - 0.34) / 3)
    for i in range(3):
        whiplash(f"panel_far_{i}", "x", hl - 0.05,
                 -hw + 0.17 + i * bay_x, bay_x)

    # --- Freestanding lamp standards at the entrance corners ---------------
    # They stand forward of the sign plane; the lanterns project OUTBOARD of
    # the sign ends so they cannot be mistaken for sign fittings.
    lamp_y = g.let("lamp_standard_y", -hl - 0.45)
    az = DATUMS["lamp_arm_z"]
    for side, sx in (("l", -1.0), ("r", 1.0)):
        x = sx * (hw + 0.06)
        filleted_box(g, f"lamp_base_{side}_geom", [0.28, 0.28, 0.18],
                     at=[x, lamp_y, 0.09], width=0.018, mode="fwn")
        g.mat(f"lamp_base_{side}", f"lamp_base_{side}_geom", "iron")
        parts.append(f"lamp_base_{side}")
        g.box(f"lamp_plinth_{side}_raw", [0.215, 0.215, 0.085],
              at=[x, lamp_y, 0.223])
        g.mat(f"lamp_plinth_{side}", f"lamp_plinth_{side}_raw", "patina")
        parts.append(f"lamp_plinth_{side}")

        g.rod(f"lamp_stem_{side}_raw", [x, lamp_y, 0.26],
              [x, lamp_y, az + 0.09], 0.054, cap="quad")
        g.mat(f"lamp_stem_{side}", f"lamp_stem_{side}_raw", "patina")
        parts.append(f"lamp_stem_{side}")
        # Four raised flutes running up the cast shaft.
        for k, (dx, dy) in enumerate(((0.038, 0.0), (-0.038, 0.0),
                                      (0.0, 0.038), (0.0, -0.038))):
            g.rod(f"lamp_flute_{side}_{k}_raw",
                  [x + dx, lamp_y + dy, 0.40],
                  [x + dx, lamp_y + dy, az - 0.20], 0.009, cap="quad")
            g.mat(f"lamp_flute_{side}_{k}", f"lamp_flute_{side}_{k}_raw", "iron")
            parts.append(f"lamp_flute_{side}_{k}")
        for lvl, cz in (("low", 0.78), ("high", 2.36)):
            sds_cylinder(g, f"lamp_collar_{side}_{lvl}_raw", 0.079, 0.055,
                         at=[x, lamp_y, cz], sides=12, lod_levels=[2, 1, 0])
            g.mat(f"lamp_collar_{side}_{lvl}", f"lamp_collar_{side}_{lvl}_raw", "iron")
            parts.append(f"lamp_collar_{side}_{lvl}")
        sds_cylinder(g, f"lamp_capital_{side}_raw", 0.093, 0.085,
                     at=[x, lamp_y, az - 0.01], sides=12, lod_levels=[2, 1, 0])
        g.mat(f"lamp_capital_{side}", f"lamp_capital_{side}_raw", "iron")
        parts.append(f"lamp_capital_{side}")
        g.rod(f"lamp_finial_{side}_raw", [x, lamp_y, az + 0.08],
              [x, lamp_y, az + 0.25], 0.026, cap="quad")
        g.mat(f"lamp_finial_{side}", f"lamp_finial_{side}_raw", "bronze")
        parts.append(f"lamp_finial_{side}")

        # Curved cast arm, branching away from the sign towards the lantern.
        arm = [
            [x, lamp_y, az],
            [x + sx * 0.14, lamp_y - 0.06, az + 0.14],
            [x + sx * 0.36, lamp_y - 0.18, az + 0.24],
            [x + sx * 0.60, lamp_y - 0.32, az + 0.17],
            [x + sx * 0.74, lamp_y - 0.46, az - 0.02],
        ]
        for j in range(len(arm) - 1):
            g.rod(f"lamp_arm_{side}_{j}_raw", arm[j], arm[j + 1],
                  0.038 - 0.004 * j, cap="quad")
            g.mat(f"lamp_arm_{side}_{j}", f"lamp_arm_{side}_{j}_raw", "patina")
            parts.append(f"lamp_arm_{side}_{j}")
        # Whipped leaf spring at the bend of the arm.
        g.rod(f"lamp_leaf_{side}_raw",
              [0, -0.11, 0], [0, 0.11, 0],
              0.048, cap="quad")
        g.xf(f"lamp_leaf_{side}_lean", f"lamp_leaf_{side}_raw",
             r=[0, 0, sx * 0.62])
        g.xf(f"lamp_leaf_{side}_place", f"lamp_leaf_{side}_lean",
             t=[x + sx * 0.36, lamp_y - 0.18, az + 0.25])
        g.mat(f"lamp_leaf_{side}", f"lamp_leaf_{side}_place", "iron")
        parts.append(f"lamp_leaf_{side}")

        lx = x + sx * 0.74
        ly = lamp_y - 0.46
        g.rod(f"lamp_hanger_{side}_raw", arm[-1], [lx, ly, 2.72],
              0.015, cap="quad")
        g.mat(f"lamp_hanger_{side}", f"lamp_hanger_{side}_raw", "iron")
        parts.append(f"lamp_hanger_{side}")
        # Faceted orange-red glass body with a cast cage and a crown.
        g.rod(f"lamp_glass_{side}_raw", [lx, ly, 2.40], [lx, ly, 2.72],
              0.145, cap="quad")
        g.mat(f"lamp_glass_{side}", f"lamp_glass_{side}_raw", "terracotta")
        parts.append(f"lamp_glass_{side}")
        for k in range(6):
            a = 2.0 * PI * k / 6.0
            g.rod(f"lamp_cage_{side}_{k}_raw",
                  [lx + 0.148 * cos(a), ly + 0.148 * sin(a), 2.405],
                  [lx + 0.148 * cos(a), ly + 0.148 * sin(a), 2.715],
                  0.008, cap="quad")
            g.mat(f"lamp_cage_{side}_{k}", f"lamp_cage_{side}_{k}_raw", "iron")
            parts.append(f"lamp_cage_{side}_{k}")
        for capname, cz in (("top", 2.72), ("bottom", 2.37)):
            g.rod(f"lamp_{capname}_{side}_raw", [lx, ly, cz],
                  [lx, ly, cz + 0.035], 0.166, cap="quad")
            g.mat(f"lamp_{capname}_{side}", f"lamp_{capname}_{side}_raw", "iron")
            parts.append(f"lamp_{capname}_{side}")
        g.rod(f"lamp_crown_{side}_raw", [lx, ly, 2.755], [lx, ly, 2.84],
              0.090, cap="quad")
        g.mat(f"lamp_crown_{side}", f"lamp_crown_{side}_raw", "patina")
        parts.append(f"lamp_crown_{side}")
        g.rod(f"lamp_crown_finial_{side}_raw", [lx, ly, 2.84],
              [lx, ly, 2.94], 0.024, cap="quad")
        g.mat(f"lamp_crown_finial_{side}", f"lamp_crown_finial_{side}_raw", "bronze")
        parts.append(f"lamp_crown_finial_{side}")
        # A second, lower whiplash branch makes the standard read as a cast
        # tree in every view, quite apart from the arch of the sign.
        g.rod(f"lamp_branch_{side}_raw", [x, lamp_y, az - 0.42],
              [lx - sx * 0.12, ly + 0.10, 2.80], 0.019, cap="quad")
        g.mat(f"lamp_branch_{side}", f"lamp_branch_{side}_raw", "patina")
        parts.append(f"lamp_branch_{side}")
        g.rod(f"lamp_branch_tip_{side}_raw",
              [lx - sx * 0.12, ly + 0.10, 2.80], [lx, ly, 2.84],
              0.015, cap="quad")
        g.mat(f"lamp_branch_tip_{side}", f"lamp_branch_tip_{side}_raw", "iron")
        parts.append(f"lamp_branch_tip_{side}")

    # --- Arched enamel sign held between the two standards -----------------
    sign = arch_sign_board(
        g, "metropolitain", "METROPOLITAIN",
        span=W + 0.12, band_height=DATUMS["sign_band"],
        z_ends=DATUMS["sign_z_ends"], rise=DATUMS["sign_rise"],
        y=lamp_y, thickness=0.06, both_sides=True,
        board_slot="terracotta", rim_slot="iron", text_slot="marking",
        spread=0.12,
    )
    if isinstance(sign, str):
        sign_ids = [sign]
    elif isinstance(sign, dict):
        sign_ids = [v for v in sign.values() if isinstance(v, str)]
    elif isinstance(sign, (list, tuple)):
        sign_ids = [v for v in sign if isinstance(v, str)]
    else:
        sign_ids = []
    parts.extend(sign_ids)

    # --- Canopy: taller corner posts, iron ribs and convex glazed wings ----
    for side, sx in (("l", -1.0), ("r", 1.0)):
        for end, ey in (("front", -hl + 0.18), ("back", hl - 0.17)):
            g.rod(f"canopy_post_{side}_{end}_raw", [sx * hw, ey, rz - 0.02],
                  [sx * hw, ey, DATUMS["eave_z"] + 0.04], 0.046, cap="quad")
            g.mat(f"canopy_post_{side}_{end}", f"canopy_post_{side}_{end}_raw", "iron")
            parts.append(f"canopy_post_{side}_{end}")
            g.rod(f"eave_bracket_{side}_{end}_raw",
                  [sx * hw, ey, DATUMS["eave_z"] - 0.02],
                  [sx * (hw + 0.07), ey, DATUMS["eave_z"]], 0.030, cap="quad")
            g.mat(f"eave_bracket_{side}_{end}", f"eave_bracket_{side}_{end}_raw", "patina")
            parts.append(f"eave_bracket_{side}_{end}")

    Lc = g.let("canopy_len", L - 0.42)
    ridge_z, mid_z, eave_z = DATUMS["ridge_z"], DATUMS["mid_z"], DATUMS["eave_z"]
    stations = [(0.0, ridge_z), (hw * 0.42, ridge_z - 0.22),
                (hw * 0.78, mid_z), (hw + 0.07, eave_z)]
    for side, sx in (("l", -1.0), ("r", 1.0)):
        for j in range(len(stations) - 1):
            x0, z0 = stations[j]
            x1, z1 = stations[j + 1]
            dx, dz = sx * (x1 - x0), z1 - z0
            seg = sqrt(dx * dx + dz * dz)
            ang = atan2(-dz, dx)
            g.box(f"glass_{side}_{j}_raw", [seg + 0.024, Lc, 0.014], at=[0, 0, 0])
            g.xf(f"glass_{side}_{j}_tilt", f"glass_{side}_{j}_raw", r=[0, ang, 0])
            g.xf(f"glass_{side}_{j}_place", f"glass_{side}_{j}_tilt",
                 t=[sx * (x0 + x1) * 0.5, 0, (z0 + z1) * 0.5])
            g.mat(f"glass_{side}_{j}", f"glass_{side}_{j}_place", "glass")
            parts.append(f"glass_{side}_{j}")
            g.rod(f"glazing_bar_{side}_{j}_raw",
                  [sx * x0, -Lc * 0.5, z0], [sx * x0, Lc * 0.5, z0],
                  0.021, cap="quad")
            g.mat(f"glazing_bar_{side}_{j}", f"glazing_bar_{side}_{j}_raw", "iron")
            parts.append(f"glazing_bar_{side}_{j}")

    for k, (x, z) in enumerate(stations):
        for side, sx in (("l", -1.0), ("r", 1.0)):
            if k == 0 and sx < 0:
                continue
            g.rod(f"purlin_{k}_{side}_raw", [sx * x, -Lc * 0.5, z],
                  [sx * x, Lc * 0.5, z], 0.026 if k else 0.034, cap="quad")
            g.mat(f"purlin_{k}_{side}", f"purlin_{k}_{side}_raw", "iron")
            parts.append(f"purlin_{k}_{side}")
    for i in range(5):
        y = -Lc * 0.5 + Lc * i / 4
        for j in range(len(stations) - 1):
            for side, sx in (("l", -1.0), ("r", 1.0)):
                p, q = stations[j], stations[j + 1]
                g.rod(f"rib_{i}_{j}_{side}_raw",
                      [sx * p[0], y, p[1]], [sx * q[0], y, q[1]],
                      0.018, cap="quad")
                g.mat(f"rib_{i}_{j}_{side}", f"rib_{i}_{j}_{side}_raw", "iron")
                parts.append(f"rib_{i}_{j}_{side}")
        # Drop-forged arch at each rib station, tying both wings together.
        g.rod(f"transom_{i}_raw", [0, y, ridge_z - 0.026],
              [0, y, ridge_z + 0.042], 0.026, cap="quad")
        g.mat(f"transom_{i}", f"transom_{i}_raw", "bronze")
        parts.append(f"transom_{i}")
    # Heavy cast eave fascias and a continuous ridge crest.
    for side, sx in (("l", -1.0), ("r", 1.0)):
        g.rod(f"fascia_{side}_raw",
              [sx * (hw + 0.07), -Lc * 0.5 - 0.06, eave_z - 0.035],
              [sx * (hw + 0.07), Lc * 0.5 + 0.06, eave_z - 0.035],
              0.046, cap="quad")
        g.mat(f"fascia_{side}", f"fascia_{side}_raw", "iron")
        parts.append(f"fascia_{side}")
        for i in range(7):
            y = -Lc * 0.5 + Lc * i / 6
            g.rod(f"eave_scroll_{side}_{i}_raw",
                  [sx * (hw + 0.07), y, eave_z - 0.06],
                  [sx * (hw + 0.14), y, eave_z - 0.17],
                  0.014, cap="quad")
            g.mat(f"eave_scroll_{side}_{i}", f"eave_scroll_{side}_{i}_raw", "patina")
            parts.append(f"eave_scroll_{side}_{i}")
    return parts


def build_details_and_signage(g: Graph, DATUMS: dict, C) -> list[str]:
    """Post-SDS surface detail: rail fasteners, stone jointing, lettering trim."""
    W, L = C("width"), C("length")
    hw, hl = g.let("hw_detail", W * 0.5), g.let("hl_detail", L * 0.5)
    parts = []

    # Cast rosettes on the long rail at every post, slightly proud of the cap.
    step_y = g.let("rosette_pitch", (L - 0.34) / 4)
    for side, sx in (("l", -1.0), ("r", 1.0)):
        for i in range(5):
            y = -hl + 0.17 + i * step_y
            g.rod(f"rosette_{side}_{i}_raw",
                  [sx * hw, y, DATUMS["rail_z"] + 0.055],
                  [sx * hw, y, DATUMS["rail_z"] + 0.085], 0.058, cap="quad")
            g.mat(f"rosette_{side}_{i}", f"rosette_{side}_{i}_raw", "bronze")
            parts.append(f"rosette_{side}_{i}")

    # Thin incised course lines on the outer curb faces, offset from the FWN bevel.
    for side, sx in (("l", -1.0), ("r", 1.0)):
        for i in range(2):
            z = 0.045 + 0.055 * i
            g.box(f"curb_joint_{side}_{i}_raw", [0.008, L + 0.32, 0.008],
                  at=[sx * (hw + DATUMS["curb_t"] + 0.002), 0, z])
            g.mat(f"curb_joint_{side}_{i}", f"curb_joint_{side}_{i}_raw", "iron")
            parts.append(f"curb_joint_{side}_{i}")

    # Incised pavement joints read as coursing on the two entrance aprons.
    pave_pitch = g.let("pave_pitch", L / 6)
    for side, sx in (("l", -1.0), ("r", 1.0)):
        for i in range(6):
            y = -hl + (i + 0.5) * pave_pitch
            g.box(f"pave_joint_{side}_{i}_raw", [0.82, 0.010, 0.006],
                  at=[sx * (hw + 0.30), y, 0.003])
            g.mat(f"pave_joint_{side}_{i}", f"pave_joint_{side}_{i}_raw", "iron")
            parts.append(f"pave_joint_{side}_{i}")

    # Decorative cast finials along the canopy ridge.
    finial_pitch = g.let("finial_pitch", (L - 0.70) / 4)
    for i in range(5):
        y = -L * 0.5 + 0.35 + i * finial_pitch
        g.rod(f"ridge_finial_{i}_raw", [0, y, DATUMS["ridge_z"] + 0.01],
              [0, y, DATUMS["ridge_z"] + 0.13], 0.042, cap="quad")
        g.mat(f"ridge_finial_{i}", f"ridge_finial_{i}_raw", "patina")
        parts.append(f"ridge_finial_{i}")
    return parts


def build_module():
    kit = Kit(ROOT, "hy4/out/metro-edicule", "metro-edicule")
    g = Graph()
    controls = {
        "width": control(2.2, 1.6, 3.2),
        "length": control(5, 3.5, 7),
    }
    ports = {
        "pavement": port([0.0, C("length") * -0.5, 0.0], [0.0, -1.0, 0.0], "deck",
                         {"width": "$width"}),
        "stair": port([0.0, C("length") * 0.5,
                       C("length") * -0.5 * 0.55], [0.0, 1.0, 0.0], "deck",
                      {"width": "$width"}),
    }
    parts = []
    parts.extend(build_base(g, DATUMS, C))
    parts.extend(build_primary_mass(g, DATUMS, C))
    parts.extend(build_openings_or_mechanism(g, DATUMS, C))
    parts.extend(build_secondary_craft(g, DATUMS, C))
    parts.extend(build_details_and_signage(g, DATUMS, C))
    g.join("root", *parts)
    kit.module("metro-edicule", controls, g, "root", ports)
    kit.close()


if __name__ == "__main__":
    build_module()
