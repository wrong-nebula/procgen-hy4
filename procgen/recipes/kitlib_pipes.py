"""Pipework helpers for kit builders: pipe runs with true bends, fittings, valves, supports and a Manhattan router.
Built by Opus 5.5 (medium), 2026-11-16.

kitlib.py imports this module at its end, so builders get the helpers as `Graph` methods and the router as a
function:

    from kitlib import Graph, route
    pts = route([0, 0, 0.4], [6, 0, 0.4], start_dir=[0, 0, 1], end_dir=[0, 0, -1], height=3.0, bend_radius=0.45)
    run = g.pipe_run("main", pts, 0.3, bend_radius=0.45, joints="flange", pipe_length=3.66, slot="iron")
    v = g.valve("valve", run, 0.5, kind="gate", wheel_slot="bronze")
    s = g.supports("hangers", run, kind="hanger", plane=3.6, spacing=2.5)
    g.join("pipework", run, v, s)

Proportions are rules of thumb after period practice, not a standard's tables: flanged cast-iron main in lengths
of about 3.66 m (12 ft), flanges 1.2 D + 0.1 across and 0.02 + 0.04 D thick on a short neck, 4 * round(1 + 7 D)
bolts set between the centre lines; flanged bends of radius 1 to 2 D (default 1.5 D) as torus segments, flanged
at both tangent points; bell-and-spigot cast iron with a beaded bell 0.1 + 0.25 D long; riveted steel pipe in
courses with double-riveted lap seams and a riveted longitudinal seam; gate valves with a tall oval chest, a
bolted bonnet and a dished handwheel about 0.9 D + 0.12 across; supports every 2.5 to 3.5 m.

Frame: document coordinates, metres, Z-up; every part is laid out in place from points and directions (no local
frame, no transform node). Each helper returns its node id as a `str` that also carries what it laid out
(ports, positions, the run's path and joints), so it can be passed straight to `join`. Every part is a closed,
manifold, outward `surface` shell; the members of a helper overlap or touch (supports reach 2 mm into the pipe),
so a run with its fittings, valves and supports audits as one piece. Round parts are smoothed and carry small
edge bevels so smooth shading does not wash over their corners; nuts and plates are flat-shaded.
Only pipe_run's `diameter` may be an X expression (then bend_radius must be a number and the run takes no
flanges or bells); everything else takes numbers. Tests and a sample sheet: recipes/test_kitlib_pipes.py.
"""
import math

import kitlib
from kitlib import PI, X, _num, cos, sin, T

__all__ = ["route", "flange_dims"]


# --- vectors (numbers) -----------------------------------------------------------------------------------

def _v3(p): return tuple(float(c) for c in p)
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _scl(a, k): return (a[0] * k, a[1] * k, a[2] * k)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def _len(a): return math.sqrt(_dot(a, a))


def _unit(a):
    n = _len(a)
    assert n > 1e-12, "zero-length direction"
    return _scl(a, 1.0 / n)


def _rot(v, k, a):
    """v turned by angle a about the unit axis k (Rodrigues)."""
    c, s = math.cos(a), math.sin(a)
    return _add(_add(_scl(v, c), _scl(_cross(k, v), s)), _scl(k, _dot(k, v) * (1 - c)))


def _frame(d, up=(0.0, 0.0, 1.0)):
    """(side, nrm) square to the unit direction d, nrm as close to `up` as it can be; side x nrm = d."""
    for hint in (up, (0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)):
        n = _sub(hint, _scl(d, _dot(hint, d)))
        if _len(n) > 1e-3:
            n = _unit(n)
            return _cross(n, d), n
    raise AssertionError("no frame")


def _xv(x):
    return x.v if isinstance(x, X) else x


def _pt(base, *terms):
    """base + sum(vec * k): vec a numeric 3-vector, k a number or X. Components stay numbers where they can."""
    out = []
    for i in range(3):
        acc = X(base[i])
        for vec, k in terms:
            c = vec[i]
            if abs(c) < 1e-12:
                continue
            if abs(c - 1) < 1e-12:
                acc = acc + k
            elif abs(c + 1) < 1e-12:
                acc = acc - k
            else:
                acc = acc + X(k) * c
        v = acc.v
        out.append(round(v, 6) if _num(v) else v)
    return out


def _bevel(profile, closed, b):
    """Profile with each corner sharper than 40 degrees cut by a small chamfer (b metres, at most a quarter of
    either edge), so smoothed normals stay true on the faces either side; corners on edges under 4 b (a
    gasket groove) stay sharp. Numbers only."""
    n = len(profile)
    out = []
    for i, p in enumerate(profile):
        if not closed and (i == 0 or i == n - 1):
            out.append(p)
            continue
        a, c = profile[(i - 1) % n], profile[(i + 1) % n]
        e0, e1 = (p[0] - a[0], p[1] - a[1]), (c[0] - p[0], c[1] - p[1])
        l0, l1 = math.hypot(*e0), math.hypot(*e1)
        if l0 < 1e-9 or l1 < 1e-9:
            out.append(p)
            continue
        cosang = (e0[0] * e1[0] + e0[1] * e1[1]) / (l0 * l1)
        if cosang > math.cos(math.radians(40)) or (not closed and p[0] <= 1e-9) or min(l0, l1) < 4 * b:
            out.append(p)
            continue
        k = min(b, 0.25 * l0, 0.25 * l1)
        out.append((p[0] - e0[0] / l0 * k, p[1] - e0[1] / l0 * k))
        out.append((p[0] + e1[0] / l1 * k, p[1] + e1[1] / l1 * k))
    return out


# --- meshes ----------------------------------------------------------------------------------------------

class _Mesh:
    """Vertices and polygon faces of one or more closed shells, emitted as one `surface` node."""

    def __init__(self):
        self.verts, self.faces = [], []

    def _vid(self, p):
        self.verts.append(list(p))
        return len(self.verts) - 1

    def stations(self, sts, n, closed=False, phase=0.0):
        """Rings through stations (centre, side, nrm, radius) with side x nrm along the direction of travel; a
        station of radius 0 is one vertex on the axis. Open ends must be such points; closed=True joins the last
        station to the first (a torus or a closed lathe loop). Profiles turning counter-clockwise in (radius,
        travel) face outward."""
        rings = []
        angles = [phase + 2 * PI * j / n for j in range(n)]
        for c, sd, nm, r in sts:
            if _num(_xv(r)) and abs(_xv(r)) < 1e-12:
                rings.append([self._vid(_pt(c))])
            else:
                rings.append([self._vid(_pt(c, (sd, X(r) * math.cos(a)), (nm, X(r) * math.sin(a)))) for a in angles])
        pairs = list(zip(rings, rings[1:])) + ([(rings[-1], rings[0])] if closed else [])
        for A, B in pairs:
            for j in range(n):
                k = (j + 1) % n
                f = [A[j % len(A)], A[k % len(A)], B[k % len(B)], B[j % len(B)]]
                f = [v for i, v in enumerate(f) if v != f[i - 1]]
                if len(f) >= 3:
                    self.faces.append(f)

    def lathe(self, profile, origin, axis, n, side=None, closed=False, bevel=0.004, phase=0.0):
        """Profile [(radius, z)] turned about `axis` through `origin` (z along the axis). Open profiles start and
        end on the axis; closed ones are loops (rings round a bore). Counter-clockwise in (r, z) faces outward."""
        axis = _unit(axis)
        sd, nm = _frame(axis, side or (0.0, 0.0, 1.0))
        if bevel and all(_num(_xv(r)) and _num(_xv(z)) for r, z in profile):
            profile = _bevel([(float(r), float(z)) for r, z in profile], closed, bevel)
        self.stations([(_pt(origin, (axis, z)), sd, nm, r) for r, z in profile], n, closed, phase)

    def rod(self, a, b, radius, n=8, bevel=0.0):
        a, b = _v3(a), _v3(b)
        L = _len(_sub(b, a))
        self.lathe([(0, 0), (radius, 0), (radius, L), (0, L)], a, _sub(b, a), n, bevel=bevel)

    def torus(self, centre, axis, R, rr, n_major, n_minor, side=None):
        axis = _unit(axis)
        e1, e2 = _frame(axis, side or (1.0, 0.0, 0.0))
        sts = []
        for k in range(n_major):
            ph = 2 * PI * k / n_major
            radial = _add(_scl(e1, math.cos(ph)), _scl(e2, math.sin(ph)))
            tang = _add(_scl(e1, -math.sin(ph)), _scl(e2, math.cos(ph)))
            sts.append((_pt(centre, (radial, R)), radial, _cross(tang, radial), rr))
        self.stations(sts, n_minor, closed=True)

    def prism(self, outline, origin, u, v, z0, z1):
        """Outline [(a, b)] counter-clockwise in the (u, v) plane through origin, extruded along u x v from z0 to
        z1 (z1 > z0)."""
        u, v = _unit(u), _unit(v)
        w = _cross(u, v)
        bot = [self._vid(_pt(origin, (u, a), (v, b), (w, z0))) for a, b in outline]
        top = [self._vid(_pt(origin, (u, a), (v, b), (w, z1))) for a, b in outline]
        m = len(outline)
        self.faces.append(list(top))
        self.faces.append(list(reversed(bot)))
        for i in range(m):
            j = (i + 1) % m
            self.faces.append([bot[i], bot[j], top[j], top[i]])

    def bar(self, a, b, width, height, up):
        """A rectangular bar from a to b, `width` across and `height` along `up` (squared to the bar)."""
        a, b = _v3(a), _v3(b)
        w = _unit(_sub(b, a))
        upn = _unit(_sub(up, _scl(w, _dot(up, w))))
        u = _cross(upn, w)
        hw, hh = width / 2, height / 2
        self.prism([(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)], a, u, upn, 0.0, _len(_sub(b, a)))

    def plate(self, centre, normal, size, thickness, along=None):
        """A rectangular plate size = (a, b) lying on the plane through centre, standing `thickness` out along normal."""
        normal = _unit(normal)
        u, v = _frame(normal, along or (0.0, 0.0, 1.0))        # u x v = normal
        a, b = size[0] / 2, size[1] / 2
        self.prism([(-a, -b), (a, -b), (a, b), (-a, b)], centre, u, v, 0.0, thickness)

    def nut(self, centre, axis, rn, z0, z1, side=None):
        self.lathe([(0, z0), (rn, z0), (rn, z1), (0, z1)], centre, axis, 6, side=side, bevel=0)

    def emit(self, g, id_, slot=None, smooth=True):
        if not self.faces:
            return None
        part = g.n(id_ + "-shell", "surface", vertices=self.verts, faces=self.faces)
        if smooth:
            part = g.n(id_ + "-smooth", "smoothNormals", [part])
        if slot:
            part = g.mat(id_ + "-slot", part, slot)
        return part


def _emit_all(g, id_, meshes):
    """meshes: [(suffix, mesh, slot, smooth)] -> one joined node id."""
    parts = [m.emit(g, f"{id_}-{sfx}", slot, smooth) for sfx, m, slot, smooth in meshes]
    parts = [p for p in parts if p]
    assert parts, f"{id_}: nothing to build"
    return g.join(id_, *parts)


class Built(str):
    """A node id carrying what a helper laid out (ports, positions, ...) as attributes."""

    def __new__(cls, id_, **attrs):
        s = super().__new__(cls, id_)
        s.__dict__.update(attrs)
        return s


# --- proportions -----------------------------------------------------------------------------------------

def flange_dims(diameter):
    """Flange proportions for a pipe of outside diameter D (metres, number): dict with radius (flange outer),
    thickness, neck, bolt_radius (bolt circle), bolts (count), nut (nut corner radius), nut_height, bore.
    Rules of thumb after cast-iron practice (DN 300: 0.48 m across, 12 bolts on a 0.44 m circle).
    Example: flange_dims(0.32)["bolts"] -> 12"""
    D = float(diameter)
    Rf = (1.2 * D + 0.1) / 2
    return {"radius": Rf, "thickness": 0.02 + 0.04 * D, "neck": 0.01 + 0.03 * D,
            "bolt_radius": D / 2 + 0.6 * (Rf - D / 2) + 0.004, "bolts": 4 * max(1, round(1 + 7 * D)),
            "nut": 0.008 + 0.025 * D, "nut_height": 0.012 + 0.03 * D, "bore": D / 2 - (0.008 + 0.03 * D)}


def _wall(D):
    return 0.008 + 0.03 * D


# --- the path: straights and torus bends ---------------------------------------------------------------

class _Path:
    """A polyline with every corner replaced by a circular arc of radius R. Pieces carry their start frame,
    transported without twist (exact rotation-minimising frames: constant on straights, turned about the
    bend axis on arcs)."""

    def __init__(self, points, R, up=(0.0, 0.0, 1.0)):
        pts = []
        for p in points:
            p = _v3(p)
            if not pts or _len(_sub(p, pts[-1])) > 1e-9:
                pts.append(p)
        assert len(pts) >= 2, "pipe path needs two distinct points"
        keep = [pts[0]]                                 # drop points where the path runs straight on
        for i in range(1, len(pts) - 1):
            d0, d1 = _unit(_sub(pts[i], keep[-1])), _unit(_sub(pts[i + 1], pts[i]))
            if _dot(d0, d1) < 1 - 1e-9:
                keep.append(pts[i])
        keep.append(pts[-1])
        self.points = keep
        dirs = [_unit(_sub(b, a)) for a, b in zip(keep, keep[1:])]
        lens = [_len(_sub(b, a)) for a, b in zip(keep, keep[1:])]
        tans, thetas = [0.0] * len(keep), [0.0] * len(keep)
        for i in range(1, len(keep) - 1):
            c = max(-1.0, min(1.0, _dot(dirs[i - 1], dirs[i])))
            th = math.acos(c)
            if th > PI - 1e-3:
                raise ValueError(f"pipe path doubles back on itself at point {i} {keep[i]}")
            thetas[i], tans[i] = th, R * math.tan(th / 2)
        for i, L in enumerate(lens):
            need = tans[i] + tans[i + 1]
            if need > L + 1e-7:
                raise ValueError(f"pipe leg {i} from {keep[i]} to {keep[i + 1]} is {L:.3f} m, the bends need {need:.3f} m "
                                 f"at bend radius {R:.3f}; lengthen the leg or lower bend_radius")
        self.R = R
        side, nrm = _frame(dirs[0], up)
        pieces, s = [], 0.0
        cur = keep[0]
        self.bends = []
        for i in range(len(dirs)):
            last = i + 1 == len(keep) - 1
            end = keep[i + 1] if last else _sub(keep[i + 1], _scl(dirs[i], tans[i + 1]))
            L = _len(_sub(end, cur))
            if L > 1e-9:
                pieces.append({"kind": "line", "s0": s, "len": L, "p0": cur, "d": dirs[i], "side": side, "nrm": nrm})
                s += L
            if last:
                break
            th = thetas[i + 1]
            w = _unit(_cross(dirs[i], dirs[i + 1]))
            c = _add(end, _scl(_cross(w, dirs[i]), R))
            pieces.append({"kind": "arc", "s0": s, "len": R * th, "c": c, "u0": _sub(end, c), "w": w, "theta": th,
                           "side": side, "nrm": nrm})
            self.bends.append({"centre": c, "axis": w, "angle": th, "s0": s, "s1": s + R * th, "corner": keep[i + 1]})
            s += R * th
            side, nrm = _rot(side, w, th), _rot(nrm, w, th)
            cur = _add(keep[i + 1], _scl(dirs[i + 1], tans[i + 1]))
        self.pieces, self.length = pieces, s

    def piece(self, s):
        for p in self.pieces:
            if s <= p["s0"] + p["len"] + 1e-9:
                return p
        return self.pieces[-1]

    def at(self, s):
        """(point, tangent, side, nrm) at arc length s."""
        s = min(max(s, 0.0), self.length)
        p = self.piece(s)
        f = s - p["s0"]
        if p["kind"] == "line":
            return _add(p["p0"], _scl(p["d"], f)), p["d"], p["side"], p["nrm"]
        ph = f / self.R
        v0 = _cross(p["w"], p["u0"])
        pt = _add(p["c"], _add(_scl(p["u0"], math.cos(ph)), _scl(v0, math.sin(ph))))
        tg = _unit(_add(_scl(p["u0"], -math.sin(ph)), _scl(v0, math.cos(ph))))
        return pt, tg, _rot(p["side"], p["w"], ph), _rot(p["nrm"], p["w"], ph)

    def straight_at(self, s, half=0.0):
        """True when [s - half, s + half] lies on one straight piece."""
        for p in self.pieces:
            if p["kind"] == "line" and p["s0"] - 1e-9 <= s - half and s + half <= p["s0"] + p["len"] + 1e-9:
                return True
        return False

    def samples(self, s0, s1, steps90):
        """Arc lengths from s0 to s1: both ends, every straight/arc boundary between and arc steps."""
        out = [s0, s1]
        for p in self.pieces:
            a, b = p["s0"], p["s0"] + p["len"]
            if b <= s0 + 1e-9 or a >= s1 - 1e-9:
                continue
            if p["kind"] == "arc":
                n = max(2, math.ceil(steps90 * p["theta"] / (PI / 2) - 1e-9))
                out += [a + p["len"] * k / n for k in range(n + 1)]
            else:
                out += [a, b]
        return sorted({round(min(max(x, s0), s1), 9) for x in out})

    def closest(self, point):
        point = _v3(point)
        n = max(200, int(self.length / 0.01))
        return min((_len(_sub(self.at(self.length * k / n)[0], point)), self.length * k / n) for k in range(n + 1))[1]


# --- flanges, bells, seams -------------------------------------------------------------------------------

def _pair_profile(r, F):
    """Closed (r, z) loop of a flanged joint: two flanges back to back on the plane z = 0, with a gasket line."""
    tf, nk, Rf = F["thickness"], F["neck"], F["radius"]
    ri, g, gd = r * 0.9, 0.0015, 0.004
    return [(ri, -tf - nk), (r, -tf - nk), (r + nk, -tf), (Rf, -tf), (Rf, -g), (Rf - gd, -g), (Rf - gd, g), (Rf, g),
            (Rf, tf), (r + nk, tf), (r, tf + nk), (ri, tf + nk)]


def _end_profile(r, F, blind):
    """Open (r, z) profile of a flange ending a pipe, face on z = 0 (outward +z): a bore recess, or solid."""
    tf, nk, Rf = F["thickness"], F["neck"], F["radius"]
    prof = [(0, -tf - nk), (r, -tf - nk), (r + nk, -tf), (Rf, -tf), (Rf, 0)]
    if blind:
        return prof + [(0, 0)]
    db = 0.6 * tf + 0.5 * nk
    return prof + [(F["bore"], 0), (F["bore"], -db), (0, -db)]


def _nut_ring(m, centre, axis, side, nrm, F, z0, z1, count=None, radius=None, offset=0.5, nut=None):
    n = count or F["bolts"]
    Rb = radius or F["bolt_radius"]
    for k in range(n):
        a = 2 * PI * (k + offset) / n
        c = _pt(centre, (side, Rb * math.cos(a)), (nrm, Rb * math.sin(a)))
        m.nut(c, axis, nut or F["nut"], z0, z1, side=_add(_scl(side, math.cos(a)), _scl(nrm, math.sin(a))))


def _circle(g, id_, centre, side, nrm, radius):
    """A closed guide circle (curve node) round an axis, in the plane of side and nrm."""
    tt = T * (2 * PI)
    return g.n(id_, "curve", point=_pt(centre, (side, X(radius) * cos(tt)), (nrm, X(radius) * sin(tt))),
               segments=48, closed=1)


def _flange_pair(round_m, flat_m, c, t, sd, nm, r, F, sides, bolts):
    round_m.lathe(_pair_profile(r, F), c, t, sides, side=nm, closed=True, phase=PI / sides)
    if bolts == "hex":
        tf, hn = F["thickness"], F["nut_height"]
        _nut_ring(flat_m, c, t, sd, nm, F, tf - 0.001, tf + hn)
        _nut_ring(flat_m, c, t, sd, nm, F, -tf - hn, -tf + 0.001)


def _blind(round_m, flat_m, face, d, sd, nm, D, F, sides, bolts):
    tb = F["thickness"] * 1.1
    round_m.lathe([(0, 0), (F["radius"], 0), (F["radius"], tb), (D * 0.25, tb), (D * 0.25, tb + 0.01 + 0.02 * D),
                   (0, tb + 0.01 + 0.02 * D)], face, d, sides, side=nm)
    if bolts == "hex":
        _nut_ring(flat_m, face, d, sd, nm, F, tb - 0.001, tb + F["nut_height"])


# --- pipe runs -------------------------------------------------------------------------------------------

_ENDS = ("flange", "blind", "cap", "open", "plain")


def pipe_run(self, id_, points, diameter, bend_radius=None, sides=16, bend_steps=8, ends="flange", joints="flange",
             pipe_length=3.66, bend_joints=True, bolts="hex", lagging=None, bands=None, up=(0, 0, 1), slot=None,
             fitting_slot=None, smooth=True):
    """A pipe along a polyline, every corner bent at `bend_radius` (torus segments, not mitres). Metres.

    Frame: document coordinates; `points` are the centre line's corners (any directions), the pipe starts at the
    first and ends at the last. diameter: outside diameter D (number, or X for a bare run). bend_radius: centre
    line radius of every bend (default 1.5 D); each leg must hold the tangent lengths R tan(angle / 2) of the
    bends at its ends, else ValueError. sides: barrel sides; bend_steps: rings per 90 degrees of bend.
    ends: flange (a flange whose face is the end point, bore showing), blind (flange and bolted blind flange,
    meeting on the end point), cap (a socket cap from the end point, dished about 0.5 D beyond it), open (a cut end showing the bore),
    plain (flat); or a (start, end) pair.
    joints: "flange" (pairs of flanges with hexagon nuts on both faces), "bell" (bell and spigot: a beaded
    bell over each joint, on the straight side), "riveted" (riveted steel: double-riveted lap seams round the
    pipe and a riveted longitudinal seam on each straight; surface detail) or None. Joints fall on the bend
    tangent points (bend_joints) and divide each straight into lengths of at most pipe_length (None: none).
    bolts: "hex" (nut geometry), "detail" (hexagon heads as surface detail, for runs out of reach) or None.
    lagging: lagging thickness over the pipe (the barrel grows by it; flanges stay pipe size); bands: spacing
    of strap bands round the barrel (surface detail). up: the first frame's `nrm` leans toward it (the
    longitudinal seam lies there).
    Returns the node id with attributes: path (dense centre line), points, length, diameter, radius (pipe),
    outer (barrel radius), bend_radius, bends [{centre, axis, angle, s0, s1, corner}], joints [(s, kind)],
    ends, at(s) -> (point, tangent, side, nrm), locate(at) -> s (fraction, point or {"s": m}), occupied
    [(s0, s1)] (taken by joints, fittings and valves; supports keep clear of it).
    Triangles (D 0.3, 16 sides): 32 per straight, 32 per bend step (a 90-degree bend +160); a flanged joint
    1,216 with its 24 nuts (640 with bolts="detail"), a flanged end 768, a blind end 1,216.
    Example: run = g.pipe_run("main", [[0, 0, 0], [4, 0, 0], [4, 3, 0], [4, 3, 2]], 0.3, slot="iron")"""
    D = X(diameter)
    numeric = _num(D.v)
    if not numeric:
        assert bend_radius is not None, "pipe_run: an X diameter needs a numeric bend_radius"
        assert joints in (None, "riveted"), "pipe_run: an X diameter takes no flanges or bells (joints None or riveted)"
    Dn = float(D.v) if numeric else None
    R = float(bend_radius) if bend_radius is not None else 1.5 * Dn
    r = D * 0.5
    if numeric:
        assert R > Dn * 0.6, f"pipe_run: bend radius {R:.3f} must exceed the pipe radius ({Dn / 2:.3f}) with room"
    ro = r + lagging if lagging else r
    path = _Path(points, R, up)
    L = path.length
    e0, e1 = (ends, ends) if isinstance(ends, str) else tuple(ends)
    assert e0 in _ENDS and e1 in _ENDS, f"pipe_run ends: {_ENDS}"
    if not numeric:
        assert e0 not in ("flange", "blind") and e1 not in ("flange", "blind"), "pipe_run: an X diameter takes no flanged ends"
    assert joints in (None, "flange", "bell", "riveted"), "pipe_run joints: flange, bell, riveted or None"
    assert bolts in ("hex", "detail", None), "pipe_run bolts: hex, detail or None"
    F = flange_dims(Dn) if numeric else None
    fsides = max(int(sides), 20)
    round_m, flat_m = _Mesh(), _Mesh()
    occupied = []

    jpos = []                                           # joint positions on the arc length
    if joints:
        if bend_joints:
            for b in path.bends:
                jpos += [(b["s0"], "bend-start"), (b["s1"], "bend-end")]
        if pipe_length:
            for p in path.pieces:
                if p["kind"] == "line":
                    k = math.ceil(p["len"] / float(pipe_length) - 1e-9)
                    jpos += [(p["s0"] + p["len"] * i / k, "length") for i in range(1, k)]
    joint_list = []
    for s, kind in sorted(jpos):
        if 1e-6 < s < L - 1e-6 and not (joint_list and abs(joint_list[-1][0] - s) < 1e-6):
            joint_list.append((s, kind))

    # Barrel: stations from the start end to the finish end; flanged ends hide the barrel's end in the flange.
    bv = 0.004
    trims = [0.0, 0.0]
    if F:
        for i, e in enumerate((e0, e1)):
            if e in ("flange", "blind"):
                db = 0.6 * F["thickness"] + 0.5 * F["neck"]
                trims[i] = db + 0.5 * (F["thickness"] + F["neck"] - db)

    def end_stations(e, s_end, out_sign):
        """Stations from the barrel's last ring to the tip of an end, travelling outward (out_sign * tangent).
        Bevelled ends (all but cap) start at the end point; the barrel stops a bevel short of it."""
        p, t, sd, nm = path.at(s_end)
        to = _scl(t, out_sign)
        if e in ("plain", "flange", "blind"):
            return [(_pt(p), sd, nm, ro - bv), (_pt(p), sd, nm, 0)]
        if e == "open":
            ri = r - (_wall(Dn) if numeric else D * 0.03 + 0.008)
            room = s_end if out_sign < 0 else L - s_end
            depth = min(Dn, 0.8 * room) if numeric else kitlib.mn(D * 0.5, 0.8 * room)
            return [(_pt(p), sd, nm, ro - bv), (_pt(p), sd, nm, ri), (_pt(p, (to, X(depth) * -1)), sd, nm, ri),
                    (_pt(p, (to, X(depth) * -1)), sd, nm, 0)]
        h, Lc, tc = D * 0.22, D * 0.25 + 0.02, D * 0.04 + 0.006          # cap: socket from the end point, dished beyond
        rc = ro + tc
        st = [(_pt(p), sd, nm, rc - bv), (_pt(p, (to, bv)), sd, nm, rc)]
        for k in range(7):
            phi = PI / 2 * k / 6
            st.append((_pt(p, (to, Lc + h * math.sin(phi))), sd, nm, rc * math.cos(phi) if k < 6 else 0))
        return st

    s_lo, s_hi = trims[0], L - trims[1]
    b0, b1 = (0.0 if e == "cap" else bv for e in (e0, e1))
    sts = list(reversed(end_stations(e0, s_lo, -1.0)))
    for s in path.samples(s_lo + b0, s_hi - b1, bend_steps):
        p, t, sd, nm = path.at(s)
        sts.append((_pt(p), sd, nm, ro))
    sts += end_stations(e1, s_hi, 1.0)
    barrel = _Mesh()
    barrel.stations(sts, int(sides), phase=PI / int(sides))

    for s, kind in joint_list:
        p, t, sd, nm = path.at(s)
        if joints == "flange":
            _flange_pair(round_m, flat_m, p, t, sd, nm, Dn / 2, F, fsides, bolts)
            w = F["thickness"] + F["neck"] + F["nut_height"]
            occupied.append((s - w, s + w))
        elif joints == "bell":
            Lb, tb = 0.1 + 0.25 * Dn, _wall(Dn) * 1.6 + 0.006
            direction = -1.0 if kind == "bend-start" else 1.0
            rb, bd = Dn / 2 + tb, 0.25 * tb + 0.003
            prof = [(Dn / 2 * 0.9, 0), (rb + bd, 0), (rb + bd, 0.22 * Lb), (rb, 0.3 * Lb), (rb, Lb),
                    (Dn / 2 + 0.25 * tb, Lb + 1.5 * tb), (Dn / 2 * 0.9, Lb + 1.5 * tb)]
            round_m.lathe(prof, p, _scl(t, direction), fsides, side=nm, closed=True)
            a, b = sorted((s, s + direction * (Lb + 1.5 * tb)))
            occupied.append((a - 0.02, b + 0.02))
        else:
            occupied.append((s - 0.04, s + 0.04))
    if F:
        for e, s_end, sign in ((e0, 0.0, -1.0), (e1, L, 1.0)):
            if e not in ("flange", "blind"):
                continue
            p, t, sd, nm = path.at(s_end)
            to = _scl(t, sign)
            sd_o = _cross(nm, to)
            round_m.lathe(_end_profile(Dn / 2, F, e == "blind"), p, to, fsides, side=nm)
            if e == "blind":
                _blind(round_m, flat_m, p, to, sd_o, nm, Dn, F, fsides, bolts)
            if bolts == "hex":
                _nut_ring(flat_m, p, to, sd_o, nm, F, -F["thickness"] - F["nut_height"], -F["thickness"] + 0.001)
            w = F["thickness"] + F["neck"] + F["nut_height"]
            occupied.append((s_end - w, s_end + w))

    body = barrel.emit(self, id_ + "-barrel", slot, smooth)
    k = [0]

    def nid(tag):
        k[0] += 1
        return f"{id_}-{tag}{k[0]}"

    if joints == "riveted":                             # surface detail on the barrel
        pitch = 0.035 + 0.05 * (Dn if numeric else 0.3)
        for s, kind in joint_list:
            p, t, sd, nm = path.at(s)
            body = self.stroke(nid("lap"), body, _circle(self, nid("lapc"), p, sd, nm, ro), profile="lap", width=0.006,
                               height=0.003, reach=0.05)
            for off in (-0.022, 0.022):
                q, _, sd2, nm2 = path.at(min(max(s + off, 0), L))
                body = self.rivets(nid("row"), body, _circle(self, nid("rowc"), q, sd2, nm2, ro), pitch, radius=0.008,
                                   height=0.005, reach=0.05)
        for pc in path.pieces:
            if pc["kind"] == "line" and pc["len"] > 0.3:
                gid = nid("seamg")
                self.guide(gid, _pt(pc["p0"], (pc["nrm"], ro), (pc["d"], 0.06)), _pt(pc["p0"], (pc["nrm"], ro), (pc["d"], pc["len"] - 0.06)))
                body = self.rivets(nid("seam"), body, gid, 0.06, radius=0.008, height=0.005, reach=0.05)
    if bands:
        n = max(1, int(L / float(bands)))
        for i in range(n):
            p, t, sd, nm = path.at((i + 0.5) * L / n)
            body = self.stroke(nid("band"), body, _circle(self, nid("bandc"), p, sd, nm, ro), profile="band", width=0.025,
                               height=0.002, reach=0.05)
    parts = [body]
    fit = round_m.emit(self, id_ + "-fittings", fitting_slot or slot, True)
    if fit:
        if bolts == "detail" and joints == "flange":
            for s, kind in joint_list:
                p, t, sd, nm = path.at(s)
                for sg in (-1, 1):
                    c = _circle(self, nid("boltc"), _pt(p, (t, sg * (F["thickness"] + 0.01))), sd, nm, F["bolt_radius"])
                    fit = self.fasteners(nid("bolts"), fit, c, head="hex", count=F["bolts"], radius=F["nut"],
                                         height=F["nut_height"], washer=F["nut"] * 1.4, reach=0.05)
        parts.append(fit)
    nuts = flat_m.emit(self, id_ + "-nuts", fitting_slot or slot, False)
    if nuts:
        parts.append(nuts)
    node = self.join(id_, *parts)
    dense = [list(path.at(s)[0]) for s in path.samples(0.0, L, bend_steps)]

    def locate(at):
        """Arc length of `at`: a fraction of the run (number), a point (closest), or {"s": metres}."""
        if isinstance(at, dict):
            return float(at["s"])
        if isinstance(at, (int, float)):
            return float(at) * L
        return path.closest(at)

    return Built(node, path=dense, points=[list(p) for p in path.points], length=L, diameter=diameter, radius=r, outer=ro,
                 bend_radius=R, bends=path.bends, joints=joint_list, ends=(e0, e1), at=path.at, locate=locate,
                 straight_at=path.straight_at, occupied=occupied, flange=F, sides=int(sides), is_run=True)



# --- fittings: tees, crosses, reducers ------------------------------------------------------------------

def _need_numeric(run, what):
    assert getattr(run, "is_run", False), f"{what}: pass the Built returned by pipe_run"
    assert run.flange is not None, f"{what}: the run's diameter must be a number"


def _place_on(run, at, half, what):
    s = run.locate(at)
    if not run.straight_at(s, half):
        raise ValueError(f"{what}: {half * 2:.3f} m of straight pipe needed at {s:.3f} m along the run "
                         f"(length {run.length:.3f}); move it off the bends and ends")
    for a, b in run.occupied:
        if a < s + half and s - half < b:
            raise ValueError(f"{what}: overlaps a joint or fitting at {a:.3f}..{b:.3f} m along the run")
    run.occupied.append((s - half, s + half))
    return s


def _square(v, axis, fallback):
    v = _v3(v)
    w = _sub(v, _scl(axis, _dot(v, axis)))
    return _unit(w) if _len(w) > 1e-6 else _unit(fallback)


def tee(self, id_, run, at, branch=(0, 0, 1), length=None, diameter=None, cross=False, end="flange", bolts="hex",
        slot=None):
    """A flanged cast-iron tee (or cross) set into a run: the run passes through the body, which is flanged to
    it on both sides (flange pairs with nuts); each branch ends in a flange (end "flange", its face the port)
    or a bolted blind flange (end "blind").
    Frame: document coordinates. at: where on the run (fraction 0..1, a point, or {"s": metres}), on a
    straight. branch: the branch direction, squared to the run. length: centre to branch face, also centre to
    each main face (default 0.9 D + 0.1). diameter: branch outside diameter (default the run's: an equal tee).
    cross: a second branch opposite the first.
    Returns the node id with ports [{"point", "direction", "diameter"}] per branch face (start a pipe_run there
    with ends=("flange", ...) and the two faces meet) and s (arc length on the run).
    Triangles: 3,560 for an equal 0.3 m tee with hexagon nuts (its two main joints included).
    Example: t = g.tee("tee", run, 0.3); g.pipe_run("riser", [t.ports[0]["point"], [1, 0, 3]], 0.3, ends=("flange", "blind"))"""
    _need_numeric(run, "tee")
    assert end in ("flange", "blind"), "tee end: flange or blind"
    Dm = float(run.diameter)
    Db = float(diameter) if diameter is not None else Dm
    assert Db <= Dm + 1e-9, "tee: the branch may not be wider than the run"
    Fm, Fb = run.flange, flange_dims(Db)
    Lb = float(length) if length is not None else 0.9 * Dm + 0.1
    assert Lb > Fm["radius"] + Fb["thickness"] + 0.02, "tee: branch length too short to clear the main flanges"
    s = _place_on(run, at, Lb + Fm["thickness"] + Fm["neck"] + Fm["nut_height"], "tee")
    p, t, sd, nm = run.at(s)
    bdir = _square(branch, t, nm)
    rm, rb = Dm / 2 * 1.1 + 0.004, Db / 2 * 1.1 + 0.004
    round_m, flat_m = _Mesh(), _Mesh()
    sides = max(run.sides, 20)
    h = Lb - Fm["thickness"] * 0.5
    round_m.lathe([(Dm / 2 * 0.9, -h), (rm, -h), (rm, h), (Dm / 2 * 0.9, h)], p, t, sides, side=nm, closed=True)
    for sg in (-1, 1):
        _flange_pair(round_m, flat_m, _pt(p, (t, sg * Lb)), t, sd, nm, Dm / 2, Fm, sides, bolts)
    ports = []
    for d in [bdir] + ([_scl(bdir, -1)] if cross else []):
        bn = _square(t, d, nm)
        bs = _cross(bn, d)
        tf, nk = Fb["thickness"], Fb["neck"]
        prof = [(0, 0), (rb, 0), (rb, Lb - tf - nk), (Db / 2 + nk, Lb - tf), (Fb["radius"], Lb - tf), (Fb["radius"], Lb)]
        if end == "flange":
            db = 0.6 * tf + 0.5 * nk
            prof += [(Fb["bore"], Lb), (Fb["bore"], Lb - db), (0, Lb - db)]
        else:
            prof += [(0, Lb)]
        round_m.lathe(prof, p, d, sides, side=bn)
        face = _pt(p, (d, Lb))
        if bolts == "hex":
            _nut_ring(flat_m, face, d, bs, bn, Fb, -tf - Fb["nut_height"], -tf + 0.001)
        if end == "blind":
            _blind(round_m, flat_m, face, d, bs, bn, Db, Fb, sides, bolts)
        ports.append({"point": face, "direction": list(d), "diameter": Db})
    node = _emit_all(self, id_, [("body", round_m, slot, True), ("nuts", flat_m, slot, False)])
    return Built(node, ports=ports, s=s)


def reducer(self, id_, at, d2, direction=None, d1=None, length=None, bolts="hex", slot=None):
    """A flanged concentric reducer (taper): one lathe with a flange at each end, nuts on the flange backs.
    Frame: document coordinates. at: a pipe_run (the reducer continues from its end, which should end in a
    flange: the faces meet) or a point (the inlet face), then `direction` (flow, unit) and `d1` are needed.
    d1 / d2: inlet / outlet outside diameters; length: face to face (default 0.6 d1 + 0.25).
    Returns the node id with ports [inlet, outlet] ({"point", "direction" (outward), "diameter"}).
    Triangles: 1,200 for 0.3 to 0.2 m.
    Example: red = g.reducer("taper", run, 0.2); g.pipe_run("on", [red.ports[1]["point"], [9, 0, 1]], 0.2)"""
    if getattr(at, "is_run", False):
        _need_numeric(at, "reducer")
        p, t, _, _ = at.at(at.length)
        d1 = float(at.diameter)
    else:
        assert direction is not None and d1 is not None, "reducer at a point needs direction and d1"
        p, t = _v3(at), _unit(_v3(direction))
    d1, d2 = float(d1), float(d2)
    F1, F2 = flange_dims(d1), flange_dims(d2)
    L = float(length) if length is not None else 0.6 * d1 + 0.25
    t1, t2 = F1["thickness"], F2["thickness"]
    assert L > t1 + t2 + F1["neck"] + F2["neck"] + 0.05, "reducer: too short for its flanges"
    r1, r2 = d1 / 2 * 1.06, d2 / 2 * 1.06
    prof = [(0, 0), (F1["radius"], 0), (F1["radius"], t1), (r1 + F1["neck"], t1), (r1, t1 + F1["neck"]),
            (r2, L - t2 - F2["neck"]), (r2 + F2["neck"], L - t2), (F2["radius"], L - t2), (F2["radius"], L), (0, L)]
    round_m, flat_m = _Mesh(), _Mesh()
    sd, nm = _frame(t)
    round_m.lathe(prof, p, t, 24, side=nm)
    if bolts == "hex":
        _nut_ring(flat_m, p, t, sd, nm, F1, t1 - 0.001, t1 + F1["nut_height"])
        _nut_ring(flat_m, _pt(p, (t, L)), t, sd, nm, F2, -t2 - F2["nut_height"], -t2 + 0.001)
    node = _emit_all(self, id_, [("body", round_m, slot, True), ("nuts", flat_m, slot, False)])
    return Built(node, ports=[{"point": _pt(p), "direction": [-c for c in t], "diameter": d1},
                              {"point": _pt(p, (t, L)), "direction": list(t), "diameter": d2}])


# --- valves ----------------------------------------------------------------------------------------------

def _handwheel(m, centre, axis, W, rs, spokes, side):
    """A dished handwheel: rim torus, `spokes` spokes falling to it from the hub, hub boss."""
    rr = 0.02 * W + 0.006
    dish = 0.08 * W
    rim_c = _pt(centre, (axis, -dish))
    m.torus(rim_c, axis, W / 2 - rr, rr, 40, 8, side=side)
    e1, e2 = _frame(axis, side)
    hub_r = max(2.4 * rs, rr * 1.6)
    for k in range(spokes):
        a = 2 * PI * (k + 0.5) / spokes
        radial = _add(_scl(e1, math.cos(a)), _scl(e2, math.sin(a)))
        m.rod(_pt(centre, (radial, hub_r * 0.6)), _pt(rim_c, (radial, W / 2 - rr)), rr * 0.62, 8)
    m.lathe([(0, -0.035 - 0.02 * W), (hub_r, -0.035 - 0.02 * W), (hub_r, 0.02), (rs * 1.4, 0.03), (0, 0.03)], centre, axis, 16,
            side=side)


def valve(self, id_, run, at, kind="gate", stem=(0, 0, 1), wheel=None, spokes=5, rising=False, bolts="hex",
          slot=None, wheel_slot=None):
    """A flanged valve set into a run, flanged to it on both sides (flange pairs with nuts).
    kind "gate": a tall oval chest across the bore, bolted bonnet, gland, stem and a dished handwheel with
    `spokes` spokes (rising=True: the stem stands above the wheel, as an outside-screw valve's does);
    "globe": a spherical body, round bonnet and a smaller wheel; "check": a swing check valve, a chest with a
    bolted domed cover and the hinge-pin boss, no wheel.
    Frame: document coordinates. at: where on the run (fraction 0..1, a point, or {"s": metres}), on a
    straight. stem: the stem direction, squared to the pipe (default up; a vertical run takes its frame).
    wheel: handwheel diameter (default 0.9 D + 0.12 gate, 0.75 D + 0.1 globe). Face to face 0.8 D + 0.2
    (gate), D + 0.25 (globe), 1.15 D + 0.25 (check).
    Returns the node id with s (arc length), stem (unit), top (wheel centre or cover top), kind.
    Triangles on a 0.3 m run, its two flanged joints (2,432) included: gate 4,832, globe 5,360, check 3,888.
    Example: v = g.valve("valve", run, 0.5, kind="gate", wheel_slot="bronze")"""
    assert kind in ("gate", "globe", "check"), "valve kind: gate, globe or check"
    _need_numeric(run, "valve")
    D = float(run.diameter)
    F = run.flange
    r = D / 2
    L = {"gate": 0.8 * D + 0.2, "globe": D + 0.25, "check": 1.15 * D + 0.25}[kind]
    s = _place_on(run, at, L / 2 + F["thickness"] + F["neck"] + F["nut_height"], f"{kind} valve")
    p, a, sd, nm = run.at(s)
    up = _square(stem, a, nm)
    v = _cross(up, a)                                   # across the pipe; a x v = up
    body, flat, wheel_m = _Mesh(), _Mesh(), _Mesh()
    sides = max(run.sides, 24)
    tf, h = F["thickness"], L / 2 - F["thickness"] * 0.9
    for sg in (-1, 1):
        _flange_pair(body, flat, _pt(p, (a, sg * L / 2)), a, sd, nm, r, F, sides, bolts)
    ri = r * 0.9
    rs = 0.03 * D + 0.008
    if kind == "gate":
        rb, rb2 = r * 1.12 + 0.005, r * 1.2 + 0.006
        body.lathe([(ri, -h), (rb, -h), (rb2, -0.3 * L), (rb2, 0.3 * L), (rb, h), (ri, h)], p, a, sides, side=up, closed=True)
        cu, cv, hc = 0.3 * D + 0.02, 0.62 * D + 0.03, 0.95 * D + 0.05

        def ell(A, B, n=20):
            return [(A * math.cos(2 * PI * k / n), B * math.sin(2 * PI * k / n)) for k in range(n)]
        flat.prism(ell(cu, cv), p, a, v, -0.35 * D, hc)
        fl = 0.05 * D + 0.02
        flat.prism(ell(cu + fl, cv + fl), p, a, v, hc - 0.001, hc + tf)
        for k in range(8):
            ang = 2 * PI * (k + 0.5) / 8
            c = _pt(p, (a, (cu + fl * 0.5) * math.cos(ang)), (v, (cv + fl * 0.5) * math.sin(ang)))
            flat.nut(c, up, F["nut"] * 0.8, hc + tf - 0.001, hc + tf + F["nut_height"] * 0.8, side=a)
        z0 = hc + tf - 0.002
        hb, rg, hg = 0.45 * D + 0.05, 0.07 * D + 0.02, 0.06 * D + 0.02
        body.lathe([(0, z0), (cu * 0.95, z0), (cu * 0.7, z0 + hb * 0.6), (rg * 1.25, z0 + hb), (rg, z0 + hb),
                    (rg, z0 + hb + hg), (0, z0 + hb + hg)], p, up, 20, side=a)
        zt = z0 + hb + hg
        W = float(wheel) if wheel else 0.9 * D + 0.12
        zw = zt + 0.04 * D + 0.05
    elif kind == "globe":
        rb = r * 1.1 + 0.004
        Rg = 0.62 * D + 0.04
        zc = math.sqrt(max(Rg * Rg - rb * rb, 1e-6))
        prof = [(ri, -h), (rb, -h), (rb, -zc)]
        for k in range(1, 12):
            z = -zc + 2 * zc * k / 12
            prof.append((math.sqrt(Rg * Rg - z * z), z))
        prof += [(rb, zc), (rb, h), (ri, h)]
        body.lathe(prof, p, a, sides, side=up, closed=True)
        z0, zf = Rg * 0.7, Rg * 0.95
        rbn, hb, rg = 0.3 * D + 0.01, 0.4 * D + 0.06, 0.07 * D + 0.02
        rfl = rbn + 0.08 * D + 0.02
        body.lathe([(0, z0), (rbn, z0), (rbn, zf), (rfl, zf), (rfl, zf + tf), (rbn * 0.8, zf + tf), (rg, zf + tf + hb),
                    (0, zf + tf + hb)], p, up, 20, side=a)
        _nut_ring(flat, p, up, a, v, F, zf + tf - 0.001, zf + tf + F["nut_height"] * 0.8, count=6,
                  radius=rbn + 0.04 * D + 0.01, nut=F["nut"] * 0.8)
        zt = zf + tf + hb
        W = float(wheel) if wheel else 0.75 * D + 0.1
        zw = zt + 0.04 * D + 0.04
    else:
        rb, rb2 = r * 1.1 + 0.004, r * 1.25 + 0.01
        body.lathe([(ri, -h), (rb, -h), (rb2, -0.25 * L), (rb2, 0.25 * L), (rb, h), (ri, h)], p, a, sides, side=up, closed=True)
        rc, hc = 0.42 * D + 0.02, 0.72 * D + 0.04
        body.lathe([(0, 0), (rc, 0), (rc, hc), (0, hc)], p, up, 24, side=a)
        rcv = rc + 0.08 * D + 0.03
        ztop = hc + tf + 0.04 * D + 0.01
        body.lathe([(0, hc - 0.004), (rcv, hc - 0.004), (rcv, hc + tf), (rc * 0.7, ztop), (0, ztop)], p, up, 24, side=a)
        _nut_ring(flat, p, up, a, v, F, hc + tf - 0.001, hc + tf + F["nut_height"] * 0.8, count=8,
                  radius=rc + 0.04 * D + 0.015, nut=F["nut"] * 0.8)
        pin = _pt(p, (up, hc * 0.62), (a, -0.25 * D))
        body.rod(_pt(pin, (v, -(rc + 0.03))), _pt(pin, (v, rc + 0.03)), 0.07 * D + 0.012, 12, bevel=0.003)
        top = _pt(p, (up, ztop))
    if kind != "check":
        ztop = zw + (0.35 * D + 0.12 if rising else 0.03)
        body.rod(_pt(p, (up, zt - 0.01)), _pt(p, (up, ztop)), rs, 10, bevel=0.002)
        _handwheel(wheel_m, _pt(p, (up, zw)), up, W, rs, int(spokes), a)
        top = _pt(p, (up, zw))
    node = _emit_all(self, id_, [("body", body, slot, True), ("cast", flat, slot, False),
                                 ("wheel", wheel_m, wheel_slot or slot, True)])
    return Built(node, s=s, stem=list(up), top=top, kind=kind)


# --- supports --------------------------------------------------------------------------------------------

def _plane(plane, default_normal):
    if isinstance(plane, (int, float)):
        return (0.0, 0.0, float(plane)), default_normal
    q, n = plane
    return _v3(q), _unit(_v3(n))


def _clip_ring(m, p, a, side, r, width, thick, sides):
    m.lathe([(r - 0.002, -width / 2), (r + thick, -width / 2), (r + thick, width / 2), (r - 0.002, width / 2)], p, a, sides,
            side=side, closed=True, bevel=0.0015)


def supports(self, id_, run, kind="stool", spacing=3.0, plane=None, at=None, start=None, end=None, clearance=0.08,
             width=None, slot=None):
    """Pipe supports along a run, each reaching 2 mm into the pipe and stopping on a plane.
    kind "stool": a cast-iron floor stool (base plate, flared column, saddle cradling the lower 110 degrees);
    "hanger": a rod hanger from above (ceiling plate, rod with a turnbuckle, lug and a strap clip round the
    pipe); "wall": a wall bracket (wall plate, arm under the pipe and a diagonal brace, strap clip; for a run
    rising up the wall: a straight arm to the clip).
    Frame: document coordinates. plane: (point, normal) of the surface the supports stop on, or a number: the
    height of the floor (stool, default 0) or the ceiling (hanger); "wall" needs (point, normal). The normal
    is turned to face the pipe. Positions: every `spacing` metres from start (default half a spacing) to end,
    or the arc lengths in `at`; a position on a bend, where the pipe is not parallel to the plane, or within
    `clearance` of a joint, fitting or valve (run.occupied) slides along the run by up to a third of a
    spacing, else it is dropped. width: cradle width along the pipe (default 0.12 + 0.3 D; straps are narrower).
    Returns the node id with positions (arc lengths used) and feet (points on the plane).
    Triangles per support (0.3 m pipe): stool 364, hanger 544, wall bracket 356.
    Example: g.supports("stools", run, kind="stool", spacing=2.5, plane=0.0)"""
    assert kind in ("stool", "hanger", "wall"), "supports kind: stool, hanger or wall"
    _need_numeric(run, "supports")
    D, r = float(run.diameter), float(_xv(run.outer))
    if kind == "stool":
        q, n = _plane(plane if plane is not None else 0.0, (0.0, 0.0, 1.0))
    elif kind == "hanger":
        assert plane is not None, "supports(hanger) needs the ceiling plane or height"
        q, n = _plane(plane, (0.0, 0.0, -1.0))
    else:
        assert plane is not None and not isinstance(plane, (int, float)), "supports(wall) needs plane=(point, normal)"
        q, n = _plane(plane, None)
    W = float(width) if width else 0.12 + 0.3 * D
    L = run.length
    if at is not None:
        cands = [float(x) for x in at]
    else:
        s, s1 = (float(start) if start is not None else spacing / 2), (float(end) if end is not None else L)
        cands = []
        while s <= s1 + 1e-9:
            cands.append(s)
            s += spacing

    def usable(s):
        if not run.straight_at(s, W / 2 + clearance):
            return None
        p, a, _, _ = run.at(s)
        nn = n if _dot(_sub(p, q), n) >= 0 else _scl(n, -1)
        if abs(_dot(a, nn)) > 0.05:
            return None
        dist = _dot(_sub(p, q), nn)
        if dist < r + 0.03:
            return None
        if any(lo - clearance < s + W / 2 and s - W / 2 < hi + clearance for lo, hi in run.occupied):
            return None
        return p, a, nn, dist

    m, flat = _Mesh(), _Mesh()
    used, feet = [], []
    for c in cands:
        hit = None
        for k in range(int(spacing / 3 / 0.02) + 1):
            for s in ([c] if k == 0 else [c + k * 0.02, c - k * 0.02]):
                if 0 <= s <= L and usable(s):
                    hit = (s, usable(s))
                    break
            if hit:
                break
        if not hit or any(abs(hit[0] - x) < W + 0.1 for x in used):
            continue
        s, (p, a, nn, dist) = hit
        used.append(s)
        foot = _sub(p, _scl(nn, dist))
        feet.append([round(x, 6) for x in foot])
        e = _cross(nn, a)                               # across the pipe, in the plane; e x nn = a
        if kind == "stool":
            alpha = math.radians(55)
            rin, hc = r - 0.002, 0.04 + 0.05 * D
            hw = rin * math.sin(alpha) + 0.02 + 0.03 * D
            out = [(-hw, -(r + hc)), (hw, -(r + hc)), (hw, -rin * math.cos(alpha))]
            for k in range(13):
                ang = -PI / 2 + alpha - 2 * alpha * k / 12
                out.append((rin * math.cos(ang), rin * math.sin(ang)))
            out.append((-hw, -rin * math.cos(alpha)))
            flat.prism(out, p, e, nn, -W / 2, W / 2)
            col = dist - r - hc + 0.004
            rc = 0.18 * D + 0.03
            if col > 0.03:
                m.lathe([(0, 0), (rc * 1.6, 0), (rc * 1.6, 0.02), (rc * 1.15, 0.05), (rc, min(0.12, col * 0.5)), (rc, col),
                         (0, col)], foot, nn, 16, side=a)
            flat.plate(foot, nn, (rc * 3.6, rc * 3.6), 0.016, along=a)
        else:
            tb = 0.006 + 0.01 * D
            wb = min(W, 0.05 + 0.1 * D)
            _clip_ring(m, p, a, nn, r, wb, tb, max(run.sides, 20))
        if kind == "hanger":
            rr = 0.008 + 0.02 * D
            reach = r + tb + 0.05 + 0.05 * D
            flat.bar(_pt(p, (nn, -(r + tb - 0.003))), _pt(p, (nn, -reach)), wb, rr * 3, e)
            L2 = dist - reach + 0.004
            if L2 > 0.05:
                prof = [(0, 0), (rr, 0), (rr, L2 * 0.5 - 0.07), (rr * 2.2, L2 * 0.5 - 0.06), (rr * 2.2, L2 * 0.5 + 0.06),
                        (rr, L2 * 0.5 + 0.07), (rr, L2), (0, L2)] if L2 > 0.3 else [(0, 0), (rr, 0), (rr, L2), (0, L2)]
                m.lathe(prof, foot, nn, 10, side=a)
            flat.plate(foot, nn, (0.12 + 0.1 * D, 0.12 + 0.1 * D), 0.012, along=a)
        elif kind == "wall":
            ah = 0.04 + 0.06 * D
            down = (0.0, 0.0, -1.0)
            dp = _sub(_sub(down, _scl(a, _dot(down, a))), _scl(nn, _dot(down, nn)))
            if _len(dp) > 0.5:                          # pipe running along the wall: arm under it, brace below
                ed = _unit(dp)
                y = r + tb + ah / 2 - 0.002
                arm1 = _pt(p, (ed, y), (nn, r * 0.6 + 0.03))
                flat.bar(_pt(foot, (ed, y)), arm1, wb + 0.02, ah, _scl(ed, -1))
                yb = y + ah / 2 + 0.5 * dist + 0.1
                br0, br1 = _v3(_pt(foot, (ed, yb))), _v3(_pt(p, (ed, y + ah * 0.3), (nn, 0.35 * dist)))
                flat.bar(br0, br1, wb * 0.8, ah * 0.6, e)
                flat.plate(_pt(foot, (ed, (y + yb) / 2)), nn, (wb + 0.06, yb - y + ah * 1.5), 0.012, along=ed)
            else:                                       # pipe rising up the wall: arm straight to the clip
                flat.bar(_pt(foot), _pt(p, (nn, -(r + tb * 0.5))), wb, ah, a)
                flat.plate(foot, nn, (wb + 0.06, ah + 0.1), 0.012, along=a)
    assert used, f"supports {id_}: no position on the run could take a {kind}"
    node = _emit_all(self, id_, [("round", m, slot, True), ("flat", flat, slot, False)])
    return Built(node, positions=used, feet=feet, kind=kind)


# --- routing ---------------------------------------------------------------------------------------------

_AX = {"x": 0, "y": 1, "z": 2}


def route(start, end, order="xyz", start_dir=None, end_dir=None, lead=None, wall=None, standoff=0.3, height=None,
          bend_radius=None):
    """Orthogonal (Manhattan) route for pipe_run: a polyline from start to end along the axes. Metres.
    start_dir: the axis direction the pipe leaves `start` in (a nozzle's), held for `lead` metres; end_dir:
    the direction it arrives at `end` in (flow; [0, 0, -1] comes down onto it), held for `lead` before it.
    wall: (point, normal) of an axis-aligned wall plane the run follows at `standoff` (wall face to pipe
    axis); height: the level the run travels at. The constrained axes (wall, height) are reached first, in
    `order`; the free axes are then travelled in `order`; the constrained ones are left
    last, in `order` or reversed, whichever gives the valid route with fewer corners.
    Zero legs and straight-on points are dropped; a route that doubles back raises ValueError. bend_radius:
    when given, every leg must hold the bends at its ends (R per right-angle corner) or ValueError names the
    leg. lead defaults to R + 0.1 (0.3 without R). Returns a list of [x, y, z].
    Example: route([0, 0, 0.5], [5, 1.5, 0.5], start_dir=[0, 0, 1], end_dir=[0, 0, -1], wall=([0, 3, 0], [0, -1, 0]), standoff=0.4, height=3.2, bend_radius=0.45)"""
    a, b = list(_v3(start)), list(_v3(end))
    R = float(bend_radius) if bend_radius is not None else None
    ld = float(lead) if lead is not None else ((R + 0.1) if R else 0.3)

    def axis_dir(d, what):
        d = _unit(_v3(d))
        k = max(range(3), key=lambda i: abs(d[i]))
        assert abs(abs(d[k]) - 1) < 1e-6, f"route {what}: must be along an axis"
        return k, d

    pts, cur = [a[:]], a[:]
    if start_dir is not None:
        _, d = axis_dir(start_dir, "start_dir")
        cur = [cur[i] + d[i] * ld for i in range(3)]
        pts.append(cur[:])
    tgt = b[:]
    if end_dir is not None:
        _, d = axis_dir(end_dir, "end_dir")
        tgt = [b[i] - d[i] * ld for i in range(3)]
    cons = {}
    if wall is not None:
        k, d = axis_dir(wall[1], "wall normal")
        cons[k] = _v3(wall[0])[k] + d[k] * float(standoff)
    if height is not None:
        cons[2] = float(height)
    axes = [_AX[c] for c in order]
    assert sorted(axes) == [0, 1, 2], "route order: a permutation of xyz"
    held = [k for k in axes if k in cons]
    best, err = None, None
    for leave in ([k for k in held], list(reversed(held))):
        seq = [(k, cons[k]) for k in held] + [(k, tgt[k]) for k in axes if k not in cons] + [(k, tgt[k]) for k in leave]
        try:
            out = _route_points(pts, cur, seq, b if end_dir is not None else None, R)
        except ValueError as ex:
            err = err or ex
            continue
        if best is None or len(out) < len(best):
            best = out
    if best is None:
        raise err
    out = best
    return [[round(c, 6) for c in p] for p in out]


def _route_points(pts, cur, seq, end, R):
    pts, cur = [p[:] for p in pts], cur[:]
    for k, val in seq:
        cur[k] = val
        pts.append(cur[:])
    if end is not None:
        pts.append(list(end))
    clean = []
    for p in pts:
        if not clean or math.dist(p, clean[-1]) > 1e-9:
            clean.append(p)
    out = [clean[0]]
    for i in range(1, len(clean) - 1):
        d0, d1 = _unit(_sub(clean[i], out[-1])), _unit(_sub(clean[i + 1], clean[i]))
        if _dot(d0, d1) < -1 + 1e-9:
            raise ValueError(f"route: the path doubles back at {clean[i]}; change order, lead or the constraints")
        if _dot(d0, d1) < 1 - 1e-9:
            out.append(clean[i])
    out.append(clean[-1])
    if R:
        for i in range(len(out) - 1):
            need = (R if i > 0 else 0.0) + (R if i + 1 < len(out) - 1 else 0.0)
            L = math.dist(out[i], out[i + 1])
            if L + 1e-9 < need:
                raise ValueError(f"route: leg {i} from {out[i]} to {out[i + 1]} is {L:.3f} m, its bends need {need:.3f} m "
                                 f"at bend radius {R}; give more room (lead, standoff, height) or a smaller radius")
    return out


kitlib.Graph.pipe_run = pipe_run
kitlib.Graph.tee = tee
kitlib.Graph.reducer = reducer
kitlib.Graph.valve = valve
kitlib.Graph.supports = supports
kitlib.route = route
kitlib.flange_dims = flange_dims
