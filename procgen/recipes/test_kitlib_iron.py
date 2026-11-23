"""Tests of the kitlib structural iron helpers. Built by Opus 5.5 (medium), 2026-11-16.

Builds sample documents into a temporary kit and runs pgm on them: every sample closed, manifold and
outward (pgm check); every assembly (truss, girder, built-up member, connection) one component
(pgm audit: nothing floats); member counts as asked; section sizes read back from the bounds; triangle
budgets; controls swept. Control-driven samples (Built by Opus 5.5 (medium)): a shed bay whose span, roof
depth and eave height are module controls driving a pitched roof truss, a plate girder and laced columns;
a fink truss and a gusset on controlled members. Each is one component at both ends of every control.

    PGM=/path/to/pgm python3 procgen/recipes/test_kitlib_iron.py [--sheet /tmp/iron-sheet.png] [--keep DIR]
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitlib import C, Graph, Kit, control, iron_outline, iron_section, mitre, sqrt  # noqa: E402

PGM = os.environ.get("PGM", str(Path(__file__).resolve().parent.parent / "build-release" / "pgm"))
KIT = None
INFO = {}
# Triangle budgets (placed triangles; rivets are detail and cost none).
BUDGET = {"rolled-I200": 44, "rolled-L80x10": 20, "rolled-FL100x10": 12, "truss-pratt": 2000, "truss-howe": 2000,
          "truss-warren": 1600, "truss-lattice": 2600, "truss-bowstring": 2200, "girder": 1300, "girder-arched": 1900,
          "laced-single": 900, "laced-double": 2200, "battened": 500, "strut-2L": 400, "box": 128, "portal": 160,
          "truss-pratt-pitched": 1100, "truss-howe-pitched": 1100, "truss-fink": 500, "shed-bay": 5200,
          "truss-fink-control": 500, "gusset-control": 220}
TRUSSES = {"pratt": (18.0, 2.2, 8), "howe": (18.0, 2.2, 8), "warren": (16.0, 1.8, 6), "lattice": (14.0, 1.6, 7),
           "bowstring": (24.0, 3.6, 8)}
PITCHED = {"pratt-pitched": (18.0, 3.0, 8), "howe-pitched": (18.0, 3.0, 8), "fink": (18.0, 3.0, 4)}
# Module controls of the control-driven samples: span, roof depth (ridge over the tie) and eave height.
SHED = {"span": control(18.0, 12.0, 26.0), "depth": control(3.0, 1.8, 4.5), "eave": control(6.0, 4.0, 8.0)}
CONTROLLED = ("shed-bay", "truss-fink-control", "gusset-control")


def pgm(*args):
    r = subprocess.run([PGM, *map(str, args)], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        raise AssertionError(f"pgm {' '.join(map(str, args))}: {r.stdout[:400]} {r.stderr[:400]}")


def samples():
    """name -> (controls, graph, output)."""
    out = {}

    def add(name, build, controls=None):
        g = Graph()
        out[name] = (controls or {}, g, build(g))

    for sec in ("I200", "H200", "U140", "L80x10", "L100x65x9", "T80", "FL100x10", "RD30"):
        add(f"rolled-{sec}", lambda g, sec=sec: g.rolled("m", sec, [0, 0, 0], [2, 0, 0.5], slot="iron"))
    RC = {"span": control(4.0, 1.0, 9.0), "roll": control(0.0, -0.5, 0.5)}
    add("rolled-control", lambda g: g.rolled("m", "I260", [0, 0, 3], [C("span"), 0, 3], roll=C("roll"), controls=RC), RC)

    def portal(g):                                 # two I posts and a beam, mitred at the knees
        a, b, c, d = [0, 0, 0], [0, 0, 3], [4, 0, 3], [4, 0, 0]
        m1, m2 = mitre((0, 0, 1), (1, 0, 0)), mitre((1, 0, 0), (0, 0, -1))
        y = (0, 1, 0)
        return g.join("portal", g.rolled("p1", "I260", a, b, up=y, ends=(None, m1)), g.rolled("bm", "I260", b, c, up=y, ends=(m1, m2)),
                      g.rolled("p2", "I260", c, d, up=y, ends=(m2, None)))
    add("portal", portal)

    for kind, (span, depth, n) in TRUSSES.items():
        def tr(g, kind=kind, span=span, depth=depth, n=n):
            node, info = g.truss("truss", span, depth, n, kind=kind, slot="iron")
            INFO[f"truss-{kind}"] = info
            return node
        add(f"truss-{kind}", tr)

    for kind, (span, depth, n) in PITCHED.items():
        def trp(g, kind=kind, span=span, depth=depth, n=n):
            node, info = g.truss("truss", span, depth, n, kind=kind, slot="iron")
            INFO[f"truss-{kind}"] = info
            return node
        add(f"truss-{kind}", trp)

    def shed(g):
        """A shed bay driven by its controls: laced columns to the eave, a plate girder on them over the
        span, a pitched Pratt roof truss on the girder (panels from a 2.25 m panel pitch at the default span)."""
        S, Dr, E = C("span"), C("depth"), C("eave")
        gd = (S + 0.6) / 16                                           # girder depth follows the span
        girder, ginfo = g.plate_girder("girder", S + 0.6, gd, bearing_at=0.3, at=[-0.3, 0, E], slot="iron", controls=SHED)
        INFO["shed-girder"] = ginfo
        cols = [g.laced_column(f"col{k}", [x, 0, 0], [x, 0, E - 0.0255], width=0.36, slot="iron", controls=SHED)
                for k, x in enumerate((0, S))]
        roof, info = g.truss("roof", S, Dr, kind="pratt-pitched", panel_pitch=2.25, at=[0, 0, E + gd + 0.039],
                             chord="L90x11", slot="iron", controls=SHED)
        INFO["shed-roof"] = info
        return g.join("bay", girder, roof, *cols)
    add("shed-bay", shed, SHED)

    def fink(g):
        node, info = g.truss("truss", C("span"), C("depth"), kind="fink", slot="iron", controls=SHED)
        INFO["truss-fink-control"] = info
        return node
    add("truss-fink-control", fink, SHED)

    def gusset_control(g):
        """A node of a Pratt panel C("span") / 8 wide and C("depth") deep: chord pair, a gusset sized along the
        controlled diagonal and vertical, and double-angle struts on its faces along the same directions."""
        dx, dz = C("span") / 8, C("depth")
        L = sqrt(dx * dx + dz * dz)
        diag = (dx / L, 0, dz / L)
        parts = [g.rolled(f"ch{s}", "L80x10", [-0.6 * s, s * 0.006, -0.04], [0.6 * s, s * 0.006, -0.04], up=(0, s, 0))
                 for s in (1, -1)]
        gp = g.gusset_plate("gusset", [0, 0, 0], [(diag, 0.06, 0.32), ((0, 0, 1), 0.06, 0.32), ((1, 0, 0), 0.08, 0.4),
                                                  ((-1, 0, 0), 0.08, 0.4)], flush=([0, 0, -0.04], (1, 0, 0)), controls=SHED)
        for k, (d, up) in enumerate(((diag, (0, 0, 1)), ((0, 0, 1), (1, 0, 0)))):
            parts.append(g.laced_column(f"strut{k}", [d[i] * 0.09 for i in range(3)], [d[i] * 0.9 for i in range(3)],
                                        width=0.012, angles=2, angle="L60x8", up=up, controls=SHED))
        return g.join("node", gp, *parts)
    add("gusset-control", gusset_control, SHED)

    add("truss-close", lambda g: g.truss("truss", 3.6, 1.4, 3, kind="pratt", slot="iron")[0])
    add("girder-close", lambda g: g.plate_girder("girder", 3.0, 1.0, stiffener_pitch=0.9, slot="iron")[0])

    def girder(g, **kw):
        node, info = g.plate_girder("girder", 12.0, 1.2, slot="iron", **kw)
        INFO["girder-arched" if kw else "girder"] = info
        return node
    add("girder", girder)
    add("girder-arched", lambda g: girder(g, soffit=0.45, segments=10))

    add("laced-single", lambda g: g.laced_column("col", [0, 0, 0], [0, 0, 3.0], width=0.36, slot="iron"))
    add("laced-double", lambda g: g.laced_column("col", [0, 0, 0], [0, 0, 3.0], width=0.44, depth=0.36, system="double",
                                                 slot="iron"))
    add("battened", lambda g: g.laced_column("col", [0, 0, 0], [0, 0, 3.0], width=0.40, system="batten", slot="iron"))
    add("strut-2L", lambda g: g.laced_column("strut", [0, 0, 0], [3.0, 0, 1.0], width=0.012, angles=2, angle="L70x9",
                                             up=(0, 0, 1), slot="iron"))
    add("box", lambda g: g.box_member("box", [0, 0, 0], [3, 0, 0], width=0.4, depth=0.5, slot="iron"))
    BC = {"length": control(3.0, 1.0, 8.0), "width": control(0.4, 0.25, 0.6)}
    add("box-control", lambda g: g.box_member("box", [0, 0, 0], [C("length"), 0, 0], width=C("width"), depth=0.5,
                                              controls=BC), BC)

    def node(g):                                   # a gusset on a pair of angles, with two struts
        parts = [g.rolled(f"ch{s}", "L80x10", [-0.6 * s, s * 0.006, -0.04], [0.6 * s, s * 0.006, -0.04], up=(0, s, 0))
                 for s in (1, -1)]                 # heels on the heel line, legs up (u = up x axis = +z)
        gp = g.gusset_plate("gusset", [0, 0, 0], [((1, 0, 1), 0.06, 0.32), ((-1, 0, 1), 0.06, 0.32), ((1, 0, 0), 0.08, 0.4),
                                                 ((-1, 0, 0), 0.08, 0.4)], flush=([0, 0, -0.04], (1, 0, 0)))
        return g.join("node", gp, *parts)
    add("gusset", node)

    def splice(g):
        m = g.rolled("bar", "FL200x16", [0, 0, 0], [2, 0, 0])
        sp = g.splice_plate("splice", [1.0, 0, 0.008], (1, 0, 0), (0, 0, 1), length=0.5, width=0.2)
        return g.join("spliced", m, sp)
    add("splice", splice)

    def base(g):
        b = g.base_plate("base", [0, 0, 0], (0.6, 0.6), bolts=4)
        col = g.rolled("col", "H200", [0, 0, 0.029], [0, 0, 2.0], up=(1, 0, 0))
        return g.join("footing", b, col)
    add("base", base)
    return out


# Assemblies that must hold together as one piece.
ASSEMBLIES = ("portal", "truss-", "girder", "laced-", "battened", "strut-", "box", "gusset", "splice", "base", "shed-")


def build(directory):
    kit = Kit(directory, "iron", "iron", bake_leaves=True)
    for name, (controls, g, output) in samples().items():
        kit.module(name, controls, g, output, {})
    kit.close()
    return Path(directory) / "iron"


class Sections(unittest.TestCase):
    def test_table_and_names(self):
        self.assertEqual(iron_section("L80x10"), dict(kind="L", a=0.08, b=0.08, t=0.01))
        self.assertEqual(iron_section("L100x65x9")["b"], 0.065)
        self.assertEqual(iron_section("FL60x10"), dict(kind="FL", b=0.06, t=0.01))
        self.assertEqual(iron_section("RD30")["d"], 0.03)
        self.assertEqual(len(iron_outline("I200")), 12)
        for name in ("I200", "U140", "L80x10", "T80", "FL100x10", "RD30"):
            lp = iron_outline(name)
            area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(lp, lp[1:] + lp[:1])) / 2
            self.assertGreater(area, 0, name)                       # counter-clockwise
        with self.assertRaises(AssertionError):
            iron_section("Z99")


class Iron(unittest.TestCase):
    def doc(self, name):
        return KIT / f"{name}.json"

    def test_check_clean(self):
        for name in samples():
            with self.subTest(name):
                r = pgm("check", self.doc(name))
                self.assertTrue(r.get("passed") and r["clean"], r)
                self.assertEqual(r["openPrototypes"], 0, r)

    def test_assemblies_one_component(self):
        for name in samples():
            if not name.startswith(ASSEMBLIES):
                continue
            with self.subTest(name):
                r = pgm("audit", self.doc(name))
                self.assertTrue(r["passed"], r)
                self.assertEqual(r["components"], 1, r)

    def test_section_sizes(self):
        lo, hi = pgm("evaluate", self.doc("rolled-I200"))["boundsZUp"]
        # a 2 m by 0.5 m rise member; across (y) the bounds are the flange width
        self.assertAlmostEqual(hi[1] - lo[1], 0.090, delta=1e-4)
        lo, hi = pgm("evaluate", self.doc("rolled-control"), "--set", "span=7")["boundsZUp"]
        self.assertAlmostEqual(hi[0] - lo[0], 7.0, delta=1e-4)
        self.assertAlmostEqual(hi[2] - lo[2], 0.26, delta=1e-4)

    def test_truss_members(self):
        for kind, (span, depth, n) in TRUSSES.items():
            with self.subTest(kind):
                m = INFO[f"truss-{kind}"]["members"]
                web = {"pratt": 2 * n + 1, "howe": 2 * n + 1, "warren": 2 * n, "lattice": 2 * n + 2,
                       "bowstring": (n - 1) + (n - 2)}[kind]
                nodes = {"warren": 2 * n + 1, "bowstring": 2 * n}.get(kind, 2 * (n + 1))
                self.assertEqual(m["web"], web, m)
                self.assertEqual(m["gussets"], nodes, m)
                self.assertEqual(m["chord"], n + 1 if kind == "bowstring" else 2, m)
                self.assertEqual(m["fillers"], 2 * n if kind == "lattice" else 0, m)
                info = INFO[f"truss-{kind}"]
                self.assertEqual(len(info["bottom"]), n + 1)
                lo, hi = pgm("evaluate", self.doc(f"truss-{kind}"))["boundsZUp"]
                self.assertAlmostEqual(hi[2], depth + 0.04, delta=0.01)            # half an L80 leg over the working line
                self.assertGreater(hi[0] - lo[0], span)

    def test_girder(self):
        self.assertEqual(INFO["girder"]["stiffeners"], 9)
        lo, hi = pgm("evaluate", self.doc("girder"))["boundsZUp"]
        self.assertAlmostEqual(hi[2], 1.2, delta=1e-4)
        self.assertAlmostEqual(hi[0] - lo[0], 12.0, delta=1e-3)
        r = pgm("evaluate", self.doc("girder-arched"))
        self.assertAlmostEqual(r["boundsZUp"][1][2], 1.2, delta=1e-4)

    def test_pitched_trusses(self):
        for kind, (span, depth, n) in PITCHED.items():
            with self.subTest(kind):
                m = INFO[f"truss-{kind}"]["members"]
                web = 4 if kind == "fink" else (n - 1) + (n - 2)        # verticals and diagonals
                self.assertEqual(m["web"], web, m)
                self.assertEqual(m["chord"], 3, m)                       # tie and two rafters
                self.assertEqual(m["gussets"], 7 if kind == "fink" else 2 * n, m)
                lo, hi = pgm("evaluate", self.doc(f"truss-{kind}"))["boundsZUp"]
                self.assertGreater(hi[2], depth)                         # rafter heels over the ridge
                self.assertLess(hi[2], depth + 0.12)
                self.assertGreater(hi[0] - lo[0], span)

    def test_controls(self):
        """The control-driven samples: checked and one piece at both ends of every control, and their
        bounds follow the controls."""
        for name in CONTROLLED:
            with self.subTest(name):
                r = pgm("sweep", self.doc(name))
                self.assertTrue(r["passed"] and r["cases"] >= 7, r)
                r = pgm("audit", self.doc(name), "--sweep")
                self.assertTrue(r["passed"] and not r["failures"], r)
        self.assertEqual(INFO["shed-roof"]["members"]["web"], 13)          # 8 panels from 18 m / 2.25 m
        self.assertEqual(INFO["shed-girder"]["stiffeners"], 14)
        for span, depth, eave in ((12.0, 1.8, 4.0), (26.0, 4.5, 8.0), (18.0, 3.0, 6.0)):
            lo, hi = pgm("evaluate", self.doc("shed-bay"), "--set", f"span={span}", "--set", f"depth={depth}",
                         "--set", f"eave={eave}")["boundsZUp"]
            self.assertAlmostEqual(hi[0] - lo[0], span + 0.6, delta=0.12)
            top = eave + (span + 0.6) / 16 + depth
            self.assertGreater(hi[2], top)
            self.assertLess(hi[2], top + 0.2)
            self.assertAlmostEqual(lo[2], -0.0, delta=1e-3)
            r = pgm("audit", self.doc("shed-bay"), "--set", f"span={span}", "--set", f"depth={depth}", "--set", f"eave={eave}")
            self.assertEqual(r["components"], 1, r)                       # all controls at once, too
        with self.assertRaises(AssertionError):                           # a control without its default
            Graph().truss("t", C("span"), 2.0, 4)

    def test_rivets_are_detail(self):
        r = pgm("inspect", self.doc("truss-pratt"))
        self.assertIn("fastener", json.dumps(r))

    def test_triangle_budget(self):
        for name in samples():
            if name not in BUDGET:
                continue
            with self.subTest(name):
                r = pgm("evaluate", self.doc(name))
                self.assertLessEqual(r["placedTriangles"], BUDGET[name], r)
                print(f"  {name}: {r['placedTriangles']} triangles", file=sys.stderr)

    def test_sweep(self):
        r = pgm("sweep", KIT)
        self.assertTrue(r["passed"], r)
        r = pgm("audit", KIT, "--sweep")
        failures = [f for f in r.get("failures", []) if str(f).find("rolled-") < 0]
        self.assertFalse(failures, r)


def sheet(out):
    panels = [
        ("truss-pratt", "Pratt truss, 18 m, 8 panels", -70, 10),
        ("truss-close", "Pratt close-up: paired angles, gussets in the gap, rivets", -70, 10),
        ("truss-lattice", "lattice girder (double intersection), flats packed at the crossings", -70, 10),
        ("truss-bowstring", "bowstring, 24 m, parabolic", -70, 10),
        ("truss-pratt-pitched", "pitched Pratt roof truss, 18 m, 8 panels", -70, 10),
        ("truss-fink", "fink (W) roof truss", -70, 10),
        ("shed-bay", "shed bay on controls: columns, plate girder, pitched truss", -60, 12),
        ("truss-warren", "Warren truss", -70, 10),
        ("girder-close", "plate girder: covers, flange angles, stiffeners on fillers", -60, 15),
        ("girder-arched", "arched spandrel plate girder, 12 m", -70, 8),
        ("laced-double", "four-angle column, double lacing, tie plates", -40, 15),
        ("gusset", "gusset plate on a chord node", -60, 12),
        ("base", "base plate and H column", -40, 25),
    ]
    spec = {"title": "kitlib iron: rolled sections, built-up members, riveted trusses, plate girders", "width": 2000,
            "height": 3000, "layout": "grid", "columns": 2,
            "panels": [{"document": str(KIT / f"{d}.json"), "caption": c, "yaw": y, "pitch": p} for d, c, y, p in panels]}
    path = KIT / "sheet.json"
    path.write_text(json.dumps(spec, indent=1))
    print(json.dumps(pgm("sheet", path, "--out", out)))


if __name__ == "__main__":
    args = sys.argv[1:]
    out = args[args.index("--sheet") + 1] if "--sheet" in args else None
    keep = args[args.index("--keep") + 1] if "--keep" in args else None
    root = keep or tempfile.mkdtemp(prefix="kitlib-iron-")
    KIT = build(root)
    if out:
        sheet(out)
    unittest.main(argv=[sys.argv[0], "-v"])
