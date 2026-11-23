"""Tests of the kitlib part helpers: ladder, gear, rack, mesh, mesh_rack, ring_gear, gear_segment, mesh_internal,
ratchet, pawl, seat_pawls. Built by Opus 5.5 (medium), 2026-11-16.

Builds sample documents into a temporary kit and runs pgm on them: every part closed, manifold and
outward (pgm check), one component per part (pgm audit), meshed pairs free of crossings (the pair baked
into one prototype and checked with --intersections), triangle counts in budget, controls swept.

    PGM=/path/to/pgm python3 procgen/recipes/test_kitlib_helpers.py [--sheet /tmp/helpers-sheet.png]
        [--sheet2 /tmp/gears2-sheet.png] [--keep DIR]
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
from kitlib import C, Graph, Kit, PI, control, mesh, mesh_internal, mesh_rack, seat_pawls, segment_phase  # noqa: E402

PGM = os.environ.get("PGM", str(Path(__file__).resolve().parent.parent / "build-release" / "pgm"))
KIT = None                       # directory of the sample kit, made once
GEAR_BUDGET = {8: 1200, 12: 1400, 40: 3000, 72: 6000}       # triangles, solid / web / spokes alike
PART_BUDGET = {"ring-40": 2500, "ring-72": 4000, "ring-module-control": 4000, "segment-quadrant": 1500,
               "segment-internal": 1200, "ratchet-24": 600, "ratchet-24-hub-cw": 900, "ratchet-12": 500,
               "pawl": 200, "pawl-cw-solid": 150}


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

    H = {"height": control(4.2, 1.2, 9.0)}
    add("ladder-flat", lambda g: g.ladder("ladder", C("height"), slot="iron"), H)
    add("ladder-round", lambda g: g.ladder("ladder", C("height"), stringer="round", facing="-x", at=[0.3, 0, 0]), H)
    add("ladder-cage", lambda g: g.ladder("ladder", C("height"), cage=True), {"height": control(7.5, 1.2, 12.0)})
    add("ladder-cage-short", lambda g: g.ladder("ladder", 1.5, cage=True, brackets=None))
    add("ladder-tall", lambda g: g.ladder("ladder", 11.4, cage=True, facing=0.6))
    for n in (8, 12, 40, 72):
        add(f"gear-{n}", lambda g, n=n: g.gear("gear", n, 0.01, 0.03))
    add("gear-12-hub", lambda g: g.gear("gear", 12, 0.01, 0.03, hub=0.01))
    add("gear-40-web", lambda g: g.gear("gear", 40, 0.01, 0.025, body="web", hub=0.008))
    add("gear-40-spokes", lambda g: g.gear("gear", 40, 0.01, 0.025, body="spokes", holes=5, hub=0.01))
    add("gear-72-spokes", lambda g: g.gear("gear", 72, 0.01, 0.03, body="spokes", hub=0.012))
    add("gear-72-web", lambda g: g.gear("gear", 72, 0.01, 0.03, body="web", holes=8))
    add("gear-module-control", lambda g: g.gear("gear", 40, C("module"), C("module") * 3, body="spokes", hub=C("module")),
        {"module": control(0.01, 0.004, 0.05)})
    add("rack", lambda g: g.rack("rack", 16, 0.01, 0.03))

    def pair(g):
        a, ph = mesh(12, 40, 0.01, direction=0.4)
        g.gear("pinion", 12, 0.01, 0.03)
        g.gear("wheel", 40, 0.01, 0.03, body="spokes", at=[a * math.cos(0.4), a * math.sin(0.4), 0], phase=ph)
        return g.join("pair", "pinion", "wheel")
    add("pair-12-40", pair)

    def pair8(g):
        a, ph = mesh(8, 72, 0.01, phase1=0.13)
        g.gear("pinion", 8, 0.01, 0.03, phase=0.13)
        g.gear("wheel", 72, 0.01, 0.03, at=[a, 0, 0], phase=ph)
        return g.join("pair", "pinion", "wheel")
    add("pair-8-72", pair8)

    def train(g):                                  # three gears in a row, each phase from its neighbour
        a1, p1 = mesh(18, 30, 0.01)
        a2, p2 = mesh(30, 24, 0.01, direction=PI / 2, phase1=p1)
        g.gear("a", 18, 0.01, 0.03)
        g.gear("b", 30, 0.01, 0.03, at=[a1, 0, 0], phase=p1)
        g.gear("c", 24, 0.01, 0.03, at=[a1, a2, 0], phase=p2)
        return g.join("train", "a", "b", "c")
    add("train", train)

    def on_rack(g):
        c, ph = mesh_rack(12, 0.01, 0.2)
        g.rack("rack", 16, 0.01, 0.03)
        g.gear("pinion", 12, 0.01, 0.03, at=c, phase=ph)
        return g.join("pair", "rack", "pinion")
    add("pinion-on-rack", on_rack)

    def on_rack_x(g):                              # the pinion's place follows a control
        c, ph = mesh_rack(10, 0.01, C("x"))
        g.rack("rack", 16, 0.01, 0.03)
        g.gear("pinion", 10, 0.01, 0.03, at=c, phase=ph)
        return g.join("pair", "rack", "pinion")
    add("pinion-on-rack-x", on_rack_x, {"x": control(0.25, 0.05, 0.45)})

    # Ring gears, segments, ratchets, pawls.
    add("ring-40", lambda g: g.ring_gear("ring", 40, 0.01, 0.03))
    add("ring-72", lambda g: g.ring_gear("ring", 72, 0.01, 0.03, rim=3, phase=0.02))
    add("ring-module-control", lambda g: g.ring_gear("ring", 60, C("module"), C("module") * 3),
        {"module": control(0.01, 0.004, 0.05)})
    add("segment-quadrant", lambda g: g.gear_segment("seg", 64, 0.01, 0.03, start=0, end=PI / 2, inner=0.06))
    add("segment-internal", lambda g: g.gear_segment("seg", 60, 0.01, 0.03, start=0.3, count=12, internal=True))
    add("ratchet-24", lambda g: g.ratchet("wheel", 24, 0.12, 0.015, 0.03))
    add("ratchet-24-hub-cw", lambda g: g.ratchet("wheel", 24, 0.12, 0.015, 0.03, hub=0.01, free="cw", phase=0.1))
    add("ratchet-12", lambda g: g.ratchet("wheel", 12, C("radius"), 0.02, 0.025), {"radius": control(0.1, 0.07, 0.3)})
    add("pawl", lambda g: g.pawl("pawl", 0.1, 0.014, 0.03, beak=0.025))
    add("pawl-cw-solid", lambda g: g.pawl("pawl", 0.1, 0.014, 0.03, bore=0, free="cw"))

    def in_ring(n_ring, n_pin, d):
        def build(g):
            a, ph = mesh_internal(n_ring, n_pin, 0.01, direction=d, phase_ring=0.05)
            g.ring_gear("ring", n_ring, 0.01, 0.03, phase=0.05)
            g.gear("pinion", n_pin, 0.01, 0.03, at=[a * math.cos(d), a * math.sin(d), 0], phase=ph)
            return g.join("pair", "ring", "pinion")
        return build
    add("pinion-in-ring-72-18", in_ring(72, 18, 0.4))
    add("pinion-in-ring-40-12", in_ring(40, 12, 2.0))
    add("pinion-in-ring-60-48", in_ring(60, 48, 1.1))

    def on_segment(g):
        d = 0.7
        a, ph = mesh(64, 14, 0.01, direction=d, phase1=segment_phase(64, 0.0))
        g.gear_segment("seg", 64, 0.01, 0.03, start=0, end=PI / 2, inner=0.06)
        g.gear("pinion", 14, 0.01, 0.03, at=[a * math.cos(d), a * math.sin(d), 0], phase=ph)
        return g.join("pair", "seg", "pinion")
    add("pinion-on-segment", on_segment)

    def in_segment(g):
        d = 0.3 + 6 * 2 * PI / 60
        a, ph = mesh_internal(60, 16, 0.01, direction=d, phase_ring=segment_phase(60, 0.3))
        g.gear_segment("seg", 60, 0.01, 0.03, start=0.3, count=12, internal=True)
        g.gear("pinion", 16, 0.01, 0.03, at=[a * math.cos(d), a * math.sin(d), 0], phase=ph)
        return g.join("pair", "seg", "pinion")
    add("pinion-in-segment", in_segment)

    def pawled(free, count, phase):
        def build(g):
            g.ratchet("wheel", 24, 0.12, 0.015, 0.03, free=free, phase=phase, hub=0.01)
            places, kw = seat_pawls(24, 0.12, 0.015, 0.1, count=count, phase=phase, free=free)
            names = ["wheel"]
            for i, (at, rz) in enumerate(places):
                names.append(g.pawl(f"pawl{i}", face=0.03, at=at, rotation=rz, **kw))
            return g.join("set", *names)
        return build
    add("ratchet-pawls-2", pawled("ccw", 2, 0.0))
    add("ratchet-pawl-cw", pawled("cw", 1, 0.07))
    add("ratchet-pawls-3", pawled("ccw", 3, 0.2))
    return out


MESHED = ("pair-12-40", "pair-8-72", "train", "pinion-on-rack", "pinion-on-rack-x", "pinion-in-ring-72-18",
          "pinion-in-ring-40-12", "pinion-in-ring-60-48", "pinion-on-segment", "pinion-in-segment")
PAWLED = ("ratchet-pawls-2", "ratchet-pawl-cw", "ratchet-pawls-3")
PARTS = ("ring-", "segment-", "ratchet-", "pawl")


def build(directory):
    kit = Kit(directory, "helpers", "helpers", bake_leaves=True)
    for name, (controls, g, output) in samples().items():
        kit.module(name, controls, g, output, {})
    kit.close()
    return Path(directory) / "helpers"


class Helpers(unittest.TestCase):
    def doc(self, name):
        return KIT / f"{name}.json"

    def test_check_clean(self):
        for name in samples():
            with self.subTest(name):
                r = pgm("check", self.doc(name))
                self.assertTrue(r.get("passed") and r["clean"], r)
                self.assertEqual(r["openPrototypes"], 0, r)

    def test_parts_self_intersection_free(self):
        for name in samples():
            if name.startswith("gear") or name == "rack" or (name.startswith(PARTS) and name not in PAWLED):
                with self.subTest(name):
                    r = pgm("check", self.doc(name), "--intersections")
                    self.assertEqual(r["selfIntersectingPrototypes"], 0, r)

    def test_meshed_pairs_do_not_intersect(self):
        # Baking joins the pair into one prototype, so a tooth crossing its mate is a self-intersection.
        for name in MESHED + PAWLED:
            with self.subTest(name):
                r = pgm("check", self.doc(name), "--intersections")
                self.assertEqual(r["prototypes"], 1, r)
                self.assertEqual(r["selfIntersectingPrototypes"], 0, r)

    def test_mesh_is_tight(self):
        # Play, not a gap: moved 0.6 module closer, the 12/40 pair must collide.
        a, ph = mesh(12, 40, 0.01)
        g = Graph()
        g.gear("pinion", 12, 0.01, 0.03)
        g.gear("wheel", 40, 0.01, 0.03, at=[a.v - 0.006, 0, 0], phase=ph)
        kit = Kit(KIT.parent, "tight", "tight")
        kit.module("tight", {}, g, g.join("pair", "pinion", "wheel"), {})
        kit.close()
        r = pgm("check", KIT.parent / "tight" / "tight.json", "--intersections")
        self.assertEqual(r["selfIntersectingPrototypes"], 1, r)

    def test_one_component_each(self):
        for name in samples():
            if name in MESHED or name in PAWLED or name == "train":
                continue
            with self.subTest(name):
                r = pgm("audit", self.doc(name))
                self.assertTrue(r["passed"], r)
                self.assertEqual(r["components"], 1, r)

    def test_triangle_budget(self):
        for name in samples():
            if not name.startswith("gear-") or name == "gear-module-control":
                continue
            n = int(name.split("-")[1])
            with self.subTest(name):
                r = pgm("evaluate", self.doc(name))
                self.assertLessEqual(r["placedTriangles"], GEAR_BUDGET[n], r)
                print(f"  {name}: {r['placedTriangles']} triangles", file=sys.stderr)

    def tight(self, name, build):
        g = Graph()
        out = build(g)
        kit = Kit(KIT.parent, name, name)
        kit.module(name, {}, g, out, {})
        kit.close()
        return pgm("check", KIT.parent / name / f"{name}.json", "--intersections")

    def test_internal_mesh_is_tight(self):
        # Moved 0.6 module out from the ring's centre (into the ring's teeth), the pinion must collide.
        for n_ring, n_pin in ((72, 18), (40, 12)):
            with self.subTest(f"{n_ring}-{n_pin}"):
                def build(g):
                    a, ph = mesh_internal(n_ring, n_pin, 0.01)
                    g.ring_gear("ring", n_ring, 0.01, 0.03)
                    g.gear("pinion", n_pin, 0.01, 0.03, at=[a.v + 0.006, 0, 0], phase=ph)
                    return g.join("pair", "ring", "pinion")
                r = self.tight(f"tight-ring-{n_ring}", build)
                self.assertEqual(r["selfIntersectingPrototypes"], 1, r)

    def test_segment_mesh_is_tight(self):
        def ext(g):
            d = 0.7
            a, ph = mesh(64, 14, 0.01, direction=d, phase1=segment_phase(64, 0.0))
            a = a.v - 0.006
            g.gear_segment("seg", 64, 0.01, 0.03, start=0, end=PI / 2, inner=0.06)
            g.gear("pinion", 14, 0.01, 0.03, at=[a * math.cos(d), a * math.sin(d), 0], phase=ph)
            return g.join("pair", "seg", "pinion")

        def internal(g):
            d = 0.3 + 6 * 2 * PI / 60
            a, ph = mesh_internal(60, 16, 0.01, direction=d, phase_ring=segment_phase(60, 0.3))
            a = a.v + 0.006
            g.gear_segment("seg", 60, 0.01, 0.03, start=0.3, count=12, internal=True)
            g.gear("pinion", 16, 0.01, 0.03, at=[a * math.cos(d), a * math.sin(d), 0], phase=ph)
            return g.join("pair", "seg", "pinion")
        for name, build in (("tight-seg-ext", ext), ("tight-seg-int", internal)):
            with self.subTest(name):
                self.assertEqual(self.tight(name, build)["selfIntersectingPrototypes"], 1)

    def test_pawls_seat(self):
        # Seated pawls touch their teeth (one piece within the audit's 3 mm) and cross nothing; pushed
        # 4 mm further into the tooth they collide.
        for name in PAWLED:
            with self.subTest(name):
                r = pgm("audit", self.doc(name))
                self.assertEqual(r["components"], 1, r)

        def deep(g):
            g.ratchet("wheel", 24, 0.12, 0.015, 0.03)
            places, kw = seat_pawls(24, 0.12, 0.015, 0.1, clearance=0.001)
            (x, y, z), rz = places[0]
            g.pawl("pawl", face=0.03, at=[x + 0.004 * math.cos(rz), y + 0.004 * math.sin(rz), z], rotation=rz, **kw)
            return g.join("set", "wheel", "pawl")
        self.assertEqual(self.tight("tight-pawl", deep)["selfIntersectingPrototypes"], 1)

    def test_part_triangle_budget(self):
        for name, budget in PART_BUDGET.items():
            with self.subTest(name):
                r = pgm("evaluate", self.doc(name))
                self.assertLessEqual(r["placedTriangles"], budget, r)
                print(f"  {name}: {r['placedTriangles']} triangles", file=sys.stderr)

    def test_ladder_rungs_follow_height(self):
        lo = pgm("evaluate", self.doc("ladder-flat"), "--set", "height=1.2")
        hi = pgm("evaluate", self.doc("ladder-flat"), "--set", "height=9")
        self.assertGreater(hi["placedTriangles"], lo["placedTriangles"] * 3, (lo, hi))
        self.assertAlmostEqual(hi["boundsZUp"][1][2], 9 + 1.1, delta=0.02)    # stringers carried on as hand-holds

    def test_sweep(self):
        r = pgm("sweep", KIT)
        self.assertTrue(r["passed"], r)
        r = pgm("audit", KIT, "--sweep")
        bad = [f for f in r.get("failures", []) if not any(m in f["document"] for m in MESHED)]
        self.assertFalse(bad, r)


def sheet2(out):
    panels = [
        ("ring-72", "72-tooth internal ring gear, rim 3 m", 0, 89),
        ("pinion-in-ring-72-18", "18 inside 72, phased by mesh_internal()", 0, 89),
        ("pinion-in-ring-60-48", "48 inside 60 (12 teeth apart)", 0, 89),
        ("segment-quadrant", "quadrant: 64-tooth segment, 0 to 90 deg", -30, 55),
        ("pinion-on-segment", "14-tooth pinion on the quadrant", 0, 89),
        ("pinion-in-segment", "16 in a 12-tooth internal segment", 0, 89),
        ("ratchet-pawls-2", "24-tooth ratchet, two seated pawls", 0, 89),
        ("ratchet-pawl-cw", "mirrored (free cw), one pawl, hub", -30, 55),
    ]
    spec = {"title": "kitlib helpers: ring gear, segments, ratchet and pawl", "width": 2000, "height": 1000, "layout": "grid",
            "columns": 4, "panels": [{"document": str(KIT / f"{d}.json"), "caption": c, "yaw": y, "pitch": p} for d, c, y, p in panels]}
    path = KIT / "sheet2.json"
    path.write_text(json.dumps(spec, indent=1))
    print(json.dumps(pgm("sheet", path, "--out", out)))


def sheet(out):
    panels = [
        ("ladder-cage", "ladder, cage from 2.4 m (height 7.5)", -40, 10),
        ("ladder-round", "round stringers, brackets", -130, 8),
        ("gear-12-hub", "12 teeth, undercut, hub boss", 0, 70),
        ("gear-40-web", "40 teeth, web with lightening holes", -30, 55),
        ("gear-72-spokes", "72 teeth, spokes", 0, 89),
        ("pair-12-40", "12 on 40, phased by mesh()", 0, 89),
        ("pinion-on-rack", "12-tooth pinion on a rack", 0, 89),
        ("train", "18 / 30 / 24 train", 0, 89),
    ]
    spec = {"title": "kitlib helpers: ladder, gear, rack, mesh", "width": 2000, "height": 1000, "layout": "grid", "columns": 4,
            "panels": [{"document": str(KIT / f"{d}.json"), "caption": c, "yaw": y, "pitch": p} for d, c, y, p in panels]}
    path = KIT / "sheet.json"
    path.write_text(json.dumps(spec, indent=1))
    print(json.dumps(pgm("sheet", path, "--out", out)))


if __name__ == "__main__":
    args = sys.argv[1:]
    out = args[args.index("--sheet") + 1] if "--sheet" in args else None
    keep = args[args.index("--keep") + 1] if "--keep" in args else None
    root = keep or tempfile.mkdtemp(prefix="kitlib-helpers-")
    KIT = build(root)
    if out:
        sheet(out)
    if "--sheet2" in args:
        sheet2(args[args.index("--sheet2") + 1])
    unittest.main(argv=[sys.argv[0], "-v"])
