#!/usr/bin/env python3
"""Tests for bond_maps.py and masonry_bonds.py. Built by Opus 5.5 (medium).

  python3 procgen/tools/test_bond_maps.py          (or: python3 -m pytest procgen/tools/test_bond_maps.py)

If a matc build is found (MATC=..., or engine/build/*/tools/matbuild/matc in this checkout or the main
one), the .mats is also run through `matc --images`; otherwise that test is skipped.
GeometryAgrees (Opus 5.5 medium): the joints of stone-by-stone brick_wall / ashlar_wall geometry fall on the
baked maps' mortar lines at the same metre coordinates.
"""
import math
import os
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bond_maps as bm  # noqa: E402  (puts procgen/recipes on the path)
import masonry_bonds as mb  # noqa: E402
import kitlib  # noqa: E402
import kitlib_masonry as km  # noqa: E402

DENSITY = 256                       # coarse for speed; the defaults are exercised by test_write_and_parse


class BondRules(unittest.TestCase):
    def test_six_bonds(self):
        self.assertEqual(mb.BONDS, ("brick-stretcher", "brick-english", "brick-flemish", "brick-header",
                                    "ashlar-coursed", "ashlar-rusticated"))

    def test_units_tile_the_cell_without_overlap(self):
        for n in mb.BONDS:
            b = mb.cell(n)
            # face area + joints = cell area (each unit owns its pitch x course)
            area = sum((u.w + b.joint) * (u.h + b.joint) for u in b.units)
            self.assertAlmostEqual(area, b.size * b.size, places=9, msg=n)
            for u in b.units:
                self.assertTrue(0 <= u.x < b.size and 0 <= u.y < b.size, n)

    def test_brick_sizes(self):
        for n in mb.BONDS[:4]:
            for u in mb.cell(n).units:
                self.assertAlmostEqual(u.h, 0.065)
                self.assertIn(round(u.w, 6), (0.215, 0.1025))  # stretcher or header face


    def test_cells_repeat_bond_pattern(self):
        """Each square cell is bond_pattern's natural cell repeated (n, m) times: same joint, same head-joint
        lattice, and the documented tiles. Built by Opus 5.5 (medium)."""
        tiles = {"brick-stretcher": ((2, 3), 0.45), "brick-english": ((2, 3), 0.45), "brick-flemish": ((4, 9), 1.35),
                 "brick-header": ((4, 3), 0.45), "ashlar-coursed": ((2, 2), 1.4), "ashlar-rusticated": ((2, 3), 2.1)}
        for n in mb.BONDS:
            b, p = mb.cell(n), mb.bond_pattern(n)
            self.assertEqual((b.repeat, b.size), tiles[n], n)
            self.assertEqual(b.joint, p["joint"], n)
            (w, h), rk = p["cell"], len(p["rows"])
            self.assertEqual(len(b.rows), rk * b.repeat[1], n)
            self.assertEqual(len(b.units), len(p["units"]) * b.repeat[0] * b.repeat[1], n)
            for k, r in enumerate(b.rows):
                nat = p["rows"][k % rk]
                self.assertAlmostEqual(r["z0"], nat["z0"] + (k // rk) * h, places=9)
                self.assertEqual(len(r["cuts"]), len(nat["cuts"]) * b.repeat[0])
                for c in r["cuts"]:
                    self.assertTrue(any(abs((c - x) / w - round((c - x) / w)) < 1e-9 for x in nat["cuts"]), (n, k, c))
        self.assertIs(km.bond_pattern, mb.bond_pattern)          # one source: the kit helpers use this module's


def _runs_centre(line, t):
    """Centre (pixels, edge coordinates) of the run of True in the periodic 1-D mask `line` nearest pixel
    position t, or None if there is no True within 4 pixels."""
    N = len(line)
    i0 = int(math.floor(t))
    for d in range(5):
        for i in (i0 - d, i0 + d, i0 + 1 + d, i0 + 1 - d):
            if line[i % N]:
                lo = hi = i
                while line[(lo - 1) % N] and hi - lo < N:
                    lo -= 1
                while line[(hi + 1) % N] and hi - lo < N:
                    hi += 1
                return (lo + hi + 1) * 0.5
    return None


def _run_length(line, centre):
    """Length in pixels of the run of True in `line` whose centre _runs_centre gave."""
    N, i = len(line), int(math.floor(centre))
    n = 0
    while line[(i + n) % N] and n < N:
        n += 1
    m = 0
    while line[(i - 1 - m) % N] and n + m < N:
        m += 1
    return n + m


def _wall_faces(name):
    """(stone front rectangles [(x0, x1, z0, z1)], gap, width, height) of a stone-by-stone wall in the slot's
    bond, read from the helper's own surface nodes (the faces on the y = 0 face plane)."""
    b, p = mb.cell(name), mb.bond_pattern(name)
    W, H = round(2 * b.size + 2.5, 6), b.size                    # height: whole courses of the slot's cell
    g = kitlib.Graph()
    if name.startswith("brick"):
        g.brick_wall("w", W, H, bond=p["bond"], joints="gap")   # default brick and joint: the slot's
        gap = 0.01
    else:
        g.ashlar_wall("w", W, H, course=0.35, block=p["cell"][0], bond=0.5, joints="gap")
        gap = km.GAP
    rects = []
    for nd in g.nodes:
        if nd["type"] != "surface":
            continue
        V = nd["options"]["vertices"]
        for f in nd["options"]["faces"]:
            loop = f[0] if isinstance(f[0], list) else f
            pts = [V[i] for i in loop]
            if all(abs(float(q[1])) < 1e-9 for q in pts):
                xs, zs = [float(q[0]) for q in pts], [float(q[2]) for q in pts]
                rects.append((min(xs), max(xs), min(zs), max(zs)))
    return rects, gap, W, H


class GeometryAgrees(unittest.TestCase):
    """Built by Opus 5.5 (medium). For every slot, a brick_wall / ashlar_wall built stone by stone
    (joints="gap") in the slot's bond, and the slot's map baked at the default density: every head and bed
    joint of the geometry (the centre of the gap between neighbouring stone faces) lies on a mortar line of
    the map at the same metre coordinates (u = x, v = z of the wall frame, the joints="material" UVs), within
    one pixel; and every mortar line the map shows in a course has a geometry joint there."""

    @classmethod
    def setUpClass(cls):
        cls.maps = {n: bm.render(n) for n in mb.BONDS}           # default bake: 512 px/m, >= 1024 px per cell

    def test_joints_coincide(self):
        for name in mb.BONDS:
            with self.subTest(name):
                f, b = self.maps[name], mb.cell(name)
                S, N, s, mortar = b.size, f["N"], f["px_per_m"], f["mortar"]
                tol = 1.0 / s                                    # one pixel, metres
                rects, gap, W, H = _wall_faces(name)
                self.assertGreater(len(rects), 20, name)
                edge = 1e-6 + gap
                wrap = lambda d: (d + S / 2) % S - S / 2
                row_of = lambda z: min(N - 1, max(0, int((S - z % S) * s)))
                col_of = lambda x: min(N - 1, max(0, int((x % S) * s)))
                heads, beds = {}, 0
                for x0, x1, z0, z1 in rects:
                    zm, xm = (z0 + z1) / 2, (x0 + x1) / 2
                    for xc in (x0 - gap / 2, x1 + gap / 2):     # head joints left and right of the stone
                        if abs(xc) > W / 2 - edge:
                            continue
                        c = _runs_centre(mortar[row_of(zm)], (xc % S) * s)
                        self.assertIsNotNone(c, (name, "head", xc, zm))
                        self.assertLessEqual(abs(wrap(c / s - xc % S)), tol, (name, "head", xc, zm, c / s))
                        heads.setdefault(round(zm, 4), []).append(xc)
                    for zc in (z0 - gap / 2, z1 + gap / 2):     # bed joints under and over the stone
                        if zc < edge or zc > H - edge:
                            continue
                        # a column clear of the head joints above and below (the mortar run is one joint long)
                        c = None
                        for fr in (0.3, 0.2, 0.4, 0.6, 0.7, 0.8):
                            col = mortar[:, col_of(x0 + fr * (x1 - x0))]
                            c = _runs_centre(col, (S - zc % S) * s)
                            if c is not None and _run_length(col, c) <= b.joint * s + 3:
                                break
                        self.assertIsNotNone(c, (name, "bed", xm, zc))
                        self.assertLessEqual(abs(wrap((S - c / s) - zc % S)), tol, (name, "bed", xm, zc, S - c / s))
                        beds += 1
                self.assertGreater(beds, 10, name)
                # the other way: each map head joint in a course has a geometry joint (one tile of the interior)
                margin = 1.2 * mb.bond_pattern(name)["cell"][0]
                for zm, xs in heads.items():
                    geo = [x % S for x in xs if abs(x) < W / 2 - margin]
                    line = mortar[row_of(zm)]
                    starts = [i for i in range(N) if line[i] and not line[i - 1]]
                    self.assertTrue(starts, (name, zm))
                    for i in starts:
                        c = _runs_centre(line, i + 0.5) / s
                        self.assertTrue(any(abs(wrap(c - x)) <= tol for x in geo), (name, "map joint", zm, c))


class Maps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = {n: bm.render(n, DENSITY, seed=3, min_px=0) for n in mb.BONDS}

    def test_dimensions(self):
        for n, f in self.f.items():
            N = int(round(mb.cell(n).size * DENSITY))   # min_px=0: density alone
            for k in ("albedo", "height", "occlusion", "roughness", "normal"):
                self.assertEqual(f[k].shape[:2], (N, N), f"{n} {k}")
            imgs = bm.to_images(f)
            self.assertTrue(all(im.size == (N, N) for im in imgs), n)

    def test_default_sources_at_least_1024_px(self):
        for n in mb.BONDS:
            b = mb.cell(n)
            N = bm.cell_pixels(b, 512)
            self.assertGreaterEqual(N, 1024, n)
            self.assertGreaterEqual(N, round(b.size * 512), n)
        self.assertEqual(bm.cell_pixels(mb.cell("ashlar-rusticated"), 512), 1075)   # the 2.1 m cell

    def test_seamless_shifted_origin(self):
        """Moving the origin by half a cell gives the same image rolled: no unit or noise is cut at the edge."""
        for n, f in self.f.items():
            N = f["N"]
            s = bm.render(n, DENSITY, seed=3, shift=(N // 2 + 1, N // 3), min_px=0)
            for k in ("height", "albedo", "occlusion"):
                rolled = np.roll(f[k], (-(N // 3), -(N // 2 + 1)), axis=(0, 1))
                self.assertLess(np.abs(s[k] - rolled).max(), 1e-6, f"{n} {k}")

    def test_wrapped_edges_match(self):
        """Wrapped edge pixels differ no more than interior neighbours do."""
        for n, f in self.f.items():
            h = np.asarray(bm.to_images(f)[1], dtype=float)[..., 0]
            interior_x = np.abs(np.diff(h, axis=1)).mean()
            interior_y = np.abs(np.diff(h, axis=0)).mean()
            self.assertLess(np.abs(h[:, 0] - h[:, -1]).mean(), 3 * interior_x + 1, n)
            self.assertLess(np.abs(h[0, :] - h[-1, :]).mean(), 3 * interior_y + 1, n)

    def test_mortar_below_units(self):
        for n, f in self.f.items():
            b = mb.cell(n)
            h, mortar = f["height"], f["mortar"]
            self.assertTrue(mortar.any() and (~mortar).any(), n)
            self.assertLess(h[mortar].max(), h[~mortar].min(), n)     # every mortar pixel below every face pixel
            self.assertLess(h[mortar].mean() + 0.2, h[~mortar].mean(), n)

    def test_normals_unit_length(self):
        for n, f in self.f.items():
            v = bm.decode_normal(np.asarray(bm.to_images(f)[2]))
            ln = np.linalg.norm(v, axis=-1)
            self.assertLess(np.abs(ln - 1).max(), 0.02, n)
            self.assertTrue((v[..., 2] > 0).all(), n)


class NormalConvention(unittest.TestCase):
    def test_raised_dome_plus_y_up(self):
        """A raised dome: on its upper side (smaller row) the normal leans up (+Y, green > 128), on its
        right side it leans right (+X, red > 128); decoded vectors are unit length."""
        N, texel = 128, 0.002
        yy, xx = np.mgrid[0:N, 0:N]
        r2 = ((xx - N / 2 + 0.5) ** 2 + (yy - N / 2 + 0.5) ** 2) / (N / 3) ** 2
        h = np.sqrt(np.clip(1 - r2, 0, 1)) * 0.06               # 6 cm dome on 8.5 cm radius, metres
        rgb = bm.encode_normal(bm.normal_from_height(h, texel))
        Image.fromarray(rgb).save(os.path.join(tempfile.gettempdir(), "bond-dome-normal.png"))
        v = bm.decode_normal(rgb)
        c = N // 2
        self.assertGreater(rgb[c - 20, c, 1], 160)               # top side: green up
        self.assertLess(rgb[c + 20, c, 1], 96)                   # bottom side: green down
        self.assertGreater(rgb[c, c + 20, 0], 160)               # right side: red up
        self.assertLess(rgb[c, c - 20, 0], 96)
        self.assertAlmostEqual(rgb[c, c, 0], 128, delta=3)
        self.assertLess(np.abs(np.linalg.norm(v, axis=-1) - 1).max(), 0.02)


class Manifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bond-maps-")
        bm.main(["--out", cls.tmp, "--density", "128", "--min-px", "64"])
        cls.mats = os.path.join(cls.tmp, "bond-materials.mats")

    def test_write_and_parse(self):
        mats = bm.parse_mats(open(self.mats).read(), self.tmp)
        self.assertEqual(set(mats), set(mb.BONDS))
        for n, m in mats.items():
            self.assertAlmostEqual(m["tile"], mb.cell(n).size, places=6)
            self.assertEqual(m["height"], (f"{n}-height-ao.png", 0))
            self.assertEqual(m["occlusion"], (f"{n}-height-ao.png", 1))
            self.assertEqual(m["roughness"], (f"{n}-height-ao.png", 2))
            im = Image.open(os.path.join(self.tmp, m["albedo"][0]))
            self.assertEqual(im.mode, "RGB")                     # no alpha: nothing hidden in it
            self.assertEqual(Image.open(os.path.join(self.tmp, f"{n}-normal.png")).mode, "RGB")
            self.assertEqual(m["normal"], (f"{n}-normal.png", "y-up"))
            self.assertAlmostEqual(m["parallax"], mb.cell(n).relief, places=9)
        self.assertEqual({n: m["parallax"] for n, m in mats.items()},
                         {"brick-stretcher": 0.01, "brick-english": 0.01, "brick-flemish": 0.01,
                          "brick-header": 0.01, "ashlar-coursed": 0.007, "ashlar-rusticated": 0.03})

    def test_no_normal_record_fallback(self):
        mats = bm.parse_mats(bm.mats_text(mb.BONDS, 128, records=False), None)
        for m in mats.values():
            self.assertNotIn("normal", m)
            self.assertNotIn("parallax", m)
            self.assertIn("normal-strength", m)

    def test_parser_accepts_new_records(self):
        m = bm.parse_mats("mats 1\nmaterial x\nnormal a.png y-up\nparallax 0.01\nend\n"
                          "material y\nnormal b.png\nend\nmaterial z\nnormal c.png y-down\nend\n")
        self.assertEqual(m["x"], {"normal": ("a.png", "y-up"), "parallax": 0.01})
        self.assertEqual(m["y"]["normal"], ("b.png", "y-down"))
        self.assertEqual(m["z"]["normal"], ("c.png", "y-down"))

    def test_parser_refuses_bad_records(self):
        for bad in ("material x\nend\n", "mats 1\nmaterial x\nheight a.png 4\nend\n",
                    "mats 1\nmaterial x\nnormal a.png sideways\nend\n", "mats 1\nmaterial x\nparallax 0\nend\n",
                    "mats 1\nmaterial x\nfrobnicate 1\nend\n", "mats 1\nmaterial x\n"):
            with self.assertRaises(ValueError):
                bm.parse_mats(bad)

    def test_matc_accepts(self):
        matc = bm.find_matc()
        if not matc:
            self.skipTest("no matc build (set MATC=path/to/matc)")
        records, code, out = bm.check_with_matc(matc, list(mb.BONDS), 128, self.tmp, min_px=64)
        print(f"\nmatc {matc}: exit {code}, normal/parallax records "
              f"{'accepted' if records else 'refused, retried without them'}\n{out}")
        self.assertEqual(code, 0, out)
        self.assertGreater(os.path.getsize(os.path.join(self.tmp, "bond-materials.emat")), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
