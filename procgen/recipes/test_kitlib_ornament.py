"""Tests of the kitlib ornament helpers (kitlib_ornament.py). Built by Opus 5.5 (medium), 2026-11-16.

Curves: turn counts, end radii and eye spacing as asked. Members and brackets: sample documents built into a
temporary kit and run through pgm: every sample closed, manifold and outward (pgm check), single members free
of self-intersections (check --intersections), one component per composite (pgm audit), triangle counts in
budget, controls swept; paths that would fold a bar into itself are refused.

    PGM=/path/to/pgm python3 procgen/recipes/test_kitlib_ornament.py [--sheet /tmp/ornament-sheet.png] [--keep DIR]
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
from kitlib import C, Graph, Kit, control  # noqa: E402
from kitlib_ornament import c_scroll, s_scroll, volute, whiplash  # noqa: E402

PGM = os.environ.get("PGM", str(Path(__file__).resolve().parent.parent / "build-release" / "pgm"))
KIT = None
TAU = 2 * math.pi


def pgm(*args):
    r = subprocess.run([PGM, *map(str, args)], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        raise AssertionError(f"pgm {' '.join(map(str, args))}: {r.stdout[:400]} {r.stderr[:400]}")


def lever(g):
    """RD02-like: a 0.24 m lever that swells from its collar to the grip and rounds off; oval section."""
    path = [(0.0, 0.0), (0.06, 0.004), (0.13, 0.012), (0.19, 0.026), (0.24, 0.046)]
    return g.swell_tube("lever", path, [(0, 0.012), (0.25, 0.0075), (0.78, 0.0135), (1, 0.009)], sides=14, aspect=0.72,
                        plane="xy", slot="bronze")


def post(g):
    """An organic post: a straight stem with a bud swelling under the top."""
    return g.swell_tube("post", [(0, 0, 0), (0, 0, 1.1)], [(0, 0.045), (0.12, 0.03), (0.7, 0.026), (0.86, 0.05), (1, 0.012)],
                        sides=16, ends=("flat", "round"))


def tendril(g):
    """A tendril: a 3D helix that tightens and tapers to a point."""
    def p(t):
        r = 0.09 * (1 - 0.7 * t)
        return (r * math.cos(TAU * 2.2 * t), r * math.sin(TAU * 2.2 * t), 0.35 * t)
    return g.swell_tube("tendril", p, lambda t: 0.009 * (1 - 0.75 * t), sides=8, segments=90)


def lever_hand(g):
    """The lever mirrored by a `hand` control (-1, 0, 1) through scale=[hand, 1, 1], turned by a control too.
    Built by Opus 5.5 (medium), 2026-11-16."""
    path = [(0.0, 0.0), (0.06, 0.004), (0.13, 0.012), (0.19, 0.026), (0.24, 0.046)]
    return g.swell_tube("lever", path, [(0, 0.012), (0.25, 0.0075), (0.78, 0.0135), (1, 0.009)], sides=14, aspect=0.72,
                        plane="xy", scale=[C("hand"), 1, 1], rotate=C("hand") * 0.1, at=[0, -0.04, 0], slot="bronze")


# name -> (builder, controls, budget in triangles, single member?)
SAMPLES = {
    "swell-lever": (lever, None, 3000, True),
    "swell-lever-hand": (lever_hand, {"hand": control(1, -1, 1, True)}, 3000, True),
    "round-bar-hand": (lambda g: g.round_bar("v", volute(1.5, 0.1, 0.022, kind="euler"), 0.006, taper=0.6,
                                             scale=[C("hand"), 1, 1]), {"hand": control(-1, -1, 1, True)}, 3000, True),
    "swell-post": (post, None, 2000, True),
    "swell-tendril": (tendril, None, 2000, True),
    "volute-log": (lambda g: g.flat_bar("v", volute(2.5, 0.12, 0.012, lead=0.08), 0.02, [(0, 0.008), (0.6, 0.008), (1, 0.005)]),
                   None, 3000, True),
    "volute-euler": (lambda g: g.round_bar("v", volute(1.5, 0.1, 0.022, kind="euler"), 0.006, taper=0.6), None, 3000, True),
    "c-scroll": (lambda g: g.flat_bar("c", c_scroll(0.32, eyes=0.02, turns=(1.25, 1.0)), 0.02, 0.008), None, 3000, True),
    "s-scroll": (lambda g: g.flat_bar("s", s_scroll(0.4, eyes=0.018, turns=1.25), 0.02,
                                      [(0, 0.006), (0.25, 0.009), (0.75, 0.009), (1, 0.006)]), None, 3000, True),
    "s-scroll-round": (lambda g: g.round_bar("s", s_scroll(0.4, eyes=0.02), 0.007, taper=0.6, end="leaf"), None, 4500, False),
    "whiplash-leaf": (lambda g: g.flat_bar("w", whiplash(0.9, sway=0.1, recoil=1.1), 0.018, [(0, 0.009), (0.8, 0.008), (1, 0.005)],
                                           start="leaf"), None, 3500, False),
    "whiplash-curls": (lambda g: g.round_bar("w", whiplash(0.7, sway=0.12, recoil=0.9), 0.006, taper=0.7, start="curl"),
                       None, 3000, True),
    "bracket-whiplash": (lambda g: g.scroll_bracket("b", 0.8, slot="iron")[0], None, 12000, False),
    "bracket-s": (lambda g: g.scroll_bracket("b", 0.7, support="s", facing="-y", at=[0.5, 0, 2.5])[0], None, 12000, False),
    "bracket-c": (lambda g: g.scroll_bracket("b", 0.6, support="c", filler=False)[0], None, 12000, False),
    "bracket-scaled": (lambda g: g.scroll_bracket("b", 0.8, scale=C("size"))[0], {"size": control(1.0, 0.6, 1.6)}, 12000, False),
}


def build(directory):
    kit = Kit(directory, "ornament", "ornament", bake_leaves=True)
    for name, (fn, controls, _, _) in SAMPLES.items():
        g = Graph()
        kit.module(name, controls or {}, g, fn(g), {})
    kit.close()
    return Path(directory) / "ornament"


def turning(points, i0=0, i1=None):
    """Net tangent turning of a polyline, in turns."""
    i1 = len(points) - 1 if i1 is None else i1
    total = 0.0
    for i in range(i0 + 1, i1):
        a, b, c = points[i - 1], points[i], points[i + 1]
        h0, h1 = math.atan2(b[1] - a[1], b[0] - a[0]), math.atan2(c[1] - b[1], c[0] - b[0])
        total += (h1 - h0 + math.pi) % TAU - math.pi
    return total / TAU


def end_radius(points, at_end=True, step=3, linear="k"):
    """Radius of curvature at an end: circles through three points at two spacings (curvature at step and
    2 * step points in), extrapolated to the end point: linearly in curvature (linear="k", Euler pieces) or in
    radius (linear="r", log-spiral pieces)."""
    p = points[::-1] if at_end else points

    def k(st):
        a, b, c = p[0], p[st], p[2 * st]
        area2 = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
        return 2 * area2 / (math.dist(a, b) * math.dist(b, c) * math.dist(a, c))
    if linear == "r":
        return 2 / k(step) - 1 / k(2 * step)
    return 1.0 / (2 * k(step) - k(2 * step))


class Curves(unittest.TestCase):
    # Measured on the points (densely sampled: a polyline misses half a step of turning at each end).
    def test_log_volute(self):
        for turns, r0, r1, hand in ((2.5, 0.12, 0.012, "left"), (1.25, 0.05, 0.02, "right"), (4.0, 0.3, 0.01, "left")):
            v = volute(turns, r0, r1, hand=hand, heading=0.7, start=(1, 2), per_turn=240)
            e = v.eyes[0]
            self.assertAlmostEqual(math.dist(v[0], e), r0, delta=r0 * 0.002)
            self.assertAlmostEqual(math.dist(v[-1], e), r1, delta=r1 * 0.002)
            self.assertAlmostEqual(abs(turning(v)), turns, delta=0.01)
            self.assertEqual(turning(v) > 0, hand == "left")
            ang = [math.atan2(y - e[1], x - e[0]) for x, y in v]    # polar angle about the eye: `turns` turns too
            sweep = sum((b - a + math.pi) % TAU - math.pi for a, b in zip(ang, ang[1:]))
            self.assertAlmostEqual(abs(sweep) / TAU, turns, delta=0.01)
            radii = [math.dist(p, e) for p in v]
            self.assertTrue(all(b < a for a, b in zip(radii, radii[1:])))

    def test_euler_volute(self):
        v = volute(2.0, 0.1, 0.02, kind="euler", per_turn=240)
        self.assertAlmostEqual(abs(turning(v)), 2.0, delta=0.01)
        self.assertAlmostEqual(end_radius(v, False), 0.1, delta=0.004)
        self.assertAlmostEqual(end_radius(v, True), 0.02, delta=0.001)

    def test_c_and_s_scrolls(self):
        for fn, sign in ((c_scroll, 1), (s_scroll, -1)):
            c = fn(0.35, eyes=(0.02, 0.03), turns=(1.5, 1.0), per_turn=240)
            self.assertAlmostEqual(math.dist(*c.eyes), 0.35, delta=1e-3)
            self.assertAlmostEqual(end_radius(c, False, linear="r"), 0.02, delta=0.0005)
            self.assertAlmostEqual(end_radius(c, True, linear="r"), 0.03, delta=0.0008)
            i0, i1 = c.parts["curl1"]
            self.assertAlmostEqual(turning(c, 0, i1), 1.5, delta=0.03)
            i0, i1 = c.parts["curl2"]
            self.assertAlmostEqual(turning(c, i0, len(c) - 1), 1.0 * sign, delta=0.03)

    def test_whiplash(self):
        w = whiplash(1.0, sway=0.1, recoil=0.9, hand="right", per_turn=240)
        i = w.parts["lash"][1]
        self.assertAlmostEqual(turning(w, i), -0.9, delta=0.03)
        self.assertAlmostEqual(end_radius(w, True, linear="r"), 1.0 / 14 * 0.6, delta=0.001)
        self.assertAlmostEqual(w.s[i], 1.0, delta=0.02)
        # the S inflects: the lash turns both ways
        k = w.kappa[:i]
        self.assertTrue(max(k) > 0 and min(k) < 0)

    def test_fold_is_refused(self):
        g = Graph()
        with self.assertRaises(ValueError):          # turns of 0.01 m pitch with a 0.02 m bar
            g.flat_bar("x", volute(3.0, 0.05, 0.02), 0.02, 0.02)
        with self.assertRaises(ValueError):          # eye tighter than the bar
            g.round_bar("x", volute(1.0, 0.05, 0.004), 0.008)


class Members(unittest.TestCase):
    def doc(self, name):
        return KIT / f"{name}.json"

    def test_check_clean(self):
        for name in SAMPLES:
            with self.subTest(name):
                r = pgm("check", self.doc(name))
                self.assertTrue(r.get("passed") and r["clean"], r)
                self.assertEqual(r["openPrototypes"], 0, r)

    def test_single_members_self_intersection_free(self):
        for name, (_, _, _, single) in SAMPLES.items():
            if single:
                with self.subTest(name):
                    r = pgm("check", self.doc(name), "--intersections")
                    self.assertEqual(r["selfIntersectingPrototypes"], 0, r)

    def test_one_component_each(self):
        for name in SAMPLES:
            with self.subTest(name):
                r = pgm("audit", self.doc(name))
                self.assertTrue(r["passed"], r)
                self.assertEqual(r["components"], 1, r)

    def test_triangle_budget(self):
        for name, (_, _, budget, _) in SAMPLES.items():
            with self.subTest(name):
                r = pgm("evaluate", self.doc(name))
                self.assertLessEqual(r["placedTriangles"], budget, r)
                print(f"  {name}: {r['placedTriangles']} triangles", file=sys.stderr)

    def test_hanging_point(self):
        g = Graph()
        _, hook = g.scroll_bracket("b", 0.8, at=[1.0, 2.0, 3.0], facing="+y")
        self.assertAlmostEqual(hook[0], 1.0, delta=1e-9)
        self.assertAlmostEqual(hook[1], 2.0 + 0.86 * 0.8, delta=1e-6)
        self.assertLess(hook[2], 3.0)
        _, hook = Graph().scroll_bracket("b", 0.8, scale=C("size"))
        self.assertFalse(isinstance(hook[0], float) and isinstance(hook[2], float))

    def test_hand_mirror(self):
        """Mirrored by a control: outward and clean at hand = -1, 0 and 1; mirrored bounds at -1, full size at 0."""
        boxes = {}
        for name in ("swell-lever-hand", "round-bar-hand"):
            for h in (-1, 0, 1):
                with self.subTest(name=name, hand=h):
                    r = pgm("check", self.doc(name), "--set", f"hand={h}")
                    self.assertTrue(r.get("passed") and r["clean"], r)
                    e = pgm("evaluate", self.doc(name), "--set", f"hand={h}")
                    self.assertTrue(e.get("passed"), e)
                    boxes[name, h] = e["boundsZUp"]
        lo, hi = boxes["swell-lever-hand", -1], boxes["swell-lever-hand", 1]
        self.assertAlmostEqual(lo[0][0], -hi[1][0], delta=1e-4)
        self.assertAlmostEqual(lo[1][0], -hi[0][0], delta=1e-4)
        self.assertGreater(boxes["swell-lever-hand", 0][1][0] - boxes["swell-lever-hand", 0][0][0], 0.2)
        with self.assertRaises(AssertionError):
            Graph().swell_tube("x", [(0, 0), (0.2, 0)], 0.01, scale=[0, 1, 1])

    def test_sweep(self):
        r = pgm("sweep", KIT)
        self.assertTrue(r["passed"], r)
        r = pgm("audit", KIT, "--sweep")
        self.assertTrue(r["passed"], r)


def sheet(out):
    panels = [
        ("swell-lever", "swelling lever, oval section (RD02)", 0, 89),
        ("swell-post", "organic post with a bud", -30, 10),
        ("swell-tendril", "tapering 3D tendril", -30, 25),
        ("volute-log", "log volute, flat bar, tapered eye", 90, 0),
        ("c-scroll", "C-scroll, flat bar", 90, 0),
        ("s-scroll", "S-scroll, swelling flat bar", 90, 0),
        ("whiplash-leaf", "whiplash, leaf terminal", 90, 0),
        ("s-scroll-round", "S-scroll, round tapered, leaf", 65, 12),
        ("bracket-whiplash", "scroll bracket, whiplash", 90, 0),
        ("bracket-whiplash", "scroll bracket, whiplash (3/4)", 55, 15),
        ("bracket-s", "scroll bracket, S-scroll", 0, 0),
        ("bracket-c", "scroll bracket, C-scroll", 90, 0),
    ]
    spec = {"title": "kitlib ornament: swelling tubes, scrolls, whiplash, scroll brackets", "width": 2400, "height": 1500,
            "layout": "grid", "columns": 4,
            "panels": [{"document": str(KIT / f"{d}.json"), "caption": c, "yaw": y, "pitch": p} for d, c, y, p in panels]}
    path = KIT / "sheet.json"
    path.write_text(json.dumps(spec, indent=1))
    print(json.dumps(pgm("sheet", path, "--out", out)))


if __name__ == "__main__":
    args = sys.argv[1:]
    out = args[args.index("--sheet") + 1] if "--sheet" in args else None
    keep = args[args.index("--keep") + 1] if "--keep" in args else None
    root = keep or tempfile.mkdtemp(prefix="kitlib-ornament-")
    KIT = build(root)
    if out:
        sheet(out)
    if "--no-tests" not in args:
        unittest.main(argv=[sys.argv[0], "-v"])
