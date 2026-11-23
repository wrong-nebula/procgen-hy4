#!/usr/bin/env python3
"""KIT-FORGE modular builder: a painted-iron and plate-glass Brussels shopfront.

Z is up, metres.  The shopface plane is y = 0; the street is on its -Y side.
Regenerate with:  python3 recipes/build_bench_kit.py
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "procgen" / "recipes"))


from kitlib import (
    Kit, Graph, C, control, port, ref, review, write_reviews,
    lt, gt, iff, mn, mx, absx, sin, cos, sqrt, atan2, floor, ceil, PI, INDEX,
)
from kittype import inscribe, number_plaque
from kitparts import filleted_box, cast_housing, sds_cylinder

ROOT = Path(__file__).resolve().parent.parent

REFS = {
    "iron-shopfront": ref(
        ["FD01", "FD25", "B05", "BB01", "FD26"], [], [],
        "A late-nineteenth-century Brussels shopfront interpreted from the brief: "
        "painted cast iron pilasters and capitals, large plate-glass display windows, "
        "a leaded transom, a recessed glazed entrance and a lettered fascia. "
        "All dimensions and ornament are design assumptions, not surveyed measurements.",
        other=["Shopfront modeling brief"],
        review=review(
            "The Art Nouveau spandrel stems are faceted tubular abstractions rather than "
            "individually modelled botanical castings; the glass is a clean solid without "
            "an interior or reflection, and the entrance returns are rectangular rather "
            "than splayed.",
            ["Model tapered flat-bar whiplash scrolls and foliate capitals [brief]",
             "Add a dressed shop interior and splayed vestibule returns [review]"],
            by="KIT-FORGE", date="2026-11-19",
            seen="iso beauty and clay, opposite beauty, orthographic front, parts"),
    )
}

# Anatomy and clearance table.  All derived X expressions are staged in
# DATUMS['D'] by build_module() before any of the five builders run.
DATUMS = {
    "ground_z": 0.0,
    "face_y": 0.0,
    "frame_half_depth": 0.095,      # pilaster shaft is 0.19 m deep
    "column_w": 0.145,
    "column_d": 0.190,
    "riser_h": 0.145,               # of height
    "fascia_h": 0.105,              # of height
    "transom_h": 0.130,             # of height
    "recess_depth": 0.82,           # door is set back 0.82 m
    "shaft_base_z": 0.32,
    "butt_gap": 0.0005,
    "bore_clear": 0.0018,
    "detail_relief": 0.012,
    "materials": ("iron", "black", "glass", "wood", "tile", "bronze", "marking"),
}

SLOTS = DATUMS["materials"]


def fbox(g, groups, name, size, at, slot, bevel=0.010):
    """Machined FWN plate/bar: dead-flat faces with tight bevel catchlights."""
    node = filleted_box(g, name, size, at, width=bevel, segments=3, mode="fwn")
    groups[slot].append(node)
    return node


def plain_box(g, groups, name, size, at, slot):
    node = g.box(name, size, at)
    groups[slot].append(node)
    return node


def rod(g, groups, name, a, b, radius, slot="bronze", sides=8):
    node = g.rod(name, a, b, radius, sides)
    groups[slot].append(node)
    return node


def bent_rod(g, groups, name, points, radius, slot="iron"):
    """A whiplash stem expressed as short connected iron sections."""
    for i in range(len(points) - 1):
        rod(g, groups, "%s-%02d" % (name, i), points[i], points[i + 1], radius, slot, 8)


def build_base(g: Graph, DATUMS: dict, C) -> list:
    """Pavement-level tiling, bronze threshold and the two panelled stall risers."""
    d, groups = DATUMS["D"], DATUMS["groups"]
    riser, op = d["riser"], d["door_half"]
    depth = DATUMS["recess_depth"]

    # Tiled vestibule floor, flush with the pavement datum.
    floor = plain_box(g, groups, "vestibule-floor", [op * 2, depth, 0.06],
                      [0, depth * 0.5, 0.03], "tile")
    DATUMS["floor_id"] = floor
    plain_box(g, groups, "threshold", [op * 2 + 0.08, 0.10, 0.035],
              [0, -0.012, 0.0175], "bronze")
    plain_box(g, groups, "threshold-nosing", [op * 2 + 0.10, 0.025, 0.012],
              [0, -0.068, 0.048], "black")

    for side, s in (("l", -1), ("r", 1)):
        x = s * d["display_c"]
        span = d["display_span"]
        # Stepped stone/cast shoe, timber riser, crisp iron capping.
        fbox(g, groups, "riser-shoe-" + side, [span + 0.09, 0.29, 0.09],
             [x, 0.01, 0.045], "black", 0.012)
        fbox(g, groups, "stall-riser-" + side, [span, 0.21, riser - 0.10],
             [x, 0.01, (riser + 0.10) * 0.5], "wood", 0.012)
        fbox(g, groups, "riser-cap-" + side, [span + 0.07, 0.29, 0.055],
             [x, 0.005, riser - 0.005], "iron", 0.010)
        # Raised framing on both the street face and the sheltered rear face.
        for face, y in (("front", -0.104), ("back", 0.124)):
            for z, tag in ((0.18, "low"), (riser - 0.115, "high")):
                plain_box(g, groups, "riser-%s-%s-%s-rail" % (face, side, tag),
                          [span - 0.12, 0.018, 0.024], [x, y, z], "bronze")
            for e, ex in (("a", -1), ("b", 1)):
                plain_box(g, groups, "riser-%s-%s-stile-%s" % (face, side, e),
                          [0.024, 0.018, riser - 0.30],
                          [x + ex * (span * 0.5 - 0.07), y, riser * 0.5 + 0.015], "bronze")
    return ["vestibule-floor", "threshold", "threshold-nosing"]


def build_primary_mass(g: Graph, DATUMS: dict, C) -> list:
    """The four cast pilasters with capitals, the lintel, fascia board and cornice."""
    d, groups = DATUMS["D"], DATUMS["groups"]
    shaft_top, fb, ft = d["shaft_top"], d["fascia_bottom"], d["fascia_top"]
    fascia, H = d["fascia"], d["H"]
    parts = []

    for tag, x in (("outer-l", -d["outer_x"]), ("inner-l", -d["inner_x"]),
                   ("inner-r", d["inner_x"]), ("outer-r", d["outer_x"])):
        fbox(g, groups, "plinth-foot-" + tag, [0.23, 0.29, 0.12],
             [x, 0.0, 0.06], "black", 0.014)
        fbox(g, groups, "plinth-base-" + tag, [0.19, 0.25, 0.16],
             [x, 0.0, 0.20], "iron", 0.014)
        shaft = fbox(g, groups, "pilaster-" + tag,
                     [DATUMS["column_w"], DATUMS["column_d"],
                      shaft_top - DATUMS["shaft_base_z"]],
                     [x, 0.0, (shaft_top + DATUMS["shaft_base_z"]) * 0.5],
                     "iron", 0.012)
        DATUMS.setdefault("pilasters", {})[tag] = shaft
        fbox(g, groups, "neck-ring-" + tag, [0.185, 0.225, 0.055],
             [x, 0.0, shaft_top - 0.085], "bronze", 0.010)
        # 30% restrained Chaikin SDS: softened cast-iron capital with hard rim.
        cap = cast_housing(g, "capital-" + tag, [0.255, 0.235, 0.14],
                           at=[x, 0.0, shaft_top + 0.005], levels=2,
                           lod_levels=[2, 1, 0], wall_crease=1.8, rim_crease=3.0)
        groups["iron"].append(cap)
        fbox(g, groups, "abacus-" + tag, [0.27, 0.25, 0.065],
             [x, 0.0, shaft_top + 0.105], "iron", 0.010)
        # Flutes are expressed on both elevations, so the rear reads as finished.
        for face, y in (("front", -0.100), ("back", 0.100)):
            for j, dx in enumerate((-0.041, 0.0, 0.041)):
                rod(g, groups, "flute-%s-%s-%d" % (tag, face, j),
                    [x + dx, y, DATUMS["shaft_base_z"] + 0.07],
                    [x + dx, y, shaft_top - 0.17], 0.0045, "bronze", 6)

    # Continuous wrought beam, deep sign panel and three-step cornice.
    lintel = fbox(g, groups, "lintel", [d["W"], 0.30, 0.12],
                  [0, 0.0, fb], "iron", 0.014)
    DATUMS["lintel_id"] = lintel
    board = fbox(g, groups, "fascia-board", [d["W"] - 0.12, 0.22, fascia - 0.09],
                 [0, -0.01, (fb + ft) * 0.5], "black", 0.012)
    DATUMS["fascia_id"] = board
    fbox(g, groups, "fascia-top-rail", [d["W"], 0.27, 0.055],
         [0, -0.015, ft - 0.015], "iron", 0.011)
    fbox(g, groups, "cornice-bed", [d["W"] + 0.04, 0.33, 0.05],
         [0, -0.02, ft + 0.035], "bronze", 0.010)
    fbox(g, groups, "cornice-crown", [d["W"] + 0.12, 0.37, 0.08],
         [0, -0.025, H - 0.045], "iron", 0.013)
    for face, y in (("front", -0.128), ("back", 0.109)):
        rod(g, groups, "fascia-reveal-" + face,
            [-d["W"] * 0.5 + 0.10, y, fb + 0.075],
            [d["W"] * 0.5 - 0.10, y, fb + 0.075], 0.008, "bronze", 8)
    parts.extend(["lintel", "fascia-board", "cornice-crown"])
    return parts


def build_openings_or_mechanism(g: Graph, DATUMS: dict, C) -> list:
    """Plate glass, leaded transoms and the working recessed entrance."""
    d, groups = DATUMS["D"], DATUMS["groups"]
    riser, tb, tr = d["riser"], d["transom_bottom"], d["transom"]
    depth, op = DATUMS["recess_depth"], d["door_half"]

    for side, s in (("l", -1), ("r", 1)):
        x, span = s * d["display_c"], d["display_span"]
        # Two generous plate-glass lights per window, held by a slender mullion.
        pane = span * 0.5 - 0.035
        for half, hs in (("a", -1), ("b", 1)):
            plain_box(g, groups, "display-glass-%s-%s" % (side, half),
                      [pane, 0.022, tb - riser - 0.11],
                      [x + hs * (span * 0.25), 0.0, (riser + 0.06 + tb - 0.05) * 0.5],
                      "glass")
        fbox(g, groups, "window-sill-" + side, [span + 0.09, 0.28, 0.05],
             [x, -0.005, riser + 0.032], "iron", 0.009)
        fbox(g, groups, "transom-rail-" + side, [span + 0.10, 0.27, 0.07],
             [x, 0.0, tb], "iron", 0.010)
        fbox(g, groups, "display-mullion-" + side, [0.038, 0.13, tb - riser - 0.08],
             [x, 0.0, (riser + 0.05 + tb - 0.03) * 0.5], "iron", 0.008)
        for end, ex in (("a", -1), ("b", 1)):
            fbox(g, groups, "window-jamb-%s-%s" % (side, end), [0.035, 0.15, tb - riser - 0.06],
                 [x + ex * (span * 0.5 - 0.01), 0.0, (riser + 0.04 + tb - 0.02) * 0.5],
                 "iron", 0.007)
        tg = plain_box(g, groups, "transom-glass-" + side,
                       [span - 0.02, 0.022, tr - 0.09],
                       [x, 0.0, (tb + 0.045 + d["fb"] - 0.045) * 0.5], "glass")
        DATUMS.setdefault("transom_glass", {})[side] = tg
        zm = (tb + d["fb"]) * 0.5
        rod(g, groups, "transom-mid-came-" + side,
            [x - span * 0.5 + 0.012, -0.019, zm],
            [x + span * 0.5 - 0.012, -0.019, zm], 0.009, "bronze", 6)
        for k in range(1, 6):
            lx = x - span * 0.5 + span * k / 6.0
            rod(g, groups, "transom-came-%s-%d" % (side, k),
                [lx, -0.018, tb + 0.045],
                [lx, -0.018, d["fb"] - 0.045], 0.009, "bronze", 6)
        # Rear glazing bars mirror the street elevation.
        for k in range(1, 4):
            lx = x - span * 0.5 + span * k / 4.0
            rod(g, groups, "transom-rear-came-%s-%d" % (side, k),
                [lx, 0.018, tb + 0.045],
                [lx, 0.018, d["fb"] - 0.045], 0.006, "black", 6)

    # Returns: side glazing runs from the shopfront plane back to the door.
    for side, s in (("l", -1), ("r", 1)):
        x = s * op
        fbox(g, groups, "return-shoe-" + side, [0.10, depth, 0.10],
             [x, depth * 0.5, 0.05], "black", 0.010)
        fbox(g, groups, "return-panel-" + side, [0.075, depth - 0.02, riser - 0.10],
             [x, depth * 0.5, (riser + 0.10) * 0.5], "wood", 0.010)
        fbox(g, groups, "return-sill-" + side, [0.115, depth, 0.055],
             [x, depth * 0.5, riser], "iron", 0.009)
        plain_box(g, groups, "return-glass-" + side,
                  [0.022, depth - 0.09, tb - riser - 0.10],
                  [x, depth * 0.5, (riser + 0.06 + tb - 0.05) * 0.5], "glass")
        fbox(g, groups, "return-head-" + side, [0.11, depth, 0.065],
             [x, depth * 0.5, tb], "iron", 0.009)
        rod(g, groups, "return-mullion-" + side,
            [x, depth * 0.30, riser + 0.08],
            [x, depth * 0.30, tb - 0.05], 0.012, "bronze", 6)
        fbox(g, groups, "door-jamb-" + side, [0.09, 0.15, tb],
             [x, depth, tb * 0.5], "iron", 0.009)

    fbox(g, groups, "recess-header", [op * 2 + 0.14, depth + 0.12, 0.085],
         [0, depth * 0.5, tb + 0.035], "iron", 0.011)
    fan = plain_box(g, groups, "fanlight-glass", [op * 2 - 0.06, 0.022, tr - 0.09],
                    [0, depth, (tb + 0.045 + d["fb"] - 0.045) * 0.5], "glass")
    DATUMS["fanlight_id"] = fan
    fbox(g, groups, "fanlight-head", [op * 2 + 0.14, 0.15, 0.07],
         [0, depth, d["fb"] - 0.035], "iron", 0.009)
    for k in range(1, 5):
        lx = -op + op * 2 * k / 5.0
        rod(g, groups, "fanlight-came-%d" % k,
            [lx, depth - 0.018, tb + 0.045],
            [lx, depth - 0.018, d["fb"] - 0.045], 0.008, "bronze", 6)

    # The door leaf itself: substantial stiles, kick panel and a clear upper light.
    dy = depth - 0.035
    plain_box(g, groups, "door-glass", [op * 2 - 0.19, 0.022, tb - 0.58],
              [0, dy, (tb + 0.52) * 0.5], "glass")
    for side, s in (("l", -1), ("r", 1)):
        fbox(g, groups, "door-stile-" + side, [0.075, 0.10, tb - 0.06],
             [s * (op - 0.055), dy, (tb + 0.06) * 0.5], "iron", 0.008)
    fbox(g, groups, "door-kick", [op * 2 - 0.17, 0.085, 0.40],
         [0, dy, 0.26], "wood", 0.010)
    for z, tag in ((0.065, "bottom"), (0.47, "lock"), (tb - 0.045, "top")):
        fbox(g, groups, "door-rail-" + tag, [op * 2 - 0.16, 0.11, 0.07],
             [0, dy, z], "iron", 0.008)
    # A fine applied glazing bead gives the entrance legible panes at distance.
    for face, y in (("front", dy - 0.017), ("back", dy + 0.017)):
        for edge, ex in (("l", -1), ("r", 1)):
            rod(g, groups, "door-bead-%s-%s" % (face, edge),
                [ex * (op - 0.125), y, 0.56],
                [ex * (op - 0.125), y, tb - 0.10], 0.006, "bronze", 6)
        rod(g, groups, "door-bead-%s-mid" % face,
            [-op + 0.125, y, (tb + 0.56) * 0.5],
            [op - 0.125, y, (tb + 0.56) * 0.5], 0.006, "bronze", 6)
    for face, y in (("front", dy - 0.057), ("back", dy + 0.057)):
        fbox(g, groups, "lock-plate-" + face, [0.095, 0.018, 0.17],
             [op - 0.112, y, 1.02], "bronze", 0.005)
        rod(g, groups, "door-handle-" + face,
            [op - 0.115, y - 0.018, 1.08], [op - 0.30, y - 0.018, 1.08],
            0.013, "bronze", 8)
    return ["door-glass", "fanlight-glass", "return-glass-l", "return-glass-r"]


def build_secondary_craft(g: Graph, DATUMS: dict, C) -> list:
    """Art Nouveau spandrel scrolls, SDS rosettes and the rolled blind cassette."""
    d, groups = DATUMS["D"], DATUMS["groups"]
    tb, W = d["transom_bottom"], d["W"]

    # Whiplash stems spring from every pilaster into the transom rail.  The
    # double-curved path and curled terminal give the spandrel its Belgian line.
    for side, s in (("l", -1), ("r", 1)):
        for end, x0, direction in (("outer", s * d["outer_x"], -s),
                                   ("inner", s * d["inner_x"], s)):
            extent = (d["outer_x"] - d["inner_x"] - 0.20) * 0.58
            for leaf, amp, phase, thick in (("a", 0.15, 0.0, 0.011),
                                            ("b", -0.10, PI * 0.70, 0.008)):
                pts = []
                for i in range(13):
                    t = i / 12.0
                    px = x0 + direction * (0.030 + extent * t)
                    pz = (tb - 0.34 + 0.32 * t +
                          amp * (sin(2 * PI * t + phase) - sin(phase) * (1 - t)))
                    py = -0.115 - (0.014 if leaf == "b" else 0.0)
                    pts.append([px, py, pz])
                bent_rod(g, groups, "whiplash-%s-%s-%s" % (side, end, leaf),
                         pts, thick, "iron")
                tip = pts[-1]
                scroll = []
                for j in range(9):
                    a = -0.25 * PI + j * (1.75 * PI / 8.0) * (-1 if leaf == "b" else 1)
                    scroll.append([tip[0] + direction * 0.042 * cos(a),
                                   tip[1], tip[2] + 0.042 * sin(a)])
                bent_rod(g, groups, "whiplash-scroll-%s-%s-%s" % (side, end, leaf),
                         scroll, 0.007, "bronze")
                mid = pts[7]
                rod(g, groups, "whiplash-leaf-%s-%s-%s" % (side, end, leaf),
                    mid, [mid[0] + direction * 0.045, mid[1] - 0.008, mid[2] - 0.055],
                    0.017, "bronze", 8)
        # Cast rosette in each spandrel, a 12-sided all-quad SDS drum.
        for tag, x in (("outer", s * (d["outer_x"] - 0.24)),
                       ("inner", s * (d["inner_x"] + 0.25))):
            rod(g, groups, "rosette-stem-%s-%s" % (side, tag),
                [x, -0.123, tb - 0.20], [x, -0.123, tb - 0.015],
                0.015, "bronze", 8)
            rose = sds_cylinder(g, "rosette-%s-%s" % (side, tag), 0.043, 0.026,
                                at=[x, -0.123, tb - 0.20], axis="y", sides=12,
                                rim_crease=2.8, lod_levels=[2, 1, 0])
            groups["bronze"].append(rose)

    # Blind cassette tucked below the signboard: both end boxes are finished.
    fbox(g, groups, "blind-box", [W - 0.48, 0.17, 0.11],
         [0, -0.175, d["fb"] - 0.085], "black", 0.012)
    fbox(g, groups, "blind-pull-rail", [W - 0.50, 0.035, 0.026],
         [0, -0.268, d["fb"] - 0.152], "bronze", 0.006)
    for side, s in (("l", -1), ("r", 1)):
        fbox(g, groups, "blind-end-" + side, [0.045, 0.20, 0.135],
             [s * (W * 0.5 - 0.255), -0.175, d["fb"] - 0.085], "iron", 0.009)
        # A restrained rear bracket keeps the cassette connected to the lintel.
        fbox(g, groups, "blind-rear-bracket-" + side, [0.055, 0.15, 0.045],
             [s * (W * 0.5 - 0.255), -0.035, d["fb"] - 0.085], "iron", 0.006)
    return ["blind-box", "blind-pull-rail"]


def build_details_and_signage(g: Graph, DATUMS: dict, C) -> list:
    """Post-SDS 0x8007 riveting, leaded diamonds, tile grout and lettering."""
    d, groups = DATUMS["D"], DATUMS["groups"]
    N = C("name")

    # Rivet rows on the lintel and on each pilaster foot, on both elevations.
    for face, y in (("front", -0.151), ("back", 0.151)):
        name = "rivet-guide-lintel-" + face
        g.guide(name, [-d["W"] * 0.5 + 0.16, y, d["fb"]],
                [d["W"] * 0.5 - 0.16, y, d["fb"]])
        nodes = g.fasteners("lintel-rivets-" + face, DATUMS["lintel_id"], name,
                            head="rivet", pitch=0.18, slot="bronze", reach=0.14)
        groups["bronze"].append(nodes)
        for tag, x in (("outer-l", -d["outer_x"]), ("inner-l", -d["inner_x"]),
                       ("inner-r", d["inner_x"]), ("outer-r", d["outer_x"])):
            gid = "rivet-guide-%s-%s" % (tag, face)
            g.guide(gid, [x, y, 0.24], [x, y, 0.28])
            nodes = g.fasteners("foot-rivets-%s-%s" % (tag, face),
                                DATUMS["pilasters"][tag], gid,
                                head="rivet", count=2, slot="bronze", reach=0.20)
            groups["bronze"].append(nodes)

    # Diamond leaded quarries are shading-level strokes on the transom sheets.
    for side, s in (("l", -1), ("r", 1)):
        x, span = s * d["display_c"], d["display_span"]
        zm = (d["transom_bottom"] + d["fb"]) * 0.5
        guides = []
        for k in range(6):
            xl = x - span * 0.5 + span * k / 6.0
            xr = xl + span / 6.0
            xm = (xl + xr) * 0.5
            pts = [[xm, -0.015, d["transom_bottom"] + 0.055],
                   [xr - 0.012, -0.015, zm],
                   [xm, -0.015, d["fb"] - 0.055],
                   [xl + 0.012, -0.015, zm],
                   [xm, -0.015, d["transom_bottom"] + 0.055]]
            for j in range(4):
                guides.append(g.guide("quarry-%s-%d-%d" % (side, k, j),
                                      pts[j], pts[j + 1]))
        g.join("quarries-" + side, *guides)
        detailed = g.stroke("leaded-quarries-" + side, DATUMS["transom_glass"][side],
                            "quarries-" + side, profile="bead", width=0.011,
                            height=0.0035, slot="bronze", reach=0.12, step=0.06)
        groups["glass"].remove(DATUMS["transom_glass"][side])
        groups["glass"].append(detailed)

    # Tiled vestibule floor gets real grout lines as data, not extra mesh.
    guides = []
    op, depth = d["door_half"], DATUMS["recess_depth"]
    for k in range(1, 6):
        x = -op + op * 2 * k / 6.0
        guides.append(g.guide("tile-long-%d" % k, [x, 0.012, 0.061],
                              [x, depth - 0.012, 0.061]))
    for k in range(1, 5):
        y = depth * k / 5.0
        guides.append(g.guide("tile-cross-%d" % k, [-op + 0.012, y, 0.061],
                              [op - 0.012, y, 0.061]))
    g.join("tile-grid", *guides)
    tiled = g.seam("floor-grouted", DATUMS["floor_id"], "tile-grid",
                   width=0.009, depth=0.003, slot="black", reach=0.10, step=0.08)
    groups["tile"].remove(DATUMS["floor_id"])
    groups["tile"].append(tiled)

    # Shop name is lettered on both elevations of the fascia, fitted to the panel.
    names = ("MAISON IRIS", "FLEURS DU NORD", "ATELIER NOUVEAU")
    board = DATUMS["fascia_id"]
    for face, y, fc in (("street", -0.122, "-y"), ("inside", 0.102, "+y")):
        board = inscribe(g, "shop-name-" + face, board, names,
                         [0, y, (d["fb"] + d["ft"]) * 0.5],
                         [d["W"] - 1.15, d["fascia"] - 0.11],
                         face=fc, font="nouveau", variant=N,
                         profile="bead", slot="marking", reach=0.15)
    groups["black"].remove(DATUMS["fascia_id"])
    groups["black"].append(board)

    # Enamelled street numbers on both faces of the inner-left pilaster.
    for face, y, fc in (("street", -0.107, "-y"), ("inside", 0.107, "+y")):
        plaque = number_plaque(g, "house-number-" + face, "25",
                               at=[-d["inner_x"], y, d["riser"] + 1.05],
                               size=[0.125, 0.025, 0.165], face=fc, font="nouveau")
        groups["marking"].append(plaque)
    return ["shop-name-street", "shop-name-inside", "floor-grouted"]


def build_module():
    kit = Kit(ROOT, "hy4/out/iron-shopfront", "iron-shopfront")
    g = Graph()
    controls = {
        "width": control(4.5, 3, 7),
        "height": control(3.6, 3, 4.6),
        "name": control(0, 0, 2, True),
    }
    ports = {
        "wall": port([0.0, 0.0, 0.0], [0.0, -1.0, 0.0], "wall",
                     {"width": "$width", "height": "$height"}),
    }
    W, H = C("width"), C("height")
    half = g.let("half-width", W * 0.5)
    door_half = g.let("door-half", W * 0.105 + 0.10)
    outer_x = g.let("outer-column", half - 0.12)
    inner_x = g.let("inner-column", door_half + 0.15)
    fascia = g.let("fascia-height", H * DATUMS["fascia_h"])
    transom = g.let("transom-height", H * DATUMS["transom_h"])
    fascia_top = g.let("fascia-top", H - 0.10)
    fascia_bottom = g.let("fascia-bottom", fascia_top - fascia)
    transom_bottom = g.let("transom-bottom", fascia_bottom - transom)
    display_a = g.let("display-a", inner_x + 0.11)
    display_b = g.let("display-b", outer_x - 0.11)
    display_span = g.let("display-span", display_b - display_a)
    display_c = g.let("display-centre", (display_a + display_b) * 0.5)
    datums = dict(DATUMS)
    datums["W"] = W
    datums["H"] = H
    datums["groups"] = {slot: [] for slot in SLOTS}
    datums["D"] = {
        "W": W, "H": H, "half": half, "door_half": door_half,
        "outer_x": outer_x, "inner_x": inner_x,
        "riser": g.let("riser-height", H * DATUMS["riser_h"]),
        "fascia": fascia, "transom": transom,
        "ft": fascia_top, "fb": fascia_bottom, "fascia_top": fascia_top,
        "fascia_bottom": fascia_bottom, "transom_bottom": transom_bottom,
        "shaft_top": g.let("shaft-top", fascia_bottom - 0.11),
        "display_a": display_a, "display_b": display_b,
        "display_span": display_span, "display_c": display_c,
    }

    for builder in (build_base, build_primary_mass, build_openings_or_mechanism,
                    build_secondary_craft, build_details_and_signage):
        builder(g, datums, C)

    material_parts = []
    for slot, ids in datums["groups"].items():
        if ids:
            joined = g.join("all-" + slot, *ids)
            material_parts.append(g.mat("material-" + slot, joined, slot))
    g.join("root", *material_parts)
    kit.module("iron-shopfront", controls, g, "root", ports)
    kit.close()
    try:
        pass  # review notes are not written for examples
    except Exception:
        pass


if __name__ == "__main__":
    build_module()
