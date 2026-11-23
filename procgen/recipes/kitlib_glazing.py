"""Curved glazing helpers for kit builders: glazed vaults and domes, fan-ribbed domes, lanterns, leaded glass.
Built by Opus 5.5 (medium), 2026-11-16; controls-following vaults, proud ribs and wall plates Built by Opus 5.5
(medium), after the rail-hall usability test (docs/experiments/tool-pilot.md).

kitlib.py imports this module at its end, so builders get the helpers as `Graph` methods:

    g.glazed_vault("hall", C("width"), C("length"), section="segmental", rise=C("width") * 0.25 + 2,
                   springing=C("level"), wall_plate=True, end_screens=(True, True), controls=CONTROLS)
    g.glazed_dome("dome", 6.0, lantern=True)
    g.fan_dome("sunburst", 7.0, fans=8, fan_ribs=5)
    g.ridge_lantern("monitor", 18.0, 2.4, 1.2, at=[0, 0, 9.0])
    g.glazed_surface("skin", lambda u, v: [4 * u, 6 * v, 1.5 * math.sin(PI * u)], [0, (0.5, "purlin"), 1], [0, 0.5, 1])

Construction, as in Laeken, Kew and the iron station sheds: deep main ribs (I section) at a bay spacing carry
purlins or rings; T-section glazing bars run between them; panes rest on the bars' flanges a little below the
bar tops, kept clear of each web, lapped where no bar runs. The main ribs of vaults and domes carry a cap rib
standing proud of the glass (`proud`, default 0.12 m), so they read from outside; purlins, rings and bars stay
under or flush. Every member is a closed `surface` shell that overlaps the members it meets, and every pane
rests on its bars, so each structure audits as one piece.

Frame: metres, radians, Z-up. A glazed surface is a point function (u, v) -> [x, y, z]; members run along lines
of constant u or v and panes fill the cells between them; the glass lies on the surface, the bar flanges just
under it, ribs and purlins below (the side opposite the surface normal d/du x d/dv, reversed by flip=True).
Controls: dimensions (span, length, rise, springing, bay, radius, gauge, `at`) may be X expressions (pass the
module's controls dict as `controls=` whenever one uses a control). glazed_vault follows them fully: the arch
is an expression of span and rise for every section (solved afresh at each value), member and pane sections
turn with it (glazed_surface `frame=`), and the bays are `line`/`instance` copies whose count is an expression
of the length (round(length / bay)). Still taken at the defaults: bars per bay, purlins and pane laps up the
arch, end-screen mullions, lantern posts (these stretch evenly); in domes all counts and the profile's shape
(exact when the rise is a fixed share of the radius); in glazed_surface without `frame=` the section
directions.

Each helper returns its node id (a join of frame and glass) and records counts in `g.glazing[id]`; the frame
alone is node `{id}-frame` (slot `slot`; roofs and caps carry their own slots), the glass `{id}-glass`.
Tests and a sample sheet: recipes/test_kitlib_glazing.py.
"""
import math

from kitlib import PI, X, Graph, _num, _v, atan2 as _xatan2, cos as _xcos, gt as _xgt, mx as _xmax, pw as _xpow, \
    rnd as _xround, sin as _xsin, sqrt as _xsqrt

__all__ = []

EPS = 1e-9

# --- numeric evaluation of expressions at the controls' defaults ------------------------------------------

_BIN = {"+": lambda a, b: a + b, "-": lambda a, b: a - b, "*": lambda a, b: a * b, "/": lambda a, b: a / b,
        "max": max, "min": min, "pow": lambda a, b: a ** b, "atan2": math.atan2, "mod": math.fmod,
        ">": lambda a, b: 1.0 if a > b else 0.0, "<": lambda a, b: 1.0 if a < b else 0.0}
_UN = {"sin": math.sin, "cos": math.cos, "tan": math.tan, "sqrt": math.sqrt, "abs": abs, "neg": lambda a: -a,
       "floor": math.floor, "ceil": math.ceil, "round": lambda a: float(math.floor(a + 0.5))}


def _env(controls):
    return {k: float(c["value"]) if isinstance(c, dict) else float(c) for k, c in (controls or {}).items()}


def _ev(x, env):
    x = _v(x)
    if _num(x):
        return float(x)
    if isinstance(x, str):
        assert x.startswith("$"), f"glazing: field {x} cannot be used in a dimension"
        assert x[1:] in env, f"glazing: control {x[1:]} has no value here; pass the module's controls as controls="
        return env[x[1:]]
    op, *args = x
    if op == "if":
        return _ev(args[1], env) if _ev(args[0], env) != 0 else _ev(args[2], env)
    vals = [_ev(a, env) for a in args]
    return _BIN[op](*vals) if len(vals) == 2 else _UN[op](vals[0])


def _out(x):
    """An X or number for a document: plain numbers rounded to the micrometre."""
    x = _v(x)
    return round(x, 6) if _num(x) else X(x)


def _lin(terms):
    """Sum of coef * expr over (coef, expr) pairs (coef a number), skipping zeros and folding numbers.
    Built by Opus 5.5 (medium)."""
    k, out = 0.0, None
    for c, e in terms:
        e = _v(e)
        if abs(c) < 1e-12 or (_num(e) and e == 0):
            continue
        if _num(e):
            k += c * e
            continue
        t = X(e) if c == 1 else X(e) * c
        out = t if out is None else out + t
    if out is None:
        return k
    return out + k if abs(k) > 1e-12 else out


def _inv3(m):
    """Inverse of the 3 x 3 matrix whose columns are m[0], m[1], m[2]."""
    a = [[m[c][r] for c in range(3)] for r in range(3)]
    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
           + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    assert abs(det) > 1e-12, "glazing: degenerate frame"
    co = lambda r, c: (a[(r + 1) % 3][(c + 1) % 3] * a[(r + 2) % 3][(c + 2) % 3] -
                       a[(r + 1) % 3][(c + 2) % 3] * a[(r + 2) % 3][(c + 1) % 3])
    return [[co(c, r) / det for c in range(3)] for r in range(3)]


def _const(x, controls):
    """x as a number when it takes the same value at the defaults and at both ends of every control (and at
    all-low and all-high), else None. Lets shapes whose ratio does not change stay numeric."""
    env = _env(controls)
    v0 = _ev(x, env)
    tries = []
    for k, c in (controls or {}).items():
        if isinstance(c, dict):
            for end in ("min", "max"):
                tries.append(dict(env, **{k: float(c[end])}))
    if controls:
        tries.append({k: float(c["min"]) if isinstance(c, dict) else float(c) for k, c in controls.items()})
        tries.append({k: float(c["max"]) if isinstance(c, dict) else float(c) for k, c in controls.items()})
    for e in tries:
        try:
            if abs(_ev(x, e) - v0) > 1e-9 * max(1.0, abs(v0)):
                return None
        except (ValueError, ZeroDivisionError):
            return None
    return v0


# --- small vector helpers (numbers) ----------------------------------------------------------------------

def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _mul(a, k): return (a[0] * k, a[1] * k, a[2] * k)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(_dot(a, a))
    assert n > 1e-12, "glazing: degenerate direction (a surface pole or a zero-length line)"
    return _mul(a, 1.0 / n)


# --- member sections (gauge 1, metres): profiles counter-clockwise in (across, normal) ---------------------

SIZES = {
    "bar": (0.036, 0.005, 0.010, 0.028),   # T glazing bar: flange width, flange thickness, web width, web top above glass
    "rib": (0.11, 0.24),                   # main rib under a bar: I width, depth
    "ring": (0.08, 0.14),                  # dome ring under a bar: I width, depth
    "purlin": (0.06, 0.10),                # purlin under the bars: I width, depth
    "edge": (0.10, 0.19),                  # eaves, curb, sill or crown ring: rectangular kerb width, depth
    "cap": (0.07, 0.012, 0.010),           # cap rib proud of the glass on a main rib: flange width, thickness, web
                                           # (the web no wider than the bar's, which the panes keep clear of)
}
KINDS = ("bar", "rib", "ring", "purlin", "edge", "lap")


def _t_profile(wf, tf, tw, h):
    return [(-wf / 2, -tf), (0.0, -tf), (wf / 2, -tf), (wf / 2, 0.0), (tw / 2, 0.0), (tw / 2, h), (-tw / 2, h), (-tw / 2, 0.0), (-wf / 2, 0.0)]


def _i_profile(w, d, top):
    f, t, b = max(0.006, 0.11 * w), max(0.006, 0.09 * w), top - d
    return [(-w / 2, b), (0.0, b), (w / 2, b), (w / 2, b + f), (t / 2, b + f), (t / 2, top - f), (w / 2, top - f),
            (w / 2, top), (0.0, top), (-w / 2, top), (-w / 2, top - f), (-t / 2, top - f), (-t / 2, b + f), (-w / 2, b + f)]


def _cap_profile(top, sizes, low=None):
    """The outer cap of a main rib: a web rising from just under the bar web's top to `top` above the glass
    line, an outer flange there (gauge 1). Built by Opus 5.5 (medium)."""
    wf, tf, tw = sizes["cap"]
    y0 = sizes["bar"][3] - 0.006 if low is None else low
    y1 = max(top, y0 + tf + 0.004)
    return [(-tw / 2, y0), (0.0, y0), (tw / 2, y0), (tw / 2, y1 - tf), (wf / 2, y1 - tf), (wf / 2, y1), (0.0, y1),
            (-wf / 2, y1), (-wf / 2, y1 - tf), (-tw / 2, y1 - tf)]


def _profiles(kind, sizes):
    """Profiles of a line's members, and the half-width a pane keeps clear of at glass level (gauge 1)."""
    wf, tf, tw, h = sizes["bar"]
    bar = _t_profile(wf, tf, tw, h)
    top = -tf + 0.001                                     # members under a bar reach 1 mm into its flange
    if kind == "bar":
        return [bar], tw / 2
    if kind in ("rib", "ring"):
        w, d = sizes[kind]
        return [bar, _i_profile(w, d, top)], tw / 2
    if kind == "purlin":
        w, d = sizes["purlin"]
        return [_i_profile(w, d, top)], None
    if kind == "edge":
        w, d = sizes["edge"]
        return [[(-w / 2, h - d), (0.0, h - d), (w / 2, h - d), (w / 2, h), (0.0, h), (-w / 2, h)]], w / 2 + 0.006
    return [], None                                       # lap


def _lines(spec):
    """Normalised lines: numbers, (at, kind), (at, kind, from, to) or dicts. '~kind': no member, its clearance."""
    out = []
    for e in spec:
        if isinstance(e, dict):
            d = dict(e)
        elif isinstance(e, (tuple, list)):
            d = {"at": e[0], "kind": e[1] if len(e) > 1 else "bar"}
            if len(e) > 2:
                d["from"], d["to"] = e[2], e[3]
        else:
            d = {"at": e, "kind": "bar"}
        k = d["kind"]
        d["member"] = not k.startswith("~")
        d["kind"] = k.lstrip("~")
        assert d["kind"] in KINDS, f"glazing: line kind {k!r}; one of {KINDS} (prefix ~ for clearance only)"
        d.setdefault("from", -1e9)
        d.setdefault("to", 1e9)
        d["at"] = float(d["at"])
        out.append(d)
    return out


# --- shells -----------------------------------------------------------------------------------------------

class _Shells:
    """Closed shells with symbolic vertices (for the document) and numeric ones (at defaults, for orientation).
    Split into several `surface` nodes under the node's vertex and face limits."""

    def __init__(self):
        self.sym, self.num, self.faces, self.chunks = [], [], [], []

    def shell(self, verts, faces):
        """verts: list of (sym, num); faces index into verts, consistently wound. Flipped if inside out."""
        if len(self.sym) + len(verts) > 16000 or len(self.faces) + len(faces) > 23000:
            self.chunks.append((self.sym, self.faces))
            self.sym, self.num, self.faces = [], [], []
        base = len(self.sym)
        vol = 0.0
        for f in faces:
            a = verts[f[0]][1]
            for i in range(1, len(f) - 1):
                vol += _dot(a, _cross(verts[f[i]][1], verts[f[i + 1]][1]))
        for s, n in verts:
            self.sym.append(s)
            self.num.append(n)
        for f in faces:
            f = [base + i for i in (f if vol > 0 else reversed(f))]
            if len(f) == 4:                               # quads of curved members are not planar
                self.faces += [[f[0], f[1], f[2]], [f[0], f[2], f[3]]]
            else:
                self.faces.append(f)

    def sweep(self, rings, closed, profile=None, end_profile=None):
        """Rings of (sym, num) vertices along a member, each ring one profile (2D points `profile` of the first ring
        and `end_profile` of the last, counter-clockwise, for the end caps): a prism shell. Caps are triangles
        (float vertices are not coplanar enough for polygons)."""
        n, m = len(rings), len(rings[0])
        faces = []
        for i in range(n if closed else n - 1):
            j = (i + 1) % n
            for k in range(m):
                q = (k + 1) % m
                faces.append([i * m + k, i * m + q, j * m + q, j * m + k])
        if not closed:
            for a, b, c in _ear_clip(profile):
                faces.append([c, b, a])
            for a, b, c in _ear_clip(end_profile or profile):
                faces.append([(n - 1) * m + a, (n - 1) * m + b, (n - 1) * m + c])
        self.shell([p for r in rings for p in r], faces)

    def emit(self, g, id_):
        chunks = self.chunks + ([(self.sym, self.faces)] if self.faces else [])
        ids = [g.n(f"{id_}-s{i}", "surface", vertices=[[_out(c) for c in p] for p in s], faces=f)
               for i, (s, f) in enumerate(chunks)]
        if not ids:
            return None
        return ids[0] if len(ids) == 1 else g.join(f"{id_}-shells", *ids)

    def triangles(self):
        return sum(len(f) - 2 for _, fs in self.chunks + [(None, self.faces)] for f in fs)


def _ear_clip(poly):
    """Triangles (index triples, counter-clockwise) of a simple counter-clockwise polygon of (x, y)."""
    idx = list(range(len(poly)))
    area = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in idx)
    if area < 0:
        idx.reverse()
    cr = lambda o, a, b: (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    out = []
    while len(idx) > 3:
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if cr(a, b, c) <= 1e-15:
                continue
            if any(cr(a, b, poly[j]) > 1e-15 and cr(b, c, poly[j]) > 1e-15 and cr(c, a, poly[j]) > 1e-15
                   for j in idx if j not in (i0, i1, i2)):
                continue
            out.append((i0, i1, i2))
            del idx[k]
            break
        else:
            raise AssertionError(f"glazing: could not triangulate a section {poly}")
    out.append(tuple(idx))
    if area < 0:
        out = [(a, c, b) for a, b, c in out]
    return out


# --- the field: a point function evaluated symbolically and at defaults ------------------------------------

class _Field:
    def __init__(self, point, env, flip, wrap_u=False, frame=None):
        self.f, self.env, self.sign, self.wrap_u = point, env, -1.0 if flip else 1.0, wrap_u
        self.frame, self.bases = frame, {}
        self.cache = {}

    def sym(self, u, v):
        return [X(c) for c in self.f(u, v)]

    def lift(self, u, v, o):
        """A member or pane offset o (numbers, worked out at the defaults) as expressions that turn with the
        surface: o is written in the frame(u, v) basis at the defaults and rebuilt on the symbolic basis, so
        sections stay square to the surface at every control value. Without a frame, o itself.
        Built by Opus 5.5 (medium)."""
        if self.frame is None:
            return list(o)
        k = (round(u, 12), round(v, 12))
        if k not in self.bases:
            sym = [[_v(c) for c in b] for b in self.frame(u, v)]
            self.bases[k] = (sym, _inv3([[_ev(c, self.env) for c in b] for b in sym]))
        sym, inv = self.bases[k]
        c = [sum(inv[r][j] * o[j] for j in range(3)) for r in range(3)]
        return [_lin([(c[r], sym[r][i]) for r in range(3)]) for i in range(3)]

    def num(self, u, v):
        k = (round(u, 12), round(v, 12))
        if k not in self.cache:
            self.cache[k] = tuple(_ev(c, self.env) for c in self.f(u, v))
        return self.cache[k]

    def partials(self, u, v, ulo, uhi, vlo, vhi, h=1e-5):
        if self.wrap_u:
            a, b = u - h, u + h
        else:
            a, b = max(ulo, u - h), min(uhi, u + h)
        pu = _mul(_sub(self.num(b, v), self.num(a, v)), 1.0 / (b - a))
        a, b = max(vlo, v - h), min(vhi, v + h)
        pv = _mul(_sub(self.num(u, b), self.num(u, a)), 1.0 / (b - a))
        return pu, pv


def _straight(pts):
    if len(pts) < 3:
        return True
    a, b = pts[0], pts[-1]
    L = math.sqrt(_dot(_sub(b, a), _sub(b, a)))
    if L < 1e-9:
        return False
    t = _mul(_sub(b, a), 1.0 / L)
    for p in pts[1:-1]:
        w = _sub(p, a)
        q = _sub(w, _mul(t, _dot(w, t)))
        if _dot(q, q) > (1e-6 * L) ** 2:
            return False
    return True


# --- glazed_surface ----------------------------------------------------------------------------------------

def glazed_surface(self, id_, point, u_lines, v_lines, divisions=(1, 1), closed_u=False, open=None, gauge=1.0,
                   sizes=None, glass=0.008, clearance=0.0015, lift=0.0015, lap=0.002, flip=False, controls=None,
                   slot="iron", glass_slot="glass", leaded=None, frame=None, panes=True, proud=None,
                   cap_skip=None, cap_taper=(False, False)):
    """Glazing over a parametric surface: members on chosen u and v lines, a pane in every cell.

    point(u, v) -> [x, y, z] (numbers or X; u, v are Python floats); the surface normal d/du x d/dv (flip=True
    reverses it) points outside: glass on the surface, bar webs standing out of it, ribs and purlins under it.
    u_lines are lines of constant u (running in v), v_lines lines of constant v (running in u); each is u,
    (u, kind) or (u, kind, v_from, v_to), kind one of bar (T glazing bar), rib (bar on a deep I rib), ring (bar on
    a lighter I), purlin (I under the bars, panes lapped above it), edge (kerb: eaves, curb, sill, crown), lap
    (pane joint, no member); '~rib' etc. keeps that kind's clearance without the member (a line another part
    owns). The outermost lines bound the surface. A u line with a shorter v extent drops out and the panes either
    side of it merge (tapered meridians); closed_u wraps u (lines in [0, 1), one at 0; point periodic).
    divisions: stations per interval between lines (u, v); straight members keep only their ends.
    open(u0, u1, v0, v1) -> True leaves a cell unglazed (a lantern opening). gauge scales every section (SIZES,
    overridden per kind by sizes=); glass: pane thickness, `lift` above the flanges, `clearance` off each web.
    leaded: dict(pattern="diamond"|"rect", quarry=(w, h), came=0.006, slot="black"): lead cames on each pane as
    surface detail (meant for flat panes).
    frame(u, v) -> (t_u, t_v, n): three independent directions (X or numbers) at a point, for surfaces whose shape
    changes with the controls: each member and pane offset is written in that basis at the defaults and rebuilt
    on it, so sections stay square to the surface at every value (without it they keep their default
    directions). panes=False builds the members only (no glass); a surface with no member lines gives glass
    only. proud: height (gauge 1, metres above the glass line) of a cap rib on every `rib` line, an outer web
    and flange standing proud of the glass so the main ribs read from outside; cap_skip(a, b) -> True leaves the
    cap off a rib's interval [a, b] (a lantern opening); cap_taper=(start, end) brings the cap down to the bar
    web over the first or last interval (under a lantern). Built by Opus 5.5 (medium).
    Counts go to self.glazing[id_]. Example (a saddle on two purlins):
        g.glazed_surface("skin", lambda u, v: [4 * u, 6 * v, 1.5 * math.sin(PI * u)],
                         [(0, "edge"), (0.33, "purlin"), (0.66, "purlin"), (1, "edge")], [(0, "rib"), 0.5, (1, "rib")])"""
    env = _env(controls)
    G, Gn = X(gauge), _ev(gauge, env)
    sz = dict(SIZES, **(sizes or {}))
    us_l, vs_l = _lines(u_lines), _lines(v_lines)
    upos = sorted({d["at"] for d in us_l})
    vpos = sorted({d["at"] for d in vs_l})
    if closed_u:
        assert upos[0] == 0.0 and upos[-1] < 1.0, "closed_u: u lines in [0, 1), one at 0"
        upos = upos + [1.0]
    assert len(upos) >= 2 and len(vpos) >= 2, "glazing: at least two lines each way"
    ulo, uhi, vlo, vhi = upos[0], upos[-1], vpos[0], vpos[-1]
    for d in us_l:
        d["from"], d["to"] = max(d["from"], vlo), min(d["to"], vhi)
    for d in vs_l:
        d["from"], d["to"] = max(d["from"], ulo), min(d["to"], uhi)
    F = _Field(point, env, flip, wrap_u=closed_u, frame=frame)
    g_num = _num(_v(G))

    def off(u, v, o):                                   # a gauge-1 offset at (u, v) as expressions
        if g_num:
            return F.lift(u, v, _mul(o, Gn))
        return [G * c for c in F.lift(u, v, o)]

    def plus(a, b):
        a, b = _v(a), _v(b)
        if _num(b) and b == 0:
            return X(a)
        return X(a) + b

    def stations(pos, div):
        out = []
        for a, b in zip(pos, pos[1:]):
            out += [a + (b - a) * k / div for k in range(div)]
        return out + [pos[-1]]
    ust, vst = stations(upos, max(1, int(divisions[0]))), stations(vpos, max(1, int(divisions[1])))
    frames = {}

    def frame(u, v):
        if (u, v) not in frames:
            pu, pv = F.partials(u, v, ulo, uhi, vlo, vhi)
            frames[(u, v)] = (pu, pv, _mul(_unit(_cross(pu, pv)), F.sign))
        return frames[(u, v)]

    iron, glass_sh = _Shells(), _Shells()
    counts = {k: 0 for k in KINDS}

    u_index = {s: i for i, s in enumerate(ust)}
    v_index = {s: i for i, s in enumerate(vst)}

    def droop(u, v, along_u, p, a, n):
        """Slope (metres down per metre across, <= 0) of the pane chords on the +a (True) and -a (False) sides of a
        member at station (u, v): the panes are flat between stations, so the glass falls away from the tangent
        plane, at a polygon corner and on a coarse curve."""
        sl = {True: 0.0, False: 0.0}
        if along_u:
            i, seq = v_index[v], vst
            nb = [seq[j] for j in (i - 1, i + 1) if 0 <= j < len(seq)]
            qs = [F.num(u, w) for w in nb]
        else:
            i, seq = u_index[u], ust
            if closed_u:
                nb = [seq[i - 1] if i > 0 else seq[-2] - 1.0, seq[i + 1]]
            else:
                nb = [seq[j] for j in (i - 1, i + 1) if 0 <= j < len(seq)]
            qs = [F.num(w, v) for w in nb]
        for q in qs:
            c = _sub(q, p)
            ca, cn = _dot(c, a), _dot(c, n)
            if abs(ca) > 1e-9:
                side = ca > 0
                sl[side] = max(-1.0, min(sl[side], cn / abs(ca) - 0.02))
        return sl

    def member(d, along_u):
        lo, hi = d["from"], d["to"]
        st = [s for s in (ust if along_u else vst) if lo - EPS <= s <= hi + EPS]
        ring = along_u and closed_u and lo <= ulo + EPS and hi >= uhi - EPS
        if ring:
            st = st[:-1]
        pts = [F.num(s, d["at"]) if along_u else F.num(d["at"], s) for s in st]
        if not ring and _straight(pts):
            st, pts = [st[0], st[-1]], [pts[0], pts[-1]]
        profs, _ = _profiles(d["kind"], sz)

        def run(prof, idx, bend=True, taper=None):
            rings, flat = [], []
            for q in idx:
                s, pn = st[q], pts[q]
                u, v = (s, d["at"]) if along_u else (d["at"], s)
                pu, pv, n = frame(u, v)
                if ring:
                    t = _sub(pts[(q + 1) % len(pts)], pts[q - 1])
                else:
                    t = _sub(pts[min(q + 1, len(pts) - 1)], pts[max(q - 1, 0)])   # chords: members are polylines
                a = _unit(_cross(n, t))
                ps = F.sym(u, v)
                sl = droop(u, v, along_u, pn, a, n) if bend else {True: 0.0, False: 0.0}
                pr = taper(q) if taper else prof
                ring_, p2 = [], []
                for x, y in pr:
                    y += abs(x) * sl[x > 0]               # bent along the panes' chords (vertices at x = 0)
                    p2.append((x, y))
                    o = _add(_mul(a, x), _mul(n, y))
                    os_ = off(u, v, o)
                    ring_.append(([plus(ps[i], os_[i]) for i in range(3)], _add(pn, _mul(o, Gn))))
                rings.append(ring_)
                flat.append(p2)
            iron.sweep(rings, ring, flat[0], flat[-1])
        for prof in profs:
            run(prof, range(len(st)))
        if d["kind"] == "rib" and proud and d["member"]:
            # cap rib proud of the glass, in runs between skipped intervals (Built by Opus 5.5 (medium))
            n_ = len(st)
            ok = [cap_skip is None or not cap_skip(st[q], st[q + 1]) for q in range(n_ - 1)]
            if ring:
                ok.append(cap_skip is None or not cap_skip(st[-1], st[0] + 1.0))
            low = _cap_profile(sz["bar"][3] + sz["cap"][1] + 0.002, sz)
            full = _cap_profile(proud, sz)
            runs, cur = [], []
            for q in range(len(ok)):
                if ok[q]:
                    cur = cur or [q]
                    cur.append(q + 1)
                elif cur:
                    runs.append(cur)
                    cur = []
            if cur:
                runs.append(cur)
            for r in runs:
                if ring and len(r) == n_ + 1:
                    run(full, range(n_), bend=False)     # ring members: closed sweep over all stations
                    continue
                r = [q % n_ for q in r]
                first, last = r[0], r[-1]

                def taper(q, first=first, last=last):
                    if (q == first and cap_taper[0] and first == 0) or (q == last and cap_taper[1] and last == n_ - 1):
                        return low
                    return full
                ring_saved = ring
                ring = False
                try:
                    run(full, r, bend=False, taper=taper)
                finally:
                    ring = ring_saved
        if profs:
            counts[d["kind"]] += 1

    for d in us_l:
        if d["member"]:
            member(d, False)
    for d in vs_l:
        if d["member"]:
            member(d, True)

    def active(lines, at, lo, hi, wrap=False):
        """Kind of the line at `at` covering [lo, hi] (member or seat; a bar beats a lap), None if none."""
        best = None
        for d in lines:
            if abs(d["at"] - at) < EPS or (wrap and abs(d["at"] - at % 1.0) < EPS):
                if d["from"] <= lo + EPS and d["to"] >= hi - EPS:
                    best = d["kind"] if best in (None, "lap", "purlin") else best
                elif d["from"] < hi - EPS and d["to"] > lo + EPS and d["kind"] not in ("lap", "purlin"):
                    raise AssertionError(f"glazing: line at {at} covers part of a pane only")
        return best

    def clear(kind):
        if kind is None or kind in ("lap", "purlin"):
            return lap / 2 * Gn
        return _profiles(kind, sz)[1] * Gn + clearance

    pane_list = []
    for vj in (range(len(vpos) - 1) if panes else ()):
        va, vb = vpos[vj], vpos[vj + 1]
        i = 0
        while i < len(upos) - 1:
            if open and open(upos[i], upos[i + 1], va, vb):
                i += 1
                continue
            j = i + 1
            while (j < len(upos) - 1 and active(us_l, upos[j], va, vb, closed_u) is None
                   and not (open and open(upos[j], upos[j + 1], va, vb))):
                j += 1
            ua, ub = upos[i], upos[j]
            cl = (clear(active(us_l, ua, va, vb, closed_u)), clear(active(us_l, ub, va, vb, closed_u)),
                  clear(active(vs_l, va, ua, ub)), clear(active(vs_l, vb, ua, ub)))
            pane_list.append(_pane(F, frame, glass_sh, [s for s in ust if ua - EPS <= s <= ub + EPS],
                                   [s for s in vst if va - EPS <= s <= vb + EPS], cl, lift * Gn, glass * Gn))
            i = j
    frame_id = iron.emit(self, id_ + "-iron")
    glass_id = glass_sh.emit(self, id_ + "-panes")
    assert frame_id or glass_id, "glazing: nothing built"
    parts = []
    if frame_id:
        parts.append(self.mat(id_ + "-frame", frame_id, slot))
    if glass_id:
        g_id = _lead(self, id_, glass_id, pane_list, leaded) if leaded else glass_id
        parts.append(self.mat(id_ + "-glass", g_id, glass_slot))
    self.join(id_, *parts)
    self.__dict__.setdefault("glazing", {})[id_] = {
        "panes": len(pane_list), "ribs": counts["rib"], "bars": counts["bar"], "rings": counts["ring"],
        "purlins": counts["purlin"], "edges": counts["edge"], "frame": bool(frame_id), "glass": bool(glass_id),
        "triangles": {"frame": iron.triangles(), "glass": glass_sh.triangles()}}
    return id_


def _pane(F, frame, panes, us, vs, cl, lift, glass):
    """One pane slab over the cell us x vs, its edges pulled in by cl = (u0, u1, v0, v1) metres, measured square
    to each bounding line. Returns its top corners (symbolic, numeric) and mid normal, for leading."""
    nu, nv = len(us), len(vs)
    cu0, cu1, cv0, cv1 = cl
    bottom, top = [], []
    for i, u in enumerate(us):
        for j, v in enumerate(vs):
            pu, pv, n = frame(u, v)
            cons = []                                   # (unit normal into the cell, distance), square to the edge's chord
            if i in (0, nu - 1):
                ja, jb = max(0, j - 1), min(nv - 1, j + 1)
                a = _unit(_cross(n, _sub(F.num(u, vs[jb]), F.num(u, vs[ja]))))
                s = 1.0 if _dot(a, pu) > 0 else -1.0
                cons.append((_mul(a, s if i == 0 else -s), cu0 if i == 0 else cu1))
            if j in (0, nv - 1):
                ia, ib = max(0, i - 1), min(nu - 1, i + 1)
                a = _unit(_cross(n, _sub(F.num(us[ib], v), F.num(us[ia], v))))
                s = 1.0 if _dot(a, pv) > 0 else -1.0
                cons.append((_mul(a, s if j == 0 else -s), cv0 if j == 0 else cv1))
            if len(cons) == 1:
                o = _mul(cons[0][0], cons[0][1])
            elif len(cons) == 2:
                (n1, c1), (n2, c2) = cons
                d = _dot(n1, n2)
                beta = (c2 - c1 * d) / (1 - d * d)
                o = _add(_mul(n1, c1 - beta * d), _mul(n2, beta))
            else:
                o = (0.0, 0.0, 0.0)
            ps, pn = F.sym(u, v), F.num(u, v)
            for layer, h in ((bottom, lift), (top, lift + glass)):
                w = _add(o, _mul(n, h))
                ws = F.lift(u, v, w)
                layer.append(([X(ps[k]) + ws[k] if not (_num(_v(ws[k])) and _v(ws[k]) == 0) else X(ps[k])
                               for k in range(3)], _add(pn, w)))
    idx = lambda i, j: i * nv + j
    T = nu * nv
    faces = []
    for i in range(nu - 1):
        for j in range(nv - 1):
            q = [idx(i, j), idx(i + 1, j), idx(i + 1, j + 1), idx(i, j + 1)]
            faces.append([T + k for k in q])
            faces.append(list(reversed(q)))
    loop = ([idx(i, 0) for i in range(nu)] + [idx(nu - 1, j) for j in range(1, nv)] +
            [idx(i, nv - 1) for i in range(nu - 2, -1, -1)] + [idx(0, j) for j in range(nv - 2, 0, -1)])
    for a, b in zip(loop, loop[1:] + loop[:1]):
        faces.append([a, b, T + b, T + a])
    panes.shell(bottom + top, faces)
    corners = [top[idx(0, 0)], top[idx(nu - 1, 0)], top[idx(nu - 1, nv - 1)], top[idx(0, nv - 1)]]
    return corners, frame(us[nu // 2], vs[nv // 2])[2]


def _round_border(p, q):
    """Corners of the unit square passed going from border point p to border point q the short way round."""
    def t(a):
        x, y = a
        if y <= EPS:
            return x
        if x >= 1 - EPS:
            return 1 + y
        if y >= 1 - EPS:
            return 3 - x
        return 4 - y
    corners = [(0.0, (0.0, 0.0)), (1.0, (1.0, 0.0)), (2.0, (1.0, 1.0)), (3.0, (0.0, 1.0))]
    tp, tq = t(p), t(q)
    fwd = (tq - tp) % 4
    out = []
    if fwd <= 2:
        for c, xy in sorted(corners, key=lambda c: (c[0] - tp) % 4):
            if 0 < (c - tp) % 4 < fwd:
                out.append(xy)
    else:
        back = 4 - fwd
        for c, xy in sorted(corners, key=lambda c: (tp - c[0]) % 4):
            if 0 < (tp - c) % 4 < back:
                out.append(xy)
    return out


def _lead(g, id_, glass_id, pane_list, leaded):
    """Cames as detail strokes: per pane one polyline over its top face, the border then each quarry line."""
    pattern = leaded.get("pattern", "diamond")
    qw, qh = leaded.get("quarry", (0.12, 0.18))
    assert pattern in ("diamond", "rect"), "leaded pattern: diamond or rect"
    guides = []
    for k, (c, n) in enumerate(pane_list):
        (s00, c00), (s10, c10), (s11, c11), (s01, c01) = c
        W, H = math.dist(c00, c10), math.dist(c00, c01)
        if W < qw * 0.8 or H < qh * 0.8:
            continue

        def at(a, b):                                   # bilinear on the pane's top corners (follows the controls)
            w = ((1 - a) * (1 - b), a * (1 - b), a * b, (1 - a) * b)
            return [_out(s00[k] * w[0] + s10[k] * w[1] + s11[k] * w[2] + s01[k] * w[3] + n[k] * 0.0005) for k in range(3)]
        segs = []
        if pattern == "rect":
            nx, ny = max(1, round(W / qw)), max(1, round(H / qh))
            segs += [((i / nx, 0.0), (i / nx, 1.0)) for i in range(1, nx)]
            segs += [((0.0, j / ny), (1.0, j / ny)) for j in range(1, ny)]
        else:                                           # a A +- b B = c, A = W / qw, B = H / qh, in the unit square
            A, B = W / qw, H / qh
            for sg in (1, -1):
                lo, hi = (0.0, A + B) if sg > 0 else (-B, A)
                for c_ in range(int(math.floor(lo)) + 1, int(math.ceil(hi))):
                    pts = []
                    for a in (0.0, 1.0):
                        b = (c_ - a * A) / (sg * B)
                        if -EPS <= b <= 1 + EPS:
                            pts.append((a, min(1.0, max(0.0, b))))
                    for b in (0.0, 1.0):
                        a = (c_ - sg * b * B) / A
                        if -EPS <= a <= 1 + EPS:
                            pts.append((min(1.0, max(0.0, a)), b))
                    pts = sorted(set((round(a, 9), round(b, 9)) for a, b in pts))
                    if len(pts) >= 2 and math.dist(pts[0], pts[-1]) > 1e-6:
                        segs.append((pts[0], pts[-1]))
        path = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
        for a, b in segs:                               # to each quarry line along the border, then across
            path += _round_border(path[-1], a) + [a, b]
        poly = [at(*p) for p in path]
        guides.append(g.n(f"{id_}-came-{k}", "spline", points=poly, interpolation="polyline"))
    if not guides:
        return glass_id
    gid = guides[0] if len(guides) == 1 else g.join(f"{id_}-cames", *guides)
    return g.stroke(f"{id_}-leaded", glass_id, gid, profile="bead", width=leaded.get("came", 0.006), height=0.0015,
                    slot=leaded.get("slot", "black"))


# --- arch sections as expressions of span and rise (Built by Opus 5.5 (medium)) ---------------------------

# c(k) for a catenary of half span 1 and rise k, (cosh c - 1) / c = k: a rational fit, within 0.5 % for k in
# 0.12 .. 4.2 (span and rise stay exact outside; the curve is then a near-catenary)
_CAT_A, _CAT_B = (1.954999736961587, 3.9288459892107404, 0.12350825938551525), (1.7625538029151715, 0.957306610269786)


def _cosh(x):
    return (_xpow(math.e, x) + _xpow(math.e, X(0) - x)) * 0.5


def _sinh(x):
    return (_xpow(math.e, x) - _xpow(math.e, X(0) - x)) * 0.5


def _unit2(a, b):
    """Unit (a, b); with b = 0, a is taken to be positive."""
    a, b = _v(a), _v(b)
    if _num(b) and b == 0 and not _num(a):
        return 1.0, 0.0
    if _num(a) and _num(b):
        n = math.hypot(a, b)
        return a / n, b / n
    n = _xsqrt(X(a) * a + X(b) * b)
    return X(a) / n, X(b) / n


def _section(section, half, rise, k_const=None):
    """f(s) -> (x, z, tx, tz) for s in [0, 1], springing to springing: the arch point (x from the centre line,
    z above the springing) and its unit tangent, as X in the span and rise, so the arch is solved afresh at
    every control value (a segmental arc keeps its springings and crown, a pointed arch re-centres its two
    arcs, a catenary re-solves its parameter). k_const: rise / half span when the controls cannot change it
    (the shape then folds to numbers times the dimensions)."""
    h, R = X(half), X(rise)
    k = k_const if k_const is not None else R / h
    if section == "semicircular":                       # semicircle, or a semi-ellipse when rise != half span
        def f(s):
            c, sn = math.cos(PI * s), math.sin(PI * s)
            return h * -c, R * sn, *_unit2(sn, X(k) * c if not _num(k) else k * c)
        return f
    if section == "segmental":
        al = _xatan2(k, 1.0) * 2.0                      # half the arc's angle
        rho = X(_xsin(al))

        def f(s):
            th = al * (2 * s - 1)
            st, ct = _xsin(th), _xcos(th)
            return h * (st / rho), h * (k - (1 - ct) / rho), ct, X(0) - st
        return f
    if section == "pointed":
        r = (X(k) * k + 1) * 0.5 if not _num(k) else (k * k + 1) * 0.5   # each arc's radius, in half spans
        b1 = _xatan2(k, r - 1)                          # angle of the crown seen from the arc's centre

        def f(s):
            if abs(s - 0.5) < 1e-12:
                return 0.0, h * k, 1.0, 0.0
            b = b1 * (2 - 2 * s) if s > 0.5 else b1 * (2 * s)
            sb, cb = _xsin(b), _xcos(b)
            x = h * ((1 - r) + r * cb if _num(r) else (X(1) - r) + X(r) * cb)
            z = h * (X(r) * sb if not _num(r) else r * sb)
            return (x, z, sb, X(0) - cb) if s > 0.5 else (X(0) - x, z, sb, cb)
        return f
    if section == "catenary":
        kx = X(k)
        c = kx * (_lin([(_CAT_A[0], 1.0)]) + kx * _CAT_A[1] + kx * kx * _CAT_A[2]) / (kx * _CAT_B[0] + kx * kx * _CAT_B[1] + 1.0)
        if _num(k):                                     # exact when the ratio is fixed
            lo, hi = 1e-6, 60.0
            for _ in range(200):
                mid = 0.5 * (lo + hi)
                lo, hi = (mid, hi) if (math.cosh(mid) - 1) / mid < k else (lo, mid)
            c = 0.5 * (lo + hi)
        ch = _cosh(c)

        def f(s):
            w = 2 * s - 1
            z = h * (kx * (ch - _cosh(X(c) * w)) / (ch - 1)) if not _num(c) else h * (k * (math.cosh(c) - math.cosh(c * w)) / (math.cosh(c) - 1))
            slope = (X(0) - kx * c * _sinh(X(c) * w) / (ch - 1)) if not _num(c) else -k * c * math.sinh(c * w) / (math.cosh(c) - 1)
            return h * w, z, *_unit2(1.0, slope)
        return f
    raise AssertionError("vault section: semicircular, segmental, pointed or catenary")


def _reparam(shape, k, s0=0.0, s1=1.0, n=600):
    """(s(t), length): the shape's parameter at arc-length fraction t of [s0, s1], shape in (x, k z)."""
    ss = [s0 + (s1 - s0) * i / n for i in range(n + 1)]
    pts = [shape(s) for s in ss]
    L = [0.0]
    for a, b in zip(pts, pts[1:]):
        L.append(L[-1] + math.hypot(b[0] - a[0], k * (b[1] - a[1])))
    tot = L[-1]

    def S(t):
        target = min(max(t, 0.0), 1.0) * tot
        lo, hi = 0, n
        while hi - lo > 1:
            m = (lo + hi) // 2
            if L[m] <= target:
                lo = m
            else:
                hi = m
        f = 0.0 if L[hi] == L[lo] else (target - L[lo]) / (L[hi] - L[lo])
        return ss[lo] + (ss[hi] - ss[lo]) * f
    return S, tot


def _invert(fn, target, lo=0.0, hi=1.0):
    """t in [lo, hi] with fn(t) = target, fn monotone there."""
    a, b = lo, hi
    up = fn(b) > fn(a)
    for _ in range(80):
        m = (a + b) / 2
        if (fn(m) < target) == up:
            a = m
        else:
            b = m
    return (a + b) / 2


def _even(n):
    return [k / n for k in range(n + 1)]


# --- glazed vault (Built by Opus 5.5 (medium): controls-following rewrite) -----------------------------------

def glazed_vault(self, id_, span, length, rise=None, section="semicircular", bays=None, bay=4.0, bar_pitch=0.6,
                 purlins=None, purlin_pitch=1.6, pane_length=0.9, ends=1.0, end_screens=(False, False),
                 screen_pitch=0.9, screen_transoms=2, lantern=None, springing=None, spring=0.0, proud=0.12,
                 wall_plate=None, gauge=1.0, controls=None, slot="iron", roof_slot="slate", glass_slot="glass",
                 leaded=None):
    """A glazed barrel vault over a rectangle: main arched I ribs at a bay spacing, purlins along the length,
    T glazing bars on arch lines between the ribs, panes lapped every pane_length up the slope.

    Frame: x across the span (-span/2 .. span/2), y along the length (0 .. length), z up; the springing line
    (eaves kerbs) at z = springing (alias spring), the glass on the arch surface. section: "semicircular"
    (rise = span/2; another rise gives a semi-ellipse), "segmental" (rise < span/2), "pointed" (two-centred,
    rise > span/2) or "catenary" (any rise).
    Following the controls: span, length, rise, springing, bay and gauge may be X. The arch is an expression of
    span and rise (points, and the section frames of every member and pane, solved at each value: a segmental
    arc keeps its springings and crown, a pointed arch re-centres, a catenary re-solves), never a stretched
    default. The bay count is an expression, max(1, round(length / bay)) (or the int `bays`), and the bays are
    `line`/`instance` copies of one rib, one bay of bars and one strip of panes, so ribs keep their pitch as the
    length changes. Within a bay the bar count (from bar_pitch; ends: factor for the first and last bay) is
    fixed at the defaults and the bars share the bay evenly; up the arch the purlin count (purlins, or from
    purlin_pitch) and the pane laps (pane_length) are fixed at the defaults and stretch with the arch.
    proud: height in metres (times gauge) of a cap rib on each main rib above the glass line, an outer web and
    flange standing proud of the glass as on station sheds (default 0.12; 0 or None: ribs under the glass);
    purlins and bars stay under or flush. wall_plate: True or dict(width=0.3, depth=0.2, overhang=0.0,
    slot=slot): a plate along each eaves under the kerb, its top on the springing line and centred on x =
    +-span/2, for joining to stonework: the wall top goes at g.glazing[id]["seat"]["z"] (springing - depth).
    end_screens (south y=0, north y=length): a vertical lunette screen under each end rib, mullions every
    screen_pitch (every third an I post; count at the defaults), screen_transoms transoms following the arch,
    the head fixed to the rib's soffit; leaded applies to the screens and the lantern. lantern: dict(width,
    height, roof="gable"|"barrel", roof_rise, overhang) opens the crown between two curbs and sets a
    ridge_lantern on them (its posts stand on the default ribs' places and its width is taken at the defaults
    as a share of the arch).
    Example: g.glazed_vault("shed", C("width"), C("length"), section="segmental", rise=C("width") * 0.25 + 2,
                            springing=C("level"), wall_plate=True, end_screens=(True, False), controls=CONTROLS)"""
    env = _env(controls)
    half, L = X(span) * 0.5, X(length)
    R = X(rise) if rise is not None else half
    Z0 = X(springing if springing is not None else spring)
    h0, L0, R0, G0 = (_ev(x, env) for x in (half, L, R, gauge))
    k0 = R0 / h0
    assert section != "segmental" or k0 < 1, "segmental arch: rise below half the span"
    assert section != "pointed" or k0 > 1, "pointed arch: rise above half the span"
    kc = 1.0 if rise is None else _const(R / half, controls)
    sec = _section(section, half, R, kc)
    memo = {}

    def at(s):                                          # the section at s, symbolic, memoised
        if s not in memo:
            memo[s] = sec(s)
        return memo[s]

    def num(s):
        return tuple(_ev(c, env) for c in at(s))
    S, arc = _reparam(lambda s: num(s)[:2], 1.0)

    def P(u, y):
        x, z, _, _ = at(S(u))
        return [x, y, Z0 + z]

    def Fr(u, v):
        _, _, tx, tz = at(S(u))
        return [tx, 0.0, tz], [0.0, 1.0, 0.0], [X(0) - tz, 0.0, tx]

    # bays: a count expression; bars per bay and the end bays' density at the defaults
    nb = _v(int(bays) if bays is not None else _xmax(1, _xround(L / bay)))
    nb = int(round(nb)) if _num(nb) else X(nb)
    nb0 = int(round(_ev(nb, env)))
    m = max(1, round(L0 / nb0 / bar_pitch))
    me = max(1, round(L0 / nb0 / bar_pitch * ends))
    if nb0 == 1:
        m = me
    Bp = L / nb

    np_ = purlins if purlins is not None else max(1, round(arc / purlin_pitch) - 1)
    ul = [(0.0, "edge"), (1.0, "edge")]
    cuts = _even(np_ + 1)
    for i, (a, b) in enumerate(zip(cuts, cuts[1:])):
        if i > 0:
            ul.append((a, "purlin"))
        mm = max(1, math.ceil((b - a) * arc / pane_length - 1e-9))
        ul += [(a + (b - a) * j / mm, "lap") for j in range(1, mm)]
    opening = None
    if lantern:
        w0 = _ev(lantern.get("width", 0.4 * h0), env)
        ua = _invert(lambda t: num(S(t))[0], -w0 / 2)
        ub = _invert(lambda t: num(S(t))[0], w0 / 2)
        ul = [d for d in ul if not (ua - 1e-6 <= d[0] <= ub + 1e-6)] + [(ua, "edge"), (ub, "edge")]
        opening = (ua, ub)
    is_open = (lambda u0, u1, v0, v1: opening[0] - 1e-9 <= u0 and u1 <= opening[1] + 1e-9) if opening else None
    skip = (lambda a, b: opening[0] - 1e-9 <= a and b <= opening[1] + 1e-9) if opening else None
    clear_ul = [(a, kind if kind == "lap" else "~" + kind) for a, kind in ul]
    common = dict(gauge=gauge, controls=controls, slot=slot, glass_slot=glass_slot, frame=Fr)

    # the pieces: one rib, one bar, pane strips (one bar pitch wide), the runs along the length
    rib = self.glazed_surface(id_ + "-rib", P, clear_ul, [(-1.0, "~bar"), (0.0, "rib"), (1.0, "~bar")], panes=False,
                              proud=proud, cap_skip=skip, **common)
    bl = [(0.0, "bar")] if not opening else [(0.0, "bar", 0.0, opening[0]), (0.0, "bar", opening[1], 1.0)]
    bar = self.glazed_surface(id_ + "-bar", P, clear_ul, [(-1.0, "~bar")] + bl + [(1.0, "~bar")], panes=False, **common)
    runs = self.glazed_surface(id_ + "-runs", lambda u, v: P(u, _lin([(v, L)])), ul, [(0.0, "~rib"), (1.0, "~rib")],
                               panes=False, **common)
    strips = {}

    def strip(n):
        if n not in strips:
            p = Bp / n
            sid = f"{id_}-strip{n}"
            self.glazed_surface(sid, lambda u, v: P(u, _lin([(v, p)])), clear_ul, [(0.0, "~bar"), (1.0, "~bar")],
                                open=is_open, **common)
            strips[n] = sid
        return strips[n] + "-glass"

    def when(name, cond, node):
        c = _v(cond)
        if node is None or (_num(c) and not c):
            return None
        return node if _num(c) else self.when(name, cond, node)

    def arr(name, src, y0, step, count):
        return self.array(name, src, [0, y0, 0], [0, step, 0], count)

    def shift(name, src, dy):
        return self.xf(name, src, t=[0, dy, 0]) if src else None

    frame = [arr(id_ + "-ribs", rib + "-frame", 0, Bp, nb + 1), runs + "-frame"]
    glass = []
    many = _xgt(nb, 1.5)
    middle = _xgt(nb, 2.5)
    if me == m:
        if m > 1:
            frame.append(arr(id_ + "-bars", arr(id_ + "-bay-bars", bar + "-frame", Bp / m, Bp / m, m - 1), 0, Bp, nb))
        glass.append(arr(id_ + "-panes", strip(m), 0, Bp / m, nb * m))
    else:
        eb = arr(id_ + "-end-bars", bar + "-frame", Bp / me, Bp / me, me - 1) if me > 1 else None
        es = arr(id_ + "-end-panes", strip(me), 0, Bp / me, me)
        frame += [eb, when(id_ + "-last-bars", many, shift(id_ + "-last-bars-at", eb, L - Bp))]
        glass += [es, when(id_ + "-last-panes", many, shift(id_ + "-last-panes-at", es, L - Bp))]
        mid_n = _xmax(1, X(nb) - 2) if not _num(_v(nb)) else max(1, nb - 2)
        if m > 1:
            mb = arr(id_ + "-mid-bay-bars", bar + "-frame", Bp / m, Bp / m, m - 1)
            frame.append(when(id_ + "-mid-bars", middle, arr(id_ + "-mid-bars-at", mb, Bp, Bp, mid_n)))
        glass.append(when(id_ + "-mid-panes", middle, arr(id_ + "-mid-panes-at", strip(m), Bp, Bp / m, X(mid_n) * m)))
    frame, glass = [f for f in frame if f], [g_ for g_ in glass if g_]
    pane_cells = self.glazing[strips[m]]["panes"]
    bars0 = nb0 * (m - 1) if nb0 == 1 or me == m else 2 * (me - 1) + max(0, nb0 - 2) * (m - 1)
    strips0 = nb0 * m if nb0 == 1 or me == m else 2 * me + max(0, nb0 - 2) * m
    tri = lambda i: self.glazing[i]["triangles"]
    info = {"bays": nb0, "bays_expr": _out(nb), "ribs": nb0 + 1, "bars": bars0, "purlins": self.glazing[runs]["purlins"],
            "panes": pane_cells * strips0, "strip_panes": pane_cells, "bars_per_bay": (me, m), "screens": 0, "screen_panes": 0, "springing": _out(Z0),
            "triangles": {"frame": tri(rib)["frame"] * (nb0 + 1) + tri(bar)["frame"] * bars0 + tri(runs)["frame"],
                          "glass": tri(strips[m])["glass"] * strips0}}

    # wall plates: the seat a builder joins to the walls (top on the springing line)
    if wall_plate:
        wp = wall_plate if isinstance(wall_plate, dict) else {}
        G = X(gauge)
        w, dp, ov = X(wp.get("width", 0.3)) * G, X(wp.get("depth", 0.2)) * G, X(wp.get("overhang", 0.0))
        plates = []
        for side, sx in (("west", -1.0), ("east", 1.0)):
            xc = half * sx
            prof = [(xc - w * 0.5, Z0 - dp), (xc + w * 0.5, Z0 - dp), (xc + w * 0.5, Z0), (xc - w * 0.5, Z0)]
            plates.append(_prism(self, f"{id_}-plate-{side}", prof, X(0) - ov, L + ov, wp.get("slot", slot), env))
        frame.append(self.join(id_ + "-plates", *plates))
        info["seat"] = {"z": _out(Z0 - dp), "x": [_out(X(0) - half), _out(half)], "width": _out(w),
                        "y": [_out(X(0) - ov), _out(L + ov)]}

    # end screens under the end ribs: vertical mullions at constant arch parameter, head on the rib soffit
    tf = SIZES["bar"][1]
    d_in = (tf + SIZES["rib"][1] - 0.006) * G0            # 6 mm up into the rib's bottom flange

    def soffit(s):                                       # (x, height above the springing) at the defaults
        x, z, tx, tz = num(S(s))
        return x + tz * d_in, z - tx * d_in
    for end, want in enumerate(end_screens):
        if not want:
            continue
        y = _lin([(end, L)])
        hmin = max(0.6 * G0, 0.1 * R0)                # the outer column starts where it is this tall
        sa = _invert(lambda s: soffit(s)[1], hmin, 0.0, 0.5)
        sb = _invert(lambda s: soffit(s)[1], hmin, 0.5, 1.0)
        width = soffit(sb)[0] - soffit(sa)[0]
        nm = max(2, round(width / screen_pitch))
        mull = [sa] + [_invert(lambda s: soffit(s)[0], soffit(sa)[0] + width * i / nm, sa, sb) for i in range(1, nm)] + [sb]
        ulines = [(mull[0], "edge"), (mull[-1], "edge")] + [(mm, "rib" if i % 3 == 0 else "bar") for i, mm in enumerate(mull[1:-1], 1)]
        vlines = [(0.0, "edge"), (1.0, "edge")] + [(t / (screen_transoms + 1), "bar") for t in range(1, screen_transoms + 1)]
        DG = X(gauge) * (d_in / G0)

        def Q(u, v, y=y):
            x, z, tx, tz = at(S(u))
            return [x + DG * tz, y, Z0 + _lin([(v, z - DG * tx)])]

        def Qf(u, v):                                   # transoms follow the arch scaled by v; mullions plumb
            _, _, tx, tz = at(S(u))
            ex, ez = _unit2(tx, _lin([(v, tz)]))
            return [ex, 0.0, ez], [0.0, 0.0, 1.0], [0.0, 1.0, 0.0]
        sid = f"{id_}-screen-{'south' if end == 0 else 'north'}"
        self.glazed_surface(sid, Q, ulines, vlines, divisions=(4, 1), flip=end == 1, gauge=gauge, controls=controls, slot=slot,
                            glass_slot=glass_slot, leaded=leaded, frame=Qf)
        frame.append(sid + "-frame")
        glass.append(sid + "-glass")
        info["screens"] += 1
        info["screen_panes"] += self.glazing[sid]["panes"]
    if lantern:
        ua, ub = opening
        lw = at(S(ub))[0] - at(S(ua))[0]
        z_k = Z0 + at(S(ua))[1] + 0.02 * X(gauge)
        lid = id_ + "-lantern"
        self.ridge_lantern(lid, L, lw, lantern.get("height", 0.2 * h0), at=[0, 0, z_k],
                           posts=[b / nb0 for b in range(nb0 + 1)], roof=lantern.get("roof", "gable"),
                           roof_rise=lantern.get("roof_rise"), overhang=lantern.get("overhang", 0.2),
                           gauge=gauge, controls=controls, slot=slot, roof_slot=roof_slot, glass_slot=glass_slot,
                           leaded=leaded)
        frame.append(lid + "-frame")
        glass.append(lid + "-glass")
        info["lantern_panes"] = self.glazing[lid]["panes"]
    self.join(id_ + "-frame", *frame)
    self.join(id_ + "-glass", *glass)
    self.join(id_, id_ + "-frame", id_ + "-glass")
    self.glazing[id_] = info
    return id_


# --- ridge lantern -----------------------------------------------------------------------------------------

def _prism(g, id_, profile, y0, y1, slot, env):
    """A straight prism along y from y0 to y1 over an (x, z) profile polygon (numbers or X)."""
    sh = _Shells()
    rings = [[([X(x), X(y), X(z)], (_ev(x, env), _ev(y, env), _ev(z, env))) for x, z in profile] for y in (y0, y1)]
    sh.sweep(rings, False, [(p[1][0], p[1][2]) for p in rings[0]])
    return g.mat(id_, sh.emit(g, id_ + "-shell"), slot)


def ridge_lantern(self, id_, length, width, height, at=(0, 0, 0), posts=None, post_pitch=3.0, bar_pitch=0.5,
                  roof="gable", roof_rise=None, overhang=0.2, gauge=1.0, controls=None, slot="iron",
                  roof_slot="slate", glass_slot="glass", leaded=None):
    """A ridge (monitor) lantern: vertical glazed side and end walls standing on a vault's crown curbs, under its
    own small roof.

    Frame: `at` is the centre of the lantern's foot at its y = 0 end; side walls at x = +-width/2 from at.z to
    at.z + height, y from 0 to length; the roof (gable or shallow barrel, `roof_rise` default width/4, slot
    roof_slot) overhangs by `overhang` all round, its soffit resting on the wall heads. posts: y fractions of I
    posts (default every post_pitch), T mullions between at bar_pitch. Dimensions may be X; counts at defaults.
    Example: g.ridge_lantern("monitor", 24.0, 2.4, 1.1, at=[0, 0, 7.2], posts=[0, 0.25, 0.5, 0.75, 1])"""
    env = _env(controls)
    L, W, H = X(length), X(width), X(height)
    ax, ay, az = (X(c) for c in at)
    L0, W0 = _ev(L, env), _ev(W, env)
    G = X(gauge)
    if posts is None:
        posts = _even(max(1, round(L0 / post_pitch)))
    ul = [(p, "edge" if p in (posts[0], posts[-1]) else "rib") for p in posts]
    for a, b in zip(posts, posts[1:]):
        n = max(1, round((b - a) * L0 / bar_pitch))
        ul += [(a + (b - a) * i / n, "bar") for i in range(1, n)]
    vl = [(0.0, "edge"), (1.0, "edge")]
    fr, gl, panes = [], [], 0
    for side, sx in (("east", 0.5), ("west", -0.5)):
        sid = f"{id_}-{side}"
        self.glazed_surface(sid, lambda u, v, sx=sx: [ax + W * sx, ay + L * u, az + H * v], ul, vl, flip=sx < 0,
                            gauge=gauge, controls=controls, slot=slot, glass_slot=glass_slot, leaded=leaded)
        fr.append(sid + "-frame")
        gl.append(sid + "-glass")
        panes += self.glazing[sid]["panes"]
    ne = max(1, round(W0 / bar_pitch))
    e0 = (SIZES["edge"][1] - SIZES["bar"][3]) * _ev(G, env) / W0   # the side walls' corner posts reach this far in
    el = [(e0, "~edge"), (1.0 - e0, "~edge")] + [(e0 + (1 - 2 * e0) * i / ne, "bar") for i in range(1, ne)]
    for end, yy in (("south", 0.0), ("north", 1.0)):
        sid = f"{id_}-{end}"
        self.glazed_surface(sid, lambda u, v, yy=yy: [ax - W * 0.5 + W * u, ay + L * yy, az + H * v], el, vl,
                            flip=yy > 0, gauge=gauge, controls=controls, slot=slot, glass_slot=glass_slot, leaded=leaded)
        fr.append(sid + "-frame")
        gl.append(sid + "-glass")
        panes += self.glazing[sid]["panes"]
    rr = X(roof_rise) if roof_rise is not None else W * 0.25
    t, ov = G * 0.035, X(overhang)
    xe = W * 0.5 + ov
    head = az + H + G * 0.02                              # roof soffit over the wall heads
    if roof == "gable":
        ze = head + t - rr * (ov / xe)
        prof = [(-xe, ze - t), (X(0), ze + rr - t), (xe, ze - t), (xe, ze), (X(0), ze + rr), (-xe, ze)]
    elif roof == "barrel":
        n = 10
        f = lambda s: 1 - s * s                           # parabolic barrel, rise rr at the ridge
        ze = head + t - rr * f(_ev(W * 0.5 / xe, env))
        prof = [(xe * (2 * i / n - 1), ze - t + rr * f(2 * i / n - 1)) for i in range(n + 1)]
        prof += [(xe * (1 - 2 * i / n), ze + rr * f(1 - 2 * i / n)) for i in range(n + 1)]
    else:
        raise AssertionError("ridge_lantern roof: gable or barrel")
    fr.append(_prism(self, id_ + "-cover", [(ax + x, z) for x, z in prof], ay - ov, ay + L + ov, roof_slot, env))
    self.join(id_ + "-frame", *fr)
    self.join(id_ + "-glass", *gl)
    self.join(id_, id_ + "-frame", id_ + "-glass")
    self.__dict__.setdefault("glazing", {})[id_] = {"posts": len(posts), "panes": panes}
    return id_


# --- domes -------------------------------------------------------------------------------------------------

def _dome_shape(profile, k):
    """(fr, fz) on s in [0, 1]: fr 1 at the base, 0 at the pole; fz 0 at the base, 1 at the pole (radius 1, rise k)."""
    if profile == "hemisphere":
        return lambda s: (math.cos(PI * s / 2), math.sin(PI * s / 2))
    if profile == "segmental":
        assert k < 1, "segmental dome: rise below the radius"
        rho = (1 + k * k) / (2 * k)
        p0 = math.asin(1 / rho)
        return lambda s: (rho * math.sin(p0 * (1 - s)), (rho * math.cos(p0 * (1 - s)) - (rho - k)) / k)
    if profile == "onion":
        # bulb then a concave neck to the point (radius 1 at the base, rise 1): Catmull-Rom through these
        pts = [(1.0, 0.0), (1.1, 0.1), (1.16, 0.24), (1.08, 0.4), (0.82, 0.55), (0.48, 0.68), (0.24, 0.8), (0.1, 0.9),
               (0.0, 1.0)]
        n = len(pts) - 1

        def f(s):
            x = min(max(s, 0.0), 1.0) * n
            i = min(int(x), n - 1)
            t = x - i
            p0, p1, p2, p3 = pts[max(i - 1, 0)], pts[i], pts[i + 1], pts[min(i + 2, n)]
            return tuple(0.5 * (2 * p1[k] + (p2[k] - p0[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t * t
                                + (3 * p1[k] - p0[k] - 3 * p2[k] + p3[k]) * t ** 3) for k in range(2))
        return f
    raise AssertionError("dome profile: hemisphere, segmental or onion")


def _dome(self, id_, radius, rise, profile, ulines, warp, rings, ring_pitch, pane_length, crown, lantern, oculus,
          at, gauge, controls, slot, glass_slot, leaded, extra_frame=None, divisions=(2, 1), proud=None):
    env = _env(controls)
    R = X(radius)
    R0 = _ev(R, env)
    H = X(rise) if rise is not None else (R * 1.6 if profile == "onion" else R)
    k = _ev(H, env) / R0
    shape = _dome_shape(profile, k)
    rc0 = _ev(crown, env) if crown is not None else (0.15 if oculus else 0.12) * R0
    sc = _invert(lambda s: shape(s)[0], rc0 / R0, 0.3, 1.0)
    S, mer = _reparam(shape, k, 0.0, sc)
    mer *= R0
    ax, ay, az = (X(c) for c in at)

    def P(u, v):
        fr, fz = shape(S(v))
        th = 2 * PI * (warp(u, v) if warp else u)
        return [ax + R * (fr * math.cos(th)), ay + R * (fr * math.sin(th)), az + H * fz]

    nr = rings if rings is not None else max(1, round(mer / ring_pitch) - 1)
    cuts = _even(nr + 1)
    vl = [(0.0, "edge"), (1.0, "edge")] + [(c, "ring") for c in cuts[1:-1]]
    for a, b in zip(cuts, cuts[1:]):
        m = max(1, math.ceil((b - a) * mer / pane_length - 1e-9))
        vl += [(a + (b - a) * j / m, "lap") for j in range(1, m)]
    ul = ulines(cuts, lambda v: 2 * PI * R0 * shape(S(v))[0])
    sid = id_ + "-shell"
    self.glazed_surface(sid, P, ul, vl, divisions=divisions, closed_u=True, gauge=gauge, controls=controls, slot=slot,
                        glass_slot=glass_slot, proud=proud, cap_taper=(False, bool(lantern and not oculus)))
    fr_, gl_ = [sid + "-frame"], [sid + "-glass"]
    info = dict(self.glazing[sid])
    info["rings"] = nr
    info["triangles"] = dict(info["triangles"])
    if extra_frame:
        fr_.append(extra_frame(P))
    frc, fzc = shape(S(1.0))
    if lantern and not oculus:
        lw = lantern if isinstance(lantern, dict) else {}
        lid = id_ + "-lantern"
        self.dome_lantern(lid, R * frc, lw.get("height", rc0 * 1.1), at=[ax, ay, az + H * fzc + 0.02 * X(gauge)],
                          sides=lw.get("sides", 8), cap_rise=lw.get("cap_rise"), gauge=gauge, controls=controls,
                          slot=slot, cap_slot=lw.get("cap_slot", "patina"), glass_slot=glass_slot, leaded=leaded)
        fr_.append(lid + "-frame")
        gl_.append(lid + "-glass")
        info["lantern_panes"] = self.glazing[lid]["panes"]
    self.join(id_ + "-frame", *fr_)
    self.join(id_ + "-glass", *gl_)
    self.join(id_, id_ + "-frame", id_ + "-glass")
    self.glazing[id_] = info
    return id_


def glazed_dome(self, id_, radius, rise=None, profile="hemisphere", ribs=8, bars=3, rings=None, ring_pitch=1.4,
                pane_length=0.8, crown=None, lantern=True, oculus=False, taper=True, min_pane=0.3, at=(0, 0, 0),
                proud=0.12, gauge=1.0, controls=None, slot="iron", glass_slot="glass", leaded=None):
    """A glazed dome on a ring: meridian I ribs, horizontal I rings, T glazing bars between the ribs, panes lapped
    every pane_length, a crown ring with a lantern (or an open oculus).

    Frame: the base ring (radius `radius`) is centred on `at` in its horizontal plane, z up to the crown ring
    (radius `crown`, default 0.12 radius, 0.15 with an oculus). profile: "hemisphere" (rise = radius),
    "segmental" (rise < radius) or "onion" (bulging, pointed; rise default 1.6 radius). ribs: main meridian
    ribs; bars: glazing bars per bay; taper (bars + 1 a power of two): bars drop out, alternate ones first, at
    the ring above which panes would be narrower than min_pane, as built domes do. rings: count between base
    and crown (default from ring_pitch). lantern: True or dict(height, sides, cap_rise, cap_slot), a
    dome_lantern on the crown ring. proud: height of a cap rib on each meridian rib above the glass line (default
    0.12 m times gauge; brought down to the bar web over the last bay under a lantern; 0 or None: none); rings
    and bars stay under the glass. radius, rise, crown, at and gauge may be X; counts and the profile's shape are
    taken at the defaults (a rise that is a fixed share of the radius keeps the shape exact).
    Example: g.glazed_dome("dome", 6.0, profile="segmental", rise=3.5, ribs=12, bars=1, oculus=True)"""
    nb = bars + 1
    m = int(round(math.log2(nb))) if nb > 1 else 0
    tapering = taper and nb > 1 and 2 ** m == nb

    def ulines(cuts, girth):
        out = []
        for i in range(ribs * nb):
            u = i / (ribs * nb)
            if i % nb == 0:
                out.append((u, "rib"))
            elif not tapering:
                out.append((u, "bar"))
            else:
                p = i % nb
                lvl = (p & -p).bit_length() - 1              # trailing zeros: odd bars drop first
                top = 0.0
                for a, b in zip(cuts, cuts[1:]):
                    stage = 0
                    while stage < m and girth(b) / ribs / 2 ** (m - stage) < min_pane:
                        stage += 1
                    if lvl < stage:
                        break
                    top = b
                if top > 0:
                    out.append((u, "bar", 0.0, top))
        return out
    return _dome(self, id_, radius, rise, profile, ulines, None, rings, ring_pitch, pane_length, crown, lantern, oculus,
                 at, gauge, controls, slot, glass_slot, leaded, proud=proud)


def fan_dome(self, id_, radius, rise=None, profile="hemisphere", fans=8, fan_ribs=5, gap_bars=1, spread=0.6,
             waist=0.15, rings=None, ring_pitch=1.4, pane_length=0.8, crown=None, lantern=True, oculus=False,
             at=(0, 0, 0), proud=0.12, gauge=1.0, controls=None, slot="iron", glass_slot="glass", leaded=None):
    """An art-deco fan-ribbed (sunburst) dome: from `fans` springer points on the base ring, fan_ribs I ribs
    splay upward in a fan (the outer two deep I ribs, the inner ones lighter); between neighbouring fans a
    straight meridian rib with gap_bars T bars each side; rings cross them all; a crown ring with a lantern or
    an oculus.

    Frame as glazed_dome (base ring of `radius` centred on `at`, z up). spread: share of each sector a fan
    covers at the crown (0..1); waist: the fan's width at its springer as a share of that (the ribs gather on
    an iron springer block there). The fan ribs are lines of constant parameter on a warped grid, so every cell
    stays four-sided and holds a pane. proud: cap ribs on the deep (outer fan and meridian) ribs, as glazed_dome.
    Dimensions may be X (counts and the profile's shape at the defaults).
    Example: g.fan_dome("sunburst", 8.0, profile="segmental", rise=4.0, fans=6, fan_ribs=7, oculus=True)"""
    assert fan_ribs >= 2 and 0 < spread < 1 and 0 < waist <= 1, "fan_dome: fan_ribs >= 2, 0 < spread < 1, 0 < waist <= 1"
    sa = (1 - spread) / 2

    def warp(u, v):
        k = math.floor(u * fans + 1e-12)
        s = u * fans - k
        ww = waist + (1 - waist) * (1 - (1 - min(max(v, 0.0), 1.0)) ** 1.6)
        mid = 0.5 - (0.5 - sa) * ww                     # where the fan's outer rib (s = sa) lies
        if s < sa:
            h = s * mid / sa
        elif s > 1 - sa:
            h = 1 - (1 - s) * mid / sa
        else:
            h = 0.5 + (s - 0.5) * ww
        return (k + h) / fans

    def ulines(cuts, girth):
        out = []
        for f in range(fans):
            base = f / fans
            out.append((base, "rib"))
            for j in range(1, gap_bars + 1):
                out.append((base + sa * j / (gap_bars + 1) / fans, "bar"))
                out.append((base + (1 - sa * j / (gap_bars + 1)) / fans, "bar"))
            for i in range(fan_ribs):
                out.append((base + (sa + (1 - 2 * sa) * i / (fan_ribs - 1)) / fans, "rib" if i in (0, fan_ribs - 1) else "ring"))
        return out

    def springers(P):
        env = _env(controls)
        G0 = _ev(gauge, env)
        cx, cy = _ev(at[0], env), _ev(at[1], env)
        sh = _Shells()
        hw = 0.5 * (1 - 2 * sa) * waist / fans           # half the fan's foot, in u
        for f in range(fans):
            uc = (f + 0.5) / fans
            c = P(uc, 0.0)
            cn = tuple(_ev(x, env) for x in c)
            z_at = lambda t: _ev(P(uc, t)[2], env) - cn[2]
            ct = tuple(_ev(x, env) for x in P(uc, _invert(z_at, 0.45 * G0, 0.0, 0.5)))   # the surface at the top
            a0 = tuple(_ev(x, env) for x in P(uc - hw, 0.0))
            a1 = tuple(_ev(x, env) for x in P(uc + hw, 0.0))
            t = _unit(_sub(a1, a0))
            r = _unit((cn[0] - cx, cn[1] - cy, 0.0))
            ht = math.dist(a0, a1) / 2 + 0.08 * G0
            verts = []
            pull = math.hypot(ct[0] - cx, ct[1] - cy) - math.hypot(cn[0] - cx, cn[1] - cy)   # dome leaning in
            for dz, dp in ((-0.19 * G0, 0.0), (0.45 * G0, pull)):
                for dt, dr in ((-ht, -0.3 * G0), (ht, -0.3 * G0), (ht, -0.03 * G0), (-ht, -0.03 * G0)):
                    o = _add(_add(_mul(t, dt), _mul(r, dr + (dp if dr > -0.1 else 0.0))), (0.0, 0.0, dz))
                    verts.append(([X(c[i]) + o[i] for i in range(3)], _add(cn, o)))
            sh.shell(verts, [[3, 2, 1, 0], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]])
        return self.mat(id_ + "-springers", sh.emit(self, id_ + "-springer"), slot)
    _dome(self, id_, radius, rise, profile, ulines, warp, rings, ring_pitch, pane_length, crown, lantern, oculus,
          at, gauge, controls, slot, glass_slot, leaded, extra_frame=springers, divisions=(1, 1), proud=proud)
    self.glazing[id_].update({"fans": fans, "fan_ribs": fans * fan_ribs, "meridians": fans})
    return id_


def dome_lantern(self, id_, radius, height, at=(0, 0, 0), sides=8, cap_rise=None, finial=True, gauge=1.0,
                 controls=None, slot="iron", cap_slot="patina", glass_slot="glass", leaded=None):
    """A crown lantern: a polygonal glazed drum (flat panes, light I posts at the corners, sill and head rings)
    under a turned cap with a finial.

    Frame: the drum's foot is centred on `at`; its corners lie on a circle of `radius`, z up to at.z + height;
    the cap (rise `cap_rise`, default 0.8 radius, slot cap_slot) overhangs the head by a tenth of the radius.
    Dimensions may be X. Example: g.dome_lantern("lantern", 0.8, 0.9, at=[0, 0, 6.0], sides=8)"""
    r, h = X(radius), X(height)
    ax, ay, az = (X(c) for c in at)
    G = X(gauge)
    cs = [(math.cos(2 * PI * k / sides), math.sin(2 * PI * k / sides)) for k in range(sides + 1)]

    def P(u, v):
        f = (u % 1.0) * sides
        k = min(int(math.floor(f + 1e-12)), sides - 1)
        t = f - k
        return [ax + r * (cs[k][0] * (1 - t) + cs[k + 1][0] * t), ay + r * (cs[k][1] * (1 - t) + cs[k + 1][1] * t), az + h * v]
    did = id_ + "-drum"
    self.glazed_surface(did, P, [(k / sides, "rib") for k in range(sides)], [(0.0, "edge"), (1.0, "edge")],
                        closed_u=True, gauge=gauge, controls=controls, slot=slot, glass_slot=glass_slot, leaded=leaded,
                        sizes={"rib": (0.06, 0.09)})
    cr = X(cap_rise) if cap_rise is not None else r * 0.8
    zt = az + h + G * 0.03
    ro = r * 1.1
    prof = [[0, zt], [ro, zt], [ro, zt + G * 0.05], [r * 1.02, zt + G * 0.08]]
    prof += [[r * (1.02 * math.cos(PI / 2 * i / 8)), zt + G * 0.08 + cr * math.sin(PI / 2 * i / 8)] for i in range(1, 8)]
    top = zt + G * 0.08 + cr
    if finial:
        prof += [[G * 0.03, top], [G * 0.03, top + G * 0.12], [G * 0.06, top + G * 0.16], [G * 0.03, top + G * 0.22],
                 [0, top + G * 0.24]]
    else:
        prof += [[0, top]]
    self.n(id_ + "-cap-turned", "revolve", profile=[[_out(a), _out(b)] for a, b in prof], sides=max(16, 2 * sides))
    self.n(id_ + "-cap-smooth", "smoothNormals", [id_ + "-cap-turned"])
    self.xf(id_ + "-cap-at", id_ + "-cap-smooth", t=[ax, ay, 0])
    self.mat(id_ + "-cap", id_ + "-cap-at", cap_slot)
    self.join(id_ + "-frame", did + "-frame", id_ + "-cap")
    self.join(id_ + "-glass", did + "-glass")
    self.join(id_, id_ + "-frame", id_ + "-glass")
    self.__dict__.setdefault("glazing", {})[id_] = {"sides": sides, "panes": self.glazing[did]["panes"]}
    return id_


# ----------------------------------------------------------------------------------------------- attach
for _f in (glazed_surface, glazed_vault, ridge_lantern, glazed_dome, fan_dome, dome_lantern):
    setattr(Graph, _f.__name__, _f)
