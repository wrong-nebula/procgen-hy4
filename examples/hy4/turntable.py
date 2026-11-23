#!/usr/bin/env python3
"""Built by Hy4 Flash (tencent/hy4-flash, xhigh) in the 2026-11-21 showdown. One later human edit: the
`wall` control (0 = Hy4's block-built pit, the default; 1 = one turned wall ring, one coping ring, plain floor).

KIT-FORGE modular builder: a late-19th-century railway turntable in its pit.

The masonry pit, ring rail and approach tracks are static; the girder bridge,
deck, track, end carriages and turning gear are assembled in a local frame and
rotated about the king-pin by the `angle` control.  All geometry is derived from
the `diameter` control through the shared DATUMS/derived datum table.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "procgen" / "recipes"))


from kitlib import (
    Kit, Graph, C, control, port, ref, review,
    mn, mx, absx, sin, cos, sqrt, atan2, PI,
)
from kitparts import filleted_box, sds_cylinder, bored_collar, bored_nut

ROOT = Path(__file__).resolve().parent.parent

# Material nodes need their own ids; keep the geometric node name as the source.
_original_mat = Graph.mat
MAT_OF = {}


def _named_mat(self, id_, src, slot):
    mat_id = ("mat_" + id_) if id_ == src else id_
    node = _original_mat(self, mat_id, src, slot)
    MAT_OF[src] = mat_id
    return node


Graph.mat = _named_mat


def _material_leaf(id_):
    """Material nodes are the renderable leaves; return one when a node was shaded."""
    return MAT_OF.get(id_, id_)

GAUGE = 1.435
WALL_SEGMENTS = 64
COPING_SEGMENTS = 64
DECK_PLANKS = 48
SLEEPERS = 26


def TUNE(default, lo=None, hi=None):
    return default


REFS = {
    "turntable": ref(
        ["SS13", "SS14", "SS15", "SS16"],
        ["cherbourg", "eiffel-vi"],
        ["s25", "s11"],
        "Hydraulic-lift and yard group atlas SS13-SS16: a single-track girder bridge pivoting on a "
        "central king-pin in a circular masonry pit, carried on a ring rail by end wheels and turned "
        "by hand.  Coping, rail gauge, pit depth and rivet/fastener patterns are design assumptions.",
    ) | {
        "review": review(
            "The rotating bridge and the static pit are deliberately separate assemblies; at oblique "
            "angles the end wheels are not exactly on the ring rail because the wheel centres are set "
            "from the nominal pit radius, and the turntable has no cinder floor texture or approach "
            "signalling.",
            [
                "Check ring-rail eccentricity against the wheel centres across the whole angle sweep",
                "Add tied approach-track ends and a ground-level walkway around the pit",
            ],
            by="KIT-FORGE builder",
            seen="iso beauty, opposite beauty and clay studies of the first pass",
        )
    },
}

DATUMS = {
    "ground_z": 0.0,
    "rail_top_z": 0.0,
    "gauge": GAUGE,
    "wall_t_frac": 0.028,
    "pit_d_min": 1.35,
    "pit_d_frac": 0.115,
    "floor_thick": 0.14,
    "coping_thick": 0.14,
    "girder_top_z": -0.29,
    "girder_depth_frac": 0.060,
    "girder_end_depth_frac": 0.030,
    "wheel_r": 0.30,
    "wheel_bore_r": 0.052,
    "butt_gap_m": 0.0005,
    "bore_clear_m": 0.0018,
    "min_materials": 3,
    "static": [],
    "bridge": [],
}


def _derived(C):
    """Anatomy and clearance datum table; all mating coordinates are computed here."""
    d = C("diameter")
    theta = C("angle") * PI / 180.0
    r = d * 0.5
    wall_t = d * DATUMS["wall_t_frac"]
    inner_r = r - wall_t
    pit_d = mx(DATUMS["pit_d_min"], d * DATUMS["pit_d_frac"])
    floor_top = -pit_d + DATUMS["floor_thick"]
    ring_r = inner_r - 0.42
    ring_top = floor_top + 0.22
    half = inner_r - 0.10                       # bridge end over the wheel centre line
    girder_y = 1.34                             # girder centre lines, constant standard-gauge deck
    web_t = 0.075
    h_mid = d * DATUMS["girder_depth_frac"]
    h_end = d * DATUMS["girder_end_depth_frac"]
    top = DATUMS["girder_top_z"]
    bot_mid = top - h_mid
    bot_end = top - h_end
    pier_r = mn(1.05, d * 0.072)
    pier_top = bot_mid - 0.13
    wheel_r = DATUMS["wheel_r"]
    wheel_z = ring_top + wheel_r + 0.012   # steel tyre outer radius 0.312
    return dict(
        D=d, T=theta, R=r, wall_t=wall_t, inner_r=inner_r, pit_d=pit_d,
        floor_top=floor_top, ring_r=ring_r, ring_top=ring_top, half=half,
        girder_y=girder_y, web_t=web_t, h_mid=h_mid, h_end=h_end,
        top=top, bot_mid=bot_mid, bot_end=bot_end, pier_r=pier_r,
        pier_top=pier_top, wheel_r=wheel_r, wheel_z=wheel_z,
    )


def _near_compass(i, n, tolerance):
    step = 360.0 / n
    a = (i * step) % 90.0
    return a < tolerance or a > 90.0 - tolerance


def build_base(g: Graph, DATUMS: dict, C) -> list[str]:
    """Circular pit: paved floor, coursed/buttressed retaining wall, coping and approaches."""
    v = _derived(C)
    static = DATUMS["static"]
    wall_t, r, inner_r, pit_d = v["wall_t"], v["R"], v["inner_r"], v["pit_d"]
    ft = v["floor_top"]
    blocks = []   # pieces of Hy4's block-built pit (wall = 0)

    # Concrete floor and its cinder/ballast wearing layer.
    g.n("pit_floor_raw", "cylinder", radius=inner_r, depth=DATUMS["floor_thick"],
        sides=80, cap="quad")
    g.xf("pit_floor", "pit_floor_raw", t=[0, 0, -pit_d + DATUMS["floor_thick"] * 0.5])
    g.mat("pit_floor", "pit_floor", "concrete")
    static.append("pit_floor")

    g.n("pit_cinder_raw", "cylinder", radius=inner_r - 0.04, depth=0.045,
        sides=80, cap="quad")
    g.xf("pit_cinder", "pit_cinder_raw", t=[0, 0, ft + 0.0225])
    g.mat("pit_cinder", "pit_cinder", "ballast")
    static.append("pit_cinder")

    # Concentric courses of radial stone flags: three bonded rings of individual
    # small flags around the central pier, stopping short of the ring-rail bed.
    pav_lo, pav_hi = v["pier_r"] + 0.34, v["ring_r"] - 0.22
    band = (pav_hi - pav_lo) / 3.0
    for k, count in enumerate((28, 44, 60)):
        rm = pav_lo + band * (k + 0.5)
        raw = f"floor_flag_{k}_raw"
        filleted_box(g, raw,
                     [band - DATUMS["butt_gap_m"], 2 * PI * rm / count * 0.94, 0.052],
                     at=[rm, 0, ft + 0.024], width=0.004, segments=2, mode="fwn")
        g.mat(raw, raw, "stone" if k % 2 else "chipped-limestone")
        nm = f"floor_flags_{k}"
        g.ring(nm, _material_leaf(raw), count, 2 * PI / count,
               start_angle=(0.5 if k % 2 else 0.0) * PI / count)
        blocks.append(nm)

    # Solid structural backing ring: the ashlar blocks below are the facing veneer,
    # so the bonded joints read as mortar rather than as holes through the wall.
    core = [
        [inner_r + 0.03, -pit_d],
        [r - 0.03, -pit_d],
        [r - 0.03, -DATUMS["coping_thick"]],
        [inner_r + 0.03, -DATUMS["coping_thick"]],
    ]
    g.lathe("wall_core", core + [core[0]], sides=96, cap="quad")
    g.mat("wall_core", "wall_core", "chipped-limestone")
    blocks.append("wall_core")

    # Three bonded courses of individual ashlar blocks, alternately half-offset.
    seg = 2 * PI * r / WALL_SEGMENTS
    r_mid = r - wall_t * 0.5
    course_h = pit_d / 3.0
    for course in range(3):
        offset = 0.5 if course % 2 else 0.0
        for i in range(WALL_SEGMENTS):
            a = 2 * PI * (i + offset) / WALL_SEGMENTS
            name = f"wall_c{course}_{i:02d}"
            filleted_box(g, name + "_raw",
                         [seg * 1.055, wall_t, course_h - DATUMS["butt_gap_m"]],
                         at=[r_mid, 0, -pit_d + course_h * (course + 0.5)],
                         width=0.004, segments=2, mode="fwn")
            g.xf(name, name + "_raw", r=[0, 0, a])
            g.mat(name, name,
                  "brick" if (course == 0 and i % 7 == 0)
                  else ("stone" if (i + 2 * course) % 9 < 2
                        else "chipped-limestone"))
            blocks.append(name)

    # Projecting string bands articulate the deep wall into rusticated courses.
    for b, frac in enumerate((0.34, 0.67)):
        zb = -pit_d * frac
        prof = [
            [r - 0.02, zb - 0.045], [r + 0.055, zb - 0.045],
            [r + 0.055, zb + 0.045], [r - 0.02, zb + 0.045],
        ]
        nm = f"wall_string_band_{b}"
        g.lathe(nm, prof + [prof[0]], sides=96, cap="quad")
        g.mat(nm, nm, "stone")
        static.append(nm)

    # Eight shallow rusticated buttresses on the outer face, between the approach
    # gaps, give the rear elevation the same articulation as the front.
    for i in range(8):
        a = 2 * PI * (i + 0.5) / 8.0
        nm = f"wall_buttress_{i:02d}_raw"
        filleted_box(g, nm, [0.22, 0.54, pit_d - 0.24],
                     at=[r + 0.045, 0, -pit_d * 0.5 - 0.02],
                     width=0.010, segments=2, mode="fwn")
        g.xf(f"wall_buttress_{i:02d}", nm, r=[0, 0, a])
        g.mat(f"wall_buttress_{i:02d}", f"wall_buttress_{i:02d}", "stone")
        static.append(f"wall_buttress_{i:02d}")

    # Coping: larger stones, interrupted at the four compass points by approach tracks.
    cop_seg = 2 * PI * r / COPING_SEGMENTS
    for i in range(COPING_SEGMENTS):
        if _near_compass(i, COPING_SEGMENTS, 17.0):
            continue
        a = 2 * PI * i / COPING_SEGMENTS
        name = f"coping_{i:02d}"
        filleted_box(g, name + "_raw",
                     [cop_seg * 1.006, wall_t + 0.26, DATUMS["coping_thick"]],
                     at=[r - wall_t * 0.42, 0, -DATUMS["coping_thick"] * 0.5],
                     width=0.011, segments=3, mode="fwn")
        g.xf(name, name + "_raw", r=[0, 0, a])
        g.mat(name, name, "chipped-limestone")
        blocks.append(name)

    # Solid pit (wall = 1): one turned wall ring, one coping ring, plain cinder floor.
    # Added after the Hy4 run; wall = 0 keeps Hy4's block-built pit exactly.
    wall = [
        [inner_r, -pit_d],
        [r, -pit_d],
        [r, -DATUMS["coping_thick"]],
        [inner_r, -DATUMS["coping_thick"]],
    ]
    g.lathe("pit_wall", wall + [wall[0]], sides=128, cap="quad")
    g.mat("pit_wall", "pit_wall", "chipped-limestone")
    cop_w = wall_t + 0.26
    cop_c = r - wall_t * 0.42
    coping = [
        [cop_c - cop_w * 0.5, -DATUMS["coping_thick"]],
        [cop_c + cop_w * 0.5, -DATUMS["coping_thick"]],
        [cop_c + cop_w * 0.5, 0.0],
        [cop_c - cop_w * 0.5, 0.0],
    ]
    g.lathe("pit_coping", coping + [coping[0]], sides=128, cap="quad")
    g.mat("pit_coping", "pit_coping", "stone")
    g.join("pit_solid", _material_leaf("pit_wall"), _material_leaf("pit_coping"))
    g.join("pit_blocks", *[_material_leaf(x) for x in blocks])
    g.n("pit_masonry", "select", ["pit_solid", "pit_blocks"], condition=C("wall"))
    static.append("pit_masonry")

    # Foundation ring under the running rail and the rail itself (an iron annulus).
    bed = [
        [v["ring_r"] - 0.14, ft],
        [v["ring_r"] + 0.14, ft],
        [v["ring_r"] + 0.14, v["ring_top"] - 0.085],
        [v["ring_r"] - 0.14, v["ring_top"] - 0.085],
    ]
    g.lathe("ring_rail_bed", bed + [bed[0]], sides=96, cap="quad")
    g.mat("ring_rail_bed", "ring_rail_bed", "stone")
    static.append("ring_rail_bed")

    rt, rr = v["ring_top"], v["ring_r"]
    profile = [
        [rr - 0.070, rt - 0.085],
        [rr + 0.070, rt - 0.085],
        [rr + 0.070, rt - 0.045],
        [rr + 0.022, rt],
        [rr - 0.022, rt],
        [rr - 0.070, rt - 0.045],
    ]
    g.lathe("ring_rail", profile + [profile[0]], sides=128, cap="quad")
    g.mat("ring_rail", "ring_rail", "steel")
    static.append("ring_rail")

    # Four approach tracks, built pointing +X then rotated into the compass directions.
    for label, deg in (("e", 0.0), ("n", 90.0), ("w", 180.0), ("s", 270.0)):
        add_approach(g, label, deg * PI / 180.0, v, static)
        add_abutment(g, label, deg * PI / 180.0, v, static)
    return static


def add_abutment(g, label, ang, v, static):
    """Masonry ramp-seat filling the coping gap under each approach track."""
    r, pit_d, ft = v["R"], v["pit_d"], v["floor_top"]
    p = "abut_" + label
    h = -0.145 - ft
    # Start outside the swept bridge circle at all diameters; finish past the wall.
    x0 = mx(v["half"] + 0.015, r - 0.37)
    x1 = r + 0.57
    cx = (x0 + x1) * 0.5
    raw = p + "_raw"
    filleted_box(g, raw, [x1 - x0, 2.72, h], at=[cx, 0, ft + h * 0.5],
                 width=0.014, segments=3, mode="fwn")
    g.xf(p, raw, r=[0, 0, ang])
    g.mat(p, p, "chipped-limestone")
    static.append(p)
    # Two raised stone kerbs either side of the track give the seat its parapet.
    for side, sy in (("l", -1.38), ("r", 1.38)):
        kr = f"{p}_kerb_{side}_raw"
        filleted_box(g, kr, [x1 - x0, 0.20, 0.15], at=[cx, sy, -0.075],
                     width=0.008, segments=2, mode="fwn")
        g.xf(f"{p}_kerb_{side}", kr, r=[0, 0, ang])
        g.mat(f"{p}_kerb_{side}", f"{p}_kerb_{side}", "stone")
        static.append(f"{p}_kerb_{side}")


def add_approach(g, label, ang, v, static):
    """One ballasted standard-gauge approach stopping just clear of the rotating deck."""
    r = v["R"]
    p = f"ap_{label}"
    # The inner end is tied to the rotating bridge's swept radius, so it never
    # clips the deck at any heading, while at angle 0 the rail gap is only 40 mm.
    x0 = v["half"] + DATUMS["butt_gap_m"] * 80.0
    filleted_box(g, p + "_ballast_raw", [3.90, 2.80, 0.20],
                 at=[x0 + 1.95, 0, -0.235], width=0.012, segments=2, mode="fwn")
    g.xf(p + "_ballast", p + "_ballast_raw", r=[0, 0, ang])
    g.mat(p + "_ballast", p + "_ballast", "ballast")
    static.append(p + "_ballast")

    filleted_box(g, p + "_sleeper", [0.25, 2.18, 0.12], at=[0, 0, -0.17],
                 width=0.008, segments=2, mode="fwn")
    g.mat(p + "_sleeper", p + "_sleeper", "wood")
    g.array(p + "_sleepers", _material_leaf(p + "_sleeper"),
            [x0 + 0.10, 0, 0], [0.52, 0, 0], 8)
    g.xf(p + "_sleepers_rot", p + "_sleepers", r=[0, 0, ang])
    g.mat(p + "_sleepers_rot", p + "_sleepers_rot", "wood")
    static.append(p + "_sleepers_rot")

    for side, sy in (("l", -GAUGE * 0.5), ("r", GAUGE * 0.5)):
        filleted_box(g, p + "_rail_" + side, [4.00, 0.075, 0.11],
                     at=[x0 + 2.00, sy, -0.055], width=0.009, segments=2, mode="fwn")
        filleted_box(g, p + "_head_" + side, [4.00, 0.105, 0.024],
                     at=[x0 + 2.00, sy, -0.012], width=0.006, segments=2, mode="fwn")
        g.xf(p + "_rail_r_" + side, p + "_rail_" + side, r=[0, 0, ang])
        g.xf(p + "_head_r_" + side, p + "_head_" + side, r=[0, 0, ang])
        g.mat(p + "_rail_r_" + side, p + "_rail_r_" + side, "steel")
        g.mat(p + "_head_r_" + side, p + "_head_r_" + side, "steel")
        static.append(p + "_rail_r_" + side)
        static.append(p + "_head_r_" + side)


def build_primary_mass(g: Graph, DATUMS: dict, C) -> list[str]:
    """Static centre pier and the two deep riveted plate girders of the rotating bridge."""
    v = _derived(C)
    static, bridge = DATUMS["static"], DATUMS["bridge"]
    L, gy, wt = v["half"], v["girder_y"], v["web_t"]

    # Centre pier on the pit floor, with a cast thrust housing reaching the bridge.
    pier_h = v["pier_top"] - v["floor_top"]
    g.n("pier_raw", "cylinder", radius=v["pier_r"], depth=pier_h, sides=32, cap="quad")
    g.xf("pier", "pier_raw", t=[0, 0, v["floor_top"] + pier_h * 0.5])
    g.mat("pier", "pier", "chipped-limestone")
    static.append("pier")
    g.n("pier_cap_raw", "cylinder", radius=v["pier_r"] + 0.10, depth=0.09,
        sides=32, cap="quad")
    g.xf("pier_cap", "pier_cap_raw", t=[0, 0, v["pier_top"] - 0.045])
    g.mat("pier_cap", "pier_cap", "stone")
    static.append("pier_cap")

    # Tapered webs: profile is drawn in X/Z then laid upright about the X axis.
    for side, sy in (("l", -gy), ("r", gy)):
        name = "girder_web_" + side
        g.n(name + "_raw", "extrude", profile=[
            [-L, v["bot_end"]], [0, v["bot_mid"]], [L, v["bot_end"]],
            [L, v["top"]], [-L, v["top"]],
        ], depth=wt, cap="inset")
        g.xf(name, name + "_raw", t=[0, sy + wt * 0.5, 0], r=[PI * 0.5, 0, 0])
        g.mat(name, name, "iron")
        bridge.append(name)

        # Flat flange plates: straight top chord and two sloping bottom chords.
        filleted_box(g, name + "_top_flange", [2 * L, 0.30, 0.065],
                     at=[0, sy, v["top"] + 0.0325], width=0.009, segments=3, mode="fwn")
        g.mat(name + "_top_flange", name + "_top_flange", "iron")
        bridge.append(name + "_top_flange")

        dh = v["h_mid"] - v["h_end"]
        slope = atan2(dh, L)
        seg_len = sqrt(L * L + dh * dh)
        for half_name, sgn in (("a", 1.0), ("b", -1.0)):
            nm = f"{name}_bottom_{half_name}"
            filleted_box(g, nm + "_raw", [seg_len, 0.24, 0.070],
                         at=[sgn * L * 0.5, sy, (v["bot_mid"] + v["bot_end"]) * 0.5],
                         width=0.008, segments=2, mode="fwn")
            g.xf(nm, nm + "_raw", r=[0, -sgn * slope, 0])
            g.mat(nm, nm, "iron")
            bridge.append(nm)

        # Vertical stiffener flats at each cross girder station.
        for k in range(1, 13):
            x = -L + 2 * L * k / 13.0
            depth = (v["h_mid"] - (v["h_mid"] - v["h_end"]) * abs(x) / L) * 0.88
            nm = f"{name}_stiff_{k:02d}"
            filleted_box(g, nm, [0.055, 0.20, depth],
                         at=[x, sy, v["top"] - depth * 0.5 - 0.05],
                         width=0.005, segments=2, mode="fwn")
            g.mat(nm, nm, "iron")
            bridge.append(nm)

    # Cross girders tying the two webs, at the same stations as the stiffeners.
    for k in range(13):
        x = -L + 2 * L * k / 12.0
        nm = f"cross_girder_{k:02d}"
        filleted_box(g, nm, [0.15, 2 * gy - 0.10, 0.28],
                     at=[x, 0, v["top"] - 0.14], width=0.010, segments=3, mode="fwn")
        g.mat(nm, nm, "iron")
        bridge.append(nm)

    # Diagonal wind bracing under the deck, alternating across the bays.
    for k in range(12):
        x0 = -L + 2 * L * k / 12.0
        x1 = -L + 2 * L * (k + 1) / 12.0
        z = v["top"] - 0.34
        a = [x0, -gy + 0.12, z] if k % 2 else [x0, gy - 0.12, z]
        b = [x1, gy - 0.12, z] if k % 2 else [x1, -gy + 0.12, z]
        nm = f"wind_brace_{k:02d}"
        g.rod(nm, a, b, 0.032, cap="quad")
        g.mat(nm, nm, "weathered-iron")
        bridge.append(nm)
    return static + bridge


def build_openings_or_mechanism(g: Graph, DATUMS: dict, C) -> list[str]:
    """King-pin/bearing, ring-rail carriage gear, catch sockets and centre trunnions."""
    v = _derived(C)
    static, bridge = DATUMS["static"], DATUMS["bridge"]

    # Cast-iron bearing housing on the pier: restrained SDS drum with a machined cap.
    sds_cylinder(g, "kingpin_housing", radius=0.48, depth=0.34,
                 at=[0, 0, v["pier_top"] + 0.17], sides=12,
                 lod_levels=[2, 1, 0])
    g.mat("kingpin_housing", "kingpin_housing", "weathered-iron")
    static.append("kingpin_housing")
    filleted_box(g, "kingpin_shoe", [0.90, 0.90, 0.10],
                 at=[0, 0, v["bot_mid"] - 0.05], width=0.012, segments=3, mode="fwn")
    g.mat("kingpin_shoe", "kingpin_shoe", "steel")
    bridge.append("kingpin_shoe")
    g.rod("kingpin", [0, 0, v["pier_top"]], [0, 0, v["top"] + 0.05],
          0.105, cap="quad")
    g.mat("kingpin", "kingpin", "steel")
    bridge.append("kingpin")
    bored_nut(g, "kingpin_nut", 0.19, 0.107, 0.075,
              at=[0, 0, v["top"] + 0.105])
    g.mat("kingpin_nut", "kingpin_nut", "bronze")
    bridge.append("kingpin_nut")

    # Bronze thrust races on the exposed centre spindle, clear of the deck track.
    for k, z in enumerate((v["bot_mid"] + 0.20, v["top"] - 0.16)):
        nm = f"thrust_race_{k}"
        g.n(nm + "_raw", "cylinder", radius=0.29, depth=0.075, sides=24, cap="quad")
        g.xf(nm, nm + "_raw", t=[0, 0, z])
        g.mat(nm, nm, "bronze")
        bridge.append(nm)
    # Trunnion stubs and outboard bearing bosses make the centre pivot readable
    # from both long elevations, not only from directly above.
    for side, sy in (("l", -1.0), ("r", 1.0)):
        y0 = sy * (v["girder_y"] - 0.08)
        y1 = sy * (v["girder_y"] + 0.43)
        nm = f"trunnion_{side}"
        g.rod(nm, [0, y0, v["bot_mid"] + 0.44],
              [0, y1, v["bot_mid"] + 0.44], 0.085, cap="quad")
        g.mat(nm, nm, "steel")
        bridge.append(nm)
        bored_collar(g, f"trunnion_boss_{side}", 0.23, 0.087, 0.16,
                     at=[0, y1, v["bot_mid"] + 0.44], axis="y", sides=24, bevel=0.005)
        g.mat(f"trunnion_boss_{side}", f"trunnion_boss_{side}", "weathered-iron")
        bridge.append(f"trunnion_boss_{side}")
        # Short-webbed gusset tying the boss back into the plate girder.
        filleted_box(g, f"trunnion_gusset_{side}", [0.36, 0.34, 0.34],
                     at=[0, sy * (v["girder_y"] + 0.20), v["bot_mid"] + 0.20],
                     width=0.008, segments=2, mode="fwn")
        g.mat(f"trunnion_gusset_{side}", f"trunnion_gusset_{side}", "iron")
        bridge.append(f"trunnion_gusset_{side}")

    # Socket plates at the four alignment positions; the bridge catch drops into one.
    for label, deg in (("e", 0.0), ("n", 90.0), ("w", 180.0), ("s", 270.0)):
        a = deg * PI / 180.0
        nm = f"catch_socket_{label}_raw"
        filleted_box(g, nm, [0.36, 0.34, 0.10],
                     at=[v["inner_r"] - 0.10, 0, -0.40],
                     width=0.008, segments=2, mode="fwn")
        g.xf(f"catch_socket_{label}", nm, r=[0, 0, a])
        g.mat(f"catch_socket_{label}", f"catch_socket_{label}", "steel")
        static.append(f"catch_socket_{label}")

    # Four carriage wheels: wheel centres are placed exactly on the ring-rail
    # circle (radius from the pivot), so they track the rail at every angle.
    for end, sx in (("a", 1.0), ("b", -1.0)):
        wy = v["girder_y"] + 0.25
        x = sx * sqrt(v["ring_r"] * v["ring_r"] - wy * wy)
        g.rod(f"axle_{end}", [x, -wy, v["wheel_z"]],
              [x, wy, v["wheel_z"]], 0.05, cap="quad")
        g.mat(f"axle_{end}", f"axle_{end}", "steel")
        bridge.append(f"axle_{end}")
        hanger_len = absx(v["wheel_z"] - v["bot_end"]) + 0.22
        for side, sy in (("l", -1.0), ("r", 1.0)):
            y = sy * wy
            bored_collar(g, f"wheel_{end}_{side}",
                         v["wheel_r"], DATUMS["wheel_bore_r"], 0.13,
                         at=[x, y, v["wheel_z"]], axis="y", sides=24, bevel=0.004)
            g.mat(f"wheel_{end}_{side}", f"wheel_{end}_{side}", "weathered-iron")
            bridge.append(f"wheel_{end}_{side}")
            # Steel tyre and inner guide flange running on the circular rail head.
            bored_collar(g, f"wheel_tyre_{end}_{side}", 0.312, 0.287, 0.142,
                         at=[x, y, v["wheel_z"]], axis="y", sides=24, bevel=0.003)
            g.mat(f"wheel_tyre_{end}_{side}", f"wheel_tyre_{end}_{side}", "steel")
            bridge.append(f"wheel_tyre_{end}_{side}")
            bored_collar(g, f"wheel_flange_{end}_{side}", 0.345, 0.292, 0.032,
                         at=[x, y - sy * 0.084, v["wheel_z"]], axis="y",
                         sides=24, bevel=0.003)
            g.mat(f"wheel_flange_{end}_{side}", f"wheel_flange_{end}_{side}",
                  "weathered-iron")
            bridge.append(f"wheel_flange_{end}_{side}")
            bored_collar(g, f"wheel_hub_{end}_{side}", 0.115,
                         DATUMS["wheel_bore_r"], 0.20,
                         at=[x, y, v["wheel_z"]], axis="y", sides=24, bevel=0.003)
            g.mat(f"wheel_hub_{end}_{side}", f"wheel_hub_{end}_{side}", "bronze")
            bridge.append(f"wheel_hub_{end}_{side}")
            # Hanger plate from the girder end down to the axle bearing.
            filleted_box(g, f"hanger_{end}_{side}",
                         [0.22, 0.10, hanger_len],
                         at=[x, sy * v["girder_y"] + sy * 0.10,
                             (v["wheel_z"] + v["bot_end"]) * 0.5],
                         width=0.007, segments=2, mode="fwn")
            g.mat(f"hanger_{end}_{side}", f"hanger_{end}_{side}", "iron")
            bridge.append(f"hanger_{end}_{side}")
            filleted_box(g, f"axlebox_{end}_{side}", [0.30, 0.22, 0.22],
                         at=[x, sy * (v["girder_y"] + 0.10), v["wheel_z"]],
                         width=0.008, segments=2, mode="fwn")
            g.mat(f"axlebox_{end}_{side}", f"axlebox_{end}_{side}", "weathered-iron")
            bridge.append(f"axlebox_{end}_{side}")
    return static + bridge


def build_secondary_craft(g: Graph, DATUMS: dict, C) -> list[str]:
    """Planked deck, running track, hand capstan outrigger and the alignment catch."""
    v = _derived(C)
    bridge = DATUMS["bridge"]
    L, gy = v["half"], v["girder_y"]

    # Restrained Chaikin cast boss under the centre of the bridge: second SDS cage.
    sds_cylinder(g, "pivot_boss", radius=0.34, depth=0.18,
                 at=[0, 0, v["bot_mid"] + 0.02], sides=12,
                 lod_levels=[2, 1, 0])
    g.mat("pivot_boss", "pivot_boss", "weathered-iron")
    bridge.append("pivot_boss")

    # Deck planks across the full bridge width, instanced at a derived pitch.
    pitch = 2 * L / DECK_PLANKS
    filleted_box(g, "deck_plank", [pitch * 0.90, 2 * gy - 0.08, 0.075],
                 at=[0, 0, -0.2475], width=0.004, segments=2, mode="fwn")
    g.mat("deck_plank", "deck_plank", "wood")
    g.array("deck_planks", _material_leaf("deck_plank"), [-L + pitch * 0.5, 0, 0],
            [pitch, 0, 0], DECK_PLANKS)
    bridge.append("deck_planks")

    # Transverse sleepers and the single standard-gauge running track.
    sp = 2 * L / SLEEPERS
    filleted_box(g, "bridge_sleeper", [0.25, 2.12, 0.10],
                 at=[0, 0, -0.16], width=0.006, segments=2, mode="fwn")
    g.mat("bridge_sleeper", "bridge_sleeper", "wood")
    g.array("bridge_sleepers", _material_leaf("bridge_sleeper"),
            [-L + sp * 0.5, 0, 0], [sp, 0, 0], SLEEPERS)
    bridge.append("bridge_sleepers")

    for side, sy in (("l", -GAUGE * 0.5), ("r", GAUGE * 0.5)):
        filleted_box(g, f"bridge_rail_{side}", [2 * L, 0.075, 0.11],
                     at=[0, sy, -0.055], width=0.008, segments=2, mode="fwn")
        filleted_box(g, f"bridge_head_{side}", [2 * L, 0.105, 0.024],
                     at=[0, sy, -0.012], width=0.005, segments=2, mode="fwn")
        g.mat(f"bridge_rail_{side}", f"bridge_rail_{side}", "steel")
        g.mat(f"bridge_head_{side}", f"bridge_head_{side}", "steel")
        bridge.append(f"bridge_rail_{side}")
        bridge.append(f"bridge_head_{side}")

    # Hand capstan on a broad outboard platform on the visible (-Y) side.
    # It stands well clear of the 1.435 m running gauge and above coping level.
    cx, cy = L - 1.35, -(gy + 1.12)
    filleted_box(g, "capstan_platform", [1.55, 1.30, 0.13],
                 at=[cx, cy, -0.265], width=0.012, segments=3, mode="fwn")
    g.mat("capstan_platform", "capstan_platform", "iron")
    bridge.append("capstan_platform")
    for k, dx in enumerate((-0.55, 0.55)):
        g.rod(f"capstan_arm_{k}", [cx + dx, -gy - 0.02, -0.48],
              [cx + dx, cy + 0.46, -0.33], 0.055, cap="quad")
        g.mat(f"capstan_arm_{k}", f"capstan_arm_{k}", "weathered-iron")
        bridge.append(f"capstan_arm_{k}")
        filleted_box(g, f"capstan_web_{k}", [0.13, 0.62, 0.30],
                     at=[cx + dx, cy + 0.53, -0.40], width=0.008,
                     segments=2, mode="fwn")
        g.mat(f"capstan_web_{k}", f"capstan_web_{k}", "iron")
        bridge.append(f"capstan_web_{k}")

    sds_cylinder(g, "capstan_drum", radius=0.225, depth=0.78,
                 at=[cx, cy, 0.20], sides=12, lod_levels=[2, 1, 0])
    g.mat("capstan_drum", "capstan_drum", "weathered-iron")
    bridge.append("capstan_drum")
    g.n("capstan_spindle_raw", "cylinder", radius=0.068, depth=1.62,
        sides=20, cap="quad")
    g.xf("capstan_spindle", "capstan_spindle_raw", t=[cx, cy, 0.05])
    g.mat("capstan_spindle", "capstan_spindle", "steel")
    bridge.append("capstan_spindle")
    g.n("capstan_crown_raw", "cylinder", radius=0.30, depth=0.085,
        sides=20, cap="quad")
    g.xf("capstan_crown", "capstan_crown_raw", t=[cx, cy, 0.70])
    g.mat("capstan_crown", "capstan_crown", "bronze")
    bridge.append("capstan_crown")
    for i in range(6):
        a = 2 * PI * i / 6
        ca, sa = cos(a), sin(a)
        g.rod(f"capstan_bar_{i}",
              [cx + 0.17 * ca, cy + 0.17 * sa, 0.65],
              [cx + 0.62 * ca, cy + 0.62 * sa, 0.65],
              0.033, cap="quad")
        g.mat(f"capstan_bar_{i}", f"capstan_bar_{i}", "steel")
        bridge.append(f"capstan_bar_{i}")
        # Wooden hand grips on the outer ends of the pushing bars.
        g.rod(f"capstan_grip_{i}",
              [cx + 0.50 * ca, cy + 0.50 * sa, 0.65],
              [cx + 0.66 * ca, cy + 0.66 * sa, 0.65],
              0.052, cap="quad")
        g.mat(f"capstan_grip_{i}", f"capstan_grip_{i}", "wood")
        bridge.append(f"capstan_grip_{i}")
    # Two long wooden handspikes project along the platform, clear of the rails.
    for side, sx in (("a", -1.0), ("b", 1.0)):
        g.rod(f"capstan_handspike_{side}",
              [cx + sx * 0.20, cy, 0.65], [cx + sx * 1.00, cy, 0.65],
              0.045, cap="quad")
        g.mat(f"capstan_handspike_{side}", f"capstan_handspike_{side}", "wood")
        bridge.append(f"capstan_handspike_{side}")

    # Locking bolt under the deck centre, sliding into the coping socket. Two
    # external bell-crank handles (front and rear) move it without fouling track.
    filleted_box(g, "catch_guide", [0.85, 0.20, 0.20],
                 at=[L - 0.78, 0, -0.40], width=0.008, segments=2, mode="fwn")
    g.mat("catch_guide", "catch_guide", "iron")
    bridge.append("catch_guide")
    g.rod("catch_bolt", [L - 1.42, 0, -0.40], [L + 0.01, 0, -0.40],
          0.055, cap="quad")
    g.mat("catch_bolt", "catch_bolt", "steel")
    bridge.append("catch_bolt")
    for side, sy in (("f", -1.0), ("b", 1.0)):
        hy = sy * (gy + 0.34)
        g.rod(f"catch_link_{side}", [L - 1.36, 0, -0.40],
              [L - 1.36, hy, -0.40], 0.035, cap="quad")
        g.mat(f"catch_link_{side}", f"catch_link_{side}", "iron")
        bridge.append(f"catch_link_{side}")
        filleted_box(g, f"catch_bracket_{side}", [0.36, 0.26, 0.22],
                     at=[L - 1.36, hy, -0.40], width=0.007, segments=2, mode="fwn")
        g.mat(f"catch_bracket_{side}", f"catch_bracket_{side}", "weathered-iron")
        bridge.append(f"catch_bracket_{side}")
        g.rod(f"catch_handle_{side}", [L - 1.36, hy, -0.40],
              [L - 1.36, hy, 0.21], 0.045, cap="quad")
        g.mat(f"catch_handle_{side}", f"catch_handle_{side}", "steel")
        bridge.append(f"catch_handle_{side}")
        g.n(f"catch_knob_{side}_raw", "cylinder", radius=0.085, depth=0.07,
            sides=16, cap="quad")
        g.xf(f"catch_knob_{side}", f"catch_knob_{side}_raw",
             t=[L - 1.36, hy, 0.235])
        g.mat(f"catch_knob_{side}", f"catch_knob_{side}", "bronze")
        bridge.append(f"catch_knob_{side}")
    return bridge


def build_details_and_signage(g: Graph, DATUMS: dict, C) -> list[str]:
    """Post-SDS zero-triangle rivets, panel seams, bolt heads and plank joints."""
    v = _derived(C)
    bridge = DATUMS["bridge"]
    L, gy, top = v["half"], v["girder_y"], v["top"]
    out = []

    # Two rivet rows along each web: post-chamfer, carried with the rotating group.
    for side, sy in (("l", -gy), ("r", gy)):
        web = "girder_web_" + side
        for row, z in (("upper", top - 0.13), ("lower", v["bot_end"] + 0.16)):
            guide = f"rguide_{side}_{row}"
            g.guide(guide,
                    [-L + 0.55, sy + (0.075 if side == "l" else -0.075), z],
                    [L - 0.55, sy + (0.075 if side == "l" else -0.075), z])
            nm = f"rivets_{side}_{row}"
            g.rivets(nm, web, guide, 0.34, radius=0.014, height=0.006, reach=0.5)
            out.append(nm)
        seam_guide = f"sguide_{side}"
        g.guide(seam_guide, [-L + 0.35, sy, top - 0.31], [L - 0.35, sy, top - 0.31])
        g.seam(f"web_seam_{side}", web, seam_guide, width=0.007, depth=0.003,
               reach=0.5)
        out.append(f"web_seam_{side}")

    # Bolt heads on the top flanges and on the capstan base plate.
    for side, sy in (("l", -gy), ("r", gy)):
        guide = f"bguide_{side}"
        g.guide(guide, [-L + 0.6, sy, top + 0.066], [L - 0.6, sy, top + 0.066])
        g.fasteners(f"flange_bolts_{side}",
                    "girder_web_" + side, guide, head="hex", pitch=0.95,
                    radius=0.022, reach=0.5)
        out.append(f"flange_bolts_{side}")

    g.guide("capstan_bolt_guide",
            [L - 1.58, -(gy + 1.03), -0.225],
            [L - 0.62, -(gy + 1.03), -0.225])
    g.fasteners("capstan_bolts", "capstan_platform", "capstan_bolt_guide",
                head="hex", pitch=0.38, radius=0.020, reach=0.4)
    out.append("capstan_bolts")

    # Plank joint lines on the deck (shading-level, no extra triangles).
    g.guide("deck_joint_guide", [-L + 0.3, 0, -0.207], [L - 0.3, 0, -0.207])
    g.seam("deck_joints", "deck_planks", "deck_joint_guide",
           width=0.005, depth=0.002, reach=0.5)
    out.append("deck_joints")
    # Deep riveted-lattice web articulation: angle chords, zig-zag diagonals and
    # gusset plates on BOTH outer faces, following the tapered web soffit.
    L, gy, wt = v["half"], v["girder_y"], v["web_t"]
    top, bm, be = DATUMS["girder_top_z"], v["bot_mid"], v["bot_end"]
    panels = 6
    for side, sgn in (("l", -1.0), ("r", 1.0)):
        wy = sgn * (gy + wt * 0.5 + 0.022)
        for k in range(panels):
            xa = -L + 2 * L * k / panels
            xb = -L + 2 * L * (k + 1) / panels
            za = bm + (be - bm) * abs(xa) / L + 0.07
            zb = bm + (be - bm) * abs(xb) / L + 0.07
            for diag, (x0, z0, x1, z1) in enumerate(((xa, top - 0.07, xb, zb),
                                                     (xa, za, xb, top - 0.07))):
                nm = f"web_lattice_{side}_{k}_{diag}"
                g.rod(nm, [x0, wy, z0], [x1, wy, z1], 0.026, cap="quad")
                g.mat(nm, nm, "weathered-iron")
                bridge.append(nm)
            nm = f"lower_chord_{side}_{k}"
            g.rod(nm, [xa, wy, za], [xb, wy, zb], 0.030, cap="quad")
            g.mat(nm, nm, "steel")
            bridge.append(nm)
            if k:
                nm = f"web_gusset_{side}_{k}"
                filleted_box(g, nm, [0.15, 0.060, 0.24],
                             at=[xa, wy, (top + za) * 0.5],
                             width=0.005, segments=2, mode="fwn")
                g.mat(nm, nm, "iron")
                bridge.append(nm)
        nm = f"upper_chord_{side}"
        g.rod(nm, [-L, wy, top - 0.065], [L, wy, top - 0.065], 0.032, cap="quad")
        g.mat(nm, nm, "steel")
        bridge.append(nm)
        # Second close rivet row just inside the upper chord on the web face.
        rg = f"web_rivet_guide_{side}"
        g.guide(rg, [-L + 0.32, sgn * (gy + wt * 0.5), top - 0.16],
                [L - 0.32, sgn * (gy + wt * 0.5), top - 0.16])
        g.rivets(f"web_rivets_{side}", "girder_web_" + side, rg, 0.27,
                 radius=0.013, height=0.006, reach=0.5)
        out.append(f"web_rivets_{side}")
    bridge.extend(out)
    return out


def build_module():
    kit = Kit(ROOT, "hy4/out/turntable", "turntable")
    g = Graph()
    controls = {
        "diameter": control(15, 10, 20),
        "angle": control(0, 0, 360),
        "wall": control(0, 0, 1, integer=True),   # 0 = Hy4's block-built pit, 1 = solid turned ring
    }
    v = _derived(C)
    ports = {
        "track-a": port([v["half"] * cos(v["T"]), v["half"] * sin(v["T"]), 0],
                        [cos(v["T"]), sin(v["T"]), 0], 'track', {"gauge": GAUGE}),
        "track-b": port([-v["half"] * cos(v["T"]), -v["half"] * sin(v["T"]), 0],
                        [-cos(v["T"]), -sin(v["T"]), 0], 'track', {"gauge": GAUGE}),
        "approach-e": port([v["R"] + 0.1, 0, 0], [1, 0, 0], 'track', {"gauge": GAUGE}),
        "approach-n": port([0, v["R"] + 0.1, 0], [0, 1, 0], 'track', {"gauge": GAUGE}),
        "approach-w": port([-v["R"] - 0.1, 0, 0], [-1, 0, 0], 'track', {"gauge": GAUGE}),
        "approach-s": port([0, -v["R"] - 0.1, 0], [0, -1, 0], 'track', {"gauge": GAUGE}),
    }
    DATUMS["static"] = []
    DATUMS["bridge"] = []

    build_base(g, DATUMS, C)
    build_primary_mass(g, DATUMS, C)
    build_openings_or_mechanism(g, DATUMS, C)
    build_secondary_craft(g, DATUMS, C)
    build_details_and_signage(g, DATUMS, C)

    static_leaves = [_material_leaf(x) for x in DATUMS["static"]]
    bridge_leaves = [_material_leaf(x) for x in DATUMS["bridge"]]
    g.join("bridge_local", *bridge_leaves)
    g.xf("bridge_rotated", "bridge_local", r=[0, 0, v["T"]])
    g.join("root", *static_leaves, "bridge_rotated")
    kit.module("turntable", controls, g, "root", ports)
    kit.close()


if __name__ == "__main__":
    build_module()
