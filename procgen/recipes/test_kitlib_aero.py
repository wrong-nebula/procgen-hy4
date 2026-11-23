"""Tests of the kitlib aero helpers: naca4, wing, propeller, nacelle. Built by Opus 5.5 (medium), 2026-11-16.

Builds sample documents into a temporary kit and runs pgm on them: every sample closed, manifold and
outward (pgm check), single parts free of self-intersections (check --intersections), one component
per part or assembly (pgm audit), wing chords and span as asked (read back from an OBJ export),
propeller blade angle falling toward the tip as atan(pitch / (2 pi r)), triangle budgets, controls swept.

    PGM=/path/to/pgm python3 procgen/recipes/test_kitlib_aero.py [--sheet /tmp/aero-sheet.png] [--keep DIR]
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitlib import C, PI, Graph, Kit, control, naca4  # noqa: E402

PGM = os.environ.get("PGM", str(Path(__file__).resolve().parent.parent / "build-release" / "pgm"))
KIT = None
BUDGET = {"wing": 1200, "prop": 3000, "prop-metal-3": 2000, "nacelle": 800, "boxy": 1200}


def pgm(*args):
    r = subprocess.run([PGM, *map(str, args)], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        raise AssertionError(f"pgm {' '.join(map(str, args))}: {r.stdout[:400]} {r.stderr[:400]}")


# Samples meant to be in pieces. A rudder with no hinge pins hangs free across its gap (about 3.6 cm here): the
# contact audit counted it as one piece only before its sharp-edge fix (2026-11-17).
PIECES = {"fin-rudder-bare": {"components": 2, "why": "rudder without hinge pins, separated by the hinge gap"}}


def samples():
    """name -> (controls, graph, output)."""
    out = {}

    def add(name, build, controls=None):
        g = Graph()
        out[name] = (controls or {}, g, build(g))

    add("wing-rect", lambda g: g.wing("wing", 5.0, 1.5, 1.5))
    add("wing-taper", lambda g: g.wing("wing", 6.0, 2.2, 1.1, sweep=0.12, twist=-0.06, section="2412", tip_section="0012",
                                       tip_round=0.6, slot="wood"))
    add("wing-dihedral", lambda g: g.wing("wing", 6.0, 2.0, 1.2, sweep=0.05, dihedral=0.1, twist=-0.05, section="2412",
                                          tip_round=0.7))
    add("wing-span-control", lambda g: g.wing("wing", C("span"), C("chord"), C("chord") * 0.5, sweep=C("sweep"),
                                              dihedral=0.05, section="2412", tip_round=C("chord") * 0.25),
        {"span": control(5.0, 2.0, 9.0), "chord": control(1.8, 0.8, 3.0), "sweep": control(0.1, 0.0, 0.5)})

    def fin(g, **kw):
        panel, rudder = g.wing("fin", 2.8, 3.0, 1.4, sweep=0.6, tip_round=0.5, split=0.65, r=[PI / 2, 0, 0], **kw)
        return g.join("fin-parts", panel, rudder)
    add("fin-rudder", lambda g: fin(g, slot="steel", control_slot="iron"))
    add("fin-rudder-bare", lambda g: fin(g, hinges=0, slot="steel", control_slot="iron"))

    def tailplane(g):
        p, e = g.wing("stab", 2.2, 1.4, 0.9, sweep=0.15, section="0010", tip_round=0.3, split=0.7, gap=0.015, hinges=4)
        return g.join("stab-parts", p, e)
    add("tail-elevator", tailplane)

    add("prop-wood-2", lambda g: g.propeller("prop", 2.6, 1.9))
    add("prop-metal-3", lambda g: g.propeller("prop", 2.6, 2.2, style="metal"))
    add("prop-metal-4-cw", lambda g: g.propeller("prop", 3.0, 2.6, style="metal", blades=4, turn="cw"))
    add("prop-metal-3-parts", lambda g: g.propeller("prop", 2.6, 2.2, style="metal", hub_slot="iron"))
    add("prop-control", lambda g: g.propeller("prop", C("diameter"), C("pitch"), style="metal", hub_slot="iron"),
        {"diameter": control(2.6, 1.2, 4.0), "pitch": control(2.0, 0.8, 4.0)})
    add("nacelle", lambda g: g.nacelle("nacelle", 2.4, 0.45, slot="iron"))

    def engine(g):                                 # nacelle with a metal propeller seated on its front face
        n = g.nacelle("nacelle", 2.4, 0.42, slot="iron")
        p = g.propeller("prop", 2.6, 2.2, style="metal", at=[0, 0, 0.09])
        return g.join("engine", n, p)
    add("engine-car", engine)
    add("nacelle-boxy", lambda g: g.nacelle("nacelle", 2.6, 0.5, style="boxy", slot="steel", detail_slot="iron",
                                            r=[PI / 2, 0, 0]))           # lying down: top up (+z), front to -y

    def boxy_engine(g):                            # boxy riveted car with a metal propeller (Built by Opus 5.5 (medium))
        n = g.nacelle("nacelle", 2.6, 0.5, style="boxy", slot="steel", detail_slot="iron")
        p = g.propeller("prop", 2.8, 2.2, style="metal", at=[0, 0, 0.09])
        g.join("engine-parts", n, p)
        return g.xf("engine", "engine-parts", r=[PI / 2, 0, 0])
    add("engine-car-boxy", boxy_engine)
    add("nacelle-boxy-control", lambda g: g.nacelle("nacelle", C("length"), C("radius"), style="boxy", exhausts=2),
        {"length": control(2.4, 1.5, 4.0), "radius": control(0.45, 0.3, 0.8)})
    return out


# Docs whose parts overlap inside one baked prototype (blades in the hub, pins in the panel): no intersection check.
OVERLAPPING = ("fin-rudder", "tail-elevator", "prop-metal-3", "prop-metal-4-cw", "engine-car", "nacelle-boxy",
               "engine-car-boxy", "nacelle-boxy-control")       # boxy: exhaust stubs sunk into the body


def build(directory):
    kit = Kit(directory, "aero", "aero", bake_leaves=True)
    for name, (controls, g, output) in samples().items():
        kit.module(name, controls, g, output, {}, audit=PIECES.get(name))
    kit.close()
    return Path(directory) / "aero"


def obj_vertices(doc, *sets):
    out = Path(doc).with_suffix(".obj")
    extra = [a for s in sets for a in ("--set", s)]
    r = pgm("export", doc, "--obj", out, "--zup", *extra)
    assert r.get("passed"), r
    return [tuple(map(float, ln.split()[1:4])) for ln in out.read_text().splitlines() if ln.startswith("v ")]


def chord_at(vs, axis, value, tol=1e-4):
    """Largest distance between vertices lying in the plane axis = value (a section loop's LE to TE)."""
    pts = [v for v in vs if abs(v[axis] - value) < tol]
    assert len(pts) >= 6, f"no section at {value}"
    return max(math.dist(a, b) for a in pts for b in pts)


class Airfoil(unittest.TestCase):
    def test_naca_loop(self):
        lp = naca4("0012", 16)
        self.assertEqual(len(lp), 32)
        self.assertAlmostEqual(math.dist(lp[0], (1.0, 0.0)), 0.0)   # closed trailing edge, once
        self.assertAlmostEqual(min(x for x, _ in lp), 0.0)
        self.assertAlmostEqual(max(y for _, y in lp) * 2, 0.12, delta=0.002)
        area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(lp, lp[1:] + lp[:1])) / 2
        self.assertGreater(area, 0)                                # counter-clockwise
        cam = naca4("2412", 16)
        self.assertGreater(max(y for _, y in cam), -min(y for _, y in cam))   # cambered: fuller on top
        piece = naca4("0012", 8, 0.0, 0.7)
        self.assertAlmostEqual(max(x for x, _ in piece), 0.7)
        self.assertEqual(len(piece), 17)


class Aero(unittest.TestCase):
    def doc(self, name):
        return KIT / f"{name}.json"

    def test_check_clean(self):
        for name in samples():
            with self.subTest(name):
                r = pgm("check", self.doc(name))
                self.assertTrue(r.get("passed") and r["clean"], r)
                self.assertEqual(r["openPrototypes"], 0, r)

    def test_single_parts_self_intersection_free(self):
        for name in samples():
            if name in OVERLAPPING:
                continue
            with self.subTest(name):
                r = pgm("check", self.doc(name), "--intersections")
                self.assertEqual(r["selfIntersectingPrototypes"], 0, r)

    def test_one_component(self):
        for name in samples():
            with self.subTest(name):
                r = pgm("audit", self.doc(name))
                self.assertTrue(r["passed"], r)
                self.assertEqual(r["components"], PIECES.get(name, {}).get("components", 1), r)

    def test_wing_chords_and_span(self):
        vs = obj_vertices(self.doc("wing-rect"))
        self.assertAlmostEqual(max(v[1] for v in vs), 5.0, delta=1e-4)
        self.assertAlmostEqual(chord_at(vs, 1, 0.0), 1.5, delta=1e-3)
        self.assertAlmostEqual(chord_at(vs, 1, 5.0), 1.5, delta=1e-3)
        vs = obj_vertices(self.doc("wing-taper"))
        self.assertAlmostEqual(max(v[1] for v in vs), 6.0, delta=1e-4)                # the round tip ends at the span
        self.assertAlmostEqual(chord_at(vs, 1, 0.0), 2.2, delta=1e-3)
        self.assertAlmostEqual(chord_at(vs, 1, 5.4), 1.1, delta=1e-3)                 # tip chord where the cap starts
        le_tip = min(v[0] for v in vs if abs(v[1] - 5.4) < 1e-4)
        self.assertGreater(le_tip, 5.4 * math.tan(0.12) - 0.05)                       # swept aft (twist moves it a little)
        vs = obj_vertices(self.doc("wing-span-control"), "span=8", "chord=2.5")
        self.assertAlmostEqual(max(v[1] for v in vs), 8 * math.cos(0.05), delta=0.02)

    def test_fin_stands_up(self):
        r = pgm("evaluate", self.doc("fin-rudder"))
        lo, hi = r["boundsZUp"]
        self.assertAlmostEqual(hi[2], 2.8, delta=1e-3)
        self.assertGreater(hi[0] - lo[0], 3.0)

    def test_blade_angle_falls_toward_tip(self):
        for name, d, p in (("prop-wood-2", 2.6, 1.9), ("prop-metal-3", 2.6, 2.2)):
            with self.subTest(name):
                vs = obj_vertices(self.doc(name))
                rows = defaultdict(list)
                for v in vs:
                    if v[0] > 0.2 and abs(v[1]) < 0.5:              # blade 0, along +X
                        rows[round(v[0], 5)].append(v)
                angles = []
                for x in sorted(rows):
                    pts = rows[x]
                    if len(pts) < 10:
                        continue
                    a, b = max(((a, b) for a in pts for b in pts), key=lambda ab: math.dist(*ab))
                    ang = math.atan2(abs(a[2] - b[2]), abs(a[1] - b[1]))
                    angles.append((x, ang))
                self.assertGreater(len(angles), 6, angles)
                for (x0, a0), (x1, a1) in zip(angles, angles[1:]):
                    self.assertLess(a1, a0, angles)
                for x, a in angles[:-3]:                            # the helix angle, apart from the tip cap
                    self.assertAlmostEqual(a, math.atan(p / (2 * PI * x)), delta=0.03, msg=(x, a))

    def test_triangle_budget(self):
        for name in samples():
            kind = "prop-metal-3" if name == "prop-metal-3" else "boxy" if name.startswith("nacelle-boxy") else name.split("-")[0]
            if kind not in BUDGET:
                continue
            with self.subTest(name):
                r = pgm("evaluate", self.doc(name))
                self.assertLessEqual(r["placedTriangles"], BUDGET[kind], r)
                print(f"  {name}: {r['placedTriangles']} triangles", file=sys.stderr)

    def test_prop_controls(self):
        lo = pgm("evaluate", self.doc("prop-control"), "--set", "diameter=1.2")
        hi = pgm("evaluate", self.doc("prop-control"), "--set", "diameter=4")
        self.assertAlmostEqual(hi["boundsZUp"][1][0], 2.0, delta=1e-3)
        self.assertAlmostEqual(lo["boundsZUp"][1][0], 0.6, delta=1e-3)

    def test_sweep(self):
        r = pgm("sweep", KIT)
        self.assertTrue(r["passed"], r)
        r = pgm("audit", KIT, "--sweep")
        self.assertFalse(r.get("failures"), r)


def sheet(out):
    panels = [
        ("wing-taper", "wing: 2412-0012, taper, washout", -35, 30),
        ("wing-dihedral", "wing: dihedral, round tip", -20, 12),
        ("fin-rudder", "fin + hinged rudder", -60, 15),
        ("tail-elevator", "tailplane + elevator", -30, 45),
        ("prop-wood-2", "wood, 2 blades", -20, 20),
        ("prop-metal-3", "metal, 3 blades, spinner", -25, 20),
        ("prop-metal-4-cw", "metal, 4 blades, cw", -60, 15),
        ("engine-car", "nacelle + metal propeller", -60, 15),
        ("engine-car-boxy", "boxy riveted nacelle + propeller", -55, 18),
        ("nacelle-boxy", "boxy: grille, exhausts", -150, 12),
    ]
    spec = {"title": "kitlib aero helpers: airfoils, wings and fins, propellers", "width": 2000, "height": 1000,
            "layout": "grid", "columns": 5,
            "panels": [{"document": str(KIT / f"{d}.json"), "caption": c, "yaw": y, "pitch": p} for d, c, y, p in panels]}
    path = KIT / "sheet.json"
    path.write_text(json.dumps(spec, indent=1))
    print(json.dumps(pgm("sheet", path, "--out", out)))


if __name__ == "__main__":
    args = sys.argv[1:]
    out = args[args.index("--sheet") + 1] if "--sheet" in args else None
    keep = args[args.index("--keep") + 1] if "--keep" in args else None
    root = keep or tempfile.mkdtemp(prefix="kitlib-aero-")
    KIT = build(root)
    if out:
        sheet(out)
    unittest.main(argv=[sys.argv[0], "-v"])
