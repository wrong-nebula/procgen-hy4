"""Structural iron helpers for kit builders: rolled sections, built-up members, riveted trusses, plate
girders and their connections. Built by Opus 5.5 (medium), 2026-11-16.

kitlib.py imports this module at its end, so builders get the helpers as `Graph` methods:

    from kitlib import Graph, C, PI, iron_section, mitre
    g.rolled("joist", "I200", [0, 0, 3], [6, 0, 3])                       # rolled I between two points
    tr, info = g.truss("roof", 18.0, 2.4, 8, kind="pratt")                # riveted truss with gussets

Practice followed (19th-century French and Belgian ironwork: the Eiffel plates, the Halles, viaducts):
members are rolled sections (fers a double T, cornieres, fers en U, fers a T, plats, ronds) or built up
from them with rivets. Truss chords and web members are pairs of angles (or flats) back to back with
the gusset plate in the gap between them; laced columns are four angles tied by flat lacing bars with
tie plates at the ends; plate girders are a web plate, four flange angles and cover plates, stiffened
by pairs of angles on filler plates. Rivets are surface detail (procgen/docs/DETAILING.md: out of reach
a rivet is detail); every plate and member is geometry and overlaps its neighbours, so `pgm audit`
sees one piece.

Metres and radians, Z-up. Members are closed `surface` prisms with flat (square or mitred) ends. Sizes
come from `iron_section` (a small table of period-plausible rolled sizes, or a custom dict).

Module controls (Built by Opus 5.5 (medium), 2026-11-16): every helper takes X expressions for its
dimensions. rolled(), box_member() and splice_plate() build straight from them. truss(), plate_girder(),
laced_column() and gusset_plate() also take X for span, depth, rise, soffit, `at`, end points, widths and
member directions; pass the module's controls dict as `controls=`. They work each dimension out twice: at
the controls' defaults, to choose counts, trims and which corners bound a gusset, and as an expression
for every vertex. Members, gussets and chords therefore stay in contact across the control ranges.
Counts (panels, stiffeners, lacing bars, rivets) are plain ints, or come from a pitch at the DEFAULT
value (panel_pitch=, stiffener_pitch=, batten_pitch=, incline=); spacing then stretches with the control.
Tests, a control-driven shed bay and a sample sheet: recipes/test_kitlib_iron.py.

    g.truss("roof", C("span"), C("ridge") - C("eave"), 8, kind="pratt-pitched", at=[0, 0, C("eave")],
            controls=controls)

    rolled        Graph.rolled (I, H, U, L, T, flat, round between two points; up / roll; mitred ends)
    built-up      Graph.laced_column (4 angles laced single / double or battened; 2 angles with packings),
                  Graph.box_member (plates and angles)
    trusses       Graph.truss (warren, pratt, howe, lattice, bowstring; roof trusses pratt-pitched,
                  howe-pitched, fink; gussets; rivets) -> (id, info)
    girders       Graph.plate_girder (web, flange angles, covers, stiffeners, arched soffit) -> (id, info)
    connections   Graph.gusset_plate, Graph.base_plate, Graph.splice_plate
    functions     iron_section(spec), iron_outline(spec), mitre(d_in, d_out)
"""
import math
import re

import kitlib
from kitlib import X, Graph, _num, _v, add, cos, mul, sin, sqrt, sub

__all__ = ["iron_section", "iron_outline", "mitre", "SECTIONS"]

# --- section table -------------------------------------------------------------------------------------
# Rolled sizes in metres. I and U follow the German Normalprofile of the 1880s (the Burbach and Dudelange
# mills sold the same series in France and Belgium); angles, tees and flats are common merchant sizes.
# H (broad flange) came after 1900 (Grey mill): kept for heavy columns, but not period for the 1880s.
SECTIONS = {
    # I: depth h, flange width b, web tw, flange tf
    "I100": dict(kind="I", h=0.100, b=0.050, tw=0.0045, tf=0.0068),
    "I120": dict(kind="I", h=0.120, b=0.058, tw=0.0051, tf=0.0077),
    "I160": dict(kind="I", h=0.160, b=0.074, tw=0.0063, tf=0.0095),
    "I200": dict(kind="I", h=0.200, b=0.090, tw=0.0075, tf=0.0113),
    "I260": dict(kind="I", h=0.260, b=0.113, tw=0.0094, tf=0.0141),
    "I300": dict(kind="I", h=0.300, b=0.125, tw=0.0108, tf=0.0162),
    "I400": dict(kind="I", h=0.400, b=0.155, tw=0.0144, tf=0.0216),
    "H200": dict(kind="I", h=0.200, b=0.200, tw=0.0090, tf=0.0150),
    "H300": dict(kind="I", h=0.300, b=0.300, tw=0.0110, tf=0.0190),
    # U (channel): depth h, flange b, web tw, flange tf; flanges point +u
    "U80": dict(kind="U", h=0.080, b=0.045, tw=0.006, tf=0.008),
    "U100": dict(kind="U", h=0.100, b=0.050, tw=0.006, tf=0.0085),
    "U140": dict(kind="U", h=0.140, b=0.060, tw=0.007, tf=0.010),
    "U200": dict(kind="U", h=0.200, b=0.075, tw=0.0085, tf=0.0115),
    "U260": dict(kind="U", h=0.260, b=0.090, tw=0.010, tf=0.014),
    "U300": dict(kind="U", h=0.300, b=0.100, tw=0.010, tf=0.016),
    # T: depth h (flange to the toe of the stem), flange b, thickness t
    "T50": dict(kind="T", h=0.050, b=0.050, t=0.006),
    "T60": dict(kind="T", h=0.060, b=0.060, t=0.007),
    "T80": dict(kind="T", h=0.080, b=0.080, t=0.009),
    "T100": dict(kind="T", h=0.100, b=0.100, t=0.011),
}
ANGLES = ("L50x6", "L60x8", "L70x9", "L80x10", "L90x11", "L100x12", "L120x12", "L150x15", "L100x65x9", "L120x80x10")


def iron_section(spec):
    """A section as a dict of its kind and sizes in metres. spec: a table name ("I200", "U140", "T80",
    "H200"; see SECTIONS), a parsed name in millimetres ("L80x10" equal angle, "L100x65x9" unequal: leg
    along u, leg along w, thickness; "FL100x10" flat, width x thickness; "PL300x12" plate, the same;
    "RD30" round of 30 mm diameter), or a dict: kind I/U with h, b, tw, tf; T with h, b, t; L with a, b, t;
    FL with b, t; RD with d (dict sizes may be X). Example: iron_section("L80x10") -> kind L, a = b = 0.08, t = 0.01"""
    if isinstance(spec, dict):
        s = dict(spec)
        assert s.get("kind") in ("I", "U", "T", "L", "FL", "RD"), f"section kind: {spec!r}"
        if s["kind"] == "L" and "b" not in s:
            s["b"] = s["a"]
        return s
    if spec in SECTIONS:
        return dict(SECTIONS[spec])
    num = r"(\d+(?:\.\d+)?)"
    m = re.fullmatch(rf"L{num}x{num}(?:x{num})?", spec)
    if m:
        a, b, t = m.groups()
        if t is None:
            a, b, t = a, a, b
        return dict(kind="L", a=float(a) / 1000, b=float(b) / 1000, t=float(t) / 1000)
    m = re.fullmatch(rf"(?:FL|PL){num}x{num}", spec)
    if m:
        return dict(kind="FL", b=float(m.group(1)) / 1000, t=float(m.group(2)) / 1000)
    m = re.fullmatch(rf"RD{num}", spec)
    if m:
        return dict(kind="RD", d=float(m.group(1)) / 1000)
    raise AssertionError(f"unknown iron section {spec!r} (table: {sorted(SECTIONS)}, or L80x10, FL100x10, RD30)")


def iron_outline(spec, sides=12):
    """The section's outline: a counter-clockwise loop of (u, w) in metres. I, U, T, FL and RD are centred
    on their bounding box (U: web at -u, flanges toward +u; T: flange at +w, stem down; FL: width along u,
    thickness along w; RD: `sides` flats). L has its heel at the origin, leg a along +u and leg b along +w.
    Example: iron_outline("I200") -> 12 (u, w) points"""
    s = iron_section(spec)
    k = s["kind"]
    if k == "I":
        h, b, tw, tf = (X(s[n]) for n in ("h", "b", "tw", "tf"))
        H, B, W = h * 0.5, b * 0.5, tw * 0.5
        pts = [(-B, -H), (B, -H), (B, -H + tf), (W, -H + tf), (W, H - tf), (B, H - tf), (B, H), (-B, H), (-B, H - tf),
               (-W, H - tf), (-W, -H + tf), (-B, -H + tf)]
    elif k == "U":
        h, b, tw, tf = (X(s[n]) for n in ("h", "b", "tw", "tf"))
        H, B = h * 0.5, b * 0.5
        pts = [(-B, -H), (B, -H), (B, -H + tf), (-B + tw, -H + tf), (-B + tw, H - tf), (B, H - tf), (B, H), (-B, H)]
    elif k == "T":
        h, b, t = (X(s[n]) for n in ("h", "b", "t"))
        H, B, W = h * 0.5, b * 0.5, t * 0.5
        pts = [(-W, -H), (W, -H), (W, H - t), (B, H - t), (B, H), (-B, H), (-B, H - t), (-W, H - t)]
    elif k == "L":
        a, b, t = (X(s[n]) for n in ("a", "b", "t"))
        pts = [(0, 0), (a, 0), (a, t), (t, t), (t, b), (0, b)]
    elif k == "FL":
        b, t = X(s["b"]) * 0.5, X(s["t"]) * 0.5
        pts = [(-b, -t), (b, -t), (b, t), (-b, t)]
    else:
        r = X(s["d"]) * 0.5
        n = max(6, int(sides))
        pts = [(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return [(_v(X(u)), _v(X(w))) for u, w in pts]


# --- vectors (numbers or X) ----------------------------------------------------------------------------

def _p(a):
    return [X(c) for c in a]


def _vadd(a, b): return [add(x, y) for x, y in zip(a, b)]
def _vsub(a, b): return [sub(x, y) for x, y in zip(a, b)]
def _vmul(a, k): return [mul(x, k) for x in a]
def _dot(a, b): return add(add(mul(a[0], b[0]), mul(a[1], b[1])), mul(a[2], b[2]))


def _cross(a, b):
    return [sub(mul(a[1], b[2]), mul(a[2], b[1])), sub(mul(a[2], b[0]), mul(a[0], b[2])), sub(mul(a[0], b[1]), mul(a[1], b[0]))]


def _unit(a):
    return _vmul(a, kitlib.div(1, sqrt(_dot(a, a))))


def _isnum(vec):
    return all(_num(_v(c)) for c in vec)


def _out(vec):
    return [_v(c) for c in vec]


def mitre(d_in, d_out):
    """Normal of the mitre plane where a member arriving along d_in meets one leaving along d_out (the
    plane halving the bend); pass it as an end of rolled(). Example: mitre((1, 0, 1), (1, 0, 0))"""
    return _out(_vadd(_unit(_p(d_in)), _unit(_p(d_out))))


def _frame(a, b, up, roll):
    """(a, b, axis unit, side, up) for a member a -> b; side = up x axis, so (side, up, axis) is right-handed."""
    a, b = _p(a), _p(b)
    axis = _unit(_vsub(b, a))
    u = _p(up if up is not None else (0, 0, 1))
    if _isnum(axis) and _isnum(u):
        ax, uu = _out(axis), _out(u)
        if abs(sum(x * y for x, y in zip(ax, uu))) / max(1e-12, math.sqrt(sum(x * x for x in uu))) > 0.999:
            u = _p((1, 0, 0) if abs(ax[0]) < 0.9 else (0, 1, 0))
    u = _unit(_vsub(u, _vmul(axis, _dot(axis, u))))
    side = _cross(u, axis)
    r = _v(X(roll))
    if not (_num(r) and r == 0):
        c, s = cos(roll), sin(roll)
        side, u = _vadd(_vmul(side, c), _vmul(u, s)), _vsub(_vmul(u, c), _vmul(side, s))
    return a, b, axis, side, u


def _prism(g, id_, outline, a, b, axis, side, up, ends=(None, None), slot=None, hand=1, ref=None):
    """A closed prism of the (u, w) outline swept from a to b in the frame (side, up, axis). An end is None
    (square) or the normal of its cut plane through that end point. A left-handed frame reverses the
    outline so the shell stays outward: found from the numbers, or `hand` (+1 / -1) when the frame is X."""
    loop = list(outline)
    if _isnum(axis) and _isnum(side) and _isnum(up):
        hand = 1 if _v(_dot(_cross(_p(side), _p(up)), _p(axis))) > 0 else -1
    if hand < 0:
        loop.reverse()
        ref = list(reversed(ref)) if ref else None
    a, b, axis, side, up = _p(a), _p(b), _p(axis), _p(side), _p(up)
    verts = []
    for end, P in ((ends[0], a), (ends[1], b)):
        n = _p(end) if end is not None else None
        for uu, ww in loop:
            o = _vadd(_vmul(side, uu), _vmul(up, ww))
            q = _vadd(P, o)
            if n is not None:
                q = _vadd(q, _vmul(axis, kitlib.div(sub(0, _dot(o, n)), _dot(axis, n))))
            verts.append(_out(q))
    L = len(loop)
    # Caps as triangles: pgm reads vertices as float32 and checks a polygon's flatness against its own size,
    # which a cap far from the origin fails; triangles are taken as given.
    tris = _ear_clip(loop if all(_num(c) for p in loop for c in p) else _ref_loop(loop, ref))
    faces = [[c, b_, a_] for a_, b_, c in tris] + [[L + a_, L + b_, L + c] for a_, b_, c in tris]
    for i in range(L):
        j = (i + 1) % L
        faces.append([i, j, L + j, L + i])
    part = g.n(id_ + "-shell" if slot else id_, "surface", vertices=verts, faces=faces)
    return g.mat(id_, part, slot) if slot else part


def _join(g, id_, *ids):
    """join, nested in groups of 100 (a join node takes at most 128 inputs)."""
    ids = list(ids)
    if len(ids) <= 120:
        return g.join(id_, *ids)
    groups = [g.join(f"{id_}-{k}", *ids[i:i + 100]) for k, i in enumerate(range(0, len(ids), 100))]
    return g.join(id_, *groups)


def _ear_clip(loop):
    """Triangles (index triples, wound like the loop) of a simple polygon."""
    n = len(loop)
    if sum(loop[i][0] * loop[(i + 1) % n][1] - loop[(i + 1) % n][0] * loop[i][1] for i in range(n)) < 0:
        return [(n - 1 - c, n - 1 - b, n - 1 - a) for a, b, c in _ear_clip(list(reversed(loop)))]
    idx = list(range(len(loop)))
    if len(idx) <= 4 and _convex(loop):
        return [(0, k, k + 1) for k in range(1, len(idx) - 1)]
    out = []

    def cr(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = loop[i0], loop[i1], loop[i2]
            if cr(a, b, c) <= 1e-14:
                continue
            if any(cr(a, b, loop[j]) >= 0 and cr(b, c, loop[j]) >= 0 and cr(c, a, loop[j]) >= 0
                   for j in idx if j not in (i0, i1, i2)):
                continue
            out.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            raise AssertionError("section outline is not a simple counter-clockwise polygon")
    out.append(tuple(idx))
    return out


def _convex(loop):
    n = len(loop)
    return all((loop[(i + 1) % n][0] - loop[i][0]) * (loop[(i + 2) % n][1] - loop[i][1]) -
               (loop[(i + 1) % n][1] - loop[i][1]) * (loop[(i + 2) % n][0] - loop[i][0]) > 0 for i in range(n))


def _ref_loop(loop, ref):
    """A numeric stand-in of the same shape for an outline written with X sizes (same topology)."""
    if ref is not None:
        return ref
    assert len(loop) == 4, "an X outline other than a flat needs a numeric reference outline"
    return [(-1, -1), (1, -1), (1, 1), (-1, 1)]


_REF = {"I": "I200", "U": "U140", "T": "T80", "L": "L80x10", "FL": "FL100x10", "RD": "RD30"}


def _plate(g, id_, poly, origin, e1, e2, thickness, slot=None):
    """A flat plate: the polygon (counter-clockwise in e1, e2 round origin) thickened along e1 x e2, centred."""
    e1, e2 = _p(e1), _p(e2)
    n = _cross(e1, e2)
    half = _vmul(n, X(thickness) * 0.5)
    o = _p(origin)
    return _prism(g, id_, poly, _vsub(o, half), _vadd(o, half), n, e1, e2, slot=slot)


# --- rolled sections -----------------------------------------------------------------------------------

def _symbolic(*vals):
    """True when any value (nested in lists, tuples or section dicts) is an expression."""
    for v in vals:
        if isinstance(v, (list, tuple)):
            if _symbolic(*v):
                return True
        elif isinstance(v, dict):
            if _symbolic(*v.values()):
                return True
        elif v is not None and not isinstance(v, str) and not _num(_v(v)):
            return True
        elif isinstance(v, str) and v.startswith("$"):
            return True
    return False


def _nframe(a, b, up, roll):
    """(axis, side, up) of _frame worked out at the controls' defaults (numbers)."""
    an, bn = [_n(c) for c in a], [_n(c) for c in b]
    upn = None if up is None else [_n(c) for c in up]
    _, _, axis, side, u = _frame(an, bn, upn, _n(roll))
    return _out(axis), _out(side), _out(u)


def rolled(self, id_, section, a, b, up=(0, 0, 1), roll=0.0, offset=(0.0, 0.0), ends=(None, None), sides=12, slot=None,
           controls=None):
    """A rolled section swept straight from point a to point b: one closed prism. Metres, radians.

    Frame: the member axis runs a -> b; `up` (default +Z; +X when the member is vertical) is the
    section's w direction, made square to the axis; u = up x axis; `roll` turns the section about the
    axis. I/U/T/flat/round outlines are centred on the line a-b, an angle has its heel on it (see
    iron_outline); `offset` (du, dw) shifts the section off the line. ends (start, end): None for a square
    cut or the normal of the cut plane through that end point (mitre(d_in, d_out) gives a bend's mitre).
    section: a name for iron_section ("I200", "U140", "L80x10", "T80", "FL100x10", "RD30") or a dict.
    Points, sizes, offset and roll may be X. Pass the module's controls dict as `controls=` with them (Built by
    Opus 5.5 (medium)): the end points and section offsets are then exact expressions while the frame (axis,
    up, side), the end-cut planes and the caps are worked out at the defaults, so the document stays small
    (a section tilts a little away from the defaults; the member's ends stay on a and b; roll stays exact). Without
    controls= an expression call keeps the older exact frame, whose document grows to megabytes.
    Triangles: I 44, U and T 28, L 20, flat 12, round 4 * sides - 4.
    Example: g.rolled("beam", "I260", [0, 0, 4], [C("span"), 0, 4], controls=controls)"""
    if controls is not None and _symbolic(a, b, up, roll, offset, ends, section):
        _setenv(controls)
        axis, side, u = _nframe(_qv(a), _qv(b), None if up is None else _qv(up), 0.0)
        du, dw = _qq(offset[0]), _qq(offset[1])
        c, s = qcos(roll), qsin(roll)                 # roll stays exact: it turns the outline in the section plane
        loop = []
        for p, q in iron_outline(section, sides):
            p, q = _qq(p) + du, _qq(q) + dw
            loop.append((p * c - q * s, p * s + q * c))
        e = tuple(None if n is None else [_n(c) for c in n] for n in ends)
        return _qprism(self, id_, loop, a, b, axis, side, u, e, slot)
    a, b, axis, side, u = _frame(a, b, up, roll)
    du, dw = offset
    loop = [(_v(X(p) + du), _v(X(q) + dw)) for p, q in iron_outline(section, sides)]
    ref = iron_outline(_REF[iron_section(section)["kind"]], sides)
    return _prism(self, id_, loop, a, b, axis, side, u, ends, slot, ref=ref)


# --- rivets --------------------------------------------------------------------------------------------

RIVET = dict(radius=0.014, height=0.009)     # snap head of a 20 mm rivet, out of reach: detail


def _rivet(g, id_, part, pts, rivet):
    """Rivet heads (surface detail) at points lying a few mm off the faces of `part`."""
    if not pts:
        return part
    r = dict(RIVET)
    r.update(rivet or {})
    def c6(c):                                     # numbers rounded to the micrometre; expressions kept
        c = _x(c)
        return round(float(c), 6) if _num(_v(c)) else c
    singles, rows, k = [], [], 0
    while k < len(pts):                            # an evenly spaced run of a _qrow row: one line node
        p, j = pts[k], k + 1

        def same(q):
            return isinstance(q, _RowPt) and q.row is p.row
        if isinstance(p, _RowPt) and not all(_num(_x(c)) for c in p) and j < len(pts) and same(pts[j]):
            dt0 = pts[j].t - p.t
            while j < len(pts) and same(pts[j]) and abs(pts[j].t - pts[j - 1].t - dt0) < 1e-9:
                j += 1
        if j - k >= 3:
            dt = pts[k + 1].t - p.t
            rows.append((p, [_x(c * dt) for c in p.d], j - k))
        else:
            singles += pts[k:j]
        k = j
    if not rows:
        g.points(id_ + "-at", [[c6(c) for c in p] for p in pts])
    else:
        ids = [g.n(f"{id_}-row{i}", "line", start=[c6(c) for c in p], step=step, count=n) for i, (p, step, n) in enumerate(rows)]
        if singles:
            ids.append(g.points(id_ + "-pts", [[c6(c) for c in p] for p in singles]))
        _join(g, id_ + "-at", *ids)
    return g.fasteners(id_, part, id_ + "-at", head="rivet", radius=r["radius"], height=r["height"])


# --- dimensions that follow the controls (Built by Opus 5.5 (medium)) ----------------------------------
# truss, plate_girder, laced_column and gusset_plate take X expressions for their dimensions. Each one is
# carried as a Q: its value at the controls' defaults (`n`, for the decisions: counts, which points bound a
# gusset, which member end is outermost) and its expression (`x`, for the document's vertices). Every vertex
# is then an expression of the controls, so members, gussets and chords move together at every control value.
# Plain numbers stay plain numbers: a call without expressions writes the same geometry as before.

_BIN = {"+": lambda a, b: a + b, "-": lambda a, b: a - b, "*": lambda a, b: a * b, "/": lambda a, b: a / b,
        "max": max, "min": min, "pow": lambda a, b: a ** b, "atan2": math.atan2, "mod": math.fmod,
        ">": lambda a, b: 1.0 if a > b else 0.0, "<": lambda a, b: 1.0 if a < b else 0.0}
_UN = {"sin": math.sin, "cos": math.cos, "tan": math.tan, "sqrt": math.sqrt, "abs": abs, "neg": lambda a: -a,
       "floor": math.floor, "ceil": math.ceil, "round": lambda a: float(math.floor(a + 0.5))}
_ENV = {}


def _setenv(controls):
    global _ENV
    _ENV = {k: float(c["value"]) if isinstance(c, dict) else float(c) for k, c in (controls or {}).items()}
    _POSITIVE.clear()
    _POSITIVE.update("$" + k for k, c in (controls or {}).items() if isinstance(c, dict) and float(c.get("min", 0)) > 0)


def _ev(x, env):
    """Value of a raw expression at the controls' defaults."""
    if _num(x):
        return float(x)
    if isinstance(x, str):
        assert x.startswith("$"), f"iron: field {x} cannot be used in a dimension"
        assert x[1:] in env, (f"iron: control {x[1:]} has no default here; pass the module's controls dict as "
                              "controls= (counts and gusset outlines are worked out at the defaults)")
        return env[x[1:]]
    op, *args = x
    if op == "if":
        return _ev(args[1], env) if _ev(args[0], env) != 0 else _ev(args[2], env)
    vals = [_ev(a, env) for a in args]
    return _BIN[op](*vals) if len(vals) == 2 else _UN[op](vals[0])


class Q:
    """A dimension: n, its value at the defaults; x, its expression (a number when nothing is symbolic);
    poly, the same as a sum of monomials {((control, power), ...): coefficient} while it is one, so products
    and sums of controls are written short (depth * 4 * x * (span - x) / span^2 at x = 0.375 span ->
    depth * 0.9375)."""
    __slots__ = ("n", "x", "poly")

    def __init__(self, n, x=None, poly=None):
        self.n = float(n)
        if x is None:
            self.x, self.poly = self.n, {(): self.n}
        else:
            self.x, self.poly = x, poly

    def __add__(self, o): return _comb(self, _qq(o), 1.0, "+")
    def __radd__(self, o): return _qq(o) + self
    def __sub__(self, o): return _comb(self, _qq(o), -1.0, "-")
    def __rsub__(self, o): return _qq(o) - self

    def __mul__(self, o):
        a, b = self, _qq(o)
        if _num(a.x) and _num(b.x):
            return Q(a.n * b.n)
        for p, r in ((a, b), (b, a)):
            if _num(p.x):
                if p.x == 0:
                    return Q(0.0)
                if p.x == 1:
                    return r
        if a.poly is not None and b.poly is not None and len(a.poly) * len(b.poly) <= 16:
            out = {}
            for ma, ca in a.poly.items():
                for mb, cb in b.poly.items():
                    m = _mono_mul(ma, mb, 1)
                    out[m] = out.get(m, 0.0) + ca * cb
            return _from_poly(a.n * b.n, out)
        return Q(a.n * b.n, ["*", a.x, b.x])

    def __rmul__(self, o): return _qq(o) * self

    def __truediv__(self, o):
        a, b = self, _qq(o)
        if _num(a.x) and _num(b.x):
            return Q(a.n / b.n)
        if _num(b.x) and b.x == 1:
            return a
        if _num(a.x) and a.x == 0:
            return Q(0.0)
        if a.poly is not None and b.poly is not None and len(b.poly) == 1:
            (mb, cb), = b.poly.items()
            return _from_poly(a.n / b.n, {_mono_mul(m, mb, -1): c / cb for m, c in a.poly.items()})
        return Q(a.n / b.n, ["/", a.x, b.x])

    def __rtruediv__(self, o): return _qq(o) / self
    def __neg__(self): return Q(0.0) - self
    def __float__(self): return self.n
    def __lt__(self, o): return self.n < _n(o)
    def __le__(self, o): return self.n <= _n(o)
    def __gt__(self, o): return self.n > _n(o)
    def __ge__(self, o): return self.n >= _n(o)


def _mono_mul(ma, mb, sign):
    d = dict(ma)
    for k, p in mb:
        d[k] = d.get(k, 0) + sign * p
    return tuple(sorted((k, p) for k, p in d.items() if p))


def _from_poly(n, poly):
    poly = {m: c for m, c in poly.items() if c != 0}
    if not poly:
        return Q(0.0)
    if list(poly) == [()]:
        return Q(n)
    out = None
    for m in sorted(poly, key=lambda m: (len(m), m)):
        c = poly[m]
        if not m:
            continue
        num = [k for k, p in m if p > 0 for _ in range(p)]
        den = [k for k, p in m if p < 0 for _ in range(-p)]
        t = num[0] if num else 1.0
        for k in num[1:]:
            t = ["*", t, k]
        if c != 1:
            t = ["*", t, c] if num else c
        if den:
            d = den[0]
            for k in den[1:]:
                d = ["*", d, k]
            t = ["/", t, d]
        out = t if out is None else ["+", out, t]
    const = poly.get((), 0.0)
    if const:
        out = ["+", out, const]
    return Q(n, out, poly)


def _comb(a, b, sign, op):
    if _num(a.x) and _num(b.x):
        return Q(a.n + sign * b.n)
    n = a.n + sign * b.n
    if a.poly is not None and b.poly is not None:
        out = dict(a.poly)
        for m, c in b.poly.items():
            out[m] = out.get(m, 0.0) + sign * c
        return _from_poly(n, out)
    if _num(b.x) and b.x == 0:
        return a
    if _num(a.x) and a.x == 0 and sign > 0:
        return b
    return Q(n, [op, a.x, b.x])


_POSITIVE = set()                                  # controls whose whole range is above zero


def _sqrt_mono(a):
    """sqrt of a single positive monomial with even powers of positive controls, or None."""
    if a.poly is None or len(a.poly) != 1:
        return None
    (m, c), = a.poly.items()
    if c <= 0 or any(p % 2 or k not in _POSITIVE for k, p in m):
        return None
    return _from_poly(math.sqrt(a.n), {tuple((k, p // 2) for k, p in m): math.sqrt(c)})


def _qq(v):
    if isinstance(v, Q):
        return v
    raw = _v(v)
    if _num(raw):
        return Q(raw)
    if isinstance(raw, str):
        assert raw.startswith("$"), f"iron: field {raw} cannot be used in a dimension"
        return Q(_ev(raw, _ENV), raw, {((raw, 1),): 1.0})
    op, *args = raw                       # rebuild through Q so affine parts stay short
    qs = [_qq(X(a)) for a in args]
    f = {"+": lambda p, q: p + q, "-": lambda p, q: p - q, "*": lambda p, q: p * q, "/": lambda p, q: p / q,
         "max": qmax, "min": qmin}.get(op)
    if f and len(qs) == 2:
        return f(*qs)
    if op in ("sqrt", "abs", "cos", "sin") and len(qs) == 1:
        return {"sqrt": qsqrt, "abs": qabs, "cos": qcos, "sin": qsin}[op](qs[0])
    return Q(_ev(raw, _ENV), raw)


def _n(v):
    return v.n if isinstance(v, Q) else _qq(v).n


def _x(v):
    """For the document: a number, or an X."""
    if isinstance(v, Q):
        return v.x if _num(v.x) else X(v.x)
    return v


def _fn(name, f):
    def g(a):
        a = _qq(a)
        if _num(a.x):
            return Q(f(a.n))
        if name == "sqrt":
            r = _sqrt_mono(a)
            if r is not None:
                return r
        return Q(f(a.n), [name, a.x])
    return g


qsqrt, qabs, qcos, qsin = _fn("sqrt", math.sqrt), _fn("abs", abs), _fn("cos", math.cos), _fn("sin", math.sin)


def qmax(a, b):
    a, b = _qq(a), _qq(b)
    return Q(max(a.n, b.n)) if _num(a.x) and _num(b.x) else Q(max(a.n, b.n), ["max", a.x, b.x])


def qmin(a, b):
    a, b = _qq(a), _qq(b)
    return Q(min(a.n, b.n)) if _num(a.x) and _num(b.x) else Q(min(a.n, b.n), ["min", a.x, b.x])


def _num2(d):
    """A 2D direction at the defaults (numbers)."""
    return (_n(d[0]), _n(d[1]))


def _lowest(vals):
    """The value lowest at the defaults (a choice, not a min expression)."""
    return min(vals, key=_n)


def _highest(vals):
    return max(vals, key=_n)


def _qv(a): return [_qq(c) for c in a]
def _xv(a): return [_x(c) for c in a]
def _qa(a, b): return [_qq(x) + y for x, y in zip(a, b)]
def _qs(a, b): return [_qq(x) - y for x, y in zip(a, b)]
def _qm(a, k): return [_qq(x) * k for x in a]
def _qd(a, b): return _qq(a[0]) * b[0] + _qq(a[1]) * b[1] + _qq(a[2]) * b[2]


def _qc(a, b):
    a, b = _qv(a), _qv(b)
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _qu(a):
    return _qm(a, 1 / qsqrt(_qd(a, a)))


def _qframe(a, b, up):
    """(axis, side, up) of a member a -> b as Q vectors; the up fallback for a member along `up` is decided at the defaults."""
    axis = _qu(_qs(b, a))
    u = _qv(up)
    ax, uu = [c.n for c in axis], [c.n for c in u]
    if abs(sum(p * q for p, q in zip(ax, uu))) / max(1e-12, math.sqrt(sum(p * p for p in uu))) > 0.999:
        u = _qv((1, 0, 0) if abs(ax[0]) < 0.9 else (0, 1, 0))
    u = _qu(_qs(u, _qm(axis, _qd(axis, u))))
    return axis, _qc(u, axis), u


def _qprism(g, id_, outline, a, b, axis, side, up, ends=(None, None), slot=None):
    """A closed prism like _prism, for Q end points and outlines. The frame (axis, side, up) and the end-cut
    normals are taken at the defaults: every vertex is an end point plus a fixed offset, so the document's
    expressions stay small, members meeting at a node move with it, and two members cut on a shared mitre
    plane keep their faces together at every control value (away from the defaults a section tilts a little)."""
    s, u, ax = [_n(c) for c in side], [_n(c) for c in up], [_n(c) for c in axis]
    cr = (s[1] * u[2] - s[2] * u[1], s[2] * u[0] - s[0] * u[2], s[0] * u[1] - s[1] * u[0])
    loop = list(outline)
    if sum(p * q for p, q in zip(cr, ax)) < 0:            # left-handed frame: reverse so the shell stays outward
        loop.reverse()
    verts = []
    for end, P in ((ends[0], a), (ends[1], b)):
        nrm = None if end is None else [_n(c) for c in end]
        P = _qv(P)
        for uu, ww in loop:
            o = [_qq(uu) * s[i] + _qq(ww) * u[i] for i in range(3)]
            if nrm is not None:
                t = -(o[0] * nrm[0] + o[1] * nrm[1] + o[2] * nrm[2]) / (ax[0] * nrm[0] + ax[1] * nrm[1] + ax[2] * nrm[2])
                o = [o[i] + t * ax[i] for i in range(3)]
            verts.append(_xv(_qa(P, o)))
    L = len(loop)
    tris = _ear_clip([(_n(p), _n(q)) for p, q in loop])
    faces = [[c, b_, a_] for a_, b_, c in tris] + [[L + a_, L + b_, L + c] for a_, b_, c in tris]
    for i in range(L):
        j = (i + 1) % L
        faces.append([i, j, L + j, L + i])
    part = g.n(id_ + "-shell" if slot else id_, "surface", vertices=verts, faces=faces)
    return g.mat(id_, part, slot) if slot else part


def _qplate(g, id_, poly, origin, e1, e2, thickness, slot=None):
    """_plate for Q polygons and frames."""
    n = _qc(e1, e2)
    half = _qm(n, _qq(thickness) * 0.5)
    o = _qv(origin)
    return _qprism(g, id_, poly, _qs(o, half), _qa(o, half), n, e1, e2, slot=slot)


def _flat(b, t):
    """Outline of a flat b wide and t thick (Q or numbers), centred."""
    b, t = _qq(b) * 0.5, _qq(t) * 0.5
    return [(-b, -t), (b, -t), (b, t), (-b, t)]


def _qrow(a, b, pitch, edge):
    """Points from a to b at about `pitch`, `edge` clear of both ends: count and fractions from the defaults
    (rivets are detail; along a controlled member they stretch with it)."""
    a, b = _qv(a), _qv(b)
    d = _qs(b, a)
    L = math.sqrt(sum(c.n * c.n for c in d))
    if L < 2 * edge:
        return []
    n = max(1, int(round((L - 2 * edge) / pitch)) + 1)
    out, row = [], object()
    for k in range(n):
        t = 0.5 if n == 1 else (edge + (L - 2 * edge) * k / (n - 1)) / L
        out.append(_RowPt(_qa(a, _qm(d, t)), row, d, t))
    return out


class _RowPt(list):
    """A point of a _qrow row (a + d * t): _rivet writes a run of them as one line node."""

    def __init__(self, p, row, d, t):
        super().__init__(p)
        self.row, self.d, self.t = row, d, t


# --- 2D helpers ----------------------------------------------------------------------------------------

def _hull_idx(pts):
    """Indices of the convex hull of numeric (x, y) points, counter-clockwise."""
    first = {}
    for i, (x, y) in enumerate(pts):
        first.setdefault((round(x, 7), round(y, 7)), i)
    items = sorted(first.items())
    if len(items) < 3:
        return [i for _, i in items]

    def cr(o, a, b):
        return (a[0][0] - o[0][0]) * (b[0][1] - o[0][1]) - (a[0][1] - o[0][1]) * (b[0][0] - o[0][0])
    lo, hi = [], []
    for p in items:
        while len(lo) >= 2 and cr(lo[-2], lo[-1], p) <= 1e-10:
            lo.pop()
        lo.append(p)
    for p in reversed(items):
        while len(hi) >= 2 and cr(hi[-2], hi[-1], p) <= 1e-10:
            hi.pop()
        hi.append(p)
    return [i for _, i in lo[:-1] + hi[:-1]]


def _n2(d):
    d = (_qq(d[0]), _qq(d[1]))
    L = qsqrt(d[0] * d[0] + d[1] * d[1])
    return (d[0] / L, d[1] / L)


def _gusset_poly(node, webs, heels, edge=0.03):
    """Gusset outline round `node` (x, z): webs are (unit dir, half width, reach) of the members ending on
    it; heels are (point, unit dir) lines (the chord heels) the plate stays flush with. The corners are
    expressions along the members' own directions; which of them bound the plate (its convex hull) is
    decided at the defaults. CCW."""
    pts = []
    for d, h, L in webs:
        n = (-d[1], d[0])
        for s in (-1, 1):
            w = (h + edge * 0.5) * s
            pts.append((node[0] + d[0] * L + n[0] * w, node[1] + d[1] * L + n[1] * w))
    base = list(pts)
    for p0, t in heels:
        for q in base + [node]:
            k = (q[0] - p0[0]) * t[0] + (q[1] - p0[1]) * t[1]
            pts.append((p0[0] + t[0] * k, p0[1] + t[1] * k))
    return [pts[i] for i in _hull_idx([(_n(x), _n(y)) for x, y in pts])]


def _start(d, h, others, clear):
    """Distance along unit d from the node at which a member of half width h clears the others: chords
    ("band", unit dir, half depth) and the other members ("strip", unit dir from the node, half width).
    A number, at the defaults: the cut point (node + d * s) still follows the node and the member's direction."""
    s = _qq(h) + clear
    for kind, e, he in others:
        c = d[0] * e[0] + d[1] * e[1]
        c = _qq(c)
        if kind == "band":
            sn = qsqrt(qmax(1e-9, 1 - c * c))
            s = qmax(s, (he + clear + qabs(c) * h) / sn)
        else:
            if c.n > math.cos(1e-3):                         # parallel at the defaults: no clearance needed
                continue
            # half width over tan(phi / 2), tan(phi / 2) = sin(phi) / (1 + cos(phi))
            s = qmax(s, (qmax(h, he) + clear * 0.5) * (1 + c) / qsqrt(qmax(1e-12, 1 - c * c)))
    return Q(s.n)                                            # trims are worked out at the defaults


# --- trusses -------------------------------------------------------------------------------------------

PITCHED = ("pratt-pitched", "howe-pitched", "fink")
TRUSS_KINDS = ("warren", "pratt", "howe", "lattice", "bowstring") + PITCHED


def _layout(kind, span, depth, n, rise_shape):
    """Bottom nodes, top nodes and the web as (end key, end key, layer) for each truss kind (Q positions:
    panel point i of n at span * i / n)."""
    p = span / n
    bottom = [(p * i, Q(0.0)) for i in range(n + 1)]
    web = []
    if kind == "warren":
        top = [(p * (i + 0.5), depth) for i in range(n)]
        for i in range(n):
            web += [(("t", i), ("b", i), 0), (("t", i), ("b", i + 1), 0)]
    elif kind == "bowstring":
        def h(i):
            if rise_shape == "parabolic":
                return depth * (4 * i * (n - i) / (n * n))
            return depth * _arc(i / n, depth.n / span.n)               # the circle at the defaults, scaled
        top = [(p * i, h(i)) for i in range(n + 1)]
        for i in range(1, n):
            web.append((("t", i), ("b", i), 0))
        for i in range(1, n - 1):
            web.append((("t", i), ("b", i + 1), 0) if 2 * i + 1 < n else (("t", i + 1), ("b", i), 0))
    elif kind == "fink":                                     # the W: rafters in two panels, tie in three
        bottom = [(Q(0.0), Q(0.0)), (span / 3, Q(0.0)), (span * 2 / 3, Q(0.0)), (span, Q(0.0))]
        top = [(Q(0.0), Q(0.0)), (span / 4, depth / 2), (span / 2, depth), (span * 3 / 4, depth / 2), (span, Q(0.0))]
        web = [(("t", 1), ("b", 1), 0), (("b", 1), ("t", 2), 0), (("b", 2), ("t", 2), 0), (("t", 3), ("b", 2), 0)]
    elif kind in PITCHED:                                    # gable: rafters from the bearings to a ridge at mid-span
        m = n // 2
        top = [(p * i, depth * (1 - abs(i - m) / m)) for i in range(n + 1)]
        for i in range(1, n):
            web.append((("t", i), ("b", i), 0))
        for i in range(1, n - 1):
            left = 2 * i + 1 < n
            if kind == "pratt-pitched":                      # diagonals falling toward mid-span
                web.append((("t", i), ("b", i + 1), 0) if left else (("t", i + 1), ("b", i), 0))
            else:                                            # howe: rising toward the ridge
                web.append((("t", i + 1), ("b", i), 0) if left else (("t", i), ("b", i + 1), 0))
    else:
        top = [(p * i, depth) for i in range(n + 1)]
        for i in (range(n + 1) if kind in ("pratt", "howe") else (0, n)):
            web.append((("t", i), ("b", i), 0))
        for i in range(n):
            left = 2 * i + 1 < n
            if kind == "pratt":
                web.append((("t", i), ("b", i + 1), 0) if left else (("t", i + 1), ("b", i), 0))
            elif kind == "howe":
                web.append((("t", i + 1), ("b", i), 0) if left else (("t", i), ("b", i + 1), 0))
            else:                                        # lattice: two Warren systems crossing mid-panel
                web += [(("t", i + 1), ("b", i), 0), (("t", i), ("b", i + 1), 1)]
    return bottom, top, web


def truss(self, id_, span, depth, panels=None, kind="pratt", chord="L80x10", web=None, vertical=None, gusset=0.012,
          rise="parabolic", rivets=True, pitch=0.075, rivet=None, at=(0, 0, 0), yaw=0.0, slot=None, panel_pitch=None,
          controls=None):
    """A riveted plane truss: chords and web of paired sections with a gusset plate at every node.

    Frame: span along +X from x = 0 to `span`, in the XZ plane centred on y = 0; the bottom chord's working
    line is z = 0 and the top chord's z = `depth` (the steel stands half a chord leg beyond them); `at` and
    `yaw` (radians about +Z) place it. kind: "warren" (diagonals only, top nodes mid-panel), "pratt"
    (verticals, diagonals falling toward mid-span), "howe" (rising toward mid-span), "lattice" (double
    intersection: crossed flats in every panel and end posts; the second system lies over the first, packed
    out with fillers at its ends and riveted at the crossings) or "bowstring" (top chord arched through the
    panel points, `rise` "parabolic" or "circular", `depth` its rise, landing on the bearing nodes; verticals
    and diagonals). Roof trusses for sheds (gable: two straight rafters from the bearings, mitred at a ridge
    `depth` above the tie at mid-span, landing on the tie at both ends): "pratt-pitched" (verticals, diagonals
    falling toward mid-span; even panels), "howe-pitched" (diagonals rising to the ridge) and "fink" (the
    French / W truss: rafters in two panels, the tie in three, struts from the rafter mid-points and ties to
    the ridge; panels 4). chord: an angle ("L80x10"); web / vertical: angles or flats ("FL60x10"); web
    defaults to L60x8 (lattice FL60x10), vertical to web. Every member is a pair back to back about y = 0 with
    the gusset (`gusset` thick) in the gap; web members stop clear of the chords and of each other; each
    gusset reaches every member by three rivets at `pitch` and stays flush with the chord heels. Rivets
    (detail) at both ends of every web member and through the chords at every gusset, on both faces.

    Controls: span, depth, `at` and `yaw` may be X (e.g. span=C("span"), depth=C("ridge") - C("eave")); pass
    the module's controls dict as `controls=`. Panel points are span * i / panels, every member end and
    gusset corner is an expression of the controls, so the truss stays connected at every control value.
    panels is a plain int; or give `panel_pitch` (metres) and the count is taken from span's DEFAULT value
    (round(span / panel_pitch), even for gable kinds): the panels then stretch with the control. Counts of
    rivets also come from the defaults (rivet spacing stretches a little).
    Returns (node id, info): info["top"] and info["bottom"] the panel points in the kit frame (numbers, or X
    when the truss follows controls: purlins, bracing, hangers), info["members"] counts of chords, web
    members, gussets and fillers, info["pieces"] the solids. ~20 triangles per angle, 12 per flat, 20-40 per
    gusset (a Pratt of 8 panels: ~1,100; a pitched Pratt of 8: ~1,000).
    Example: node, info = g.truss("roof", 18.0, 2.2, 8, kind="pratt", chord="L90x11", web="L60x8")
             g.truss("roof", C("span"), C("ridge") - C("eave"), 8, kind="pratt-pitched", at=[0, 0, C("eave")],
                     controls=controls)"""
    assert kind in TRUSS_KINDS, f"truss kind {kind!r} (one of {TRUSS_KINDS})"
    _setenv(controls)
    span, depth = _qq(span), _qq(depth)
    if panels is None:
        if kind == "fink":
            panels = 4
        else:
            assert panel_pitch, "truss: give panels, or panel_pitch to derive them from the span's default"
            panels = max(2, int(round(span.n / float(panel_pitch))))
            if kind in PITCHED:
                panels = max(4, panels + panels % 2)
    n = int(panels)
    assert n >= (4 if kind == "bowstring" or kind in PITCHED else 2), "too few panels"
    assert kind not in PITCHED or n % 2 == 0, "a gable truss has an even number of panels"
    assert kind != "fink" or n == 4, "a fink truss has 4 panels (two per rafter)"
    assert span.n > 0 and depth.n > 0, "truss span and depth are positive at the defaults"
    gap = float(gusset)
    web = web or ("FL60x10" if kind == "lattice" else "L60x8")
    vertical = vertical or web
    cs, ws, vs = iron_section(chord), iron_section(web), iron_section(vertical)
    assert cs["kind"] == "L", "truss chords are pairs of angles"
    assert ws["kind"] in ("L", "FL") and vs["kind"] in ("L", "FL"), "truss web members are angles or flats"
    ca, ct = float(cs["a"]), float(cs["t"])
    bottom, top, webs = _layout(kind, span, depth, n, rise)
    clear, edge = 0.012, 0.03
    arched = kind == "bowstring" or kind in PITCHED
    nb, nt = len(bottom) - 1, len(top) - 1

    def width(s):
        return float(s["a"] if s["kind"] == "L" else s["b"])

    at, yaw = _qv(at), _qq(yaw)
    cy, sy = qcos(yaw), qsin(yaw)

    def P(x, z, y=0.0):                                      # truss plane -> kit frame
        return [at[0] + _qq(x) * cy - _qq(y) * sy, at[1] + _qq(x) * sy + _qq(y) * cy, at[2] + z]

    def D(dx, dz):
        return [_qq(dx) * cy, _qq(dx) * sy, _qq(dz)]
    Y = [-sy, cy, Q(0.0)]

    counts = {"chord": 0, "web": 0, "gussets": 0, "fillers": 0}
    chords, members, plates = [], [], []
    rpts = {"chord": [], "web": []}
    nodes = {}
    for i, q in enumerate(bottom):
        nodes[("b", i)] = dict(pt=q, chords=[], webs=[], heels=[])
    for i, q in enumerate(top):
        if arched and i in (0, nt):
            nodes[("t", i)] = nodes[("b", 0 if i == 0 else nb)]       # the top chord lands on the bearing node
        else:
            nodes[("t", i)] = dict(pt=q, chords=[], webs=[], heels=[])

    def pair(tag, sec, a2, b2, outward=None, ends=(None, None), layer=0.0):
        """Two sections back to back about y = 0 from a2 to b2 (x, z). Angles: the leg on the gusset runs
        in-plane with its heel toward `outward`, the other leg stands out along +-y."""
        d = _num2(_n2((b2[0] - a2[0], b2[1] - a2[1])))                # the frame at the defaults
        nrm = (-d[1], d[0])
        made = []
        for sgn in (1, -1):
            up = [_n(c) * sgn for c in Y]
            if sec["kind"] == "L":
                o2 = _num2(outward) if outward else nrm
                side = [_n(c) for c in D(-o2[0], -o2[1])]               # u runs from the heel inward
                shift = [up[i] * (gap * 0.5 + layer) - side[i] * (width(sec) * 0.5) for i in range(3)]
            else:
                side = [_n(c) for c in D(nrm[0], nrm[1])]
                shift = [up[i] * (gap * 0.5 + layer + float(sec["t"]) * 0.5) for i in range(3)]
            a3 = _qa(P(*a2), shift)
            b3 = _qa(P(*b2), shift)
            e3 = [None if e is None else D(e[0], e[1]) for e in ends]
            made.append(_qprism(self, f"{id_}-{tag}-{'n' if sgn > 0 else 's'}", iron_outline(sec), a3, b3, D(*d), side, up, e3))
        return made

    def face_pts(x, z, sec, layer=0.0):
        off = gap * 0.5 + layer + float(sec["t"]) + 0.004
        return [P(x, z, off), P(x, z, -off)]

    # ---- chords
    over = max(width(vs), width(ws)) * 0.5 + 0.06
    bx0, bx1 = (Q(-0.18), span + 0.18) if arched else (Q(-over), span + over)
    straight = {"bottom": [bx0, bx1]}          # straight chords are laid after the gussets, long enough to carry them
    counts["chord"] += 1
    for k, nd in nodes.items():
        if k[0] == "b":
            nd["heels"].append(((nd["pt"][0], Q(-ca * 0.5)), (1.0, 0.0)))
            nd["chords"].append(("band", (1.0, 0.0), ca * 0.5))
    if arched:
        ridge = None if kind == "bowstring" else nt // 2
        runs = [(i, i + 1) for i in range(nt)] if ridge is None else [(0, ridge), (ridge, nt)]
        segs = [(top[i0], top[i1]) for i0, i1 in runs]
        for i, (q0, q1) in enumerate(segs):
            d = _n2((q1[0] - q0[0], q1[1] - q0[1]))
            e0 = None if i == 0 else _mitre2(segs[i - 1], segs[i])
            e1 = None if i == len(segs) - 1 else _mitre2(segs[i], segs[i + 1])
            a2, b2 = q0, q1
            for end, q, dd in ((0, q0, d), (1, q1, (-d[0], -d[1]))):
                if (end == 0 and i == 0) or (end == 1 and i == len(segs) - 1):      # trimmed clear of the bottom chord
                    s = _start(dd, ca * 0.5, [("band", (1.0, 0.0), ca * 0.5)], clear)
                    cut = (q[0] + dd[0] * s, q[1] + dd[1] * s)
                    if end == 0:
                        a2 = cut
                    else:
                        b2 = cut
                    nodes[("b", 0 if end == 0 else nb)]["webs"].append((dd, ca * 0.5, s + 2 * edge + 2 * pitch, "chord"))
                    for t in range(3):
                        k_ = s + edge + t * pitch
                        rpts["chord"] += face_pts(q[0] + dd[0] * k_, q[1] + dd[1] * k_, cs)
            chords += pair(f"top{i}", cs, a2, b2, (-d[1], d[0]), (e0, e1))
            counts["chord"] += 1
        for i in range(1, nt):
            nd = nodes[("t", i)]
            if i == ridge:                     # the rafters meet: a band along each, the plate along both
                for j in (0, nt):
                    dd = _n2((top[j][0] - top[i][0], top[j][1] - top[i][1]))
                    nd["chords"].append(("band", dd, ca * 0.5))
                    nd["webs"].append((dd, ca * 0.5 - edge * 0.5, ca + 2 * edge + 2 * pitch, "rafter"))
                    inn = (-dd[1], dd[0]) if _n(dd[0]) > 0 else (dd[1], -dd[0])       # the side toward the tie
                    for t in range(3):
                        k_ = ca * 0.5 + edge + t * pitch
                        rpts["chord"] += face_pts(nd["pt"][0] + dd[0] * k_ + inn[0] * (ct * 0.5),
                                                  nd["pt"][1] + dd[1] * k_ + inn[1] * (ct * 0.5), cs)
                continue
            d0 = _n2((top[i][0] - top[i - 1][0], top[i][1] - top[i - 1][1]))
            d1 = _n2((top[i + 1][0] - top[i][0], top[i + 1][1] - top[i][1]))
            t = _num2(_n2((d0[0] + d1[0], d0[1] + d1[1])))            # heel line direction at the defaults
            o = (-t[1], t[0])
            nd["heels"].append(((nd["pt"][0] + o[0] * (ca * 0.5), nd["pt"][1] + o[1] * (ca * 0.5)), t))
            nd["chords"].append(("band", t, ca * 0.5))
    else:
        straight["top"] = [top[0][0] - over, top[-1][0] + over]
        counts["chord"] += 1
        for k, nd in nodes.items():
            if k[0] == "t":
                nd["heels"].append(((nd["pt"][0], depth + ca * 0.5), (1.0, 0.0)))
                nd["chords"].append(("band", (1.0, 0.0), ca * 0.5))

    # ---- web members, each end trimmed clear of the chords and the other members at its node
    def sec_of(w):
        return vs if abs(_n(nodes[w[0]]["pt"][0]) - _n(nodes[w[1]]["pt"][0])) < 1e-9 else ws
    ends_at = {}
    for w in webs:
        for e in w[:2]:
            ends_at.setdefault(e, []).append(w)
    for k, w in enumerate(webs):
        ta, tb, layer = w
        sec = sec_of(w)
        hw = width(sec) * 0.5
        lay = layer * float(ws["t"])
        cut, pts2 = [], []
        for this, other in ((ta, tb), (tb, ta)):
            q0, q1 = nodes[this]["pt"], nodes[other]["pt"]
            d = _n2((q1[0] - q0[0], q1[1] - q0[1]))
            others = list(nodes[this]["chords"])
            for w2 in ends_at[this]:
                if w2 is w:
                    continue
                p2 = nodes[w2[1] if w2[0] == this else w2[0]]["pt"]
                others.append(("strip", _n2((p2[0] - q0[0], p2[1] - q0[1])), width(sec_of(w2)) * 0.5))
            others += [("strip", dd, hh) for dd, hh, _, tag in nodes[this]["webs"] if tag == "chord"]
            s = _start(d, hw, others, clear)
            reach = s + 2 * edge + 2 * pitch
            nodes[this]["webs"].append((d, hw, reach, "web"))
            cut.append((q0[0] + d[0] * s, q0[1] + d[1] * s))
            for t in range(3):
                k_ = s + edge + t * pitch
                pts2 += face_pts(q0[0] + d[0] * k_, q0[1] + d[1] * k_, sec, lay)
            if layer:                                                # filler between the gusset and the outer bar
                fsec = dict(kind="FL", b=width(sec), t=float(ws["t"]))
                members.extend(pair(f"filler{k}{'a' if this == ta else 'b'}", fsec, cut[-1],
                                    (q0[0] + d[0] * reach, q0[1] + d[1] * reach)))
                counts["fillers"] += 1
        members += pair(f"web{k}", sec, cut[0], cut[1], layer=lay)
        counts["web"] += 1
        rpts["web"] += pts2
    if kind == "lattice":                                            # rivets where the two systems cross
        for i in range(n):
            rpts["web"] += face_pts(span * ((i + 0.5) / n), depth * 0.5, ws, float(ws["t"]))

    # ---- gussets, and rivets through the chords along them
    seen = set()
    for key, nd in nodes.items():
        if id(nd) in seen or not nd["webs"]:
            continue
        seen.add(id(nd))
        q = nd["pt"]
        poly = _gusset_poly(q, [(d, h, L) for d, h, L, _ in nd["webs"]], nd["heels"], edge)
        loc = [(x - q[0], z - q[1]) for x, z in poly]
        plates.append(_qplate(self, f"{id_}-gusset-{key[0]}{key[1]}", loc, P(*q), D(1, 0), D(0, 1), gap + 0.002))
        for name, k0 in (("bottom", "b"), ("top", "t")):
            if name in straight and key[0] == k0:
                lo_ = _lowest([x for x, _ in poly])                  # the plate's end corners (they follow the controls)
                hi_ = _highest([x for x, _ in poly])
                straight[name][0] = _lowest([straight[name][0], lo_ - 0.015])
                straight[name][1] = _highest([straight[name][1], hi_ + 0.015])
        counts["gussets"] += 1
        for p0, t in nd["heels"]:
            ks = [(x - p0[0]) * t[0] + (z - p0[1]) * t[1] for x, z in poly]
            side_ = _n((q[0] - p0[0]) * -_qq(t[1]) + (q[1] - p0[1]) * t[0])
            inward = (-_qq(t[1]), _qq(t[0])) if side_ > 0 else (_qq(t[1]), -_qq(t[0]))
            gg = (ca + ct) * 0.5                                      # gauge: middle of the leg's free part
            k0_, k1_ = _lowest(ks), _highest(ks)
            a3 = [p0[0] + t[0] * k0_ + inward[0] * gg, 0.0, p0[1] + t[1] * k0_ + inward[1] * gg]
            b3 = [p0[0] + t[0] * k1_ + inward[0] * gg, 0.0, p0[1] + t[1] * k1_ + inward[1] * gg]
            for p in _qrow([_n(c) for c in a3], [_n(c) for c in b3], pitch, 0.035):
                # laid out at the defaults and carried by the node (detail; the plate stays round them)
                rpts["chord"] += face_pts(q[0] + (p[0].n - q[0].n), q[1] + (p[2].n - q[1].n), cs)

    for name, (x0, x1) in straight.items():
        z = Q(0.0) if name == "bottom" else depth
        chords += pair(name, cs, (x0, z), (x1, z), (0.0, -1.0 if name == "bottom" else 1.0))
    ch = _join(self, f"{id_}-chords", *chords)
    wb = _join(self, f"{id_}-webs", *members)
    if rivets:
        ch = _rivet(self, f"{id_}-chord-rivets", ch, rpts["chord"], rivet)
        wb = _rivet(self, f"{id_}-web-rivets", wb, rpts["web"], rivet)
    body = _join(self, id_ + "-parts" if slot else id_, ch, wb, *plates)
    if slot:
        body = self.mat(id_, body, slot)
    info = {"top": [_xv(P(x, z)) for x, z in (top[1:-1] if arched else top)],
            "bottom": [_xv(P(x, z)) for x, z in bottom],
            "members": counts, "pieces": len(chords) + len(members) + len(plates)}
    return body, info


def _mitre2(seg0, seg1):
    """Mitre normal (x, z) at the shared node of two consecutive (x, z) segments."""
    d0 = _n2((seg0[1][0] - seg0[0][0], seg0[1][1] - seg0[0][1]))
    d1 = _n2((seg1[1][0] - seg1[0][0], seg1[1][1] - seg1[0][1]))
    return (d0[0] + d1[0], d0[1] + d1[1])


# --- plate girder --------------------------------------------------------------------------------------

def plate_girder(self, id_, span, depth, web=0.010, flange="L100x12", cover=(0.30, 0.012), stiffener="L80x10",
                 stiffener_pitch=1.2, bearing="L100x12", bearing_at=0.25, soffit=0.0, segments=10, rivets=True,
                 pitch=0.10, rivet=None, sole=True, at=(0, 0, 0), yaw=0.0, slot=None, controls=None):
    """A riveted plate girder: web plate, four flange angles, cover plates, stiffeners and rivet rows.

    Frame: along +X from x = 0 to `span`, web in the XZ plane centred on y = 0, bottom of the steel at z = 0
    and top at z = `depth` (over the covers); `at` and `yaw` place it. web: plate thickness. flange: the
    four flange angles (legs on the web, outstanding legs outward). cover: (width, thickness) of one cover
    plate top and bottom, or None. Stiffeners: pairs of `stiffener` angles on filler plates (so they clear
    the flange angles) at about `stiffener_pitch`, between bearing stiffeners (pairs of `bearing` angles)
    at `bearing_at` from each end, with a sole plate under each bearing (`sole`). soffit > 0 arches the
    underside (an arched spandrel girder): the bottom flange and cover rise by `soffit` at mid-span on a
    circular arc in `segments` mitred pieces. Rivets (detail): one row through the web on each flange
    angle, two rows on each cover, one row up each stiffener, at `pitch`.
    Controls: span, depth, soffit, bearing_at, `at` and `yaw` may be X (pass the module's controls dict as
    `controls=`); sections, cover and counts are numbers. The number of stiffeners (and rivets) comes from
    the span's DEFAULT value / stiffener_pitch, so their spacing stretches with the control. An X soffit may
    reach 0 (a straight underside) at the end of its range.
    Returns (node id, info): info["bearings"] (points under the sole plates, kit frame; X when controlled),
    info["stiffeners"] (intermediate pairs), info["pieces"]. 12 m x 1.2 m with 9 stiffener stations is
    ~850 triangles (arched, 10 segments: ~1,350).
    Example: node, info = g.plate_girder("girder", 12.0, 1.2, soffit=0.5)
             g.plate_girder("girder", C("span"), C("span") / 12, controls=controls)"""
    _setenv(controls)
    span, Dp, tw = _qq(span), _qq(depth), float(web)
    fs, ss, bs = iron_section(flange), iron_section(stiffener), iron_section(bearing)
    assert fs["kind"] == ss["kind"] == bs["kind"] == "L", "flange and stiffeners are angles"
    fa, fb, ft = float(fs["a"]), float(fs["b"]), float(fs["t"])
    cw, ctk = (float(cover[0]), float(cover[1])) if cover else (0.0, 0.0)
    bearing_at = _qq(bearing_at)
    at, yaw = _qv(at), _qq(yaw)
    cy, sy = qcos(yaw), qsin(yaw)

    def V(x, y, z):
        x, y = _qq(x), _qq(y)
        return [x * cy - y * sy, x * sy + y * cy, _qq(z)]

    def P(x, y, z):
        return _qa(at, V(x, y, z))
    rise = _qq(soffit)
    if not (_num(rise.x) and rise.n <= 0):
        def zb(x):                     # the circle through the defaults, scaled by the rise (x's place at the defaults)
            return rise * _arc(_n(x) / span.n, rise.n / span.n)
        xs = [span * (k / int(segments)) for k in range(int(segments) + 1)]
    else:
        def zb(x):
            return Q(0.0)
        xs = [Q(0.0), span]
    assert Dp.n - rise.n > 2 * (ctk + fa) + 0.1, "plate girder too shallow for its flanges"
    soff = [(x, zb(x)) for x in xs]

    flanges, covers, stiffs, others = [], [], [], []
    r_fl, r_cv, r_st = [], [], []

    def run(tag, loop, path, side_fn, up_fn, y=0.0):
        """Prisms along the polyline path [(x, z)] at y, mitred at its bends; side_fn / up_fn give a piece's
        frame from its unit direction (dx, dz)."""
        out = []
        for i in range(len(path) - 1):
            (x0, z0), (x1, z1) = path[i], path[i + 1]
            d = _n2((x1 - x0, z1 - z0))
            e0 = None if i == 0 else V(*_mitre3(path[i - 1], path[i], path[i + 1]))
            e1 = None if i == len(path) - 2 else V(*_mitre3(path[i], path[i + 1], path[i + 2]))
            out.append(_qprism(self, f"{id_}-{tag}{i}", loop, P(x0, y, z0), P(x1, y, z1), V(d[0], 0, d[1]),
                               V(*side_fn(d)), V(*up_fn(d)), (e0, e1)))
        return out

    # web plate between the flange angle heels, 4 mm clear of the covers
    top_web = Dp - ctk - 0.004
    poly = [(x, z + ctk + 0.004) for x, z in soff] + [(span, top_web), (Q(0.0), top_web)]
    others.append(_qplate(self, f"{id_}-web", poly, P(0, 0, 0), V(1, 0, 0), V(0, 0, 1), tw))
    top_path = [(Q(0.0), Dp - ctk), (span, Dp - ctk)]
    bot_path = [(x, z + ctk) for x, z in soff]
    loopL = iron_outline(fs)
    for sgn, nm in ((1, "n"), (-1, "s")):          # heels on the web faces under / over the covers
        flanges += run(f"ftop-{nm}", loopL, top_path, lambda d: (0, 0, -1), lambda d, s=sgn: (0, s, 0), sgn * tw * 0.5)
        flanges += run(f"fbot-{nm}", loopL, bot_path, lambda d: (-d[1], 0, d[0]), lambda d, s=sgn: (0, s, 0), sgn * tw * 0.5)
    if cover:
        cl = iron_outline(dict(kind="FL", b=cw, t=ctk))
        covers += run("ctop", cl, [(Q(0.0), Dp - ctk * 0.5), (span, Dp - ctk * 0.5)], lambda d: (0, 1, 0), lambda d: (0, 0, 1))
        covers += run("cbot", cl, [(x, z + ctk * 0.5) for x, z in soff], lambda d: (0, 1, 0), lambda d: (-d[1], 0, d[0]))
    # stiffeners: bearing pairs and intermediate pairs, each angle on a filler as thick as the flange angle
    nb = max(1, int(round((span.n - 2 * bearing_at.n) / stiffener_pitch)))
    sx = [bearing_at + (span - bearing_at * 2) * (k / nb) for k in range(1, nb)]
    stations = [(bearing_at, bs, "bear0"), (span - bearing_at, bs, "bear1")] + [(x, ss, f"st{k}") for k, x in enumerate(sx)]
    for x, sec, tag in stations:
        sa, st = float(sec["a"]), float(sec["t"])
        z0, z1 = zb(x) + ctk + ft, Dp - ctk - ft                    # between the outstanding legs
        fz0, fz1 = zb(x) + ctk + fa, Dp - ctk - fa                  # filler between the flange angles' toes
        for sgn, nm in ((1, "n"), (-1, "s")):
            y0 = sgn * (tw * 0.5 + ft)
            stiffs.append(_qprism(self, f"{id_}-{tag}-{nm}", iron_outline(sec), P(x - sa * 0.5, y0, z0), P(x - sa * 0.5, y0, z1),
                                  V(0, 0, 1), V(1, 0, 0), V(0, sgn, 0)))
            yf = sgn * (tw + ft) * 0.5
            stiffs.append(_qprism(self, f"{id_}-{tag}-fill-{nm}", iron_outline(dict(kind="FL", b=sa, t=ft)),
                                  P(x, yf, fz0), P(x, yf, fz1), V(0, 0, 1), V(1, 0, 0), V(0, sgn, 0)))
            gy = sgn * (tw * 0.5 + ft + st + 0.004)
            gx = x - sa * 0.5 + (sa + st) * 0.5
            r_st += _qrow(P(gx, gy, fz0), P(gx, gy, fz1), pitch * 1.5, 0.05)
    keep_out = [(_n(x), float(s["a"]) * 0.5 + 0.03) for x, s, _ in stations]
    g_leg = (fa + ft) * 0.5                                         # gauge on the legs: middle of the free part
    for sgn in (1, -1):
        gy = sgn * (tw * 0.5 + ft + 0.004)
        for path, top in ((top_path, True), (bot_path, False)):
            for i in range(len(path) - 1):
                (x0, z0), (x1, z1) = path[i], path[i + 1]
                d = _num2(_n2((x1 - x0, z1 - z0)))
                n_in = (0.0, -1.0) if top else (-d[1], d[0])
                a3 = P(x0 + n_in[0] * g_leg, gy, z0 + n_in[1] * g_leg)
                b3 = P(x1 + n_in[0] * g_leg, gy, z1 + n_in[1] * g_leg)
                xa, xb = _n(x0) + n_in[0] * g_leg, _n(x1) + n_in[0] * g_leg
                for p in _qrow(a3, b3, pitch, 0.04):
                    if all(abs(xa + (xb - xa) * p.t - xk) > hk for xk, hk in keep_out):
                        r_fl.append(p)
        if cover:
            gy = sgn * (tw * 0.5 + (fb + ft) * 0.5)
            for path, zoff in ((top_path, ctk + 0.004), (bot_path, -ctk - 0.004)):
                for i in range(len(path) - 1):
                    (x0, z0), (x1, z1) = path[i], path[i + 1]
                    d = _num2(_n2((x1 - x0, z1 - z0)))
                    nz = (-d[1], d[0])
                    r_cv += _qrow(P(x0 + nz[0] * zoff, gy, z0 + nz[1] * zoff), P(x1 + nz[0] * zoff, gy, z1 + nz[1] * zoff),
                                  pitch, 0.04)
    bearings = []
    for k, x in enumerate((bearing_at, span - bearing_at)):
        zs = qmax(_under(soff, x - 0.15), _under(soff, x + 0.15))     # top of the sole at the underside's high end
        if sole:
            hw = max(cw, 2 * fb + tw) * 0.5 + 0.04
            others.append(_qplate(self, f"{id_}-sole{k}", [(-0.15, -hw), (0.15, -hw), (0.15, hw), (-0.15, hw)],
                                  P(x, 0, zs - 0.0125), V(1, 0, 0), V(0, 1, 0), 0.026))
            bearings.append(_xv(P(x, 0, zs - 0.0255)))
        else:
            bearings.append(_xv(P(x, 0, zs)))
    fl = _join(self, f"{id_}-flanges", *flanges)
    cv = _join(self, f"{id_}-covers", *covers) if covers else None
    stj = _join(self, f"{id_}-stiffeners", *stiffs)
    if rivets:
        fl = _rivet(self, f"{id_}-flange-rivets", fl, r_fl, rivet)
        stj = _rivet(self, f"{id_}-stiffener-rivets", stj, r_st, rivet)
        if cv:
            cv = _rivet(self, f"{id_}-cover-rivets", cv, r_cv, rivet)
    body = _join(self, id_ + "-parts" if slot else id_, *[p for p in (fl, cv, stj) if p], *others)
    if slot:
        body = self.mat(id_, body, slot)
    return body, {"bearings": bearings, "stiffeners": len(sx), "pieces": len(flanges) + len(covers) + len(stiffs) + len(others)}


def _arc(xi, ratio):
    """Height, as a fraction of the rise, of a circular arc over a span (xi = x / span, ratio = rise / span);
    the parabola 4 xi (1 - xi) when the rise is 0."""
    if ratio <= 1e-9:
        return 4 * xi * (1 - xi)
    R = (0.25 + ratio * ratio) / (2 * ratio)                       # radius / span
    return (math.sqrt(max(0.0, R * R - (xi - 0.5) ** 2)) - (R - ratio)) / ratio


def _under(path, x):
    """Height of the polyline path [(x, z)] at x (clamped to its ends); the segment is chosen at the defaults."""
    xn = _n(x)
    for i, ((x0, z0), (x1, z1)) in enumerate(zip(path, path[1:])):
        if xn <= _n(x1) or i == len(path) - 2:
            t = (xn - _n(x0)) / (_n(x1) - _n(x0))
            if t <= 0:
                return z0
            if t >= 1:
                return z1
            return z0 + (z1 - z0) * ((x - x0) / (x1 - x0))
    return path[-1][1]


def _mitre3(p0, p1, p2):
    d0 = _n2((p1[0] - p0[0], p1[1] - p0[1]))
    d1 = _n2((p2[0] - p1[0], p2[1] - p1[1]))
    return (d0[0] + d1[0], 0.0, d0[1] + d1[1])


# --- built-up members ----------------------------------------------------------------------------------

def laced_column(self, id_, a, b, width=0.36, depth=None, angle="L80x10", angles=4, lacing="FL50x8", system="single",
                 incline=0.95, tie=None, batten_pitch=0.9, up=None, rivets=True, rivet=None, slot=None, controls=None):
    """A built-up column or strut of angles tied by lacing bars, batten plates or packings. Metres, radians.

    Frame: axis a -> b; `up` (default +Y, or +X when the axis is along Y) is the section's depth direction,
    width along u = up x axis. angles=4: four angles at the corners of a width x depth box (depth defaults
    to width), heels out and legs along the faces; system "single" (a zigzag of `lacing` flats at about
    `incline` radians from the axis on every face), "double" (two crossed zigzags, the outer one packed
    over the inner at its ends and riveted where they cross) or "batten" (batten plates at about
    `batten_pitch`); tie plates (`tie` long, default 0.9 x the larger side) close both ends, as practice
    required. angles=2: a double-angle strut, two angles back to back `width` apart (the gusset's
    thickness, or more) with their outstanding legs in line, `depth` ignored, joined by packing plates in
    the gap at `batten_pitch`. Rivets (detail) at every bar end, crossing, tie, batten and packing.
    Controls: the end points a and b, width, depth and tie may be X (e.g. b=[0, 0, C("eave")]; pass the
    module's controls dict as `controls=`). The numbers of lacing bars, battens and packings come from the
    length's DEFAULT value (incline, batten_pitch), so the bars stretch with the control: the lacing angle
    changes, every bar still lands on its angles. Four L80 x 3 m with single lacing: ~750 triangles.
    Example: g.laced_column("col", [0, 0, 0], [0, 0, 6], width=0.4, angle="L80x10", system="double")"""
    _setenv(controls)
    a, b = _qv(a), _qv(b)
    dv0 = _qs(b, a)
    Lm = qsqrt(_qd(dv0, dv0))
    axn = _qm(dv0, 1 / Lm)
    up = up or ((0, 1, 0) if abs(axn[1].n) < 0.9 else (1, 0, 0))
    axv = [c.n for c in axn]
    _, side, u = _qframe(a, b, up)
    side, u = [c.n for c in side], [c.n for c in u]                 # section frame at the defaults
    W = _qq(width)
    Dp = _qq(depth if depth is not None else width)
    s = iron_section(angle)
    la, lb, lt = float(s["a"]), float(s["b"]), float(s["t"])
    ls = iron_section(lacing)
    bw, bt = float(ls["b"]), float(ls["t"])
    tie = _qq(tie) if tie is not None else qmax(W, Dp) * 0.9
    plateL = iron_outline

    def at(k, uu, ww):                                            # axial distance k, offsets along side and up
        return _qa(_qa(a, _qm(axn, k)), _qa(_qm(side, uu), _qm(u, ww)))

    ang, bars, rp = [], [], []
    if angles == 2:
        for k, sg in enumerate((1, -1)):
            o = at(0, W * (sg * 0.5), -la * 0.5)
            e = at(Lm, W * (sg * 0.5), -la * 0.5)
            ang.append(_qprism(self, f"{id_}-angle{k}", plateL(angle), o, e, axn, u, _qm(side, sg)))
        npk = max(2, int(round(Lm.n / batten_pitch)) + 1)
        for j in range(npk):
            kc = Q(0.06) if j == 0 else (Lm - 0.06 if j == npk - 1 else Lm * (j / (npk - 1)))
            q = at(kc, 0.0, 0.0)
            bars.append(_qprism(self, f"{id_}-pack{j}", _flat(la, W + 0.002),
                                _qs(q, _qm(axv, 0.05)), _qa(q, _qm(axv, 0.05)), axn, u, side))
            for sg in (1, -1):
                rp.append(at(kc, (W * 0.5 + lt + 0.004) * sg, lt * 0.5))
    else:
        assert angles == 4, "angles is 2 or 4"
        assert system in ("single", "double", "batten"), f"lacing system {system!r}"
        for k, (cu, cw) in enumerate(((1, 1), (-1, 1), (-1, -1), (1, -1))):
            o, e = at(0, W * (cu * 0.5), Dp * (cw * 0.5)), at(Lm, W * (cu * 0.5), Dp * (cw * 0.5))
            ang.append(_qprism(self, f"{id_}-angle{k}", plateL(angle), o, e, axn, _qm(side, -cu), _qm(u, -cw)))
        for fi, (fc, fs) in enumerate((("w", 1), ("w", -1), ("u", 1), ("u", -1))):
            half = W * 0.5 - (la + lt) * 0.5 if fc == "w" else Dp * 0.5 - (lb + lt) * 0.5     # rivet lines on the legs
            plate_w = (W if fc == "w" else Dp) - 0.01

            def fp(k, c, lift, fc=fc, fs=fs):
                return at(k, c, (Dp * 0.5 + lift) * fs) if fc == "w" else at(k, (W * 0.5 + lift) * fs, c)
            nrm = _qm(u if fc == "w" else side, fs)
            across = side if fc == "w" else u
            flat = _flat(plate_w, bt)
            for j, (k0, k1) in enumerate(((Q(0.0), tie), (Lm - tie, Lm))):
                bars.append(_qprism(self, f"{id_}-tie{fi}{j}", flat, fp(k0, 0.0, bt * 0.5), fp(k1, 0.0, bt * 0.5), axn, across, nrm))
                for c in (-half, half):                                   # a row along each angle under the plate
                    rp += _qrow(fp(k0, c, bt + 0.004), fp(k1, c, bt + 0.004), 0.075, 0.04)
            k0, k1 = tie, Lm - tie
            if system == "batten":
                nbat = max(1, int(round((k1 - k0).n / batten_pitch)))
                bl = qmin(tie * 0.7, (k1 - k0) / nbat * 0.5)
                for j in range(1, nbat):
                    kc = k0 + (k1 - k0) * (j / nbat)
                    bars.append(_qprism(self, f"{id_}-batten{fi}-{j}", flat, fp(kc - bl / 2, 0.0, bt * 0.5),
                                        fp(kc + bl / 2, 0.0, bt * 0.5), axn, across, nrm))
                    for kk in (kc - bl / 2 + 0.035, kc + bl / 2 - 0.035):
                        rp += [fp(kk, c, bt + 0.004) for c in (-half, half)]
                continue
            nbars = max(1, int(round((k1 - k0).n / (2 * half.n / math.tan(incline)))))
            step = (k1 - k0) / nbars
            for sys_ in ((0,) if system == "single" else (0, 1)):
                lift = bt * sys_
                for j in range(nbars):
                    ka, kb = k0 + step * j, k0 + step * (j + 1)
                    ca, cb = (-half, half) if (j + sys_) % 2 == 0 else (half, -half)
                    p0, p1 = fp(ka, ca, lift + bt * 0.5), fp(kb, cb, lift + bt * 0.5)
                    dd = [c.n for c in _qs(p1, p0)]                    # the bar's frame at the defaults
                    dv = [c / math.sqrt(sum(e * e for e in dd)) for c in dd]
                    ext = bw * 0.5 + 0.01
                    p0 = _qs(p0, _qm(dv, ext))
                    p1 = _qa(p1, _qm(dv, ext))
                    bsd = _qc(nrm, dv)
                    bars.append(_qprism(self, f"{id_}-bar{fi}-{sys_}-{j}", plateL(lacing), p0, p1, dv, bsd, nrm))
                    rp.append(fp(ka, ca, lift + bt + 0.004))
                    if j == nbars - 1:
                        rp.append(fp(kb, cb, lift + bt + 0.004))
                    if sys_ == 1:
                        rp.append(fp((ka + kb) * 0.5, 0.0, 2 * bt + 0.004))             # where the systems cross
                        for tag, kk, cc in (("a", ka, ca), ("b", kb, cb)):               # packing under the outer bar
                            q = fp(kk, cc, bt * 0.5)
                            bars.append(_qprism(self, f"{id_}-pack{fi}-{j}{tag}", _flat(bw, bt),
                                                _qs(q, _qm(axv, bw * 0.5)), _qa(q, _qm(axv, bw * 0.5)), axn, across, nrm))
    an = _join(self, f"{id_}-angles", *ang)
    br = _join(self, f"{id_}-bars", *bars)
    if rivets:
        br = _rivet(self, f"{id_}-rivets", br, rp, rivet)
    body = _join(self, id_ + "-parts" if slot else id_, an, br)
    return self.mat(id_, body, slot) if slot else body


def box_member(self, id_, a, b, width=0.36, depth=0.36, plate=0.012, angle="L70x9", overhang=0.02, up=(0, 0, 1),
               rivets=True, pitch=0.12, rivet=None, slot=None, controls=None):
    """A box section built of plates and angles: two web plates (the u faces), two cover plates (the w
    faces, `overhang` past the webs) and four angles inside the corners, riveted through both legs.
    Frame as rolled(): axis a -> b, `up` the w direction; width (u) and depth (w) are the outer sizes of the
    webs. Points and sizes may be X (rivet rows are rivetLine guides at `pitch`); pass the module's controls
    dict as `controls=` with them (Built by Opus 5.5 (medium)): points and sizes stay exact expressions, the
    frame and the caps are worked out at the defaults, so the document stays small. 4 x 12 + 4 x 20 = 128
    triangles. Example: g.box_member("chord", [0, 0, 0], [C("length"), 0, 0], width=0.4, depth=0.5, controls=controls)"""
    if controls is not None and _symbolic(a, b, width, depth, plate, overhang, up, angle):
        return _box_q(self, id_, a, b, width, depth, plate, angle, overhang, up, rivets, pitch, rivet, slot, controls)
    a, b, axis, side, u = _frame(a, b, up, 0.0)
    W, Dp, t, ov = X(width), X(depth), X(plate), X(overhang)
    s = iron_section(angle)
    la, lb, lt = X(s["a"]), X(s["b"]), X(s["t"])
    webs, covers, angs, rows_c, rows_w = [], [], [], [], []
    for sg in (1, -1):
        ow = _vmul(side, (W * 0.5 - t * 0.5) * sg)
        webs.append(_prism(self, f"{id_}-webplate-{'n' if sg > 0 else 's'}", iron_outline(dict(kind="FL", b=Dp, t=t)),
                           _vadd(a, ow), _vadd(b, ow), axis, u, _vmul(side, sg), hand=-sg))
        oc = _vmul(u, (Dp * 0.5 + t * 0.5) * sg)
        covers.append(_prism(self, f"{id_}-cover{'t' if sg > 0 else 'b'}", iron_outline(dict(kind="FL", b=W + ov * 2, t=t)),
                             _vadd(a, oc), _vadd(b, oc), axis, side, _vmul(u, sg), hand=sg))
    for k, (cu, cw) in enumerate(((1, 1), (-1, 1), (-1, -1), (1, -1))):
        o = _vadd(_vmul(side, (W * 0.5 - t) * cu), _vmul(u, Dp * 0.5 * cw))
        angs.append(_prism(self, f"{id_}-angle{k}", iron_outline(angle), _vadd(a, o), _vadd(b, o), axis,
                           _vmul(side, -cu), _vmul(u, -cw), hand=cu * cw))
        gc = _vadd(_vmul(side, (W * 0.5 - t - (la + lt) * 0.5) * cu), _vmul(u, (Dp * 0.5 + t + 0.004) * cw))
        rows_c.append((_vadd(a, gc), _vadd(b, gc)))
        gw = _vadd(_vmul(side, (W * 0.5 + 0.004) * cu), _vmul(u, (Dp * 0.5 - (lb + lt) * 0.5) * cw))
        rows_w.append((_vadd(a, gw), _vadd(b, gw)))
    wj, cj = _join(self, f"{id_}-webs", *webs), _join(self, f"{id_}-covers", *covers)
    if rivets:
        r = dict(RIVET)
        r.update(rivet or {})
        done = []
        for tag, part, rows in (("webs", wj, rows_w), ("covers", cj, rows_c)):
            gs = [self.guide(f"{id_}-{tag}-row{k}", _out(p0), _out(p1)) for k, (p0, p1) in enumerate(rows)]
            done.append(self.rivets(f"{id_}-{tag}-riveted", part, _join(self, f"{id_}-{tag}-rows", *gs), pitch,
                                    radius=r["radius"], height=r["height"]))
        wj, cj = done
    body = _join(self, id_ + "-parts" if slot else id_, wj, cj, *angs)
    return self.mat(id_, body, slot) if slot else body


def _box_q(self, id_, a, b, width, depth, plate, angle, overhang, up, rivets, pitch, rivet, slot, controls):
    """box_member with exact points and sizes and the frame at the defaults (see box_member)."""
    _setenv(controls)
    a, b = _qv(a), _qv(b)
    axis, side, u = _nframe(a, b, None if up is None else _qv(up), 0.0)
    W, Dp, t, ov = _qq(width), _qq(depth), _qq(plate), _qq(overhang)
    s = iron_section(angle)
    la, lb, lt = _qq(s["a"]), _qq(s["b"]), _qq(s["t"])
    angle_loop = [(_qq(p), _qq(q)) for p, q in iron_outline(angle)]
    webs, covers, angs, rows_c, rows_w = [], [], [], [], []
    for sg in (1, -1):
        ow = _qm(side, (W * 0.5 - t * 0.5) * sg)
        webs.append(_qprism(self, f"{id_}-webplate-{'n' if sg > 0 else 's'}", _flat(Dp, t), _qa(a, ow), _qa(b, ow),
                            axis, u, [c * sg for c in side]))
        oc = _qm(u, (Dp * 0.5 + t * 0.5) * sg)
        covers.append(_qprism(self, f"{id_}-cover{'t' if sg > 0 else 'b'}", _flat(W + ov * 2, t), _qa(a, oc), _qa(b, oc),
                              axis, side, [c * sg for c in u]))
    for k, (cu, cw) in enumerate(((1, 1), (-1, 1), (-1, -1), (1, -1))):
        o = _qa(_qm(side, (W * 0.5 - t) * cu), _qm(u, Dp * (0.5 * cw)))
        angs.append(_qprism(self, f"{id_}-angle{k}", angle_loop, _qa(a, o), _qa(b, o), axis,
                            [-cu * c for c in side], [-cw * c for c in u]))
        gc = _qa(_qm(side, (W * 0.5 - t - (la + lt) * 0.5) * cu), _qm(u, (Dp * 0.5 + t + 0.004) * cw))
        rows_c.append((_qa(a, gc), _qa(b, gc)))
        gw = _qa(_qm(side, (W * 0.5 + 0.004) * cu), _qm(u, (Dp * 0.5 - (lb + lt) * 0.5) * cw))
        rows_w.append((_qa(a, gw), _qa(b, gw)))
    wj, cj = _join(self, f"{id_}-webs", *webs), _join(self, f"{id_}-covers", *covers)
    if rivets:
        r = dict(RIVET)
        r.update(rivet or {})
        done = []
        for tag, part, rows in (("webs", wj, rows_w), ("covers", cj, rows_c)):
            gs = [self.guide(f"{id_}-{tag}-row{k}", _xv(p0), _xv(p1)) for k, (p0, p1) in enumerate(rows)]
            done.append(self.rivets(f"{id_}-{tag}-riveted", part, _join(self, f"{id_}-{tag}-rows", *gs), pitch,
                                    radius=r["radius"], height=r["height"]))
        wj, cj = done
    body = _join(self, id_ + "-parts" if slot else id_, wj, cj, *angs)
    return self.mat(id_, body, slot) if slot else body


# --- connections ---------------------------------------------------------------------------------------

def gusset_plate(self, id_, at, members, normal=(0, 1, 0), x_dir=(1, 0, 0), thickness=0.012, reach=0.25, edge=0.03,
                 flush=None, rivets=True, pitch=0.075, rivet=None, slot=None, controls=None):
    """A gusset plate at a node, sized to the members meeting there. Metres.

    Frame: the plate lies in the plane through `at` with `normal`, centred on it, `thickness` thick; x_dir
    orients its outline. members: (direction, width) or (direction, width, reach) of each member ending on
    the node, directions in 3D (projected into the plane); the plate runs `reach` along each and `edge`
    past its sides. flush: optional (point, direction) of a chord heel line the plate's edge stops on.
    Rivets (detail, both faces) at `pitch` along each member's centre line inward from its reach.
    Controls: `at`, the member directions, widths and reaches, `normal`, `x_dir` and `flush` may be X (e.g.
    the direction of a diagonal (C("span") / 8, 0, C("depth"))); pass the module's controls dict as
    `controls=`. Each corner of the plate lies on a member's own direction at its width, so the plate keeps
    its members covered at every control value; which corners bound it (the convex hull) and the rivet
    counts are worked out at the defaults. A prism of the convex outline: 20-40 triangles.
    Example: g.gusset_plate("g", [3, 0, 0], [((1, 0, 1), 0.08), ((-1, 0, 1), 0.08), ((1, 0, 0), 0.1)])"""
    _setenv(controls)
    o = _qv(at)
    nn = _qu(_qv(normal))
    xd = _qv(x_dir)
    e1 = _qu(_qs(xd, _qm(nn, _qd(nn, xd))))
    e2 = _qc(nn, e1)

    def proj(v):
        v = _qv(v)
        return (_qd(v, e1), _qd(v, e2))
    webs, pts = [], []
    off = float(thickness) * 0.5 + 0.004
    for m in members:
        d = _n2(proj(m[0]))
        L = _qq(m[2] if len(m) > 2 else reach)
        webs.append((d, _qq(m[1]) * 0.5, L))
        along = _qa(_qm(e1, d[0]), _qm(e2, d[1]))
        j = 0
        while L.n - 0.03 - j * pitch > 0.04:
            k = L - 0.03 - j * pitch
            pts += [_qa(_qa(o, _qm(along, k)), _qm(nn, off * sg)) for sg in (1, -1)]
            j += 1
    heels = []
    if flush:
        p3, t3 = flush
        heels.append((proj(_qs(p3, o)), _n2(proj(t3))))
    poly = _gusset_poly((Q(0.0), Q(0.0)), webs, heels, edge)
    part = _qplate(self, id_ + "-plate" if (rivets or slot) else id_, poly, o, e1, e2, thickness)
    if rivets:
        part = _rivet(self, id_ + "-riveted" if slot else id_, part, pts, rivet)
    return self.mat(id_, part, slot) if slot else part


def splice_plate(self, id_, at, along, normal, length=0.40, width=0.20, thickness=0.012, rows=2, pitch=0.07, gap=0.05,
                 sink=0.001, rivets=True, rivet=None, slot=None):
    """A cover (splice) plate over a butt joint on a member's face, riveted on both sides of the joint.
    Frame: `at` is the joint's point on the face, `normal` the face's outward normal, `along` the member
    axis; the plate stands `thickness` proud (sunk `sink` for contact), `length` along the axis and
    `width` across, with `rows` rivet rows on each side, the two groups `gap` apart about the joint.
    Points and sizes may be X (rivet rows along guides at `pitch`). 12 triangles.
    Example: g.splice_plate("splice", [2.0, 0, 0.3], (1, 0, 0), (0, 0, 1), length=0.5, width=0.18)"""
    nrm = _unit(_p(normal))
    al = _unit(_vsub(_p(along), _vmul(nrm, _dot(nrm, _p(along)))))
    acr = _cross(nrm, al)
    o = _vsub(_p(at), _vmul(nrm, sink))
    L, Wd, t = X(length), X(width), X(thickness)
    ctr = _vadd(o, _vmul(nrm, t * 0.5))
    part = _prism(self, id_ + "-plate" if (rivets or slot) else id_, iron_outline(dict(kind="FL", b=L, t=Wd)),
                  _vsub(ctr, _vmul(nrm, t * 0.5)), _vadd(ctr, _vmul(nrm, t * 0.5)), nrm, al, acr, hand=1)
    if rivets:
        r = dict(RIVET)
        r.update(rivet or {})
        guides = []
        lift = _vadd(o, _vmul(nrm, t + 0.004))
        nr = int(rows)
        for sd in (1, -1):
            for k in range(nr):
                c = (k + 0.5) / nr - 0.5
                p0 = _vadd(lift, _vadd(_vmul(al, X(gap) * 0.5 * sd), _vmul(acr, Wd * c * 0.8)))
                p1 = _vadd(lift, _vadd(_vmul(al, L * 0.5 * sd), _vmul(acr, Wd * c * 0.8)))
                guides.append(self.guide(f"{id_}-row{'p' if sd > 0 else 'm'}{k}", _out(p0), _out(p1)))
        part = self.rivets(id_ + "-riveted" if slot else id_, part, _join(self, f"{id_}-rows", *guides), pitch,
                           radius=r["radius"], height=r["height"])
    return self.mat(id_, part, slot) if slot else part


def base_plate(self, id_, at=(0, 0, 0), size=(0.6, 0.6), thickness=0.03, bolts=4, inset=0.07, nut=0.024, close=True,
               slot=None):
    """A column base plate with anchor bolts. Frame: `at` is the centre of the plate's underside (the
    foundation's top); plate `size` (x, y) and `thickness`; bolts 4 (corners), 6 (and mid x-sides) or 8
    (and mid y-sides), `inset` from the edges. close=True: nuts are geometry carrying washer, faced nut and
    bolt end as detail (kitlib close_bolts: a column base is within reach); close=False: hex heads and
    washers as detail only. The column stands on the top face, z = at.z + thickness. Numbers only.
    4 bolts: 12 + 4 x 24 triangles. Example: g.base_plate("base", [0, 0, 0], (0.7, 0.7), bolts=8)"""
    sx, syz, t = float(size[0]), float(size[1]), float(thickness)
    ox, oy, oz = (float(c) for c in at)
    self.n(id_ + "-stock", "cube", size=[sx, syz, t])
    plate = self.xf(id_ + "-plate", id_ + "-stock", t=[ox, oy, oz + t * 0.5])
    hx, hy = sx * 0.5 - inset, syz * 0.5 - inset
    pos = [(hx, hy), (-hx, hy), (-hx, -hy), (hx, -hy)]
    if bolts >= 6:
        pos += [(0, hy), (0, -hy)]
    if bolts >= 8:
        pos += [(hx, 0), (-hx, 0)]
    pos = [(ox + x, oy + y) for x, y in pos]
    if close:
        seated, nuts = self.close_bolts(id_ + "-bolts", plate, pos, oz + t, nut)
        body = _join(self, id_ + "-parts" if slot else id_, seated, nuts)
    else:
        self.points(id_ + "-at", [[x, y, oz + t + 0.01] for x, y in pos])
        body = self.fasteners(id_ + "-parts" if slot else id_, plate, id_ + "-at", head="hex", radius=nut, washer=nut * 1.7)
    return self.mat(id_, body, slot) if slot else body


# ----------------------------------------------------------------------------------------------- attach
for _f in (rolled, truss, plate_girder, laced_column, box_member, gusset_plate, splice_plate, base_plate):
    setattr(Graph, _f.__name__, _f)
for _f in (iron_section, iron_outline, mitre):
    setattr(Graph, _f.__name__, staticmethod(_f))
