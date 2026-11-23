"""Ornament helpers for kitlib: swelling tubes, scroll curves, ironwork bars and scroll brackets.

Built by Opus 5.5 (medium), 2026-11-16. Attached to kitlib.Graph when kitlib is imported (the last line of
kitlib.py), so builders call g.swell_tube(...), g.flat_bar(...), g.scroll_bracket(...); the curve functions are
plain functions here and static methods on Graph (g.volute(...) is kitlib_ornament.volute(...)).

Curves are plane curves in their own (x, y) plane, metres, traced by integrating a curvature profile (a piecewise
Euler / log spiral), so curls are true spirals with continuous curvature, turn counts are exact and end radii are
radii of curvature (or, for volute(kind="log"), distances from the eye). Each returns a Curve: a list of (x, y)
points with the curvature, arc length and eye centres kept alongside. Members lift 2D curves into a plane of the
document ("xz" by default: x stays x, y becomes z) and build one closed `surface` shell each; they refuse paths
that would fold the bar into itself. Numbers only inside a member; `at`, `rotate` and `scale` may be X:
`scale` a number or [sx, sy, sz], each a number or X, so scale=[C("hand"), 1, 1] mirrors a member across x
(the engine flips the winding of mirrored parts; a control factor at 0 reads as +1, see _place).
Tests and sample sheet: recipes/test_kitlib_ornament.py.
"""
import math

import kitlib
from kitlib import PI, X, Graph, _facing, _num, _v, absx, iff, lt

TAU = 2 * PI


# ----------------------------------------------------------------------------------------------- curves
class Curve(list):
    """Points (x, y) of a plane curve, metres. Alongside: kappa (signed curvature at each point, 1/m, + turns
    left), s (arc length at each point), heading (tangent angle, radians, unwrapped), parts (name -> (first,
    last) point index), eyes (centres of the curls, start first) and length."""

    def __init__(self, pts=(), **meta):
        super().__init__(pts)
        self.kappa, self.s, self.heading = meta.get("kappa", []), meta.get("s", []), meta.get("heading", [])
        self.parts, self.eyes = meta.get("parts", {}), meta.get("eyes", [])
        self.length = self.s[-1] if self.s else 0.0

    def turns(self, part=None):
        """Net turning of the tangent over a part (or the whole curve), in turns (+ left)."""
        i0, i1 = self.parts[part] if part else (0, len(self) - 1)
        return (self.heading[i1] - self.heading[i0]) / TAU


def _seg_fns(kind, L, a, b):
    """Curvature and heading change at arc length u of one segment. kind "k": curvature a -> b linear in arc
    length (a clothoid piece); "r": radius of curvature a -> b linear in arc length (a logarithmic spiral piece;
    a and b share a sign, the side it turns to)."""
    if kind == "k":
        return (lambda u: a + (b - a) * u / L), (lambda u: a * u + (b - a) * u * u / (2 * L))
    if abs(b - a) < 1e-12:
        return (lambda u: 1.0 / a), (lambda u: u / a)
    return (lambda u: 1.0 / (a + (b - a) * u / L)), (lambda u: L / (b - a) * math.log((a + (b - a) * u / L) / a))


def _r_length(turning, r0, r1):
    """Length of a log-spiral piece turning `turning` radians while its radius of curvature goes r0 -> r1."""
    r0, r1 = abs(r0), abs(r1)
    return abs(turning) * r0 if abs(r1 - r0) < 1e-12 else abs(turning) * abs(r1 - r0) / abs(math.log(r1 / r0))


def _trace(segs, start=(0.0, 0.0), heading=0.0, per_turn=40, max_step=0.05, fine=400):
    """Integrate segments [(name, kind, length, a, b)] from `start` along `heading`. Output points are spaced so
    that each step turns at most 1/per_turn of a turn and runs at most max_step."""
    x, y, th = float(start[0]), float(start[1]), float(heading)
    k0 = _seg_fns(*segs[0][1:])[0](0.0)
    rows = [(0.0, 0.0, x, y, th, k0)]                    # (measure, s, x, y, heading, kappa)
    bounds, m, s_tot = [], 0.0, 0.0
    for name, kind, L, a, b in segs:
        kap, dth = _seg_fns(kind, L, a, b)
        nf = fine                                          # coarse while solving; final: rows turn 0.004 rad at most
        if fine >= 400:
            nf = max(fine, math.ceil(L * max(abs(kap(0.0)), abs(kap(L))) / 0.004))
        th0, ds = th, L / nf
        for j in range(1, nf + 1):
            u0, u1 = (j - 1) * ds, j * ds
            ta, tm, tb = th0 + dth(u0), th0 + dth((u0 + u1) / 2), th0 + dth(u1)
            x += ds / 6 * (math.cos(ta) + 4 * math.cos(tm) + math.cos(tb))
            y += ds / 6 * (math.sin(ta) + 4 * math.sin(tm) + math.sin(tb))
            m += ds / max_step + abs(tb - ta) * per_turn / TAU
            rows.append((m, s_tot + u1, x, y, tb, kap(u1)))
        th, s_tot = th0 + dth(L), s_tot + L
        bounds.append((name, s_tot - L, s_tot))
    n = max(2, math.ceil(m))
    pts, ks, ss, hs, r = [], [], [], [], 0
    for i in range(n + 1):
        target = m * i / n
        while r < len(rows) - 2 and rows[r + 1][0] < target:
            r += 1
        A, B = rows[r], rows[r + 1]
        f = 0.0 if B[0] == A[0] else min(1.0, max(0.0, (target - A[0]) / (B[0] - A[0])))
        pts.append((A[2] + (B[2] - A[2]) * f, A[3] + (B[3] - A[3]) * f))
        ss.append(A[1] + (B[1] - A[1]) * f)
        hs.append(A[4] + (B[4] - A[4]) * f)
        ks.append(A[5] + (B[5] - A[5]) * f)
    parts = {}
    for name, s0, s1 in bounds:
        i0 = min(range(len(ss)), key=lambda i: abs(ss[i] - s0))
        i1 = min(range(len(ss)), key=lambda i: abs(ss[i] - s1))
        parts[name] = (parts[name][0], i1) if name in parts else (i0, i1)
    return Curve(pts, kappa=ks, s=ss, heading=hs, parts=parts)


def _centre(p, heading, kappa):
    """Centre of curvature at a point."""
    return (p[0] - math.sin(heading) / kappa, p[1] + math.cos(heading) / kappa)


def _hand(hand):
    assert hand in ("left", "right"), "hand is left (counter-clockwise in the plane) or right"
    return 1.0 if hand == "left" else -1.0


def volute(turns=2.0, r_start=0.12, r_end=0.015, kind="log", hand="left", start=(0.0, 0.0), heading=0.0,
           lead=0.0, per_turn=40):
    """A volute: a spiral from radius r_start in to its eye at r_end, `turns` turns. Plane (x, y), metres.

    The curve starts at `start` heading along `heading` (radians from +x), after a straight `lead`, and turns
    left (counter-clockwise) or right. kind "log": a logarithmic spiral, the classical smith's and Ionic
    volute; r_start and r_end are distances from the eye (curve.eyes[0]), each turn shrinks by the same ratio.
    kind "euler": a clothoid whose curvature grows linearly along it; r_start and r_end are radii of
    curvature, eyes[0] the centre of curvature at the end. Returns a Curve (parts "lead", "curl").
    Example: g.flat_bar("scroll", volute(2.5, 0.12, 0.012), 0.02, 0.008)"""
    sg, th = _hand(hand), TAU * turns
    assert turns > 0 and r_start > r_end > 0, "volute: turns > 0 and r_start > r_end > 0"
    segs = [("lead", "k", lead, 0.0, 0.0)] if lead > 0 else []
    b = math.log(r_start / r_end) / th
    if kind == "log":
        q = math.sqrt(1 + b * b)                          # radius of curvature / distance from the pole
        segs.append(("curl", "r", _r_length(th, r_start * q, r_end * q), sg * r_start * q, sg * r_end * q))
    else:
        assert kind == "euler", "volute kind is log or euler"
        segs.append(("curl", "k", 2 * th / (1 / r_start + 1 / r_end), sg / r_start, sg / r_end))
    c = _trace(segs, start, heading, per_turn, max_step=max(r_start, 0.01) / 3)
    p, h = c[-1], c.heading[-1]
    if kind == "log":
        a = h + sg * (PI / 2 - math.atan(b))              # the pole lies a little ahead of the inward normal
        c.eyes = [(p[0] + r_end * math.cos(a), p[1] + r_end * math.sin(a))]
    else:
        c.eyes = [_centre(p, h, c.kappa[-1])]
    return c


def _solve(f, lo, hi, target, what):
    """Bisection on a function rising through target on [lo, hi]."""
    flo, fhi = f(lo), f(hi)
    assert flo <= target <= fhi, f"{what}: cannot reach {target:.3f} (range {flo:.3f} .. {fhi:.3f})"
    for _ in range(48):
        mid = (lo + hi) / 2
        if f(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _pair(v):
    return (v, v) if _num(v) else tuple(v)


def _two_eyed(build, height, eyes, what):
    """Solve the outer radius R of a two-eyed scroll so its eye centres are `height` apart."""
    a, b = eyes

    def dist(R, fine=120):
        c = build(R, fine)
        e0 = _centre(c[0], c.heading[0], c.kappa[0])
        e1 = _centre(c[-1], c.heading[-1], c.kappa[-1])
        return math.hypot(e1[0] - e0[0], e1[1] - e0[1])
    R = _solve(dist, 1.3 * max(a, b), 60 * height, height, what)
    c = build(R, 400)
    c.eyes = [_centre(c[0], c.heading[0], c.kappa[0]), _centre(c[-1], c.heading[-1], c.kappa[-1])]
    c.outer = R
    return c


def c_scroll(height=0.3, eyes=0.02, turns=1.25, spine=0.35, belly=3.0, hand="left", start=(0.0, 0.0), heading=0.0,
             per_turn=40):
    """A C-scroll: two volutes curling to the same side, joined by a flatter spine. Plane (x, y), metres.

    Traced from the first eye out, along the spine and into the second eye. eyes: radius of curvature at each end
    (a number or a pair); turns: turns of each curl (number or pair). The curls are log-spiral pieces growing
    from the eye radius to R; the spine eases (Euler ramps one R long) to a radius `belly` * R and back, turning
    `spine` turns in all. R is solved so the eye centres (curve.eyes) are `height` apart; curve.outer is R.
    The curve turns left or right throughout. Returns a Curve (parts "curl1", "spine", "curl2").
    Example: c = c_scroll(0.35, eyes=0.018, turns=(1.5, 1.0)); g.flat_bar("c", c, 0.02, 0.008)"""
    (a, b), (t1, t2), sg = _pair(eyes), _pair(turns), _hand(hand)
    ramp_turn = (1 + 1 / belly) / 2                       # turning of each ramp, radians per R of length (x R / R)
    arc = TAU * spine - 2 * ramp_turn
    assert arc >= 0, "c_scroll: spine too short for its ramps; raise spine or lower belly"

    def build(R, fine):
        S = belly * R
        return _trace([("curl1", "r", _r_length(TAU * t1, a, R), sg * a, sg * R),
                       ("spine", "k", R, sg / R, sg / S), ("spine", "k", arc * S, sg / S, sg / S),
                       ("spine", "k", R, sg / S, sg / R),
                       ("curl2", "r", _r_length(TAU * t2, R, b), sg * R, sg * b)],
                      start, heading, per_turn, max_step=R / 4, fine=fine)
    return _two_eyed(build, height, (a, b), "c_scroll height")


def s_scroll(height=0.4, eyes=0.02, turns=1.25, waist=1.2, hand="left", start=(0.0, 0.0), heading=0.0, per_turn=40):
    """An S-scroll: two volutes curling to opposite sides, joined by an inflected waist. Plane (x, y), metres.

    Traced from the first eye (turning to `hand`) out, through the waist (an Euler inflection `waist` * R long,
    curvature +1/R to -1/R) and into the second eye. eyes, turns: per end, number or pair. R, the radius where
    the curls meet the waist, is solved so the eye centres (curve.eyes) are `height` apart; curve.outer is R.
    Returns a Curve (parts "curl1", "waist", "curl2").
    Example: g.round_bar("s", s_scroll(0.4, eyes=0.016), 0.007, taper=0.6)"""
    (a, b), (t1, t2), sg = _pair(eyes), _pair(turns), _hand(hand)

    def build(R, fine):
        return _trace([("curl1", "r", _r_length(TAU * t1, a, R), sg * a, sg * R),
                       ("waist", "k", waist * R, sg / R, -sg / R),
                       ("curl2", "r", _r_length(TAU * t2, R, b), -sg * R, -sg * b)],
                      start, heading, per_turn, max_step=R / 4, fine=fine)
    return _two_eyed(build, height, (a, b), "s_scroll height")


def whiplash(length=1.0, sway=0.1, recoil=0.8, recoil_radius=None, eye=None, hand="left", snap=True, start=(0.0, 0.0),
             heading=0.0, per_turn=40):
    """Horta's whiplash (coup de fouet): a long S that quickens, then snaps back in a tight recoil. Plane (x, y).

    The S ("lash", arc length `length`, metres) bows first to `hand` over about 60 % of it and then, tighter, to
    the other side; its curvature scale is solved so the bow stands `sway` * chord off the S's chord. The recoil
    ("recoil") reverses at once (a short Euler ramp: the snap) and curls back to `hand`, turning `recoil` turns
    net from the end of the S, from radius `recoil_radius` (default length / 14) to `eye` (default 0.6 of it).
    snap=False: no reversal, the recoil tightens the S's last bow into a hook (turning to the other side).
    Returns a Curve (parts "lash", "recoil"); eyes[0] is the recoil's eye.
    Example: w = whiplash(0.8, recoil=1.1); g.flat_bar("lash", w, 0.018, 0.007, end="leaf")"""
    sg, L = _hand(hand), float(length)
    rr = recoil_radius or L / 14
    re = eye or rr * 0.6

    def build(k, fine, full=True):
        segs = [("lash", "k", 0.40 * L, sg * 0.15 * k, sg * k), ("lash", "k", 0.35 * L, sg * k, -sg * 0.6 * k),
                ("lash", "k", 0.25 * L, -sg * 0.6 * k, -sg * 1.3 * k)]
        if full:
            ramp = 1.2 * rr
            k_end, rs = -sg * 1.3 * k, (sg if snap else -sg)
            turn = ramp * (k_end + rs / rr) / 2           # turning of the ramp into the recoil
            rest = TAU * recoil * rs - turn
            assert rest * rs > 0.2, "whiplash: recoil too small for the snap; raise recoil"
            segs += [("recoil", "k", ramp, k_end, rs / rr), ("recoil", "r", _r_length(rest, rr, re), rs * rr, rs * re)]
        return _trace(segs, start, heading, per_turn, max_step=L / 30, fine=fine)

    def bow(k):
        c = build(k, 80, full=False)
        (x0, y0), (x1, y1) = c[0], c[-1]
        ch = math.hypot(x1 - x0, y1 - y0)
        return max(abs((x - x0) * (y1 - y0) - (y - y0) * (x1 - x0)) / ch for x, y in c) / ch
    k = _solve(bow, 0.0, 10.0 / L, sway, "whiplash sway")
    c = build(k, 400)
    c.eyes = [_centre(c[-1], c.heading[-1], c.kappa[-1])]
    return c


_PLANES = {"xy": ((1, 0, 0), (0, 1, 0)), "xz": ((1, 0, 0), (0, 0, 1)), "yz": ((0, 1, 0), (0, 0, 1))}


def lift(points, plane="xz", origin=(0.0, 0.0, 0.0)):
    """3D points of a plane curve: plane "xz" (x -> x, y -> z; normal -y), "xy" (normal +z) or "yz" (x -> y,
    y -> z; normal +x), offset by origin."""
    e1, e2 = _PLANES[plane]
    return [tuple(origin[i] + x * e1[i] + y * e2[i] for i in range(3)) for x, y in points]


# ----------------------------------------------------------------------------------------------- vectors
def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _addv(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _scl(a, k): return (a[0] * k, a[1] * k, a[2] * k)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(_dot(a, a))
    assert n > 1e-12, "degenerate direction (repeated path points?)"
    return _scl(a, 1.0 / n)


def _dedupe(pts):
    out = [pts[0]]
    for p in pts[1:]:
        if math.dist(p, out[-1]) > 1e-7:
            out.append(p)
    return out


# ----------------------------------------------------------------------------------------------- profiles
def _profile(p):
    """A function of t in [0, 1] from a number, a callable, or knots [(t, value), ...] joined by a monotone
    cubic (it swells and tapers smoothly and never overshoots the knots)."""
    if callable(p):
        return p
    if _num(p):
        return lambda t: float(p)
    k = sorted((float(t), float(v)) for t, v in p)
    xs, ys = [t for t, _ in k], [v for _, v in k]
    if len(k) == 1:
        return lambda t: ys[0]
    h = [xs[i + 1] - xs[i] for i in range(len(k) - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(len(h))]
    m = [d[0]] + [0.0] * (len(k) - 2) + [d[-1]]
    for i in range(1, len(k) - 1):
        if d[i - 1] * d[i] > 0:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])

    def f(t):
        if t <= xs[0]:
            return ys[0]
        if t >= xs[-1]:
            return ys[-1]
        i = max(j for j in range(len(h)) if xs[j] <= t)
        u = (t - xs[i]) / h[i]
        return ((2 * u ** 3 - 3 * u ** 2 + 1) * ys[i] + (u ** 3 - 2 * u ** 2 + u) * h[i] * m[i]
                + (-2 * u ** 3 + 3 * u ** 2) * ys[i + 1] + (u ** 3 - u ** 2) * h[i] * m[i + 1])
    return f


# ----------------------------------------------------------------------------------------------- paths and frames
def _path(path, plane, segments):
    """(3D points, plane normal or None) from a Curve / 2D points (lifted into `plane`), 3D points, or a
    callable t -> (x, y, z) sampled at segments + 1 points."""
    if callable(path):
        pts = [tuple(float(c) for c in path(i / segments)) for i in range(segments + 1)]
    elif len(path[0]) == 2:
        e1, e2 = _PLANES[plane]
        return _densify(_dedupe(lift(path, plane)), segments), _cross(e1, e2)
    else:
        pts = _densify(_dedupe([tuple(float(c) for c in p) for p in path]), segments)
    # A planar 3D path keeps a fixed normal (no twist); otherwise frames are parallel-transported.
    c = _scl(tuple(sum(p[i] for p in pts) for i in range(3)), 1.0 / len(pts))
    nrm = (0.0, 0.0, 0.0)
    for a, b in zip(pts, pts[1:] + pts[:1]):
        nrm = _addv(nrm, _cross(_sub(a, c), _sub(b, c)))
    if math.sqrt(_dot(nrm, nrm)) > 1e-9:
        n = _unit(nrm)
        span = max(math.dist(p, c) for p in pts)
        if max(abs(_dot(_sub(p, c), n)) for p in pts) < 1e-6 * max(span, 1e-3):
            return pts, n
    return pts, None


def _densify(pts, segments):
    """Points of a sparse path refined by a centripetal Catmull-Rom spline (through every given point) so no
    step is longer than 1/segments of its length; dense paths (Curves) pass through unchanged."""
    s = _arclen(pts)
    step = s[-1] / segments
    if max(b - a for a, b in zip(s, s[1:])) <= step * 1.5:
        return pts
    ext = [_addv(pts[0], _sub(pts[0], pts[1]))] + pts + [_addv(pts[-1], _sub(pts[-1], pts[-2]))]
    out = [pts[0]]
    for i in range(1, len(ext) - 2):
        P = ext[i - 1:i + 3]
        t = [0.0]
        for a, b in zip(P, P[1:]):
            t.append(t[-1] + max(1e-9, math.dist(a, b)) ** 0.5)
        n = max(1, math.ceil(math.dist(P[1], P[2]) / step))
        for j in range(1, n + 1):
            u = t[1] + (t[2] - t[1]) * j / n
            lerp = lambda a, b, ta, tb: _addv(_scl(a, (tb - u) / (tb - ta)), _scl(b, (u - ta) / (tb - ta)))
            A1, A2, A3 = lerp(P[0], P[1], t[0], t[1]), lerp(P[1], P[2], t[1], t[2]), lerp(P[2], P[3], t[2], t[3])
            B1, B2 = lerp(A1, A2, t[0], t[2]), lerp(A2, A3, t[1], t[3])
            out.append(lerp(B1, B2, t[1], t[2]))
    return out


def _frames(pts, normal, up=None):
    """Per point (tangent, side, nrm): side lies in the plane (left of travel), nrm is the plane normal."""
    T = []
    for i in range(len(pts)):
        a, b = pts[max(0, i - 1)], pts[min(len(pts) - 1, i + 1)]
        T.append(_unit(_sub(b, a)))
    if normal is not None:
        return [(t, _unit(_cross(normal, t)), tuple(normal)) for t in T]
    u = up or min(((1, 0, 0), (0, 1, 0), (0, 0, 1)), key=lambda e: abs(_dot(e, T[0])))
    side = _unit(_cross(u, T[0]))
    out = [(T[0], side, _cross(T[0], side))]
    for i in range(1, len(pts)):                      # rotation-minimising frames (double reflection)
        v1 = _sub(pts[i], pts[i - 1])
        c1 = _dot(v1, v1)
        sL = _sub(side, _scl(v1, 2 * _dot(v1, side) / c1))
        tL = _sub(T[i - 1], _scl(v1, 2 * _dot(v1, T[i - 1]) / c1))
        v2 = _sub(T[i], tL)
        c2 = _dot(v2, v2)
        side = _unit(sL if c2 < 1e-18 else _sub(sL, _scl(v2, 2 * _dot(v2, sL) / c2)))
        out.append((T[i], side, _cross(T[i], side)))
    return out


def _arclen(pts):
    s = [0.0]
    for a, b in zip(pts, pts[1:]):
        s.append(s[-1] + math.dist(a, b))
    return s


def _check_clear(pts, half, what):
    """Refuse a path that would fold a member of half thickness half[i] (in the bending plane) into itself:
    radius of curvature under the half thickness, or two stretches apart along the bar coming closer than
    their thicknesses (the turns of a volute touching)."""
    s = _arclen(pts)
    for i in range(1, len(pts) - 1):
        a, b, c = pts[i - 1], pts[i], pts[i + 1]
        cr = _cross(_sub(b, a), _sub(c, a))
        area2 = math.sqrt(_dot(cr, cr))
        if area2 > 1e-15:
            R = math.dist(a, b) * math.dist(b, c) * math.dist(a, c) / (2 * area2)
            if R < half[i] * 1.05:
                raise ValueError(f"{what}: bends tighter (radius {R:.4f} m) than its half thickness "
                                 f"{half[i]:.4f} m at {s[i]:.3f} m along; thin the bar or open the curve")
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            gap = half[i] + half[j]
            if s[j] - s[i] > 1.7 * gap and math.dist(pts[i], pts[j]) < gap * 1.04:
                raise ValueError(f"{what}: the bar touches itself between {s[i]:.3f} m and {s[j]:.3f} m along "
                                 f"(clearance {math.dist(pts[i], pts[j]) - gap:.4f} m); thin the bar or open the curve")


# ----------------------------------------------------------------------------------------------- shells
def _shell(rings, start_cap, end_cap):
    """Vertices and faces of a closed shell through rings of equal size. A cap is a point (apex fan) or None
    (a flat fan from the ring centre). Faces are flipped as a whole so the shell faces outward."""
    verts, faces, idx = [], [], []
    for ring in rings:
        idx.append(list(range(len(verts), len(verts) + len(ring))))
        verts.extend(ring)
    n = len(rings[0])
    for A, B in zip(idx, idx[1:]):
        for j in range(n):
            k = (j + 1) % n
            faces.append([A[j], A[k], B[k], B[j]])
    for cap, ring, first in ((start_cap, idx[0], True), (end_cap, idx[-1], False)):
        if cap is None:                                # a flat cap: a fan from the ring's centre (always planar)
            cap = _scl(tuple(sum(verts[i][c] for i in ring) for c in range(3)), 1.0 / len(ring))
        verts.append(cap)
        apex = len(verts) - 1
        for j in range(n):
            k = (j + 1) % n
            faces.append([ring[k], ring[j], apex] if first else [ring[j], ring[k], apex])
    vol = 0.0
    for f in faces:
        for i in range(1, len(f) - 1):
            vol += _dot(verts[f[0]], _cross(verts[f[i]], verts[f[i + 1]]))
    if vol < 0:
        faces = [list(reversed(f)) for f in faces]
    return [[round(c, 6) for c in v] for v in verts], faces


def _section_ring(p, fr, pts2):
    _, side, nrm = fr
    return [_addv(p, _addv(_scl(side, u), _scl(nrm, v))) for u, v in pts2]


def _scale_axis(c):
    """One scale factor. Built by Opus 5.5 (medium), 2026-11-16. A number is used as given (0 refused); an X
    expression whose value comes within 1e-4 of 0 reads as +1, so a mirror control that sweeps -1, 0, 1 (a
    `hand`) keeps the part at full size, unmirrored, at 0 instead of collapsing it."""
    if _num(_v(c)):
        assert abs(_v(c)) >= 1e-4, "scale factors must not be 0 (use -1 to mirror)"
        return _v(c)
    return iff(lt(absx(c), 1e-4), 1.0, c)


def _place(g, id_, part, at, rotate, scale):
    """Transform node for a member. Built by Opus 5.5 (medium), 2026-11-16. scale: a number or X (uniform) or
    [sx, sy, sz] of numbers / X; a negative factor mirrors (the engine reverses the winding of mirrored parts,
    so the shell stays outward); X factors near 0 read as +1 (_scale_axis). rotate: [rx, ry, rz] radians of
    numbers / X, or one number / X (about z)."""
    if scale is None:
        sc = None
    elif isinstance(scale, (list, tuple)):
        assert len(scale) == 3, "scale is a number or [sx, sy, sz]"
        sc = [_scale_axis(c) for c in scale]
    else:
        sc = [_scale_axis(scale)] * 3
    if rotate is not None and not isinstance(rotate, (list, tuple)):
        rotate = [0.0, 0.0, rotate]
    return g.xf(id_, part, t=list(at) if at is not None else None, r=list(rotate) if rotate is not None else None, s=sc)


def _finish(g, id_, verts, faces, smooth, slot, at, rotate, scale):
    part = g.n(id_ + "-shell", "surface", vertices=verts, faces=faces)
    if smooth:
        part = g.n(id_ + "-smooth", "smoothNormals", [part])
    if slot:
        part = g.mat(id_ + "-slot", part, slot)
    return _place(g, id_, part, at, rotate, scale)


# ----------------------------------------------------------------------------------------------- members
def swell_tube(self, id_, path, profile=0.02, sides=12, aspect=1.0, ends="round", plane="xz", segments=48, up=None,
               smooth=True, at=None, rotate=None, scale=None, slot=None):
    """A tube whose radius swells and tapers along its path: levers, organic posts, tendrils. Metres.

    path: a Curve or 2D points (lifted into `plane`, see lift()), 3D points, or a callable t -> (x, y, z) for t in
    [0, 1] sampled at `segments` + 1 points; sparse point lists are refined by a Catmull-Rom spline through
    them to about `segments` steps. profile: the radius as a number, a callable of t (arc-length
    fraction, 0 at the first point) or knots [(t, r), ...] joined by a monotone cubic. The section is round, or
    elliptical with `aspect` = extent along the plane normal / extent in the plane (for 3D paths: along the
    frame's normal; frames are twist-free, `up` picks the first). ends "round" (half-ellipsoid caps reaching
    one radius past each end), "flat" (a cut with a small edge round) or a pair (start, end). One closed,
    smooth-shaded `surface` shell; refuses paths that fold it into itself. at / rotate / scale may be X;
    scale=[C("hand"), 1, 1] mirrors it across x for a hand control (-1, 0, 1: 0 reads as +1), see _place.
    Example: g.swell_tube("lever", [(0, 0), (0.12, 0.01), (0.24, 0.035)], [(0, 0.011), (0.3, 0.008), (0.85, 0.014), (1, 0.01)])"""
    pts, normal = _path(path, plane, segments)
    fr = _frames(pts, normal, up)
    s = _arclen(pts)
    f = _profile(profile)
    rs = [f(si / s[-1]) for si in s]
    assert min(rs) > 0, "swell_tube: radius must stay positive"
    _check_clear(pts, rs if normal is not None else [r * max(1.0, aspect) for r in rs], f"swell_tube {id_}")
    sides = max(3, int(sides))
    circle = [(math.cos(TAU * j / sides), math.sin(TAU * j / sides) * aspect) for j in range(sides)]
    rings = [_section_ring(p, f_, [(u * r, v * r) for u, v in circle]) for p, f_, r in zip(pts, fr, rs)]
    e0, e1 = (ends, ends) if isinstance(ends, str) else ends
    caps = []
    for which, e in ((0, e0), (-1, e1)):
        p, f_, r = pts[which], fr[which], rs[which]
        out = _scl(f_[0], -1.0 if which == 0 else 1.0)
        extra = []
        if e == "round":
            K = max(2, sides // 4)
            for k in range(1, K):
                ph = PI / 2 * k / K
                extra.append(_section_ring(_addv(p, _scl(out, r * math.sin(ph))), f_,
                                           [(u * r * math.cos(ph), v * r * math.cos(ph)) for u, v in circle]))
            caps.append((extra, _addv(p, _scl(out, r))))
        else:
            assert e == "flat", "ends are round or flat"
            bev = 0.15 * r
            extra.append(_section_ring(_addv(p, _scl(out, bev)), f_, [(u * (r - bev), v * (r - bev)) for u, v in circle]))
            caps.append((extra, None))
    rings = list(reversed(caps[0][0])) + rings + caps[1][0]
    verts, faces = _shell(rings, caps[0][1], caps[1][1])
    return _finish(self, id_, verts, faces, smooth, slot, at, rotate, scale)


def _curl_terminal(curve, half_end, turns, radius, hand):
    """The 2D curve with a small log-spiral curl added at its end (continuing its tangent)."""
    pts = list(curve)
    (x0, y0), (x1, y1) = pts[-2], pts[-1]
    h = math.atan2(y1 - y0, x1 - x0)
    if hand is None:
        k = getattr(curve, "kappa", None)
        hand = "right" if k and k[-1] < 0 else "left"
    sg = _hand(hand)
    R = radius or max(6.0 * half_end, 0.01)
    eye = max(2.2 * half_end, R * 0.3)
    ramp = R * 0.6
    rest = TAU * turns - ramp / (2 * R)
    c = _trace([("ramp", "k", ramp, 0.0, sg / R), ("curl", "r", _r_length(rest, R, eye), sg * R, sg * eye)],
               (x1, y1), h, 40, max_step=R / 3)
    return pts + list(c[1:])


def _with_terminals(path, start, end, half0, half1, curl_turns, curl_radius):
    if start != "curl" and end != "curl":
        return path
    assert not callable(path) and len(path[0]) == 2, "curl terminals need a 2D path (a Curve or (x, y) points)"
    c = path
    if end == "curl":
        c = Curve(_curl_terminal(c, half1, curl_turns, curl_radius, None))
    if start == "curl":
        rev = Curve(list(reversed(c)))
        k = getattr(path, "kappa", None)
        rev.kappa = [-k[0]] if k else []               # the start curls to the side the path first bends to
        c = Curve(list(reversed(_curl_terminal(rev, half0, curl_turns, curl_radius, None))))
    return c


def _leaf(self, id_, pts3, normal, at_end, length, width, thin, slot):
    """A forged leaf at one end of a member: a pointed blade in the member's plane, its base sunk in the bar."""
    p, q = (pts3[-1], pts3[-2]) if at_end else (pts3[0], pts3[1])
    t = _unit(_sub(p, q))
    if normal is None:
        normal = _unit(_cross(t, (0, 0, 1))) if abs(t[2]) < 0.9 else (1.0, 0.0, 0.0)
    side = _unit(_cross(normal, t))
    base = _sub(p, _scl(t, 0.25 * length))
    bend = 0.18 * length                                   # the blade curves gently to one side
    path = [_addv(_addv(base, _scl(t, length * i / 16)), _scl(side, bend * (i / 16) ** 2)) for i in range(17)]
    half = width / 2
    prof = lambda u: half * max(0.08, math.sin(PI * min(1.0, u ** 0.62)) ** 0.9)
    return swell_tube(self, id_, path, prof, sides=12, aspect=thin / half, ends="round", slot=slot)


def round_bar(self, id_, path, radius, taper=1.0, start=None, end=None, sides=10, plane="xz", segments=48,
              curl_turns=0.8, curl_radius=None, leaf=None, at=None, rotate=None, scale=None, slot=None):
    """A round iron bar along a path, tapering from `radius` to radius * taper. Metres.

    path as for swell_tube (a Curve or 2D points in `plane`, 3D points, or a callable). Terminals at `start`
    and `end`: None (a rounded end), "curl" (a small log-spiral curl continuing the bar's tangent, `curl_turns`
    turns from `curl_radius`, default six bar radii; 2D paths only) or "leaf" (a pointed blade in the plane,
    `leaf` = (length, width), default (14, 6) end radii, sunk into the bar end). Returns one node: the bar
    joined with its leaves, a single piece. at / rotate / scale may be X (scale also [sx, sy, sz], as swell_tube).
    Example: g.round_bar("tendril", volute(1.5, 0.08, 0.012), 0.006, taper=0.5, start="leaf")"""
    r1 = radius * taper
    p2 = _with_terminals(path, start, end, radius, r1, curl_turns, curl_radius)
    parts = [swell_tube(self, id_ + "-bar", p2, [(0, radius), (1, r1)], sides=sides, plane=plane, segments=segments, slot=slot)]
    pts3, normal = _path(p2, plane, segments)
    for which, term, r in ((False, start, radius), (True, end, r1)):
        if term == "leaf":
            L, W = leaf or (14 * r, 6 * r)
            parts.append(_leaf(self, f"{id_}-leaf-{'end' if which else 'start'}", pts3, normal, which, L, W, 0.6 * r, slot))
    body = parts[0] if len(parts) == 1 else self.join(id_ + "-parts", *parts)
    return _place(self, id_, body, at, rotate, scale)


def flat_bar(self, id_, path, width, thickness, start=None, end=None, plane="xz", segments=48, curl_turns=0.8,
             curl_radius=None, leaf=None, at=None, rotate=None, scale=None, slot=None):
    """A flat iron bar (rectangular section) along a path, bent the smith's way. Metres.

    The section stays upright in the curve's plane: `width` runs along the plane normal, `thickness` across the
    curve in the plane, so a scroll coils like a clock spring and reads, face on, as a line of the bar's
    thickness. width and thickness are numbers, callables of t (arc-length fraction) or knots [(t, v), ...]:
    [(0, 0.008), (0.7, 0.008), (1, 0.004)] draws a forged taper into a scroll's eye. The edges carry a small
    round (smooth shading stays crisp on the faces). path: a Curve or 2D points in `plane`, or planar 3D points
    (non-planar paths use twist-free frames). Terminals as round_bar: None (a square cut), "curl" or "leaf"
    (`leaf` default (7, 2.6) bar widths). One node, a single piece. at / rotate / scale may be X (scale also
    [sx, sy, sz], as swell_tube).
    Example: g.flat_bar("scroll", c_scroll(0.3, eyes=0.016), 0.02, [(0, 0.006), (0.5, 0.009), (1, 0.006)])"""
    fw, ft = _profile(width), _profile(thickness)
    p2 = _with_terminals(path, start, end, ft(0) / 2, ft(1) / 2, curl_turns, curl_radius)
    pts, normal = _path(p2, plane, segments)
    fr = _frames(pts, normal)
    s = _arclen(pts)
    ts, ws = [ft(si / s[-1]) for si in s], [fw(si / s[-1]) for si in s]
    assert min(ts) > 0 and min(ws) > 0, "flat_bar: width and thickness must stay positive"
    _check_clear(pts, [t / 2 for t in ts], f"flat_bar {id_}")

    def section(t, w, shrink=0.0):
        a, c = t / 2 - shrink, w / 2 - shrink
        e = 0.12 * min(t, w)
        return [(a, c - e), (a - e, c), (-a + e, c), (-a, c - e), (-a, -c + e), (-a + e, -c), (a - e, -c), (a, -c + e)]
    rings = [_section_ring(p, f_, section(t, w)) for p, f_, t, w in zip(pts, fr, ts, ws)]
    e0, e1 = 0.12 * min(ts[0], ws[0]), 0.12 * min(ts[-1], ws[-1])
    first = _section_ring(_addv(pts[0], _scl(fr[0][0], -e0)), fr[0], section(ts[0], ws[0], e0))
    last = _section_ring(_addv(pts[-1], _scl(fr[-1][0], e1)), fr[-1], section(ts[-1], ws[-1], e1))
    verts, faces = _shell([first] + rings + [last], None, None)
    parts = [_finish(self, id_ + "-bar", verts, faces, True, slot, None, None, None)]
    for which, term, t, w in ((False, start, ts[0], ws[0]), (True, end, ts[-1], ws[-1])):
        if term == "leaf":
            L, W = leaf or (7 * w, 2.6 * w)
            parts.append(_leaf(self, f"{id_}-leaf-{'end' if which else 'start'}", pts, normal, which, L, W,
                               min(0.35 * w, 0.6 * t + 0.002), slot))
    body = parts[0] if len(parts) == 1 else self.join(id_ + "-parts", *parts)
    return _place(self, id_, body, at, rotate, scale)


# ----------------------------------------------------------------------------------------------- composite
def _moved(curve, f):
    """A Curve with its points mapped by f, keeping its metadata (f a similarity of scale k: pass k)."""
    out = Curve([f(p) for p in curve], kappa=list(curve.kappa), s=list(curve.s), heading=list(curve.heading),
                parts=dict(curve.parts))
    return out


def scroll_bracket(self, id_, reach=0.8, drop=None, support="whiplash", at=(0.0, 0.0, 0.0), facing="+x",
                   arm=(0.016, 0.034), bar=(0.022, 0.009), plate=(0.11, 0.012), ring=0.028, filler=True,
                   scale=None, slot=None):
    """A wrought-iron wall bracket for a hanging lamp or sign. Metres, Z-up. Returns (node id, hanging point).

    Frame: `at` is the point on the wall face level with the arm's centre line; `facing` is the wall's outward
    normal ("+x", "-x", "+y", "-y" or radians from +x); the bracket stands in the vertical plane through it.
    Parts, each overlapping the next so the bracket is one piece:
    - the arm: a flat bar on edge (`arm` = (width across, depth), tapering to 40 %) running out from the wall and
      ending in an upward curl near `reach`; the lamp ring hangs under it 0.86 * reach out;
    - the support, sprung from low on the plate (`drop` below the arm, default 0.55 reach) up to the arm's
      underside and collared to it: "whiplash" (its recoil curls under the arm), "s" (an S-scroll) or "c" (a
      C-scroll); a flat bar `bar` = (width across, thickness);
    - `filler`: a small volute in the corner between plate and arm;
    - the fixing plate (`plate` = (width, thickness)) with four square bolt heads (surface detail);
    - a hanging ring of radius `ring` (wire radius ring / 5).
    The hanging point is the bottom of the ring in document coordinates (X when `at` or `scale` is). `scale`
    (number or X) scales the whole bracket about `at`.
    Example: node, hook = g.scroll_bracket("bracket", 0.9, at=[0, 0, 3.2], facing="-y")"""
    assert not isinstance(scale, (list, tuple)), "scroll_bracket: scale is uniform (a number or X)"
    R = float(reach)
    D = float(drop) if drop is not None else 0.55 * R
    aw, ad = arm
    bw, bt = bar
    pw, pt = plate
    parts = []
    # Arm: on edge, from inside the plate out to the curl.
    tip = 0.80 * R
    arm_path = Curve([(pt * 0.3 + (tip - pt * 0.3) * i / 8, 0.0) for i in range(9)], kappa=[0.0] * 9)
    parts.append(flat_bar(self, id_ + "-arm", arm_path, aw, [(0, ad), (0.5, ad * 0.8), (1, ad * 0.4)], end="curl",
                          curl_turns=1.0, curl_radius=0.075 * R, slot=slot))
    under = -ad * 0.8 / 2                                  # underside of the arm where the support meets it
    # Support curve: turned so its top runs level, scaled so its foot is `drop` down, its foot set on the plate.
    if support == "whiplash":
        # Sway solved so the lash climbs from the foot to the arm at the slope from plate foot to 0.55 reach.
        slope = math.atan2(D, 0.55 * R - pt)

        def climb(sw):
            w = whiplash(1.0, sway=sw, recoil=0.9, hand="left", snap=False)
            i = w.parts["lash"][1]
            return math.atan2(w[i][1] - w[0][1], w[i][0] - w[0][0]) - w.heading[i], w
        lo_, hi_ = 0.05, 0.3
        for _ in range(16):
            mid = (lo_ + hi_) / 2
            lo_, hi_ = (mid, hi_) if climb(mid)[0] < slope else (lo_, mid)
        c = climb((lo_ + hi_) / 2)[1]
        i_top = c.parts["lash"][1]
        ang = -c.heading[i_top]                            # the top runs level (heading 0) along the arm
        lo = i_top + 1
    elif support in ("s", "c"):
        c = (s_scroll(1.0, eyes=0.06, turns=(1.0, 1.25), hand="left") if support == "s"
             else c_scroll(1.0, eyes=0.05, turns=(1.0, 1.25), hand="right"))
        # Stand the eye line up at 55 degrees (first eye low by the wall); the top of the curve meets the arm.
        (ex0, ey0), (ex1, ey1) = c.eyes
        ang = math.radians(55) - math.atan2(ey1 - ey0, ex1 - ex0)
        i_top = max(range(len(c)), key=lambda i: math.sin(ang) * c[i][0] + math.cos(ang) * c[i][1])
        lo = len(c)
    else:
        raise AssertionError("support is whiplash, s or c")
    ca, sa = math.cos(ang), math.sin(ang)
    px, py = c[i_top]
    c = _moved(c, lambda p: (ca * (p[0] - px) - sa * (p[1] - py), sa * (p[0] - px) + ca * (p[1] - py)))
    c.heading = [h + ang for h in c.heading]
    y_foot = min(y for x, y in c[:lo])
    k = (D - bt) / max(1e-6, -y_foot)
    x_min = min(x for x, y in c[:lo]) * k
    dx, dy = pt + bt * (0.6 if support == "whiplash" else 0.2) - x_min, under - bt * 0.3
    c = _moved(c, lambda p: (p[0] * k + dx, p[1] * k + dy))
    c.kappa, c.s = [q / k for q in c.kappa], [q * k for q in c.s]
    # The foot: carry the bar back into the plate along its first tangent.
    (x0, y0), (x1, y1) = c[0], c[1]
    tx, ty = _unit((x1 - x0, y1 - y0, 0.0))[:2]
    back = []
    if x0 > pt * 0.3 and tx > 0.15:
        L_back = (x0 - pt * 0.3) / tx
        nb = max(1, math.ceil(L_back / 0.02))
        back = [(x0 - tx * L_back * (nb - j) / nb, y0 - ty * L_back * (nb - j) / nb) for j in range(nb)]
    sup = Curve(back + list(c), kappa=[0.0] * len(back) + c.kappa)
    parts.append(flat_bar(self, id_ + "-support", sup, bw, [(0, bt), (0.6, bt), (1, bt * 0.7)], slot=slot))
    top_x = c[i_top][0]
    self.box(id_ + "-collar", [0.026, max(aw, bw) + 0.008, ad * 0.8 + bt + 0.012], [top_x, 0.0, (under + dy) / 2 + 0.002])
    parts.append(id_ + "-collar")
    y_low = min(y for x, y in sup)
    if filler:
        fr = min(0.16 * R, 0.4 * D)
        fv = volute(1.6, fr, max(0.012, fr * 0.16), hand="right", start=(pt * 0.3, under - bt * 0.25), heading=0.0,
                    lead=fr * 0.25)
        parts.append(flat_bar(self, id_ + "-filler", fv, bw * 0.8, [(0, bt * 0.85), (1, bt * 0.6)], slot=slot))
    # Fixing plate with four square bolt heads.
    z_top, z_bot = ad * 0.5 + 0.05, y_low - 0.05
    self.box(id_ + "-plate-body", [pt, pw, z_top - z_bot], [pt / 2, 0.0, (z_top + z_bot) / 2])
    self.points(id_ + "-bolts-at", [[pt + 0.01, sy * pw * 0.3, z] for sy in (-1, 1) for z in (z_top - 0.03, z_bot + 0.03)])
    parts.append(self.fasteners(id_ + "-plate", id_ + "-plate-body", id_ + "-bolts-at", head="square",
                                radius=0.011, height=0.006))
    # Hanging ring under the arm.
    hx, rr = 0.86 * R, float(ring)
    wire = rr / 5
    cz = -ad * 0.6 / 2 - rr + wire * 0.8
    self.n(id_ + "-ring-path", "curve", point=[hx + rr * kitlib.cos(kitlib.T * TAU), 0, cz + rr * kitlib.sin(kitlib.T * TAU)],
           segments=20, closed=1)
    parts.append(self.n(id_ + "-ring", "tube", [id_ + "-ring-path"], radius=wire, sides=8))
    body = self.join(id_ + "-parts", *parts)
    if slot:
        body = self.mat(id_ + "-slot", body, slot)
    a = _facing(facing)
    node = _place(self, id_, body, at, [0, 0, a], scale)
    k = X(scale if scale is not None else 1.0)
    lx, lz = hx, cz - rr - wire
    hang = [X(at[0]) + k * (lx * math.cos(a)), X(at[1]) + k * (lx * math.sin(a)), X(at[2]) + k * lz]
    return node, [h.v if _num(h.v) else h for h in hang]


# ----------------------------------------------------------------------------------------------- attach
for _f in (swell_tube, round_bar, flat_bar, scroll_bracket):
    setattr(Graph, _f.__name__, _f)
for _f in (volute, c_scroll, s_scroll, whiplash, lift):
    setattr(Graph, _f.__name__, staticmethod(_f))
