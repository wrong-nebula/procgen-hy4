"""Classical orders and moulding helpers for kitlib: moulding profiles, mitred runs, turned work, the five orders
by Vignola's rule, entablatures, pediments, balustrades and window or door surrounds.

Built by Opus 5.5 (medium), 2026-11-16. Attached to kitlib.Graph when kitlib is imported (the last lines of
kitlib.py), so builders call g.column(...), g.entablature(...), g.moulding_run(...); the profile functions are
plain functions here and static methods on Graph (g.profile(...) is kitlib_orders.profile(...)).

Canon. Proportions follow Vignola's Regola delli cinque ordini (1562) as tabulated in Ware's American Vignola.
The MODULE is the LOWER RADIUS of the column shaft (Vignola's modulo, a semi-diameter), divided into 12 parts for
the Tuscan and Doric and 18 parts for the Ionic, Corinthian and Composite. Column heights (base, shaft and capital)
are 14, 16, 18, 20 and 20 modules; every base is 1 module; capitals are 1, 1, 2/3, 7/3 and 7/3 modules; the
entablature is a quarter of the column (3.5, 4, 4.5, 5 and 5 modules), split architrave / frieze / cornice as
1 / 7/6 / 4/3 (Tuscan), 1 / 1.5 / 1.5 (Doric), 1.25 / 1.5 / 1.75 (Ionic) and 1.5 / 1.5 / 2 (Corinthian,
Composite). The shaft is straight for its lower third and diminishes above by Vignola's first rule (radius
r0 cos(theta), theta divided evenly with the height) to 4/5 of the lower radius (Tuscan) or 5/6 (the others).
Flutes: 20 meeting in sharp arrises (Doric), 24 parted by fillets of a quarter of the flute (Ionic and up).
Doric triglyphs are 1 module wide at 2.5 modules (square metopes) with regulae, guttae and mutules; dentils
4 parts wide at 6 parts; Corinthian modillions at 4/3 modules. Tables: ORDERS and _ENT below.

Frames. Profiles are (x, z): x the projection from a reference face (or the radius, for turned work), z up,
listed from bottom to top. Runs follow an outline: a moulding projects to the RIGHT of the direction of travel
(outward for a counter-clockwise plan outline; a facade run along +x projects towards -y). Columns stand on the
origin, axis Z; facade pieces (pilaster, pediment, surround) are built against a wall face in the plane y = 0
with their front towards -y, then turned by `facing` (the outward normal, as kitlib's "+x", "-y" or an angle).
Units: metres, except inside an order helper, where lengths are modules times `module`. Every shell is a closed
`surface` or `revolve`; the members of a piece overlap, so `pgm audit` sees one component. Smooth normals with
crisp creases: corners of profiles and of runs get a relief 1/500 of the profile's size, so shading stays sharp.

Module controls (Built by Opus 5.5 (medium), 2026-11-16). Lengths may be X expressions of the module's controls:
`module` and `height=` (an order sized to a storey: order_for_height), `at`, outline points, `z`, `depth`, a
pediment's span and rise, a balustrade's outline and height, a surround's width, height and band, a moulded
block's outline and height, a run's `module` (profile scale). Columns and pilasters are built in modules and
scaled. Runs, blocks and the other members are built at the controls' defaults and written with vertices affine in
those inputs (finite differences at the defaults): exact for straight work whose directions do not turn with the
controls; a pediment whose rise is an expression independent of its span is approximate away from the defaults.
When an outline, span, height or a run's module is an expression, pass the module's controls dict as `controls=`
(or set `g.controls = controls` once on the Graph). Counts follow the controls inside the document: dentils,
modillions, triglyphs, piers and balusters are `line` points whose count is an expression of the run length (the
spacing stays within half a pitch of the canon, never stretched blocks); flutes and leaves are plain ints (they
belong to members scaled whole by the module).
Tests and sample sheet: recipes/test_kitlib_orders.py.
"""
import json
import math

from kitlib import PI, X, Graph, _facing, _num, _v, ceil as _xceil, floor as _xfloor, gt as _xgt, mx as _xmax, rnd as _xrnd

TAU = 2 * PI


# ------------------------------------------------------------------------------------------------ vectors
def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _scl(a, k): return (a[0] * k, a[1] * k, a[2] * k)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def _len(a): return math.sqrt(_dot(a, a))


def _unit(a):
    n = _len(a)
    if n < 1e-12:
        raise ValueError("zero-length direction")
    return (a[0] / n, a[1] / n, a[2] / n)


def _p3(p, z=0.0):
    return (float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else float(z))


# ------------------------------------------------------------------------------------------------ 2D polygons
def _area2(poly):
    return sum(poly[i - 1][0] * poly[i][1] - poly[i][0] * poly[i - 1][1] for i in range(len(poly)))


def _dedupe(pts, closed=False, tol=1e-7):
    out = []
    for p in pts:
        p = tuple(p)
        if not out or math.dist(p, out[-1]) > tol:
            out.append(p)
    if closed and len(out) > 1 and math.dist(out[0], out[-1]) <= tol:
        out.pop()
    return out


def _earclip(poly):
    """Triangles (index triples, counter-clockwise) of a simple polygon; points on an ear's edge block it, so
    collinear runs end up in proper triangles."""
    n = len(poly)
    idx = list(range(n))
    if _area2(poly) < 0:
        idx.reverse()

    def cr(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    scale = max(1e-9, max(abs(c) for p in poly for c in p))
    eps = 1e-13 * scale * scale
    tris = []
    while len(idx) > 3:
        m = len(idx)
        for i in range(m):
            a, b, c = idx[i - 1], idx[i], idx[(i + 1) % m]
            pa, pb, pc = poly[a], poly[b], poly[c]
            if cr(pa, pb, pc) <= eps:
                continue
            if any(cr(pa, pb, poly[p]) >= -eps and cr(pb, pc, poly[p]) >= -eps and cr(pc, pa, poly[p]) >= -eps
                   for p in idx if p not in (a, b, c)):
                continue
            tris.append((a, b, c))
            idx.pop(i)
            break
        else:
            raise ValueError("could not triangulate a cap (profile or outline crosses itself)")
    tris.append(tuple(idx))
    return tris


def _crisp(pts, eps, closed=False, angle=0.5):
    """Split each corner turning more than `angle` (radians) into two points eps apart along its edges, so smooth
    shading keeps the crease (vertex normals are area weighted, the relief face is tiny)."""
    if not eps:
        return list(pts)
    n = len(pts)
    out = []
    for i in range(n):
        p = pts[i]
        if not closed and (i == 0 or i == n - 1):
            out.append(p)
            continue
        a, c = pts[i - 1], pts[(i + 1) % n]
        u = (p[0] - a[0], p[1] - a[1])
        w = (c[0] - p[0], c[1] - p[1])
        lu, lw = math.hypot(*u), math.hypot(*w)
        if lu < 3 * eps or lw < 3 * eps:
            out.append(p)
            continue
        cosang = (u[0] * w[0] + u[1] * w[1]) / (lu * lw)
        if math.acos(max(-1.0, min(1.0, cosang))) < angle:
            out.append(p)
            continue
        out.append((p[0] - u[0] / lu * eps, p[1] - u[1] / lu * eps))
        out.append((p[0] + w[0] / lw * eps, p[1] + w[1] / lw * eps))
    return out


# ------------------------------------------------------------------------------------------------ mouldings
MOULDINGS = ("fillet", "fascia", "corona", "plinth", "band", "torus", "astragal", "bead", "scotia", "cavetto",
             "ovolo", "cyma_recta", "cyma_reversa")


def _quarter(a, b, convex, n):
    """Quarter ellipse from a to b, bulging out (convex: centre at the inner corner) or hollow (concave)."""
    (x0, z0), (x1, z1) = a, b
    pts = []
    if convex == (x0 < x1):                       # centre (x0, z1): a straight below it, b level with it
        for i in range(n + 1):
            t = PI / 2 * i / n
            pts.append((x0 + (x1 - x0) * math.sin(t), z1 + (z0 - z1) * math.cos(t)))
    else:                                          # centre (x1, z0): a level with it, b straight above it
        for i in range(n + 1):
            t = PI / 2 * i / n
            pts.append((x1 + (x0 - x1) * math.cos(t), z0 + (z1 - z0) * math.sin(t)))
    return pts


def moulding(kind, height, x0, x1=None, z0=0.0, segments=8, depth=None):
    """Points (x, z) of one moulding from its bottom to its top. Units are the caller's (metres or modules).

    kind: fillet / fascia / corona / plinth / band (a flat face at x0, slanted if x1 differs); torus / astragal /
    bead (a half round standing on the face x0, bulging `depth` (default height / 2) beyond it); ovolo (convex
    quarter round from x0 at the bottom to x1 at the top); cavetto (hollow quarter); cyma_recta (S curve whose
    outer half is hollow: the crowning sima); cyma_reversa (S whose outer half is round: the talon of bed
    mouldings and abaci); scotia (the deep hollow of a base, from x0 to x1, cut `depth` (default 0.4 height)
    behind the line between them, deepest two thirds up as Vignola's two-centred scotia). x1 < x0 makes a
    moulding recede as it rises (bases). Example: moulding("ovolo", 0.1, 0.0, 0.12)"""
    h, n = float(height), max(2, int(segments))
    x0 = float(x0)
    x1 = x0 if x1 is None else float(x1)
    z1 = z0 + h
    if kind in ("fillet", "fascia", "corona", "plinth", "band"):
        return [(x0, z0), (x1, z1)]
    if kind in ("torus", "astragal", "bead"):
        b = h / 2 if depth is None else float(depth)
        m = 2 * n
        return [(x0 + b * math.sin(PI * i / m), z0 + h * (1 - math.cos(PI * i / m)) / 2) for i in range(m + 1)]
    if kind == "ovolo":
        return _quarter((x0, z0), (x1, z1), True, n)
    if kind == "cavetto":
        return _quarter((x0, z0), (x1, z1), False, n)
    if kind in ("cyma_recta", "cyma_reversa"):
        mid = ((x0 + x1) / 2, (z0 + z1) / 2)
        lower_outer = x0 > x1                       # receding: the lower half is the outer one
        outer_hollow = kind == "cyma_recta"
        lower_convex = (not outer_hollow) if lower_outer else outer_hollow
        k = max(2, n // 2 + 1)
        return _quarter((x0, z0), mid, lower_convex, k) + _quarter(mid, (x1, z1), not lower_convex, k)[1:]
    if kind == "scotia":
        d = 0.4 * h if depth is None else float(depth)
        m = 2 * n
        return [(x0 + (x1 - x0) * (i / m) - d * math.sin(PI * (i / m) ** 1.6), z0 + h * i / m) for i in range(m + 1)]
    raise ValueError(f"unknown moulding {kind!r}; one of {', '.join(MOULDINGS)}")


class Profile:
    """A moulding profile: contour points (x, z) from bottom to top, and tags name -> (z0, z1, x0, x1) of the
    tagged steps (dentil band, corona, crown...). `polygon(back)` closes it against a wall at x = back."""

    def __init__(self, points, tags=None):
        self.points = _dedupe(points)
        self.tags = dict(tags or {})

    @property
    def bottom(self): return min(p[1] for p in self.points)
    @property
    def top(self): return max(p[1] for p in self.points)
    @property
    def height(self): return self.top - self.bottom
    @property
    def reach(self): return max(p[0] for p in self.points)

    def polygon(self, back):
        """Closed polygon: the contour, then back to the wall line x = back at the top and the bottom."""
        assert back < min(p[0] for p in self.points), "profile back must lie behind every point of the contour"
        return _dedupe(self.points + [(back, self.points[-1][1]), (back, self.points[0][1])], closed=True)

    def scaled(self, k, dz=0.0):
        return Profile([(x * k, z * k + dz) for x, z in self.points],
                       {t: (a * k + dz, b * k + dz, c * k, d * k) for t, (a, b, c, d) in self.tags.items()})

    def shifted(self, dx=0.0, dz=0.0):
        return Profile([(x + dx, z + dz) for x, z in self.points],
                       {t: (a + dz, b + dz, c + dx, d + dx) for t, (a, b, c, d) in self.tags.items()})


def profile(steps, segments=8, z0=0.0):
    """Compose a Profile from named mouldings stacked bottom to top. Each step is (kind, height, x0[, x1[, depth]]
    [, tag]) with x measured from the reference face (or the axis); consecutive steps are joined by level (or
    straight) steps, so a fillet at x = 3 over an ovolo ending at x = 2 makes a one-unit step.
    Example: profile([("fascia", 0.3, 0), ("cyma_reversa", 0.1, 0, 0.08), ("fillet", 0.03, 0.09)])"""
    pts, tags, z = [], {}, float(z0)
    for st in steps:
        st = list(st)
        tag = st.pop() if len(st) > 3 and isinstance(st[-1], str) else None
        kind, h, x0 = st[0], float(st[1]), float(st[2])
        x1 = float(st[3]) if len(st) > 3 and st[3] is not None else None
        depth = st[4] if len(st) > 4 else None
        seg = moulding(kind, h, x0, x1, z, segments, depth)
        pts.extend(seg)
        if tag:
            box = (z, z + h, min(p[0] for p in seg), max(p[0] for p in seg))
            a = tags.get(tag)
            tags[tag] = box if a is None else (min(a[0], box[0]), max(a[1], box[1]), min(a[2], box[2]), max(a[3], box[3]))
        z += h
    return Profile(pts, tags)


def square(half, half_y=None):
    """Counter-clockwise rectangle outline centred on the origin (half widths)."""
    hy = half if half_y is None else half_y
    return [(-half, -hy), (half, -hy), (half, hy), (-half, hy)]


# ------------------------------------------------------------------------------------------------ meshes
def _fix_orientation(verts, faces):
    vol = 0.0
    for f in faces:
        for i in range(1, len(f) - 1):
            vol += _dot(verts[f[0]], _cross(verts[f[i]], verts[f[i + 1]]))
    return faces if vol >= 0 else [list(reversed(f)) for f in faces]


def _sweep_mesh(path, poly, up=(0.0, 0.0, 1.0), closed=False, cuts=(None, None), eps=0.0):
    """Closed shell of a 2D polygon (px along the right of travel, pz along up x travel) swept along a 3D polyline,
    with true mitres: each section is slid along its segment onto the plane bisecting the corner. cuts: plane
    normals for the end sections of an open path (None: square). eps: crease relief at corners and open ends."""
    path = [_p3(p) for p in path]
    path = [p for i, p in enumerate(path) if i == 0 or _len(_sub(p, path[i - 1])) > 1e-9]
    if closed and _len(_sub(path[0], path[-1])) < 1e-9:
        path.pop()
    assert len(path) >= 2, "a run needs two distinct points"
    if eps:
        out, n = [], len(path)
        for i, p in enumerate(path):
            if not closed and (i == 0 or i == n - 1):
                q = path[1] if i == 0 else path[-2]
                if _len(_sub(q, p)) > 4 * eps:
                    r = _add(p, _scl(_unit(_sub(q, p)), eps))
                    out.extend([p, r] if i == 0 else [r, p])
                else:
                    out.append(p)
                continue
            prev, nxt = path[i - 1], path[(i + 1) % n]
            u, w = _unit(_sub(p, prev)), _unit(_sub(nxt, p))
            if _dot(u, w) < math.cos(0.3) and _len(_sub(p, prev)) > 4 * eps and _len(_sub(nxt, p)) > 4 * eps:
                out.extend([_sub(p, _scl(u, eps)), p, _add(p, _scl(w, eps))])
            else:
                out.append(p)
        path = out
    poly = list(poly)
    if _area2(poly) < 0:
        poly.reverse()
    n, K = len(path), len(poly)
    segs = n if closed else n - 1
    T, N, B = [], [], []
    for i in range(segs):
        t = _unit(_sub(path[(i + 1) % n], path[i]))
        nn = _cross(t, up)
        if _len(nn) < 1e-9:
            raise ValueError("run segment parallel to its up vector")
        nn = _unit(nn)
        T.append(t), N.append(nn), B.append(_cross(nn, t))
    verts, rings = [], []
    for j in range(n):
        if closed or 0 < j < n - 1:
            a, c = (j - 1) % segs, j % segs
            m = _add(T[a], T[c])
            if _len(m) < 1e-9:
                raise ValueError("run reverses on itself")
            m = _unit(m)
        else:
            a = 0 if j == 0 else segs - 1
            cut = cuts[0] if j == 0 else cuts[1]
            m = _unit(cut) if cut is not None else T[a]
        t, nn, bb = T[a], N[a], B[a]
        tm = _dot(t, m)
        if abs(tm) < 0.05:
            raise ValueError("run end cut or mitre too oblique")
        ring = []
        for px, pz in poly:
            o = _add(_scl(nn, px), _scl(bb, pz))
            s = -_dot(o, m) / tm
            ring.append(len(verts))
            verts.append(_add(path[j], _add(o, _scl(t, s))))
        rings.append(ring)
    faces = []
    for j in range(segs):
        A, Bv = rings[j], rings[(j + 1) % n]
        for k in range(K):
            k1 = (k + 1) % K
            faces.append([A[k], A[k1], Bv[k1], Bv[k]])
    if not closed:
        tris = _earclip(poly)
        faces += [[rings[0][c], rings[0][b], rings[0][a]] for a, b, c in tris]
        faces += [[rings[-1][a], rings[-1][b], rings[-1][c]] for a, b, c in tris]
    return verts, _fix_orientation(verts, faces)


def _offset_outline(outline, d):
    """Plan outline (counter-clockwise) moved outward by d with mitred corners."""
    n, out = len(outline), []
    for i in range(n):
        p, a, c = outline[i], outline[i - 1], outline[(i + 1) % n]
        e1 = (p[0] - a[0], p[1] - a[1])
        e2 = (c[0] - p[0], c[1] - p[1])
        l1, l2 = math.hypot(*e1), math.hypot(*e2)
        n1 = (e1[1] / l1, -e1[0] / l1)
        n2 = (e2[1] / l2, -e2[0] / l2)
        k = 1.0 + n1[0] * n2[0] + n1[1] * n2[1]
        out.append((p[0] + d * (n1[0] + n2[0]) / k, p[1] + d * (n1[1] + n2[1]) / k))
    return out


def _offset_solid(outline, contour, eps=0.0):
    """Closed solid: a plan outline offset by each contour point (d outward, z), capped top and bottom."""
    outline = _dedupe([tuple(map(float, p[:2])) for p in outline], closed=True)
    if _area2(outline) < 0:
        outline = outline[::-1]
    contour = _dedupe(contour)
    rings2 = [_offset_outline(outline, d) for d, _ in contour]
    if eps:                                   # relieve the same corners on every ring (offset first, then split)
        contour = _crisp(contour, eps)

        def toward(p, q):
            d = math.dist(p, q)
            return (p[0] + (q[0] - p[0]) * eps / d, p[1] + (q[1] - p[1]) * eps / d)
        if math.dist(contour[0], contour[1]) > 3 * eps:      # relief rings next to the caps
            contour.insert(1, toward(contour[0], contour[1]))
        if math.dist(contour[-1], contour[-2]) > 3 * eps:
            contour.insert(-1, toward(contour[-1], contour[-2]))
        rings2 =[_offset_outline(outline, d) for d, _ in contour]
        sharp = [len(_crisp([outline[i - 1], outline[i], outline[(i + 1) % len(outline)]], eps)) == 4
                 for i in range(len(outline))]
        split = []
        for r in rings2:
            n = len(r)
            out = []
            for i in range(n):
                if not sharp[i]:
                    out.append(r[i])
                    continue
                p, a, c = r[i], r[i - 1], r[(i + 1) % n]
                la, lc = math.dist(p, a), math.dist(p, c)
                out.append((p[0] + (a[0] - p[0]) * eps / la, p[1] + (a[1] - p[1]) * eps / la))
                out.append((p[0] + (c[0] - p[0]) * eps / lc, p[1] + (c[1] - p[1]) * eps / lc))
            split.append(out)
        rings2 = split
        outline = rings2[0]
    verts, rings = [], []
    for r2, (_, z) in zip(rings2, contour):
        rings.append(list(range(len(verts), len(verts) + len(r2))))
        verts.extend((x, y, z) for x, y in r2)
    K = len(outline)
    faces = []
    for A, Bv in zip(rings, rings[1:]):
        for k in range(K):
            k1 = (k + 1) % K
            faces.append([A[k], A[k1], Bv[k1], Bv[k]])
    for ring, r2, top in ((rings[0], rings2[0], False), (rings[-1], rings2[-1], True)):
        for a, b, c in _earclip(r2):
            faces.append([ring[a], ring[b], ring[c]] if top else [ring[c], ring[b], ring[a]])
    return verts, _fix_orientation(verts, faces)


def _prism(poly, z0, z1):
    return _offset_solid(poly, [(0.0, z0), (0.0, z1)])


def _stack(rings, fan=True):
    """Closed shell through rings of equal size; caps are fans from each end ring's centroid (fan) or
    ear-clipped in plan (level rings)."""
    verts, idx = [], []
    for r in rings:
        idx.append(list(range(len(verts), len(verts) + len(r))))
        verts.extend(r)
    K = len(rings[0])
    faces = []
    for A, Bv in zip(idx, idx[1:]):
        for k in range(K):
            k1 = (k + 1) % K
            faces.append([A[k], A[k1], Bv[k1], Bv[k]])
    for ring, first in ((idx[0], True), (idx[-1], False)):
        if fan:
            c = tuple(sum(verts[i][a] for i in ring) / K for a in range(3))
            verts.append(c)
            ap = len(verts) - 1
            for k in range(K):
                k1 = (k + 1) % K
                faces.append([ring[k1], ring[k], ap] if first else [ring[k], ring[k1], ap])
        else:
            for a, b, c in _earclip([(verts[i][0], verts[i][1]) for i in ring]):
                faces.append([ring[c], ring[b], ring[a]] if first else [ring[a], ring[b], ring[c]])
    return verts, _fix_orientation(verts, faces)


def _map(mesh, f):
    verts, faces = mesh
    verts = [f(v) for v in verts]
    return verts, _fix_orientation(verts, faces)


def _merge(*meshes):
    verts, faces = [], []
    for v, f in meshes:
        o = len(verts)
        verts.extend(v)
        faces.extend([[i + o for i in face] for face in f])
    return verts, faces


def _emit(g, id_, mesh, smooth=True):
    verts, faces = mesh
    assert len(verts) <= 16384 and len(faces) <= 24000, f"{id_}: surface too large ({len(verts)} vertices)"
    node = g.n(id_ + ("-shell" if smooth else ""), "surface",
               vertices=[[round(c, 6) if _num(c) else c for c in v] for v in verts], faces=faces)
    return g.n(id_, "smoothNormals", [node]) if smooth else node


# ---------- control expressions (Built by Opus 5.5 (medium))
def _envof(g, controls):
    """Default values of the controls: the `controls=` dict, else g.controls, else none."""
    return _env(controls if controls is not None else getattr(g, "controls", None))


_BIN = {"+": lambda a, b: a + b, "-": lambda a, b: a - b, "*": lambda a, b: a * b, "/": lambda a, b: a / b,
        "max": max, "min": min, "pow": lambda a, b: a ** b, "atan2": math.atan2, "mod": math.fmod,
        ">": lambda a, b: 1.0 if a > b else 0.0, "<": lambda a, b: 1.0 if a < b else 0.0}
_UN = {"sin": math.sin, "cos": math.cos, "tan": math.tan, "sqrt": math.sqrt, "abs": abs, "neg": lambda a: -a,
       "floor": math.floor, "ceil": math.ceil, "round": lambda a: float(math.floor(a + 0.5))}


def _env(controls):
    return {k: float(c["value"]) if isinstance(c, dict) else float(c) for k, c in (controls or {}).items()}


def _ev(x, env):
    """Value of an expression at the controls' defaults."""
    x = _v(x)
    if _num(x):
        return float(x)
    if isinstance(x, str):
        assert x.startswith("$"), f"orders: field {x} cannot be used in a dimension"
        assert x[1:] in env, (f"orders: control {x[1:]} has no default here; pass the module's controls as controls= "
                              "(or set g.controls = controls)")
        return env[x[1:]]
    op, *args = x
    if op == "if":
        return _ev(args[1], env) if _ev(args[0], env) != 0 else _ev(args[2], env)
    vals = [_ev(a, env) for a in args]
    return _BIN[op](*vals) if len(vals) == 2 else _UN[op](vals[0])


def _affine(x):
    """x as const + sum coef * atom: (const, {key: [coef, atom]}); atoms are controls or non-affine terms."""
    x = _v(x)
    if _num(x):
        return float(x), {}
    if isinstance(x, list) and len(x) == 3 and x[0] in "+-*/":
        (ca, ta), (cb, tb) = _affine(x[1]), _affine(x[2])
        op = x[0]
        if op in "+-":
            sg = 1.0 if op == "+" else -1.0
            out = {k: list(v) for k, v in ta.items()}
            for k, (c, a) in tb.items():
                out.setdefault(k, [0.0, a])[0] += sg * c
            return ca + sg * cb, out
        if op == "*" and not ta:
            return ca * cb, {k: [ca * c, a] for k, (c, a) in tb.items()}
        if op == "*" and not tb:
            return ca * cb, {k: [cb * c, a] for k, (c, a) in ta.items()}
        if op == "/" and not tb and cb != 0:
            return ca / cb, {k: [c / cb, a] for k, (c, a) in ta.items()}
    return 0.0, {json.dumps(x): [1.0, x]}


def _symbolic(build, params, env, what="orders"):
    """Mesh(es) with vertices affine in the controls. build(values) -> a mesh or a list of meshes, numbers only;
    params: numbers or X. Each param is split into const + sum coef * atom (atoms: controls, or terms that are
    not affine in them); each atom is perturbed at its default and every vertex written as v0 + sum G (atom -
    atom0): exact where the mesh is affine in the params (straight runs, offsets and profiles scaled by a module),
    the tangent otherwise. Topology is taken at the defaults."""
    dec = [_affine(p) for p in params]
    atoms = {}
    for _, terms in dec:
        for k, (_, a) in terms.items():
            atoms.setdefault(k, a)
    a0 = {k: _ev(a, env) for k, a in atoms.items()}

    def values(shift=None, d=0.0):
        return [c + sum(co * (a0[k] + (d if k == shift else 0.0)) for k, (co, _) in t.items()) for c, t in dec]
    base = build(values())
    single = not isinstance(base, list)
    base = [base] if single else base
    if not atoms:
        return base[0] if single else base
    grads = {}
    for k in atoms:
        d = 1e-4 * max(abs(a0[k]), 0.01)
        alt = build(values(k, d))
        alt = [alt] if single else alt
        gm = []
        for (v0, f0), (v1, f1) in zip(base, alt):
            if len(v0) != len(v1) or f0 != f1:
                raise ValueError(f"{what}: the mesh changes its topology near the defaults; move the defaults "
                                 "away from a threshold (a run as short as its profile, a corner turning)")
            gm.append([[(a[c] - b[c]) / d for c in range(3)] for a, b in zip(v1, v0)])
        grads[k] = gm
    out = []
    for mi, (v0, f0) in enumerate(base):
        vs = []
        for n, v in enumerate(v0):
            pt = []
            for c in range(3):
                const, terms = v[c], []
                for k, a in atoms.items():
                    gk = grads[k][mi][n][c]
                    if abs(gk) > 1e-7:
                        const -= gk * a0[k]
                        terms.append((round(gk, 7), a))
                e = round(const, 7)
                for gk, a in terms:
                    t = a if gk == 1 else ["*", gk, a]
                    e = t if (_num(e) and abs(e) < 1e-9) else ["+", e, t]
                pt.append(e)
            vs.append(pt)
        out.append((vs, f0))
    return out[0] if single else out


def _num0(x, env):
    """Number at the defaults."""
    x = _v(x)
    return float(x) if _num(x) else _ev(x, env)


def _iff(c, a, b):
    c, a, b = _v(c), _v(a), _v(b)
    if _num(c):
        return X(a if c != 0 else b)
    return X(["if", c, a, b])


def _line_instance(g, id_, proto, start, step, count, **options):
    """Instance proto at start + k * step, k < count (count may be an expression, and may reach zero)."""
    cnt = _v(count)
    if _num(cnt):
        cnt = int(round(cnt))
        if cnt <= 0:
            return None
        g.n(id_ + "-at", "line", start=start, step=step, count=cnt)
        return g.n(id_, "instance", [id_ + "-at", proto], **options)
    g.n(id_ + "-at", "line", start=start, step=step, count=_xmax(count, 1))
    g.n(id_ + "-all", "instance", [id_ + "-at", proto], **options)
    return g.when(id_, _xgt(count, 0.5), id_ + "-all")


def _seg_dirs(outline, closed, env):
    """Straight runs of a plan outline: (a, b) symbolic and the unit direction t at the defaults."""
    pts = [tuple(p[:2]) for p in outline]
    segs = list(zip(pts, pts[1:] + ([pts[0]] if closed else [])))
    out = []
    for a, b in segs:
        an, bn = [_num0(c, env) for c in a], [_num0(c, env) for c in b]
        L = math.dist(an, bn)
        out.append((a, b, ((bn[0] - an[0]) / L, (bn[1] - an[1]) / L)))
    return out


def _seg_len(a, b, t):
    """Length of a run along its fixed direction t (an X when an end is one)."""
    return (X(b[0]) - a[0]) * t[0] + (X(b[1]) - a[1]) * t[1]


def _is(v, k):
    v = X(v).v
    return _num(v) and abs(v - k) < 1e-12


def _place(g, id_, part, at=None, rotate=None, scale=None, slot=None):
    if slot:
        part = g.mat(id_ + "-slot", part, slot)
    if scale is not None and not _is(scale, 1):
        part = g.xf(id_ + "-scaled", part, s=[scale, scale, scale])
    rot = None if rotate is None or _is(rotate, 0) else [0, 0, rotate]
    return g.xf(id_, part, t=list(at) if at is not None else None, r=rot)


def _as_profile(p, segments=8):
    return p if isinstance(p, Profile) else profile(p, segments)


def _run_path(outline, z, closed, ends, reach, back, up):
    """Path of a run, with return legs into the wall at the ends asked to return; end cut normals."""
    path = [_p3(p, z) for p in outline]
    ends = (ends, ends) if isinstance(ends, str) or ends is None else tuple(ends)
    cuts = [None, None]
    if closed:
        return path, cuts
    leg = max(reach, -back) * 1.02 + 1e-4
    for side in (0, 1):
        e = ends[side]
        if isinstance(e, str) and e == "return":
            p, q = (path[0], path[1]) if side == 0 else (path[-1], path[-2])
            t = _unit(_sub(q, p)) if side == 0 else _unit(_sub(p, q))
            nrm = _unit(_cross(t, up))
            r = _sub(p, _scl(nrm, leg))
            if side == 0:
                path.insert(0, r)
            else:
                path.append(r)
        elif e is not None and not (isinstance(e, str) and e == "stop"):
            cuts[side] = tuple(map(float, e))
    return path, cuts


# ------------------------------------------------------------------------------------------------ mouldings as members
def _run_mesh(prof, outline, closed, ends, z, upv, back, smooth):
    """Numeric mesh of a run (prof a Profile in metres, back None for the default)."""
    if back is None:
        back = min(p[0] for p in prof.points) - max(0.25 * prof.height, 1e-3)
    path, cuts = _run_path(outline, z, closed, ends, prof.reach, back, upv)
    eps = 0.002 * prof.height if smooth else 0.0
    poly = _crisp(prof.polygon(back), eps, closed=True) if smooth else prof.polygon(back)
    return _sweep_mesh(path, poly, upv, closed, cuts, eps)


def _flat(seq):
    return [c for p in seq for c in p]


def _unflat(vals, dims):
    it = iter(vals)
    return [tuple(next(it) for _ in range(d)) for d in dims]


def moulding_run(self, id_, prof, outline, closed=False, ends="return", z=0.0, up=(0, 0, 1), back=None,
                 smooth=True, segments=8, slot=None, at=None, rotate=None, scale=None, module=1, controls=None):
    """A moulding swept along an outline with true mitres at every corner: cornices, string courses, plinth
    courses, architraves, rails. Metres (or the profile's units).

    prof: a Profile or a list of profile() steps; x projects to the right of travel, z along `up` (default +Z:
    a level run; for an architrave round an opening pass the wall's outward normal). outline: (x, y) points at
    height z, or (x, y, z) points (raking cornices, arcs). closed: the outline is a loop (counter-clockwise for an
    outward moulding). ends (open outlines): "return" (the moulding turns into the wall at the end, as a string
    course at a building corner or a hood over a window), "stop" (cut square), a plane normal for a cut through
    the end point ((0, 0, 1) a level cut, (1, 0, 0) a plumb one), or a pair (start, end). back: x of the wall face
    behind the moulding (default a quarter of its height behind its innermost point; metres, not scaled).
    module: the profile's scale (profile units times module; number or X; the outline stays in metres). Outline
    points, z, back and module may be X (Built by Opus 5.5 (medium)): pass the module's `controls=` when an
    outline point, back or module is one (an X z alone lifts the run). `scale` scales the whole run.
    Example: g.moulding_run("course", [("fascia", 0.2, 0), ("cyma_reversa", 0.08, 0, 0.06)], [(0, 0), (6, 0)], z=3.2)"""
    prof = _as_profile(prof, segments)
    upv = _unit(_p3(up))
    if not _num(X(z).v):                      # an X height: build level at 0 and lift the run
        a = list(at) if at is not None else [0, 0, 0]
        at, z = [a[0], a[1], X(a[2]) + z], 0.0
        at[2] = at[2].v
    pts = [tuple(p) for p in outline]
    dims = [len(p) for p in pts]
    params = _flat(pts) + [module, 0.0 if back is None else back]

    def build(v):
        m, bk = v[-2], v[-1]
        return _run_mesh(prof.scaled(m), _unflat(v[:-2], dims), closed, ends, z, upv, None if back is None else bk, smooth)
    mesh = _symbolic(build, params, _envof(self, controls), id_)
    return _place(self, id_, _emit(self, id_ + "-run", mesh, smooth), at, rotate, scale, slot)


def turned(self, id_, prof, sides=32, smooth=True, segments=8, base=None, top=None, slot=None, at=None, scale=None):
    """A lathe-turned piece from a profile whose x is the radius: column bases, astragals, balusters, finials.
    Metres (or the profile's units), axis Z. The profile is closed to the axis at its bottom and top (or at the
    heights `base` / `top`, to sink the piece into what it stands on). Example:
    g.turned("base", [("torus", 0.08, 0.30), ("fillet", 0.02, 0.28)], sides=32)"""
    prof = _as_profile(prof, segments)
    pts = list(prof.points)
    zb = prof.bottom if base is None else base
    zt = prof.top if top is None else top
    meridian = [(0.0, zb)] + ([(pts[0][0], zb)] if abs(zb - pts[0][1]) > 1e-9 else []) + pts
    meridian += ([(pts[-1][0], zt)] if abs(zt - pts[-1][1]) > 1e-9 else []) + [(0.0, zt)]
    meridian = _dedupe(meridian)
    if smooth:
        meridian = _crisp(meridian, 0.002 * max(prof.height, prof.reach), angle=0.5)
    assert len(meridian) <= 512, "turned profile has more than 512 points"
    node = self.n(id_ + ("-turned" if smooth else ""), "revolve",
                  profile=[[round(max(0.0, r), 6), round(z_, 6)] for r, z_ in meridian], sides=int(sides))
    if smooth:
        node = self.n(id_, "smoothNormals", [node])
    if at is not None or scale is not None or slot:
        return _place(self, id_ + "-placed", node, at, None, scale, slot)
    return node


def _stretched(points, height):
    """Contour points with its tallest plain (vertical) face stretched so the whole is `height` high."""
    best, bi = 0.0, None
    for i, (a, b) in enumerate(zip(points, points[1:])):
        if abs(a[0] - b[0]) < 1e-9 and b[1] - a[1] > best:
            best, bi = b[1] - a[1], i
    if bi is None:
        raise ValueError("moulded_block height= needs a plain vertical face in the profile to stretch")
    extra = height - (max(p[1] for p in points) - min(p[1] for p in points))
    if best + extra <= 1e-6:
        raise ValueError(f"moulded_block: height {height:.4g} leaves no room for the profile's plain face")
    return [(x, z + (extra if i > bi else 0.0)) for i, (x, z) in enumerate(points)]


def moulded_block(self, id_, outline, prof, smooth=True, segments=8, slot=None, at=None, rotate=None, scale=None,
                  module=1, height=None, controls=None):
    """A block whose sides carry a moulding: the plan outline (counter-clockwise (x, y) points) offset outward by
    the profile's x at each height z. Abaci (square, or the Corinthian's concave one), plinths, pier caps and
    bases, sills, pedestal dies. module: the profile's scale (the outline stays in metres). height: overall
    height; the profile's tallest plain vertical face stretches to make it (a pier's shaft, a pedestal's die).
    Outline points, module and height may be X (pass `controls=`; Built by Opus 5.5 (medium)).
    Example: g.moulded_block("abacus", square(0.4), [("fascia", 0.1, 0), ("ovolo", 0.04, 0, 0.04)])"""
    prof = _as_profile(prof, segments)
    pts = [tuple(p[:2]) for p in outline]
    params = _flat(pts) + [module, 0.0 if height is None else height]

    def build(v):
        m, h = v[-2], v[-1]
        pr = prof.scaled(m)
        contour = pr.points if height is None else _stretched(pr.points, h)
        eps = 0.002 * max(pr.height, 1e-3) if smooth else 0.0
        return _offset_solid(_unflat(v[:-2], [2] * len(pts)), contour, eps)
    mesh = _symbolic(build, params, _envof(self, controls), id_)
    return _place(self, id_, _emit(self, id_ + "-block", mesh, smooth), at, rotate, scale, slot)


# ------------------------------------------------------------------------------------------------ the orders
ORDERS = {
    # parts per module; column / base / capital / entablature / architrave / frieze / cornice heights (modules);
    # top radius of the shaft (modules); flutes (count, fillet fraction of the pitch); base kind
    "tuscan":     dict(parts=12, column=14, base=1, capital=1, entablature=3.5, architrave=1, frieze=7 / 6,
                       cornice=4 / 3, top=4 / 5, flutes=(0, 0.0), base_kind="tuscan"),
    "doric":      dict(parts=12, column=16, base=1, capital=1, entablature=4, architrave=1, frieze=1.5,
                       cornice=1.5, top=5 / 6, flutes=(20, 0.0), base_kind="attic"),
    "ionic":      dict(parts=18, column=18, base=1, capital=2 / 3, entablature=4.5, architrave=1.25, frieze=1.5,
                       cornice=1.75, top=5 / 6, flutes=(24, 0.2), base_kind="attic"),
    "corinthian": dict(parts=18, column=20, base=1, capital=7 / 3, entablature=5, architrave=1.5, frieze=1.5,
                       cornice=2, top=5 / 6, flutes=(24, 0.2), base_kind="corinthian"),
    "composite":  dict(parts=18, column=20, base=1, capital=7 / 3, entablature=5, architrave=1.5, frieze=1.5,
                       cornice=2, top=5 / 6, flutes=(24, 0.2), base_kind="corinthian"),
}

# Entablature steps in parts (x from the architrave's lower face): Vignola's members in order, the smallest ones
# approximate. Tags place the dentils, modillions, mutules (corona), triglyphs (frieze, taenia) and the sima.
_ENT = {
    "tuscan": [("fascia", 10, 0, "architrave"), ("fillet", 2, 1, "architrave"),
               ("fascia", 14, 0, "frieze"),
               ("fillet", 1, 1, "cornice"), ("cyma_reversa", 3, 1, 4, "cornice"), ("corona", 5, 12, "corona"),
               ("fillet", 1, 12.5, "crown"), ("ovolo", 5, 12.5, 16, "crown"), ("fillet", 1, 16, "crown")],
    "doric": [("fascia", 4, 0, "architrave"), ("fascia", 6, 0.5, "architrave"), ("fillet", 2, 1.5, "taenia"),
              ("fascia", 16, 0, "frieze"), ("fillet", 2, 1.5, "frieze"),
              ("cyma_reversa", 2, 1.5, 3, "cornice"), ("fillet", 1, 3.5, "cornice"), ("ovolo", 3, 3.5, 6, "cornice"),
              ("corona", 5, 16, "corona"), ("cyma_reversa", 1.5, 16, 17.5, "cymatium"), ("fillet", 0.5, 17.5, None, "cymatium"),
              ("cyma_recta", 4, 17.5, 21, "crown"), ("fillet", 1, 21, "crown")],
    "ionic": [("fascia", 4.5, 0, "architrave"), ("fascia", 6, 1, "architrave"), ("fascia", 7.5, 2, "architrave"),
              ("cyma_reversa", 3, 2, 4.5, "architrave"), ("fillet", 1.5, 4.5, "architrave"),
              ("fascia", 27, 0, "frieze"),
              ("cyma_reversa", 3, 0, 3, "cornice"), ("fillet", 1, 3.5, "cornice"), ("fascia", 6, 3.5, "dentils"),
              ("fillet", 1, 8, "cornice"), ("ovolo", 4, 8, 11.5, "cornice"), ("corona", 7, 24, "corona"),
              ("cyma_reversa", 1.5, 24, 25.5, "cymatium"), ("fillet", 0.5, 25.5, None, "cymatium"),
              ("cyma_recta", 6, 25.5, 30.5, "crown"), ("fillet", 1.5, 30.5, "crown")],
    "corinthian": [("fascia", 5, 0, "architrave"), ("astragal", 1, 0, None, 0.6, "architrave"),
                   ("fascia", 7, 1, "architrave"), ("astragal", 1, 1, None, 0.6, "architrave"),
                   ("fascia", 8, 2, "architrave"), ("astragal", 1, 2, None, 0.6, "architrave"),
                   ("cyma_reversa", 3, 2, 4.5, "architrave"), ("fillet", 1, 4.5, "architrave"),
                   ("fascia", 27, 0, "frieze"),
                   ("cyma_reversa", 3, 0, 3, "cornice"), ("fillet", 1, 3.5, "cornice"), ("fascia", 6, 3.5, "dentils"),
                   ("fillet", 1, 8, "cornice"), ("ovolo", 4, 8, 11.5, "cornice"), ("fascia", 8, 11.5, "modillions"),
                   ("corona", 6, 30, "corona"), ("cyma_reversa", 1.5, 30, 31.5, "cymatium"),
                   ("fillet", 0.5, 31.5, None, "cymatium"),
                   ("cyma_recta", 4, 31.5, 35.5, "crown"), ("fillet", 1, 35.5, "crown")],
}
_ENT["composite"] = _ENT["corinthian"]


def order(name):
    """Vignola's numbers for an order (modules, module = lower radius): column, base, capital, shaft,
    entablature, architrave, frieze, cornice, top (top radius of the shaft), parts (per module), flutes."""
    o = dict(ORDERS[name])
    o["shaft"] = o["column"] - o["base"] - o["capital"]
    return o


def order_for_height(name, height):
    """An order sized to a storey (Built by Opus 5.5 (medium)): column and entablature together fill `height`
    (number or X), the entablature a quarter of the column, as Vignola without pedestal (Tuscan 17.5 modules,
    Doric 20, Ionic 22.5, Corinthian and Composite 25). Returns X values in metres: module (lower radius),
    column, base, shaft, capital, entablature, architrave, frieze, cornice, top_radius, and capital_top (the
    column's top above the storey's floor: the entablature's z). The same sizing is `height=` on column,
    pilaster and entablature. Example: o = order_for_height("ionic", C("height")); g.pilaster("p", "ionic", o["module"])"""
    o = order(name)
    m = X(height) / (o["column"] + o["entablature"])
    out = {k: m * o[k] for k in ("column", "base", "shaft", "capital", "entablature", "architrave", "frieze", "cornice")}
    out.update(module=m, top_radius=m * o["top"], capital_top=m * o["column"])
    return out


def entablature_profile(name, crown=True, segments=8):
    """The whole entablature of an order as one Profile in modules (x from the architrave's lower face, z from its
    bottom), tagged architrave, frieze, cornice, dentils, modillions, corona, cymatium, taenia, crown.
    crown=False leaves off the crowning sima (the horizontal cornice under a pediment).
    Example: entablature_profile("ionic").tags["dentils"]"""
    P = ORDERS[name]["parts"]
    steps = []
    for st in _ENT[name]:
        st = list(st)
        tag = st.pop() if len(st) > 3 and isinstance(st[-1], str) else None
        if not crown and tag == "crown":
            continue
        rest = [None if v is None else v / P for v in st[3:]]
        steps.append(tuple([st[0], st[1] / P, st[2] / P] + rest + ([tag] if tag else [])))
    return profile(steps, segments)


def _base(kind):
    """(plinth height, plinth half width, turned steps from the plinth top) of a column base: 1 module in all."""
    if kind == "tuscan":            # plinth 6, torus 5, fillet 1 (of 12)
        return 0.5, 4 / 3, [("torus", 5 / 12, 13 / 12), ("fillet", 1 / 12, 13 / 12)]
    if kind == "attic":             # plinth 1/3; lower torus 1/4; scotia and its fillets 1/4; upper torus 1/6
        return 1 / 3, 1.375, [("torus", 0.25, 1.25), ("fillet", 1 / 28, 1.21), ("scotia", 0.25 - 2 / 28, 1.21, 1.17, 0.1),
                              ("fillet", 1 / 28, 1.17), ("torus", 1 / 6, 13 / 12)]
    if kind == "corinthian":        # Vignola's Corinthian base: two scotias parted by a pair of astragals (of 18)
        s = 1 / 18
        return 6 * s, 25 * s, [("torus", 3 * s, 22.5 * s), ("fillet", 0.5 * s, 22 * s),
                               ("scotia", 1.5 * s, 22 * s, 21 * s, 1.1 * s), ("fillet", 0.5 * s, 21 * s),
                               ("astragal", 0.75 * s, 20.5 * s), ("astragal", 0.75 * s, 20.5 * s),
                               ("fillet", 0.5 * s, 20.5 * s), ("scotia", 1.5 * s, 20.5 * s, 19.8 * s, 0.9 * s),
                               ("fillet", 0.5 * s, 19.8 * s), ("torus", 2.5 * s, 19.5 * s)]
    raise ValueError("base is tuscan, attic or corinthian")


def _res(resolution, n, lo=3):
    return max(lo, int(round(n * resolution)))


def _shaft_meridian(o, z0, z1, segs):
    """Meridian (r, z) of a shaft from z0 (top of the base) to z1 (top of its astragal), modules: cincture and
    apophyge, the straight lower third, Vignola's diminution, apophyge, fillet and astragal."""
    rt = o["top"]
    fil, apo = 1 / 24, 1 / 8
    ast = 1 / 9 if o["parts"] == 18 else 1 / 12
    pts = [(13 / 12, z0), (13 / 12, z0 + fil)]
    pts += _quarter((13 / 12, z0 + fil), (1.0, z0 + fil + apo), False, segs)[1:]
    zs0 = z0 + fil + apo
    zs1 = z1 - ast - fil - 1 / 12
    z3 = z0 + (z1 - z0) / 3
    th = math.acos(rt)
    nb = _res(segs / 5, 12, 6)
    pts.append((1.0, z3))
    for i in range(1, nb + 1):
        z = z3 + (zs1 - z3) * i / nb
        pts.append((math.cos(th * (z - z3) / (zs1 - z3)), z))
    pts += _quarter((rt, zs1), (rt + fil, zs1 + 1 / 12), False, segs)[1:]
    pts.append((rt + fil, zs1 + 1 / 12 + fil))
    pts += moulding("astragal", ast, rt + fil, z0=zs1 + 1 / 12 + fil, segments=segs)
    return _dedupe(pts), zs0, zs1


def _shaft(g, id_, o, z0, z1, flutes, resolution, sides):
    segs = _res(resolution, 5)
    mer, zs0, zs1 = _shaft_meridian(o, z0, z1, segs)
    nflutes, fillet = flutes
    if not nflutes:
        return turned(g, id_, Profile(mer), sides=sides)
    # Fluted: level rings; flutes cut between zs0 + 0.12 and zs1 - 0.06, rounded off at both ends.
    alpha = TAU / nflutes
    k = _res(resolution, 6, 3)
    e = 0.04
    fr = [0.0] + ([fillet, fillet + e * (1 - fillet)] if fillet else [e]) + \
         [fillet + (1 - fillet) * j / k for j in range(1, k)] + [1 - e * (1 - fillet)]
    fr = sorted(set(round(f, 9) for f in fr))
    width = (1 - fillet) * alpha                   # flute chord, radians at r = 1
    depth = width / 2 if fillet else width * 0.28
    lend = width * 0.6
    a0, a1 = zs0 + 0.12, zs1 - 0.06
    extra = [a0 + lend * (1 - math.cos(PI / 2 * i / 4)) for i in range(5)] + \
            [a1 - lend * (1 - math.cos(PI / 2 * i / 4)) for i in range(5)]
    rows = sorted(set([round(z, 9) for _, z in mer] + [round(z, 9) for z in extra]))

    def radius(z):
        for (ra, za), (rb, zb) in zip(mer, mer[1:]):
            if za <= z <= zb and zb > za:
                return ra + (rb - ra) * (z - za) / (zb - za)
        return mer[-1][0]

    rings = []
    for z in rows:
        r = radius(z)
        fade = math.sqrt(max(0.0, 1 - (1 - min(1.0, min(z - a0, a1 - z) / lend)) ** 2)) if a0 <= z <= a1 else 0.0
        ring = []
        for i in range(nflutes):
            for f in fr:
                u = (f - fillet) / (1 - fillet)
                inset = depth * math.sqrt(max(0.0, 1 - (2 * u - 1) ** 2)) * fade if 0 <= u <= 1 else 0.0
                a = alpha * (i + f)
                ring.append(((r - inset) * math.cos(a), (r - inset) * math.sin(a), z))
        rings.append(ring)
    return _emit(g, id_, _stack(rings))


def _square_block(g, id_, half, z0, z1, top_steps=None, half_y=None, centre=(0.0, 0.0)):
    """A rectangular block (plinth or abacus) from z0 to z1, with optional crowning steps (x as offset)."""
    zt = z1 - (sum(s[1] for s in top_steps) if top_steps else 0.0)
    contour = [(0.0, z0), (0.0, zt)]
    if top_steps:
        contour += profile(top_steps, 6, zt).points[1:]
    out = [(x + centre[0], y + centre[1]) for x, y in square(half, half_y)]
    return moulded_block(g, id_, out, Profile(contour))


def _eggs(g, id_, size, count, radius, z, lean=0.0):
    """A ring of eggs (the eggs of an egg-and-dart) round an echinus."""
    prof = [(size * 0.5 * math.sin(PI * i / 8), -size * 0.8 * math.cos(PI * i / 8)) for i in range(9)]
    prof[0], prof[-1] = (0.0, prof[0][1]), (0.0, prof[-1][1])
    g.n(id_ + "-stock", "revolve", profile=[[round(a, 6), round(b, 6)] for a, b in prof], sides=8)
    g.n(id_ + "-smooth", "smoothNormals", [id_ + "-stock"])
    g.n(id_ + "-flat", "transform", [id_ + "-smooth"], scale=[0.55, 1, 1])
    g.xf(id_ + "-one", id_ + "-flat", t=[radius, 0, z], r=[0, lean, 0])
    return g.ring(id_, id_ + "-one", count, TAU / count, PI / count)


def _logspiral(cx, cz, r0, turns, r1, phi0, cw, n):
    """Points of a logarithmic spiral round (cx, cz) from radius r0 at angle phi0 in to r1 after `turns`."""
    q = (r0 / r1) ** (1 / turns)
    m = max(4, int(math.ceil(turns * n)))
    sg = -1 if cw else 1
    return [(cx + r0 * q ** (-turns * i / m) * math.cos(phi0 + sg * TAU * turns * i / m),
             cz + r0 * q ** (-turns * i / m) * math.sin(phi0 + sg * TAU * turns * i / m)) for i in range(m + 1)]


def _band_mesh(pts2, to3, width, depth, normal_out):
    """A rectangular-section band along a plane curve: width in the plane (number or (start, end) taper), depth
    out of it along normal_out (centred)."""
    w0, w1 = (width, width) if _num(width) else width
    n = len(pts2)
    rings = []
    for i, p in enumerate(pts2):
        a, c = pts2[max(0, i - 1)], pts2[min(n - 1, i + 1)]
        t = (c[0] - a[0], c[1] - a[1])
        lt = math.hypot(*t)
        nr = (-t[1] / lt, t[0] / lt)
        w = (w0 + (w1 - w0) * i / (n - 1)) / 2
        ring = []
        for su, sd in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
            q = to3((p[0] + su * w * nr[0], p[1] + su * w * nr[1]))
            ring.append(_add(q, _scl(normal_out, sd * depth / 2)))
        rings.append(ring)
    return _stack(rings)


def _ionic_cushion(zc, front, back, res, faces=(True, True)):
    """The Ionic pulvinus and its two volutes, modules: the outline in x-z (top under the abacus, the volutes'
    outer 300 degrees, the canalis' lower edge) swept from y = front to y = back and waisted in the middle like a
    bolster with a belt. Volutes: log spirals of 3 turns, 16 parts high, eyes 2 parts across on the line of the
    astragal, catheti 0.9 module off the axis (Vignola's construction, simplified). Returns (cushion, listels)."""
    xe, R0, r_eye, turns = 0.9, 8 / 18, 1 / 18, 3.0
    z_ab = zc + 0.5
    ze = z_ab - R0
    n = _res(res, 28, 10)
    right = _logspiral(xe, ze, R0, turns, r_eye, PI / 2, True, n)      # clockwise from the top, seen from -y
    outer = right[:int(len(right) / turns * (300 / 360)) + 1]

    def outline(s):
        rs = [(xe + (x - xe) * s, ze + (z - ze) * s) for x, z in outer]
        ls = [(-x, z) for x, z in rs]
        pts = [rs[0], (0.4 * xe, z_ab), (-0.4 * xe, z_ab)] + ls
        a, b = ls[-1], rs[-1]
        for i in range(1, 6):
            t = i / 6
            pts.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t - 0.03 * math.sin(PI * t)))
        pts += rs[::-1][:-1]
        return _dedupe(pts, closed=True)

    span = back - front
    ys = [front + span * f for f in (0, 0.02 / span, 0.25, 0.42, 0.5, 0.58, 0.75, 1 - 0.02 / span, 1)]

    def waist(y):
        u = abs((y - front) / span - 0.5) * 2
        return 1 - 0.24 * (1 - u ** 2) + (0.05 if u < 0.2 else 0.0)
    base = outline(1.0)
    assert _area2(base) > 0, "Ionic cushion outline must run counter-clockwise"
    rings = [[(x, y, z) for x, z in outline(waist(y))] for y in ys]
    K = len(base)
    verts, idx = [], []
    for r in rings:
        idx.append(list(range(len(verts), len(verts) + K)))
        verts.extend(r)
    fcs = []
    for A, Bv in zip(idx, idx[1:]):
        for k in range(K):
            k1 = (k + 1) % K
            fcs.append([A[k], A[k1], Bv[k1], Bv[k]])
    tris = _earclip(base)
    fcs += [[idx[0][c], idx[0][b], idx[0][a]] for a, b, c in tris]
    fcs += [[idx[-1][a], idx[-1][b], idx[-1][c]] for a, b, c in tris]
    cushion = (verts, _fix_orientation(verts, fcs))
    listels = []
    inner = right[:int(len(right) * 0.97)]
    for (y, sgn), want in zip(((front, -1), (back, 1)), faces):
        if not want:
            continue
        for mirror in (1, -1):
            pts = [(mirror * x, z) for x, z in inner]
            listels.append(_band_mesh(pts, lambda p, y=y: (p[0], y, p[1]), (0.05, 0.022), 0.05, (0, 1, 0)))
            ex = mirror * xe
            eye = [(ex + r_eye * math.cos(TAU * i / 12), ze + r_eye * math.sin(TAU * i / 12)) for i in range(12)]
            listels.append(_map(_prism(eye, -0.03, 0.035), lambda v, y=y, s=sgn: (v[0], y + s * v[2], v[1])))
    return cushion, listels


def _leaf(height, width, wrap, res, lobes=3, flop=1.0, thick=0.03):
    """An acanthus leaf, modules: a cupped, lobed sheet with a raised rib rising from its base at the origin
    (outward +x, across +y, wrapped round an axis `wrap` behind it) that flops forward and down at the tip."""
    nv, nu = _res(res, 14, 6), _res(res, 9, 5) | 1
    pts, x, z = [(0.0, 0.0, PI / 2)], 0.0, 0.0
    for i in range(1, nv + 1):
        v = i / nv
        a = math.radians(86 - 118 * flop * v ** 3)
        x += math.cos(a) / nv
        z += math.sin(a) / nv
        pts.append((x, z, a))
    k = height / max(p[1] for p in pts)
    rings = []
    for i, (sx, sz, a) in enumerate(pts):
        v = i / nv
        sx, sz = sx * k, sz * k
        w = width * (0.24 + 0.76 * math.sin(PI * min(v, 0.98)) ** 0.75)
        w *= 1 - 0.3 * (0.5 - 0.5 * math.cos(TAU * lobes * v)) ** 2 * (0.3 + 0.7 * v)
        nx, nz = math.sin(a), -math.cos(a)
        t = thick * (1 - 0.5 * v)
        front, backs = [], []
        for j in range(nu):
            u = -1 + 2 * j / (nu - 1)
            ang = u * w / 2 / wrap
            out = wrap * (math.cos(ang) - 1) + 0.16 * w * u * u * (0.4 + v) + 0.025 * math.exp(-(u / 0.18) ** 2)
            front.append((sx + nx * out, wrap * math.sin(ang), sz + nz * out))
            backs.append((sx + nx * (out - t), wrap * math.sin(ang), sz + nz * (out - t)))
        rings.append(front + backs[::-1])
    return _stack(rings)


def _bell_radius(zrel, rt, top_r, h):
    return rt + (top_r - rt) * (max(0.0, zrel) / h) ** 2


def _corinthian_set(zc, res, composite=False, face=None):
    """Scrolls of one face of a Corinthian (or Composite) capital, face normal +x, modules: from each of two
    caulicoli a volute to the corner under the abacus horn and a helix towards the face centre (Composite: one
    large volute each). face: x of a flat face (pilaster) instead of the round bell."""
    meshes = []
    n = _res(res, 16, 8)
    for side in (1, -1):
        if face is None:
            rb = _bell_radius(1.15, 5 / 6, 0.97, 2.0) - 0.04
            A = (rb * math.cos(math.radians(26)), side * rb * math.sin(math.radians(26)))
            corner, helix = (1.12, side * 1.12), (1.0, side * 0.16)
        else:
            A = (face - 0.03, side * 0.42)
            corner, helix = (face + 0.32, side * 1.1), (face + 0.14, side * 0.16)
        za = zc + 1.15
        specs = [(corner, 0.34 if composite else 0.22, 2.25 if composite else 1.75, zc + 1.97, 0.06, 0.075)]
        if not composite:
            specs.append((helix, 0.11, 1.5, zc + 1.95, 0.045, 0.055))
        for target, r0, turns, ztop, bw, bd in specs:
            d = (target[0] - A[0], target[1] - A[1])
            sE = math.hypot(*d)
            d = (d[0] / sE, d[1] / sE)
            q = (r0 / 0.04) ** (1 / turns)
            zE = ztop - r0 * q ** -0.25 - bw / 2
            spiral = _logspiral(sE, zE, r0, turns, 0.04, PI, True, n)
            S0 = spiral[0]
            L = math.hypot(S0[0], S0[1] - za)
            P1, P2 = (0.18 * L, za + 0.45 * L), (S0[0], S0[1] - 0.4 * L)
            stalk = []
            for i in range(8):
                t = i / 8
                b = ((1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3)
                stalk.append((b[1] * P1[0] + b[2] * P2[0] + b[3] * S0[0], b[0] * za + b[1] * P1[1] + b[2] * P2[1] + b[3] * S0[1]))
            pts = _dedupe(stalk + spiral[:int(len(spiral) * 0.96)])
            meshes.append(_band_mesh(pts, lambda p, A=A, d=d: (A[0] + p[0] * d[0], A[1] + p[0] * d[1], p[1]),
                                     (bw * 1.3, bw * 0.55), bd, (-d[1], d[0], 0.0)))
    return meshes


def _concave_abacus(a=1.42, sag=0.27, chamfer=0.12, n=8):
    """Plan of the Corinthian abacus: four concave sides and cut-off horns, counter-clockwise, modules."""
    pts = []
    for k in range(4):
        c, s = math.cos(k * PI / 2), math.sin(k * PI / 2)
        for i in range(n + 1):
            t = -1 + 2 * i / n
            x, y = a - sag * (1 - t * t), t * (a - chamfer)
            pts.append((x * c - y * s, x * s + y * c))
    return _dedupe(pts, closed=True)


_ABACUS_CORINTHIAN = [("fascia", 2.5 / 18, -0.08), ("fillet", 0.5 / 18, -0.05), ("ovolo", 2.5 / 18, -0.05, 0.0),
                      ("fillet", 0.5 / 18, 0.0)]
_ABACUS_IONIC = [("fascia", 1 / 18, -0.05), ("ovolo", 1.5 / 18, -0.05, 0.03), ("fillet", 0.5 / 18, 0.03)]


def _capital(g, id_, name, o, zc, res, sides):
    """The capital of an order over a shaft whose astragal tops out at zc (modules). Returns part ids."""
    rt = o["top"]
    segs = _res(res, 5)
    parts = []
    if name in ("tuscan", "doric"):
        if name == "tuscan":         # neck 4, fillet 1, ovolo 3, abacus 4 (of 12)
            steps = [("fascia", 1 / 3, rt), ("fillet", 1 / 12, rt + 1 / 24), ("ovolo", 1 / 4, rt + 1 / 24, 1.2)]
            ab_half, ab_steps = 1.25, None
        else:                        # neck 4, three annulets 1, ovolo 3, abacus 3 with its cyma and fillet 1
            steps = [("fascia", 1 / 3, rt), ("fillet", 1 / 36, rt + 0.025), ("fillet", 1 / 36, rt + 0.045),
                     ("fillet", 1 / 36, rt + 0.065), ("ovolo", 1 / 4, rt + 0.065, 1.29)]
            ab_half, ab_steps = 4 / 3, [("cyma_reversa", 0.75 / 12, 0, 1 / 12), ("fillet", 0.25 / 12, 1 / 12)]
        prof = profile(steps, segs, zc)
        z_ab = prof.top
        parts.append(turned(g, id_ + "-echinus", prof, sides=sides, base=zc - 0.03, top=z_ab + 0.03))
        parts.append(_square_block(g, id_ + "-abacus", ab_half, z_ab, zc + 1.0, ab_steps))
        return parts
    if name == "ionic":
        prof = profile([("ovolo", 5 / 18, rt + 0.02, 1.03)], segs, zc)
        parts.append(turned(g, id_ + "-echinus", Profile(prof.points + [(0.88, prof.top), (0.88, zc + 0.53)]),
                            sides=sides, base=zc - 0.03))
        parts.append(_eggs(g, id_ + "-eggs", 0.16, 16, 1.0, zc + 0.12, lean=-0.35))
        cushion, listels = _ionic_cushion(zc, -1.05, 1.05, res)
        parts.append(_emit(g, id_ + "-cushion", cushion))
        parts.append(_emit(g, id_ + "-listels", _merge(*listels)))
        parts.append(moulded_block(g, id_ + "-abacus", square(1.17, 1.07), profile(_ABACUS_IONIC, segs, zc + 0.5)))
        return parts
    # Corinthian and Composite: bell, two rows of eight leaves, scrolls, the concave abacus and its fleurons.
    composite = name == "composite"
    hb = 2.0
    bell = [(_bell_radius(hb * i / 8, rt, 0.97, hb), zc + hb * i / 8) for i in range(9)]
    parts.append(turned(g, id_ + "-bell", Profile(bell + [(1.02, zc + hb - 0.04), (1.02, zc + hb), (0.9, zc + hb + 0.03)]),
                        sides=sides, base=zc - 0.03))
    for row, (h, wdt, start, z0, flop) in enumerate(((0.8, 0.7, PI / 8, 0.0, 0.55), (1.4, 0.66, 0.0, 0.04, 0.7))):
        leaf = _emit(g, f"{id_}-leaf{row}", _leaf(h, wdt, 0.95 + 0.1 * row, res, flop=flop))
        rb = _bell_radius(z0, rt, 0.97, hb) - 0.03 + 0.02 * row
        g.xf(f"{id_}-leaf{row}-at", leaf, t=[rb, 0, zc + z0])
        parts.append(g.ring(f"{id_}-leaves{row}", f"{id_}-leaf{row}-at", 8, PI / 4, start))
    if composite:
        prof = profile([("ovolo", 0.22, 0.9, 1.08)], segs, zc + 1.45)
        parts.append(turned(g, id_ + "-echinus", Profile(prof.points + [(1.0, zc + hb + 0.02)]), sides=sides, base=zc + 1.4))
        parts.append(_eggs(g, id_ + "-eggs", 0.15, 20, 1.04, zc + 1.56, lean=-0.4))
    scroll = _emit(g, id_ + "-scroll", _merge(*_corinthian_set(zc, res, composite)))
    parts.append(g.ring(id_ + "-scrolls", scroll, 4, PI / 2))
    parts.append(moulded_block(g, id_ + "-abacus", _concave_abacus(), profile(_ABACUS_CORINTHIAN, segs, zc + hb)))
    flower = turned(g, id_ + "-fleuron", Profile([(0.13, 0.0), (0.12, 0.03), (0.08, 0.07), (0.04, 0.09), (0.035, 0.12)]),
                    sides=10, base=-0.01)
    g.xf(id_ + "-fleuron-at", flower, t=[1.42 - 0.27 - 0.06, 0, zc + hb + 1 / 6], r=[0, PI / 2, 0])
    parts.append(g.ring(id_ + "-fleurons", id_ + "-fleuron-at", 4, PI / 2))
    return parts


def column(self, id_, name="doric", module=0.25, at=None, rotate=None, flutes=None, base=None, resolution=1.0,
           sides=None, slot=None, height=None):
    """A column of an order by Vignola's rule: plinth and base, shaft with entasis, astragal and capital.
    height (number or X): size the order to a storey instead of `module` (order_for_height: column plus
    entablature fill it; the column itself is 4/5 of the height).

    Frame: axis Z through `at` (default the origin), foot of the plinth at z = 0; the Ionic volutes face -y and +y
    (turn with `rotate`, radians about Z). module: the LOWER RADIUS of the shaft in metres (number or X); the
    column is ORDERS[name]["column"] modules high (Tuscan 14, Doric 16, Ionic 18, Corinthian and Composite 20).
    name: tuscan, doric, ionic, corinthian, composite. base: "tuscan", "attic" or "corinthian" (default: Tuscan
    for the Tuscan, Attic for Doric and Ionic, Vignola's double-scotia base for Corinthian and Composite).
    flutes: None plain; True the order's own (Doric 20 with arrises, the others 24 with fillets; Tuscan 20); or
    (count, fillet fraction). resolution scales every subdivision (0.5 for a distant colonnade, 1.5 close up):
    a Corinthian capital is heavy (see the triangle table in the tests); sides: lathe sides (default 40 x res).
    Example: g.column("col", "corinthian", C("module"), flutes=True)"""
    if height is not None:
        module = order_for_height(name, height)["module"]
    o = order(name)
    res = float(resolution)
    sides = int(sides or _res(res, 32, 12))
    ph, half, steps = _base(base or o["base_kind"])
    parts = [_square_block(self, id_ + "-plinth", half, 0.0, ph)]
    bprof = profile(steps, _res(res, 5), ph)
    parts.append(turned(self, id_ + "-base", bprof, sides=sides, base=ph - 0.03, top=bprof.top + 0.02))
    z1 = o["base"] + o["shaft"]
    if flutes is True:
        flutes = o["flutes"] if o["flutes"][0] else (20, 0.0)
    parts.append(_shaft(self, id_ + "-shaft", o, o["base"], z1, flutes or (0, 0.0), res, sides))
    parts += _capital(self, id_ + "-cap", name, o, z1, res, sides)
    return _place(self, id_, self.join(id_ + "-parts", *parts), at, rotate, module, slot)


def pilaster(self, id_, name="doric", module=0.25, at=None, facing="-y", projection=0.5, width=2.0, flutes=None,
             resolution=1.0, slot=None, height=None):
    """A pilaster of an order: an engaged rectangular pier with the column's base, height and capital.
    height (number or X): size it to a storey instead of `module` (order_for_height); an entablature with the
    same height= and z left out sits on its capital at every control value.

    Frame: `at` is the foot on the wall face, under the pilaster's centre line; `facing` the wall's outward
    normal. module: lower radius of the matching column (number or X). width and projection in modules (default
    2 wide, no diminution, as Vignola's pilasters, projecting half a module). flutes: None, True (7 parted by
    fillets on the face) or (count, fillet fraction). The capital is the column's laid onto the face: Tuscan and
    Doric mouldings return round the sides; Ionic volutes on the face; Corinthian leaves, volutes and helices on
    the face and at both corners. Example: g.pilaster("pil", "ionic", 0.22, at=[2, 0, 0], facing="-y")"""
    if height is not None:
        module = order_for_height(name, height)["module"]
    o = order(name)
    res = float(resolution)
    segs = _res(res, 8)
    hw, p, e = width / 2, projection, 0.15
    U = [(-hw, e), (-hw, -p), (hw, -p), (hw, e)]

    def run(nid, prof_, dx=0.0):
        return moulding_run(self, nid, prof_.shifted(dx=dx), U, ends="stop", back=-0.25)
    ph, half, steps = _base(o["base_kind"])
    out = half - 1
    parts = [moulded_block(self, id_ + "-plinth", [(-hw - out, -p - out), (hw + out, -p - out), (hw + out, e), (-hw - out, e)],
                           Profile([(0, 0), (0, ph)]))]
    parts.append(run(id_ + "-base", profile(steps, segs, ph), -1.0))
    z0, z1 = o["base"], o["base"] + o["shaft"]
    nfl, fil = (7, 0.25) if flutes is True else (flutes or (0, 0.0))
    zrows = [z0 - 0.02, z1 + 0.02]
    k = _res(res, 6, 3)
    if nfl:
        pitch = 2 * hw / (nfl + fil)
        fw = pitch * (1 - fil)
        lend = fw * 0.7
        a0, a1 = z0 + 0.35, z1 - 0.3
        zrows = sorted(set(zrows + [a0, a1] + [a0 + lend * (1 - math.cos(PI / 2 * i / 4)) for i in range(1, 5)]
                           + [a1 - lend * (1 - math.cos(PI / 2 * i / 4)) for i in range(1, 5)]))
    rings = []
    for z in zrows:
        front = [(-hw, -p)]
        if nfl:
            fade = math.sqrt(max(0.0, 1 - (1 - min(1.0, min(z - a0, a1 - z) / lend)) ** 2)) if a0 <= z <= a1 else 0.0
            for i in range(nfl):
                xa = -hw + pitch * (i + fil)
                for j in range(k + 1):
                    u = j / k
                    front.append((xa + fw * u, -p + fw / 2 * math.sqrt(max(0.0, 1 - (2 * u - 1) ** 2)) * fade))
        front.append((hw, -p))
        rings.append([(x, y, z) for x, y in _dedupe([(-hw, e)] + front + [(hw, e)], closed=True)])
    parts.append(_emit(self, id_ + "-shaft", _stack(rings, fan=False), smooth=False))
    zc = z1
    if name in ("tuscan", "doric"):
        if name == "tuscan":
            steps = [("fascia", 1 / 3, 0), ("fillet", 1 / 12, 1 / 24), ("ovolo", 1 / 4, 1 / 24, 0.4)]
            ab, abs_ = 0.45, None
        else:
            steps = [("fascia", 1 / 3, 0), ("fillet", 1 / 36, 0.025), ("fillet", 1 / 36, 0.045), ("fillet", 1 / 36, 0.065),
                     ("ovolo", 1 / 4, 0.065, 0.45)]
            ab, abs_ = 0.5, [("cyma_reversa", 0.75 / 12, 0, 1 / 12), ("fillet", 0.25 / 12, 1 / 12)]
        cp = profile([("astragal", 1 / 12, 0.0)] + steps, segs, zc - 1 / 12)
        parts.append(run(id_ + "-echinus", cp))
        zt = zc + 1.0 - (sum(s[1] for s in abs_) if abs_ else 0.0)
        contour = [(0.0, cp.top), (0.0, zt)] + (profile(abs_, 6, zt).points[1:] if abs_ else [])
        parts.append(moulded_block(self, id_ + "-abacus", [(-hw - ab, -p - ab), (hw + ab, -p - ab), (hw + ab, e), (-hw - ab, e)],
                                   Profile(contour)))
    elif name == "ionic":
        parts.append(run(id_ + "-echinus", profile([("astragal", 1 / 9, 0.0), ("ovolo", 5 / 18, 0.04, 0.2)], segs, zc - 1 / 9)))
        cushion, listels = _ionic_cushion(zc, -p - 0.2, -p + 0.25, res, faces=(True, False))
        parts.append(_emit(self, id_ + "-cushion", cushion))
        parts.append(_emit(self, id_ + "-listels", _merge(*listels)))
        parts.append(moulded_block(self, id_ + "-abacus", [(-1.2, -p - 0.27), (1.2, -p - 0.27), (1.2, e), (-1.2, e)],
                                   profile(_ABACUS_IONIC, segs, zc + 0.5)))
    else:
        hb = 2.0
        parts.append(run(id_ + "-bell", profile([("astragal", 1 / 9, 0.0), ("fascia", hb - 0.04, 0.0, 0.14),
                                                 ("fillet", 0.04, 0.18)], segs, zc - 1 / 9)))
        for row, (h, w, xs, flop) in enumerate(((0.8, 0.7, (-0.55, 0.55), 0.55), (1.4, 0.66, (0.0,), 0.7))):
            leaf = _emit(self, f"{id_}-leaf{row}", _leaf(h, w, 4.0, res, flop=flop))
            ls = [self.xf(f"{id_}-leaf{row}-{i}", leaf, t=[x, -p + 0.02, zc + 0.04 * row], r=[0, 0, -PI / 2])
                  for i, x in enumerate(xs)]
            ls += [self.xf(f"{id_}-leaf{row}-c{i}", leaf, t=[sx * (hw - 0.05), -p + 0.05, zc + 0.04 * row],
                           r=[0, 0, -PI / 2 + sx * PI / 4]) for i, sx in enumerate((-1, 1))]
            parts.append(self.join(f"{id_}-leaves{row}", *ls))
        scrolls = [_map(m, lambda v: (v[1], -v[0], v[2])) for m in _corinthian_set(zc, res, name == "composite", face=p)]
        parts.append(_emit(self, id_ + "-scrolls", _merge(*scrolls)))
        parts.append(moulded_block(self, id_ + "-abacus", [(-1.35, -p - 0.4), (1.35, -p - 0.4), (1.35, e), (-1.35, e)],
                                   profile(_ABACUS_CORINTHIAN, segs, zc + hb)))
    body = self.join(id_ + "-parts", *parts)
    return _place(self, id_, body, at, (X(_facing(facing)) + PI / 2).v, module, slot)


# ---------- entablature
def _repeat(g, id_, proto, outline, closed, z, pitch, inset, width, env):
    """Instance a prototype (built with u along +x, its front towards -y) along each straight run of an outline:
    evenly spaced near `pitch`, the first and last `inset` from the ends, one in the middle of a run too short
    for two, none on a run shorter than `width`. Lengths may be X (Built by Opus 5.5 (medium)): the count is then
    an expression of the run's length (`line` count), so blocks keep their size and spacing at every control."""
    nodes = []
    for i, (a, b, t) in enumerate(_seg_dirs(outline, closed, env)):
        L = _seg_len(a, b, t)
        span = L - X(inset) * 2
        n = _iff(_xgt(span, 1e-9), _xmax(_xrnd(span / pitch), 1) + 1, _iff(_xgt(L, width), 1, 0))
        step = span / _xmax(n - 1, 1)
        s0 = L / 2 - (n - 1) * step / 2
        nd = _line_instance(g, f"{id_}-{i}", proto, [X(a[0]) + s0 * t[0], X(a[1]) + s0 * t[1], z],
                            [step * t[0], step * t[1], 0], n, rotation=[0, 0, math.atan2(t[1], t[0])])
        if nd:
            nodes.append(nd)
    return nodes


def _block(u0, u1, v0, v1, z0, z1):
    """A box from u0..u1 along the run, v0..v1 out from the face (towards -y), z0..z1."""
    return _prism([(u0, -v0), (u1, -v0), (u1, -v1), (u0, -v1)], z0, z1)


def _cone(cx, cy, z0, z1, r0, r1, sides=8):
    return _stack([[(cx + r * math.cos(TAU * i / sides), cy + r * math.sin(TAU * i / sides), zz) for i in range(sides)]
                   for r, zz in ((r0, z0), (r1, z1))])


def entablature(self, id_, name="doric", module=0.25, outline=((0, 0), (4, 0)), closed=False, ends="return", z=None,
                depth=None, crown=True, blocks=True, slot=None, height=None, controls=None):
    """The entablature of an order along an outline: architrave (fasciae), frieze (plain, or Doric triglyphs and
    metopes) and cornice (Doric mutules, Ionic dentils, Corinthian dentils and modillions). Metres.

    outline: plan points of the architrave's lower face (straight runs, mitred at the corners; over a colonnade on
    the axis line y = 0 that is the line y = -top radius; over pilasters the line y = -projection, half a module);
    the mouldings project to the right of travel, so a run along +x faces -y. z: top of the capitals (default 0).
    closed: a loop round a building (counter-clockwise). ends: "return" (into the wall), "stop" or a cut normal,
    as moulding_run. depth: thickness behind the architrave face (default the column's top diameter).
    module, outline points, z and depth may be X (Built by Opus 5.5 (medium)); pass the module's `controls=` when
    an outline point or the module is one. height (number or X): size the order to a storey instead of `module`
    (order_for_height); z then defaults to the column's top (the storey's floor at z = 0), so pilasters or columns
    with the same height= carry it at every control value.
    Blocks are instanced along each run with a count that follows the run's length (an expression when the
    length is one): triglyphs at 2.5 modules with the end ones over a column axis one top radius in, regulae and
    guttae under them, a mutule with 18 guttae over each; dentils at 1/3 module (4 parts wide, 2 apart); modillions
    at 4/3 modules. The blocks are scaled by the module, never stretched. crown=False leaves off the sima (under a
    pediment); blocks=False: mouldings only.
    Example: g.entablature("ent", "ionic", 0.25, [(0, 0), (8.0, 0)], z=4.5)
             g.entablature("ent", "ionic", outline=[(0, -0.5 * m), (C("pitch"), -0.5 * m)], height=C("height"), controls=CONTROLS)"""
    o = order(name)
    if height is not None:
        module = order_for_height(name, height)["module"]
        if z is None:
            z = X(module) * o["column"]
    z = 0.0 if z is None else _v(z)
    m = X(module)
    P = o["parts"]
    prof = entablature_profile(name, crown, 8)                   # modules
    back = -(m * (2 * o["top"])) if depth is None else -X(depth)
    parts = [moulding_run(self, id_ + "-mouldings", prof, outline, closed, ends, z, back=back.v, module=m.v,
                          controls=controls)]
    if blocks:
        env = _envof(self, controls)
        u = 1.0 / P
        tg = prof.tags

        def scaled(nid, proto):
            return self.xf(nid + "-m", proto, s=[m.v] * 3)
        if name == "doric":
            fz0, tz0, cz0 = tg["frieze"][0], tg["taenia"][0], tg["corona"][0]
            glyphs = [(-6, 0.2), (-5, 1), (-3, 1), (-2, 0.2), (-1, 1), (1, 1), (2, 0.2), (3, 1), (5, 1), (6, 0.2), (6, -0.4), (-6, -0.4)]
            pieces = [_prism([(a * u, -b * u) for a, b in glyphs], fz0 - 0.2 * u, fz0 + 16.2 * u)]
            pieces.append(_block(-6 * u, 6 * u, -0.3 * u, 1.5 * u, tz0 - 1 * u, tz0 + 0.3 * u))           # regula
            pieces += [_cone((-5 + 2 * i) * u, -0.9 * u, tz0 - 2 * u, tz0 - 0.9 * u, 0.38 * u, 0.5 * u) for i in range(6)]
            pieces.append(_block(-6 * u, 6 * u, 5.8 * u, 15.5 * u, cz0 - 1.5 * u, cz0 + 0.3 * u))        # mutule
            pieces += [_cone((-5 + 2 * i) * u, -(8 + 3 * j) * u, cz0 - 2.4 * u, cz0 - 1.4 * u, 0.4 * u, 0.5 * u)
                       for i in range(6) for j in range(3)]
            proto = scaled(id_ + "-triglyph", _emit(self, id_ + "-triglyph", _merge(*pieces), smooth=False))
            parts += _repeat(self, id_ + "-triglyphs", proto, outline, closed, z, m * 2.5, m * o["top"], m, env)
        if "dentils" in tg:
            dz0, dz1, dx0, _ = tg["dentils"]
            proto = scaled(id_ + "-dentil", _emit(self, id_ + "-dentil", _block(-2 * u, 2 * u, dx0 - 0.3 * u, dx0 + 4 * u, dz0, dz1 + 0.2 * u),
                                                  smooth=False))
            parts += _repeat(self, id_ + "-dentils", proto, outline, closed, z, m * (6 * u), m * (dx0 + 6 * u), m * (4 * u), env)
        if "modillions" in tg:
            mz0, mz1, mx0, _ = tg["modillions"]
            L = tg["corona"][2] - 0.5 * u - mx0
            h = mz1 - mz0
            side = [(-0.3 * u, h + 0.25 * u), (-0.3 * u, 0.275 * h)]
            side += [(0.12 * L + 0.11 * L * math.cos(PI + PI * i / 6), 0.275 * h + 0.25 * h * math.sin(PI + PI * i / 6))
                     for i in range(1, 7)]                                       # the large curl at the back
            side += [(0.45 * L, 0.42 * h), (0.75 * L, 0.5 * h)]
            side += [(0.92 * L + 0.08 * L * math.cos(-PI / 2 + PI * i / 5), 0.62 * h + 0.13 * h * math.sin(-PI / 2 + PI * i / 5))
                     for i in range(6)]                                          # the small curl at the front
            side += [(L, h + 0.25 * u)]
            mesh = _map(_prism(_dedupe(side, closed=True), -4.5 * u, 4.5 * u), lambda q: (q[2], -(mx0 + q[0]), mz0 + q[1]))
            proto = scaled(id_ + "-modillion", _emit(self, id_ + "-modillion", mesh))
            parts += _repeat(self, id_ + "-modillions", proto, outline, closed, z, m * (4 / 3), m * (mx0 + L + 4.5 * u), m * (9 * u), env)
    return _place(self, id_, self.join(id_ + "-parts", *parts), None, None, None, slot)


# ---------- pediment
def _pediment_meshes(S, prof, flat, kind, R, depth):
    """Numeric meshes (horizontal cornice, raking cornice, tympanum) of a pediment facing -y, the frieze top's
    centre at the origin, metres."""
    upv = (0.0, 0.0, 1.0)
    level = _run_mesh(flat, [(-S / 2, 0), (S / 2, 0)], False, "return", 0.0, upv, -depth, True)
    zt, reach = flat.top, prof.reach
    if kind == "triangular":
        slope = R / (S / 2)
        xe = S / 2 + reach
        path = [(-xe, 0, zt - slope * reach), (0, 0, zt + R), (xe, 0, zt - slope * reach)]
        tymp = [(-S / 2, zt - 0.02 * R), (S / 2, zt - 0.02 * R), (0, zt + R * 0.98)]
    else:
        rad = ((S / 2) ** 2 + R * R) / (2 * R)
        cz = zt + R - rad
        a_end = math.asin(min(0.95, (S / 2 + reach) / rad))
        path = [(rad * math.sin(-a_end + 2 * a_end * i / 24), 0, cz + rad * math.cos(-a_end + 2 * a_end * i / 24)) for i in range(25)]
        a_s = math.asin(S / 2 / rad)
        tymp = [(-S / 2, zt - 0.02 * R), (S / 2, zt - 0.02 * R)] + \
               [(rad * math.sin(a_s - 2 * a_s * i / 16), cz + rad * math.cos(a_s - 2 * a_s * i / 16) - 0.02 * R) for i in range(1, 16)]
    raking = _run_mesh(prof, path, False, ((1, 0, 0), (1, 0, 0)), 0.0, upv, -depth, True)
    tympanum = _map(_prism(tymp, -0.01, depth * 0.9), lambda v: (v[0], v[2], v[1]))
    return [level, raking, tympanum]


def _pediment_profiles(name, prof):
    """(raking profile, horizontal profile) in modules (name) or the given profile's units."""
    if prof is None:
        ent = entablature_profile(name, True, 8)
        c0 = ent.tags["cornice"][0]
        rk = Profile([(x, z_ - c0) for x, z_ in ent.points if z_ >= c0 - 1e-9])
        flat = Profile([(x, z_ - c0) for x, z_ in entablature_profile(name, False, 8).points if z_ >= c0 - 1e-9])
        return rk, flat
    return prof, Profile([p for p in prof.points if p[1] <= prof.bottom + 0.75 * prof.height + 1e-9])


def pediment(self, id_, span, name="ionic", module=0.25, prof=None, kind="triangular", rise=None, at=None,
             facing="-y", depth=None, slot=None, controls=None):
    """A pediment over an entablature: the horizontal cornice (without its sima), raking cornices mitred at the
    apex (or one segmental arc) cut plumb at the eaves, and the tympanum in the plane of the frieze. Metres.

    Frame: `at` is the centre of the frieze top (where the horizontal cornice starts) on the facade face line;
    `facing` the facade's outward normal. span: length of the horizontal cornice's face. prof: a cornice Profile
    (x from the frieze face, z up from the frieze top, metres; default the order's cornice at `module`). rise: apex
    over the horizontal cornice's top (default 2/9 of the span, the classic slope of about 24 degrees; a segmental
    pediment 0.15 of the span). depth: thickness behind the face (default half the cornice height).
    span, module, rise, depth and at may be X (Built by Opus 5.5 (medium); pass `controls=` when span, module,
    rise or depth is one). Exact when the rise follows the span (the default); a rise expression of its own is
    approximate away from the defaults (the raking cornice's slope is linearised there).
    Example: g.pediment("ped", 8.0, "corinthian", 0.25, at=[0, 0, 10.0])"""
    if kind not in ("triangular", "segmental"):
        raise ValueError("pediment kind is triangular or segmental")
    rk0, flat0 = _pediment_profiles(name, prof)
    k = module if prof is None else 1.0
    params = [span, k, 0.0 if rise is None else rise, 0.0 if depth is None else depth]

    def build(v):
        S, kk, R, d = v
        rk, fl = rk0.scaled(kk), flat0.scaled(kk)
        dd = d if depth is not None else 0.5 * rk.height
        RR = R if rise is not None else (2 / 9 if kind == "triangular" else 0.15) * S
        return _pediment_meshes(S, rk, fl, kind, RR, dd)
    level, raking, tymp = _symbolic(build, params, _envof(self, controls), id_)
    parts = [_emit(self, id_ + "-level", level), _emit(self, id_ + "-raking", raking),
             _emit(self, id_ + "-tympanum", tymp, smooth=False)]
    body = self.join(id_ + "-parts", *parts)
    return _place(self, id_, body, at, (X(_facing(facing)) + PI / 2).v, None, slot)


# ---------- balustrade
def _baluster_profile(hb, R):
    """Turned part of a single-bellied baluster, hb high, belly radius R: torus, belly, neck, collar, cap."""
    pts = moulding("torus", 0.05 * hb, 0.72 * R, z0=0.10 * hb, segments=5, depth=0.12 * R)
    pts += [(0.6 * R, 0.15 * hb), (0.6 * R, 0.165 * hb)]
    pts += [(0.55 * R + 0.45 * R * math.sin(PI / 2 * i / 6), 0.165 * hb + 0.195 * hb * i / 6) for i in range(1, 7)]
    pts += [(0.38 * R + 0.62 * R * (0.5 + 0.5 * math.cos(PI * i / 8)), 0.36 * hb + 0.36 * hb * i / 8) for i in range(1, 9)]
    pts += moulding("astragal", 0.05 * hb, 0.4 * R, z0=0.725 * hb, segments=4, depth=0.1 * R)
    pts += [(0.42 * R, 0.78 * hb)] + moulding("cavetto", 0.09 * hb, 0.42 * R, 0.8 * R, z0=0.78 * hb, segments=5)
    return Profile(pts + [(0.8 * R, 0.885 * hb)])


def _balustrade_dims(H):
    hr0, hr1 = 0.16 * H, 0.13 * H
    hb = H - hr0 - hr1
    return hr0, hr1, hb, hb / 6


def _balustrade_rails(pts, H):
    """Numeric meshes of the plinth and the handrail swept along the plan points."""
    hr0, hr1, hb, R = _balustrade_dims(H)
    base_r = profile([("plinth", 0.75 * hr0, 1.15 * R), ("cyma_reversa", 0.18 * hr0, 1.15 * R, 0.95 * R),
                      ("fillet", 0.07 * hr0, 0.95 * R)], 6).points
    rail_r = profile([("fillet", 0.08 * hr1, 0.9 * R), ("cyma_reversa", 0.32 * hr1, 0.9 * R, 1.25 * R),
                      ("fascia", 0.4 * hr1, 1.3 * R), ("ovolo", 0.2 * hr1, 1.3 * R, 1.0 * R)], 6, hr0 + hb - 0.02 * hr1).points
    rail_r = rail_r + [(0.6 * R, H)]
    out = []
    for half in (base_r, rail_r):
        poly = _dedupe(half + [(-x, z_) for x, z_ in reversed(half)], closed=True)
        poly = _crisp(poly, 0.002 * H, closed=True)
        out.append(_sweep_mesh([(x, y, 0.0) for x, y in pts], poly, (0, 0, 1), False, (None, None), 0.002 * H))
    return out


def balustrade(self, id_, outline, height=0.95, at=None, pier_every=3.0, pitch=None, sides=16, slot=None, controls=None):
    """A balustrade along an open plan outline: moulded plinth, turned balusters (a vase with one belly), a
    moulded handrail and square piers at the ends, at each corner and at least every `pier_every` metres. Metres.

    Frame: outline (x, y) points on the floor (z = 0; `at` moves the whole), the balustrade centred on it.
    height: floor to rail top. Plinth 0.16 and rail 0.13 of the height; the balusters fill the rest with a belly
    a third of their height across, at `pitch` (default twice the belly: the gap about one baluster).
    Outline points, height and pitch may be X (Built by Opus 5.5 (medium); pass `controls=` when the outline or
    height is one): balusters and piers keep their proportions to the height, and their counts (pier bays per run,
    balusters per bay) are expressions of the run's length, so none stretch.
    Example: g.balustrade("bal", [(0, 0), (6, 0), (6, 3)], 0.95)"""
    env = _envof(self, controls)
    H = X(height)
    pts = [tuple(p[:2]) for p in outline]
    plinth, rail = _symbolic(lambda v: _balustrade_rails(_unflat(v[:-1], [2] * len(pts)), v[-1]),
                             _flat(pts) + [height], env, id_)
    parts = [_emit(self, id_ + "-plinth", plinth), _emit(self, id_ + "-rail", rail)]
    # prototypes for a balustrade one metre high, scaled by the height
    hr0, hr1, hb, R = _balustrade_dims(1.0)
    turned(self, id_ + "-bal-turned", _baluster_profile(hb, R), sides=sides, base=0.08 * hb, top=0.9 * hb)
    sq = 0.85 * R
    _emit(self, id_ + "-bal-blocks", _merge(_prism(square(sq), -0.02 * hb, 0.1 * hb), _prism(square(sq), 0.885 * hb, 1.02 * hb)), smooth=False)
    self.join(id_ + "-bal-j", id_ + "-bal-turned", id_ + "-bal-blocks")
    self.xf(id_ + "-baluster", id_ + "-bal-j", t=[0, 0, hr0])
    pw = 3.6 * R
    pier_prof = Profile(profile([("plinth", 0.75 * hr0, 0.12 * pw), ("cyma_reversa", 0.2 * hr0, 0.12 * pw, 0.0),
                                 ("fascia", 1 - 0.95 * hr0 - 0.7 * hr1, 0.0), ("cyma_reversa", 0.4 * hr1, 0.0, 0.14 * pw),
                                 ("fascia", 0.3 * hr1, 0.14 * pw)], 6).points + [(0.1 * pw, 1.04)])
    moulded_block(self, id_ + "-pier", square(pw / 2), pier_prof)
    PW = H * pw
    PITCH = X(pitch) if pitch is not None else H * (4 * R)
    segs = _seg_dirs(pts, False, env)
    for i, (a, b, t) in enumerate(segs):
        rot = math.atan2(t[1], t[0])
        pier = self.xf(f"{id_}-pier-{i}", id_ + "-pier", r=[0, 0, rot], s=[H.v] * 3)
        bal = self.xf(f"{id_}-baluster-{i}", id_ + "-baluster", r=[0, 0, rot], s=[H.v] * 3)
        L = _seg_len(a, b, t)
        nb = _xmax(_xceil(L / pier_every - 1e-9), 1)
        bay = L / nb
        nd = _line_instance(self, f"{id_}-piers-{i}", pier, [a[0], a[1], 0], [bay * t[0], bay * t[1], 0],
                            nb + (1 if i == len(segs) - 1 else 0))
        if nd:
            parts.append(nd)
        clear = bay - PW
        n = _xmax(_xfloor(clear / PITCH), 0)
        s0 = PW / 2 + (clear - (n - 1) * PITCH) / 2
        k = X("@index")
        s = _xfloor(k / _xmax(n, 1)) * bay + s0 + X(["mod", k.v, _v(_xmax(n, 1))]) * PITCH
        nd = _line_instance(self, f"{id_}-bay-{i}", bal, [a[0], a[1], 0], [0, 0, 0], nb * n,
                            translation=[s * t[0], s * t[1], 0])
        if nd:
            parts.append(nd)
    return _place(self, id_, self.join(id_ + "-parts", *parts), at, None, None, slot)


# ---------- window and door surrounds
def _surround_meshes(w, h, b, sill, keystone, hood):
    """Numeric meshes [(name, mesh, smooth)] of a surround facing -y, opening bottom centre at the origin."""
    e = 0.3 * b
    arch = profile([("fascia", 0.38 * b, 0.12 * b), ("fascia", 0.42 * b, 0.18 * b), ("cyma_reversa", 0.15 * b, 0.18 * b, 0.28 * b),
                    ("fillet", 0.05 * b, 0.3 * b)], 6)
    poly = _dedupe([(0.0, -e)] + [(z_, x) for x, z_ in arch.points] + [(b, -e)], closed=True)
    eps = 0.003 * b
    path = [(w / 2, 0, 0.0), (w / 2, 0, h), (-w / 2, 0, h), (-w / 2, 0, 0.0)]
    upv = (0.0, 0.0, 1.0)
    out = [("architrave", _sweep_mesh(path, _crisp(poly, eps, closed=True), (0, -1, 0), False, ((0, 0, 1), (0, 0, 1)), eps), True)]
    L = w + 2 * b
    if sill:
        sp = profile([("cyma_reversa", 0.25 * b, 0.05 * b, 0.25 * b), ("fascia", 0.35 * b, 0.32 * b), ("fillet", 0.06 * b, 0.36 * b)],
                     6, -0.66 * b)
        out.append(("sill", _run_mesh(sp, [(-L / 2 - 0.1 * b, 0), (L / 2 + 0.1 * b, 0)], False, "return", 0.0, upv, -e, True), True))
    if keystone:
        k = [(-0.28 * b, h - 0.05 * b), (0.28 * b, h - 0.05 * b), (0.4 * b, h + 1.15 * b), (-0.4 * b, h + 1.15 * b)]
        out.append(("key", _map(_prism(k, -e, 0.42 * b), lambda v: (v[0], -v[2], v[1])), False))
    if hood:
        zf = h + b
        fr = Profile([(0.0, zf - 0.02 * b), (0.0, zf + 0.7 * b)])
        out.append(("frieze", _offset_solid([(-L / 2, -0.12 * b), (L / 2, -0.12 * b), (L / 2, e), (-L / 2, e)], fr.points,
                                            0.002 * max(fr.height, 1e-3)), True))
        cp = profile([("cyma_reversa", 0.15 * b, 0.0, 0.15 * b), ("ovolo", 0.15 * b, 0.18 * b, 0.33 * b), ("corona", 0.25 * b, 0.62 * b),
                      ("fillet", 0.04 * b, 0.65 * b), ("cyma_recta", 0.22 * b, 0.65 * b, 0.86 * b), ("fillet", 0.05 * b, 0.86 * b)], 6)
        cp = cp.shifted(dx=0.12 * b)
        if hood == "cornice":
            out.append(("cornice", _run_mesh(cp.shifted(dz=zf + 0.7 * b), [(-L / 2, 0), (L / 2, 0)], False, "return", 0.0, upv, -e, True), True))
        else:
            kind = "triangular" if hood == "pediment" else "segmental"
            rk, flat = _pediment_profiles(None, cp)
            R = (2 / 9 if kind == "triangular" else 0.15) * L
            lift = lambda m, dz=zf + 0.7 * b: _map(m, lambda v: (v[0], v[1], v[2] + dz))
            lv, rkm, ty = _pediment_meshes(L, rk, flat, kind, R, e)
            out += [("pediment-level", lift(lv), True), ("pediment-raking", lift(rkm), True), ("pediment-tympanum", lift(ty), False)]
    return out


def surround(self, id_, width, height, at=None, facing="-y", band=None, sill=True, keystone=True, hood=None,
             slot=None, controls=None):
    """A window or door surround on a wall: architrave of two fasciae and a cyma round the opening (mitred at the
    head, cut level at the foot), a moulded sill, a keystone and an optional hood. Metres.

    Frame: `at` is the bottom centre of the opening on the wall face; `facing` the wall's outward normal. The
    opening is width x height; band: architrave width (default a sixth of the width, Vignola's rule for door
    architraves). sill: a moulded sill under the opening (False for a door: the jambs stand on the ground).
    keystone: a tapered key at the head. hood: None, "cornice" (frieze and cornice over the architrave, returned
    at the ends), "pediment" (triangular) or "segmental". width, height, band and at may be X (Built by Opus 5.5
    (medium); pass `controls=` when width, height or band is one). Example: g.surround("win", 1.2, 2.2, at=[3, 0, 1.0], hood="pediment")"""
    if hood not in (None, "cornice", "pediment", "segmental"):
        raise ValueError("hood is None, cornice, pediment or segmental")
    names = [nm for nm, _, _ in _surround_meshes(1.2, 2.2, 0.2, sill, keystone, hood)]
    smooth = [sm for _, _, sm in _surround_meshes(1.2, 2.2, 0.2, sill, keystone, hood)]
    b = band if band is not None else X(width) / 6
    meshes = _symbolic(lambda v: [m for _, m, _ in _surround_meshes(v[0], v[1], v[2], sill, keystone, hood)],
                       [width, height, b], _envof(self, controls), id_)
    parts = [_emit(self, f"{id_}-{nm}", mesh, sm) for nm, mesh, sm in zip(names, meshes, smooth)]
    body = self.join(id_ + "-parts", *parts)
    return _place(self, id_, body, at, (X(_facing(facing)) + PI / 2).v, None, slot)


# ------------------------------------------------------------------------------------------------ attach
for _f in (moulding_run, turned, moulded_block, column, pilaster, entablature, pediment, balustrade, surround):
    setattr(Graph, _f.__name__, _f)
for _f in (moulding, profile, square, order, order_for_height, entablature_profile):
    setattr(Graph, _f.__name__, staticmethod(_f))
