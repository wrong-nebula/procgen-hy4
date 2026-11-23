"""Masonry helpers for kit builders: voussoir arches, ashlar and rusticated walls, brick bonds, copings and
string courses, cutwater caps, rubble. Built by Opus 5.5 (medium), 2026-11-16.

kitlib.py imports this module at its end, so builders get the helpers as `Graph` methods:

    from kitlib import Graph, C
    g.arch_ring("arch", 4.0, "segmental", rise=1.2, short=0.35, key_proud=0.05)
    g.ashlar_wall("wall", 8.0, 5.0, rustic="banded", quoins="both",
                  openings=[{"kind": "arch", "x": 0, "width": 2.4, "springing": 2.1}])
    g.brick_wall("leaf", 6.0, 3.0, bond="flemish", openings=[{"kind": "rect", "x": 0, "width": 1.0,
                 "sill": 0.75, "head": 2.25, "lintel": "flat"}])

Frame (all wall and arch helpers): metres, Z-up. The face lies in the XZ plane: x along the face (centred
on x = 0), z up from the foot (an arch's springing line is z = 0), the face at y = 0 and the masonry
running back into +y. `at` places that origin.

Module controls (Built by Opus 5.5 (medium)): spans, rises, wall widths and heights, opening positions
and sizes (x, width, sill, head, springing, rise), voussoir lengths, coping and string course lengths
and `depth` may be X expressions (C("span"), C("level") - 1.2, ...). Stone counts are Python ints, so a
helper given expressions lays the masonry out once at the controls' defaults (pass `controls=`, the dict
given to Kit.module, or set `g.controls`) and lets every length follow the controls: courses, blocks
and bricks keep their number and stretch (course lines spread piecewise between the wall foot, the
sills, heads and springings, the arch crowns and the wall top; head joints between the wall ends and
the openings' sides), while arches are built from expressions in the voussoir index, so their joints
stay radial at every value and the blocks round them stay cut to the true extrados. Limits: openings
keep their order (a sill, head or crown may not pass another opening's across the range); a
three-centred arch keeps the default's stones per arc; `pgm sweep` and `pgm audit --sweep` are the check.

Joints, by reach (docs/DETAILING.md): joints="gap" builds every stone, brick and voussoir as its own
closed prism a mortar gap (`gap`, at most 2.8 mm so `pgm audit` still sees one piece) from its
neighbours, units with wide joints (bricks, rubble) standing as tiles on a mortar core just behind them;
joints="detail" builds one slab per wall or ring and lays the joints on it as grooves (a few hundred
triangles whatever the bond: walls out of reach); joints="material" builds the same plain slab with no
joints, one material slot per bond (BOND_SLOTS: brick-stretcher, brick-english, brick-flemish,
brick-header, ashlar-coursed, ashlar-rusticated) and planar UVs in metres (planarUV xz: u = x, v = z of
the prototype, unscaled), for the engine's bond maps; the bond cycle belongs in the material record.
Maps and stone-by-stone geometry share one source of truth, `bond_pattern()`. Projecting stones
(keystones, imposts, quoins, sills) stay geometry in every mode; in "material" every trim stone
(voussoirs, lintels, quoins) stands as a FACING-thick tile on the slab so the bond map stops at it.
Faces over pgm's 128-corner limit are split. Tests and a sample sheet: recipes/test_kitlib_masonry.py.
"""
import math
import random

import kitlib
from kitlib import PI, X, _v

__all__ = ["arch_geometry", "courses_for", "bond_pattern", "BOND_SLOTS"]

GAP = 0.002            # default mortar gap between dressed stones (pgm audit holds pieces within 3 mm)
CORE_GAP = 0.001       # facing units stand this far in front of the core they are bedded on
MAX_CORNERS = 120      # pgm refuses a face of more than 128 corners (all loops together): split larger ones
FACING = 0.012         # joints="material": trim stones stand this far proud of the slab


# ----------------------------------------------------------------------------------------------- values that follow controls
# Built by Opus 5.5 (medium). A _D is a length that follows the module's controls: its value at the controls'
# defaults (`f`, which every layout decision reads) and its expression (`e`, an X expression tree, which goes
# into the document). Arithmetic on _D builds the expression; a constant result is a plain float.
class _D:
    """f: the value at the defaults; the expression is kept as a linear form c + sum(coef * atom), atoms being
    controls ("$span") or nonlinear terms, so interpolations stay small and proportional ratios fold away."""
    __slots__ = ("f", "lin", "c", "_e")

    def __init__(self, f, lin, c=0.0):
        self.f, self.lin, self.c, self._e = float(f), lin, float(c), None

    @property
    def e(self):
        if self._e is None:
            terms = []
            for k, coef in sorted(self.lin.items()):
                a = _ATOMS[k]
                terms.append(a if coef == 1 else ["*", coef, a])
            if self.c:
                terms.append(self.c)
            e = terms[0]
            for t in terms[1:]:
                e = ["+", e, t]
            self._e = e
        return self._e

    def __repr__(self):
        return f"_D({self.f:.6g})"

    def __add__(self, o): return _lin(self, o, 1.0)
    def __radd__(self, o): return _lin(self, o, 1.0)
    def __sub__(self, o): return _lin(self, o, -1.0)
    def __rsub__(self, o): return _lin(_scale(self, -1.0), o, 1.0)
    def __mul__(self, o): return _mul(self, o)
    def __rmul__(self, o): return _mul(o, self)
    def __truediv__(self, o): return _div(self, o)
    def __rtruediv__(self, o): return _div(o, self)
    def __neg__(self): return _scale(self, -1.0)
    def __abs__(self): return _scale(self, 1.0 if self.f >= 0 else -1.0)
    def __round__(self, n=None): return round(self.f, n) if n is not None else round(self.f)
    def __lt__(self, o): return self.f < _f(o)
    def __le__(self, o): return self.f <= _f(o)
    def __gt__(self, o): return self.f > _f(o)
    def __ge__(self, o): return self.f >= _f(o)
    def __eq__(self, o): return isinstance(o, (int, float, _D)) and self.f == _f(o)
    def __hash__(self): return hash(self.f)


_ATOMS = {}
_PROD = {}             # product atom -> its two factor atoms, so (A B) / B folds to A


def _atom(e, f):
    """A nonlinear term (or a control) as a _D of its own."""
    import json
    k = e if isinstance(e, str) else json.dumps(e, separators=(",", ":"))
    _ATOMS.setdefault(k, e)
    return _D(f, {k: 1.0})


def _make(f, lin, c):
    lin = {k: v for k, v in lin.items() if v != 0}
    return _D(f, lin, c) if lin else float(c)


def _scale(a, k):
    if not isinstance(a, _D):
        return a * k
    return _make(a.f * k, {n: v * k for n, v in a.lin.items()}, a.c * k)


def _lin(a, b, k):
    """a + k * b."""
    if not isinstance(b, _D):
        return _make(a.f + k * b, dict(a.lin), a.c + k * b) if b else a
    lin = dict(a.lin)
    for n, v in b.lin.items():
        lin[n] = lin.get(n, 0.0) + k * v
    return _make(a.f + k * b.f, lin, a.c + k * b.c)


def _mul(a, b):
    if not isinstance(a, _D):
        return _scale(b, a)
    if not isinstance(b, _D):
        return _scale(a, b)
    if not a.c and not b.c and len(a.lin) == 1 and len(b.lin) == 1:      # (k1 A)(k2 B) = k1 k2 (A B)
        (ka, ca), (kb, cb) = next(iter(a.lin.items())), next(iter(b.lin.items()))
        ka, kb = sorted((ka, kb))
        at = _atom(["*", _ATOMS[ka], _ATOMS[kb]], a.f * b.f / (ca * cb))
        _PROD[next(iter(at.lin))] = (ka, kb)
        return _scale(at, ca * cb)
    return _atom(["*", a.e, b.e], a.f * b.f)


def _div(a, b):
    if not isinstance(a, _D) and a == 0:
        return 0.0
    if not isinstance(b, _D):
        return _scale(a, 1.0 / b)
    if isinstance(a, _D) and a.lin.keys() == b.lin.keys():      # proportional forms fold to a number
        r = a.f / b.f
        if all(abs(a.lin[n] - r * b.lin[n]) <= 1e-12 * max(1.0, abs(a.lin[n])) for n in a.lin) and \
                abs(a.c - r * b.c) <= 1e-12 * max(1.0, abs(a.c)):
            return r
    if isinstance(a, _D) and not a.c and not b.c and len(a.lin) == 1 and len(b.lin) == 1:
        (ka, ca), (kb, cb) = next(iter(a.lin.items())), next(iter(b.lin.items()))
        pr = _PROD.get(ka)
        if pr and kb in pr:                                     # (k1 A B) / (k2 B) = k1 / k2 A
            other = pr[1] if pr[0] == kb else pr[0]
            return _scale(_D(a.f / b.f * cb / ca, {other: 1.0}), ca / cb)
    return _atom(["/", _e(a), b.e], _f(a) / b.f)


def _f(x):
    """Value at the defaults."""
    return x.f if isinstance(x, _D) else x


def _e(x):
    """Document value: a number or an expression tree."""
    return x.e if isinstance(x, _D) else _v(x)


def _fn(name, fn, *args):
    if not any(isinstance(a, _D) for a in args):
        return fn(*args)
    return _atom([name] + [_e(a) for a in args], fn(*[_f(a) for a in args]))


def _cos(a):
    return _fn("cos", math.cos, a)


def _sin(a):
    return _fn("sin", math.sin, a)


def _sqrt(a):
    return _fn("sqrt", math.sqrt, a)


def _atan2(y, x):
    if isinstance(y, _D) and isinstance(x, _D) and not y.c and not x.c and y.lin.keys() == x.lin.keys():
        r = y.f / x.f if x.f else None                          # atan2(k1 A, k2 A): the angle of (k1, k2)
        if r is not None and all(abs(y.lin[n] - r * x.lin[n]) <= 1e-12 * max(1.0, abs(y.lin[n])) for n in y.lin):
            return math.atan2(y.f, x.f)
    return _fn("atan2", math.atan2, y, x)


def _acos(c):
    return _atan2(_sqrt(1.0 - c * c), c) if isinstance(c, _D) else math.acos(c)


def _hypot(x, z):
    if not (isinstance(x, _D) or isinstance(z, _D)):
        return math.hypot(x, z)
    if not isinstance(z, _D) and z == 0:
        return _scale(x, 1.0 if _f(x) >= 0 else -1.0)
    if not isinstance(x, _D) and x == 0:
        return _scale(z, 1.0 if _f(z) >= 0 else -1.0)
    return _sqrt(x * x + z * z)


def _dist(a, b):
    """Distance at the defaults (a layout decision)."""
    return math.hypot(_f(a[0]) - _f(b[0]), _f(a[1]) - _f(b[1]))


def _fp(p):
    return [(_f(q[0]), _f(q[1])) for q in p]


def _lerp(a, b, t):
    return a if t == 0 else (b if t == 1 else a + (b - a) * t)


def _lerp2(p, q, t):
    return (_lerp(p[0], q[0], t), _lerp(p[1], q[1], t))


def _parse(e, values):
    """An expression tree as a linear form (controls and nonlinear subterms as atoms)."""
    if isinstance(e, (int, float)):
        return float(e)
    if isinstance(e, str):
        return _atom(e, evaluate(e, values))
    op, args = e[0], e[1:]
    if op in ("+", "-", "*", "/") and len(args) == 2:
        a, b = _parse(args[0], values), _parse(args[1], values)
        return {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b, "/": lambda: a / b}[op]() \
            if isinstance(a, _D) or isinstance(b, _D) else float(_OPS[op](a, b))
    if op == "neg":
        return -_parse(args[0], values)
    return _atom(e, evaluate(e, values))


_OPS = {"+": lambda a, b: a + b, "-": lambda a, b: a - b, "*": lambda a, b: a * b, "/": lambda a, b: a / b,
        "max": max, "min": min, "pow": lambda a, b: a ** b, "atan2": math.atan2, "mod": math.fmod,
        ">": lambda a, b: float(a > b), "<": lambda a, b: float(a < b), "sin": math.sin, "cos": math.cos,
        "tan": math.tan, "sqrt": math.sqrt, "abs": abs, "neg": lambda a: -a, "floor": math.floor,
        "ceil": math.ceil, "round": lambda a: float(round(a))}


def evaluate(e, values):
    """An expression's value with controls `values` ({name: number or control dict}); X, _D or a tree."""
    e = _e(e)
    if isinstance(e, (int, float)):
        return float(e)
    if isinstance(e, str):
        assert e.startswith("$"), f"masonry helpers follow controls ($name), not fields ({e})"
        v = values.get(e[1:]) if values else None
        assert v is not None, (f"control {e[1:]!r} has no default: pass controls= (the module's controls dict) "
                               "or set g.controls, so the helper can lay the stones out")
        return float(v["value"] if isinstance(v, dict) else v)
    if e[0] == "if":
        return evaluate(e[2], values) if evaluate(e[1], values) != 0 else evaluate(e[3], values)
    return float(_OPS[e[0]](*[evaluate(a, values) for a in e[1:]]))


def _is_expr(x):
    return isinstance(x, (X, _D)) or (isinstance(x, str) and x.startswith("$")) or \
        (isinstance(x, list) and bool(x) and isinstance(x[0], str))


def _lift(x, values):
    """A helper argument as a number or a _D (when it is an expression)."""
    if isinstance(x, _D) or not _is_expr(x):
        return x
    return _parse(_v(X(x) if isinstance(x, (str, list)) else x), values)


def _controls(g, controls):
    return controls if controls is not None else getattr(g, "controls", None)


class _PL:
    """A piecewise-linear map from default positions to positions that follow the controls, through anchors
    (_D or floats); beyond the end anchors, constant offsets."""

    def __init__(self, anchors):
        self.a = []
        for f, a in sorted(((_f(a), a) for a in anchors), key=lambda t: t[0]):
            if not self.a or f - self.a[-1][0] > 1e-9:
                self.a.append((f, a))

    def __call__(self, x):
        a = self.a
        for f, e in a:
            if abs(x - f) <= 1e-9:
                return e
        if x < a[0][0]:
            return a[0][1] + (x - a[0][0])
        if x > a[-1][0]:
            return a[-1][1] + (x - a[-1][0])
        for (f0, e0), (f1, e1) in zip(a, a[1:]):
            if f0 < x < f1:
                return _lerp(e0, e1, (x - f0) / (f1 - f0))
        raise AssertionError


# ----------------------------------------------------------------------------------------------- 2D polygons
def _area(p):
    p = _fp(p)
    return 0.5 * sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1] for i in range(len(p)))


def _clean(p, tol=1e-6):
    """Drop repeated and collinear vertices of a closed polygon (judged at the defaults)."""
    q = []
    for pt in p:
        if not q or _dist(pt, q[-1]) > tol:
            q.append(pt if isinstance(pt[0], _D) or isinstance(pt[1], _D) else (float(pt[0]), float(pt[1])))
    while len(q) > 1 and _dist(q[0], q[-1]) <= tol:
        q.pop()
    changed = True
    while changed and len(q) > 3:
        changed = False
        for i in range(len(q)):
            a, b, c = _fp((q[i - 1], q[i], q[(i + 1) % len(q)]))
            cr = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            if abs(cr) < tol * max(1e-9, math.dist(a, c)) and (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1]) > 0:
                q.pop(i)
                changed = True
                break
    return q


def _ccw(p):
    p = _clean(p)
    return p if _area(p) > 0 else p[::-1]


def _inset(p, d):
    """Polygon p (counter-clockwise) moved in by d (a number, or one distance per edge p[i] -> p[i+1]).
    None when an edge would turn over (the polygon is too small for the inset)."""
    n = len(p)
    ds = list(d) if isinstance(d, (list, tuple)) else [d] * n
    if not any(ds):
        return list(p)
    lines = []
    for i in range(n):
        a, b = p[i], p[(i + 1) % n]
        dx, dz = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dz)
        ux, uz = dx / L, dz / L
        lines.append(((a[0] - uz * ds[i], a[1] + ux * ds[i]), (ux, uz)))
    out = []
    for i in range(n):
        (p0, u0), (p1, u1) = lines[i - 1], lines[i]
        cr = u0[0] * u1[1] - u0[1] * u1[0]
        if abs(cr) < 1e-9:
            out.append(p1)
            continue
        t = ((p1[0] - p0[0]) * u1[1] - (p1[1] - p0[1]) * u1[0]) / cr
        out.append((p0[0] + u0[0] * t, p0[1] + u0[1] * t))
    for i in range(n):
        a, b = p[i], p[(i + 1) % n]
        c, e = out[i], out[(i + 1) % n]
        if (b[0] - a[0]) * (e[0] - c[0]) + (b[1] - a[1]) * (e[1] - c[1]) <= 0:
            return None
    return out if _area(out) > 0 else None


def _shrink(p, d):
    """_inset for polygons that may follow controls: each vertex moved by its inset offset at the defaults
    (a mortar gap or chamfer keeps its width while the stone stretches). None when too small."""
    pf = _fp(p)
    q = _inset(pf, d)
    if q is None or not any(isinstance(c, _D) for pt in p for c in pt):
        return q
    return [(a[0] + (c[0] - b[0]), a[1] + (c[1] - b[1])) for a, b, c in zip(p, pf, q)]


def _split_face(loops, limit=None):
    """A planar face (outline counter-clockwise, holes clockwise; (x, z) points, maybe _D) of more than
    `limit` corners cut into pieces along vertical lines (horizontal ones when vertical cuts cannot do it,
    as along a quoined wall's stepped ends). Returns (loops with the cut points inserted, pieces as lists
    of (loop, vertex)), or (loops, None) when the face fits. Every hole is cut through its middle, then
    pieces still too large are halved. Built by Opus 5.5 (medium)."""
    limit = limit or MAX_CORNERS
    if sum(len(lp) for lp in loops) <= limit:
        return loops, None
    for axis in (0, 1):
        try:
            return _split_along(loops, limit, axis)
        except AssertionError:
            if axis:
                raise


def _split_along(loops, limit, axis):
    xs = sorted({round(_f(q[axis]), 9) for lp in loops for q in lp})

    def between(t):
        for a, b in zip(xs, xs[1:]):
            if a <= t < b:
                return (a + b) * 0.5
        return None
    cuts = set()
    for h in loops[1:]:
        hx = [_f(q[axis]) for q in h]
        c = between((min(hx) + max(hx)) * 0.5)
        if c is not None:
            cuts.add(c)
    for _ in range(40):
        new, pieces = _cut_face(loops, sorted(cuts), axis)
        big = [pc for pc in pieces if len(pc) > limit]
        if not big:
            return new, pieces
        for pc in big:
            px = sorted(_f(new[li][vi][axis]) for li, vi in pc)
            c = between(px[len(px) // 2])
            assert c is not None and c not in cuts, "face too large to split under the corner limit"
            cuts.add(c)
    raise AssertionError("face split did not converge")


def _cut_face(loops, cuts, axis=0):
    new, cross = [], {}
    for li, lp in enumerate(loops):
        out = []
        for i in range(len(lp)):
            p, q = lp[i], lp[(i + 1) % len(lp)]
            out.append(p)
            px, qx = _f(p[axis]), _f(q[axis])
            ins = sorted(((c - px) / (qx - px), c) for c in cuts if min(px, qx) < c < max(px, qx))
            for t, c in ins:
                cross.setdefault(c, []).append((li, len(out)))
                out.append(_lerp2(p, q, t))
        new.append(out)
    P = lambda n: _fp([new[n[0]][n[1]]])[0]
    adj = {}
    for li, lp in enumerate(new):
        for vi in range(len(lp)):
            adj.setdefault((li, vi), []).append((li, (vi + 1) % len(lp)))
    for c, nodes in cross.items():
        nodes.sort(key=lambda n: P(n)[1 - axis])
        assert len(nodes) % 2 == 0, "face cut: odd crossings"
        for a, b in zip(nodes[::2], nodes[1::2]):
            adj[a].append(b)
            adj[b].append(a)
    used, pieces = set(), []
    for u0 in adj:
        for v0 in adj[u0]:
            if (u0, v0) in used:
                continue
            cyc, u, v = [u0], u0, v0
            while (u, v) not in used:
                used.add((u, v))
                if v == u0:
                    break
                cyc.append(v)
                pu, pv = P(u), P(v)
                ref = math.atan2(pu[1] - pv[1], pu[0] - pv[0])

                def turn(w):
                    pw = P(w)
                    d = (ref - math.atan2(pw[1] - pv[1], pw[0] - pv[0])) % (2 * PI)
                    return d if d > 1e-12 else 2 * PI
                u, v = v, min(adj[v], key=turn)
            pieces.append(cyc)
    for pc in pieces:
        assert _area([new[li][vi] for li, vi in pc]) > 0, "face cut left a hole uncut"
    return new, pieces


def _simplify(p, tol):
    """Merge away edges shorter than tol (keeps at least a triangle): rough polygons before a wide inset."""
    q = list(p)
    while len(q) > 3:
        k = min(range(len(q)), key=lambda i: math.dist(q[i], q[(i + 1) % len(q)]))
        if math.dist(q[k], q[(k + 1) % len(q)]) >= tol:
            break
        q.pop((k + 1) % len(q))
    return q


def _round_corners(p, r):
    """Each corner cut back by r along both edges (at most a third of the edge): a dressed-off rubble arris."""
    out = []
    n = len(p)
    for i in range(n):
        a, b, c = p[i - 1], p[i], p[(i + 1) % n]
        la, lc = math.dist(a, b), math.dist(b, c)
        ra, rc = min(r, la / 3), min(r, lc / 3)
        out.append((b[0] + (a[0] - b[0]) * ra / la, b[1] + (a[1] - b[1]) * ra / la))
        out.append((b[0] + (c[0] - b[0]) * rc / lc, b[1] + (c[1] - b[1]) * rc / lc))
    return out


def _inside(pt, poly):
    x, z = pt
    c = False
    for i in range(len(poly)):
        (x0, z0), (x1, z1) = poly[i - 1], poly[i]
        if (z0 > z) != (z1 > z) and x < x0 + (z - z0) * (x1 - x0) / (z1 - z0):
            c = not c
    return c


def _bbox(poly):
    xs, zs = [_f(q[0]) for q in poly], [_f(q[1]) for q in poly]
    return min(xs), min(zs), max(xs), max(zs)


def _notch(outline, holes):
    """Holes standing on the outline's bottom edge (z = 0: doorways) become notches in it; returns (outline, rest)."""
    outline = _ccw(outline)
    ground = sorted((h for h in holes if min(q[1] for q in h) < 1e-9), key=lambda h: h[0][0])
    rest = [h for h in holes if min(q[1] for q in h) >= 1e-9]
    if not ground:
        return outline, rest
    i = next(k for k in range(len(outline)) if abs(outline[k][1]) < 1e-9 and abs(outline[(k + 1) % len(outline)][1]) < 1e-9
             and outline[(k + 1) % len(outline)][0] > outline[k][0])
    ins = []
    for h in ground:                             # each hole starts (x0, 0), (x1, 0): walk it clockwise
        ins += [h[0]] + h[2:][::-1] + [h[1]]
    return _clean(outline[:i + 1] + ins + outline[i + 1:]), rest


# ----------------------------------------------------------------------------------------------- shells
class _Shells:
    """Closed prisms gathered into one `surface` node's vertex and face lists."""

    def __init__(self):
        self.verts, self.faces, self.count = [], [], 0

    def _p(self, x, y, z):
        self.verts.append([_e(x), _e(y), _e(z)])
        return len(self.verts) - 1

    def prism(self, poly, y0, y1, chamfer=None, bulge=0.0):
        """poly (x, z) counter-clockwise seen from the face (-y); front face at y0, back at y1 (may be X).
        chamfer: inset of the front face per edge (or one number), at 45 degrees to the largest inset.
        bulge: the face rises toward its middle by this much (a pillowed rubble face). Vertices may be _D:
        insets are offsets worked out at the defaults."""
        n = len(poly)
        ring = lambda pts, y: [self._p(q[0], y, q[1]) for q in pts]
        front, cd = None, 0
        if chamfer is not None and (any(chamfer) if isinstance(chamfer, (list, tuple)) else chamfer):
            front = _shrink(poly, chamfer)
            cd = max(chamfer) if isinstance(chamfer, (list, tuple)) else chamfer
        faces = []
        if front is not None:
            F, M = ring(front, y0), ring(poly, y0 + cd)
            for i in range(n):
                j = (i + 1) % n
                faces.append([F[i], M[i], M[j], F[j]])
        else:
            front = poly
            F = M = ring(poly, y0)
        inner = _shrink(front, min(0.25 * math.sqrt(abs(_area(front))), 0.04)) if bulge else None
        if inner is not None:
            I = ring(inner, y0 - bulge * 0.7)
            c = self._p(sum((q[0] for q in inner), 0.0) / len(inner), y0 - bulge, sum((q[1] for q in inner), 0.0) / len(inner))
            m = len(inner)
            for i in range(m):
                j = (i + 1) % m
                faces.append([F[i], F[j], I[j], I[i]])
                faces.append([I[i], I[j], c])
        else:
            faces.append(list(F))
        B = ring(poly, y1)
        for i in range(n):
            j = (i + 1) % n
            faces.append([M[i], B[i], B[j], M[j]])
        faces.append(B[::-1])
        self.faces += faces
        self.count += 1

    def slab(self, outline, holes, y0, y1):
        """A prism with holes (openings that do not touch the outline). A face of more than MAX_CORNERS
        corners is cut into pieces along vertical lines (the cut points are added to the loops)."""
        outline = _ccw(outline)
        holes = [_ccw(h)[::-1] for h in holes]
        loops, pieces = _split_face([outline] + holes)
        F = [[self._p(q[0], y0, q[1]) for q in lp] for lp in loops]
        B = [[self._p(q[0], y1, q[1]) for q in lp] for lp in loops]
        if pieces is None:
            self.faces.append(F if holes else F[0])
            self.faces.append([b[::-1] for b in B] if holes else B[0][::-1])
        else:
            for pc in pieces:
                self.faces.append([F[li][vi] for li, vi in pc])
                self.faces.append([B[li][vi] for li, vi in pc][::-1])
        for f, b in zip(F, B):
            n = len(f)
            for i in range(n):
                j = (i + 1) % n
                self.faces.append([f[i], b[i], b[j], f[j]])
        self.count += 1

    def swap_xy(self):
        """Turn a shell built with its extrusion along y into one extruded along x (a mirror: faces reversed)."""
        for v in self.verts:
            v[0], v[1] = v[1], v[0]
        self.faces = [[lp[::-1] for lp in f] if isinstance(f[0], list) else f[::-1] for f in self.faces]

    def node(self, g, id_):
        return g.n(id_, "surface", vertices=self.verts, faces=self.faces)


# ----------------------------------------------------------------------------------------------- arches
def _pieces(kind, span, rise, beta, centre_depth):
    """Intrados pieces from the right springing (x = span/2) to the left: ('arc', (cx, cz), r, a0, a1)
    counter-clockwise, or ('line', p0, p1, fan centre); and the rise."""
    h = span * 0.5
    if kind == "semicircular":
        return [("arc", (0.0, 0.0), h, 0.0, PI)], h
    if kind == "segmental":
        assert rise is not None and 0 < rise < h, "segmental arch: 0 < rise < span / 2"
        r = (h * h + rise * rise) / (2 * rise)
        cz = rise - r
        a0 = _atan2(-cz, h)
        return [("arc", (0.0, cz), r, a0, PI - a0)], rise
    if kind in ("three-centred", "basket", "elliptical"):
        rise = rise if rise is not None else span / 3
        c, s = math.cos(beta), math.sin(beta)
        det = (1 - c) * (1 - s) - c * s
        r1, r2 = (h * (1 - s) - c * rise) / det, ((1 - c) * rise - s * h) / det
        assert 0 < r1 < r2, f"three-centred arch: rise {rise} does not fit span {span} with beta {beta:.3f}"
        return [("arc", (h - r1, 0.0), r1, 0.0, beta), ("arc", (0.0, rise - r2), r2, beta, PI - beta),
                ("arc", (r1 - h, 0.0), r1, PI - beta, PI)], rise
    if kind == "pointed":
        rise = rise if rise is not None else h * math.sqrt(3)          # equilateral: centres on the springings
        assert rise > h, "pointed arch: rise > span / 2"
        r = (h * h + rise * rise) / span
        th = _acos((r - h) / r)
        return [("arc", (h - r, 0.0), r, 0.0, th), ("arc", (r - h, 0.0), r, PI - th, PI)], rise
    if kind == "flat":
        f = centre_depth if centre_depth is not None else span * math.sqrt(3) * 0.5
        return [("line", (h, 0.0), (-h, 0.0), (0.0, -f))], 0.0
    raise ValueError(f"arch kind {kind!r}: semicircular, segmental, three-centred, pointed or flat")


def _plen(pc):
    return pc[2] * (pc[4] - pc[3]) if pc[0] == "arc" else _hypot(pc[2][0] - pc[1][0], pc[2][1] - pc[1][1])


def _at(pieces, s):
    """(point, outward joint direction, centre) at arc length s along the intrados; at the apex of a
    pointed arch the direction is the bisector and the centre None."""
    for k, pc in enumerate(pieces):
        L = _plen(pc)
        last = k == len(pieces) - 1
        if s <= L + 1e-9 or last:
            if pc[0] == "arc":
                (cx, cz), r, a0, a1 = pc[1], pc[2], pc[3], pc[4]
                a = _lerp(a0, a1, min(s, L) / L)
                n = (_cos(a), _sin(a))
                if abs(s - L) < 1e-9 and not last:
                    a2 = pieces[k + 1][3]
                    if abs(_f(n[0]) * math.sin(_f(a2)) - _f(n[1]) * math.cos(_f(a2))) > 1e-6:
                        m = (n[0] + _cos(a2), n[1] + _sin(a2))
                        ml = _hypot(*m)
                        return (cx + r * n[0], cz + r * n[1]), (m[0] / ml, m[1] / ml), None, m
                return (cx + r * n[0], cz + r * n[1]), n, (cx, cz), n
            p0, p1, c = pc[1], pc[2], pc[3]
            t = min(s, L) / L
            p = (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)
            d = (p[0] - c[0], p[1] - c[1])
            dl = _hypot(*d)
            return p, (d[0] / dl, d[1] / dl), c, d
        s -= L
    raise AssertionError


def _intrados_pts(pieces, s0, s1, step=0.07):
    """Points along the intrados from s0 to s1 (both ends and piece joins included)."""
    marks, acc = [], 0.0
    for pc in pieces:
        acc += _plen(pc)
        marks.append(acc)
    cuts = sorted({s0, s1} | {m for m in marks[:-1] if s0 < m < s1})
    pts = []
    for a, b in zip(cuts, cuts[1:]):
        pc = pieces[min(i for i, m in enumerate(marks) if (a + b) * 0.5 <= m + 1e-12)]
        k = 1 if pc[0] == "line" else max(1, math.ceil(_f(b - a) / min(step, _f(pc[2]) * 0.07)))
        for i in range(k):
            pts.append(_at(pieces, a + (b - a) * i / k)[0])
    pts.append(_at(pieces, s1)[0])
    return pts


def arch_geometry(span, kind="semicircular", rise=None, count=None, voussoir=0.4, key=True, key_scale=1.2,
                  length=0.45, short=None, extrados="plain", course=None, level=None, key_rise=0.0,
                  beta=PI / 3, centre_depth=None, fill_notches=False, skewback=0.12):
    """The voussoirs of an arch ring as 2D polygons, built from nothing (arch_ring and the walls use it).

    Frame: the face plane, x across (centred), z up, springing line z = 0. kind: "semicircular",
    "segmental" (rise < span/2), "three-centred" (basket handle; rise default span/3, side arcs of `beta`
    radians), "pointed" (two-centred; rise default equilateral, span * sqrt(3)/2) or "flat" (a jack arch:
    level soffit, joints fanning from a centre `centre_depth` below, default span * sqrt(3)/2). Joints are
    radial to the centre of the arc they rise from (to the fan centre for a flat arch); a pointed arch
    without a key has a vertical joint at its apex. count: stones (else intrados length / `voussoir`);
    with `key` the count is odd and the middle stone a keystone `key_scale` times as wide. length: radial
    length of the stones; short: alternate long-and-short voussoirs (key long). extrados "plain" (each
    stone squared off at its length), "stepped" (stone tops on the course lines above the springing,
    `course` a height or a list of line heights) or "level" (tops on z = level: flat and segmental arches
    in coursed work). key_rise lifts the keystone, with vertical sides. Segmental and flat arches spring
    from skewback stones (bed on z = 0, top on the inclined springing joint, `skewback` past it).
    fill_notches adds packing stones (kind "filler") outside short voussoirs: the outer edge is smooth.

    Returns {"stones": [{"poly", "kind" voussoir|key|skewback|filler, "index"}] (counter-clockwise),
    "joints": [{"a", "n", "centre", "length"}] (joint lines from the intrados, index 0 = right springing),
    "outer" (the ring's outer chain from right to left), "outline" (the whole ring), "rise", "count"}.
    Example: arch_geometry(3.0, "three-centred", rise=1.0, short=0.3)["joints"][1]"""
    pieces, rise = _pieces(kind, span, rise, beta, centre_depth)
    total = sum(_plen(p) for p in pieces)
    if count is None:
        count = max(3, int(round(total / voussoir)))
        if key and count % 2 == 0:
            count += 1
        if not key and kind == "pointed" and count % 2:
            count += 1
    n = int(count)
    assert n >= (3 if key else 2), "an arch needs at least three stones (two without a key)"
    assert not key or n % 2 == 1, "a keyed arch has an odd stone count"
    w = [1.0] * n
    if key:
        w[n // 2] = key_scale
    acc, s = 0.0, [0.0]
    for x in w:
        acc += x
        s.append(total * acc / sum(w))
    joints = []
    for j in range(n + 1):
        a, d, c, dv = _at(pieces, s[j])
        joints.append({"a": a, "n": d, "centre": c, "dv": dv})
    if kind == "pointed" and not key:
        joints[n // 2]["n"], joints[n // 2]["centre"], joints[n // 2]["dv"] = (0.0, 1.0), None, (0.0, 1.0)

    def is_long(i):
        if short is None:
            return True
        return (n // 2 - i) % 2 == 0 if n % 2 else min(i, n - 1 - i) % 2 == 0
    L = [length if is_long(i) else short for i in range(n)]
    Lmax = max(L)
    lines = None
    if extrados == "stepped":
        lines = list(course) if isinstance(course, (list, tuple)) else [course * k for k in range(1, 400)]

    def along(j, t):
        a, d = joints[j]["a"], joints[j]["n"]
        return (a[0] + d[0] * t, a[1] + d[1] * t)

    def reach(j, Ls):
        """(extrados point, length along the joint) on a course line, a level or at Ls."""
        a, d, dv = joints[j]["a"], joints[j]["n"], joints[j]["dv"]
        on = lambda z: ((a[0] + dv[0] * ((z - a[1]) / dv[1]), z), (z - a[1]) / d[1])     # no square roots
        if extrados == "level":
            assert d[1] > 0.05, "level extrados: joints must rise (use a flat or segmental arch)"
            assert level is not None and level > a[1], "level extrados needs level above the springing"
            return on(level)
        if extrados == "stepped" and d[1] > math.sin(math.radians(12)):
            for z in lines:
                E, t = on(z)
                if t >= Ls - 1e-9:
                    return (E, t) if t <= 2.2 * Ls else (along(j, Ls), Ls)
        return along(j, Ls), Ls
    assert extrados in ("plain", "stepped", "level"), "extrados: plain, stepped or level"
    ends = [reach(j, length) for j in range(n + 1)]
    tj = [t for _, t in ends]
    stones, chain = [], []
    mid = n // 2
    for i in range(n):
        if extrados != "plain":
            Ea, Eb = ends[i][0], ends[i + 1][0]
        else:
            Ea, Eb = along(i, L[i]), along(i + 1, L[i])
        side = "key" if key and i == mid else ("right" if 2 * i + 1 < n else "left")
        path = [Ea]
        if side == "key" and (key_rise > 0 or extrados == "stepped"):
            zt = max(Ea[1], Eb[1]) + key_rise
            path += [(Ea[0], zt), (Eb[0], zt)]
        elif extrados == "stepped" and side == "right" and Eb[1] >= Ea[1] and Ea[0] >= Eb[0]:
            path.append((Ea[0], Eb[1]))
        elif extrados == "stepped" and side == "left" and Ea[1] >= Eb[1] and Ea[0] >= Eb[0]:
            path.append((Eb[0], Ea[1]))
        path.append(Eb)
        intr = _intrados_pts(pieces, s[i], s[i + 1])
        stones.append({"poly": _ccw(path + intr[::-1]), "kind": "key" if side == "key" else "voussoir", "index": i})
        if fill_notches and extrados == "plain" and L[i] < Lmax - 1e-9:
            fa, fb = along(i, Lmax), along(i + 1, Lmax)
            stones.append({"poly": _ccw([Ea, fa, fb, Eb]), "kind": "filler", "index": i})
            chain += [fa, fb]
        else:
            chain += path
    for end, j in (("right", 0), ("left", n)):         # skewbacks under inclined springing joints
        d = joints[j]["n"]
        if d[1] > 0.02:
            a = joints[j]["a"]
            E = chain[0] if end == "right" else chain[-1]
            Xo = E[0] + (skewback if end == "right" else -skewback)
            stones.append({"poly": _ccw([a, (Xo, a[1]), (Xo, E[1]), E]), "kind": "skewback", "index": -1 if end == "right" else n})
            chain = [(Xo, a[1]), (Xo, E[1])] + chain if end == "right" else chain + [(Xo, E[1]), (Xo, a[1])]
    outer = [chain[0]]
    for p in chain[1:]:
        if _dist(p, outer[-1]) > 1e-7:
            outer.append(p)
    intr = _intrados_pts(pieces, 0.0, total)
    outline = _clean([intr[0]] + outer + [intr[-1]] + intr[::-1][1:-1])
    jl = []
    for j in range(n + 1):
        nb = [L[i] for i in (j - 1, j) if 0 <= i < n]
        jl.append(dict({k: v for k, v in joints[j].items() if k != "dv"}, length=min(nb) if extrados == "plain" else tj[j]))
    return {"stones": stones, "joints": jl, "outer": outer, "outline": outline, "rise": rise, "pieces": pieces, "count": n}


# ----------------------------------------------------------------------------------------------- coursed walls
def courses_for(height, course):
    """Course lines [0, ..., height]: course a number (equal courses as near that height as fit) or a list of
    heights from the bottom (repeated; the last course takes what is left). Example: courses_for(3.0, 0.35)"""
    if not isinstance(course, (list, tuple)):
        n = max(1, int(round(height / course)))
        return [height * k / n for k in range(n + 1)]
    zs, k = [0.0], 0
    while zs[-1] < height - 1e-9:
        zs.append(zs[-1] + course[k % len(course)])
        k += 1
    if len(zs) > 2 and height - zs[-2] < 0.5 * course[(k - 1) % len(course)]:
        zs.pop(-2)
    zs[-1] = height
    return zs


class _Obstacle:
    """A vertically convex region of a wall face that the coursing works round (an opening with its arch,
    lintel or sill; a quoin), with the stones that fill it."""

    def __init__(self, poly, stones=(), hole=None):
        self.poly = p = _ccw(poly)
        self.stones, self.hole = list(stones), hole
        self.xmin, self.xmax = min(q[0] for q in p), max(q[0] for q in p)
        self.zmin, self.zmax = min(q[1] for q in p), max(q[1] for q in p)
        n, eps = len(p), 1e-9
        i0 = min((i for i in range(n) if p[i][0] < self.xmin + eps), key=lambda i: p[i][1])
        lo, i = [p[i0]], i0
        while p[i][0] < self.xmax - eps:
            i = (i + 1) % n
            lo.append(p[i])
        hi, i = [p[i0]], i0
        while True:                                   # backwards: up the left edge, then along the top
            i = (i - 1) % n
            if p[i][0] < self.xmin + eps:
                hi = [p[i]]
                continue
            hi.append(p[i])
            if p[i][0] > self.xmax - eps:
                break
        self.lo, self.hi = lo, hi
        for ch in (lo, hi):
            assert all(b[0] >= a[0] - 1e-7 for a, b in zip(ch, ch[1:])), "masonry opening outline is not vertically convex"


def _ev(ch, x, side):
    """Chain height at x: side +1 the right-hand limit, -1 the left-hand one."""
    at = [k for k, q in enumerate(ch) if abs(q[0] - x) < 1e-9]
    if at:
        return ch[at[-1]][1] if side > 0 else ch[at[0]][1]
    for a, b in zip(ch, ch[1:]):
        if a[0] < x < b[0]:
            return a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0])
    raise ValueError(f"x = {x} outside a chain")


def _band(z0, z1, X0, X1, obstacles, cuts, min_len):
    """Stones of one course (z0 to z1, x = X0 to X1) worked round the obstacles: counter-clockwise polygons
    whose beds and tops follow any obstacle they meet. cuts: the bond's head-joint positions."""
    eps = 1e-6
    obs = [o for o in obstacles if o.zmax > z0 + eps and o.zmin < z1 - eps and o.xmax > X0 and o.xmin < X1]
    bps = {X0, X1}
    for o in obs:
        for ch in (o.lo, o.hi):
            bps |= {q[0] for q in ch if X0 < q[0] < X1}
            for a, b in zip(ch, ch[1:]):
                for zc in (z0, z1):
                    if (a[1] - zc) * (b[1] - zc) < 0 and b[0] > a[0]:
                        bps.add(a[0] + (b[0] - a[0]) * (zc - a[1]) / (b[1] - a[1]))
    xs = []
    for x in sorted(bps):
        if not xs or x - xs[-1] > 1e-7:
            xs.append(x)
    iv = []
    for u, v in zip(xs, xs[1:]):
        m = (u + v) * 0.5
        Bu = Bv = z0
        Tu = Tv = z1
        for o in obs:
            if not o.xmin < m < o.xmax:
                continue
            lm, hm = _ev(o.lo, m, 1), _ev(o.hi, m, 1)
            if hm <= z0 + eps or lm >= z1 - eps:
                continue
            if lm <= z0 + eps:
                Bu, Bv = max(Bu, _ev(o.hi, u, 1)), max(Bv, _ev(o.hi, v, -1))
            elif hm >= z1 - eps:
                Tu, Tv = min(Tu, _ev(o.lo, u, 1)), min(Tv, _ev(o.lo, v, -1))
            else:
                raise ValueError(f"an opening edge falls inside the course {z0:.3f}-{z1:.3f} at x = {m:.3f}: "
                                 "sills, heads, imposts and springings belong on course lines")
        Bu, Bv, Tu, Tv = min(Bu, z1), min(Bv, z1), max(Tu, z0), max(Tv, z0)
        iv.append((u, v, Bu, Bv, Tu, Tv, (Tu + Tv) - (Bu + Bv) > 2e-4))

    def f(x, side, which):
        for u, v, Bu, Bv, Tu, Tv, _ in iv:
            if (u <= x < v) if side > 0 else (u < x <= v):
                t = (x - u) / (v - u)
                return Bu + (Bv - Bu) * t if which == "B" else Tu + (Tv - Tu) * t
        u, v, Bu, Bv, Tu, Tv, _ = iv[-1] if side > 0 else iv[0]
        return (Bv if side > 0 else Bu) if which == "B" else (Tv if side > 0 else Tu)
    runs, cur = [], None
    for k, it in enumerate(iv):
        if it[6]:
            if cur is None:
                cur = [it[0], it[1], []]
            else:
                pv = iv[k - 1]
                if abs(pv[3] - it[2]) > 0.02 or abs(pv[5] - it[4]) > 0.02 or min(pv[5] - pv[3], it[4] - it[2]) < 1e-3:
                    cur[2].append(it[0])                                   # a step in the bed or top: a head joint
                cur[1] = it[1]
        elif cur is not None:
            runs.append(cur)
            cur = None
    if cur is not None:
        runs.append(cur)
    out = []
    for ra, rb, forced in runs:
        ends = [ra] + forced + [rb]
        cs = sorted(set(ends) | {c for c in cuts if ra < c < rb and all(abs(c - e) >= min_len for e in ends)})
        for xa, xb in zip(cs, cs[1:]):
            inner = [x for x in xs if xa + 1e-9 < x < xb - 1e-9]
            poly = [(xa, f(xa, 1, "B"))]
            for x in inner:
                poly += [(x, f(x, -1, "B")), (x, f(x, 1, "B"))]
            poly += [(xb, f(xb, -1, "B")), (xb, f(xb, -1, "T"))]
            for x in reversed(inner):
                poly += [(x, f(x, 1, "T")), (x, f(x, -1, "T"))]
            poly.append((xa, f(xa, 1, "T")))
            poly = _clean(poly)
            if len(poly) >= 3 and _area(poly) > 1e-5:
                out.append(poly)
    return out


# Bond layout lives in masonry_bonds (pure, shared with procgen/tools/bond_maps.py, which bakes the maps from
# the same cells), so geometry and maps cannot drift. Built by Opus 5.5 (medium).
from masonry_bonds import ASHLAR, BOND_SLOTS, bond_pattern  # noqa: E402,F401


def _lattice(cuts, w, X0, X1):
    """Repeat cell head joints (in [0, w)) over [X0, X1]."""
    out = []
    m = math.floor(X0 / w) - 1
    while m * w < X1 + w:
        out += [c + m * w for c in cuts if X0 <= c + m * w < X1]
        m += 1
    return sorted(out)


def _bond_cuts(bond, k, X0, X1, unit, joint, rng=None, vary=0.0, bond_frac=0.5, prev=None, lap=0.0):
    """Head-joint centre lines of course k across [X0, X1], on bond_pattern's lattice from x = 0."""
    Lb, Wb = unit
    if bond == "ashlar":
        start = ((k * bond_frac) % 1.0) * Lb
        cuts, x = [], start - Lb * math.ceil((start - X0) / Lb)
        while x < X1:
            cuts.append(x)
            x += Lb * (1 + vary * (2 * rng.random() - 1)) if vary else Lb
        if prev and lap:                          # keep perpends a lap clear of the course below
            fixed = []
            for c in cuts:
                for p in prev:
                    if abs(c - p) < lap:
                        c = p + (lap if c >= p else -lap)
                fixed.append(c)
            cuts = sorted(fixed)
        return cuts
    if bond == "soldier":
        return _lattice([0.0], Wb + joint, X0, X1)
    pat = bond_pattern(bond, (Lb, Wb, 0.065), joint)
    return _lattice(pat["rows"][k % len(pat["rows"])]["cuts"], pat["cell"][0], X0, X1)


def _snap(z, zs):
    return min(zs, key=lambda c: abs(c - z))


_ARCH_KEYS = ("rise", "count", "voussoir", "key", "key_scale", "length", "short", "extrados", "key_rise", "beta", "centre_depth")


def _opening(spec, zs, arch_defaults, unit_h):
    """An obstacle for one opening spec (see ashlar_wall)."""
    kind = spec.get("kind", "rect")
    xc, w = spec.get("x", 0.0), spec["width"]
    h = w * 0.5
    zsill = _snap(spec.get("sill", 0.0), zs)
    st, extra = [], []
    if spec.get("sill_stone") and zsill > 0:                 # its own obstacle: the jambs stand between sill and lintel
        zb = max(c for c in zs if c < zsill - 1e-9)
        b = spec.get("bearing", 0.12)
        sp = [(xc - h - b, zb), (xc + h + b, zb), (xc + h + b, zsill), (xc - h - b, zsill)]
        extra.append(_Obstacle(sp, [{"poly": sp, "kind": "sill", "proud": spec.get("sill_proud", 0.04)}]))
    lo_r, lo_l = [(xc - h, zsill), (xc + h, zsill)], []
    opts = {k: v for k, v in arch_defaults.items() if k in _ARCH_KEYS}
    opts.update({k: spec[k] for k in _ARCH_KEYS if k in spec})
    key_proud = spec.get("key_proud", arch_defaults.get("key_proud", 0.0))
    if kind == "rect":
        zh = _snap(spec["head"], zs)
        assert zh > zsill, "rect opening: head above sill"
        head = spec.get("lintel", "stone")
        hole = [(xc - h, zsill), (xc + h, zsill), (xc + h, zh), (xc - h, zh)]
        if head is None:
            return [_Obstacle(lo_r + [(xc + h, zh), (xc - h, zh)] + lo_l, st, hole)] + extra
        above = [c for c in zs if c > zh + 1e-9]
        if head in ("stone", "soldier"):
            b = spec.get("bearing", 0.15 if head == "stone" else 0.0)
            ztop = min(above, key=lambda c: abs(c - zh - unit_h)) if head == "soldier" else above[0]
            if head == "soldier":                           # bricks on end across the head
                m = max(1, round((w + 2 * b) / arch_defaults.get("soldier_pitch", 0.075)))
                xs = [xc - h - b + (w + 2 * b) * i / m for i in range(m + 1)]
                st += [{"poly": [(xs[i], zh), (xs[i + 1], zh), (xs[i + 1], ztop), (xs[i], ztop)], "kind": "soldier"} for i in range(m)]
            else:
                st.append({"poly": [(xc - h - b, zh), (xc + h + b, zh), (xc + h + b, ztop), (xc - h - b, ztop)],
                           "kind": "lintel", "proud": spec.get("lintel_proud", 0.0)})
            poly = lo_r + [(xc + h, zh), (xc + h + b, zh), (xc + h + b, ztop), (xc - h - b, ztop), (xc - h - b, zh), (xc - h, zh)] + lo_l
            return [_Obstacle(poly, st, hole)] + extra
        assert head == "flat", "rect opening lintel: stone, flat, soldier or None"
        ztop = min(above, key=lambda c: abs(c - zh - spec.get("arch_height", 0.35)))
        ag = arch_geometry(w, "flat", count=opts.get("count"), voussoir=opts.get("voussoir", 0.3), key=True,
                           key_scale=opts.get("key_scale", 1.2), extrados="level", level=ztop - zh,
                           centre_depth=opts.get("centre_depth"))
        mv = lambda p: [(q[0] + xc, q[1] + zh) for q in p]
        for s in ag["stones"]:
            st.append({"poly": mv(s["poly"]), "kind": s["kind"], "proud": key_proud if s["kind"] == "key" else 0.0})
        return [_Obstacle(lo_r + [(xc + h, zh)] + mv(ag["outer"]) + [(xc - h, zh)] + lo_l, st, hole)] + extra
    assert kind == "arch", "opening kind: rect or arch"
    zsp = _snap(spec["springing"], zs)
    assert zsp >= zsill, "arched opening: springing at or above the sill"
    akind = spec.get("arch", arch_defaults.get("arch", "semicircular"))
    opts.pop("arch", None)
    opts.pop("key_proud", None)
    if opts.get("extrados", "plain") == "stepped":
        opts["course"] = [c - zsp for c in zs if c > zsp + 1e-9]
    if opts.get("short") is not None and opts.get("extrados", "plain") == "plain":
        opts["fill_notches"] = True
    ag = arch_geometry(w, akind, **opts)
    mv = lambda p: [(q[0] + xc, q[1] + zsp) for q in p]
    for s in ag["stones"]:
        st.append({"poly": mv(s["poly"]), "kind": s["kind"], "proud": key_proud if s["kind"] == "key" else 0.0})
    outer = mv(ag["outer"])
    imp = spec.get("impost")
    if imp and zsp > zsill:
        zb = max(c for c in zs if c < zsp - 1e-9)
        xo = max(q[0] for q in outer) - xc + 0.08
        ip = imp.get("proud", 0.05) if isinstance(imp, dict) else 0.05
        for sx in (1, -1):
            a, b = sorted((xc + sx * h, xc + sx * xo))
            st.append({"poly": [(a, zb), (b, zb), (b, zsp), (a, zsp)], "kind": "impost", "proud": ip})
        right, left = [(xc + h, zb), (xc + xo, zb), (xc + xo, zsp)], [(xc - xo, zsp), (xc - xo, zb), (xc - h, zb)]
    else:
        right, left = [(xc + h, zsp)], [(xc - h, zsp)]
    if outer[0][1] > zsp + 1e-9:
        right.append((outer[0][0], zsp))
    if outer[-1][1] > zsp + 1e-9:
        left = [(outer[-1][0], zsp)] + left
    intr = mv(_intrados_pts(ag["pieces"], 0.0, sum(_plen(p) for p in ag["pieces"])))
    return [_Obstacle(lo_r + right + outer + left + lo_l, st, [(xc - h, zsill), (xc + h, zsill)] + intr)] + extra


def _joint_curves(g, id_, polys, y=-0.002, where=None):
    """Guide curves along the joints between polygons (edges with a polygon on both sides): horizontal runs
    merged into lines, equal vertical joints instanced, the rest chained into polylines. Polygons may hold
    _D vertices (then every guide point is the vertex's expression, or `where((x, z))` for a point that is
    no vertex, and nothing is instanced)."""
    edges = {}
    key = lambda q: (round(_f(q[0]), 5), round(_f(q[1]), 5))
    exact = {}
    for p in polys:
        for q in p:
            if isinstance(q[0], _D) or isinstance(q[1], _D):
                exact.setdefault(key(q), q)
    param = bool(exact) or where is not None
    at = lambda x, z: exact.get(key((x, z))) or (where((x, z)) if where else (x, z))
    P = lambda x, z: (lambda q: [_e(q[0]), y, _e(q[1])])(at(x, z))
    polys = [_fp(p) for p in polys]
    boxes = [(_bbox(p), p) for p in polys]

    def covered(pt):
        return any(x0 - 1e-9 <= pt[0] <= x1 + 1e-9 and z0 - 1e-9 <= pt[1] <= z1 + 1e-9 and _inside(pt, p)
                   for (x0, z0, x1, z1), p in boxes)
    for p in polys:
        for i in range(len(p)):
            a, b = key(p[i]), key(p[(i + 1) % len(p)])
            k = tuple(sorted((a, b)))
            if k in edges or a == b:
                continue
            m = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)
            L = math.dist(a, b)
            nx, nz = -(b[1] - a[1]) / L * 0.004, (b[0] - a[0]) / L * 0.004
            edges[k] = covered((m[0] + nx, m[1] + nz)) and covered((m[0] - nx, m[1] - nz))
    horiz, vert, other, heads = {}, {}, [], []
    for (a, b), inside in edges.items():
        if not inside:
            continue
        if abs(a[1] - b[1]) < 1e-6:
            horiz.setdefault(a[1], []).append(sorted((a[0], b[0])))
        elif abs(a[0] - b[0]) < 1e-6:
            lo, hi = sorted((a[1], b[1]))
            vert.setdefault(round(hi - lo, 4), []).append((a[0], lo))
            heads.append((a[0], lo, hi))
        else:
            other.append((a, b))
    curves = []
    for z, segs in sorted(horiz.items()):
        segs.sort()
        runs = [list(segs[0])]
        for s0, s1 in segs[1:]:
            if s0 <= runs[-1][1] + 1e-6:
                runs[-1][1] = max(runs[-1][1], s1)
            else:
                runs.append([s0, s1])
        for s0, s1 in runs:
            curves.append(g.guide(f"{id_}-bed-{len(curves)}", P(s0, z), P(s1, z)))
    if param:
        for x, lo, hi in sorted(heads):
            curves.append(g.guide(f"{id_}-head-{len(curves)}", P(x, lo), P(x, hi)))
        vert = {}
    for k, (L, feet) in enumerate(sorted(vert.items())):
        g.n(f"{id_}-head-{k}-unit", "line", start=[0, 0, 0], step=[0, 0, L], count=2)
        g.points(f"{id_}-head-{k}-at", [[x, y, z] for x, z in feet])
        curves.append(g.n(f"{id_}-head-{k}", "instance", [f"{id_}-head-{k}-at", f"{id_}-head-{k}-unit"]))
    adj = {}
    for a, b in other:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    used = set()
    for a, b in other:
        if (a, b) in used or (b, a) in used:
            continue
        line = [a, b]
        used.add((a, b))
        for end in (1, 0):
            while True:
                tip = line[-1] if end else line[0]
                nxt = [c for c in adj.get(tip, []) if (tip, c) not in used and (c, tip) not in used]
                if len(adj.get(tip, [])) != 2 or not nxt:
                    break
                used.add((tip, nxt[0]))
                line.append(nxt[0]) if end else line.insert(0, nxt[0])
        curves.append(g.n(f"{id_}-joint-{len(curves)}", "spline", points=[P(*q) for q in line], interpolation="polyline"))
    return _join_many(g, id_, curves) if curves else None


def _join_many(g, id_, ids, chunk=64):
    """A join of any number of inputs (a join node takes a limited number), in chunks."""
    ids = list(ids)
    if len(ids) <= chunk:
        return g.join(id_, *ids)
    parts = [g.join(f"{id_}-{k}", *ids[i:i + chunk]) for k, i in enumerate(range(0, len(ids), chunk))]
    return _join_many(g, id_, parts, chunk) if len(parts) > chunk else g.join(id_, *parts)


def _meander(poly, rng, pitch=0.045, amp=0.012, margin=0.03, y=-0.002):
    """One vermiculation track over a stone face: wiggling rows joined at their ends, kept inside the face."""
    face = _inset(poly, margin)
    if face is None:
        return None
    x0, z0, x1, z1 = _bbox(face)
    pts, row, z = [], 0, z0 + pitch * 0.5
    while z < z1 - pitch * 0.3:
        xs = [x0 + 0.01 + (x1 - x0 - 0.02) * i / 12 for i in range(13)]
        for x in (xs[::-1] if row % 2 else xs):
            q = (x, z + amp * (2 * rng.random() - 1))
            if _inside(q, face):
                pts.append([q[0], y, q[1]])
        z += pitch
        row += 1
    return pts if len(pts) >= 4 else None


# ----------------------------------------------------------------------------------------------- helpers on Graph
def _finish(g, id_, parts, at):
    body = parts[0] if len(parts) == 1 else g.join(id_ + "-parts", *parts)
    return g.xf(id_, body, t=list(at) if at is not None else None)


def _emit(g, id_, shells, details=None, uv=()):
    """One surface node per slot ({slot: fn(g, src) -> id} details applied before the material; slots in
    `uv` get planar UVs in metres on the face plane, x and z)."""
    parts = []
    for k, (slot, sh) in enumerate(shells.items()):
        if not sh.faces:
            continue
        src = sh.node(g, f"{id_}-{k}-{slot}-shell")
        if details and slot in details:
            src = details[slot](g, src)
        if slot in uv:
            src = g.n(f"{id_}-{k}-{slot}-uv", "planarUV", [src], plane="xz", metresPerTile=1.0)
        parts.append(g.mat(f"{id_}-{k}-{slot}", src, slot))
    return parts


def _chamfers(poly, rustic, chamfer, arris):
    """Front-edge insets per edge for rustic smooth|chamfered|banded|vermiculated."""
    poly = _fp(poly)
    n = len(poly)
    if rustic in ("chamfered", "vermiculated"):
        return [chamfer] * n
    if rustic == "banded":
        return [chamfer if abs(poly[i][1] - poly[(i + 1) % n][1]) < 1e-6 else max(arris, 0.003) for i in range(n)]
    assert rustic in ("smooth", None), f"rustic {rustic!r}: smooth, chamfered, banded or vermiculated"
    return [arris] * n if arris else None


def _sliver(p):
    """A piece too thin to lay (a feathered offcut against an arch): left to the mortar."""
    a = _area(p)
    per = sum(_dist(p[i], p[(i + 1) % len(p)]) for i in range(len(p)))
    return a < 6e-4 or 2 * a / per < 0.01


def _groove(rustic, chamfer, gap):
    return (2 * chamfer + gap, chamfer) if rustic not in ("smooth", None) else (max(gap, 0.006), 0.004)


def arch_ring(self, id_, span, kind="semicircular", rise=None, at=None, depth=0.45, voussoir=0.4, count=None,
              length=0.45, short=None, key=True, key_scale=1.2, key_proud=0.0, key_rise=0.0, extrados="plain",
              course=0.35, level=None, impost=None, joints="gap", gap=GAP, rustic="smooth", chamfer=0.02, arris=0.0,
              beta=PI / 3, centre_depth=None, slot="stone", key_slot=None, impost_slot=None, fill_notches=False,
              controls=None, material_slot=None):
    """A free-standing arch ring of voussoirs (in a wall, use an ashlar_wall or brick_wall opening). Metres, Z-up.

    Frame: springing line on z = 0 centred on x = 0 (at `at`), the face on y = 0, the ring running back to
    y = depth. kind semicircular|segmental|three-centred|pointed|flat, rise, count or voussoir width,
    length / short (long-and-short work), key / key_scale, extrados plain|stepped|level with course /
    level: as arch_geometry. key_proud brings the keystone forward of the face, key_rise lifts it. impost:
    (height, projection): a block under each springer reaching 0.08 past it, standing proud.
    Controls: span, rise, length, short, level, centre_depth, key_rise and depth may be X; the stone count
    is taken at the controls' defaults (`controls`, the module's controls dict, or g.controls) and every
    joint is an expression in its index, radial to its centre at every value (stones widen with the span).
    joints "gap": each stone a closed prism `gap` from its neighbours (one piece to pgm audit); "detail":
    one ring slab with joint grooves (proud keystone and imposts stay geometry); "material": the plain ring
    slab with planar UVs in metres and slot `material_slot` (default ashlar-coursed, ashlar-rusticated when
    rustic), proud keystone and imposts as geometry. rustic smooth|chamfered|banded: front arrises
    chamfered `chamfer` at 45 degrees; arris: a small chamfer on smooth stones. Returns the node id.
    Triangles: a 4 m semicircle of 11 stones about 450 (gap), 100 plus grooves (detail).
    Example: g.arch_ring("arch", C("span"), "segmental", rise=C("span") * 0.25, short=0.35, impost=(0.3, 0.06),
                         controls={"span": control(8, 6, 12)})"""
    cv = _controls(self, controls)
    L = lambda v: _lift(v, cv)
    span, rise, length, short, level, centre_depth, key_rise = map(L, (span, rise, length, short, level, centre_depth, key_rise))
    ag = arch_geometry(span, kind, rise, count, voussoir, key, key_scale, length, short, extrados, course, level,
                       key_rise, beta, centre_depth, fill_notches)
    key_slot, impost_slot = key_slot or slot, impost_slot or slot
    shells = {}
    sh = lambda s: shells.setdefault(s, _Shells())
    details, uv = {}, set()
    imposts = []
    if impost:
        xo = max(q[0] for q in ag["outer"]) + 0.08
        for sx in (1, -1):
            x0, x1 = (span * 0.5, xo) if sx > 0 else (-xo, span * -0.5)
            imposts.append([(x0, -impost[0]), (x1, -impost[0]), (x1, 0.0), (x0, 0.0)])
    if joints == "gap":
        for s in ag["stones"]:
            poly = _shrink(s["poly"], gap * 0.5)
            assert poly is not None, f"arch stone {s['index']} too small for the joint gap"
            ch = _chamfers(poly, rustic, chamfer, arris)
            if s["kind"] == "key":
                sh(key_slot).prism(poly, -key_proud, depth, ch)
            else:
                sh(slot).prism(poly, 0.0, depth, ch)
    else:
        assert joints in ("detail", "material"), "joints: gap, detail or material"
        face = slot
        if joints == "material":
            face = material_slot or ("ashlar-coursed" if rustic in ("smooth", None) else "ashlar-rusticated")
            uv.add(face)
        sh(face).slab(ag["outline"], [], 0.0, depth)
        if joints == "detail":
            guides = _joint_curves(self, id_ + "-joint-lines", [s["poly"] for s in ag["stones"]])
            wdt, dep = _groove(rustic, chamfer, gap)
            if guides:
                details[slot] = lambda g, src: g.stroke(id_ + "-grooved", src, guides, profile="groove", width=wdt, depth=dep)
        for s in ag["stones"]:
            if s["kind"] == "key" and key_proud:
                sh(key_slot).prism(s["poly"], -key_proud - 0.0005, -0.0005)
    for p in imposts:
        q = _shrink(p, gap * 0.5)
        sh(impost_slot).prism(q, -impost[1], depth, _chamfers(q, rustic, chamfer, arris) if joints == "gap" else None)
    return _finish(self, id_, _emit(self, id_, shells, details, uv), at)


def _wall(self, id_, width, height, zs, openings, cuts_for, min_len, at, depth, bed, joints, gap, slot, core_slot,
          rustic, chamfer, arris, arch_defaults, unit_h, quoins=None, quoin=(0.75, 0.45), quoin_slot=None,
          quoin_proud=0.0, quoin_rustic=None, seed=1, trim_slot=None, cv=None, material_slot=None):
    """Shared by ashlar_wall and brick_wall. width and height: numbers or _D; zs: course lines at the defaults.
    With expressions (Built by Opus 5.5 (medium)): the stones are laid out at the defaults; course lines then
    follow a piecewise-linear map through the foot, sills, heads, springings, arch crowns and top (Sz), head
    joints one through the wall ends and the openings' sides (Sx), and every vertex on an opening's outline
    is put back on the opening's true (expression) outline."""
    openings = [{k: _lift(v, cv) for k, v in sp.items()} for sp in openings]
    arch_defaults = {k: _lift(v, cv) for k, v in arch_defaults.items()}
    W, H = width, height
    Wf = _f(W)
    X0, X1, X0f, X1f = W * -0.5, W * 0.5, -0.5 * Wf, 0.5 * Wf
    param = isinstance(W, _D) or isinstance(H, _D) or any(isinstance(v, _D) for sp in openings for v in sp.values()) or \
        any(isinstance(v, _D) for v in arch_defaults.values())
    Sz = Sx = None
    if param:
        za = [0.0, H]
        assert not any(sp.get("extrados", arch_defaults.get("extrados")) == "stepped" for sp in openings), \
            "a wall that follows controls takes plain or level arch extrados (stepped tops ride the course lines)"
        for sp in openings:
            for k in ("sill", "head", "springing"):
                if k in sp:
                    v = sp[k]
                    za.append(v + (_snap(_f(v), zs) - _f(v)))
        Sz = _PL(za)
        first = [(sp, o) for sp in openings for o in _opening(sp, [Sz(z) for z in zs], arch_defaults, unit_h)]
        za += [o.zmax for sp, o in first if sp.get("kind") == "arch" and o.hole is not None and
               sp.get("extrados", arch_defaults.get("extrados", "plain")) != "stepped"]
        Sz = _PL(za)
        zs_d = [Sz(z) for z in zs]
    else:
        zs_d = zs
    obstacles = [o for sp in openings for o in _opening(sp, zs_d, arch_defaults, unit_h)]
    ends = {"both": ("left", "right"), "left": ("left",), "right": ("right",), None: ()}[quoins]
    qlen = lambda k: quoin[0] if k % 2 == 0 else quoin[1]
    trim = [s for o in obstacles for s in o.stones]
    for k, (z0, z1) in enumerate(zip(zs_d, zs_d[1:])):
        for e in ends:
            x0, x1 = (X0, X0 + qlen(k)) if e == "left" else (X1 - qlen(k), X1)
            poly = [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
            trim.append({"poly": poly, "kind": "quoin", "proud": quoin_proud})
            obstacles.append(_Obstacle(poly))
    where = None
    if param:
        Sx = _PL([X0, X1] + [v for o in obstacles for v in (o.xmin, o.xmax)])
        reg, edges = {}, []
        key = lambda q: (round(_f(q[0]), 6), round(_f(q[1]), 6))
        for o in obstacles:
            for poly in [o.poly] + [s["poly"] for s in o.stones] + ([o.hole] if o.hole else []):
                for q in poly:
                    reg.setdefault(key(q), q)
            for i in range(len(o.poly)):
                a, b = o.poly[i], o.poly[(i + 1) % len(o.poly)]
                af, bf = _fp((a, b))
                edges.append((a, b, af, bf, min(af[0], bf[0]), max(af[0], bf[0]), min(af[1], bf[1]), max(af[1], bf[1])))
        zline = {round(z, 9): k for k, z in enumerate(zs)}

        def where(q):
            x, z = q
            hit = reg.get(key(q))
            if hit is not None:
                return hit
            k = zline.get(round(z, 9))
            for a, b, af, bf, x0, x1, z0, z1 in edges:
                if not (x0 - 1e-7 <= x <= x1 + 1e-7 and z0 - 1e-7 <= z <= z1 + 1e-7):
                    continue
                dx, dz = bf[0] - af[0], bf[1] - af[1]
                L2 = dx * dx + dz * dz
                t = ((x - af[0]) * dx + (z - af[1]) * dz) / L2
                if not 0 < t < 1 or abs((x - af[0]) * dz - (z - af[1]) * dx) / math.sqrt(L2) > 1e-7:
                    continue
                if k is not None and abs(dz) > 1e-9:          # a course line meeting the outline
                    zz = zs_d[k]
                    return (a[0] + (b[0] - a[0]) * ((zz - a[1]) / (b[1] - a[1])), zz)
                if abs(dx) < 1e-12:
                    return (a[0], zs_d[k] if k is not None else _lerp(a[1], b[1], t))
                return _lerp2(a, b, t)
            return (Sx(x), Sz(z))
        flat = [_Obstacle(_fp(o.poly)) for o in obstacles]
    else:
        flat = obstacles
    stones = [p for k, (z0, z1) in enumerate(zip(zs, zs[1:])) for p in _band(z0, z1, X0f, X1f, flat, cuts_for(k), min_len)]
    if where:
        stones = [[where(q) for q in p] for p in stones]
    holes = [o.hole for o in obstacles if o.hole]
    shells, details, uv = {}, {}, set()
    sh = lambda s: shells.setdefault(s, _Shells())
    tslot = lambda s: (quoin_slot or slot) if s["kind"] == "quoin" else (trim_slot or slot)
    if joints == "gap":
        rng = random.Random(seed)
        tracks = []
        for poly in stones:
            p = _shrink(poly, gap * 0.5)
            if p is None or _sliver(p):
                continue
            sh(slot).prism(p, 0.0, bed, _chamfers(p, rustic, chamfer, arris))
            if rustic == "vermiculated":
                pf = _fp(p)
                m = _meander(_inset(pf, chamfer) or pf, rng)
                if m:
                    tracks.append([[_e(Sx(q[0])), q[1], _e(Sz(q[2]))] for q in m] if param else m)
        for s in trim:
            p = _shrink(_ccw(s["poly"]), gap * 0.5)
            if p is None:
                continue
            quoin_ = s["kind"] == "quoin"
            rr = (quoin_rustic or rustic) if quoin_ else ("smooth" if rustic == "vermiculated" else rustic)
            if rr == "vermiculated":
                rr = "chamfered"
            sh(tslot(s)).prism(p, -s.get("proud", 0.0), depth if quoin_ else bed, _chamfers(p, rr, chamfer, arris))
        dv = _v(depth)
        if not isinstance(dv, (int, float)) or dv > bed + CORE_GAP + 0.005:
            lx = [X0 + (qlen(k) + CORE_GAP if "left" in ends else 0.0) for k in range(len(zs) - 1)]
            rx = [X1 - (qlen(k) + CORE_GAP if "right" in ends else 0.0) for k in range(len(zs) - 1)]
            core = [q for k in range(len(zs) - 1) for q in ((rx[k], zs_d[k]), (rx[k], zs_d[k + 1]))] + \
                   [q for k in reversed(range(len(zs) - 1)) for q in ((lx[k], zs_d[k + 1]), (lx[k], zs_d[k]))]
            outline, rest = _notch(core, holes)
            sh(core_slot).slab(outline, rest, bed + CORE_GAP, depth)
        if tracks:
            for k, t in enumerate(tracks):
                self.n(f"{id_}-worm-{k}", "spline", points=t, interpolation="polyline")
            _join_many(self, f"{id_}-worms", [f"{id_}-worm-{k}" for k in range(len(tracks))])
            details[slot] = lambda g, src: g.stroke(id_ + "-vermiculated", src, f"{id_}-worms", profile="groove", width=0.011, depth=0.005)
    else:
        assert joints in ("detail", "material"), "joints: gap, detail or material"
        face = material_slot if joints == "material" else slot
        outline, rest = _notch([(X0, 0.0), (X1, 0.0), (X1, H), (X0, H)], holes)
        sh(face).slab(outline, rest, 0.0, depth)
        if joints == "detail":
            guides = _joint_curves(self, id_ + "-joint-lines", stones + [_ccw(s["poly"]) for s in trim], where=where)
            wdt, dep = _groove(rustic, chamfer, gap)
            if guides:
                details[slot] = lambda g, src: g.stroke(id_ + "-jointed", src, guides, profile="groove", width=wdt, depth=dep)
            for s in trim:
                if s.get("proud"):
                    sh(tslot(s)).prism(_ccw(s["poly"]), -s["proud"] - 0.0005, -0.0005)
        else:
            uv.add(face)
            for s in trim:                       # every trim stone a facing, so the bond map stops at it
                p = _shrink(_ccw(s["poly"]), gap * 0.5)
                if p is not None:
                    sh(tslot(s)).prism(p, -s.get("proud", 0.0) - FACING, -0.0005)
    return _finish(self, id_, _emit(self, id_, shells, details, uv), at)


def ashlar_wall(self, id_, width, height, at=None, depth=0.5, course=0.35, block=None, bond=0.5, vary=0.0, seed=1,
                rustic="smooth", chamfer=0.02, arris=0.0, openings=(), quoins=None, quoin=(0.75, 0.45), quoin_proud=0.0,
                quoin_rustic=None, quoin_slot=None, joints="gap", gap=GAP, bed=0.25, slot="stone", core_slot=None,
                trim_slot=None, arch=None, min_block=None, controls=None, material_slot=None):
    """A wall panel of coursed ashlar. Metres, Z-up.

    Frame: the face on y = 0 from x = -width/2 to width/2 and z = 0 to height, the wall running back to
    y = depth (number or X). course: a height (equal courses near it) or a list of heights from the bottom
    (diminishing courses). Head joints: blocks `block` long (default 0.75; with joints="material" the slot's
    block, 0.7 coursed or 1.05 rusticated, so the maps fit), each course shifted `bond` of a block (0.5:
    half bond), lengths varied by +-`vary` (seeded; perpends kept a quarter block clear of those below),
    no stone shorter than `min_block` (default 0.4 block) unless an opening forces it. rustic: smooth
    (`arris` chamfer optional), chamfered (V-jointed: every front arris chamfered `chamfer` at 45
    degrees), banded (bed joints only: banded rustication), vermiculated (chamfered, a worm-track groove
    over each face as detail).
    openings: dicts with "x" (centre), "width", "sill" (default 0) and
      kind "rect": "head", "lintel" "stone" (one course, "bearing" 0.15 each side) | "flat" (a jack arch to
          the course nearest head + "arch_height", on skewbacks cut into the jambs) | None; "sill_stone": a
          sill one course deep, "sill_proud" forward;
      kind "arch": "springing", "arch" (an arch_geometry kind) and its options ("rise", "voussoir",
          "count", "length", "short", "extrados" plain|stepped, "key_proud", "key_rise"); "impost": True
          for impost blocks in the course under the springing.
    Sills, heads and springings move to the nearest course line, and the blocks round an opening are cut
    to its jambs, lintel, skewbacks and extrados in every course they meet (a long-and-short ring gets
    packing stones in its notches). `arch` gives defaults for every arch. quoins "left" | "right" | "both":
    full-depth corner stones alternating `quoin` (long, short), `quoin_proud` forward. joints "gap":
    every block a prism `bed` deep on a core (closed, one piece); "detail": one slab with grooves (proud
    trim stays geometry); "material": one plain slab, slot `material_slot` (default "ashlar-coursed",
    "ashlar-rusticated" when rustic is not smooth), planar UVs in metres, trim as facings (the slot's maps are
    baked from bond_pattern(slot) - 0.35 m courses, half bond, the slot's block - and line up with the
    geometry's joints when the wall keeps those and its height is whole courses; Built by Opus 5.5 (medium)).
    Head joints sit on a lattice from x = 0 of the wall's frame.
    Controls: width, height, depth and the openings' x, width, sill, head, springing, rise (and `arch`
    defaults) may be X; give `controls` (the module's controls dict) or set g.controls. Courses and blocks
    are counted at the defaults and stretch with the controls (see the module docstring).
    Returns the node id. Triangles: 8 x 5 m in 0.35 m courses with an arched door (about 160 stones) is
    about 2,500 smooth, 4,000 chamfered, 300 plus grooves as detail.
    Example: g.ashlar_wall("front", C("span") + 3, C("level"), rustic="banded", quoins="both", controls=ctl,
                           openings=[{"kind": "arch", "x": 0, "width": C("span"), "springing": C("level") - C("span") / 2 - 1.2}])"""
    cv = _controls(self, controls)
    width, height = _lift(width, cv), _lift(height, cv)
    Wf = _f(width)
    mslot = material_slot or ("ashlar-coursed" if rustic in ("smooth", None) else "ashlar-rusticated")
    if block is None:
        block = ASHLAR.get(mslot, ASHLAR["ashlar-coursed"])["block"] if joints == "material" else 0.75
    zs = courses_for(_f(height), course)
    rng = random.Random(seed)
    prev = {}

    def cuts(k):
        c = _bond_cuts("ashlar", k, -Wf * 0.5 - block, Wf * 0.5 + block, (block, 0), 0, rng, vary, bond,
                       prev.get(k - 1), 0.25 * block if vary else 0.0)
        prev[k] = c
        return c
    return _wall(self, id_, width, height, zs, openings, cuts, min_block if min_block is not None else 0.4 * block, at, depth,
                 bed, joints, gap, slot, core_slot or slot, rustic, chamfer, arris, dict(arch or {}), zs[1] - zs[0], quoins,
                 quoin, quoin_slot, quoin_proud, quoin_rustic, seed, trim_slot, cv, mslot)


def brick_wall(self, id_, width, height, at=None, depth=0.33, bond="stretcher", brick=(0.215, 0.1025, 0.065), joint=0.01,
               openings=(), soldiers=(), joints="gap", recess=0.012, slot="brick", mortar_slot="concrete", trim_slot=None,
               arch=None, controls=None, material_slot=None):
    """A brick face in a bond. Metres, Z-up; frame as ashlar_wall (face on y = 0, centred on x = 0).

    bond: "stretcher" (half bond), "english" (alternate courses of stretchers and headers, the headers over
    the perpends and centres below), "flemish" (header and stretcher in every course, headers centred on
    the stretchers below), "header" (quarter-bond headers). brick (length, width, height) and joint: a
    course is height + joint. soldiers: course indices laid as soldier courses (bricks on end).
    Openings as ashlar_wall, with brick heads: rect "lintel" "soldier" (a soldier course over the opening),
    "flat" (a gauged flat arch of rubbed bricks fanning to a centre below; "arch_height", default 0.35),
    "stone" or None; kind "arch": a ring of rubbed brick voussoirs (default 0.075 at the soffit, one brick
    length deep, no projecting key). Arch options in `arch` apply to every arch. joints "gap": each brick
    a tile `recess` deep on a mortar core (`mortar_slot`), so the joints read recessed; "detail": one slab
    with joint grooves; "material": one plain slab, slot "brick-" + bond (or `material_slot`), planar UVs in
    metres, trim as facings; the bond is bond_pattern(bond, brick, joint), its cell anchored at x = 0, z = 0
    of the wall's frame, so maps and geometry agree (the maps are baked at the default brick and joint). Controls as ashlar_wall (bricks stretch: keep the
    range modest). Course ends take closers. Returns the node id. Triangles: 4 x 2.5 m stretcher bond
    (about 450 bricks) about 5,500 (gap), 200 plus grooves (detail): walls out of reach want "detail".
    Example: g.brick_wall("leaf", 5, 3, bond="english", openings=[{"kind": "arch", "x": 0, "width": 1.2,
                          "sill": 0.6, "springing": 1.8}])"""
    cv = _controls(self, controls)
    width, height = _lift(width, cv), _lift(height, cv)
    Hf, Wf = _f(height), _f(width)
    Lb, Wb, Hb = brick
    c = Hb + joint
    sol = set(soldiers)
    zs, k = [0.0], 0
    while zs[-1] < Hf - 1e-9:
        zs.append(zs[-1] + (Lb + joint if k in sol else c))
        k += 1
    if zs[-1] > Hf + 1e-9:
        zs[-1] = Hf
        if zs[-1] - zs[-2] < c * 0.5:
            zs.pop(-2)

    def cuts(k):
        if k in sol:
            return _bond_cuts("soldier", k, -Wf * 0.5 - Lb, Wf * 0.5 + Lb, (Lb, Hb), joint)
        return _bond_cuts(bond, k, -Wf * 0.5 - 2 * Lb, Wf * 0.5 + 2 * Lb, (Lb, Wb), joint)
    ad = {"voussoir": Hb + joint, "length": Lb, "key_scale": 1.0, "soldier_pitch": Hb + joint}
    ad.update(arch or {})
    return _wall(self, id_, width, height, zs, openings, cuts, Wb * 0.45, at, depth, recess, joints, joint, slot,
                 mortar_slot, "smooth", 0.0, 0.0, ad, Lb + joint, trim_slot=trim_slot, cv=cv,
                 material_slot=material_slot or bond_pattern(bond)["slot"])


def rubble_wall(self, id_, width, height, at=None, depth=0.6, kind="random", stone=0.35, joint=0.02, batter=0.0,
                seed=3, joints="gap", recess=0.035, bulge=0.025, slot="stone", mortar_slot="concrete", controls=None):
    """A rubble wall or battered toe of irregular stones. Metres, Z-up; frame as ashlar_wall (face on y = 0).

    kind "random": uncoursed random rubble (beds wander, head joints lean, stones of mixed size about
    `stone`); "squared": coursed squared rubble (level beds, courses of random height, square heads).
    joint: mortar joint width; each stone a tile `recess` deep with a pillowed face (`bulge`) and dressed-
    off corners, on a mortar core to y = depth. batter: the face leans back `batter` metres per metre of
    rise, so the foot stands height * batter forward of the top (a quay or isle wall toe); the back stays
    vertical. joints "detail": one slab with grooves. Returns the node id. Triangles: 4 x 1.5 m random
    rubble (about 50 stones) about 3,000. width and height may be X (`controls` as ashlar_wall): the stones
    are laid at the defaults and stretch with the controls.
    Example: g.rubble_wall("toe", 6, 1.2, batter=0.25, depth=0.9, slot="pier-ashlar")"""
    assert kind in ("random", "squared"), "rubble kind: random or squared"
    cv = _controls(self, controls)
    W, H = _lift(width, cv), _lift(height, cv)
    width, height = _f(W), _f(H)
    param = isinstance(W, _D) or isinstance(H, _D)
    Sx, Sz = _PL([W * -0.5, W * 0.5]), _PL([0.0, H])
    rng = random.Random(seed)
    X0, X1 = width * -0.5, width * 0.5
    rough = kind == "random"
    beds, z = [0.0], 0.0
    while z < height - 1e-9:
        z += stone * (0.45 + 0.6 * rng.random()) if rough else stone * rng.choice((0.55, 0.75, 1.0))
        beds.append(min(z, height))
    if len(beds) > 2 and beds[-1] - beds[-2] < stone * 0.3:
        beds.pop(-2)
    beds[-1] = height
    rows = []                                       # head joints per row: (x on the bed below, x on the bed above)
    for r in range(len(beds) - 1):
        heads, x = [], X0
        while True:
            x += stone * (0.6 + rng.random()) * (1.0 if rough else 1.3)
            if x > X1 - stone * 0.35:
                break
            lean = (rng.random() - 0.5) * (beds[r + 1] - beds[r]) * 0.5 if rough else 0.0
            heads.append((x - lean * 0.5, x + lean * 0.5))
        rows.append(heads)
    knots = []                                      # bed lines through every head joint meeting them, jittered
    for i, zb in enumerate(beds):
        xs = {X0, X1}
        if i > 0:
            xs |= {t for _, t in rows[i - 1]}
        if i < len(rows):
            xs |= {b for b, _ in rows[i]}
        amp = 0.18 * min(beds[i] - beds[i - 1], beds[i + 1] - beds[i]) if rough and 0 < i < len(beds) - 1 else 0.0
        base = (rng.random() - 0.5) * amp
        knots.append([(x, zb + base + amp * (rng.random() - 0.5)) for x in sorted(xs)])
    polys = []
    for r, heads in enumerate(rows):
        cols = [(X0, X0)] + heads + [(X1, X1)]
        for (ba, ta), (bb, tb) in zip(cols, cols[1:]):
            bottom = [q for q in knots[r] if ba - 1e-9 <= q[0] <= bb + 1e-9]
            top = [q for q in knots[r + 1] if ta - 1e-9 <= q[0] <= tb + 1e-9][::-1]
            polys.append(_ccw(bottom + top))
    shells = {slot: _Shells(), mortar_slot: _Shells()}
    details = {}
    outline = [(X0, 0.0), (X1, 0.0), (X1, height), (X0, height)]
    if joints == "gap":
        for p in polys:
            q = _inset(_simplify(p, joint * 2.5), joint * 0.5)
            if q is None:
                continue
            q = _round_corners(q, joint * 1.2)
            shells[slot].prism(_clean(q), 0.0, recess, bulge=bulge * (0.6 + 0.8 * rng.random()))
        shells[mortar_slot].slab(outline, [], recess + CORE_GAP, depth)
    else:
        assert joints == "detail", "joints: gap or detail"
        shells[slot].slab(outline, [], 0.0, depth)
        guides = _joint_curves(self, id_ + "-joint-lines", polys,
                               where=(lambda q: (Sx(q[0]), Sz(q[1]))) if param else None)
        if guides:
            details[slot] = lambda g, src: g.stroke(id_ + "-jointed", src, guides, profile="groove", width=joint, depth=0.01)
    dv = _v(depth)
    for sh in shells.values():                      # follow the controls; lean the face back (the back face stays put)
        for v in sh.verts:
            z = Sz(v[2]) if param else v[2]
            if batter and isinstance(v[1], (int, float)) and not (isinstance(dv, (int, float)) and v[1] >= dv - 1e-9):
                v[1] = _e(v[1] - batter * (H - z))
            if param:
                v[0], v[2] = _e(Sx(v[0])), _e(z)
    return _finish(self, id_, _emit(self, id_, shells, details), at)


# ----------------------------------------------------------------------------------------------- copings and caps
def _coping_profile(kind, width, height, overhang, fall, bead):
    """(y, z) profile across a wall top from y = -overhang to width + overhang."""
    y0, y1 = -overhang, width + overhang
    if kind == "flat":
        return [(y0, 0), (y1, 0), (y1, height), (y0, height)]
    if kind == "weathered":                      # once-weathered: falls to the face side
        return [(y0, 0), (y1, 0), (y1, height), (y0, height - fall)]
    if kind == "saddleback":                     # twice-weathered: a ridge on the centre line
        m = (y0 + y1) * 0.5
        return [(y0, 0), (y1, 0), (y1, height - fall), (m, height), (y0, height - fall)]
    if kind == "half-round":                     # a half-round over the coping's width on a short upstand
        r = (y1 - y0) * 0.5
        m = (y0 + y1) * 0.5
        base = max(height - r, 0.02)
        return [(y0, 0), (y1, 0)] + [(m + r * math.cos(PI * k / 12), base + r * math.sin(PI * k / 12)) for k in range(13)]
    if kind == "bullnose":                       # both top arrises rounded to radius `bead`
        r = min(bead, height * 0.9, (y1 - y0) * 0.45)
        arc = lambda cy, cz, a0: [(cy + r * math.cos(a0 + PI / 2 * k / 6), cz + r * math.sin(a0 + PI / 2 * k / 6)) for k in range(7)]
        return [(y0, 0), (y1, 0)] + arc(y1 - r, height - r, 0.0) + arc(y0 + r, height - r, PI / 2)
    raise ValueError(f"coping kind {kind!r}: flat, weathered, saddleback, half-round or bullnose")


def _string_profile(kind, projection, height, embed):
    """(y, z) profile of a string course: face at y = 0, projecting to y = -projection, bedded to y = embed."""
    y0 = -projection
    if kind == "plain":
        return [(y0, 0), (embed, 0), (embed, height), (y0, height)]
    if kind == "weathered":                      # sloped top shedding water off the face
        return [(y0, 0), (embed, 0), (embed, height), (0.0, height), (y0, height - projection * 0.5)]
    if kind == "bead":                           # a half-round nosing
        r = min(height * 0.5, projection)
        return [(embed, 0), (embed, height), (y0 + r, height)] + \
               [(y0 + r - r * math.sin(PI * k / 10), height * 0.5 + r * math.cos(PI * k / 10)) for k in range(1, 10)] + [(y0 + r, 0)]
    raise ValueError(f"string course kind {kind!r}: plain, weathered or bead")


def _profile_run(self, id_, prof, length, stone, joints, gap, at, slot, drip=None):
    """A (y, z) profile run along x from -length/2 to length/2 in stones `stone` long."""
    prof = _ccw(prof)
    sh = _Shells()
    n = max(1, int(round(_f(length) / stone))) if joints == "gap" else 1
    for k in range(n):
        x0 = -length * 0.5 + length * k / n + (gap * 0.5 if k else 0)
        x1 = -length * 0.5 + length * (k + 1) / n - (gap * 0.5 if k < n - 1 else 0)
        sh.prism(prof, x0, x1)
    sh.swap_xy()
    src = sh.node(self, id_ + "-shell")
    if drip is not None:                         # throat under the overhang: a groove along the run (detail)
        yd, zd = drip
        self.guide(id_ + "-drip-line", [_e(-length * 0.5 + 0.03), yd, zd - 0.002], [_e(length * 0.5 - 0.03), yd, zd - 0.002])
        src = self.stroke(id_ + "-dripped", src, id_ + "-drip-line", profile="groove", width=0.012, depth=0.008)
    body = self.mat(id_ + "-stone", src, slot)
    return self.xf(id_, body, t=list(at) if at is not None else None)


def coping(self, id_, length, width, kind="weathered", height=0.15, overhang=0.05, fall=0.04, bead=0.04, stone=0.9,
           joints="gap", gap=GAP, drip=True, at=None, slot="stone", controls=None):
    """A coping along a wall top. Metres, Z-up.

    Frame: the run along x from -length/2 to length/2; the wall top on z = 0 between y = 0 (its face) and
    y = width; the coping overhangs `overhang` both sides. kind: "flat", "weathered" (falls `fall` toward
    the face side), "saddleback" (twice weathered to a centre ridge), "half-round", "bullnose" (top
    arrises rounded to `bead`). Stones `stone` long a `gap` apart (joints "gap") or one piece ("detail").
    drip: a throat groove under the face-side overhang (detail). Triangles: 10 m of saddleback in 0.9 m
    stones about 180. length may be X (stones counted at the default, `controls` as ashlar_wall).
    Example: g.coping("cope", 12.0, 0.45, "saddleback", at=[0, 0, C("height")])"""
    prof = _coping_profile(kind, width, height, overhang, fall, bead)
    return _profile_run(self, id_, prof, _lift(length, _controls(self, controls)), stone, joints, gap, at, slot,
                        drip=(-overhang * 0.5, 0.0) if drip and overhang >= 0.03 else None)


def string_course(self, id_, length, kind="weathered", projection=0.08, height=0.2, embed=0.1, stone=0.9,
                  joints="gap", gap=GAP, drip=True, at=None, slot="stone", controls=None):
    """A string course (a projecting band) along a wall face. Metres, Z-up.

    Frame: the run along x from -length/2 to length/2, the wall face on y = 0, the band's bed on z = 0; it
    projects `projection` forward (to y = -projection) and is bedded `embed` into the wall. kind "plain",
    "weathered" (top sloped off the face), "bead" (a half-round nosing). drip: a throat under the
    projection (detail). length may be X, as coping. Example: g.string_course("band", 10, at=[0, 0, 3.2])"""
    prof = _string_profile(kind, projection, height, embed)
    return _profile_run(self, id_, prof, _lift(length, _controls(self, controls)), stone, joints, gap, at, slot,
                        drip=(-projection * 0.55, 0.0) if drip and projection >= 0.05 and kind != "bead" else None)


def cutwater_cap(self, id_, width, nose=None, kind="pointed", thickness=0.12, rise=0.35, overhang=0.06, at=None,
                 slot="stone"):
    """The weathered cap on a pier's cutwater (the pointed or rounded nose that parts the stream). Metres, Z-up.

    Frame: the cutwater's root (the pier side it stands on) is x = 0, it points +x, centred on y = 0,
    `width` wide at the root and `nose` long (default 0.6 width; width/2 when rounded). kind "pointed" (two
    arcs meeting at the nose, square to the root), "triangular" or "rounded". The cap overhangs the
    cutwater by `overhang`, has an edge `thickness` high from z = 0, and its top falls from `rise` above
    the edge at the pier face to the edge all round: a half-pyramid or half-cone hood. One closed solid,
    about 70 triangles. Example: g.cutwater_cap("cap", 2.4, kind="pointed", at=[5.0, 0, 3.0])"""
    w = width * 0.5 + overhang
    nose = (width * 0.6 if kind != "rounded" else width * 0.5) if nose is None else nose
    nz = nose + overhang
    if kind == "triangular":
        outer = [(0.0, -w), (nz, 0.0), (0.0, w)]
    elif kind == "rounded":
        outer = [(nz * math.cos(a), w * math.sin(a)) for a in [-PI / 2 + PI * k / 16 for k in range(17)]]
    elif kind == "pointed":
        r = (w * w + nz * nz) / (2 * w)          # arcs from (0, -+w), square to the root, meeting at the nose
        a1 = math.atan2(nz, r - w)
        half = [(r * math.sin(a1 * k / 8), -w + r - r * math.cos(a1 * k / 8)) for k in range(9)]
        half[-1] = (nz, 0.0)
        outer = half + [(x, -y) for x, y in reversed(half[:-1])]
    else:
        raise ValueError(f"cutwater kind {kind!r}: pointed, triangular or rounded")
    verts, faces = [], []

    def vid(x, y, z):
        verts.append([x, y, z])
        return len(verts) - 1
    lo = [vid(x, y, 0.0) for x, y in outer]
    hi = [vid(x, y, thickness) for x, y in outer]
    apex = vid(0.0, 0.0, thickness + rise)
    faces.append(lo[::-1])
    for i in range(len(outer) - 1):
        faces.append([lo[i], lo[i + 1], hi[i + 1], hi[i]])
        faces.append([hi[i], hi[i + 1], apex])
    faces.append([lo[-1], lo[0], hi[0], apex, hi[-1]])       # back, on the pier face (x = 0)
    vol = 0.0
    for f in faces:
        for k in range(1, len(f) - 1):
            a, b, c = verts[f[0]], verts[f[k]], verts[f[k + 1]]
            vol += a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])
    if vol < 0:
        faces = [f[::-1] for f in faces]
    self.n(id_ + "-shell", "surface", vertices=verts, faces=faces)
    self.mat(id_ + "-stone", id_ + "-shell", slot)
    return self.xf(id_, id_ + "-stone", t=list(at) if at is not None else None)


kitlib.Graph.arch_ring = arch_ring
kitlib.Graph.ashlar_wall = ashlar_wall
kitlib.Graph.brick_wall = brick_wall
kitlib.Graph.rubble_wall = rubble_wall
kitlib.Graph.coping = coping
kitlib.Graph.string_course = string_course
kitlib.Graph.cutwater_cap = cutwater_cap
