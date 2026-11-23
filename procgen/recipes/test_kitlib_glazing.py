"""Tests of the kitlib curved glazing helpers. Built by Opus 5.5 (medium), 2026-11-16; controls-following
vaults, proud ribs and wall plates added by Opus 5.5 (medium).

Builds sample documents into a temporary kit and runs pgm on them: every sample closed, manifold and outward
(pgm check); each frame (ribs, purlins, bars, kerbs) and each whole structure one component (pgm audit), so
every pane rests on its bars; no pane crosses a member (glass triangles against iron triangles, from OBJ
exports, on the small samples); pane, rib, bar and ring counts follow the parameters (the glass export's
connected pieces against the recorded count); triangle budgets; controls swept. Vaults driven by controls
(rail-hall sized span, length, springing): the arch re-solved at each value (glass on the true segmental circle
at both ends of the span), bays and panes counted at both ends of the length, panes clear of the members at the
ends of every control, main ribs' caps above the glass and bars flush, wall plates at the seat.

    PGM=/path/to/pgm python3 procgen/recipes/test_kitlib_glazing.py [--sheet /tmp/glazing-sheet.png] [--keep DIR]
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
from kitlib import C, PI, Graph, Kit, control  # noqa: E402

PGM = os.environ.get("PGM", str(Path(__file__).resolve().parent.parent / "build-release" / "pgm"))
KIT = None
INFO = {}
# Triangle budgets (placed triangles, frame and glass) per sample. Hero pieces: a 12 x 20 m hall with two end
# screens about 49k, a 14 m fan dome about 68k; a 5 m dome under 8k.
BUDGET = {"vault-hall": 55000, "vault-pointed-lantern": 30000, "vault-control": 30000, "vault-catenary": 30000,
          "vault-small": 8000, "dome-hemi": 38000, "dome-oculus": 30000, "dome-onion": 20000, "dome-small": 12000,
          "fan-dome": 80000, "fan-oculus": 62000, "fan-small": 27000, "ridge-lantern": 8000, "dome-lantern": 3000,
          "saddle": 4000, "vault-rail": 200000, "vault-pointed-control": 40000, "vault-catenary-control": 30000,
          "vault-small-control": 12000}
# Small samples for the pane-crossing test (pure Python triangle tests).
SMALL = ("vault-small", "dome-small", "fan-small", "saddle", "dome-lantern", "ridge-lantern", "vault-small-control")
VAULT_CONTROLS = {"span": control(10.0, 6.0, 16.0), "length": control(12.0, 6.0, 24.0)}
# The usability test's rail-hall: width 16-40, length 30-120, and a springing level.
RAIL = {"width": control(28.0, 16.0, 40.0), "length": control(60.0, 30.0, 120.0), "level": control(7.0, 5.0, 9.0)}
RAIL_RISE = C("width") * 0.25 + 2.0                    # rise not a fixed share of the span: the arch must re-solve
POINTED = {"span": control(10.0, 8.0, 14.0), "length": control(12.0, 6.0, 24.0)}
CATENARY = {"span": control(8.0, 6.0, 16.0), "length": control(10.0, 4.0, 20.0)}
SMALLC = {"span": control(4.0, 3.0, 8.0), "length": control(4.0, 3.0, 10.0), "level": control(2.0, 1.0, 3.0)}


def pgm(*args):
    r = subprocess.run([PGM, *map(str, args)], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        raise AssertionError(f"pgm {' '.join(map(str, args))}: {r.stdout[:400]} {r.stderr[:400]}")


def samples():
    """name -> (controls, build(g) -> id, kwargs for counts)."""
    S = {}
    S["vault-hall"] = ({}, lambda g: g.glazed_vault("x", 12.0, 20.0, ends=2.0, end_screens=(True, True)))
    S["vault-pointed-lantern"] = ({}, lambda g: g.glazed_vault("x", 10.0, 12.0, rise=7.0, section="pointed", bays=3,
                                                                lantern={"width": 2.0, "height": 1.0}))
    S["vault-control"] = (VAULT_CONTROLS, lambda g: g.glazed_vault(
        "x", C("span"), C("length"), rise=C("span") * 0.3, section="segmental", end_screens=(True, False),
        leaded={"pattern": "diamond", "quarry": (0.14, 0.2)}, controls=VAULT_CONTROLS))
    S["vault-catenary"] = ({}, lambda g: g.glazed_vault("x", 8.0, 10.0, rise=6.0, section="catenary",
                                                         lantern={"width": 1.4, "height": 0.7, "roof": "barrel"}))
    S["vault-small"] = ({}, lambda g: g.glazed_vault("x", 4.0, 4.0, bays=2, bar_pitch=0.7, end_screens=(True, False)))
    S["dome-hemi"] = ({}, lambda g: g.glazed_dome("x", 6.0))
    S["dome-oculus"] = ({}, lambda g: g.glazed_dome("x", 7.0, rise=3.5, profile="segmental", ribs=12, bars=1, oculus=True))
    S["dome-onion"] = ({}, lambda g: g.glazed_dome("x", 3.0, profile="onion", ribs=8, bars=1, lantern={"sides": 6}))
    S["dome-small"] = ({}, lambda g: g.glazed_dome("x", 2.5, ribs=6, bars=1))
    S["fan-dome"] = ({}, lambda g: g.fan_dome("x", 7.0, fans=8, fan_ribs=5))
    S["fan-oculus"] = ({}, lambda g: g.fan_dome("x", 8.0, rise=4.5, profile="segmental", fans=6, fan_ribs=7, oculus=True))
    S["fan-small"] = ({}, lambda g: g.fan_dome("x", 3.5, fans=6, fan_ribs=3, gap_bars=1))
    S["ridge-lantern"] = ({}, lambda g: g.ridge_lantern("x", 6.0, 1.8, 0.9, at=[0, 0, 3.0],
                                                        leaded={"pattern": "rect", "quarry": (0.12, 0.16)}))
    S["dome-lantern"] = ({}, lambda g: g.dome_lantern("x", 0.7, 0.8, sides=8, leaded={"pattern": "diamond"}))
    S["vault-rail"] = (RAIL, lambda g: g.glazed_vault(
        "x", C("width"), C("length"), rise=RAIL_RISE, section="segmental", springing=C("level"), wall_plate=True,
        end_screens=(True, False), controls=RAIL))
    S["vault-pointed-control"] = (POINTED, lambda g: g.glazed_vault(
        "x", C("span"), C("length"), rise=C("span") * 0.5 + 2.0, section="pointed", ends=2.0,
        lantern={"width": 2.0, "height": 1.0}, controls=POINTED))
    S["vault-catenary-control"] = (CATENARY, lambda g: g.glazed_vault(
        "x", C("span"), C("length"), rise=C("span") * 0.2 + 3.0, section="catenary", wall_plate={"width": 0.4},
        controls=CATENARY))
    S["vault-small-control"] = (SMALLC, lambda g: g.glazed_vault(
        "x", C("span"), C("length"), rise=1.2, section="segmental", bay=2.0, bar_pitch=0.7, springing=C("level"),
        wall_plate=True, end_screens=(True, False), controls=SMALLC))
    S["saddle"] = ({}, lambda g: g.glazed_surface(
        "x", lambda u, v: [4 * u, 3 * v, 1.2 * math.sin(PI * u) + 0.4 * math.sin(PI * v)],
        [(0, "edge"), (0.25, "bar"), (0.5, "purlin"), (0.75, "bar"), (1, "edge")],
        [(0, "rib"), (0.33, "bar"), (0.5, "lap"), (0.66, "bar"), (1, "rib")], divisions=(3, 3)))
    return S


def build(directory):
    kit = Kit(directory, "glazing", "glazing", bake_leaves=True)
    for name, (controls, make) in samples().items():
        for suffix, out in (("", ""), ("-frame", "-frame"), ("-glass", "-glass")):
            g = Graph()
            i = make(g)
            INFO[name] = g.glazing[i]
            audit = {"components": 10000, "why": "loose panes, for the crossing test"} if suffix == "-glass" else None
            kit.module(name + suffix, controls, g, i + out, {}, audit=audit)
    kit.close()
    return Path(directory) / "glazing"


def obj(doc, **values):
    tag = "".join(f"-{k}{v}" for k, v in values.items())
    out = Path(doc).with_name(Path(doc).stem + tag + ".obj")
    r = pgm("export", doc, "--obj", out, "--zup", *[a for k, v in values.items() for a in ("--set", f"{k}={v}")])
    assert r.get("passed"), r
    vs, fs = [], []
    for ln in out.read_text().splitlines():
        if ln.startswith("v "):
            vs.append(tuple(map(float, ln.split()[1:4])))
        elif ln.startswith("f "):
            f = [int(t.split("/")[0]) - 1 for t in ln.split()[1:]]
            fs += [(f[0], f[i], f[i + 1]) for i in range(1, len(f) - 1)]
    return vs, fs


def pieces(vs, fs):
    parent = list(range(len(vs)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b, c in fs:
        for x, y in ((a, b), (b, c)):
            parent[find(x)] = find(y)
    return len({find(a) for f in fs for a in f})


def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def edge_crosses(p, q, tri, eps=2e-5):
    """Does segment pq pass properly through triangle tri (not touching, not coplanar)?"""
    a, b, c = tri
    n = _cross(_sub(b, a), _sub(c, a))
    ln = math.sqrt(_dot(n, n))
    if ln < 1e-14:
        return False
    n = (n[0] / ln, n[1] / ln, n[2] / ln)
    dp, dq = _dot(_sub(p, a), n), _dot(_sub(q, a), n)
    if not ((dp > eps and dq < -eps) or (dp < -eps and dq > eps)):
        return False
    t = dp / (dp - dq)
    x = (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t, p[2] + (q[2] - p[2]) * t)
    for u, v in ((a, b), (b, c), (c, a)):
        e = _sub(v, u)
        m = _cross(e, _sub(x, u))
        if _dot(m, n) <= eps * math.sqrt(_dot(e, e)):
            return False
    return True


def crossings(glass, iron, cell=0.25):
    gv, gf = glass
    iv, if_ = iron
    grid = defaultdict(list)

    def cells(tri):
        lo = [min(p[k] for p in tri) for k in range(3)]
        hi = [max(p[k] for p in tri) for k in range(3)]
        r = [range(math.floor(lo[k] / cell), math.floor(hi[k] / cell) + 1) for k in range(3)]
        return [(i, j, k) for i in r[0] for j in r[1] for k in r[2]]
    for t in if_:
        tri = [iv[i] for i in t]
        for c in cells(tri):
            grid[c].append(tri)
    hits = 0
    for t in gf:
        g = [gv[i] for i in t]
        seen = set()
        for c in cells(g):
            for tri in grid[c]:
                if id(tri) in seen:
                    continue
                seen.add(id(tri))
                if any(edge_crosses(g[i], g[(i + 1) % 3], tri) for i in range(3)) or \
                   any(edge_crosses(tri[i], tri[(i + 1) % 3], g) for i in range(3)):
                    hits += 1
    return hits


class Glazing(unittest.TestCase):
    def doc(self, name):
        return KIT / f"{name}.json"

    def test_check_clean(self):
        for name in samples():
            for suffix in ("", "-frame"):
                with self.subTest(name + suffix):
                    r = pgm("check", self.doc(name + suffix))
                    self.assertTrue(r.get("passed") and r["clean"], r)
                    self.assertEqual(r["openPrototypes"], 0, r)

    def test_one_component(self):
        """The frame alone is one piece, and with the glass still one: every pane rests on its bars."""
        for name in samples():
            for suffix in ("-frame", ""):
                with self.subTest(name + suffix):
                    r = pgm("audit", self.doc(name + suffix))
                    self.assertTrue(r["passed"], r)
                    self.assertEqual(r["components"], 1, r)

    def test_panes_do_not_cross_members(self):
        for name in SMALL:
            with self.subTest(name):
                hits = crossings(obj(self.doc(name + "-glass")), obj(self.doc(name + "-frame")))
                self.assertEqual(hits, 0, f"{name}: {hits} glass triangles cross the ironwork")

    def test_pane_count_matches_geometry(self):
        for name in samples():
            with self.subTest(name):
                vs, fs = obj(self.doc(name + "-glass"))
                expect = INFO[name]["panes"] + INFO[name].get("screen_panes", 0) + INFO[name].get("lantern_panes", 0)
                self.assertEqual(pieces(vs, fs), expect)

    def test_counts_follow_parameters(self):
        def info(make):
            g = Graph()
            i = make(g)
            return g.glazing[i]
        for bays in (2, 3, 5):
            i = info(lambda g: g.glazed_vault("v", 8.0, 12.0, bays=bays, bar_pitch=0.5, purlins=4))
            self.assertEqual(i["ribs"], bays + 1)
            self.assertEqual(i["bars"], bays * (round(12.0 / bays / 0.5) - 1))
            self.assertEqual(i["purlins"], 4)
        dense = info(lambda g: g.glazed_vault("v", 8.0, 12.0, bays=3, bar_pitch=0.5, ends=2.0))
        self.assertEqual(dense["bars"], 2 * (round(4 / 0.25) - 1) + (round(4 / 0.5) - 1))
        for ribs, bars in ((6, 1), (8, 3), (12, 0)):
            i = info(lambda g: g.glazed_dome("d", 5.0, ribs=ribs, bars=bars, taper=False, rings=4))
            self.assertEqual(i["ribs"], ribs)
            self.assertEqual(i["bars"], ribs * bars)
            self.assertEqual(i["rings"], 4)
        t = info(lambda g: g.glazed_dome("d", 5.0, ribs=8, bars=3, rings=4, min_pane=0.6))
        self.assertEqual(t["bars"], 24)                    # all present at the base, shortened higher up
        self.assertLess(t["panes"], info(lambda g: g.glazed_dome("d", 5.0, ribs=8, bars=3, rings=4, taper=False))["panes"])
        f = info(lambda g: g.fan_dome("f", 6.0, fans=6, fan_ribs=4, gap_bars=2))
        self.assertEqual((f["fans"], f["fan_ribs"], f["bars"]), (6, 24, 24))

    def test_vault_dimensions(self):
        r = pgm("evaluate", self.doc("vault-hall"))
        lo, hi = r["boundsZUp"]
        self.assertAlmostEqual(hi[2], 6.0 + 0.12, delta=0.002)           # crown glass plane + proud cap rib
        self.assertAlmostEqual(hi[1] - lo[1], 20.0 + 0.11, delta=0.002)   # end rib flanges
        r = pgm("evaluate", self.doc("vault-control"), "--set", "span=16")
        self.assertAlmostEqual(r["boundsZUp"][1][0], 8.0 + 0.05, delta=0.06)

    def test_panes_clear_at_control_ends(self):
        """Sections follow the re-solved arch, so panes stay clear of the members away from the defaults."""
        name = "vault-small-control"
        for k, c in SMALLC.items():
            for end in ("min", "max"):
                with self.subTest(f"{k}={c[end]}"):
                    v = {k: c[end]}
                    hits = crossings(obj(self.doc(name + "-glass"), **v), obj(self.doc(name + "-frame"), **v))
                    self.assertEqual(hits, 0, f"{name} {v}: {hits} glass triangles cross the ironwork")

    def test_arch_resolved_per_value(self):
        """Segmental rail-hall vault: at both ends of the width (the rise is not a fixed share of it) every roof
        pane vertex lies on the true circle through the springings and crown at that value, lifted 1.5-9.5 mm."""
        for w in (16.0, 28.0, 40.0):
            with self.subTest(width=w):
                lvl, h, R = 7.0, w / 2, w * 0.25 + 2.0
                rho = (h * h + R * R) / (2 * R)
                zc = lvl + R - rho
                vs, fs = obj(self.doc("vault-rail-glass"), width=w)
                used = {i for f in fs for i in f}
                dev = [math.hypot(vs[i][0], vs[i][2] - zc) - rho for i in used if vs[i][1] > 0.5]
                self.assertGreater(len(dev), 100)
                self.assertGreater(min(dev), 0.0)
                self.assertLess(max(dev), 0.0125, f"width {w}: glass off the arch by {max(dev):.4f} m")
                lo, hi = pgm("evaluate", self.doc("vault-rail-glass"), "--set", f"width={w}")["boundsZUp"]
                self.assertAlmostEqual(hi[2], lvl + R + 0.0095, delta=0.004)

    def test_counts_follow_controls(self):
        """Bays (and so ribs, bars and pane strips) are counted from the length at each value."""
        cases = [("vault-rail", RAIL, "length", 30.0), ("vault-rail", RAIL, "length", 120.0),
                 ("vault-pointed-control", POINTED, "length", 6.0), ("vault-pointed-control", POINTED, "length", 24.0),
                 ("vault-catenary-control", CATENARY, "length", 4.0), ("vault-catenary-control", CATENARY, "length", 20.0)]
        for name, ctl, k, val in cases:
            with self.subTest(f"{name} {k}={val}"):
                info = INFO[name]
                nb = max(1, math.floor(val / 4.0 + 0.5))
                me, m = info["bars_per_bay"]
                strips = nb * m if (me == m or nb == 1) else 2 * me + max(0, nb - 2) * m
                vs, fs = obj(self.doc(name + "-glass"), **{k: val})
                extra = info.get("screen_panes", 0) + info.get("lantern_panes", 0)
                self.assertEqual(pieces(vs, fs), info["strip_panes"] * strips + extra)
                lo, hi = pgm("evaluate", self.doc(name), "--set", f"{k}={val}")["boundsZUp"]
                ov = 0.2 if "lantern_panes" in info else 0.0      # the lantern roof overhangs the ends
                self.assertAlmostEqual(hi[1] - lo[1], val + max(0.11, 2 * ov), delta=0.01)

    def test_ribs_proud_of_the_glass(self):
        """From the OBJ export: over each main rib the ironwork stands >= 0.1 m above the glass all the way up the
        arch (between the eaves), while mid-bay the bars stay within their web height of it."""
        for w in (16.0, 40.0):
            with self.subTest(width=w):
                lvl, h, R, L = 7.0, w / 2, w * 0.25 + 2.0, 60.0
                rho = (h * h + R * R) / (2 * R)
                zc = lvl + R - rho
                gv, gf = obj(self.doc("vault-rail-glass"), width=w)
                fv, ff = obj(self.doc("vault-rail-frame"), width=w)
                pitch = L / 15
                bins = 24

                def tops(vs, fs, keep):
                    out = defaultdict(lambda: -1e9)
                    for i in {i for f in fs for i in f}:
                        x, y, z = vs[i]
                        if not keep(y) or z < lvl - 1e-3:
                            continue
                        b = int(math.degrees(math.atan2(x, z - zc)) * bins / 90 + 1000)
                        out[b] = max(out[b], math.hypot(x, z - zc) - rho)
                    return out
                near_rib = lambda y: min(y % pitch, pitch - y % pitch) < 0.03 and 1.0 < y < L - 1.0
                me, m = INFO["vault-rail"]["bars_per_bay"]
                mid_bay = lambda y: abs(y % pitch - pitch * (m // 2) / m) < 0.03     # a glazing bar mid-bay
                glass = tops(gv, gf, lambda y: 0.5 < y < L - 0.5)
                ribs, bars = tops(fv, ff, near_rib), tops(fv, ff, mid_bay)
                common = [b for b in glass if b in ribs]
                self.assertGreater(len(common), 8)
                # leave out the bins at the eaves, where the kerb stands
                inner = sorted(common)[1:-1]
                for b in inner:
                    self.assertGreater(ribs[b] - glass[b], 0.1, f"rib top not proud of the glass in bin {b}")
                for b in [b for b in inner if b in bars]:
                    self.assertLess(bars[b] - glass[b], 0.03, f"bar stands above the glass in bin {b}")

    def test_wall_plates_seat(self):
        """The plates' undersides are the seat: at springing - 0.2 at every level, under both eaves."""
        for lvl in (5.0, 9.0):
            with self.subTest(level=lvl):
                lo, hi = pgm("evaluate", self.doc("vault-rail-frame"), "--set", f"level={lvl}")["boundsZUp"]
                self.assertAlmostEqual(lo[2], lvl - 0.2, delta=0.002)
                self.assertAlmostEqual(hi[0], 14.0 + 0.15, delta=0.002)
        seat = INFO["vault-rail"]["seat"]
        self.assertIn("$level", json.dumps(seat["z"].v if hasattr(seat["z"], "v") else seat["z"]))

    def test_triangle_budget(self):
        for name in samples():
            with self.subTest(name):
                r = pgm("evaluate", self.doc(name))
                self.assertLessEqual(r["placedTriangles"], BUDGET[name], r)
                print(f"  {name}: {r['placedTriangles']} triangles", file=sys.stderr)

    def test_leaded_details(self):
        for name in ("ridge-lantern", "dome-lantern", "vault-control"):
            with self.subTest(name):
                r = pgm("evaluate", self.doc(name))
                self.assertTrue(r["passed"], r)
                doc = json.loads(self.doc(name).read_text())
                self.assertTrue(any(n["type"] == "detailStroke" for n in doc["nodes"]))

    def test_sweep(self):
        r = pgm("sweep", KIT)
        self.assertTrue(r["passed"], r)
        r = pgm("audit", KIT, "--sweep")
        self.assertFalse(r.get("failures"), r)

    def test_triangles_at_control_ends(self):
        for name, ctl in (("vault-rail", RAIL), ("vault-pointed-control", POINTED), ("vault-catenary-control", CATENARY),
                          ("vault-small-control", SMALLC)):
            for k, c in ctl.items():
                for end in ("min", "max"):
                    r = pgm("evaluate", self.doc(name), "--set", f"{k}={c[end]}")
                    self.assertTrue(r["passed"], r)
                    print(f"  {name} {k}={c[end]}: {r['placedTriangles']} triangles", file=sys.stderr)


def sheet(out):
    panels = [
        ("vault-hall", "vault: semicircular, ends denser, end screens", -35, 22),
        ("vault-hall-frame", "the same frame: I ribs, purlins, T bars", -35, 22),
        ("vault-pointed-lantern-frame", "pointed vault, ridge lantern on curbs", -30, 18),
        ("vault-catenary", "catenary vault, barrel-roofed lantern", -40, 20),
        ("dome-hemi", "dome: hemisphere, crown lantern", -30, 18),
        ("dome-hemi-frame", "dome frame: ribs, rings, tapered bars", -30, 18),
        ("dome-oculus-frame", "segmental dome, oculus", -20, 35),
        ("dome-onion", "onion dome", -30, 12),
        ("fan-dome", "fan-ribbed (sunburst) dome", -25, 18),
        ("fan-dome-frame", "fan dome frame: springers, fans", -25, 18),
        ("fan-oculus-frame", "segmental fan dome, oculus", -20, 40),
        ("ridge-lantern", "ridge lantern, leaded quarries", -35, 15),
    ]
    spec = {"title": "kitlib curved glazing: vaults, domes, fan domes, lanterns", "width": 2400, "height": 1500,
            "layout": "grid", "columns": 4,
            "panels": [{"document": str(KIT / f"{d}.json"), "caption": c, "yaw": y, "pitch": p} for d, c, y, p in panels]}
    path = KIT / "sheet.json"
    path.write_text(json.dumps(spec, indent=1))
    print(json.dumps(pgm("sheet", path, "--out", out)))


if __name__ == "__main__":
    args = sys.argv[1:]
    out = args[args.index("--sheet") + 1] if "--sheet" in args else None
    keep = args[args.index("--keep") + 1] if "--keep" in args else None
    root = keep or tempfile.mkdtemp(prefix="kitlib-glazing-")
    KIT = build(root)
    if out:
        sheet(out)
    unittest.main(argv=[sys.argv[0], "-v"])
