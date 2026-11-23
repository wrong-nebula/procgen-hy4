"""Shared 2D curve construction for kit builders: offsets, fillets, tangents, booleans, plates.

Built by Opus 5.5 (medium), 2026-11-16. Pure Python, no dependencies; written from first principles
(standard computational geometry). Units are the caller's (metres in the kits). Tests:
recipes/test_kitlib_curves.py.

Representation
  point     (x, y) tuple.
  polyline  list of points; a closed ring does not repeat its first point; counter-clockwise = positive area.
  path      list of exact pieces, Seg(a, b) and Arc(centre, radius, start_angle, sweep) (sweep signed,
            counter-clockwise > 0). Paths keep arcs exact; sample(path, tol) gives points with a
            sagitta of at most tol when they meet nodes or booleans.
  shapes    list of (outer, [holes]): outer counter-clockwise, holes clockwise. Every boolean and region
            offset returns shapes; they accept a ring, a path, an (outer, holes) tuple or a list of those.

Functions
  sampling     sample, length, point_at, resample (equal arc length), closest_point, reverse
  rings        area, shapes_area, orient, winding, contains, simplify, bbox, circle, rect, transform
  offsets      offset (closed ring -> shapes, inward offsets cleaned of self-intersections; open
               polyline -> parallel polyline), stroke (band of a width round a curve: a flat bar seen
               square to its face), offset_shapes, offset_path (exact for line/arc paths);
               joins mitre | round | bevel with a mitre limit
  corners      fillet (exact arcs, or tension blends, at chosen or all corners), chamfer
  tangents     circle_3p, fillet_arc, circles_tangent_to_lines, circle_tangent_lines (outer and
               inner), point_tangents, incircle, perpendicular, bisector, perp_bisector
  crossings    intersect (exact Seg/Arc pairs), intersect_lines, intersect_line_circle,
               intersect_circles, intersections (polylines, with arc lengths), split
  booleans     union (any number), intersection, difference, clean (positive-winding self-union)
  solids       triangulate (polygon with holes), solid (vertices, faces), Graph.plate (shapes ->
               closed `surface` shells: triangle caps and quad walls, so no face nears the
               128-corner limit; plates past the vertex budget are cut into strips)

Approximate: arcs become polylines (sagitta <= tol, default 1.5 mm) wherever they meet booleans,
offsets of polylines or geometry. Booleans are exact for the sampled polygons up to a vertex snap of
1e-9 of the drawing's extent; coincident and touching edges merge instead of leaving slivers.
"""
import math

TOL = 0.0015            # default sampling tolerance (sagitta), metres
_SNAP = 1e-9            # relative vertex snap for booleans
PI = math.pi
PI2 = 2.0 * math.pi


# ---------------------------------------------------------------- vectors
def _add(a, b): return (a[0] + b[0], a[1] + b[1])
def _sub(a, b): return (a[0] - b[0], a[1] - b[1])
def _mul(a, k): return (a[0] * k, a[1] * k)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1]
def _crs(a, b): return a[0] * b[1] - a[1] * b[0]
def _len(a): return math.hypot(a[0], a[1])
def _perp(a): return (-a[1], a[0])                     # +90 degrees (to the left)
def _right(a): return (a[1], -a[0])                    # -90 degrees (to the right)
def _dist(a, b): return math.hypot(a[0] - b[0], a[1] - b[1])
def _turn(a, b, c): return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
def _mid(a, b): return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
def _polar(c, r, t): return (c[0] + r * math.cos(t), c[1] + r * math.sin(t))
def _P(p): return (float(p[0]), float(p[1]))
def _ang(p, c): return math.atan2(p[1] - c[1], p[0] - c[0])


def _unit(a):
    n = _len(a)
    assert n > 0, "zero-length direction"
    return (a[0] / n, a[1] / n)


# ---------------------------------------------------------------- exact pieces
class Seg:
    """Straight segment a -> b."""
    __slots__ = ("a", "b")

    def __init__(self, a, b):
        self.a, self.b = _P(a), _P(b)

    start = property(lambda s: s.a)
    end = property(lambda s: s.b)
    length = property(lambda s: _dist(s.a, s.b))

    def point(self, t): return _add(self.a, _mul(_sub(self.b, self.a), t))
    def tangent(self, t=0.0): return _unit(_sub(self.b, self.a))
    def reversed(self): return Seg(self.b, self.a)
    def sample(self, tol=TOL): return [self.a]

    def offset(self, d):
        """Parallel segment d to the left."""
        n = _mul(_perp(self.tangent()), d)
        return Seg(_add(self.a, n), _add(self.b, n))

    def __repr__(self): return f"Seg({self.a}, {self.b})"


class Arc:
    """Circular arc: centre c, radius r, from angle a0 through a signed sweep (counter-clockwise > 0)."""
    __slots__ = ("c", "r", "a0", "sweep")

    def __init__(self, c, r, a0, sweep):
        assert r > 0, "arc radius must be positive"
        self.c, self.r, self.a0, self.sweep = _P(c), float(r), float(a0), float(sweep)

    start = property(lambda s: _polar(s.c, s.r, s.a0))
    end = property(lambda s: _polar(s.c, s.r, s.a0 + s.sweep))
    length = property(lambda s: s.r * abs(s.sweep))

    def point(self, t): return _polar(self.c, self.r, self.a0 + self.sweep * t)

    def tangent(self, t=0.0):
        a = self.a0 + self.sweep * t
        s = 1.0 if self.sweep >= 0 else -1.0
        return (-math.sin(a) * s, math.cos(a) * s)

    def reversed(self): return Arc(self.c, self.r, self.a0 + self.sweep, -self.sweep)

    def steps(self, tol=TOL):
        da = 2.0 * math.acos(1.0 - tol / self.r) if tol < self.r else PI2 / 3
        return max(1, int(math.ceil(abs(self.sweep) / da - 1e-9)))

    def sample(self, tol=TOL):
        """Points from the start (the end excluded) with sagitta <= tol."""
        n = self.steps(tol)
        return [self.point(k / n) for k in range(n)]

    def contains_angle(self, a, eps=1e-12):
        lo, sw = (self.a0, self.sweep) if self.sweep >= 0 else (self.a0 + self.sweep, -self.sweep)
        d = (a - lo) % PI2
        return d <= sw + eps or d >= PI2 - eps

    def param(self, a):
        """Arc parameter t of direction a."""
        if not self.sweep:
            return 0.0
        return ((a - self.a0) % PI2) / self.sweep if self.sweep > 0 else ((self.a0 - a) % PI2) / -self.sweep

    def offset(self, d):
        """Concentric arc d to the left (toward the centre for a counter-clockwise arc)."""
        r = self.r - d if self.sweep >= 0 else self.r + d
        assert r > 0, "arc offset collapses the radius"
        return Arc(self.c, r, self.a0, self.sweep)

    @staticmethod
    def through(p0, p1, p2):
        """The arc from p0 through p1 to p2."""
        c, r = circle_3p(p0, p1, p2)
        a0, a2 = _ang(p0, c), _ang(p2, c)
        sweep = (a2 - a0) % PI2 if _turn(p0, p1, p2) > 0 else -((a0 - a2) % PI2)
        return Arc(c, r, a0, sweep)

    def __repr__(self): return f"Arc(c={self.c}, r={self.r:.6g}, a0={self.a0:.6g}, sweep={self.sweep:.6g})"


def _is_path(x):
    return isinstance(x, (list, tuple)) and len(x) > 0 and isinstance(x[0], (Seg, Arc))


def _is_pts(x):
    return isinstance(x, (list, tuple)) and len(x) > 0 and isinstance(x[0], (list, tuple)) and len(x[0]) == 2 \
        and isinstance(x[0][0], (int, float))


def _dedupe(pts, closed, eps=1e-12):
    out = []
    for p in pts:
        if not out or _dist(p, out[-1]) > eps:
            out.append(p)
    if closed and len(out) > 1 and _dist(out[0], out[-1]) <= eps:
        out.pop()
    return out


def sample(curve, tol=TOL, closed=None):
    """Points of a path (arcs to sagitta tol) or a polyline. A closed path (end meets start) gives its
    ring without the repeated point; an open one ends with its end point."""
    if not _is_path(curve):
        return [_P(p) for p in curve]
    pts = []
    for s in curve:
        pts.extend(s.sample(tol))
    if closed is None:
        closed = _dist(curve[-1].end, curve[0].start) < 1e-9
    if not closed:
        pts.append(curve[-1].end)
    return _dedupe(pts, closed)


def length(curve, closed=False):
    """Arc length of a path (exact) or polyline."""
    if _is_path(curve):
        return sum(s.length for s in curve)
    pts = list(curve) + ([curve[0]] if closed else [])
    return sum(_dist(a, b) for a, b in zip(pts, pts[1:]))


def reverse(curve):
    return [s.reversed() for s in reversed(curve)] if _is_path(curve) else list(reversed(curve))


def _cum(pts, closed):
    q = list(pts) + ([pts[0]] if closed else [])
    s = [0.0]
    for a, b in zip(q, q[1:]):
        s.append(s[-1] + _dist(a, b))
    return q, s


def point_at(curve, s, closed=False):
    """(point, unit tangent) at arc length s along a path or polyline."""
    if _is_path(curve):
        for seg in curve:
            L = seg.length
            if s <= L or seg is curve[-1]:
                t = min(max(s / L, 0.0), 1.0) if L > 0 else 0.0
                return seg.point(t), seg.tangent(t)
            s -= L
    q, cs = _cum(curve, closed)
    s = min(max(s, 0.0), cs[-1])
    lo, hi = 0, len(cs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if cs[mid] <= s:
            lo = mid
        else:
            hi = mid
    L = cs[hi] - cs[lo]
    t = (s - cs[lo]) / L if L > 0 else 0.0
    a, b = q[lo], q[hi]
    return _add(a, _mul(_sub(b, a), t)), _unit(_sub(b, a))


def resample(curve, count=None, step=None, closed=False):
    """Points at equal arc length along a path (arcs measured exactly) or polyline: `count` points (open:
    both ends included; closed: the ring) or as many as fit `step`."""
    L = length(curve, closed)
    if count is None:
        assert step and step > 0, "resample needs count or step"
        count = max(2, int(round(L / step)) + (0 if closed else 1))
    n = count if closed else count - 1
    assert n >= 1, "resample needs at least two points"
    return [point_at(curve, L * k / n, closed)[0] for k in range(count)]


def closest_point(curve, p, closed=False):
    """(point, distance, s): the nearest point of a path (exact on arcs) or polyline to p and its arc length."""
    p = _P(p)
    if _is_path(curve):
        segs = curve
    else:
        q = list(curve) + ([curve[0]] if closed else [])
        segs = [Seg(a, b) for a, b in zip(q, q[1:])]
    best, s0 = (None, float("inf"), 0.0), 0.0
    for seg in segs:
        if isinstance(seg, Seg):
            d = _sub(seg.b, seg.a)
            L2 = _dot(d, d)
            t = min(max(_dot(_sub(p, seg.a), d) / L2, 0.0), 1.0) if L2 > 0 else 0.0
        elif seg.contains_angle(_ang(p, seg.c)):
            t = seg.param(_ang(p, seg.c))
        else:
            t = 0.0 if _dist(p, seg.start) <= _dist(p, seg.end) else 1.0
        q = seg.point(t)
        if _dist(p, q) < best[1]:
            best = (q, _dist(p, q), s0 + t * seg.length)
        s0 += seg.length
    return best


# ---------------------------------------------------------------- rings
def area(ring):
    """Signed area (counter-clockwise > 0)."""
    n = len(ring)
    return 0.5 * sum(ring[i][0] * ring[(i + 1) % n][1] - ring[(i + 1) % n][0] * ring[i][1] for i in range(n))


def shapes_area(shapes):
    return sum(area(o) + sum(area(h) for h in hs) for o, hs in shapes)


def orient(ring, ccw=True):
    ring = [_P(p) for p in ring]
    return ring if (area(ring) > 0) == ccw else ring[::-1]


def bbox(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def winding(rings, p):
    """Winding number of p in oriented rings."""
    w, y = 0, p[1]
    for ring in rings:
        n = len(ring)
        for i in range(n):
            a, b = ring[i], ring[(i + 1) % n]
            if a[1] <= y < b[1] and _turn(a, b, p) > 0:
                w += 1
            elif b[1] <= y < a[1] and _turn(a, b, p) < 0:
                w -= 1
    return w


def contains(region, p):
    """Whether p is inside a ring, shape or shapes (nonzero winding)."""
    return winding(_rings(region), _P(p)) != 0


def simplify(ring, closed=True, eps=1e-10):
    """Drop repeated and collinear vertices (and zero-width spikes)."""
    pts = _dedupe([_P(p) for p in ring], closed)
    changed = True
    while changed and len(pts) > (3 if closed else 2):
        changed, out, n = False, [], len(pts)
        for i in range(n):
            if not closed and i in (0, n - 1):
                out.append(pts[i])
                continue
            a, b, c = (out[-1] if out else pts[i - 1]), pts[i], pts[(i + 1) % n]
            scale = max(_dist(a, b), _dist(b, c), 1e-30)
            if abs(_turn(a, b, c)) <= eps * scale * scale:
                changed = True
                continue
            out.append(b)
        pts = _dedupe(out, closed)
    return pts


def circle(c, r, tol=TOL, start=0.0):
    """Counter-clockwise ring of a circle (vertices on it, sagitta <= tol)."""
    return Arc(c, r, start, PI2).sample(tol)


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def transform(shapes, rotate=0.0, translate=(0.0, 0.0), scale=1.0):
    """Shapes (or one ring) turned by rotate radians about the origin, scaled, then moved."""
    c, s = math.cos(rotate), math.sin(rotate)
    f = lambda p: (scale * (p[0] * c - p[1] * s) + translate[0], scale * (p[0] * s + p[1] * c) + translate[1])
    if _is_pts(shapes):
        return [f(p) for p in shapes]
    return [([f(p) for p in o], [[f(p) for p in h] for h in hs]) for o, hs in shapes]


# ---------------------------------------------------------------- tangent constructions
def circle_3p(a, b, c):
    """(centre, radius) of the circle through three points."""
    a, b, c = _P(a), _P(b), _P(c)
    d = 2.0 * _turn(a, b, c)
    assert abs(d) > 1e-18, "circle_3p: points are collinear"
    a2, b2, c2 = _dot(a, a), _dot(b, b), _dot(c, c)
    ux = (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d
    uy = (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d
    return (ux, uy), _dist((ux, uy), a)


def fillet_arc(a, v, b, r):
    """Arc of radius r tangent to the lines v-a and v-b (the fillet of corner v on a path a -> v -> b).
    Returns (arc, t1, t2); the arc runs from t1 on v-a to t2 on v-b."""
    a, v, b = _P(a), _P(v), _P(b)
    u1, u2 = _unit(_sub(a, v)), _unit(_sub(b, v))
    phi = math.acos(max(-1.0, min(1.0, _dot(u1, u2)))) / 2.0      # half the corner angle
    assert 1e-9 < phi < PI / 2 - 1e-12, "fillet_arc: straight or folded-back corner"
    t = r / math.tan(phi)
    t1, t2 = _add(v, _mul(u1, t)), _add(v, _mul(u2, t))
    c = _add(v, _mul(_unit(_add(u1, u2)), r / math.sin(phi)))
    sweep = (PI - 2.0 * phi) * (1 if _turn(a, v, b) > 0 else -1)
    return Arc(c, r, _ang(t1, c), sweep), t1, t2


def circles_tangent_to_lines(p1, d1, p2, d2, r):
    """Centres of the circles of radius r tangent to the lines (point p1, direction d1) and (p2, d2):
    four when the lines cross, one (the mid line) when they are parallel and 2r apart, else none."""
    p1, p2 = _P(p1), _P(p2)
    n1, n2 = _perp(_unit(d1)), _perp(_unit(d2))
    det = _crs(n1, n2)
    if abs(det) > 1e-14:
        out = []
        for s1 in (1, -1):
            for s2 in (1, -1):
                c1, c2 = _dot(n1, p1) + s1 * r, _dot(n2, p2) + s2 * r
                out.append(((c1 * n2[1] - c2 * n1[1]) / det, (n1[0] * c2 - n2[0] * c1) / det))
        return out
    gap = _dot(n1, _sub(p2, p1))
    return [_add(p1, _mul(n1, gap / 2))] if abs(abs(gap) - 2 * r) < 1e-12 * max(1.0, r) else []


def circle_tangent_lines(c1, r1, c2, r2):
    """Common tangents of two circles: {"outer": [(p1, p2), ...], "inner": [...]}, p1 on the first circle
    and p2 on the second; outer tangents keep both circles on one side, inner ones cross between."""
    c1, c2 = _P(c1), _P(c2)
    d = _sub(c2, c1)
    D = _len(d)
    res = {"outer": [], "inner": []}
    if D < 1e-15:
        return res
    base = math.atan2(d[1], d[0])
    for kind, k in (("outer", (r1 - r2) / D), ("inner", (r1 + r2) / D)):
        if abs(k) > 1.0:
            continue
        th = math.acos(k)
        for s in (1, -1):
            n = (math.cos(base + s * th), math.sin(base + s * th))
            res[kind].append((_add(c1, _mul(n, r1)), _add(c2, _mul(n, r2 if kind == "outer" else -r2))))
    return res


def point_tangents(p, c, r):
    """Tangent points on the circle (c, r) of the two lines from p (none when p is inside)."""
    p, c = _P(p), _P(c)
    d = _dist(p, c)
    if d < r:
        return []
    a, base = math.acos(r / d), _ang(p, c)
    return [_polar(c, r, base + a), _polar(c, r, base - a)]


def incircle(a, b, c):
    """(centre, radius) of the circle inscribed in a triangle."""
    a, b, c = _P(a), _P(b), _P(c)
    la, lb, lc = _dist(b, c), _dist(c, a), _dist(a, b)
    s = la + lb + lc
    return ((la * a[0] + lb * b[0] + lc * c[0]) / s, (la * a[1] + lb * b[1] + lc * c[1]) / s), abs(_turn(a, b, c)) / s


def perpendicular(p, a, b):
    """Foot of the perpendicular from p onto the line a-b."""
    p, a, b = _P(p), _P(a), _P(b)
    d = _sub(b, a)
    return _add(a, _mul(d, _dot(_sub(p, a), d) / _dot(d, d)))


def bisector(v, a, b):
    """Unit direction from v bisecting the angle a-v-b."""
    return _unit(_add(_unit(_sub(_P(a), _P(v))), _unit(_sub(_P(b), _P(v)))))


def perp_bisector(a, b):
    """(midpoint, unit direction) of the perpendicular bisector of a-b."""
    a, b = _P(a), _P(b)
    return _mid(a, b), _perp(_unit(_sub(b, a)))


# ---------------------------------------------------------------- intersections
def intersect_lines(a, b, c, d, segments=True, eps=1e-12):
    """Crossing of a-b with c-d (segments, or infinite lines): [] or [point]; collinear overlapping
    segments give the overlap's end points."""
    a, b, c, d = _P(a), _P(b), _P(c), _P(d)
    r, s, qp = _sub(b, a), _sub(d, c), _sub(c, a)
    den = _crs(r, s)
    if abs(den) <= eps * _len(r) * _len(s):
        if not segments or abs(_crs(qp, r)) > eps * _len(r) * max(_len(qp), _len(r)):
            return []
        rr = _dot(r, r)
        t0, t1 = sorted((_dot(qp, r) / rr, _dot(_sub(d, a), r) / rr))
        lo, hi = max(0.0, t0), min(1.0, t1)
        if lo > hi + eps:
            return []
        return [_add(a, _mul(r, lo))] + ([_add(a, _mul(r, hi))] if hi - lo > eps else [])
    t, u = _crs(qp, s) / den, _crs(qp, r) / den
    if segments and not (-eps <= t <= 1 + eps and -eps <= u <= 1 + eps):
        return []
    return [_add(a, _mul(r, t))]


def intersect_line_circle(a, b, c, r, segment=True, eps=1e-12):
    """Points where a-b (segment, or infinite line) meets the circle (c, r)."""
    a, b, c = _P(a), _P(b), _P(c)
    d, f = _sub(b, a), _sub(a, c)
    A, B, Cc = _dot(d, d), 2 * _dot(f, d), _dot(f, f) - r * r
    disc = B * B - 4 * A * Cc
    if disc < -eps * max(B * B, A * r * r):
        return []
    disc = math.sqrt(max(disc, 0.0))
    ts = sorted({(-B - disc) / (2 * A), (-B + disc) / (2 * A)})
    return [_add(a, _mul(d, t)) for t in ts if not segment or -eps <= t <= 1 + eps]


def intersect_circles(c1, r1, c2, r2):
    """Points where two circles meet (one when they touch)."""
    c1, c2 = _P(c1), _P(c2)
    d = _dist(c1, c2)
    if d < 1e-15 or d > r1 + r2 + 1e-12 or d < abs(r1 - r2) - 1e-12:
        return []
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h = math.sqrt(max(r1 * r1 - a * a, 0.0))
    u = _mul(_sub(c2, c1), 1.0 / d)
    m = _add(c1, _mul(u, a))
    if h <= 1e-12 * max(r1, r2):
        return [m]
    n = _perp(u)
    return [_add(m, _mul(n, h)), _sub(m, _mul(n, h))]


def intersect(e1, e2):
    """Exact intersection points of two Seg/Arc pieces."""
    if isinstance(e1, Seg) and isinstance(e2, Seg):
        return intersect_lines(e1.a, e1.b, e2.a, e2.b)
    if isinstance(e1, Arc) and isinstance(e2, Seg):
        e1, e2 = e2, e1
    if isinstance(e1, Seg):
        return [p for p in intersect_line_circle(e1.a, e1.b, e2.c, e2.r) if e2.contains_angle(_ang(p, e2.c), 1e-9)]
    return [p for p in intersect_circles(e1.c, e1.r, e2.c, e2.r)
            if e1.contains_angle(_ang(p, e1.c), 1e-9) and e2.contains_angle(_ang(p, e2.c), 1e-9)]


def _edges(pts, closed):
    n = len(pts)
    return [(pts[i], pts[(i + 1) % n]) for i in range(n if closed else n - 1)]


def intersections(a, b=None, closed_a=False, closed_b=False, tol=TOL):
    """Crossings of two polylines (paths are sampled) as (point, s_a, s_b), arc lengths along each, in
    order along a. With b omitted: the self-crossings of a (neighbouring edges skipped)."""
    A = sample(a, tol)
    B = A if b is None else sample(b, tol)
    ca, cb = closed_a, (closed_a if b is None else closed_b)
    ea, eb = _edges(A, ca), _edges(B, cb)
    sa, sb = _cum(A, ca)[1], _cum(B, cb)[1]
    out = []
    for i, (p, q) in enumerate(ea):
        x0, x1, y0, y1 = min(p[0], q[0]), max(p[0], q[0]), min(p[1], q[1]), max(p[1], q[1])
        for j, (r, s) in enumerate(eb):
            if b is None and (j <= i + 1 or (ca and i == 0 and j == len(ea) - 1)):
                continue
            if max(r[0], s[0]) < x0 or min(r[0], s[0]) > x1 or max(r[1], s[1]) < y0 or min(r[1], s[1]) > y1:
                continue
            for x in intersect_lines(p, q, r, s):
                out.append((x, sa[i] + _dist(p, x), sb[j] + _dist(r, x)))
    out.sort(key=lambda t: t[1])
    dedup = []
    for t in out:
        if not dedup or _dist(t[0], dedup[-1][0]) > 1e-9 or abs(t[1] - dedup[-1][1]) > 1e-9:
            dedup.append(t)
    return dedup


def split(curve, cutters=(), closed=False, at=(), tol=TOL):
    """Open polylines of a polyline (or sampled path) cut where it crosses the cutters and at the given
    arc lengths, in order."""
    pts = sample(curve, tol)
    cuts = list(at)
    for c in cutters:
        cuts += [s for _, s, _ in intersections(pts, c, closed, False, tol)]
    q, cs = _cum(pts, closed)
    L = cs[-1]
    cuts = sorted({s for s in cuts if 1e-9 < s < L - 1e-9})
    bounds = [0.0] + cuts + [L]
    pieces = []
    for s0, s1 in zip(bounds, bounds[1:]):
        inner = [q[i] for i in range(len(q)) if s0 + 1e-12 < cs[i] < s1 - 1e-12]
        pieces.append(_dedupe([point_at(pts, s0, closed)[0]] + inner + [point_at(pts, s1, closed)[0]], False))
    if closed and len(pieces) > 1:                           # the ring's seam is not a cut
        pieces[0] = pieces.pop()[:-1] + pieces[0]
    return pieces


# ---------------------------------------------------------------- fillet and chamfer
def _corners(n, closed, at):
    idx = range(n) if closed else range(1, n - 1)
    if at is None:
        return set(idx)
    if callable(at):
        return {i for i in idx if at(i)}
    return {i % n for i in at if closed or 0 < i % n < n - 1}


def fillet(poly, radius, at=None, closed=True, tension=None, tol=TOL):
    """Round the corners of a polyline: at=None for every corner, a list of vertex indices or a
    predicate on the index. Exact circular arcs (returns a path of Seg/Arc), or with tension in (0, 1]
    a cubic blend between the same tangent points, its handles that fraction of the way to the corner
    (returns a polyline; 0.55 is close to a circle at a right angle). Radii too large for the edges
    are reduced so neighbouring fillets meet but do not overlap."""
    pts = [_P(p) for p in poly]
    n = len(pts)
    plan = {}
    for i in _corners(n, closed, at):
        u1, u2 = _sub(pts[i - 1], pts[i]), _sub(pts[(i + 1) % n], pts[i])
        if _len(u1) == 0 or _len(u2) == 0:
            continue
        phi = math.acos(max(-1.0, min(1.0, _dot(_unit(u1), _unit(u2))))) / 2.0
        if 1e-6 < phi < PI / 2 - 1e-6:
            plan[i] = [phi, radius / math.tan(phi)]
    t0 = {i: v[1] for i, v in plan.items()}
    for i in range(n if closed else n - 1):                 # edge i -> i+1
        j = (i + 1) % n
        need = t0.get(i, 0.0) + t0.get(j, 0.0)
        L = _dist(pts[i], pts[j])
        if need > L:
            for k in (i, j):
                if k in plan:
                    plan[k][1] = min(plan[k][1], t0[k] * L / need)
    items = []
    for i in range(n):
        if i in plan:
            phi, t = plan[i]
            v = pts[i]
            t1 = _add(v, _mul(_unit(_sub(pts[i - 1], v)), t))
            t2 = _add(v, _mul(_unit(_sub(pts[(i + 1) % n], v)), t))
            items.append(("corner", i, t1, t2, t * math.tan(phi)))
        else:
            items.append(("pt", pts[i]))
    if tension is None:
        path, last = [], None
        seq = items + ([items[0]] if closed else [])
        for it in seq:
            if it[0] == "pt":
                if last is not None and _dist(last, it[1]) > 1e-12:
                    path.append(Seg(last, it[1]))
                last = it[1]
            else:
                _, i, t1, t2, r = it
                if last is not None and _dist(last, t1) > 1e-12:
                    path.append(Seg(last, t1))
                if it is seq[-1] and closed and path:
                    break
                arc = fillet_arc(pts[i - 1], pts[i], pts[(i + 1) % n], r)[0]
                path.append(arc)
                last = arc.end
        return path
    out = []
    for it in items:
        if it[0] == "pt":
            out.append(it[1])
            continue
        _, i, t1, t2, r = it
        v = pts[i]
        c1, c2 = _add(t1, _mul(_sub(v, t1), tension)), _add(t2, _mul(_sub(v, t2), tension))
        m = max(4, Arc((0, 0), max(r, 1e-9), 0, PI - 2 * plan[i][0]).steps(tol))
        for j in range(m + 1):
            s = j / m
            w = ((1 - s) ** 3, 3 * s * (1 - s) ** 2, 3 * s * s * (1 - s), s ** 3)
            out.append((w[0] * t1[0] + w[1] * c1[0] + w[2] * c2[0] + w[3] * t2[0],
                        w[0] * t1[1] + w[1] * c1[1] + w[2] * c2[1] + w[3] * t2[1]))
    return _dedupe(out, closed)


def chamfer(poly, dist, at=None, closed=True):
    """Cut the corners of a polyline back by dist along each edge (at most half an edge)."""
    pts = [_P(p) for p in poly]
    n = len(pts)
    chosen = _corners(n, closed, at)
    out = []
    for i, v in enumerate(pts):
        if i not in chosen:
            out.append(v)
            continue
        a, b = pts[i - 1], pts[(i + 1) % n]
        out += [_add(v, _mul(_unit(_sub(a, v)), min(dist, _dist(a, v) / 2))),
                _add(v, _mul(_unit(_sub(b, v)), min(dist, _dist(b, v) / 2)))]
    return _dedupe(out, closed)


# ---------------------------------------------------------------- offsets
def _join(out, v, n1, n2, d, join, limit, tol):
    """Corner at v of an offset d to the right; n1, n2 the right normals of the edges in and out."""
    p1, p2 = _add(v, _mul(n1, d)), _add(v, _mul(n2, d))
    cos = max(-1.0, min(1.0, _dot(n1, n2)))
    if cos > 1 - 1e-12:
        out.append(p1)
        return
    turn = _crs(_perp(n1), _perp(n2))                 # the edges' turn (left > 0)
    if turn * d <= 0:                                  # offset on the inside of the turn
        out += [p1, v, p2]                             # loop through the corner; the cleanup removes it
        return
    if join == "round":
        a1 = math.atan2(n1[1], n1[0]) + (0 if d > 0 else PI)
        sweep = (math.atan2(n2[1], n2[0]) - math.atan2(n1[1], n1[0])) % PI2
        if sweep > PI:
            sweep -= PI2
        out += Arc(v, abs(d), a1, sweep).sample(tol) + [p2]
        return
    if join == "mitre" and cos > -1 + 1e-12 and math.sqrt(2.0 / (1.0 + cos)) <= limit:
        out.append(_add(v, _mul(_add(n1, n2), d / (1.0 + cos))))
        return
    out += [p1, p2]                                    # bevel, and mitres over the limit


def _raw_offset(pts, d, closed, join, limit, tol, cap="butt"):
    """Raw ring swept d to the right of pts (open curves go there and back round end caps). Its winding
    counts the bar pieces covering a point, so a positive-winding cleanup gives the exact region."""
    if closed:
        n, out = len(pts), []
        for i in range(n):
            v = pts[i]
            _join(out, v, _right(_unit(_sub(v, pts[i - 1]))), _right(_unit(_sub(pts[(i + 1) % n], v))), d, join, limit, tol)
        return out
    m = len(pts)
    seq = list(pts) + list(reversed(pts))[1:-1]
    n, out = len(seq), []
    for i in range(n):
        v, a, b = seq[i], seq[i - 1], seq[(i + 1) % n]
        n1 = _right(_unit(_sub(v, a)))
        if i in (0, m - 1):                            # an end: half turn round a cap
            p1, p2 = _add(v, _mul(n1, d)), _sub(v, _mul(n1, d))
            if cap == "round":
                out += Arc(v, abs(d), math.atan2(n1[1], n1[0]) + (0 if d > 0 else PI), PI).sample(tol) + [p2]
            elif cap == "square":
                ext = _mul(_unit(_sub(v, a)), abs(d))
                out += [_add(p1, ext), _add(p2, ext)]
            else:
                out += [p1, p2]
            continue
        _join(out, v, n1, _right(_unit(_sub(b, v))), d, join, limit, tol)
    return out


def offset(poly, d, closed=True, join="mitre", mitre_limit=2.0, tol=TOL):
    """Offset of a polyline or path. Closed: the region grown by d (shrunk for d < 0) as shapes, cleaned
    of self-intersections (inward offsets that pinch split into several shapes, or vanish). Open: the
    parallel polyline d to the left, loops at tight curvature trimmed.
    join mitre | round | bevel; mitres longer than mitre_limit x |d| fall back to bevel."""
    pts = simplify(sample(poly, tol), closed)
    if closed:
        return clean([_raw_offset(orient(pts), d, True, join, mitre_limit, tol)])
    out = []
    for i in range(len(pts)):
        if i in (0, len(pts) - 1):
            j = 1 if i == 0 else i
            out.append(_add(pts[i], _mul(_perp(_unit(_sub(pts[j], pts[j - 1]))), d)))
            continue
        _join(out, pts[i], _right(_unit(_sub(pts[i], pts[i - 1]))), _right(_unit(_sub(pts[i + 1], pts[i]))), -d,
              join, mitre_limit, tol)
    return _trim_loops(_dedupe(out, False))


def _trim_loops(pts):
    """Cut out the loops of a polyline that crosses itself."""
    for _ in range(len(pts)):
        xs = intersections(pts)
        if not xs:
            break
        x, s1, s2 = xs[0]
        cs = _cum(pts, False)[1]
        i = max(k for k in range(len(cs)) if cs[k] <= s1)
        j = max(k for k in range(len(cs)) if cs[k] <= s2)
        pts = _dedupe(pts[:i + 1] + [x] + pts[j + 1:], False)
    return pts


def stroke(curve, width, closed=False, join="round", cap="butt", mitre_limit=2.0, tol=TOL):
    """The band of `width` centred on a polyline or path, as shapes: a flat bar seen square to its
    face. A closed curve (closed=True, a path that ends where it starts, or points repeating the first
    at the end) gives a ring band (outer + hole). cap butt | round | square on open curves."""
    pts = sample(curve, tol, closed or None)
    closed = closed or (_is_path(curve) and _dist(curve[-1].end, curve[0].start) < 1e-9) or \
        (not _is_path(curve) and len(pts) > 3 and _dist(pts[0], pts[-1]) < 1e-9)
    pts = simplify(_dedupe(pts, closed), closed)
    h = width / 2.0
    if closed:
        ring = orient(pts)
        return difference(clean([_raw_offset(ring, h, True, join, mitre_limit, tol)]),
                          clean([_raw_offset(ring, -h, True, join, mitre_limit, tol)]))
    return clean([_raw_offset(pts, h, False, join, mitre_limit, tol, cap)])


def offset_shapes(shapes, d, join="mitre", mitre_limit=2.0, tol=TOL):
    """Shapes grown by d (shrunk for d < 0); holes shrink as the outline grows."""
    return clean([_raw_offset(simplify(r), d, True, join, mitre_limit, tol) for r in _rings(shapes)])


def offset_path(path, d, closed=None, join="mitre"):
    """Exact offset d to the left of a path of lines and arcs: lines stay lines, arcs concentric arcs.
    Where offset pieces part at a corner they are extended to meet (mitre) or joined by an arc round
    the corner (round); where they overlap they are trimmed to their crossing. No global cleanup."""
    if closed is None:
        closed = _dist(path[-1].end, path[0].start) < 1e-9
    segs = [s.offset(d) for s in path]
    n = len(segs)
    starts, ends = [s.start for s in segs], [s.end for s in segs]
    rounds = {}
    for k in (range(n) if closed else range(1, n)):
        a, b = segs[k - 1], segs[k]
        if _dist(a.end, b.start) < 1e-9:
            continue
        corner = path[k].start
        meet = _extended_meet(a, b)
        overlapping = _dot(_sub(b.start, a.end), a.tangent(1.0)) < 0
        if meet and (join == "mitre" or overlapping):
            p = min(meet, key=lambda q: _dist(q, corner))
            ends[k - 1], starts[k] = p, p
        else:
            rounds[k] = corner
    out = []
    for k in range(n):
        if k in rounds:
            c, a_end, b_start = rounds[k], ends[k - 1], starts[k]
            mid_dir = _unit(_sub(_mid(a_end, b_start), c)) if _dist(_mid(a_end, b_start), c) > 1e-12 else _perp(segs[k].tangent())
            out.append(Arc.through(a_end, _add(c, _mul(mid_dir, abs(d))), b_start))
        out.append(_retrim(segs[k], starts[k], ends[k]))
    if closed and 0 in rounds:
        out.append(out.pop(0))
    return out


def _extended_meet(a, b):
    if isinstance(a, Seg) and isinstance(b, Seg):
        return intersect_lines(a.a, a.b, b.a, b.b, segments=False)
    if isinstance(a, Seg) or isinstance(b, Seg):
        s, c = (a, b) if isinstance(a, Seg) else (b, a)
        return intersect_line_circle(s.a, s.b, c.c, c.r, segment=False)
    return intersect_circles(a.c, a.r, b.c, b.r)


def _retrim(s, start, end):
    if isinstance(s, Seg):
        return Seg(start, end)
    a0 = _ang(start, s.c)
    raw = _ang(end, s.c) - a0
    return Arc(s.c, s.r, a0, raw + PI2 * round((s.sweep - raw) / PI2))


# ---------------------------------------------------------------- booleans (winding overlay)
def _rings(x):
    """Oriented rings (outers counter-clockwise, holes clockwise) of a ring, path, shape or list of them."""
    if x is None:
        return []
    if _is_path(x):
        return [orient(sample(x, TOL, True))]
    if _is_pts(x):
        return [orient(x)]
    if isinstance(x, tuple) and len(x) == 2 and _is_pts(x[0]):
        return [orient(x[0])] + [orient(h, ccw=False) for h in x[1]]
    out = []
    for item in x:
        out += _rings(item)
    return out


class _Pool:
    """Vertex snapping: points within eps share one index."""

    def __init__(self, eps):
        self.eps, self.cells, self.pts = eps, {}, []

    def id(self, p):
        e = self.eps
        kx, ky = int(math.floor(p[0] / e)), int(math.floor(p[1] / e))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in self.cells.get((kx + dx, ky + dy), ()):
                    q = self.pts[i]
                    if abs(q[0] - p[0]) <= e and abs(q[1] - p[1]) <= e:
                        return i
        self.pts.append(p)
        self.cells.setdefault((kx, ky), []).append(len(self.pts) - 1)
        return len(self.pts) - 1


def _on_seg(p, a, b, eps):
    """Parameter of p on a-b when p lies within eps of its interior (clear of its ends), else None."""
    d = _sub(b, a)
    L = _len(d)
    t = _dot(_sub(p, a), d) / (L * L)
    if t * L <= eps or (1 - t) * L <= eps or abs(_crs(_sub(p, a), d)) / L > eps:
        return None
    return t


def _cross_pair(p, q, r, s, ci, cj, eps):
    touched = False
    for pt, a, b, c in ((r, p, q, ci), (s, p, q, ci), (p, r, s, cj), (q, r, s, cj)):
        t = _on_seg(pt, a, b, eps)
        if t is not None:
            c.append((t, pt))
            touched = True
    d1, d2 = _sub(q, p), _sub(s, r)
    den = _crs(d1, d2)
    L1, L2 = _len(d1), _len(d2)
    if abs(den) <= 1e-14 * L1 * L2:
        return
    w = _sub(r, p)
    t, u = _crs(w, d2) / den, _crs(w, d1) / den
    if eps / L1 < t < 1 - eps / L1 and eps / L2 < u < 1 - eps / L2:
        x = _add(p, _mul(d1, t))
        if touched and min(_dist(x, r), _dist(x, s), _dist(x, p), _dist(x, q)) <= 2 * eps:
            return
        ci.append((t, x))
        cj.append((u, x))


def _split_edges(edges, eps):
    """Cut points of every edge where it meets another (crossings, T-touches, collinear overlaps)."""
    m = len(edges)
    cuts = [[] for _ in range(m)]
    xmax = [max(e[0][0], e[1][0]) for e in edges]
    order = sorted(range(m), key=lambda i: min(edges[i][0][0], edges[i][1][0]))
    active = []
    for i in order:
        p, q = edges[i][0], edges[i][1]
        x0 = min(p[0], q[0])
        y0, y1 = min(p[1], q[1]), max(p[1], q[1])
        active = [j for j in active if xmax[j] >= x0 - eps]
        for j in active:
            r, s = edges[j][0], edges[j][1]
            if max(r[1], s[1]) < y0 - eps or min(r[1], s[1]) > y1 + eps:
                continue
            _cross_pair(p, q, r, s, cuts[i], cuts[j], eps)
        active.append(i)
    return cuts


def _overlay(A, B, keep):
    """Boundary of {p: keep(winding of A at p, winding of B at p)} as shapes."""
    allp = [p for r in A + B for p in r]
    if not allp:
        return []
    x0, y0, x1, y1 = bbox(allp)
    eps = max(x1 - x0, y1 - y0, 1e-12) * _SNAP
    edges = []
    for src, rings in ((0, A), (1, B)):
        for r in rings:
            n = len(r)
            for i in range(n):
                if _dist(r[i], r[(i + 1) % n]) > eps:
                    edges.append((r[i], r[(i + 1) % n], src))
    cuts = _split_edges(edges, eps)
    pool = _Pool(eps)
    pieces = {}
    for (p, q, src), cs in zip(edges, cuts):
        chain = [pool.id(p)] + [pool.id(pt) for _, pt in sorted(cs)] + [pool.id(q)]
        for u, v in zip(chain, chain[1:]):
            if u != v:
                rec = pieces.setdefault((u, v) if u < v else (v, u), [0, 0])
                rec[src] += 1 if u < v else -1
    P = pool.pts
    ca, sa = math.cos(0.6180339887), math.sin(0.6180339887)    # rays in a turned frame: no edge lies along one
    R = [(x * ca + y * sa, -x * sa + y * ca) for x, y in P]
    keys = [k for k, d in pieces.items() if d[0] or d[1]]
    segs = [(R[u], R[v], pieces[(u, v)]) for u, v in keys]
    if not segs:
        return []
    ylo, yhi = min(r[1] for r in R), max(r[1] for r in R)
    nb = max(1, min(4096, len(segs) // 4))
    hb = (yhi - ylo) / nb or 1.0
    bands = [[] for _ in range(nb)]
    for k, (a, b, _) in enumerate(segs):
        lo, hi = sorted((a[1], b[1]))
        for bi in range(max(0, int((lo - ylo) / hb)), min(nb - 1, int((hi - ylo) / hb)) + 1):
            bands[bi].append(k)
    out_edges = {}
    for k, (a, b, d) in enumerate(segs):
        x, y = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        w = [0, 0]
        for j in bands[min(nb - 1, max(0, int((y - ylo) / hb)))]:
            if j == k:
                continue
            c, e, dj = segs[j]
            if (c[1] <= y) != (e[1] <= y) and c[0] + (y - c[1]) * (e[0] - c[0]) / (e[1] - c[1]) > x:
                s = 1 if e[1] > c[1] else -1
                w[0] += s * dj[0]
                w[1] += s * dj[1]
        wr = w if b[1] > a[1] else [w[0] - d[0], w[1] - d[1]]         # just right of a -> b
        kl, kr = keep(wr[0] + d[0], wr[1] + d[1]), keep(wr[0], wr[1])
        if kl != kr:
            u, v = keys[k] if kl else keys[k][::-1]                    # inside on the left
            out_edges.setdefault(u, []).append(v)
    return _link(out_edges, P)


def _link(out_edges, P):
    rings, used = [], set()
    for start in list(out_edges):
        for first in out_edges[start]:
            if (start, first) in used:
                continue
            ring, prev, cur = [start], start, first
            used.add((start, first))
            while cur != start:
                ring.append(cur)
                cands = [w for w in out_edges.get(cur, ()) if (cur, w) not in used]
                if not cands:
                    break
                back = math.atan2(P[prev][1] - P[cur][1], P[prev][0] - P[cur][0])
                nxt = min(cands, key=lambda w: ((back - math.atan2(P[w][1] - P[cur][1], P[w][0] - P[cur][0])) % PI2) or PI2)
                used.add((cur, nxt))
                prev, cur = cur, nxt
            if cur == start and len(ring) >= 3:
                rings.append(simplify([P[i] for i in ring]))
    rings = [r for r in rings if len(r) >= 3 and area(r) != 0]
    shapes = [(o, []) for o in sorted((r for r in rings if area(r) > 0), key=area)]
    for h in (r for r in rings if area(r) < 0):
        a, b = max(zip(h, h[1:] + h[:1]), key=lambda e: _dist(*e))
        probe = _add(_mid(a, b), _mul(_perp(_unit(_sub(b, a))), max(_dist(a, b) * 1e-4, 1e-9)))   # in the solid
        for o, hs in shapes:
            if winding([o], probe):
                hs.append(h)
                break
    return shapes


def union(*regions):
    """Union of any number of rings / paths / shapes, as shapes."""
    return _overlay([r for x in regions for r in _rings(x)], [], lambda a, b: a > 0)


def clean(rings):
    """Region where a list of raw rings (taken as drawn, not re-oriented) winds positively."""
    return _overlay([[_P(p) for p in r] for r in rings if len(r) >= 3], [], lambda a, b: a > 0)


def intersection(a, b):
    return _overlay(_rings(a), _rings(b), lambda wa, wb: wa > 0 and wb > 0)


def difference(a, b):
    return _overlay(_rings(a), _rings(b), lambda wa, wb: wa > 0 and wb <= 0)


# ---------------------------------------------------------------- triangulation
class _N:
    __slots__ = ("i", "x", "y", "prev", "next", "alive")

    def __init__(self, i, x, y):
        self.i, self.x, self.y, self.alive = i, x, y, True
        self.prev = self.next = None


def _tri(a, b, c): return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
def _in_tri(a, b, c, p): return _tri(a, b, p) >= 0 and _tri(b, c, p) >= 0 and _tri(c, a, p) >= 0


def _iter(start):
    p = start
    while True:
        yield p
        p = p.next
        if p is start:
            return


def _link_ring(nodes):
    n = len(nodes)
    for k in range(n):
        nodes[k].next, nodes[k].prev = nodes[(k + 1) % n], nodes[k - 1]
    return nodes[0]


def _locally_inside(a, b):
    if _tri(a.prev, a, a.next) >= 0:
        return _tri(a, a.next, b) >= 0 and _tri(a.prev, a, b) >= 0
    return _tri(a, a.next, b) >= 0 or _tri(a.prev, a, b) >= 0


def _bridge(hole, outer):
    """Join a clockwise hole into the counter-clockwise outer loop by a doubled edge from the hole's
    rightmost vertex to an outer vertex it can see along the +x ray."""
    m = max(_iter(hole), key=lambda p: (p.x, -p.y))
    hx, hy = m.x, m.y
    best, qx = None, float("inf")
    for a in _iter(outer):
        b = a.next
        if a.y != b.y and min(a.y, b.y) <= hy <= max(a.y, b.y):
            x = a.x + (hy - a.y) * (b.x - a.x) / (b.y - a.y)
            if hx <= x < qx:
                qx, best = x, (a if a.x > b.x else b)
    assert best is not None, "triangulate: hole outside its outline"
    if qx > hx:
        I = _N(-1, qx, hy)
        cand, tmin = best, float("inf")
        for p in _iter(outer):
            if p is best or not (hx < p.x <= best.x):
                continue
            if _in_tri(m, I, best, p) or _in_tri(m, best, I, p):
                t = abs(hy - p.y) / (p.x - hx)
                if _locally_inside(p, m) and (t < tmin or (t == tmin and p.x < cand.x)):
                    cand, tmin = p, t
        best = cand
    b2, m2 = _N(best.i, best.x, best.y), _N(m.i, m.x, m.y)
    bn, mp = best.next, m.prev
    best.next, m.prev = m, best
    b2.next, bn.prev = bn, b2
    m2.next, b2.prev = b2, m2
    mp.next, m2.prev = m2, mp
    return best


def triangulate(outer, holes=()):
    """Triangles (index triples into outer + holes, in that order) covering a polygon with holes,
    counter-clockwise. Ear clipping with the holes bridged in, for simple rings such as the booleans give."""
    outer = orient(outer)
    holes = [orient(h, ccw=False) for h in holes]
    loops, base = [], 0
    for ring in [outer] + holes:
        loops.append([_N(base + k, p[0], p[1]) for k, p in enumerate(ring)])
        base += len(ring)
    start = _link_ring(loops[0])
    for h in sorted((_link_ring(l) for l in loops[1:]), key=lambda h: -max(n.x for n in _iter(h))):
        start = _bridge(h, start)
    nodes = list(_iter(start))
    xs, ys = [n.x for n in nodes], [n.y for n in nodes]
    x0, y0 = min(xs), min(ys)
    g = max(1, int(math.sqrt(len(nodes)) / 2))
    cell = max(max(xs) - x0, max(ys) - y0, 1e-12) / g * (1 + 1e-9)
    grid = {}
    for n in nodes:
        grid.setdefault((int((n.x - x0) / cell), int((n.y - y0) / cell)), []).append(n)

    def is_ear(b, strict):
        a, c = b.prev, b.next
        if _tri(a, b, c) <= 0:
            return False
        lx, hx, ly, hy = min(a.x, b.x, c.x), max(a.x, b.x, c.x), min(a.y, b.y, c.y), max(a.y, b.y, c.y)
        corners = ((a.x, a.y), (b.x, b.y), (c.x, c.y))
        for gx in range(int((lx - x0) / cell), int((hx - x0) / cell) + 1):
            for gy in range(int((ly - y0) / cell), int((hy - y0) / cell) + 1):
                for p in grid.get((gx, gy), ()):
                    if not p.alive or p is a or p is b or p is c or (p.x, p.y) in corners:
                        continue
                    if lx <= p.x <= hx and ly <= p.y <= hy and _in_tri(a, b, c, p) and \
                            (not strict or _tri(p.prev, p, p.next) <= 0):
                        return False
        return True

    tris = []
    ear, remaining, stall, strict = start, len(nodes), 0, True

    def clip(b):
        a, c = b.prev, b.next
        tris.append((a.i, b.i, c.i))
        b.alive = False
        a.next, c.prev = c, a
        return c

    while remaining > 3:
        if is_ear(ear, strict):
            ear, remaining, stall = clip(ear), remaining - 1, 0
            continue
        ear, stall = ear.next, stall + 1
        if stall > remaining:
            if strict:
                strict = False
            else:                                       # numerically stuck: clip the most convex corner
                ear = clip(max(_iter(ear), key=lambda n: _tri(n.prev, n, n.next)))
                remaining -= 1
            stall = 0
    tris.append((ear.prev.i, ear.i, ear.next.i))
    return tris


# ---------------------------------------------------------------- solids
def _strips(shapes, max_vertices):
    """Shapes cut into strips across x until each strip's solid stays under max_vertices."""
    total = sum(len(o) + sum(len(h) for h in hs) for o, hs in shapes)
    if 2 * total <= max_vertices:
        return [shapes]
    x0, y0, x1, y1 = bbox([p for o, _ in shapes for p in o])
    k = int(math.ceil(2.5 * total / max_vertices))
    cuts = [x0 - 1] + [x0 + (x1 - x0) * i / k for i in range(1, k)] + [x1 + 1]
    out = []
    for a, b in zip(cuts, cuts[1:]):
        piece = intersection(shapes, rect(a, y0 - 1, b, y1 + 1))
        if piece:
            out += _strips(piece, max_vertices)
    return out


def solid(shapes, depth, plane="xz", centre=True, at=(0.0, 0.0, 0.0)):
    """(vertices, faces) of shapes extruded by depth: triangle caps and quad walls, outward.
    plane xz: shape x -> x, y -> z, depth along y; xy: depth along z; yz: x -> y, y -> z, depth along x.
    centre: depth from -depth/2 to depth/2, else 0 to depth."""
    verts, faces = [], []
    w0, w1 = (-depth / 2.0, depth / 2.0) if centre else (0.0, depth)

    def place(u, v, w):
        p = (u, w, v) if plane == "xz" else (w, u, v) if plane == "yz" else (u, v, w)
        return [p[0] + at[0], p[1] + at[1], p[2] + at[2]]

    for o, hs in shapes:
        rings = [orient(simplify(_dedupe(o, True, 2e-5)))] + [orient(simplify(_dedupe(h, True, 2e-5)), ccw=False) for h in hs]
        rings = [r for r in rings if len(r) >= 3 and abs(area(r)) > 1e-10]
        flat = [p for r in rings for p in r]
        base, n = len(verts), len(flat)
        verts += [place(p[0], p[1], w0) for p in flat] + [place(p[0], p[1], w1) for p in flat]
        for a, b, c in triangulate(rings[0], rings[1:]):
            faces.append([base + a, base + c, base + b])               # w0 cap
            faces.append([base + n + a, base + n + b, base + n + c])   # w1 cap
        k = 0
        for r in rings:
            m = len(r)
            for i in range(m):
                a, b = base + k + i, base + k + (i + 1) % m
                faces.append([a, b, b + n, a + n])
            k += m
    vol = 0.0
    for f in faces:
        p0 = verts[f[0]]
        for i in range(1, len(f) - 1):
            p1, p2 = verts[f[i]], verts[f[i + 1]]
            vol += (p0[0] * (p1[1] * p2[2] - p1[2] * p2[1]) - p0[1] * (p1[0] * p2[2] - p1[2] * p2[0]) +
                    p0[2] * (p1[0] * p2[1] - p1[1] * p2[0]))
    if vol < 0:                                        # the xz mapping is a mirror: turn every face
        faces = [f[::-1] for f in faces]
    return verts, faces


def plate(self, id_, shapes, depth, plane="xz", centre=True, at=(0.0, 0.0, 0.0), slot=None, max_vertices=15000):
    """Graph method. A flat plate of shapes extruded by depth (`surface` shells): a cast or pierced
    plate, or a panel of flat bars already unioned into one outline. Faces are triangles and quads, so
    none nears the 128-corner limit; a plate over max_vertices is cut into strips across x, each its own
    closed shell, meeting face to face (one piece to pgm audit). Frame as solid(). Returns the node id."""
    shapes = [s for s in shapes if s[0]]
    assert shapes, "plate: no outline"
    shells = []
    for k, piece in enumerate(_strips(shapes, max_vertices)):
        verts, faces = solid(piece, depth, plane, centre, at)
        assert len(verts) <= 16384 and len(faces) <= 24000, "plate: strip over the surface node's limits"
        shells.append(self.n(f"{id_}-shell{k}", "surface", vertices=[[round(c, 6) for c in v] for v in verts], faces=faces))
    if slot:
        return self.mat(id_, self.join(id_ + "-shells", *shells), slot)
    return self.join(id_, *shells)


def _attach():
    import kitlib
    kitlib.Graph.plate = plate


_attach()
