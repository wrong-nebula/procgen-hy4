"""Tests of the kitlib masonry helpers. Built by Opus 5.5 (medium), 2026-11-16.

Builds sample documents into a temporary kit and runs pgm on them: every sample closed, manifold and
outward (pgm check), stone-by-stone samples free of intersections (check --intersections), every sample
one component (pgm audit: an arch ring and an ashlar wall each hold together as one piece), voussoir
joints radial to their arc centres (in arch_geometry and in the exported mesh), triangle budgets, the
X controls swept.

Module controls (Opus 5.5 medium, 2026-11-17): samples whose spans, rises, wall sizes and openings follow
`span` and `level` controls (CTL) pass `pgm sweep` and `pgm audit --sweep` (one component at both ends of
every control), their voussoir joints stay radial at the ends of the range, no face has more than 128
corners (a tall quoined wall and a 20 m detail ring used to), joints="material" emits the bond slots with
UVs in metres (the wall's width and height, read from the exported pack's mesh UV ranges: pgm's OBJ only
carries construction charts), and bond_pattern() cells tile without overlap and match the geometry.

    PGM=/path/to/pgm python3 procgen/recipes/test_kitlib_masonry.py [--sheet /tmp/masonry-sheet.png] [--keep DIR]
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
import struct  # noqa: E402

import kitlib_masonry as km  # noqa: E402
from kitlib import C, Graph, Kit, control  # noqa: E402
from kitlib_masonry import BOND_SLOTS, arch_geometry, bond_pattern, courses_for  # noqa: E402

PGM = os.environ.get("PGM", str(Path(__file__).resolve().parent.parent / "build-release" / "pgm"))
KIT = None

# Triangle budgets (placed triangles). Stone-by-stone walls are the expensive ones; their detail twins are cheap.
BUDGET = {
    "arch-semicircular": 700, "arch-segmental-ls": 1200, "arch-three-centred": 900, "arch-pointed": 900,
    "arch-flat": 400, "arch-stepped": 1200, "arch-detail": 500,
    "ashlar-door": 3500, "ashlar-banded": 5000, "ashlar-vermiculated": 3500, "ashlar-detail": 800, "ashlar-depth-control": 3500,
    "brick-stretcher": 7000, "brick-english": 10000, "brick-flemish": 9000, "brick-header": 9000, "brick-detail": 900,
    "rubble-random": 6000, "rubble-squared": 5000, "rubble-toe": 6000,
    "coping-saddleback": 300, "coping-half-round": 700, "coping-bullnose": 600, "string-weathered": 200, "string-bead": 500,
    "cutwater-pointed": 120, "cutwater-rounded": 120,
    "param-viaduct-bay": 6000, "param-bay-detail": 1600, "param-bay-material": 2600, "param-front": 4000,
    "param-brick": 14000, "param-brick-material": 1500,
}

CTL = {"span": control(8.0, 6.0, 12.0), "level": control(9.0, 6.0, 14.0)}
SPRING = C("level") - C("span") / 2 - 1.2           # the bay arch's springing: crown about 0.75 under the top


def bay_wall(g, joints="gap"):
    return g.ashlar_wall("wall", C("span") + 2.4, C("level"), controls=CTL, quoins="both", quoin_proud=0.02, joints=joints,
                         openings=[{"kind": "arch", "x": 0, "width": C("span"), "springing": SPRING, "impost": True,
                                    "key_proud": 0.04}])


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

    add("arch-semicircular", lambda g: g.arch_ring("arch", 4.0, key_proud=0.05, key_rise=0.1, impost=(0.3, 0.05)))
    add("arch-segmental-ls", lambda g: g.arch_ring("arch", 6.0, "segmental", rise=1.6, voussoir=0.5, length=0.62, short=0.42,
                                                   fill_notches=True, slot="chipped-limestone", key_slot="stone"))
    add("arch-three-centred", lambda g: g.arch_ring("arch", 5.0, "three-centred", rise=1.6, rustic="chamfered", chamfer=0.025))
    add("arch-pointed", lambda g: g.arch_ring("arch", 3.0, "pointed", key=False, voussoir=0.35, length=0.4))
    add("arch-flat", lambda g: g.arch_ring("arch", 1.4, "flat", voussoir=0.2, extrados="level", level=0.42, key_proud=0.03))
    add("arch-stepped", lambda g: g.arch_ring("arch", 3.0, extrados="stepped", course=0.36, length=0.5, rustic="chamfered"))
    add("arch-detail", lambda g: g.arch_ring("arch", 4.0, "segmental", rise=1.2, short=0.35, joints="detail", key_proud=0.04))

    door = [{"kind": "arch", "x": 0.0, "width": 2.4, "springing": 2.1, "impost": True, "key_proud": 0.04},
            {"kind": "rect", "x": -2.6, "width": 1.0, "sill": 1.05, "head": 2.45, "sill_stone": True},
            {"kind": "rect", "x": 2.6, "width": 1.0, "sill": 1.05, "head": 2.45, "lintel": "flat", "voussoir": 0.22}]
    add("ashlar-door", lambda g: g.ashlar_wall("wall", 8.0, 4.9, openings=door, quoins="both", quoin_proud=0.03))
    add("ashlar-banded", lambda g: g.ashlar_wall("wall", 8.0, 4.9, course=[0.5, 0.45, 0.4, 0.35], rustic="banded", chamfer=0.03,
                                                 openings=[{"kind": "arch", "x": 0, "width": 2.6, "springing": 1.9,
                                                            "extrados": "stepped", "length": 0.55}], vary=0.2))
    add("ashlar-vermiculated", lambda g: g.ashlar_wall("wall", 4.0, 2.4, course=0.4, block=0.9, rustic="vermiculated", chamfer=0.025,
                                                       quoins="left", quoin_rustic="chamfered"))
    add("ashlar-detail", lambda g: g.ashlar_wall("wall", 8.0, 4.9, openings=door, quoins="both", quoin_proud=0.03, joints="detail"))
    add("ashlar-depth-control", lambda g: g.ashlar_wall("wall", 5.0, 3.5, depth=C("depth"), at=[0, 0, C("lift")],
                                                        openings=[{"kind": "arch", "x": 0, "width": 1.6, "springing": 1.4,
                                                                   "arch": "segmental", "rise": 0.5}]),
        {"depth": control(0.6, 0.35, 1.2), "lift": control(0.0, 0.0, 2.0)})

    bopen = [{"kind": "arch", "x": -1.0, "width": 1.2, "sill": 0.6, "springing": 1.5},
             {"kind": "rect", "x": 1.1, "width": 1.0, "sill": 0.6, "head": 1.95, "lintel": "flat", "arch_height": 0.3}]
    add("brick-stretcher", lambda g: g.brick_wall("leaf", 4.0, 2.5, openings=bopen))
    add("brick-english", lambda g: g.brick_wall("leaf", 4.0, 2.5, bond="english", soldiers=(31,),
                                                openings=[{"kind": "rect", "x": 0, "width": 1.2, "sill": 0.6, "head": 1.8, "lintel": "soldier"}]))
    add("brick-flemish", lambda g: g.brick_wall("leaf", 4.0, 2.5, bond="flemish", openings=bopen, slot="weathered-brick"))
    add("brick-header", lambda g: g.brick_wall("leaf", 3.0, 2.0, bond="header",
                                               openings=[{"kind": "arch", "x": 0, "width": 1.0, "sill": 0.3, "springing": 1.2,
                                                          "arch": "segmental", "rise": 0.25}]))
    add("brick-detail", lambda g: g.brick_wall("leaf", 4.0, 2.5, bond="flemish", openings=bopen, joints="detail"))

    add("rubble-random", lambda g: g.rubble_wall("wall", 4.0, 1.5))
    add("rubble-squared", lambda g: g.rubble_wall("wall", 4.0, 1.5, kind="squared", stone=0.3))
    add("rubble-toe", lambda g: g.rubble_wall("toe", 4.0, 1.2, batter=0.25, depth=0.9, slot="pier-ashlar"))

    add("coping-saddleback", lambda g: g.coping("cope", 4.0, 0.45, "saddleback"))
    add("coping-half-round", lambda g: g.coping("cope", 4.0, 0.35, "half-round", height=0.2))
    add("coping-bullnose", lambda g: g.coping("cope", 4.0, 0.45, "bullnose"))
    add("string-weathered", lambda g: g.string_course("band", 4.0))
    add("string-bead", lambda g: g.string_course("band", 4.0, "bead", projection=0.06, height=0.14))
    add("cutwater-pointed", lambda g: g.cutwater_cap("cap", 2.4))
    add("cutwater-rounded", lambda g: g.cutwater_cap("cap", 2.4, kind="rounded"))

    def pier(g):                                   # a wall with coping, string course and an arch: a joined assembly
        w = g.ashlar_wall("wall", 6.0, 3.5, course=0.35, depth=0.6,
                          openings=[{"kind": "arch", "x": 0, "width": 2.8, "springing": 1.05, "arch": "segmental", "rise": 0.9,
                                     "short": 0.38, "length": 0.55}])
        s = g.string_course("band", 6.0, at=[0, -0.0005, 2.45], embed=0.0)      # standing on the face
        c = g.coping("cope", 6.0, 0.6, "weathered", at=[0, 0, 3.5005])
        return g.join("pier", w, s, c)
    add("assembly-wall-band-coping", pier)

    # ---- following module controls (span 6-12, level 6-14)
    def viaduct(g):                                # a bay face and the arch ring of the barrel behind it
        w = bay_wall(g)
        r = g.arch_ring("ring", C("span"), at=[0, 0.501, SPRING], depth=0.45, length=0.5, controls=CTL)
        c = g.coping("cope", C("span") + 2.4, 0.5, "weathered", at=[0, 0, C("level") + 0.0005], controls=CTL)
        return g.join("bay", w, r, c)
    add("param-viaduct-bay", viaduct, CTL)
    add("param-bay-detail", lambda g: bay_wall(g, "detail"), CTL)
    add("param-bay-material", lambda g: bay_wall(g, "material"), CTL)
    add("param-arch-semicircular", lambda g: g.arch_ring("arch", C("span"), key_proud=0.05, key_rise=0.1, impost=(0.3, 0.05),
                                                         controls=CTL), CTL)
    add("param-arch-segmental", lambda g: g.arch_ring("arch", C("span"), "segmental", rise=C("span") * 0.25, length=0.6,
                                                      short=0.42, fill_notches=True, controls=CTL), CTL)
    add("param-arch-segmental-rise", lambda g: g.arch_ring("arch", C("span"), "segmental", rise=C("level") * 0.2,
                                                           controls=CTL), CTL)
    add("param-arch-pointed", lambda g: g.arch_ring("arch", C("span"), "pointed", key=False, controls=CTL), CTL)
    add("param-arch-three-centred", lambda g: g.arch_ring("arch", C("span"), "three-centred", rise=C("span") * 0.3,
                                                          rustic="chamfered", controls=CTL), CTL)
    add("param-arch-flat", lambda g: g.arch_ring("arch", C("span") * 0.2, "flat", voussoir=0.2, extrados="level", level=0.45,
                                                 controls=CTL), CTL)
    add("param-front", lambda g: g.ashlar_wall("wall", C("span"), C("level") * 0.5, rustic="chamfered", controls=CTL, openings=[
        {"kind": "rect", "x": C("span") * -0.25, "width": 1.0, "sill": 1.0, "head": C("level") * 0.3, "sill_stone": True},
        {"kind": "rect", "x": C("span") * 0.25, "width": C("span") * 0.12, "sill": 1.0, "head": 2.4, "lintel": "flat"}]), CTL)
    brick_open = [{"kind": "arch", "x": C("span") * -0.1 - 0.5, "width": 1.2, "sill": 0.6, "springing": C("level") * 0.2,
                   "arch": "segmental", "rise": 0.3},
                  {"kind": "rect", "x": C("span") * 0.1 + 0.5, "width": 1.0, "sill": 0.6, "head": C("level") * 0.2 + 0.2, "lintel": "soldier"}]
    add("param-brick", lambda g: g.brick_wall("leaf", C("span") * 0.5 + 2, C("level") * 0.2 + 0.9, bond="flemish",
                                              openings=brick_open, controls=CTL), CTL)
    add("param-brick-material", lambda g: g.brick_wall("leaf", C("span") * 0.5 + 2, C("level") * 0.2 + 0.9, bond="english",
                                                       openings=brick_open, joints="material", controls=CTL), CTL)
    add("param-rubble", lambda g: g.rubble_wall("toe", C("span") * 0.5, C("level") * 0.15, batter=0.2, controls=CTL), CTL)
    add("param-string", lambda g: g.string_course("band", C("span"), controls=CTL), CTL)

    # ---- faces over 128 corners (split)
    add("tall-quoined", lambda g: g.ashlar_wall("wall", 10.0, 12.0, course=0.3, quoins="both",
                                                openings=[{"kind": "arch", "x": 0, "width": 6.0, "springing": 4.0}]))
    add("tall-quoined-detail", lambda g: g.ashlar_wall("wall", 10.0, 12.0, course=0.3, quoins="both", joints="detail",
                                                       openings=[{"kind": "arch", "x": 0, "width": 6.0, "springing": 4.0}]))
    add("ring-20m-detail", lambda g: g.arch_ring("arch", 20.0, length=0.9, joints="detail"))
    return out


# Samples whose stones and core are all a gap apart (no part overlaps another in its baked prototype).
NON_OVERLAPPING = [n for n in BUDGET if not n.startswith(("string", "param-"))] + ["assembly-wall-band-coping", "param-front",
                                                                                   "tall-quoined", "param-arch-segmental"]


def pack_meshes(path):
    """(slot name, uv min, uv extent) of every mesh in an EMSH pack (the mesh and submesh sections and the tool
    metadata, from src/emesh.cpp's layout)."""
    b = Path(path).read_bytes()
    count, table = struct.unpack_from("<II", b, 16)
    sec = {}
    for i in range(count):
        kind, codec, esize, off, size, raw, n = struct.unpack_from("<IHHIIII", b, table + 24 * i)
        sec[kind] = (off, size, n)
    meta = json.loads(b[sec[0x8001][0]:sec[0x8001][0] + sec[0x8001][1]].decode())
    names = {s["id"]: s["name"] for s in meta["slots"]}
    off, _, n = sec[1]
    out = []
    for i in range(n):
        uvmin = struct.unpack_from("<2f", b, off + 72 * i + 32)
        uvscale = struct.unpack_from("<2f", b, off + 72 * i + 40)
        first = struct.unpack_from("<I", b, off + 72 * i + 48)[0]
        mat = struct.unpack_from("<I", b, sec[2][0] + 16 * first)[0]
        out.append((names[mat], uvmin, uvscale))
    return out


def build(directory):
    kit = Kit(directory, "masonry", "masonry", bake_leaves=True)
    for name, (controls, g, output) in samples().items():
        kit.module(name, controls, g, output, {})
    kit.close()
    return Path(directory) / "masonry"


def obj_vertices(doc):
    out = Path(doc).with_suffix(".obj")
    r = pgm("export", doc, "--obj", out, "--zup")
    assert r.get("passed"), r
    return [tuple(map(float, ln.split()[1:4])) for ln in out.read_text().splitlines() if ln.startswith("v ")]


class Geometry(unittest.TestCase):
    def test_joints_radial(self):
        cases = [("semicircular", {}), ("segmental", {"rise": 1.0}), ("three-centred", {"rise": 1.2}),
                 ("pointed", {"key": False}), ("pointed", {}), ("flat", {"extrados": "level", "level": 0.4})]
        for kind, kw in cases:
            with self.subTest(kind=kind, **kw):
                ag = arch_geometry(4.0 if kind != "flat" else 1.4, kind, voussoir=0.3, **kw)
                centred = 0
                for j in ag["joints"]:
                    a, n, c = j["a"], j["n"], j["centre"]
                    if c is None:                            # pointed apex: the vertical joint
                        self.assertAlmostEqual(n[0], 0.0, places=9)
                        continue
                    r = (a[0] - c[0], a[1] - c[1])
                    rl = math.hypot(*r)
                    self.assertLess(abs(r[0] * n[1] - r[1] * n[0]) / rl, 1e-9)   # along the radius
                    self.assertGreater(r[0] * n[0] + r[1] * n[1], 0)             # outward
                    centred += 1
                self.assertGreater(centred, 2)
                if kind == "three-centred":
                    self.assertEqual(len({j["centre"] for j in ag["joints"]}), 3)

    def test_symmetric_and_keyed(self):
        ag = arch_geometry(5.0, "segmental", rise=1.4, short=0.3, length=0.5)
        self.assertEqual(ag["count"] % 2, 1)
        for j, k in zip(ag["joints"], reversed(ag["joints"])):
            self.assertAlmostEqual(j["a"][0], -k["a"][0], places=9)
            self.assertAlmostEqual(j["a"][1], k["a"][1], places=9)
        self.assertEqual(sum(s["kind"] == "key" for s in ag["stones"]), 1)
        self.assertEqual(sum(s["kind"] == "skewback" for s in ag["stones"]), 2)

    def test_courses(self):
        self.assertEqual(len(courses_for(3.0, 0.35)), 10)
        zs = courses_for(2.0, [0.5, 0.4])
        self.assertAlmostEqual(zs[-1], 2.0)
        self.assertTrue(all(b > a for a, b in zip(zs, zs[1:])))


class Masonry(unittest.TestCase):
    def doc(self, name):
        return KIT / f"{name}.json"

    def test_check_clean(self):
        for name in samples():
            with self.subTest(name):
                r = pgm("check", self.doc(name))
                self.assertTrue(r.get("passed") and r["clean"], r)
                self.assertEqual(r["openPrototypes"], 0, r)

    def test_no_intersections(self):
        for name in NON_OVERLAPPING:
            with self.subTest(name):
                r = pgm("check", self.doc(name), "--intersections")
                self.assertEqual(r["selfIntersectingPrototypes"], 0, r)

    def test_one_component(self):
        for name in samples():
            with self.subTest(name):
                r = pgm("audit", self.doc(name))
                self.assertTrue(r["passed"], r)
                self.assertEqual(r["components"], 1, r)
        r = pgm("audit", self.doc("arch-semicircular"))
        self.assertGreater(r["islands"], 10)                 # stone by stone, held together through their joints
        r = pgm("audit", self.doc("ashlar-door"))
        self.assertGreater(r["islands"], 100)

    def test_mesh_joints_radial(self):
        """Every internal joint of the semicircle shows as two faces a gap apart along the radius."""
        vs = obj_vertices(self.doc("arch-semicircular"))
        face = [(x, z) for x, y, z in vs if abs(y) < 1e-5 or abs(y + 0.05) < 1e-5]   # the face and the proud key's face
        ag = arch_geometry(4.0, key_rise=0.1)
        for j in ag["joints"][1:-1]:
            a, n = j["a"], j["n"]
            sides = {-1: 0, 1: 0}
            for p in face:
                d = (p[0] - a[0], p[1] - a[1])
                along, off = d[0] * n[0] + d[1] * n[1], d[0] * n[1] - d[1] * n[0]
                if -0.01 < along < j["length"] + 0.01 and abs(abs(off) - 0.001) < 2e-4:
                    sides[1 if off > 0 else -1] += 1
            self.assertGreaterEqual(min(sides.values()), 2, (j, sides))

    def test_triangle_budget(self):
        for name, cap in BUDGET.items():
            with self.subTest(name):
                r = pgm("evaluate", self.doc(name))
                self.assertLessEqual(r["placedTriangles"], cap, r)
                print(f"  {name}: {r['placedTriangles']} triangles, {r.get('placedDetails', 0)} details", file=sys.stderr)

    def test_detail_is_cheap(self):
        geo = pgm("evaluate", self.doc("ashlar-door"))["placedTriangles"]
        det = pgm("evaluate", self.doc("ashlar-detail"))
        self.assertLess(det["placedTriangles"] * 3, geo)
        self.assertGreater(det["placedDetails"], 100)

    def test_controls(self):
        lo = pgm("evaluate", self.doc("ashlar-depth-control"), "--set", "depth=0.35", "--set", "lift=2")
        hi = pgm("evaluate", self.doc("ashlar-depth-control"), "--set", "depth=1.2")
        self.assertAlmostEqual(lo["boundsZUp"][1][1] - lo["boundsZUp"][0][1], 0.35, delta=1e-3)
        self.assertAlmostEqual(hi["boundsZUp"][1][1] - hi["boundsZUp"][0][1], 1.2, delta=1e-3)
        self.assertAlmostEqual(lo["boundsZUp"][0][2], 2.0, delta=1e-3)

    def test_batter(self):
        r = pgm("evaluate", self.doc("rubble-toe"))
        self.assertLess(r["boundsZUp"][0][1], -1.2 * 0.25 + 0.01)

    def test_sweep(self):
        r = pgm("sweep", KIT)
        self.assertTrue(r["passed"], r)
        r = pgm("audit", KIT, "--sweep")
        self.assertFalse(r.get("failures"), r)

    def test_controls_one_piece(self):
        """The control-following samples hold together at both ends of span and level."""
        for name in [n for n in samples() if n.startswith("param-")]:
            for k, c in CTL.items():
                for v in (c["min"], c["max"]):
                    with self.subTest(name, **{k: v}):
                        r = pgm("audit", self.doc(name), "--set", f"{k}={v}")
                        self.assertTrue(r["passed"] and r["components"] == 1, r)
                        r = pgm("check", self.doc(name), "--set", f"{k}={v}")
                        self.assertTrue(r.get("passed") and r["clean"], r)

    def test_controls_follow(self):
        """The bay is span + 2.4 wide and level high at every value."""
        for span, level in ((6, 9), (12, 9), (8, 6), (8, 14)):
            r = pgm("evaluate", self.doc("param-bay-material"), "--set", f"span={span}", "--set", f"level={level}")
            (x0, _, z0), (x1, _, z1) = r["boundsZUp"]
            self.assertAlmostEqual(x1 - x0, span + 2.4, delta=1e-3)
            self.assertAlmostEqual(z1 - z0, level, delta=1e-3)

    def test_face_corner_limit(self):
        for name in samples():
            with self.subTest(name):
                doc = json.loads(self.doc(name).read_text())
                for n in doc["nodes"]:
                    if n["type"] == "surface":
                        for f in n["options"]["faces"]:
                            self.assertLessEqual(sum(len(lp) for lp in f) if isinstance(f[0], list) else len(f), 128)

    def test_material_mode(self):
        size = {"param-bay-material": lambda sp, lv: (sp + 2.4, lv), "param-brick-material": lambda sp, lv: (sp * 0.5 + 2, lv * 0.2 + 0.9)}
        for name, bond in (("param-bay-material", "ashlar-coursed"), ("param-brick-material", "brick-english")):
            for sets, (w, h) in (((), size[name](8, 9)), (("span=12", "level=14"), size[name](12, 14)),
                                 (("span=6", "level=6"), size[name](6, 6))):
                with self.subTest(name, sets=sets):
                    out = KIT / f"{name}.emesh"
                    args = [x for st in sets for x in ("--set", st)]
                    r = pgm("export", self.doc(name), "--emesh", out, "--zup", *args)
                    self.assertTrue(r.get("passed"), r)
                    meshes = pack_meshes(out)
                    face = [m for m in meshes if m[0] == bond]
                    self.assertEqual(len(face), 1, meshes)
                    self.assertTrue(all(m[0] in (bond, "stone", "brick") for m in meshes), meshes)
                    (u0, v0), (du, dv) = face[0][1], face[0][2]
                    self.assertAlmostEqual(du, w, delta=2e-3)        # metres: u = x across the wall, v = z up it
                    self.assertAlmostEqual(dv, h, delta=2e-3)
                    self.assertAlmostEqual(u0, -w / 2, delta=2e-3)
                    self.assertAlmostEqual(v0, 0.0, delta=2e-3)


class Controls(unittest.TestCase):
    def test_joints_radial_at_every_value(self):
        """Arches built from expressions: every joint along the radius from its centre at both ends of the range."""
        cases = [("semicircular", {}), ("segmental", {"rise": C("span") * 0.25}), ("segmental", {"rise": C("level") * 0.2}),
                 ("pointed", {}), ("three-centred", {"rise": C("span") * 0.3})]
        for kind, kw in cases:
            ag = arch_geometry(km._lift(C("span"), CTL), kind, voussoir=0.4,
                               **{k: km._lift(v, CTL) for k, v in kw.items()})
            for vals in ({"span": 6.0, "level": 9.0}, {"span": 12.0, "level": 9.0}, {"span": 8.0, "level": 14.0}):
                with self.subTest(kind=kind, **vals):
                    ev = lambda x: km.evaluate(x, vals)
                    for j in ag["joints"]:
                        if j["centre"] is None:
                            continue
                        a = [ev(c) for c in j["a"]]
                        n = [ev(c) for c in j["n"]]
                        c = [ev(q) for q in j["centre"]]
                        r = (a[0] - c[0], a[1] - c[1])
                        self.assertLess(abs(r[0] * n[1] - r[1] * n[0]) / math.hypot(*r), 1e-9)
                        self.assertAlmostEqual(math.hypot(*n), 1.0, places=9)
                    if kind == "semicircular":                 # the springings sit on the span
                        self.assertAlmostEqual(ev(ag["joints"][0]["a"][0]), vals["span"] / 2, places=9)

    def test_counts_need_defaults(self):
        with self.assertRaises(AssertionError):
            Graph().arch_ring("a", C("span"))                   # no controls: the stone count has no default

    def test_bond_patterns(self):
        for bond in BOND_SLOTS:
            with self.subTest(bond):
                p = bond_pattern(bond)
                self.assertEqual(p["slot"], bond)
                w, h = p["cell"]
                j = p["joint"]
                # the units plus their joints fill the cell exactly once
                area = sum((x1 - x0 + j) * (z1 - z0 + j) for x0, z0, x1, z1, _ in p["units"])
                self.assertAlmostEqual(area, w * h, places=9)
                for r in p["rows"]:
                    self.assertTrue(all(0 <= c < w for c in r["cuts"]))
        self.assertEqual(sorted(k for *_, k in bond_pattern("flemish")["units"]), ["header", "header", "stretcher", "stretcher"])
        self.assertEqual(sorted(k for *_, k in bond_pattern("english")["units"]), ["header", "header", "stretcher"])
        # the geometry's head joints are the pattern's, repeated from x = 0
        for bond in ("stretcher", "english", "flemish", "header"):
            p = bond_pattern(bond)
            for k in range(4):
                cuts = km._bond_cuts(bond, k, -3.0, 3.0, (0.215, 0.1025), 0.01)
                row = p["rows"][k % len(p["rows"])]["cuts"]
                for c in cuts:
                    self.assertTrue(any(abs((c - x) / p["cell"][0] - round((c - x) / p["cell"][0])) < 1e-9 for x in row), (bond, k, c))


def sheet(out):
    panels = [
        ("arch-semicircular", "semicircle: key proud, imposts", -25, 8),
        ("arch-segmental-ls", "segmental, long-and-short, skewbacks", -25, 8),
        ("arch-three-centred", "three-centred, chamfered", -20, 10),
        ("arch-pointed", "pointed, apex joint", -25, 8),
        ("arch-stepped", "stepped extrados", -20, 10),
        ("arch-flat", "flat (jack) arch", -20, 10),
        ("ashlar-door", "ashlar: arch door, sill, jack arch, quoins", -20, 8),
        ("ashlar-banded", "banded rustication, stepped arch", -20, 8),
        ("ashlar-vermiculated", "vermiculated, quoins", -25, 10),
        ("ashlar-detail", "ashlar, joints as detail", -20, 8),
        ("brick-flemish", "Flemish bond, rubbed arch, gauged head", -15, 5),
        ("brick-english", "English bond, soldier head", -15, 5),
        ("rubble-random", "random rubble", -20, 10),
        ("rubble-toe", "battered rubble toe", -60, 10),
        ("cutwater-pointed", "cutwater cap", -40, 30),
        ("assembly-wall-band-coping", "wall + string course + coping", -30, 12),
    ]
    spec = {"title": "kitlib masonry helpers: voussoirs, ashlar, brick bonds, rubble, copings", "width": 2400, "height": 1500,
            "layout": "grid", "columns": 4,
            "panels": [{"document": str(KIT / f"{d}.json"), "caption": c, "yaw": y, "pitch": p} for d, c, y, p in panels]}
    path = KIT / "sheet.json"
    path.write_text(json.dumps(spec, indent=1))
    print(json.dumps(pgm("sheet", path, "--out", out)))


if __name__ == "__main__":
    args = sys.argv[1:]
    out = args[args.index("--sheet") + 1] if "--sheet" in args else None
    keep = args[args.index("--keep") + 1] if "--keep" in args else None
    root = keep or tempfile.mkdtemp(prefix="kitlib-masonry-")
    KIT = build(root)
    if out:
        sheet(out)
    unittest.main(argv=[sys.argv[0], "-v"])
