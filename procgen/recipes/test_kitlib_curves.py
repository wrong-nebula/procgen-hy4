"""Geometry unit tests of kitlib_curves. Built by Opus 5.5 (medium), 2026-11-16.

Offsets keep their distance, fillets are tangent, tangent lines touch both circles, boolean areas match
analytic cases (holes, touching and coincident edges), intersections of lines and circles are exact,
triangulations cover their polygons, and plates are closed and outward (pgm check).

    PGM=/path/to/pgm python3 procgen/recipes/test_kitlib_curves.py
"""
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitlib_curves as K  # noqa: E402
from kitlib import Graph, Kit  # noqa: E402
from kitlib_curves import Arc, Seg  # noqa: E402

PGM = os.environ.get("PGM", str(Path(__file__).resolve().parent.parent / "build-release" / "pgm"))


def seg_dist(p, a, b):
    d = K._sub(b, a)
    t = max(0.0, min(1.0, K._dot(K._sub(p, a), d) / K._dot(d, d)))
    return K._dist(p, K._add(a, K._mul(d, t)))


def ring_dist(p, ring):
    return min(seg_dist(p, ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring)))


def boundary(shapes):
    return [r for o, hs in shapes for r in [o] + hs]


L_SHAPE = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]


class Offsets(unittest.TestCase):
    def test_square_mitre_round_bevel(self):
        sq = K.rect(0, 0, 1, 1)
        self.assertAlmostEqual(K.shapes_area(K.offset(sq, 0.1, join="mitre")), 1.44, 12)
        self.assertAlmostEqual(K.shapes_area(K.offset(sq, 0.1, join="bevel")), 1.44 - 4 * 0.005, 12)
        r = K.shapes_area(K.offset(sq, 0.1, join="round", tol=1e-5))
        self.assertAlmostEqual(r, 1 + 0.4 + math.pi * 0.01, 4)
        self.assertAlmostEqual(K.shapes_area(K.offset(sq, -0.2)), 0.36, 12)
        self.assertEqual(K.offset(sq, -0.6), [])                      # collapses

    def test_mitre_limit(self):
        spike = [(0, 0), (1, 0), (0, 0.1)]                            # 5.7 degree tip: mitre far past limit
        lim = K.offset(spike, 0.05, join="mitre", mitre_limit=2.0)
        far = K.offset(spike, 0.05, join="mitre", mitre_limit=100.0)
        self.assertLess(max(p[0] for p in lim[0][0]), 1.2)
        self.assertGreater(max(p[0] for p in far[0][0]), 1.5)

    def test_offset_keeps_distance(self):
        for d in (0.1, -0.1, -0.3):
            for o, hs in K.offset(L_SHAPE, d, join="round", tol=1e-4):
                for p in o:
                    self.assertAlmostEqual(ring_dist(p, L_SHAPE), abs(d), delta=2e-4)

    def test_inward_offset_splits_and_cleans(self):
        dumbbell = [(0, 0), (1, 0), (1, 0.45), (2, 0.45), (2, 0), (3, 0), (3, 1), (2, 1), (2, 0.55), (1, 0.55), (1, 1), (0, 1)]
        s = K.offset(dumbbell, -0.1)
        self.assertEqual(len(s), 2)                                   # the neck pinches off
        self.assertAlmostEqual(K.shapes_area(s), 2 * 0.8 * 0.8, 12)
        for o, _ in s:                                                # no self-crossings left
            self.assertEqual(K.intersections(o, closed_a=True), [])

    def test_open_parallel(self):
        line = [(0, 0), (1, 0), (1, 1)]
        par = K.offset(line, 0.1, closed=False)                       # left side: inside the corner, trimmed
        self.assertEqual(K.intersections(par), [])
        for p in par:
            self.assertGreaterEqual(min(seg_dist(p, *e) for e in [(line[0], line[1]), (line[1], line[2])]), 0.1 - 1e-9)

    def test_stroke_and_band(self):
        s = K.stroke([(0, 0), (2, 0)], 0.2)
        self.assertAlmostEqual(K.shapes_area(s), 0.4, 12)
        s = K.stroke([(0, 0), (2, 0)], 0.2, cap="round", tol=1e-5)
        self.assertAlmostEqual(K.shapes_area(s), 0.4 + math.pi * 0.01, 4)
        ring = K.stroke([Arc((0, 0), 1.0, 0, 2 * math.pi)], 0.1, tol=1e-5)
        self.assertEqual(len(ring), 1)
        self.assertEqual(len(ring[0][1]), 1)
        self.assertAlmostEqual(K.shapes_area(ring), math.pi * (1.05 ** 2 - 0.95 ** 2), 4)
        x = K.stroke([(0, 0), (1, 1), (1, 0), (0, 1)], 0.05)         # self-crossing bar: union, no holes lost
        self.assertEqual(len(x), 1)

    def test_offset_path_exact(self):
        arch = [Arc((1, 0), 2, math.pi, -math.pi / 3), Arc((-1, 0), 2, math.pi / 3, -math.pi / 3)]
        inset = K.offset_path(arch, 0.1)                              # left of a clockwise arch: outward
        self.assertAlmostEqual(inset[0].r, 2.1, 12)
        self.assertAlmostEqual(K._dist(inset[0].end, inset[1].start), 0.0, 9)
        self.assertAlmostEqual(inset[0].end[0], 0.0, 9)               # the mitre meets on the axis
        sq = [Seg((0, 0), (1, 0)), Seg((1, 0), (1, 1)), Seg((1, 1), (0, 1)), Seg((0, 1), (0, 0))]
        grown = K.offset_path(sq, -0.1)                              # right of counter-clockwise: grown
        self.assertAlmostEqual(K.area(K.sample(grown)), 1.44, 12)


class Corners(unittest.TestCase):
    def test_fillet_tangent(self):
        path = K.fillet(L_SHAPE, 0.2)
        arcs = [s for s in path if isinstance(s, Arc)]
        self.assertEqual(len(arcs), 6)
        for a, b in zip(path, path[1:] + path[:1]):                   # continuous and tangent everywhere
            self.assertAlmostEqual(K._dist(a.end, b.start), 0.0, 9)
            self.assertAlmostEqual(K._dot(a.tangent(1.0), b.tangent(0.0)), 1.0, 9)
        self.assertAlmostEqual(arcs[0].r, 0.2, 12)

    def test_fillet_area_and_fit(self):
        sq = K.rect(0, 0, 1, 1)
        a = K.area(K.sample(K.fillet(sq, 0.25), tol=1e-6))
        self.assertAlmostEqual(a, 1 - (4 - math.pi) * 0.0625, 5)
        big = K.fillet(sq, 5.0)                                       # reduced to fit: a circle of radius 0.5
        self.assertAlmostEqual(min(s.r for s in big if isinstance(s, Arc)), 0.5, 9)
        some = K.fillet(sq, 0.2, at=[0, 2])
        self.assertEqual(sum(isinstance(s, Arc) for s in some), 2)

    def test_tension_and_chamfer(self):
        t = K.fillet(K.rect(0, 0, 1, 1), 0.2, tension=0.55)
        self.assertAlmostEqual(K.area(t), 1 - (4 - math.pi) * 0.04, 2)
        c = K.chamfer(K.rect(0, 0, 1, 1), 0.1)
        self.assertEqual(len(c), 8)
        self.assertAlmostEqual(K.area(c), 1 - 4 * 0.005, 12)


class Tangents(unittest.TestCase):
    def test_circle_3p_and_through(self):
        c, r = K.circle_3p((1, 0), (0, 1), (-1, 0))
        self.assertAlmostEqual(c[0], 0, 12)
        self.assertAlmostEqual(r, 1, 12)
        a = Arc.through((1, 0), (0, 1), (-1, 0))
        self.assertAlmostEqual(a.sweep, math.pi, 12)
        a = Arc.through((-1, 0), (0, 1), (1, 0))
        self.assertAlmostEqual(a.sweep, -math.pi, 12)

    def test_fillet_arc(self):
        arc, t1, t2 = K.fillet_arc((0, 5), (0, 0), (5, 0), 1.0)
        self.assertAlmostEqual(K._dist(arc.c, (1, 1)), 0, 12)
        self.assertAlmostEqual(K._dist(arc.start, t1), 0, 12)
        self.assertAlmostEqual(K._dist(arc.end, t2), 0, 12)

    def test_circles_tangent_to_lines(self):
        cs = K.circles_tangent_to_lines((0, 0), (1, 0), (0, 0), (1, 1), 0.3)
        self.assertEqual(len(cs), 4)
        for c in cs:
            self.assertAlmostEqual(abs(c[1]), 0.3, 12)
            self.assertAlmostEqual(abs(c[0] - c[1]) / math.sqrt(2), 0.3, 12)

    def test_common_tangents_touch_both(self):
        random.seed(3)
        for _ in range(50):
            c1, c2 = (random.uniform(-1, 1), random.uniform(-1, 1)), (random.uniform(2, 4), random.uniform(-1, 1))
            r1, r2 = random.uniform(0.1, 0.9), random.uniform(0.1, 0.9)
            t = K.circle_tangent_lines(c1, r1, c2, r2)
            self.assertEqual((len(t["outer"]), len(t["inner"])), (2, 2))
            for kind, lines in t.items():
                for p, q in lines:
                    self.assertAlmostEqual(K._dist(p, c1), r1, 10)
                    self.assertAlmostEqual(K._dist(q, c2), r2, 10)
                    d = K._unit(K._sub(q, p))                         # the line is square to both radii
                    self.assertAlmostEqual(K._dot(d, K._sub(p, c1)), 0, 10)
                    self.assertAlmostEqual(K._dot(d, K._sub(q, c2)), 0, 10)
                    s1, s2 = K._crs(d, K._sub(c1, p)), K._crs(d, K._sub(c2, p))
                    self.assertEqual(s1 * s2 > 0, kind == "outer")    # same side / either side

    def test_point_tangents_incircle_perpendicular(self):
        for t in K.point_tangents((2, 0), (0, 0), 1):
            self.assertAlmostEqual(K._dot(K._sub(t, (2, 0)), t), 0, 12)
        c, r = K.incircle((0, 0), (3, 0), (0, 4))
        self.assertAlmostEqual(r, 1, 12)
        self.assertAlmostEqual(K._dist(c, (1, 1)), 0, 12)
        self.assertEqual(K.perpendicular((1, 5), (0, 0), (4, 0)), (1.0, 0.0))
        b = K.bisector((0, 0), (1, 0), (0, 1))
        self.assertAlmostEqual(b[0], math.sqrt(0.5), 12)
        m, d = K.perp_bisector((0, 0), (2, 0))
        self.assertEqual((m, d), ((1.0, 0.0), (-0.0, 1.0)))


class Crossings(unittest.TestCase):
    def test_lines_and_circles_exact(self):
        self.assertEqual(K.intersect_lines((0, 0), (2, 2), (0, 2), (2, 0)), [(1.0, 1.0)])
        self.assertEqual(K.intersect_lines((0, 0), (1, 0), (2, 1), (2, -1)), [])
        self.assertEqual(len(K.intersect_lines((0, 0), (1, 0), (2, 1), (2, -1), segments=False)), 1)
        ov = K.intersect_lines((0, 0), (2, 0), (1, 0), (3, 0))
        self.assertEqual(ov, [(1.0, 0.0), (2.0, 0.0)])
        pts = K.intersect_line_circle((-2, 0), (2, 0), (0, 0), 1)
        self.assertEqual(pts, [(-1.0, 0.0), (1.0, 0.0)])
        pts = K.intersect_circles((0, 0), 1, (1, 0), 1)
        for p in pts:
            self.assertAlmostEqual(K._dist(p, (0, 0)), 1, 14)
            self.assertAlmostEqual(K._dist(p, (1, 0)), 1, 14)
        self.assertEqual(len(K.intersect_circles((0, 0), 1, (2, 0), 1)), 1)   # touching
        a, b = Arc((0, 0), 1, 0, math.pi), Arc((1, 0), 1, math.pi / 2, math.pi)
        self.assertEqual(len(K.intersect(a, b)), 1)                   # only the upper crossing lies on both arcs
        self.assertEqual(len(K.intersect(Seg((-2, 0.5), (2, 0.5)), a)), 2)

    def test_polyline_crossings_and_split(self):
        xs = K.intersections([(0, 0), (2, 0)], [(0.5, -1), (0.5, 1), (1.5, 1), (1.5, -1)])
        self.assertEqual([round(s, 12) for _, s, _ in xs], [0.5, 1.5])
        pieces = K.split([(0, 0), (2, 0)], [[(0.5, -1), (0.5, 1), (1.5, 1), (1.5, -1)]])
        self.assertEqual(len(pieces), 3)
        self.assertAlmostEqual(K.length(pieces[1]), 1.0, 12)

    def test_resample_and_closest(self):
        arc = [Arc((0, 0), 1, 0, math.pi)]
        pts = K.resample(arc, count=5)
        for a, b in zip(pts, pts[1:]):
            self.assertAlmostEqual(K._dist(a, b), 2 * math.sin(math.pi / 8), 12)
        q, d, s = K.closest_point(arc, (0, 3))
        self.assertAlmostEqual(d, 2, 12)
        self.assertAlmostEqual(s, math.pi / 2, 12)
        q, d, s = K.closest_point([(0, 0), (2, 0), (2, 2)], (3, 1))
        self.assertEqual((q, d, s), ((2.0, 1.0), 1.0, 3.0))


class Booleans(unittest.TestCase):
    def test_squares(self):
        a, b = K.rect(0, 0, 2, 2), K.rect(1, 1, 3, 3)
        self.assertAlmostEqual(K.shapes_area(K.union(a, b)), 7, 12)
        self.assertAlmostEqual(K.shapes_area(K.intersection(a, b)), 1, 12)
        self.assertAlmostEqual(K.shapes_area(K.difference(a, b)), 3, 12)

    def test_holes_and_touching(self):
        frame = K.difference(K.rect(0, 0, 3, 3), K.rect(1, 1, 2, 2))
        self.assertEqual((len(frame), len(frame[0][1])), (1, 1))
        self.assertAlmostEqual(K.shapes_area(frame), 8, 12)
        side = K.union(K.rect(0, 0, 1, 1), K.rect(1, 0, 2, 1))       # shared edge: one rectangle, no seam
        self.assertEqual(len(side), 1)
        self.assertEqual(len(side[0][0]), 4)
        tee = K.union(K.rect(0, 0, 2, 1), K.rect(0.5, 1, 1.5, 2))     # partly shared edge
        self.assertEqual(len(tee), 1)
        self.assertAlmostEqual(K.shapes_area(tee), 3, 12)
        corner = K.union(K.rect(0, 0, 1, 1), K.rect(1, 1, 2, 2))      # touching at a point: two pieces
        self.assertEqual(len(corner), 2)
        filled = K.union(frame, K.rect(1, 1, 2, 2))                    # a plug fills the hole exactly
        self.assertEqual((len(filled), len(filled[0][1])), (1, 0))
        self.assertAlmostEqual(K.shapes_area(K.difference(frame, K.rect(0, 0, 3, 1.5))), 4.0, 12)   # 8 less the lower half (4.5 less its 0.5 of hole)

    def test_circles_analytic(self):
        tol = 1e-5
        c1, c2 = K.circle((0, 0), 1, tol), K.circle((1, 0), 1, tol)
        lens = 2 * math.acos(0.5) - 0.5 * math.sqrt(3)               # area of the lens of two unit circles 1 apart
        # sampled circles are inscribed polygons: area short by about 2/3 x sagitta x perimeter
        self.assertAlmostEqual(K.shapes_area(K.intersection(c1, c2)), lens, delta=tol * 2 * math.pi)
        self.assertAlmostEqual(K.shapes_area(K.union(c1, c2)), 2 * math.pi - lens, delta=tol * 4 * math.pi)
        ring = K.difference(K.circle((0, 0), 2, tol), K.circle((0, 0), 1, tol))
        self.assertAlmostEqual(K.shapes_area(ring), 3 * math.pi, delta=tol * 6 * math.pi)

    def test_many_unions(self):
        random.seed(5)
        bars = [K.stroke([(random.random(), random.random()), (random.random(), random.random())], 0.03) for _ in range(30)]
        u = K.union(*bars)
        for o, hs in u:
            self.assertGreater(K.area(o), 0)
            self.assertTrue(all(K.area(h) < 0 for h in hs))
        grid = K.union(*[K.rect(i, 0, i + 1, 1) for i in range(10)])  # ten abutting squares: one
        self.assertEqual(len(grid), 1)
        self.assertEqual(len(grid[0][0]), 4)


class Solids(unittest.TestCase):
    def test_triangulate_covers(self):
        shape = K.difference(K.circle((0, 0), 1, 1e-3), K.union(K.circle((0.4, 0), 0.25), K.circle((-0.4, 0), 0.25),
                                                                  K.rect(-0.1, 0.4, 0.1, 0.8)))[0]
        o, hs = shape
        pts = o + [p for h in hs for p in h]
        tris = K.triangulate(o, hs)
        self.assertEqual(len(tris), len(pts) + 2 * len(hs) - 2)
        tot = sum(K._turn(pts[a], pts[b], pts[c]) / 2 for a, b, c in tris)
        self.assertAlmostEqual(tot, K.shapes_area([shape]), 10)
        self.assertTrue(all(K._turn(pts[a], pts[b], pts[c]) > 0 for a, b, c in tris))

    def test_plate_check(self):
        root = Path(tempfile.mkdtemp(prefix="kitlib-curves-"))
        kit = Kit(root, "curves", "curves")
        lace = K.difference(K.circle((0, 0), 1.0, 2e-4), K.union(*[K.circle(K._polar((0, 0), 0.6, k * 0.5), 0.2, 2e-4)
                                                                    for k in range(12)]))
        for name, shapes, kw in (("plate-xz", lace, {}), ("plate-xy", lace, dict(plane="xy")),
                                 ("plate-strips", lace, dict(max_vertices=800)),
                                 ("fillet-plate", [(K.sample(K.fillet(L_SHAPE, 0.2)), [])], dict(plane="yz"))):
            g = Graph()
            kit.module(name, {}, g, g.plate("p", shapes, 0.02, slot="iron", **kw), {})
        kit.close()
        for name in ("plate-xz", "plate-xy", "plate-strips", "fillet-plate"):
            with self.subTest(name):
                doc = root / "curves" / f"{name}.json"
                r = json.loads(subprocess.run([PGM, "check", str(doc)], capture_output=True, text=True).stdout)
                self.assertTrue(r["passed"] and r["clean"], r)
                a = json.loads(subprocess.run([PGM, "audit", str(doc)], capture_output=True, text=True).stdout)
                self.assertEqual(a["components"], 1, a)
        strips = json.loads((root / "curves" / "plate-strips.json").read_text())
        self.assertGreater(sum(n["type"] == "surface" for n in strips["nodes"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
