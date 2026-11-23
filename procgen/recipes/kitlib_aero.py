"""Aeronautical part helpers for kit builders: airfoil sections, wings and fins, propellers, nacelles.
Built by Opus 5.5 (medium), 2026-11-16.

kitlib.py imports this module at its end, so builders get the helpers as `Graph` methods:

    from kitlib import Graph, C, PI, naca4
    g.wing("fin", 3.2, 2.4, 1.1, sweep=0.5, tip_round=0.4, split=0.7, r=[PI / 2, 0, 0])
    g.propeller("prop", 2.6, 1.8, blades=2, style="wood")

Every part is a closed, manifold, outward `surface` or lathe shell; a propeller is its hub shell and N
blade shells sunk into it, so `pgm audit` sees one piece. Metres and radians, Z-up. Lengths and angles
may be X expressions (controls); point and station counts are Python ints. Tests and a sample sheet:
recipes/test_kitlib_aero.py.
"""
import math

import kitlib
from kitlib import PI, X, atan2, cos, sin

__all__ = ["naca4", "naca4_parameters"]


# --- airfoil sections ----------------------------------------------------------------------------------

def naca4_parameters(code):
    """(camber m, camber position p, thickness t) as chord fractions from a NACA 4-digit code: "2412",
    2412, or a tuple (m, p, t) passed through (t may be above 0.4 for a round propeller shank)."""
    if isinstance(code, (tuple, list)):
        return tuple(float(c) for c in code)
    s = code if isinstance(code, str) else f"{int(code):04d}"
    assert len(s) == 4 and s.isdigit(), f"NACA 4-digit code: {code!r}"
    return int(s[0]) / 100.0, int(s[1]) / 10.0, int(s[2:]) / 100.0


def _naca_at(m, p, t, x):
    """Upper and lower surface points at chord station x (NACA 4-digit, closed trailing edge)."""
    yt = 5 * t * (0.2969 * math.sqrt(x) - 0.1260 * x - 0.3516 * x * x + 0.2843 * x ** 3 - 0.1036 * x ** 4)
    if m <= 0 or p <= 0:
        return (x, yt), (x, -yt)
    if x < p:
        yc, dy = m / p ** 2 * (2 * p * x - x * x), 2 * m / p ** 2 * (p - x)
    else:
        yc, dy = m / (1 - p) ** 2 * (1 - 2 * p + 2 * p * x - x * x), 2 * m / (1 - p) ** 2 * (p - x)
    th = math.atan(dy)
    return (x - yt * math.sin(th), yc + yt * math.cos(th)), (x + yt * math.sin(th), yc - yt * math.cos(th))


def _camber_at(m, p, x):
    if m <= 0 or p <= 0:
        return 0.0
    return m / p ** 2 * (2 * p * x - x * x) if x < p else m / (1 - p) ** 2 * (1 - 2 * p + 2 * p * x - x * x)


def naca4(code="0012", points=16, x0=0.0, x1=1.0):
    """A NACA 4-digit section as one closed loop of (x, y) in chord units: leading edge at (0, 0),
    trailing edge at (1, 0), y up on the suction side. The trailing edge is closed (coefficient
    -0.1036): the loop runs counter-clockwise from the trailing edge over the upper surface to the
    leading edge and back along the lower surface, each of those two vertices once, 2 * points
    vertices in all. Stations are cosine-spaced (fine at the nose and the tail). x0 / x1 cut a
    chordwise piece [x0, x1] closed by straight ends (a wing's hinged control surface); a piece keeps
    both corner points at a cut end (2 * points + 1 or + 2 vertices).
    code: "0012" symmetric, "2412" cambered (2 % camber at 0.4 chord, 12 % thick), or (m, p, t).
    Example: naca4("2412", 12)  ->  24 (x, y) pairs"""
    m, p, t = naca4_parameters(code)
    n = max(3, int(points))
    xs = [x0 + (x1 - x0) * (1 - math.cos(PI * k / n)) / 2 for k in range(n + 1)]
    up, lo = zip(*(_naca_at(m, p, t, x) for x in xs))
    upper = list(reversed(up))                     # x1 .. x0
    lower = list(lo)                               # x0 .. x1
    if x0 <= 0:
        lower = lower[1:]                          # one leading-edge vertex
    if x1 >= 1:
        lower = lower[:-1]                         # one trailing-edge vertex
    return upper + lower


# --- shared pieces -------------------------------------------------------------------------------------

def _loft(g, id_, loops, smooth=True, slot=None, start_edge=False):
    """One closed `surface` through equal-length point loops, capped flat at both ends. Loops are
    section loops whose (chord x thickness) normal points back toward the first loop, as naca4
    sections are when chord is +X, thickness +Z and the loft runs along +Y. The end caps are zipped
    into triangles across the section (see _zip_cap): a tiny or cambered cap polygon is then never
    rejected as non-planar or folded."""
    L = len(loops[0])
    assert all(len(lp) == L for lp in loops), "loft loops need equal point counts"
    verts, idx = [], []
    for lp in loops:
        idx.append(list(range(len(verts), len(verts) + L)))
        verts.extend([list(pt) for pt in lp])
    faces = _zip_cap(idx[0], start_edge)           # the first cap faces back along the loft
    for A, B in zip(idx, idx[1:]):
        for i in range(L):
            j = (i + 1) % L
            faces.append([A[i], B[i], B[j], A[j]])
    faces += [list(reversed(f)) for f in _zip_cap(idx[-1], start_edge)]
    part = g.n(id_ + "-shell", "surface", vertices=verts, faces=faces)
    if smooth:
        part = g.n(id_ + "-smooth", "smoothNormals", [part])
    if slot:
        part = g.mat(id_ + "-slot", part, slot)
    return part


def _zip_cap(loop, start_edge):
    """Triangles closing a section loop, zipped across it from the trailing edge (vertex 0, or the cut
    edge between the last vertex and vertex 0 when start_edge) to the far end: each step takes the
    next vertex on the upper or the lower side, whichever is behind in loop order, so the rungs pair
    upper and lower points of about the same chord station. Triangles keep the loop's winding."""
    L = len(loop)
    i, j = (0, L - 1) if start_edge else (0, 0)
    tris = []
    if not start_edge:                              # first rung from the single trailing-edge vertex
        tris.append([loop[L - 1], loop[0], loop[1]])
        i, j = 1, L - 1
    while (j - i) > 1:
        if (i - 0) <= (L - 1 - j):                  # upper side behind: advance it
            tris.append([loop[j], loop[i], loop[i + 1]])
            i += 1
        else:
            tris.append([loop[j - 1], loop[j], loop[i]])
            j -= 1
    return tris


def _round_nose(loop, n, a, steps=5):
    """A control surface's piece loop (naca4 cut at x0 > 0: upper corner at index n, lower at n + 1)
    with a half-elliptic nose of depth `a` (chord units) between the corners, so it turns smoothly
    under smoothNormals instead of shading a flat hinge face into the skin."""
    (ux, uy), (lx, ly) = loop[n], loop[n + 1]
    mx_, my_ = (ux + lx) / 2, (uy + ly) / 2
    nose = []
    for k in range(1, steps):
        th = PI * k / steps
        nose.append((mx_ + (ux - mx_) * math.cos(th) - a * math.sin(th), my_ + (uy - my_) * math.cos(th)))
    return loop[:n + 1] + nose + loop[n + 1:]


def _zero(v):
    v = v.v if isinstance(v, X) else v
    return isinstance(v, (int, float)) and v == 0


def _tan(a):
    a = a.v if isinstance(a, X) else a
    return math.tan(a) if isinstance(a, (int, float)) else sin(a) / cos(a)


def _cap(k, K, phi_max):
    """Rounded-tip station k of K: (fraction of the cap length reached, thickness scale)."""
    phi = phi_max * k / K
    return math.sin(phi) / math.sin(phi_max), math.cos(phi)


# --- wings, fins, rudders ------------------------------------------------------------------------------

def wing(self, id_, span, root_chord, tip_chord, sweep=0.0, dihedral=0.0, twist=0.0, section="0012",
         tip_section=None, points=16, stations=4, tip_round=0.0, round_steps=4, split=None, gap=0.012,
         hinges=3, at=None, r=None, slot=None, control_slot=None, smooth=True):
    """A lofted wing or fin panel from root to tip: one closed solid, or two with `split`. Metres, radians.

    Frame: the root chord lies on +X from its leading edge at the origin (air flows along +X), the
    span runs along +Y, section thickness along +Z (suction side up). `span` is measured along the
    panel, root to tip, the rounded cap included; `root_chord` / `tip_chord` are the chords at the
    root and where the straight panel ends (span - tip_round). sweep: leading-edge sweep angle
    (positive moves the tip aft, +X). dihedral: the panel turned up about +X. twist: tip incidence
    relative to the root about the quarter chord, nose up positive (washout is negative), linear
    along the span. section / tip_section: NACA codes (see naca4), blended root to tip. points:
    section vertices per surface (a loop has 2 * points). stations: straight-panel segments.
    tip_round: length of a rounded tip cap (chord and thickness eased to a small flat end over
    `round_steps` segments); 0 ends the panel flat. A fin or rudder: r=[PI / 2, 0, 0] stands the
    span up along +Z (suction side to -Y); `at` places the root leading edge.
    split: chord fraction of a hinge line (e.g. 0.7): the panel aft of it, less a `gap` (chord
    fraction), is a separate control surface (rudder, elevator, aileron) over the whole span, with a
    rounded nose (the fixed part keeps a flat hinge face), hung on
    `hinges` short knuckle pins across the gap, so the pair still audits as one piece. Returns
    (panel, control) then, else one id. For a part-span aileron build the wing as two panels.
    span, chords, sweep, dihedral and twist may be X. Triangles about 4 * points * (stations +
    round_steps) a part: the default 0012 panel with a round tip is 572.
    Example: g.wing("wing", C("span"), 2.2, 1.2, sweep=0.08, dihedral=0.06, twist=-0.05, section="2412", tip_round=0.6)"""
    b, cr, ct, tr = X(span), X(root_chord), X(tip_chord), X(tip_round)
    sec0 = naca4_parameters(section)
    sec1 = naca4_parameters(tip_section) if tip_section is not None else sec0
    ns = max(1, int(stations))
    nr = 0 if _zero(tip_round) else max(1, int(round_steps))
    straight = b - tr
    tl = 0.0 if _zero(sweep) else _tan(sweep)
    cg, sg = (1.0, 0.0) if _zero(dihedral) else (cos(dihedral), sin(dihedral))
    assert split is None or 0.2 < split < 0.95 - gap, "wing split: a chord fraction between 0.2 and 0.95 - gap"
    pieces = [(0.0, 1.0)]
    if split is not None:                          # the control surface's round nose reaches forward to split + gap
        up, lo = _naca_at(*sec0, float(split) + float(gap))
        nose = 0.6 * (up[1] - lo[1]) * 0.5
        pieces = [(0.0, float(split)), (float(split) + float(gap) + nose, 1.0)]

    def place(eta, x_le, c, tf, tw, xc, yc):
        """A section point at distance eta along the span: leading edge at x_le, chord c, thickness scale tf."""
        dx, dz = (xc - 0.25) * c, yc * tf * c
        if not _zero(tw):
            co, si = cos(tw), sin(tw)
            dx, dz = dx * co + dz * si, dz * co - dx * si
        return [x_le + c * 0.25 + dx, eta * cg - dz * sg, eta * sg + dz * cg]

    rows = []                                      # (eta, x_le, chord, thickness scale, twist, section blend)
    for k in range(ns + 1):
        s = k / ns
        eta = straight * s
        rows.append((eta, eta * tl, cr + (ct - cr) * s, 1.0, X(twist) * s, s))
    for k in range(1, nr + 1):
        fy, fc = _cap(k, nr, math.radians(78))
        cf = fc ** 0.5                             # plan eased slower than thickness: rounded, not pointed
        eta = straight + tr * fy
        rows.append((eta, eta * tl + ct * ((1 - cf) * 0.35), ct * cf, fc / cf, X(twist), 1.0))

    parts = []
    for n_, (x0, x1) in enumerate(pieces):
        loops = []
        for eta, x_le, c, tf, tw, blend in rows:
            sec = tuple(a + (z - a) * blend for a, z in zip(sec0, sec1))
            lp = naca4(sec, points, x0, x1)
            if x0 > 0:
                lp = _round_nose(lp, max(3, int(points)), x0 - pieces[0][1] - float(gap))
            loops.append([place(eta, x_le, c, tf, tw, xc, yc) for xc, yc in lp])
        name = id_ if split is None else id_ + ("-panel" if n_ == 0 else "-control")
        parts.append(_loft(self, name + "-skin", loops, smooth, slot if n_ == 0 else (control_slot or slot), start_edge=x1 < 1))
    t = list(at) if at is not None else None
    if split is None:
        return self.xf(id_, parts[0], t=t, r=r)
    if hinges:
        xc = float(split) + float(gap) * 0.5
        m_, p_, t_ = sec0
        up, lo = _naca_at(m_, p_, t_, xc)
        yc, half = _camber_at(m_, p_, xc), (up[1] - lo[1]) * 0.5
        pins, nh = [], int(hinges)
        for j in range(nh):
            s = (j + 0.5) / nh
            ends = []
            for e in (s - 0.1 / nh, s + 0.1 / nh):
                ends.append(place(straight * e, straight * e * tl, cr + (ct - cr) * e, 1.0, X(twist) * e, xc, yc))
            pins.append(self.n(f"{id_}-hinge-{j}", "sweep", path=ends, radius=(cr + (ct - cr) * s) * (half * 0.55), sides=8))
        pinned = self.join(f"{id_}-hinges", *pins)
        if control_slot or slot:
            pinned = self.mat(f"{id_}-hinges-slot", pinned, control_slot or slot)
        parts[1] = self.join(f"{id_}-control-parts", parts[1], pinned)
    return self.xf(id_, parts[0], t=t, r=r), self.xf(id_ + "-control", parts[1], t=t, r=r)


# --- propellers ----------------------------------------------------------------------------------------

_PROP_STYLES = {   # fractions of the tip radius R; chord_at: radius fraction of the widest chord
    "wood": dict(hub_radius=0.085, hub_length=0.15, root_chord=0.095, chord=0.2, chord_at=0.6, root_t=0.55,
                 tip_t=0.08, camber=0.04, spinner=False, tip_round=0.08, shank=2, blades=2, slot="wood"),
    "metal": dict(hub_radius=0.1, hub_length=0.16, root_chord=0.065, chord=0.15, chord_at=0.7, root_t=0.95,
                  tip_t=0.06, camber=0.03, spinner=True, tip_round=0.05, shank=3, blades=3, slot="steel"),
}


def propeller(self, id_, diameter, pitch, blades=None, style="wood", turn="ccw", hub_radius=None, hub_length=None,
              chord=None, spinner=None, points=7, stations=7, sides=20, at=None, r=None, slot=None, hub_slot="steel",
              smooth=True):
    """A propeller: hub (and spinner) with N blades sunk into it; closed shells in one piece. Metres.

    Frame: the shaft axis is Z through the hub centre at the origin, thrust and the spinner's nose
    toward +Z; blade 0 points along +X and for turn "ccw" sweeps toward +Y (counter-clockwise seen
    from in front, +Z); "cw" mirrors it. diameter: tip to tip. pitch: geometric pitch, the advance
    per turn, so the blade angle at radius r is atan(pitch / (2 pi r)): steep at the root, flat at
    the tip. Proportions follow `style`, as fractions of the tip radius R:
      "wood"  (2 blades): broad blades widest (chord 0.2 R) at 0.6 R, thick root merging into a plain
              hub boss with a bolt-plate step, no spinner; slot "wood";
      "metal" (3 blades; 4 works): narrow blades widest (0.15 R) at 0.7 R on round shanks, with a
              spinner; slot "steel".
    Sections are NACA 4-digit-like, camber fading in from the root, thickness from round-ish at the
    root to 6-8 % at the tip, ending in a rounded tip. blades, hub_radius and hub_length (fractions
    of R), chord (max chord / R) and spinner (bool) override the style. points: section vertices per
    surface; stations: blade segments root to tip (plus 3 for the tip); sides: hub lathe sides.
    diameter and pitch may be X. A 3-blade metal propeller is 1,396 triangles, a 2-blade wooden one 904.
    Example: g.propeller("prop", 2.8, C("pitch"), blades=3, style="metal", at=[0, 0, 0.1])"""
    assert style in _PROP_STYLES, "propeller style: wood or metal"
    assert turn in ("ccw", "cw"), "propeller turn: ccw or cw"
    st = dict(_PROP_STYLES[style])
    N = int(blades if blades is not None else st["blades"])
    assert 2 <= N <= 8, "propeller blades: 2 to 8"
    R, P = X(diameter) * 0.5, X(pitch)
    hr = st["hub_radius"] if hub_radius is None else float(hub_radius)
    rh, Lh = R * hr, R * (st["hub_length"] if hub_length is None else float(hub_length))
    cmax = st["chord"] if chord is None else float(chord)
    spin = st["spinner"] if spinner is None else spinner
    sign = 1.0 if turn == "ccw" else -1.0
    xi0, xim, cap = 0.55 * hr, st["chord_at"], st["tip_round"]
    ns = max(3, int(stations))

    def law(xi):
        """(chord / R, thickness ratio, camber) at xi = r / R on the blade before its tip."""
        u = (xi - xi0) / (1 - cap - xi0)
        if xi <= xim:
            c = st["root_chord"] + (cmax - st["root_chord"]) * math.sin(PI / 2 * (xi - xi0) / (xim - xi0)) ** 1.3
        else:
            c = cmax * math.sqrt(1 - 0.55 * ((xi - xim) / (1 - xim)) ** 2)
        t = st["tip_t"] + (st["root_t"] - st["tip_t"]) * (1 - u) ** st["shank"]
        return c, t, st["camber"] * min(1.0, 2.5 * u)

    # Radial stations: the root inside the hub, one just outside it, then out to the tip cap.
    x1 = hr * 1.05
    xis = [xi0, x1] + [x1 + (1 - cap - x1) * (k / ns) ** 0.9 for k in range(1, ns + 1)]
    rows = [(xi, *law(xi), 1.0) for xi in xis]
    c_t, t_t, m_t = law(1 - cap)
    for k in range(1, 4):
        fy, fc = _cap(k, 3, math.radians(75))
        cf = fc ** 0.5
        rows.append((1 - cap + cap * fy, c_t * cf, t_t * fc / cf, m_t, cf))

    loops = []
    for xi, c, t, m, cf in rows:
        rad, cc = R * xi, R * c
        beta = atan2(P, rad * (2 * PI))
        cb, sb = cos(beta), sin(beta)
        loop = []
        for xc, yc in naca4((m, 0.4, t), points):
            dx = (xc - 0.35 - (1 - cf) * 0.1) * cc     # stacking line near the thickest point
            dn = yc * cc
            # chord direction (0, -cos b, -sin b), thickness direction (0, -sin b, cos b); "cw" mirrors y
            loop.append([rad, (dx * cb + dn * sb) * -sign, dn * cb - dx * sb])
        loops.append(loop if sign > 0 else list(reversed(loop)))
    blade = _loft(self, id_ + "-blade", loops, smooth, slot or st["slot"])
    parts = [self.ring(id_ + "-blades", blade, N, 2 * PI / N)]

    # Hub boss (and spinner): one lathe profile from the back face round to the nose.
    zb, zf = Lh * -0.5, Lh * 0.5
    prof = [[0, zb], [rh * 0.8, zb], [rh, zb + Lh * 0.12], [rh, zf - Lh * 0.12]]
    if spin:
        Ls = rh * 1.9
        for k in range(1, 9):
            a = PI / 2 * k / 8
            prof.append([rh * (math.cos(a) ** 0.8 if k < 8 else 0.0), zf - Lh * 0.12 + Ls * math.sin(a)])
    else:
        prof += [[rh * 0.8, zf], [rh * 0.4, zf], [rh * 0.4, zf + Lh * 0.15], [0, zf + Lh * 0.15]]
    self.n(id_ + "-hub-turned", "revolve", profile=prof, sides=int(sides))
    hub = self.n(id_ + "-hub-smooth", "smoothNormals", [id_ + "-hub-turned"]) if smooth else id_ + "-hub-turned"
    parts.append(self.mat(id_ + "-hub", hub, hub_slot) if hub_slot else hub)
    body = self.join(id_ + "-parts", *parts)
    return self.xf(id_, body, t=list(at) if at is not None else None, r=r)


# --- nacelles and cowlings -----------------------------------------------------------------------------

def nacelle(self, id_, length, radius, nose=None, tail=None, tail_radius=None, front_radius=None, sides=24,
            at=None, r=None, slot=None, style="round", height=0.85, corner=0.35, panels=4, grille=True, exhausts=3,
            detail_slot=None):
    """An engine nacelle or cowling, front face at z = 0. Metres.

    style "round" (the default): a turned nacelle, one closed lathe solid. Frame: axis Z; the flat front face
    (where a propeller hub sits) is at z = 0, the body runs back to z = -length. radius: the widest radius;
    the nose rounds over `nose` * length (default 0.25) from a front face of `front_radius` * radius (0.55);
    the tail tapers over `tail` * length (0.4) to a flat end of `tail_radius` * radius (0.3). length and
    radius may be X.

    style "boxy": a squared, riveted engine car (Built by Opus 5.5 (medium), 2026-11-16; see _boxy_nacelle).
    Same frame and taper parameters (defaults nose 0.16, tail 0.35, front_radius 0.72, tail_radius 0.4);
    the section is a rounded rectangle 2 * radius wide (x) and 2 * radius * `height` tall (y), corners of
    `corner` * the smaller half-side. Panel seams (laps) at `panels` + 1 girth stations and along the top
    and side midlines, a rivet row each side of every girth seam (not on it), a radiator grille on the
    lower front face (`grille`) and `exhausts` stubs per side behind the nose: all surface detail on the
    body except the stubs, short iron pipes sunk into the sides (`detail_slot`, default `slot`). One piece.
    Example: g.nacelle("engine", 2.4, 0.45); g.propeller("prop", 2.6, 1.8, style="metal", at=[0, 0, 0.2])
             g.nacelle("car", 2.6, 0.5, style="boxy", slot="steel", detail_slot="iron")"""
    assert style in ("round", "boxy"), "nacelle style: round or boxy"
    if style == "boxy":
        return _boxy_nacelle(self, id_, length, radius, 0.16 if nose is None else nose, 0.35 if tail is None else tail,
                             0.4 if tail_radius is None else tail_radius, 0.72 if front_radius is None else front_radius,
                             height, corner, panels, grille, exhausts, at, r, slot, detail_slot or slot)
    nose = 0.25 if nose is None else nose
    tail = 0.4 if tail is None else tail
    tail_radius = 0.3 if tail_radius is None else tail_radius
    front_radius = 0.55 if front_radius is None else front_radius
    Lx, Rx = X(length), X(radius)
    prof = [[0, -Lx], [Rx * tail_radius, -Lx]]
    for k in range(1, 5):                              # tail taper, convex
        f = k / 4
        prof.append([Rx * (tail_radius + (1 - tail_radius) * math.sin(PI / 2 * f)), Lx * (tail * f - 1)])
    prof.append([Rx, Lx * -nose])
    for k in range(1, 6):                              # nose rounded in to the front face
        a = PI / 2 * k / 5
        prof.append([Rx * (front_radius + (1 - front_radius) * math.cos(a)), Lx * (-nose * (1 - math.sin(a)))])
    prof.append([0, 0])
    self.n(id_ + "-turned", "revolve", profile=prof, sides=int(sides))
    part = self.n(id_ + "-smooth", "smoothNormals", [id_ + "-turned"])
    if slot:
        part = self.mat(id_ + "-slot", part, slot)
    return self.xf(id_, part, t=list(at) if at is not None else None, r=r)


def _rounded_rect(a, b, c, n=5):
    """Loop of a rounded rectangle, half sides a, b, corner radius c, n + 1 points per corner, counter-clockwise."""
    pts = []
    for q, (cx, cy) in enumerate(((a - c, b - c), (-a + c, b - c), (-a + c, -b + c), (a - c, -b + c))):
        for k in range(n + 1):
            th = PI / 2 * (q + k / n)
            pts.append((cx + c * math.cos(th), cy + c * math.sin(th)))
    return pts


def _boxy_nacelle(g, id_, length, radius, nose, tail, tail_radius, front_radius, height, corner, panels, grille,
                  exhausts, at, r, slot, detail_slot):
    """The squared, riveted engine car of nacelle(style="boxy"). Built by Opus 5.5 (medium), 2026-11-16.

    The shell is built for radius 1 and length 1 (numbers) and scaled by [radius, radius, length], which keeps
    the corner rounds circular in section, so length and radius may be X; the seams, rivets and grille are
    laid on the scaled body in metres (sizes in fractions of radius)."""
    from kitlib_ornament import _shell
    Lx, Rx = X(length), X(radius)
    A, B = 1.0, float(height)
    Cr = float(corner) * min(A, B)
    unit = _rounded_rect(A, B, Cr)
    stations = [(-1.0, tail_radius)]
    for k in range(1, 5):                                   # tail taper, convex
        f = k / 4
        stations.append((tail * f - 1, tail_radius + (1 - tail_radius) * math.sin(PI / 2 * f)))
    for k in range(0, 6):                                   # nose: a quarter round in to the front face
        a = PI / 2 * k / 5
        stations.append((-nose * (1 - math.sin(a)), front_radius + (1 - front_radius) * math.cos(a)))
    bev = 0.03                                              # small flat bevels so the end faces shade flat
    rings = [[(x * s * (1 - bev), y * s * (1 - bev), -1.0 - 0.004) for x, y in unit] for z, s in stations[:1]]
    rings += [[(x * s, y * s, z) for x, y in unit] for z, s in stations]
    rings += [[(x * s * (1 - bev), y * s * (1 - bev), 0.004) for x, y in unit] for z, s in stations[-1:]]
    verts, faces = _shell(rings, None, None)
    g.n(id_ + "-shell", "surface", vertices=verts, faces=faces)
    g.n(id_ + "-smooth", "smoothNormals", [id_ + "-shell"])
    body = g.xf(id_ + "-body", id_ + "-smooth", s=[Rx, Rx, Lx])

    # Panel seams: girth laps at panels + 1 stations over the parallel body, and laps along the top and both
    # side midlines; a rivet row each side of every girth seam.
    z0, z1 = -nose, tail - 1                                # parallel body, unit z
    zs = [z0 + (z1 - z0) * j / max(1, panels) for j in range(max(1, panels) + 1)]
    girth, rows = [], []
    for j, z in enumerate(zs):
        girth.append(g.outline(f"{id_}-girth-{j}", at=[0, 0, Lx * z], shape="rect", size=[Rx * 2 * A, Rx * 2 * B],
                               corner=Rx * Cr, segments=48))
        for side in (-1, 1):
            if (j == 0 and side > 0) or (j == len(zs) - 1 and side < 0):
                continue                                    # no row off the parallel body
            rows.append(g.outline(f"{id_}-row-{j}-{'f' if side > 0 else 'a'}", at=[0, 0, Lx * z + Rx * 0.07 * side],
                                  shape="rect", size=[Rx * 2 * A, Rx * 2 * B], corner=Rx * Cr, segments=48))
    longs = [g.guide(f"{id_}-long-{k}", [Rx * x, Rx * y, Lx * (z0 - 0.01)], [Rx * x, Rx * y, Lx * (z1 + 0.01)])
             for k, (x, y) in enumerate(((0, B), (A, 0), (-A, 0)))]
    g.join(id_ + "-seam-lines", *girth, *longs)
    g.join(id_ + "-rivet-lines", *rows)
    g.stroke(id_ + "-seamed", body, id_ + "-seam-lines", profile="lap", width=Rx * 0.05, height=Rx * 0.008,
             reach=Rx * 0.2)
    part = g.rivets(id_ + "-riveted", id_ + "-seamed", id_ + "-rivet-lines", Rx * 0.11, radius=Rx * 0.014,
                    height=Rx * 0.008, reach=Rx * 0.2)
    if grille:                                              # radiator: a beaded frame round vertical slots
        af, bf = A * front_radius, B * front_radius
        cy, hh, hw = -0.56 * bf, 0.25 * bf, 0.62 * af
        g.outline(id_ + "-grille-frame", at=[0, Rx * cy, Lx * 0.004], shape="rect",
                  size=[Rx * 2 * hw, Rx * 2 * hh], corner=Rx * 0.03, segments=24)
        slats = [g.guide(f"{id_}-slat-{k}", [Rx * x, Rx * (cy - 0.8 * hh), Lx * 0.004], [Rx * x, Rx * (cy + 0.8 * hh), Lx * 0.004])
                 for k, x in enumerate(hw * (2 * i / 8 - 1) * 0.85 for i in range(9))]
        g.join(id_ + "-slats", *slats)
        g.stroke(id_ + "-framed", part, id_ + "-grille-frame", profile="bead", width=Rx * 0.035, height=Rx * 0.012,
                 reach=Rx * 0.1)
        part = g.stroke(id_ + "-grilled", id_ + "-framed", id_ + "-slats", profile="groove", width=Rx * 0.028,
                        depth=Rx * 0.012, reach=Rx * 0.1)
    if slot:
        part = g.mat(id_ + "-skin", part, slot)
    parts = [part]
    stubs = []
    for side in (-1, 1):                                    # exhaust stubs: out of the side, bent back and down
        for k in range(int(exhausts)):
            z = Lx * (-nose - 0.03) - Rx * (0.3 * k + 0.14)
            y = Rx * (0.25 * B)
            path = [[Rx * side * (A - 0.15), y, z], [Rx * side * (A + 0.14), y, z],
                    [Rx * side * (A + 0.26), y - Rx * 0.1, z - Rx * 0.24]]
            stubs.append(g.n(f"{id_}-stub-{'l' if side < 0 else 'r'}{k}", "sweep", path=path, radius=Rx * 0.06, sides=10))
    if stubs:
        sp = g.join(id_ + "-stubs", *stubs)
        if detail_slot:
            sp = g.mat(id_ + "-stubs-slot", sp, detail_slot)
        parts.append(sp)
    whole = parts[0] if len(parts) == 1 else g.join(id_ + "-parts", *parts)
    return g.xf(id_, whole, t=list(at) if at is not None else None, r=r)


kitlib.Graph.wing = wing
kitlib.Graph.propeller = propeller
kitlib.Graph.nacelle = nacelle
kitlib.naca4 = naca4
kitlib.naca4_parameters = naca4_parameters
