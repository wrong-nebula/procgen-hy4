"""Tests of the kitlib pipework helpers: route, pipe_run, tee, reducer, valve, supports. Built by Opus 5.5 (medium),
2026-11-16.

Builds sample documents into a temporary kit and runs pgm on them: every sample closed, manifold and outward
(pgm check), bare runs free of self-intersections (check --intersections), one component per sample (pgm audit;
the pump-hall assembly with fittings, valves and supports included), bends at the radius asked (the barrel's
vertices on the torus, read back from an OBJ export), router legs axis-aligned and long enough, triangle budgets,
controls swept.

    PGM=/path/to/pgm python3 procgen/recipes/test_kitlib_pipes.py [--sheet /tmp/pipes-sheet.png] [--keep DIR]
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
from kitlib import C, Graph, Kit, control, flange_dims, route  # noqa: E402

PGM = os.environ.get("PGM", str(Path(__file__).resolve().parent.parent / "build-release" / "pgm"))
KIT = None
# Placed-triangle ceilings per sample, about 10 % over the measured counts (0.3 m pipe, 16 sides): a flanged joint
# with its nuts costs 1,216, so joints dominate; bolts="detail" halves that for runs out of reach.
BUDGET = {"bend-90": 500, "bend-bare": 1100, "bend-ends": 1400, "bend-x": 1400, "flanged-run": 11000,
          "flanged-detail": 6500, "bell-run": 5000, "riveted-run": 1600, "lagged-run": 1100, "tee": 11000, "cross-blind": 7500,
          "reducer": 6600, "gate-valve": 7000, "globe-valve": 7600, "check-valve": 6000, "stools": 4800, "hangers": 5600,
          "wall-brackets": 4800, "wall-riser": 4300, "pump-hall": 37000}
BEND = {"points": [[0, 0, 0], [2, 0, 0], [2, 2, 0]], "D": 0.3, "R": 0.6}


def pgm(*args):
    r = subprocess.run([PGM, *map(str, args)], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        raise AssertionError(f"pgm {' '.join(map(str, args))}: {r.stdout[:400]} {r.stderr[:400]}")


THREE_BENDS = [[0, 0, 0.6], [3, 0, 0.6], [3, 2.5, 0.6], [3, 2.5, 2.6], [5.5, 2.5, 2.6]]


def straight(L=4.0, z=1.0):
    return [[0, 0, z], [L, 0, z]]


def samples():
    """name -> (controls, graph, output)."""
    out = {}

    def add(name, build, controls=None):
        g = Graph()
        out[name] = (controls or {}, g, build(g))

    add("bend-90", lambda g: g.pipe_run("p", BEND["points"], BEND["D"], bend_radius=BEND["R"], joints=None, ends="plain",
                                        slot="iron"))
    add("bend-bare", lambda g: g.pipe_run("p", THREE_BENDS, 0.3, joints=None, ends="plain", slot="iron"))
    add("bend-ends", lambda g: g.pipe_run("p", THREE_BENDS, 0.3, joints=None, ends=("open", "cap"), slot="iron"))
    add("bend-x", lambda g: g.pipe_run("p", THREE_BENDS, C("diameter"), bend_radius=0.5, joints=None, ends=("plain", "cap"),
                                       slot="iron"), {"diameter": control(0.3, 0.1, 0.5)})
    add("flanged-run", lambda g: g.pipe_run("p", THREE_BENDS, 0.3, joints="flange", ends=("flange", "blind"), slot="patina",
                                            fitting_slot="iron"))
    add("flanged-detail", lambda g: g.pipe_run("p", THREE_BENDS, 0.3, joints="flange", bolts="detail", slot="iron"))
    add("bell-run", lambda g: g.pipe_run("p", THREE_BENDS, 0.2, joints="bell", pipe_length=2.0, ends=("plain", "open"),
                                         slot="iron"))
    add("riveted-run", lambda g: g.pipe_run("p", THREE_BENDS, 0.5, bend_radius=1.0, joints="riveted", pipe_length=1.5,
                                            ends="cap", slot="steel"))
    add("lagged-run", lambda g: g.pipe_run("p", THREE_BENDS, 0.2, joints=None, lagging=0.04, bands=0.6, ends="plain",
                                           slot="iron"))

    def tee(g, cross=False):
        run = g.pipe_run("main", straight(4.0), 0.3, joints=None, ends="flange", slot="iron")
        t = g.tee("tee", run, 0.5, branch=[0, 0, 1], cross=cross, end="blind" if cross else "flange", slot="iron")
        parts = [run, t]
        if not cross:
            parts.append(g.pipe_run("riser", [t.ports[0]["point"], [2, 0, 3.0], [3.2, 0, 3.0]], 0.3, joints="flange",
                                    ends=("flange", "blind"), slot="iron"))
        return g.join("all", *parts)
    add("tee", tee)
    add("cross-blind", lambda g: tee(g, cross=True))

    def reducer(g):
        run = g.pipe_run("in", straight(2.0), 0.3, joints=None, ends="flange", slot="iron")
        red = g.reducer("red", run, 0.2, slot="iron")
        on = g.pipe_run("on", [red.ports[1]["point"], [4.0, 0, 1.0], [4.0, 0, 2.5]], 0.2, ends=("flange", "open"), slot="iron")
        return g.join("all", run, red, on)
    add("reducer", reducer)

    def valve(g, kind, **kw):
        run = g.pipe_run("main", straight(2.4), 0.3, joints=None, ends="flange", slot="iron")
        v = g.valve("valve", run, 0.5, kind=kind, slot="iron", wheel_slot="bronze", **kw)
        return g.join("all", run, v)
    add("gate-valve", lambda g: valve(g, "gate", rising=True))
    add("globe-valve", lambda g: valve(g, "globe", spokes=6))
    add("check-valve", lambda g: valve(g, "check"))

    def sup(g, kind, pts, plane):
        run = g.pipe_run("main", pts, 0.3, joints="flange", ends="flange", slot="iron")
        s = g.supports("sup", run, kind=kind, plane=plane, spacing=2.0, slot="iron")
        return g.join("all", run, s)
    add("stools", lambda g: sup(g, "stool", straight(7.0, 0.8), 0.0))
    add("hangers", lambda g: sup(g, "hanger", straight(7.0, 2.4), 3.5))
    add("wall-brackets", lambda g: sup(g, "wall", [[0, 0.4, 2.0], [7.0, 0.4, 2.0]], ([0, 0, 0], [0, 1, 0])))
    add("wall-riser", lambda g: sup(g, "wall", [[1, 0.35, 0.5], [1, 0.35, 5.5]], ([0, 0, 0], [0, 1, 0])))

    def pump_hall(g):
        """A pump outlet rising, along the wall at 3.2 m, down to a header: flanged main with a gate valve, a tee
        and its branch with a check valve, hangers and wall brackets."""
        pts = route([0, 0, 0.6], [7.0, 1.4, 0.8], order="zyx", start_dir=[0, 0, 1], end_dir=[0, 0, -1],
                    wall=([0, 3, 0], [0, -1, 0]), standoff=0.45, height=3.2, bend_radius=0.45)
        run = g.pipe_run("main", pts, 0.3, bend_radius=0.45, joints="flange", ends=("flange", "blind"), slot="patina",
                         fitting_slot="iron")
        gv = g.valve("gate", run, [0, 0, 1.7], kind="gate", stem=[1, 0, 0], slot="iron", wheel_slot="bronze")
        t = g.tee("tee", run, [2.2, 2.55, 3.2], branch=[0, -1, 0], diameter=0.2, slot="iron")
        br = g.pipe_run("branch", [t.ports[0]["point"], [2.2, 0.4, 3.2], [2.2, 0.4, 0.6]], 0.2, joints="flange",
                        ends=("flange", "blind"), slot="patina", fitting_slot="iron")
        cv = g.valve("check", br, 0.75, kind="check", stem=[1, 0, 0], slot="iron")
        hang = g.supports("hangers", run, kind="hanger", plane=4.2, spacing=1.6, slot="iron")
        wall = g.supports("brackets", br, kind="wall", plane=([1.7, 0.0, 0], [1, 0, 0]), spacing=1.0, slot="iron")
        return g.join("all", run, gv, t, br, cv, hang, wall)
    add("pump-hall", pump_hall)
    return out


# Samples whose parts overlap inside one baked prototype (flanges round the barrel...): no intersection check.
BARE = ("bend-90", "bend-bare", "bend-ends", "bend-x", "riveted-run", "lagged-run")


def build(directory):
    kit = Kit(directory, "pipes", "pipes", bake_leaves=True)
    for name, (controls, g, output) in samples().items():
        kit.module(name, controls, g, output, {})
    kit.close()
    return Path(directory) / "pipes"


def obj_vertices(doc, *sets):
    out = Path(doc).with_suffix(".obj")
    extra = [a for s in sets for a in ("--set", s)]
    r = pgm("export", doc, "--obj", out, "--zup", *extra)
    assert r.get("passed"), r
    return [tuple(map(float, ln.split()[1:4])) for ln in out.read_text().splitlines() if ln.startswith("v ")]


class Route(unittest.TestCase):
    def test_manhattan(self):
        pts = route([0, 0, 0.6], [7.0, 1.4, 0.8], start_dir=[0, 0, 1], end_dir=[0, 0, -1], wall=([0, 3, 0], [0, -1, 0]),
                    standoff=0.45, height=3.2, bend_radius=0.45)
        self.assertEqual(pts[0], [0, 0, 0.6])
        self.assertEqual(pts[-1], [7.0, 1.4, 0.8])
        for a, b in zip(pts, pts[1:]):
            moved = [i for i in range(3) if abs(a[i] - b[i]) > 1e-9]
            self.assertEqual(len(moved), 1, (a, b))                  # one axis per leg
        self.assertTrue(any(abs(p[1] - 2.55) < 1e-9 and abs(p[2] - 3.2) < 1e-9 for p in pts), pts)   # along the wall
        self.assertGreater(pts[1][2], pts[0][2])                     # leaves upward
        self.assertLess(pts[-1][2], pts[-2][2])                      # arrives downward

    def test_tight_route_refused(self):
        with self.assertRaises(ValueError):
            route([0, 0, 0], [3, 0.3, 0], start_dir=[1, 0, 0], bend_radius=0.45)

    def test_tight_leg_refused(self):
        with self.assertRaises(ValueError):
            Graph().pipe_run("p", [[0, 0, 0], [1, 0, 0], [1, 0.5, 0], [2, 0.5, 0]], 0.3, bend_radius=0.45)

    def test_flange_rules(self):
        f = flange_dims(0.32)
        self.assertEqual(f["bolts"], 12)
        self.assertAlmostEqual(f["radius"] * 2, 0.484, delta=0.01)


class Pipes(unittest.TestCase):
    def doc(self, name):
        return KIT / f"{name}.json"

    def test_check_clean(self):
        for name in samples():
            with self.subTest(name):
                r = pgm("check", self.doc(name))
                self.assertTrue(r.get("passed") and r["clean"], r)
                self.assertEqual(r["openPrototypes"], 0, r)

    def test_bare_runs_self_intersection_free(self):
        for name in BARE:
            with self.subTest(name):
                r = pgm("check", self.doc(name), "--intersections")
                self.assertEqual(r["selfIntersectingPrototypes"], 0, r)

    def test_one_component(self):
        for name in samples():
            with self.subTest(name):
                r = pgm("audit", self.doc(name))
                self.assertTrue(r["passed"], r)
                self.assertEqual(r["components"], 1, r)

    def test_bend_radius(self):
        """Every barrel vertex in the bend quadrant lies on the torus of centre-line radius R and tube radius r."""
        R, r = BEND["R"], BEND["D"] / 2
        cx, cy = 2 - R, R
        vs = [v for v in obj_vertices(self.doc("bend-90")) if v[0] > cx + 1e-4 and v[1] < cy - 1e-4]
        self.assertGreater(len(vs), 50)
        for x, y, z in vs:
            d = math.hypot(x - cx, y - cy)
            self.assertAlmostEqual(math.hypot(d - R, z), r, delta=2e-4, msg=(x, y, z))
        ds = [math.hypot(x - cx, y - cy) for x, y, z in vs]
        self.assertAlmostEqual(min(ds), R - r * math.cos(math.pi / 16), delta=2e-3)
        self.assertAlmostEqual(max(ds), R + r * math.cos(math.pi / 16), delta=2e-3)

    def test_run_attributes(self):
        g = Graph()
        run = g.pipe_run("p", THREE_BENDS, 0.3, joints="flange")
        self.assertEqual(len(run.bends), 3)
        for b in run.bends:
            self.assertAlmostEqual(b["angle"], math.pi / 2, places=6)
            for s in (b["s0"], (b["s0"] + b["s1"]) / 2, b["s1"]):
                self.assertAlmostEqual(math.dist(run.at(s)[0], b["centre"]), 0.45, places=6)
        kinds = [k for _, k in run.joints]
        self.assertEqual(kinds.count("bend-start"), 3)
        self.assertEqual(kinds.count("bend-end"), 3)
        self.assertAlmostEqual(math.dist(run.at(run.length)[0], THREE_BENDS[-1]), 0.0, places=6)

    def test_valve_placement_refused_on_bend(self):
        g = Graph()
        run = g.pipe_run("p", BEND["points"], 0.3, bend_radius=0.6, joints=None)
        with self.assertRaises(ValueError):
            g.valve("v", run, 0.5)

    def test_triangle_budget(self):
        for name in samples():
            with self.subTest(name):
                r = pgm("evaluate", self.doc(name))
                self.assertLessEqual(r["placedTriangles"], BUDGET[name], r)
                print(f"  {name}: {r['placedTriangles']} triangles", file=sys.stderr)

    def test_x_diameter(self):
        lo = pgm("evaluate", self.doc("bend-x"), "--set", "diameter=0.1")
        hi = pgm("evaluate", self.doc("bend-x"), "--set", "diameter=0.5")
        self.assertAlmostEqual(hi["boundsZUp"][0][2], 0.6 - 0.25 * math.cos(math.pi / 16), delta=2e-3)
        self.assertAlmostEqual(lo["boundsZUp"][0][2], 0.6 - 0.05 * math.cos(math.pi / 16), delta=2e-3)

    def test_sweep(self):
        r = pgm("sweep", KIT)
        self.assertTrue(r["passed"], r)
        r = pgm("audit", KIT, "--sweep")
        self.assertFalse(r.get("failures"), r)


def sheet(out):
    panels = [
        ("bend-ends", "bends R 1.5 D: open end, socket cap", -35, 25),
        ("flanged-run", "flanged cast iron, blind end", -35, 25),
        ("bell-run", "bell and spigot", -35, 25),
        ("riveted-run", "riveted steel, lap seams", -35, 25),
        ("lagged-run", "lagged, strap bands", -35, 25),
        ("tee", "flanged tee and riser", -30, 20),
        ("reducer", "reducer 300 to 200", -30, 20),
        ("gate-valve", "gate valve, rising stem", -30, 20),
        ("globe-valve", "globe valve", -30, 20),
        ("check-valve", "swing check valve", -30, 20),
        ("stools", "floor stools", -30, 15),
        ("hangers", "rod hangers", -30, 15),
        ("wall-brackets", "wall brackets", -150, 15),
        ("wall-riser", "riser clipped to a wall", -150, 15),
        ("pump-hall", "pump hall: route, valves, tee, supports", -40, 25),
        ("cross-blind", "cross, blind flanges", -30, 20),
    ]
    spec = {"title": "kitlib pipework: runs, fittings, valves, supports", "width": 2400, "height": 2000,
            "layout": "grid", "columns": 4,
            "panels": [{"document": str(KIT / f"{d}.json"), "caption": c, "yaw": y, "pitch": p} for d, c, y, p in panels]}
    path = KIT / "sheet.json"
    path.write_text(json.dumps(spec, indent=1))
    print(json.dumps(pgm("sheet", path, "--out", out)))


if __name__ == "__main__":
    args = sys.argv[1:]
    out = args[args.index("--sheet") + 1] if "--sheet" in args else None
    keep = args[args.index("--keep") + 1] if "--keep" in args else None
    root = keep or tempfile.mkdtemp(prefix="kitlib-pipes-")
    KIT = build(root)
    if out:
        sheet(out)
    unittest.main(argv=[sys.argv[0], "-v"])
