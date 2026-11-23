#!/usr/bin/env python3
"""KIT-FORGE modular 5-function builder for `city-gate` (Ledoux-style barriere)."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "procgen" / "recipes"))

from kitlib import (
    Kit, Graph, C, control, port, ref, review,
    lt, gt, iff, mn, mx, absx, sin, cos, sqrt, atan2, floor, ceil, PI, INDEX,
)

ROOT = Path(__file__).resolve().parent.parent


class MaterialGraph(Graph):
    """Graph wrapper: allow `g.mat(id, id, slot)` / `g.xf(id, id, ...)` idioms safely.

    Material and transform nodes are emitted under `<id>-surf` / `<id>-xf`; consumers
    (`mat`, `array`, `join`) resolve the alias to the last node carrying that id.
    """

    def __init__(self):
        super().__init__()
        self._alias = {}

    def xf(self, id_, src, **kw):
        src = self._alias.get(src, src)
        if id_ == src or id_ in self._alias:
            src = self._alias.get(id_, src)
            self._alias[id_] = id_ + "-xf"
            return super().xf(id_ + "-xf", src, **kw)
        out = super().xf(id_, src, **kw)
        self._alias[id_] = out if isinstance(out, str) else id_
        return out

    def mat(self, id_, src, slot):
        src = self._alias.get(src, src)
        if id_ == src or id_ in self._alias:
            src = self._alias.get(id_, src)
            self._alias[id_] = id_ + "-surf"
            return super().mat(id_ + "-surf", src, slot)
        out = super().mat(id_, src, slot)
        self._alias[id_] = out if isinstance(out, str) else id_
        return out

    def array(self, id_, src, *args, **kw):
        src = self._alias.get(src, src)
        out = super().array(id_, src, *args, **kw)
        self._alias[id_] = out if isinstance(out, str) else id_
        return out

    def join(self, id_, *parts):
        return super().join(id_, *[self._alias.get(p, p) for p in parts])


REFS = {
    "city-gate": ref(
        atlas=["MN14", "MN15", "MN19", "UT08", "UT09"],
        sources=[],
        plates=[],
        notes="Speculative neoclassical toll house after Ledoux's barrieres and Boullée's austere masonry; "
              "not a measured survey.",
        review=review(
            "Voussoir joints and rustication are regular machine-cut repeats; the drum colonnade is diagrammatic, "
            "and no lamps, chains or toll fittings appear on the facade.",
            improve=["Add carved frieze inscriptions and iron toll fittings", "Vary stone course lengths"],
            reviewed="2026-11-17",
        ),
    ),
}


def TUNE(default, lo=None, hi=None):
    """Identity wrapper marking continuous geometric parameters eligible for zero-LLM CMA-ES tuning."""
    return default


DATUMS = {
    "ground_z": 0.0,
    "base_h_frac": 0.105,
    "body_top_frac": 0.76,
    "crown_frac": 0.50,
    "passage_frac": 0.32,
    "depth_frac": 0.62,
    "course_count": 16,
    "joint_gap_m": 0.055,
    "ring_frac": 0.030,
    "col_h_frac": 0.43,
    "ent_frac": 0.25,
    "ped_pitch_deg": 15.0,
    "drum_r_frac": 0.225,
    "col_gap_m": 0.35,
    "butt_gap_m": 0.0005,
    "min_materials": 2,
    "ctrl_width_default": 16.0,
    "ctrl_width_min": 11.0,
    "ctrl_width_max": 22.0,
    "ctrl_height_default": 14.0,
    "ctrl_height_min": 10.0,
    "ctrl_height_max": 18.0,
    "ctrl_columns_default": 4.0,
    "ctrl_columns_min": 4.0,
    "ctrl_columns_max": 6.0,
}


def derive(g, DATUMS, C):
    """Anatomy and clearance datum table, all named in the graph with G.let."""
    W, H, N = C("width"), C("height"), C("columns")
    L = lambda name, val: g.let(name, val)
    e = {}
    e["hw"] = L("gate-half-width", W * 0.5)
    e["depth"] = L("gate-depth", W * DATUMS["depth_frac"])
    e["hd"] = L("gate-half-depth", e["depth"] * 0.5)
    e["plinth_top"] = L("gate-plinth-top", DATUMS["ground_z"] + H * DATUMS["base_h_frac"])
    e["plinth_hw"] = L("gate-plinth-half-width", e["hw"] + 0.30)
    e["plinth_hd"] = L("gate-plinth-half-depth", e["hd"] + 1.55)
    e["body_top"] = L("gate-body-top", DATUMS["ground_z"] + H * DATUMS["body_top_frac"])
    e["course_h"] = L("gate-course-height", (e["body_top"] - e["plinth_top"] - 0.02) / DATUMS["course_count"])
    e["R"] = L("gate-passage-radius", W * DATUMS["passage_frac"] * 0.5)
    e["crown"] = L("gate-crown", DATUMS["ground_z"] + H * DATUMS["crown_frac"])
    e["spring"] = L("gate-spring", e["crown"] - e["R"])
    e["ring"] = L("gate-voussoir-depth", W * DATUMS["ring_frac"])
    e["open_r"] = L("gate-cut-radius", e["R"] + e["ring"] + 0.001)
    e["col_z0"] = L("gate-column-base", e["plinth_top"] + 0.002)
    e["col_h"] = L("gate-column-height", H * DATUMS["col_h_frac"])
    e["col_r"] = L("gate-column-radius", e["col_h"] / 14.0)
    e["col_top"] = L("gate-column-top", e["col_z0"] + e["col_h"])
    e["ent_h"] = L("gate-entablature-height", e["col_h"] * DATUMS["ent_frac"])
    e["ent_top"] = L("gate-entablature-top", e["col_top"] + e["ent_h"])
    e["portico_y"] = L("gate-portico-offset", e["hd"] + 0.92)
    e["ent_depth"] = L("gate-entablature-depth", e["portico_y"] + e["col_r"] * 1.2 - e["hd"] + 0.25)
    e["ent_y"] = L("gate-entablature-centre", e["hd"] + e["ent_depth"] * 0.5 - 0.12)
    e["ped_span"] = L("gate-pediment-span", W * 0.88)
    e["ped_rise"] = L("gate-pediment-rise", W * 0.88 * 0.5 * 0.2679)
    e["drum_r"] = L("gate-drum-radius", W * DATUMS["drum_r_frac"])
    e["drum_z0"] = L("gate-drum-base", e["body_top"] - 0.001)
    e["drum_h"] = L("gate-drum-height", H - e["drum_z0"] - 0.02)
    e["half_l"] = L("gate-columns-per-side-left", ceil(N * 0.5))
    e["half_r"] = L("gate-columns-per-side-right", floor(N * 0.5))
    e["x_inner"] = L("gate-inner-column-x", e["R"] + e["col_r"] + DATUMS["col_gap_m"])
    e["x_outer"] = L("gate-outer-column-x", e["hw"] - e["col_r"] - 0.50)
    e["x_step"] = L("gate-column-step", (e["x_outer"] - e["x_inner"]) / mn(mx(e["half_l"] - 1, 1), 2))
    return e


def _extrude_xz(g, id_, profile, depth, y_far, mat):
    """Closed x-z profile extruded across y; solid spans y_far - depth .. y_far."""
    g.n(id_ + "-stock", "extrude", profile=profile, depth=depth, cap="inset")
    g.xf(id_, id_ + "-stock", t=[0, y_far, 0], r=[PI * 0.5, 0, 0])
    g.mat(id_, id_, mat)
    return id_


def build_base(g: Graph, DATUMS: dict, C) -> list[str]:
    """Drafted, stepped plinth split around the unobstructed road corridor."""
    e = DATUMS["e"]
    parts = []
    gap = g.let("gate-road-half-clear", mx(e["R"] + 0.10, 2.70))
    layers = [
        ("footing", e["plinth_hw"] * 2 + 0.24, e["plinth_hd"] * 2 + 0.24,
         0.20, 0.10, 0.025, "stone"),
        ("plinth", e["plinth_hw"] * 2, e["plinth_hd"] * 2,
         e["plinth_top"] - 0.16, 0.16 + (e["plinth_top"] - 0.16) * 0.5, 0.022, "stone"),
        ("plinth-band", e["plinth_hw"] * 2 - 0.16, e["plinth_hd"] * 2 - 0.16,
         0.10, e["plinth_top"] - 0.05 + DATUMS["butt_gap_m"], 0.014, "pier-ashlar"),
    ]
    for nm, full_w, full_d, hh, zc, bevel, mat in layers:
        half = g.let("gate-half-" + nm, full_w * 0.5 - gap)
        centre = g.let("gate-centre-" + nm, (full_w * 0.5 + gap) * 0.5)
        for sgn, side in ((-1, "w"), (1, "e")):
            raw = "gate-%s-%s-raw" % (nm, side)
            bev = "gate-%s-%s-bev" % (nm, side)
            out = "gate-%s-%s" % (nm, side)
            g.box(raw, [half, full_d, hh], at=[sgn * centre, 0, zc])
            g.chamfer(bev, raw, width=bevel, segments=3, profile=0.5, clampOverlap=1)
            g.smooth(out, bev, angle=55, weighting="areaSq")
            g.mat(out, out, mat)
            parts.append(out)
    return parts


def build_primary_mass(g: Graph, DATUMS: dict, C) -> list[str]:
    """Recessed continuous core plus sixteen rusticated courses around the arch."""
    e = DATUMS["e"]
    parts = []
    core_z0 = e["plinth_top"] - 0.004
    core_z1 = e["body_top"]
    core_x = e["hw"] - 0.28
    for sgn, tag in ((-1, "l"), (1, "r")):
        _extrude_xz(g, "gate-course-backing-" + tag,
                    [[sgn * e["open_r"], core_z0], [sgn * core_x, core_z0],
                     [sgn * core_x, core_z1], [sgn * e["open_r"], core_z1]],
                    e["depth"], e["hd"], "chipped-limestone")
        parts.append("gate-course-backing-" + tag)
    tie_z = e["crown"] + 0.05
    _extrude_xz(g, "gate-course-backing-tie",
                [[-core_x, tie_z], [core_x, tie_z], [core_x, core_z1], [-core_x, core_z1]],
                e["depth"], e["hd"], "chipped-limestone")
    parts.append("gate-course-backing-tie")

    n = DATUMS["course_count"]
    gap = DATUMS["joint_gap_m"]
    ts = [0.0, 0.25, 0.5, 0.75, 1.0]

    def opening(z):
        dz = z - e["spring"]
        arc = sqrt(mx(e["open_r"] * e["open_r"] - dz * dz, 0.0001))
        return mx(iff(lt(z, e["spring"]), e["open_r"], arc), 0.02)

    for i in range(n):
        z0 = e["plinth_top"] + 0.02 + e["course_h"] * i
        z1 = z0 + e["course_h"] - gap
        zs = [z0 + (z1 - z0) * t for t in ts]
        aa = [opening(zz) for zz in zs]
        left = [[-e["hw"], z0], [-aa[0], z0]] + [[-aa[k], zs[k]] for k in range(1, len(ts))] + [[-e["hw"], z1]]
        right = [[aa[0], z0], [e["hw"], z0], [e["hw"], z1]] + [[aa[k], zs[k]] for k in (3, 2, 1)]
        _extrude_xz(g, "gate-course-l-%02d" % i, left, e["depth"], e["hd"],
                    "chipped-limestone" if i % 3 else "pier-ashlar")
        _extrude_xz(g, "gate-course-r-%02d" % i, right, e["depth"], e["hd"],
                    "chipped-limestone" if i % 3 else "pier-ashlar")
        parts.extend(["gate-course-l-%02d" % i, "gate-course-r-%02d" % i])
    return parts


def build_openings_or_mechanism(g: Graph, DATUMS: dict, C) -> list[str]:
    """Barrel-vaulted road passage: radial voussoirs and flat jambs, open front to back."""
    e = DATUMS["e"]
    parts = []
    nv = 15
    y_far = e["hd"] + 0.055
    depth = e["depth"] + 0.11
    for i in range(nv):
        t0 = -PI * 0.5 + PI * i / nv
        t1 = -PI * 0.5 + PI * (i + 1) / nv
        r0, r1 = e["R"], e["R"] + e["ring"]
        prof = [
            [r0 * sin(t0), e["spring"] + r0 * cos(t0)],
            [r0 * sin(t1), e["spring"] + r0 * cos(t1)],
            [r1 * sin(t1), e["spring"] + r1 * cos(t1)],
            [r1 * sin(t0), e["spring"] + r1 * cos(t0)],
        ]
        _extrude_xz(g, "gate-vault-stone-%02d" % i, prof, depth, y_far,
                    "pier-ashlar" if i % 4 else "chipped-limestone")
        parts.append("gate-vault-stone-%02d" % i)

    # Jamb stones carry the springing on each side for the full depth of the roadway.
    for sgn, tag in ((-1, "l"), (1, "r")):
        g.box("gate-jamb-" + tag,
              [e["ring"], e["depth"] + 0.10, e["spring"] - e["plinth_top"] - 0.02],
              at=[sgn * (e["R"] + e["ring"] * 0.5), 0,
                  (e["plinth_top"] + 0.02 + e["spring"]) * 0.5])
        g.mat("gate-jamb-" + tag, "gate-jamb-" + tag, "pier-ashlar")
        parts.append("gate-jamb-" + tag)

    # Projecting keystones on both elevations (outside the wall face, no burial).
    for sgn, tag in ((-1, "front"), (1, "back")):
        t0, t1 = -0.085, 0.085
        prof = [
            [e["R"] * sin(t0), e["spring"] + e["R"] * cos(t0)],
            [e["R"] * sin(t1), e["spring"] + e["R"] * cos(t1)],
            [(e["R"] + e["ring"] + 0.30) * sin(t1), e["spring"] + (e["R"] + e["ring"] + 0.30) * cos(t1)],
            [(e["R"] + e["ring"] + 0.30) * sin(t0), e["spring"] + (e["R"] + e["ring"] + 0.30) * cos(t0)],
        ]
        key_y = -(e["hd"] + 0.055) if sgn < 0 else (e["hd"] + 0.195)
        _extrude_xz(g, "gate-keystone-" + tag, prof, 0.14, key_y, "pier-ashlar")
        parts.append("gate-keystone-" + tag)
    return parts


def build_secondary_craft(g: Graph, DATUMS: dict, C) -> list[str]:
    """Twin Tuscan porticoes, deep side windows, and a massive colonnaded attic drum."""
    e = DATUMS["e"]
    parts = []

    # One column unit: square die, turned base, tapered shaft, plain Tuscan capital + abacus.
    cr = e["col_r"]
    g.box("gate-col-foot-raw", [cr * 2.50, cr * 2.50, 0.12], at=[0, 0, 0.03])
    g.chamfer("gate-col-foot-bev", "gate-col-foot-raw", width=0.015, segments=3, profile=0.5, clampOverlap=1)
    g.smooth("gate-col-foot", "gate-col-foot-bev", angle=55, weighting="areaSq")
    g.lathe("gate-col-shaft", profile=[
        [cr * 0.86, 0.0], [cr * 1.04, 0.055], [cr * 1.04, 0.16],
        [cr * 0.90, 0.25], [cr * 0.82, 0.38], [cr * 0.74, e["col_h"] * 0.90],
        [cr * 0.80, e["col_h"] * 0.925], [cr * 1.06, e["col_h"] * 0.96],
        [cr * 1.06, e["col_h"]], [cr * 0.86, 0.0],
    ], sides=20, cap="quad")
    g.box("gate-col-abacus-raw", [cr * 2.55, cr * 2.55, e["col_h"] * 0.035],
          at=[0, 0, e["col_h"] * 1.018])
    g.chamfer("gate-col-abacus-bev", "gate-col-abacus-raw", width=0.012, segments=3, profile=0.5, clampOverlap=1)
    g.smooth("gate-col-abacus", "gate-col-abacus-bev", angle=55, weighting="areaSq")
    g.join("gate-col-unit", "gate-col-foot", "gate-col-shaft", "gate-col-abacus")
    g.mat("gate-col-unit", "gate-col-unit", "stone")

    for sgn, tag in ((-1, "front"), (1, "back")):
        cy = sgn * e["portico_y"]
        for side, cnt, step in (("l", e["half_l"], -e["x_step"]), ("r", e["half_r"], e["x_step"])):
            startx = -e["x_inner"] if side == "l" else e["x_inner"]
            g.array("gate-col-%s-%s" % (tag, side), "gate-col-unit",
                    start=[startx, cy, e["col_z0"]], step=[step, 0, 0], count=cnt)
            g.mat("gate-col-%s-%s" % (tag, side), "gate-col-%s-%s" % (tag, side), "stone")
            parts.append("gate-col-%s-%s" % (tag, side))

        # Wall-anchored antae make the portico read as structure rather than a screen.
        for ax, atag in ((-1, "w"), (1, "e")):
            g.box("gate-anta-%s-%s-raw" % (tag, atag),
                  [cr * 1.20, 0.36, e["ent_top"] - e["plinth_top"]],
                  at=[ax * (e["x_outer"] + cr * 1.55), sgn * (e["hd"] + 0.17),
                      (e["plinth_top"] + e["ent_top"]) * 0.5])
            g.chamfer("gate-anta-%s-%s-bev" % (tag, atag), "gate-anta-%s-%s-raw" % (tag, atag),
                      width=0.018, segments=3, profile=0.5, clampOverlap=1)
            g.smooth("gate-anta-%s-%s" % (tag, atag), "gate-anta-%s-%s-bev" % (tag, atag),
                     angle=55, weighting="areaSq")
            g.mat("gate-anta-%s-%s" % (tag, atag), "gate-anta-%s-%s" % (tag, atag), "chipped-limestone")
            parts.append("gate-anta-%s-%s" % (tag, atag))

        # Architrave / frieze / cornice: three offset FWN-machined bands.
        yc = sgn * e["ent_y"]
        outer_y = e["ent_y"] + e["ent_depth"] * 0.64
        bands = [
            ("architrave", 0.44, 1.00, "stone"),
            ("frieze", 0.36, 1.10, "chipped-limestone"),
            ("cornice", 0.20, 1.28, "pier-ashlar"),
        ]
        zc = e["col_top"]
        for nm, hf, df, mat in bands:
            hh = e["ent_h"] * hf
            g.box("gate-ent-%s-%s-raw" % (nm, tag),
                  [e["ped_span"] * 1.02, e["ent_depth"] * df, hh],
                  at=[0, yc, zc + hh * 0.5])
            g.chamfer("gate-ent-%s-%s-bev" % (nm, tag), "gate-ent-%s-%s-raw" % (nm, tag),
                      width=0.014, segments=3, profile=0.5, clampOverlap=1)
            g.smooth("gate-ent-%s-%s" % (nm, tag), "gate-ent-%s-%s-bev" % (nm, tag), angle=55, weighting="areaSq")
            g.mat("gate-ent-%s-%s" % (nm, tag), "gate-ent-%s-%s" % (nm, tag), mat)
            parts.append("gate-ent-%s-%s" % (nm, tag))
            zc = zc + hh

        # Dentil course tucked under the cornice, on both elevations.
        dz = e["col_top"] + e["ent_h"] * 0.80 - 0.075
        for k in range(13):
            xx = e["ped_span"] * 0.86 * (k / 12.0 - 0.5)
            g.box("gate-dentil-%s-%02d" % (tag, k), [0.16, 0.22, 0.15],
                  at=[xx, sgn * (e["ent_y"] + e["ent_depth"] * 0.55 + 0.03), dz])
            g.mat("gate-dentil-%s-%02d" % (tag, k), "gate-dentil-%s-%02d" % (tag, k), "stone")
            parts.append("gate-dentil-%s-%02d" % (tag, k))

        # Triangular pediment: retained inside the cornice depth, with a recessed tympanum.
        pz = e["ent_top"]
        pdep = e["ent_depth"] * 0.80
        y_far = sgn * outer_y + (pdep if sgn < 0 else 0.0)
        _extrude_xz(g, "gate-pediment-" + tag,
                    [[-e["ped_span"] * 0.5, pz], [e["ped_span"] * 0.5, pz], [0, pz + e["ped_rise"]]],
                    pdep, y_far, "chipped-limestone")
        parts.append("gate-pediment-" + tag)
        y_face = sgn * outer_y
        y_far_in = (y_face + 0.01) if sgn < 0 else (y_face - 0.01)
        _extrude_xz(g, "gate-tympanum-" + tag,
                    [[-e["ped_span"] * 0.36, pz + 0.10],
                     [e["ped_span"] * 0.36, pz + 0.10],
                     [0, pz + e["ped_rise"] * 0.64]],
                    0.05, y_far_in, "pier-ashlar")
        parts.append("gate-tympanum-" + tag)
        yrake = sgn * (outer_y + 0.045)
        for k, sx in ((-1, 1), (1, -1)):
            g.rod("gate-rake-%s-%s" % (tag, k),
                  [sx * e["ped_span"] * 0.5, yrake, pz + 0.03],
                  [0, yrake, pz + e["ped_rise"] + 0.03], 0.085, cap="quad")
            g.mat("gate-rake-%s-%s" % (tag, k), "gate-rake-%s-%s" % (tag, k), "pier-ashlar")
            parts.append("gate-rake-%s-%s" % (tag, k))
            g.box("gate-acroterion-%s-%s" % (tag, k), [0.26, 0.26, 0.30],
                  at=[sx * e["ped_span"] * 0.5, yrake, pz + 0.13])
            g.mat("gate-acroterion-%s-%s" % (tag, k), "gate-acroterion-%s-%s" % (tag, k), "stone")
            parts.append("gate-acroterion-%s-%s" % (tag, k))
        g.box("gate-apex-%s" % tag, [0.34, 0.30, 0.42],
              at=[0, yrake, pz + e["ped_rise"] + 0.18])
        g.mat("gate-apex-%s" % tag, "gate-apex-%s" % tag, "stone")
        parts.append("gate-apex-%s" % tag)

    # Deep-set square windows on both side elevations, two per side, with iron grilles.
    wz = e["plinth_top"] + (e["body_top"] - e["plinth_top"]) * 0.51
    wh = e["col_h"] * 0.13
    ww = e["col_h"] * 0.17
    for sgn, tag in ((-1, "west"), (1, "east")):
        for j, yy in enumerate((-e["depth"] * 0.23, e["depth"] * 0.23)):
            xf = sgn * e["hw"]
            g.box("gate-window-glass-%s-%d" % (tag, j), [0.016, ww, wh],
                  at=[xf + sgn * 0.004, yy, wz])
            g.mat("gate-window-glass-%s-%d" % (tag, j), "gate-window-glass-%s-%d" % (tag, j), "slate")
            parts.append("gate-window-glass-%s-%d" % (tag, j))
            bars = [
                ("jamb-a", [0.16, 0.13, wh + 0.30], yy - ww * 0.5 - 0.085, wz),
                ("jamb-b", [0.16, 0.13, wh + 0.30], yy + ww * 0.5 + 0.085, wz),
                ("lintel", [0.16, ww + 0.42, 0.15], yy, wz + wh * 0.5 + 0.085),
                ("sill", [0.24, ww + 0.46, 0.15], yy, wz - wh * 0.5 - 0.085),
                ("keystone", [0.10, 0.22, 0.26], yy, wz + wh * 0.5 + 0.19),
            ]
            for nm, sz, cy, cz in bars:
                g.box("gate-win-%s-%d-%s" % (tag, j, nm), sz,
                      at=[xf + sgn * (sz[0] * 0.5 - 0.002), cy, cz])
                g.mat("gate-win-%s-%d-%s" % (tag, j, nm), "gate-win-%s-%d-%s" % (tag, j, nm), "pier-ashlar")
                parts.append("gate-win-%s-%d-%s" % (tag, j, nm))
            for b in range(3):
                g.box("gate-win-bar-%s-%d-%d" % (tag, j, b), [0.035, 0.035, wh * 0.86],
                      at=[xf + sgn * 0.018, yy + (b - 1) * ww * 0.30, wz])
                g.mat("gate-win-bar-%s-%d-%d" % (tag, j, b), "gate-win-bar-%s-%d-%d" % (tag, j, b), "bronze")
                parts.append("gate-win-bar-%s-%d-%d" % (tag, j, b))
            for c in (-1, 1):
                g.box("gate-win-corbel-%s-%d-%d" % (tag, j, c), [0.30, 0.16, 0.22],
                      at=[xf + sgn * 0.15, yy + c * ww * 0.40, wz - wh * 0.5 - 0.20])
                g.mat("gate-win-corbel-%s-%d-%d" % (tag, j, c),
                      "gate-win-corbel-%s-%d-%d" % (tag, j, c), "chipped-limestone")
                parts.append("gate-win-corbel-%s-%d-%d" % (tag, j, c))

    # Attic drum: enlarged solid wall, twelve recessed windows, engaged colonnade and copper dome.
    dr = e["drum_r"] * 1.12
    wall_r = dr * 0.76
    outer_r = dr * 1.06
    z0 = e["drum_z0"]
    z1 = z0 + e["drum_h"]
    base_h = e["drum_h"] * 0.10
    cap_h = e["drum_h"] * 0.14
    col_gap = e["drum_h"] - base_h - cap_h

    g.n("gate-drum-wall-stock", "cylinder", radius=wall_r, depth=col_gap,
        sides=32, cap="quad")
    g.xf("gate-drum-wall-raw", "gate-drum-wall-stock",
         t=[0, 0, z0 + base_h + col_gap * 0.50718])
    g.crease("gate-drum-wall-cr", "gate-drum-wall-raw", sharpness=1.4, min_angle=40)
    g.subdivide("gate-drum-wall", "gate-drum-wall-cr", levels=1, creaseMethod="chaikin",
                limit=1, limit_normals=1, lodLevels=[1, 0], g2_fair=1)
    g.mat("gate-drum-wall", "gate-drum-wall", "chipped-limestone")
    parts.append("gate-drum-wall")

    g.lathe("gate-drum-base-ring", profile=[
        [wall_r * 0.98, 0], [outer_r, 0], [outer_r, base_h * 0.72],
        [wall_r * 1.05, base_h], [wall_r * 0.98, base_h], [wall_r * 0.98, 0],
    ], sides=32, cap="quad")
    g.xf("gate-drum-base-ring", "gate-drum-base-ring", t=[0, 0, z0])
    g.mat("gate-drum-base-ring", "gate-drum-base-ring", "pier-ashlar")
    parts.append("gate-drum-base-ring")

    g.lathe("gate-drum-cornice", profile=[
        [wall_r * 0.96, 0], [outer_r * 1.06, 0], [outer_r * 1.06, cap_h * 0.70],
        [outer_r * 1.10, cap_h], [wall_r * 0.96, cap_h], [wall_r * 0.96, 0],
    ], sides=32, cap="quad")
    g.xf("gate-drum-cornice", "gate-drum-cornice", t=[0, 0, z1 - cap_h])
    g.mat("gate-drum-cornice", "gate-drum-cornice", "stone")
    parts.append("gate-drum-cornice")

    # One radial window bay, repeated around the wall; panel overlaps the wall by 3 mm.
    win_w = dr * 0.15
    win_h = col_gap * 0.44
    win_z = z0 + base_h + col_gap * 0.52
    g.box("gate-drum-win-panel-stock", [win_w, 0.030, win_h], at=[0, wall_r + 0.004, 0])
    g.box("gate-drum-win-sill-stock", [win_w * 1.35, 0.16, 0.10],
          at=[0, wall_r + 0.030, -win_h * 0.5 - 0.05])
    g.box("gate-drum-win-head-stock", [win_w * 1.35, 0.16, 0.10],
          at=[0, wall_r + 0.030, win_h * 0.5 + 0.05])
    for s in (-1, 1):
        g.box("gate-drum-win-jamb-%d-stock" % s, [0.09, 0.15, win_h],
              at=[s * (win_w * 0.5 + 0.045), wall_r + 0.030, 0])
    g.join("gate-drum-window-unit", "gate-drum-win-panel-stock", "gate-drum-win-sill-stock",
           "gate-drum-win-head-stock", "gate-drum-win-jamb--1-stock", "gate-drum-win-jamb-1-stock")
    g.mat("gate-drum-window-unit", "gate-drum-window-unit", "slate")
    for k in range(12):
        th = PI * 2.0 * k / 12.0
        wid = "gate-drum-window-%02d" % k
        g.xf(wid, "gate-drum-window-unit", t=[0, 0, win_z], r=[0, 0, th])
        parts.append(wid)

    # Engaged colonnade: substantial shafts with square abaci, seated on the rings.
    col_ring = (wall_r + outer_r) * 0.52
    shaft_r = dr * 0.056
    g.n("gate-drum-col-stock", "cylinder", radius=shaft_r, depth=col_gap + 0.030,
        sides=16, cap="quad")
    g.xf("gate-drum-col-shaft-stock", "gate-drum-col-stock", t=[0, col_ring, 0])
    g.box("gate-drum-col-base-stock", [shaft_r * 2.8, shaft_r * 2.8, 0.10],
          at=[0, col_ring, -col_gap * 0.5])
    g.box("gate-drum-col-abacus-stock", [shaft_r * 3.2, shaft_r * 3.2, 0.10],
          at=[0, col_ring, col_gap * 0.5])
    g.join("gate-drum-col-unit", "gate-drum-col-shaft-stock",
           "gate-drum-col-base-stock", "gate-drum-col-abacus-stock")
    g.mat("gate-drum-col-unit", "gate-drum-col-unit", "stone")
    for k in range(12):
        th = PI * 2.0 * k / 12.0
        cid = "gate-drum-pier-%02d" % k
        g.xf(cid, "gate-drum-col-unit",
             t=[0, 0, z0 + base_h + col_gap * 0.5], r=[0, 0, th])
        parts.append(cid)

    rise = dr * 0.30
    g.lathe("gate-drum-roof-stock", profile=[
        [0.05, rise], [dr * 0.22, rise * 0.82], [dr * 0.52, rise * 0.42],
        [dr * 0.82, rise * 0.12], [dr * 1.12, 0], [dr * 1.12, -0.14],
        [0.05, -0.14], [0.05, rise],
    ], sides=32, cap="quad")
    g.xf("gate-drum-roof-raw", "gate-drum-roof-stock", t=[0, 0, z1])
    g.crease("gate-drum-roof-cr", "gate-drum-roof-raw", sharpness=1.4, min_angle=38)
    g.subdivide("gate-drum-roof", "gate-drum-roof-cr", levels=1, creaseMethod="chaikin",
                limit=1, limit_normals=1, lodLevels=[1, 0])
    g.mat("gate-drum-roof", "gate-drum-roof", "copper")
    parts.append("gate-drum-roof")

    g.lathe("gate-drum-finial-stock", profile=[
        [0.04, 0], [dr * 0.060, 0], [dr * 0.055, 0.16],
        [dr * 0.024, 0.30], [dr * 0.030, 0.40], [0.04, 0.56], [0.04, 0],
    ], sides=16, cap="quad")
    g.xf("gate-drum-finial", "gate-drum-finial-stock", t=[0, 0, z1 + rise - 0.16])
    g.mat("gate-drum-finial", "gate-drum-finial", "copper")
    parts.append("gate-drum-finial")
    return parts


def build_details_and_signage(g: Graph, DATUMS: dict, C) -> list[str]:
    """Post-subdivision 0x8007 detail: coursed ashlar joints, jamb seams, frieze work."""
    e = DATUMS["e"]
    parts = []
    body_h = e["body_top"] - e["plinth_top"]
    mid_z = e["plinth_top"] + body_h * 0.5
    for sgn, tag in ((-1, "front"), (1, "back")):
        g.courses("gate-ashlar-" + tag, surface="gate-course-backing-tie",
                  at=[0, sgn * (e["hd"] + 0.003), mid_z], r=[0, 0, 0],
                  size=[e["hw"] * 1.93, body_h * 0.94], course=0.085, block=0.62, minimum=0.20)
    for sgn, tag in ((-1, "west"), (1, "east")):
        g.guide("gate-jamb-guide-" + tag,
                [sgn * (e["R"] + e["ring"] * 0.5), -e["hd"] - 0.002, e["plinth_top"] + 0.10],
                [sgn * (e["R"] + e["ring"] * 0.5), -e["hd"] - 0.002, e["spring"] - 0.10])
        g.seam("gate-jamb-seam-" + tag, "gate-jamb-" + ("l" if sgn < 0 else "r"),
               "gate-jamb-guide-" + tag, width=0.008, depth=0.004)
    for sgn, tag in ((-1, "front"), (1, "back")):
        face = e["ent_y"] + e["ent_depth"] * 0.55
        g.box("gate-frieze-band-" + tag, [e["ped_span"] * 0.99, 0.045, 0.035],
              at=[0, sgn * (face + 0.018), e["col_top"] + e["ent_h"] * 0.72])
        g.mat("gate-frieze-band-" + tag, "gate-frieze-band-" + tag, "pier-ashlar")
        parts.append("gate-frieze-band-" + tag)
        g.box("gate-toll-plaque-" + tag, [e["col_h"] * 0.30, 0.050, e["col_h"] * 0.085],
              at=[0, sgn * (face + 0.021), e["col_top"] + e["ent_h"] * 0.60])
        g.mat("gate-toll-plaque-" + tag, "gate-toll-plaque-" + tag, "bronze")
        parts.append("gate-toll-plaque-" + tag)
        for k in range(9):
            xx = e["ped_span"] * 0.80 * (k / 8.0 - 0.5)
            g.box("gate-frieze-panel-%s-%d" % (tag, k), [0.10, 0.036, e["ent_h"] * 0.20],
                  at=[xx, sgn * (face + 0.014), e["col_top"] + e["ent_h"] * 0.60])
            g.mat("gate-frieze-panel-%s-%d" % (tag, k), "gate-frieze-panel-%s-%d" % (tag, k), "stone")
            parts.append("gate-frieze-panel-%s-%d" % (tag, k))

        # Post-SDS shading work on the machined frieze: seam, rivet row and studded plaque.
        bz = e["col_top"] + e["ent_h"] * 0.72
        gy = sgn * (face + 0.041)
        g.guide("gate-frieze-seam-guide-" + tag,
                [-e["ped_span"] * 0.46, gy, bz],
                [e["ped_span"] * 0.46, gy, bz])
        g.seam("gate-frieze-seam-" + tag, "gate-frieze-band-" + tag,
               "gate-frieze-seam-guide-" + tag, width=0.006, depth=0.003)
        g.guide("gate-frieze-rivet-guide-" + tag,
                [-e["ped_span"] * 0.44, gy, bz - 0.013],
                [e["ped_span"] * 0.44, gy, bz - 0.013])
        g.rivets("gate-frieze-rivets-" + tag, "gate-frieze-band-" + tag,
                 "gate-frieze-rivet-guide-" + tag, pitch=0.42, radius=0.014, height=0.006)
        pz = e["col_top"] + e["ent_h"] * 0.60
        py = sgn * (face + 0.047)
        pw = e["col_h"] * 0.30
        ph = e["col_h"] * 0.085
        g.points("gate-plaque-points-" + tag, [
            [-pw * 0.36, py, pz + ph * 0.28], [pw * 0.36, py, pz + ph * 0.28],
            [-pw * 0.36, py, pz - ph * 0.28], [pw * 0.36, py, pz - ph * 0.28],
        ])
        g.stamp("gate-plaque-studs-" + tag, "gate-toll-plaque-" + tag,
                "gate-plaque-points-" + tag, shape="disc", profile="dome",
                radius=0.022, height=0.006)
        g.text("gate-inscription-" + tag, "gate-toll-plaque-" + tag,
               [0, py, pz], "OCTROI", size=ph * 0.42, direction=[1, 0, 0],
               align="center", valign="center", profile="bead", height=0.004)
        parts.extend([
            "gate-frieze-seam-" + tag, "gate-frieze-rivets-" + tag,
            "gate-plaque-studs-" + tag, "gate-inscription-" + tag,
        ])
    return parts


def build_module():
    kit = Kit(ROOT, "hy4/out/city-gate", "city-gate")
    g = MaterialGraph()
    controls = {
        "width": control(16, 11, 22),
        "height": control(14, 10, 18),
        "columns": control(4, 4, 6),
    }
    datums = dict(DATUMS)
    e = derive(g, DATUMS, C)
    datums["e"] = e
    ports = {
        "road-front": port([0.0, -e["hd"], 0.0], [0.0, -1.0, 0.0], 'deck', {"width": 5}),
        "road-back": port([0.0, e["hd"], 0.0], [0.0, 1.0, 0.0], 'deck', {"width": 5}),
    }
    parts = []
    parts.extend(build_base(g, datums, C))
    parts.extend(build_primary_mass(g, datums, C))
    parts.extend(build_openings_or_mechanism(g, datums, C))
    parts.extend(build_secondary_craft(g, datums, C))
    parts.extend(build_details_and_signage(g, datums, C))
    g.join("root", *parts)
    kit.module("city-gate", controls, g, "root", ports)
    kit.close()


if __name__ == "__main__":
    build_module()


if __name__ == "__main__":
    build_module()
