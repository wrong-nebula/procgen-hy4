"""Tests of the classical orders and moulding helpers (kitlib_orders.py). Built by Opus 5.5 (medium), 2026-11-16.

Profiles: each moulding runs bottom to top with the right curvature. Mitred runs: corner sections lie on the
bisecting plane and closed outlines close. Sample documents are built into a temporary kit and run through pgm:
every sample closed, manifold and outward (pgm check), each one component (pgm audit), triangle counts in budget,
controls swept; column heights in modules and entablature / column ratios measured from the evaluated bounds
against Vignola's numbers. Control expressions (Built by Opus 5.5 (medium)): arcade bays sized by height and pitch,
whose capitals meet the entablature at both ends of every control, blocks counted from run lengths, and runs,
blocks, pediments, balustrades and surrounds driven by controls, all swept by pgm sweep and audit --sweep.

    PGM=/path/to/pgm python3 procgen/recipes/test_kitlib_orders.py [--sheet /tmp/orders-sheet.png] [--keep DIR]
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
import kitlib_orders as ko  # noqa: E402

PGM = os.environ.get("PGM", str(Path(__file__).resolve().parent.parent / "build-release" / "pgm"))
KIT = None
M = 0.25          # module (lower radius) of the samples, metres

CORNICE = [("fillet", 0.03, 0.0), ("cyma_reversa", 0.06, 0.0, 0.06), ("fillet", 0.015, 0.07), ("ovolo", 0.07, 0.07, 0.14),
           ("corona", 0.12, 0.32), ("fillet", 0.02, 0.33), ("cyma_recta", 0.1, 0.33, 0.45), ("fillet", 0.025, 0.45)]
BASE = [("plinth", 0.12, 0.08), ("torus", 0.08, 0.06), ("fillet", 0.012, 0.045), ("scotia", 0.05, 0.045, 0.035, 0.03),
        ("fillet", 0.012, 0.035), ("torus", 0.05, 0.02), ("cavetto", 0.06, 0.02, 0.0)]


def pgm(*args):
    r = subprocess.run([PGM, *map(str, args)], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        raise AssertionError(f"pgm {' '.join(map(str, args))}: {r.stdout[:400]} {r.stderr[:400]}")


def portico(g, name="corinthian", n=4, bay=None, res=0.6):
    """A tetrastyle portico: columns, entablature with returns and a pediment."""
    o = ko.order(name)
    bay = bay or 6.5 * M
    parts = []
    xs = [(i - (n - 1) / 2) * bay for i in range(n)]
    g.column("col", name, M, flutes=True, resolution=res)
    for i, x in enumerate(xs):
        parts.append(g.xf(f"col-{i}", "col", t=[x, 0, 0]))
    zc = o["column"] * M
    rt = o["top"] * M
    x0, x1 = xs[0] - rt - 0.2 * M, xs[-1] + rt + 0.2 * M
    parts.append(g.entablature("ent", name, M, [(x0, -rt), (x1, -rt)], z=zc, ends="return", crown=False))
    zf = zc + (o["architrave"] + o["frieze"]) * M
    parts.append(g.pediment("ped", x1 - x0, name, M, at=[0, -rt, zf]))
    return g.join("portico", *parts)


def capital(g, name):
    """A capital on a short length of neck, for close views (modules as metres)."""
    o = ko.order(name)
    neck = g.turned("neck", ko.Profile([(o["top"], -0.8), (o["top"], 0.02)]), sides=40)
    return g.join("cap", neck, *ko._capital(g, "c", name, o, 0.0, 1.0, 40))


BAY = {"height": control(4.5, 3.0, 6.0), "pitch": control(3.5, 2.5, 5.0)}


def bay(g, name, part="all"):
    """An arcade bay sized from its controls (Built by Opus 5.5 (medium)): two pilasters `pitch` apart and the
    entablature over them, the order filling the storey `height` (order_for_height)."""
    H, B = C("height"), C("pitch")
    m = ko.order_for_height(name, H)["module"]
    parts = []
    if part in ("all", "pilaster"):
        parts += [g.pilaster("pa", name, height=H, resolution=0.5),
                  g.pilaster("pb", name, height=H, at=[B, 0, 0], resolution=0.5)]
    if part in ("all", "ent"):
        parts.append(g.entablature("e", name, height=H, outline=[(m * -1.6, m * -0.5), (B + m * 1.6, m * -0.5)],
                                   controls=BAY))
    return g.join("bay", *parts)


SPAN = {"span": control(4.0, 2.5, 7.0), "module": control(0.25, 0.18, 0.35)}
RUN = {"length": control(3.0, 1.0, 6.0), "module": control(1.0, 0.6, 1.6)}
WIN = {"width": control(1.2, 0.8, 2.0), "height": control(2.2, 1.6, 3.2)}
RAIL = {"length": control(4.0, 1.5, 8.0), "height": control(0.95, 0.8, 1.2)}

# name -> (builder, controls, budget in triangles)
SAMPLES = {
    "mouldings": (lambda g: g.join("all", *[g.moulding_run(k, [(k, 0.12, 0.0, 0.08) if k not in ("torus", "astragal", "scotia")
                                                               else (k, 0.12, 0.04, 0.02 if k == "scotia" else None)],
                                                           [(i * 0.4, 0), (i * 0.4 + 0.3, 0)], ends="stop")
                                            for i, k in enumerate(("fillet", "torus", "astragal", "scotia", "cavetto", "ovolo",
                                                                   "cyma_recta", "cyma_reversa", "corona"))]), None, 3000),
    "cornice-loop": (lambda g: g.moulding_run("c", CORNICE, [(0, 0), (3, 0), (3, 2), (1.5, 2), (1.5, 3), (0, 3)], closed=True),
                     None, 3000),
    "cornice-returns": (lambda g: g.moulding_run("c", CORNICE, [(0, 0), (2.5, 0)], ends="return", back=-0.1), None, 1500),
    "string-course": (lambda g: g.moulding_run("c", [("fascia", 0.12, 0.0), ("cyma_reversa", 0.05, 0.0, 0.05), ("fillet", 0.02, 0.06)],
                                               [(0, 0), (4, 0), (4, -2)], ends=("stop", "return"), z=C("z")),
                      {"z": control(3.0, 1.0, 6.0)}, 1500),
    "turned-base": (lambda g: g.turned("b", BASE, sides=32), None, 5000),
    "block-abacus": (lambda g: g.moulded_block("a", ko._concave_abacus(), [("fascia", 0.1, -0.05), ("ovolo", 0.08, -0.05, 0.03)]),
                     None, 2000),
    "tuscan": (lambda g: g.column("c", "tuscan", M), None, 5000),
    "doric": (lambda g: g.column("c", "doric", M), None, 8000),
    "doric-fluted": (lambda g: g.column("c", "doric", M, flutes=True), None, 22000),
    "ionic": (lambda g: g.column("c", "ionic", M), None, 13500),
    "ionic-fluted": (lambda g: g.column("c", "ionic", M, flutes=True), None, 33000),
    "corinthian": (lambda g: g.column("c", "corinthian", M), None, 27000),
    "corinthian-low": (lambda g: g.column("c", "corinthian", M, resolution=0.5), None, 12500),
    "corinthian-fluted": (lambda g: g.column("c", "corinthian", C("module"), flutes=True),
                          {"module": control(M, 0.15, 0.4)}, 46000),
    "composite": (lambda g: g.column("c", "composite", M), None, 28000),
    "cap-doric": (lambda g: capital(g, "doric"), None, 3000),
    "cap-ionic": (lambda g: capital(g, "ionic"), None, 9000),
    "cap-corinthian": (lambda g: capital(g, "corinthian"), None, 20000),
    "cap-composite": (lambda g: capital(g, "composite"), None, 22000),
    "pilaster-doric": (lambda g: g.pilaster("p", "doric", M, flutes=True), None, 6000),
    "pilaster-ionic": (lambda g: g.pilaster("p", "ionic", M), None, 8000),
    "pilaster-corinthian": (lambda g: g.pilaster("p", "corinthian", M, facing="-x"), None, 20000),
    "ent-tuscan": (lambda g: g.entablature("e", "tuscan", M, [(0, 0), (4, 0)]), None, 3000),
    "ent-doric": (lambda g: g.entablature("e", "doric", M, [(0, 0), (4, 0), (4, 3)]), None, 30000),
    "ent-ionic": (lambda g: g.entablature("e", "ionic", M, [(0, 0), (4, 0)]), None, 10000),
    "ent-corinthian": (lambda g: g.entablature("e", "corinthian", M, [(0, 0), (4, 0), (4, 3), (0, 3)], closed=True), None, 40000),
    "pediment": (lambda g: g.pediment("p", 4.0, "ionic", M), None, 6000),
    "pediment-segmental": (lambda g: g.pediment("p", 3.0, "corinthian", 0.2, kind="segmental"), None, 8000),
    "balustrade": (lambda g: g.balustrade("b", [(0, 0), (4, 0), (4, 2)], 0.95), None, 25000),
    "window": (lambda g: g.surround("w", 1.2, 2.2, hood="pediment"), None, 5000),
    "window-segmental": (lambda g: g.surround("w", 1.1, 2.0, hood="segmental"), None, 5000),
    "door": (lambda g: g.surround("w", 1.6, 3.0, sill=False, hood="cornice"), None, 4000),
    "portico": (lambda g: portico(g), None, 110000),
    # control expressions (Built by Opus 5.5 (medium)); g.controls is set to the controls by build()
    "bay-doric": (lambda g: bay(g, "doric"), BAY, 40000),
    "bay-doric-pilaster": (lambda g: bay(g, "doric", "pilaster"), BAY, 12000),
    "bay-doric-ent": (lambda g: bay(g, "doric", "ent"), BAY, 30000),
    "bay-ionic": (lambda g: bay(g, "ionic"), BAY, 25000),
    "bay-ionic-pilaster": (lambda g: bay(g, "ionic", "pilaster"), BAY, 12000),
    "bay-ionic-ent": (lambda g: bay(g, "ionic", "ent"), BAY, 15000),
    "bay-corinthian": (lambda g: bay(g, "corinthian"), BAY, 45000),
    "bay-corinthian-pilaster": (lambda g: bay(g, "corinthian", "pilaster"), BAY, 25000),
    "bay-corinthian-ent": (lambda g: bay(g, "corinthian", "ent"), BAY, 25000),
    "column-height": (lambda g: g.column("c", "ionic", height=C("height"), flutes=True), {"height": control(4.5, 3.0, 6.0)}, 33000),
    "run-controls": (lambda g: g.moulding_run("c", CORNICE, [(0, 0), (C("length"), 0), (C("length"), 2)], module=C("module")),
                     RUN, 3000),
    "block-height": (lambda g: g.moulded_block("p", ko.square(C("width") / 2), [("plinth", 0.15, 0.06), ("cyma_reversa", 0.05, 0.06, 0.0),
                                                                               ("fascia", 1.0, 0.0), ("ovolo", 0.06, 0.0, 0.07), ("fillet", 0.03, 0.08)],
                                                     height=C("height")),
                     {"width": control(0.5, 0.3, 0.9), "height": control(1.4, 0.6, 3.0)}, 3000),
    "pediment-controls": (lambda g: g.pediment("p", C("span"), "ionic", C("module")), SPAN, 6000),
    "balustrade-controls": (lambda g: g.balustrade("b", [(0, 0), (C("length"), 0), (C("length"), 2)], C("height")), RAIL, 40000),
    "window-controls": (lambda g: g.surround("w", C("width"), C("height"), hood="pediment"), WIN, 5000),
}


def build(directory):
    kit = Kit(directory, "orders", "orders", bake_leaves=True)
    for name, (fn, controls, _) in SAMPLES.items():
        g = Graph()
        g.controls = controls
        audit = {"components": 9, "why": "nine separate sample mouldings"} if name == "mouldings" else None
        if name.endswith("-pilaster"):
            audit = {"components": 2, "why": "two free-standing pilasters (the entablature is in the bay document)"}
        kit.module(name, controls or {}, g, fn(g), {}, audit=audit)
    kit.close()
    return Path(directory) / "orders"


class Profiles(unittest.TestCase):
    def test_mouldings_bottom_to_top(self):
        for k in ko.MOULDINGS:
            pts = ko.moulding(k, 1.0, 0.0, 0.5)
            self.assertAlmostEqual(pts[0][1], 0.0)
            self.assertAlmostEqual(pts[-1][1], 1.0)
            self.assertTrue(all(b[1] >= a[1] - 1e-12 for a, b in zip(pts, pts[1:])), k)

    def test_curvature(self):
        # ovolo bulges outside the chord, cavetto inside; cyma recta hollow in its outer (upper) half
        def side(pts):
            (x0, z0), (x1, z1) = pts[0], pts[-1]
            m = pts[len(pts) // 2]
            return (x1 - x0) * (m[1] - z0) - (z1 - z0) * (m[0] - x0)
        self.assertLess(side(ko.moulding("ovolo", 1, 0, 1)), 0)        # right of the chord: outward
        self.assertGreater(side(ko.moulding("cavetto", 1, 0, 1)), 0)
        recta = ko.moulding("cyma_recta", 1, 0, 1)
        upper = [p for p in recta if p[1] > 0.5]
        self.assertGreater(side(upper), 0)                              # outer half hollow
        rev = ko.moulding("cyma_reversa", 1, 0, 1)
        self.assertLess(side([p for p in rev if p[1] > 0.5]), 0)        # outer half round
        tor = ko.moulding("torus", 1, 0.2)
        self.assertAlmostEqual(max(x for x, _ in tor), 0.7)
        sc = ko.moulding("scotia", 1, 0.3, 0.2, depth=0.2)
        self.assertLess(min(x for x, _ in sc), 0.1)

    def test_profile_tags_and_heights(self):
        for name in ko.ORDERS:
            o = ko.order(name)
            p = ko.entablature_profile(name)
            self.assertAlmostEqual(p.height, o["entablature"], places=9, msg=name)
            self.assertAlmostEqual(o["entablature"] / o["column"], 0.25, places=9)
            t = p.tags
            self.assertAlmostEqual(t["frieze"][0], o["architrave"], places=9, msg=name)
            self.assertAlmostEqual(t["frieze"][1], o["architrave"] + o["frieze"], places=9, msg=name)
            self.assertAlmostEqual(o["base"] + o["shaft"] + o["capital"], o["column"])
            # Vignola: the cornice projects about its own height
            self.assertLess(abs(p.reach - o["cornice"]) / o["cornice"], 0.3, name)

    def test_mitre_on_bisector(self):
        poly = ko.profile(CORNICE).polygon(-0.1)
        path = [(0, 0, 0), (3, 0, 0), (3, 2, 0)]
        verts, faces = ko._sweep_mesh(path, poly, (0, 0, 1))
        K = len(poly)
        ring = verts[K:2 * K]                    # the corner ring
        m = ko._unit(ko._add((1, 0, 0), (0, 1, 0)))
        for v in ring:
            self.assertAlmostEqual(ko._dot(ko._sub(v, (3, 0, 0)), m), 0.0, places=9)
        outer = max(ring, key=lambda v: v[0] - v[1])
        reach = max(x for x, _ in poly)
        self.assertAlmostEqual(outer[0], 3 + reach, places=9)        # projects reach on both legs at the corner
        self.assertAlmostEqual(outer[1], -reach, places=9)


class Members(unittest.TestCase):
    def doc(self, name):
        return KIT / f"{name}.json"

    def test_check_clean(self):
        for name in SAMPLES:
            with self.subTest(name):
                r = pgm("check", self.doc(name))
                self.assertTrue(r.get("passed") and r["clean"], r)
                self.assertEqual(r["openPrototypes"], 0, r)

    def test_one_component_each(self):
        for name in SAMPLES:
            if name == "mouldings" or name.endswith("-pilaster"):
                continue
            with self.subTest(name):
                r = pgm("audit", self.doc(name))
                self.assertTrue(r["passed"], r)
                self.assertEqual(r["components"], 1, r)

    def test_triangle_budget(self):
        for name, (_, _, budget) in SAMPLES.items():
            with self.subTest(name):
                r = pgm("evaluate", self.doc(name))
                self.assertLessEqual(r["placedTriangles"], budget, r)
                print(f"  {name}: {r['placedTriangles']} triangles", file=sys.stderr)

    def test_vignola_heights(self):
        for name in ko.ORDERS:
            with self.subTest(name):
                r = pgm("evaluate", self.doc(name))
                lo, hi = r["boundsZUp"]
                self.assertAlmostEqual(hi[2] / M, ko.ORDERS[name]["column"], delta=0.02)
                self.assertAlmostEqual(lo[2], 0.0, delta=1e-6)
        for name in ("tuscan", "ionic"):
            r = pgm("evaluate", self.doc("ent-" + name))
            lo, hi = r["boundsZUp"]
            self.assertAlmostEqual((hi[2] - lo[2]) / M / ko.ORDERS[name]["column"], 0.25, delta=0.002)
        r = pgm("evaluate", self.doc("corinthian-fluted"), "--set", "module=0.3")
        self.assertAlmostEqual(r["boundsZUp"][1][2], 20 * 0.3, delta=0.01)

    def test_bay_contact(self):
        """Capitals carry the entablature at both ends of every control, and the bay holds together."""
        corners = [(h, p) for h in (3.0, 6.0) for p in (2.5, 5.0)] + [(4.5, 3.5)]
        for name in ("doric", "ionic", "corinthian"):
            for h, p in corners:
                with self.subTest(name=name, height=h, pitch=p):
                    sets = ["--set", f"height={h}", "--set", f"pitch={p}"]
                    pil = pgm("evaluate", self.doc(f"bay-{name}-pilaster"), *sets)
                    ent = pgm("evaluate", self.doc(f"bay-{name}-ent"), *sets)
                    top, foot = pil["boundsZUp"][1][2], ent["boundsZUp"][0][2]
                    self.assertAlmostEqual(top, foot, delta=0.003)
                    self.assertAlmostEqual(foot, 0.8 * h, delta=0.003)          # column 4/5, entablature 1/5
                    self.assertAlmostEqual(ent["boundsZUp"][1][2], h, delta=0.003)
                    r = pgm("audit", self.doc(f"bay-{name}"), *sets)
                    self.assertTrue(r["passed"], r)
                    self.assertEqual(r["components"], 1, r)

    def test_blocks_follow_length(self):
        """Dentils, triglyphs and balusters are counted from the run length, not stretched."""
        for doc, ctl, lo, hi in (("bay-ionic-ent", "pitch", 2.5, 5.0), ("bay-doric-ent", "pitch", 2.5, 5.0),
                                 ("balustrade-controls", "length", 1.5, 8.0)):
            with self.subTest(doc):
                a = pgm("evaluate", self.doc(doc), "--set", f"{ctl}={lo}")["placedTriangles"]
                b = pgm("evaluate", self.doc(doc), "--set", f"{ctl}={hi}")["placedTriangles"]
                self.assertGreater(b - a, 100)
        r = pgm("evaluate", self.doc("column-height"), "--set", "height=6.0")
        self.assertAlmostEqual(r["boundsZUp"][1][2], 0.8 * 6.0, delta=0.01)
        r = pgm("evaluate", self.doc("block-height"), "--set", "height=2.5")
        self.assertAlmostEqual(r["boundsZUp"][1][2] - r["boundsZUp"][0][2], 2.5, delta=0.002)

    def test_sweep(self):
        r = pgm("sweep", KIT)
        self.assertTrue(r["passed"], r)
        r = pgm("audit", KIT, "--sweep")
        self.assertTrue(r["passed"], r)


def sheet(out):
    panels = [
        ("tuscan", "Tuscan, 14 modules", -90, 0), ("doric-fluted", "Doric, 20 arris flutes, 16 modules", -90, 0),
        ("ionic-fluted", "Ionic, 24 flutes, 18 modules", -90, 0), ("corinthian", "Corinthian, 20 modules", -90, 0),
        ("cap-doric", "Doric capital: annulets, echinus, abacus", -55, 20), ("cap-ionic", "Ionic capital: volutes, eggs", -55, 15),
        ("cap-corinthian", "Corinthian capital: leaves, helices", -90, 0), ("cap-composite", "Composite capital", -50, 15),
        ("ent-doric", "Doric entablature: triglyphs, mutules", -60, 15), ("ent-ionic", "Ionic entablature: dentils", -70, -10),
        ("ent-corinthian", "Corinthian entablature loop: modillions", -50, 25), ("portico", "Corinthian tetrastyle portico", -70, 8),
        ("cornice-loop", "mitred cornice round an L outline", -50, 35), ("balustrade", "balustrade with piers", -60, 20),
        ("window", "window: architrave, sill, key, pediment", -90, 0), ("pilaster-corinthian", "Corinthian pilaster (facing -x)", -150, 10),
    ]
    spec = {"title": "kitlib orders: Vignola's five orders, entablatures, mouldings (module = lower radius)",
            "width": 2400, "height": 2400, "layout": "grid", "columns": 4,
            "panels": [{"document": str(KIT / f"{d}.json"), "caption": c, "yaw": y, "pitch": p} for d, c, y, p in panels]}
    path = KIT / "sheet.json"
    path.write_text(json.dumps(spec, indent=1))
    print(json.dumps(pgm("sheet", path, "--out", out)))


if __name__ == "__main__":
    args = sys.argv[1:]
    out = args[args.index("--sheet") + 1] if "--sheet" in args else None
    keep = args[args.index("--keep") + 1] if "--keep" in args else None
    root = keep or tempfile.mkdtemp(prefix="kitlib-orders-")
    KIT = build(root)
    if out:
        sheet(out)
    if "--no-tests" not in args:
        unittest.main(argv=[sys.argv[0], "-v"])
