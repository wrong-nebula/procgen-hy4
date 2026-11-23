"""Tests of the Gothic tracery helpers. Built by Opus 5.5 (medium), 2026-11-16.

Builds sample documents into a temporary kit and runs pgm on them: every sample closed, manifold and
outward (pgm check), one component (pgm audit), no face over 128 corners, the outline one piece with
its openings as holes, triangle counts printed and held to a budget, controls swept.

    PGM=/path/to/pgm python3 procgen/recipes/test_kitlib_tracery.py [--sheet /tmp/tracery-sheet.png] [--keep DIR]
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitlib_curves as K  # noqa: E402
import kitlib_tracery as T  # noqa: E402
from kitlib import Graph, Kit  # noqa: E402

PGM = os.environ.get("PGM", str(Path(__file__).resolve().parent.parent / "build-release" / "pgm"))
KIT = None

# name -> (method, kwargs, triangle budget)
SAMPLES = {
    "arch-plain": ("tracery_arch", dict(span=0.6, spring=1.2, foils=0), 200),
    "arch-trefoil": ("tracery_arch", dict(span=0.6, spring=1.2, foils=3), 400),
    "arch-cinquefoil": ("tracery_arch", dict(span=0.7, spring=1.2, foils=5, fraction=1.2), 600),
    "trefoil": ("rosette", dict(radius=0.25, foils=3), 800),
    "quatrefoil": ("rosette", dict(radius=0.25, foils=4), 900),
    "multifoil-8": ("rosette", dict(radius=0.3, foils=8, cusp=0.75), 1500),
    "quatrefoil-plate": ("rosette", dict(radius=0.25, foils=4, style="plate"), 500),
    "window-y": ("tracery_window", dict(span=1.2, spring=1.6, kind="y"), 2000),
    "window-intersecting": ("tracery_window", dict(span=1.6, spring=1.6, lights=4, kind="intersecting"), 2600),
    "window-geometric": ("tracery_window", dict(span=1.2, spring=1.6, kind="geometric", head_foils=6), 2400),
    "window-plate": ("tracery_window", dict(span=1.2, spring=1.6, kind="geometric", style="plate", head_foils=4), 1400),
    "rose": ("rose_window", dict(radius=1.0, spokes=12), 7000),
    "railing": ("tracery_railing", dict(length=1.6, height=1.0, bays=2, arches=4), 4500),
    "railing-intersecting": ("tracery_railing", dict(length=1.6, height=1.0, kind="intersecting", arches=5), 3000),
}


def pgm(*args):
    r = subprocess.run([PGM, *map(str, args)], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        raise AssertionError(f"pgm {' '.join(map(str, args))}: {r.stdout[:400]} {r.stderr[:400]}")


def build(directory):
    kit = Kit(directory, "tracery", "tracery", bake_leaves=True)
    for name, (method, kw, _) in SAMPLES.items():
        g = Graph()
        out = getattr(g, method)("t", slot="iron", **kw)
        kit.module(name, {}, g, out, {})
    kit.close()
    return Path(directory) / "tracery"


class Shapes(unittest.TestCase):
    def test_one_piece_with_openings(self):
        cases = {"arch": T.arch_shapes(0.6, 1.2, foils=3), "y": T.window_shapes(1.2, 1.6),
                 "rose": T.rose_shapes(1.0), "rail": T.railing_shapes(1.6, 1.0, bays=2),
                 "quatrefoil": T.rosette_shapes(0.25, 4)}
        for name, s in cases.items():
            with self.subTest(name):
                self.assertEqual(len(s), 1, name)                     # one outline
                self.assertGreater(len(s[0][1]), 0)                   # pierced
                for h in s[0][1]:
                    self.assertLess(K.area(h), 0)

    def test_cusping_adds_solid(self):
        plain = K.shapes_area(T.arch_shapes(0.6, 1.2, foils=0))
        cusped = K.shapes_area(T.arch_shapes(0.6, 1.2, foils=3))
        self.assertGreater(cusped, plain * 1.05)

    def test_pointed_arch(self):
        a = T.pointed_arch(1.0, 1.0)                                  # equilateral: apex at sqrt(3)/2
        self.assertAlmostEqual(a[0].end[1], 3 ** 0.5 / 2, 12)
        self.assertAlmostEqual(a[0].end[0], 0.0, 12)
        self.assertAlmostEqual(K._dist(a[0].end, a[1].start), 0.0, 12)
        r = T.pointed_arch(2.0, 0.5)                                  # semicircle
        self.assertAlmostEqual(r[0].end[1], 1.0, 12)

    def test_foil_lobes_tangent(self):
        R = 0.3
        f = T.foil_opening((0, 0), R, 4, cusp=0.8, tol=0.0002)
        far = max(K._len(p) for p in f[0][0])
        self.assertAlmostEqual(far, R, delta=0.0002)                 # lobes touch the circle from inside


class Tracery(unittest.TestCase):
    def doc(self, name):
        return KIT / f"{name}.json"

    def test_check_clean(self):
        for name in SAMPLES:
            with self.subTest(name):
                r = pgm("check", self.doc(name))
                self.assertTrue(r.get("passed") and r["clean"], r)
                self.assertEqual(r["openPrototypes"], 0, r)

    def test_one_component(self):
        for name in SAMPLES:
            with self.subTest(name):
                r = pgm("audit", self.doc(name))
                self.assertTrue(r["passed"], r)
                self.assertEqual(r["components"], 1, r)

    def test_faces_under_128(self):
        for name in SAMPLES:
            doc = json.loads(self.doc(name).read_text())
            for n in doc["nodes"]:
                for f in n.get("options", {}).get("faces", []):
                    self.assertLessEqual(len(f), 4, name)

    def test_triangle_budget(self):
        for name, (_, _, budget) in SAMPLES.items():
            with self.subTest(name):
                r = pgm("evaluate", self.doc(name))
                print(f"  {name}: {r['placedTriangles']} triangles", file=sys.stderr)
                self.assertLessEqual(r["placedTriangles"], budget, r)

    def test_sweep(self):
        self.assertTrue(pgm("sweep", KIT)["passed"])


def sheet(out):
    panels = [(n, n) for n in SAMPLES]
    spec = {"title": "kitlib tracery: cusped lancets, foils, Y / intersecting / geometric windows, rose, railing",
            "width": 2100, "height": 2700, "layout": "grid", "columns": 3,
            "panels": [{"document": str(KIT / f"{d}.json"), "caption": c, "yaw": -72, "pitch": 10} for d, c in panels]}
    path = KIT / "sheet.json"
    path.write_text(json.dumps(spec, indent=1))
    print(json.dumps(pgm("sheet", path, "--out", out)))


if __name__ == "__main__":
    args = sys.argv[1:]
    out = args[args.index("--sheet") + 1] if "--sheet" in args else None
    keep = args[args.index("--keep") + 1] if "--keep" in args else None
    KIT = build(keep or tempfile.mkdtemp(prefix="kitlib-tracery-"))
    if out:
        sheet(out)
    unittest.main(argv=[sys.argv[0], "-v"])
