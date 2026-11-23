"""Gothic tracery helpers on kitlib_curves: pointed arches, cusping, foils, windows, roses, railings.

Built by Opus 5.5 (medium), 2026-11-16. Tests and a sample sheet: recipes/test_kitlib_tracery.py.

Frame: tracery is drawn in its own plane, x across and y up, sill (or the centre, for roses) at the
origin, and stood up in the x-z plane (plane="xz": drawing y -> z, depth along y, centred) unless
plane says otherwise; `at` moves it. Metres.

Two ways of making it, as the founders and smiths did:
  style="bar"    bar tracery: flat bars of width `bar` on every centre line (frame, mullions, arch
                 heads, circles), all the space between them open. Cusps are the solid points left
                 between the foils cut into an opening.
  style="plate"  plate tracery (a cast or cut plate): solid, pierced only by the lights and the foiled
                 openings; the spandrels stay solid.
Either way the piece is one closed outline with holes, extruded by `depth` (Graph.plate), so it is a
single closed shell (or a few strips meeting face to face when large).

Geometry (all exact arcs until sampled at `tol`):
  pointed_arch   two-centred arch; fraction = radius / span (0.5 round, 1 equilateral, > 1 lancet,
                 0.5..1 drop arch).
  cusping        an opening's head becomes `foils` lobes: circles tangent inside the arch, centred on
                 the arch drawn `lobe` radius further in, spaced so neighbours overlap; `cusp` (0..1)
                 is the centre spacing over the lobe diameter (smaller = deeper cusps).
  foil_opening   trefoil, quatrefoil, cinquefoil, multifoil: n lobes tangent inside a circle.

Graph methods: tracery_arch (a cusped or plain lancet head), rosette (foiled circle), tracery_window
(kind "y", "intersecting", "geometric"), rose_window (spokes, cusped heads, oculi, foiled hub),
tracery_railing (posts, rails and an arcade of cusped arches with foiled roundels between, after the
Gothic iron walkway railing). Each returns its node id; module docs record triangle counts in the tests.
Functions with the same names and a _shapes suffix return the 2D shapes.
"""
import math

import kitlib_curves as K
from kitlib_curves import Arc, Seg

PI = math.pi
TOL = 0.0015


# ---------------------------------------------------------------- arches and lobes
def pointed_arch(span, fraction=1.0, spring=0.0, x=0.0):
    """Path from the left springer over the apex to the right springer (two arcs)."""
    assert span > 0 and fraction >= 0.5 - 1e-12, "pointed_arch: fraction is radius / span, at least 0.5"
    r = fraction * span
    th = math.acos(max(-1.0, min(1.0, (span / 2 - r) / r)))        # apex direction from the left centre
    return [Arc((x - span / 2 + r, spring), r, PI, th - PI), Arc((x + span / 2 - r, spring), r, PI - th, th - PI)]


def apex(span, fraction=1.0, spring=0.0):
    r = fraction * span
    return spring + math.sqrt(max(r * r - (r - span / 2) ** 2, 0.0))


def lancet(span, spring, fraction=1.0, sill=0.0, x=0.0):
    """Closed counter-clockwise path of a lancet light: sill, jambs to the springing, pointed head."""
    xl, xr = x - span / 2, x + span / 2
    path = [Seg((xl, sill), (xr, sill))]
    if spring > sill:
        path.append(Seg((xr, sill), (xr, spring)))
    path += K.reverse(pointed_arch(span, fraction, spring, x))
    if spring > sill:
        path.append(Seg((xl, spring), (xl, sill)))
    return path


def lancet_inset(span, spring, fraction, sill, x, d):
    """The lancet moved in by d all round (exact: the head keeps its centres, radius less d)."""
    r = fraction * span
    return lancet(span - 2 * d, spring, (r - d) / (span - 2 * d), sill + d, x)


def _bisect(f, lo, hi, n=80):
    flo = f(lo)
    for _ in range(n):
        mid = (lo + hi) / 2
        if (f(mid) > 0) == (flo > 0):
            lo, flo = mid, f(mid)
        else:
            hi = mid
    return (lo + hi) / 2


def cusped_head(span, radius, foils=3, cusp=0.8, tol=TOL):
    """Opening of a cusped pointed head in its own frame: springers at (+-span/2, 0), arch radius
    `radius` (centres on y = 0), apex up; open below y = 0 (down to -2 radius). Returns shapes."""
    assert foils >= 2, "cusping needs at least two foils"
    assert 0.2 <= cusp < 1.0, "cusp is the lobe spacing over the lobe diameter, 0.2..1"
    fr = radius / span

    def centres(rf):
        return K.resample(pointed_arch(span - 2 * rf, (radius - rf) / (span - 2 * rf)), count=foils)

    rf = _bisect(lambda rf: K._dist(*centres(rf)[:2]) - 2 * cusp * rf, span * 1e-4, span / 2 * 0.98)
    cs = centres(rf)
    parts = [K.rect(-span / 2, -2 * radius - span, span / 2, 0.0)] + [K.circle(c, rf, tol) for c in cs]
    if foils >= 3:
        parts.append(K.orient(cs + [(cs[-1][0], 0.0), (cs[0][0], 0.0)]))
    assert fr >= 0.5
    return K.union(*parts)


def cusped_opening(cell, mid, span, radius, up=(0.0, 1.0), foils=3, cusp=0.8, tol=TOL):
    """Cusp the head of an opening `cell` (shapes): its arch springs from the two points span/2 either
    side of `mid` on the line square to `up` (the way to the apex), with the given radius (centres on
    that line). Returns the cell with the cusped head."""
    up = K._unit(up)
    head = cusped_head(span, radius, foils, cusp, tol)
    return K.intersection(cell, K.transform(head, math.atan2(-up[0], up[1]), mid))


def foil_opening(c, radius, foils=4, cusp=0.8, rotate=PI / 2, tol=TOL):
    """A multifoil opening (3 trefoil, 4 quatrefoil, 5 cinquefoil...): `foils` lobes tangent inside the
    circle (c, radius), one centred toward `rotate`; cusp as in cusped_head. Returns shapes."""
    assert foils >= 3, "a foil opening needs at least three lobes"
    s = math.sin(PI / foils)
    rc = cusp * radius / (s + cusp)
    rl = radius - rc
    cs = [K._polar(c, rc, rotate + 2 * PI * k / foils) for k in range(foils)]
    return K.union(K.orient(cs), *[K.circle(p, rl, tol) for p in cs])


def _circle_path(c, r):
    return [Arc(c, r, 0.0, 2 * PI)]


# ---------------------------------------------------------------- composition
def _compose(outer, inner, lines, bar, features, style, tol):
    """Bar style: cells = inner - bars on the lines; a cell holding a feature's seed takes that
    feature's opening (clipped to the cell), the others stay open. Plate style: only the features'
    openings. Returns outer - openings."""
    if style == "plate":
        openings = [f(None) for _, f in features]
    else:
        assert style == "bar", "style is bar or plate"
        bars = K.union(*[K.stroke(c, bar, tol=tol) for c in lines])
        cells = K.difference(inner, bars)
        openings = []
        for shape in cells:
            hit = next((f for seed, f in features if K.contains([shape], seed)), None)
            openings.append(hit([shape]) if hit else [shape])
    return K.difference(outer, [s for o in openings for s in o])


def _outer_lancet(span, spring, fraction, grow):
    r = fraction * span
    return lancet(span + 2 * grow, spring, (r + grow) / (span + 2 * grow), -grow)


def arch_shapes(span, spring, fraction=1.0, bar=0.03, foils=0, cusp=0.8, frame=None, style="bar", tol=TOL):
    """One lancet light framed by a bar (frame width `frame`, default 1.5 bar), cusped when foils > 0."""
    frame = frame or 1.5 * bar
    r = fraction * span
    light = lambda cell: _cusp_light(cell, span, spring, r, 0.0, bar, foils, cusp, tol)
    main = lancet(span, spring, fraction)
    return _compose(_outer_lancet(span, spring, fraction, frame - bar / 2), main, [main], bar,
                    [((0.0, spring / 2), light)], style, tol)


def _cusp_light(cell, span, spring, r, x, bar, foils, cusp, tol, sill=0.0):
    inset = K.sample(lancet_inset(span, spring, r / span, sill, x, bar / 2), tol)
    cell = cell if cell is not None else [(K.orient(inset), [])]
    if not foils:
        return cell if cell else []
    return cusped_opening(cell, (x, spring), span - bar, r - bar / 2, (0.0, 1.0), foils, cusp, tol)


def rosette_shapes(radius, foils=4, bar=0.03, cusp=0.8, rotate=PI / 2, frame=None, style="bar", tol=TOL):
    """A foiled circle. Bar: a ring bar on the circle and a bar along the foil outline (its lobes
    tangent inside the ring), the spandrels between open. Plate: a disc pierced by the foil."""
    frame = frame or 1.5 * bar
    outer = [K.circle((0, 0), radius + frame - bar / 2, tol)]
    if style == "plate":
        return K.difference(outer, foil_opening((0, 0), radius - bar / 2, foils, cusp, rotate, tol))
    centre = foil_opening((0, 0), radius, foils, cusp, rotate, tol)[0][0]
    centre = centre + centre[:1]                                                 # the foil's centre line, closed
    return _compose(outer, K.circle((0, 0), radius, tol), [_circle_path((0, 0), radius), centre], bar, [], "bar", tol)


def _oculus(O_in, R_in, O_out, R_out, x, lo, hi):
    """Circle centred on x = const tangent inside the circle (O_in, R_in) and outside (O_out, R_out):
    (centre, radius), searching the centre height in lo..hi."""
    def g(y):
        c = (x, y)
        return K._dist(c, O_out) - R_out - (R_in - K._dist(c, O_in))
    ys = [lo + (hi - lo) * k / 60 for k in range(61)]
    for a, b in zip(ys, ys[1:]):
        if (g(a) > 0) != (g(b) > 0):
            y = _bisect(g, a, b)
            return (x, y), R_in - K._dist((x, y), O_in)
    raise AssertionError("tracery: no room for the oculus")


def window_shapes(span, spring, lights=2, kind="y", fraction=1.0, bar=0.03, foils=3, head_foils=4, cusp=0.8,
                  light_fraction=1.0, frame=None, style="bar", tol=TOL):
    """A traceried window, sill at y = 0, springing at `spring`, under a pointed arch of span and fraction.
      kind "y"            two lights; the mullion forks into the lights' inner haunches (radius of the
                          main arch); a foiled oculus in the head when head_foils > 0.
      kind "intersecting" `lights` lights; every mullion sends two arcs of the main radius up to the
                          arch, crossing into lozenges.
      kind "geometric"    even `lights` with their own heads (light_fraction) and a foiled oculus
                          tangent to the main arch and the two middle heads.
    foils cusps the light heads (0 none, 3 trefoil, 5 cinquefoil); cusp as in cusped_head."""
    frame = frame or 1.5 * bar
    R = fraction * span
    xl = -span / 2
    main = lancet(span, spring, fraction)
    lines, feats = [main], []
    w = span / lights
    O_L, O_R = (xl + R, spring), (span / 2 - R, spring)
    top = apex(span, fraction, spring)

    def light_feature(xc, lspan, lr):
        return ((xc, spring / 2), lambda cell, xc=xc: _cusp_light(cell, lspan, spring, lr, xc, bar, foils, cusp, tol))

    if kind == "y":
        assert lights == 2, "Y tracery has two lights"
        for xc, arc in ((-span / 4, 1), (span / 4, 0)):
            lines.append([pointed_arch(span / 2, R / (span / 2), spring, xc)[arc]])
            feats.append(light_feature(xc, span / 2, R))
        lines.append([Seg((0.0, 0.0), (0.0, spring))])
        if head_foils:
            c, rho = _oculus(O_L, R, (-R, spring), R, 0.0, spring, top)
            lines.append(_circle_path(c, rho))
            feats.append((c, lambda cell, c=c, rho=rho: _foil_in(cell, c, rho - bar / 2, head_foils, cusp, tol)))
    elif kind == "intersecting":
        assert lights >= 2
        assert R >= w / 2
        for i in range(1, lights):
            xm = xl + i * w
            lines.append([Seg((xm, 0.0), (xm, spring))])
            for centre, start, main_c in (((xm - R, spring), 0.0, O_L), ((xm + R, spring), PI, O_R)):
                hit = max(K.intersect_circles(centre, R, main_c, R), key=lambda p: p[1])
                end = math.atan2(hit[1] - spring, hit[0] - centre[0])
                lines.append([Arc(centre, R, start, end - start)])
        for i in range(lights):
            feats.append(light_feature(xl + (i + 0.5) * w, w, R))
    elif kind == "geometric":
        assert lights % 2 == 0, "geometric tracery: an even number of lights"
        lr = light_fraction * w
        for i in range(lights):
            xc = xl + (i + 0.5) * w
            lines.append(pointed_arch(w, light_fraction, spring, xc))
            feats.append(light_feature(xc, w, lr))
        for i in range(1, lights):
            lines.append([Seg((xl + i * w, 0.0), (xl + i * w, spring))])
        if head_foils:
            c, rho = _oculus(O_L, R, (-lr, spring), lr, 0.0, spring, top)
            lines.append(_circle_path(c, rho))
            feats.append((c, lambda cell, c=c, rho=rho: _foil_in(cell, c, rho - bar / 2, head_foils, cusp, tol)))
    else:
        raise AssertionError(f"window kind {kind!r}: y, intersecting or geometric")
    return _compose(_outer_lancet(span, spring, fraction, frame - bar / 2), main, lines, bar, feats, style, tol)


def _foil_in(cell, c, r, foils, cusp, tol):
    f = foil_opening(c, r, foils, cusp, PI / 2, tol)
    return f if cell is None else K.intersection(cell, f)


def rose_shapes(radius, spokes=12, bar=0.03, hub=0.28, spring=0.6, fraction=1.0, foils=3, hub_foils=6,
                oculus_foils=4, cusp=0.8, frame=None, style="bar", tol=TOL):
    """A rose window centred on the origin: outer ring (radius = its centre line), a hub ring (hub x
    radius) with a foiled opening, `spokes` radial bars to the springing circle (spring x radius),
    a pointed arch (fraction of its chord) closing each sector, cusped with `foils`, and a foiled
    oculus over each spoke end, tangent to the ring and the two arches."""
    frame = frame or 1.5 * bar
    n, R = spokes, radius
    rh, rs = hub * R, spring * R
    lines = [_circle_path((0, 0), R), _circle_path((0, 0), rh)]
    feats = [((0.0, 0.0), lambda cell: _foil_in(cell, (0, 0), rh - bar / 2, hub_foils, cusp, tol))]
    chord = 2 * rs * math.sin(PI / n)
    ra = fraction * chord
    for k in range(n):
        phi = 2 * PI * k / n
        u = (math.cos(phi), math.sin(phi))
        lines.append([Seg(K._mul(u, rh), K._mul(u, rs))])
        a, b = K._mul(u, rs), K._polar((0, 0), rs, phi + 2 * PI / n)
        rot = math.atan2(a[1] - b[1], a[0] - b[0])            # local x from b to a: the apex points outward
        m = K._mid(a, b)
        arch = pointed_arch(chord, fraction)
        lines.append([Arc(K.transform([s.c], rot, m)[0], s.r, s.a0 + rot, s.sweep) for s in arch])
        mid = phi + PI / n
        seed = K._polar((0, 0), (rh + rs) / 2, mid)
        feats.append((seed, lambda cell, a=a, b=b: _sector(cell, a, b, ra, bar, foils, cusp, tol)))
    # oculus over spoke 0 (then turned to every spoke): tangent inside the ring, outside the arch arc
    # that springs from the spoke end
    a, b = (rs, 0.0), K._polar((0, 0), rs, 2 * PI / n)
    rot = math.atan2(b[1] - a[1], b[0] - a[0])
    Oc = min((K.transform([s.c], rot, K._mid(a, b))[0] for s in pointed_arch(chord, fraction)),
             key=lambda c: abs(K._dist(c, a) - ra))         # centre of the arc through the spoke end

    def g(x):
        return K._dist((x, 0.0), Oc) - ra - (R - x)
    xc = _bisect(g, rs, R)
    rho = R - xc
    assert rho > 1.6 * bar, "rose window: no room for the oculi (lower spring or fraction)"
    for k in range(n):
        c = K._polar((0, 0), xc, 2 * PI * k / n)
        lines.append(_circle_path(c, rho))
        feats.append((c, lambda cell, c=c: _foil_in(cell, c, rho - bar / 2, oculus_foils, cusp, tol) if oculus_foils else cell))
    return _compose([K.circle((0, 0), R + frame - bar / 2, tol)], K.circle((0, 0), R, tol), lines, bar, feats, style, tol)


def _sector(cell, a, b, ra, bar, foils, cusp, tol):
    if cell is None:                                           # plate style: the sector light itself
        return []
    if not foils:
        return cell
    m = K._mid(a, b)
    return cusped_opening(cell, m, K._dist(a, b) - bar, ra - bar / 2, m, foils, cusp, tol)


def railing_shapes(length, height, bays=1, arches=4, bar=0.025, post=0.06, rail=0.05, fraction=1.0, foils=3,
                   roundel=0.34, roundel_foils=4, cusp=0.8, kind="arcade", tol=TOL):
    """The pierced panel of a Gothic iron railing, `bays` bays of `length` between post centres, from
    y = 0 to height: post bands, bottom and top rails, and in each bay an arcade of `arches` pointed
    arches on colonnettes, cusped with `foils`; kind "arcade" sets a foiled roundel (roundel x arch
    span) over each colonnette under the top rail, kind "intersecting" carries every arch's haunches
    on up to the top rail so they cross."""
    total = bays * length
    outer = [K.rect(-post / 2, 0.0, total + post / 2, height)]
    lines, feats = [], []
    W = length - post
    w = W / arches
    r = fraction * w
    y_top = height - rail + bar / 2                            # a bar on this line meets the top rail's face
    y_bot = rail - bar / 2
    rho = roundel * w
    for _ in range(40):                                        # springing so the roundels touch rail and arches
        D = rho + math.sqrt((r + rho) ** 2 - r * r)
        ys = y_top - D if kind == "arcade" else min(y_top - (apex(w, fraction) + bar * 1.5), y_top - r * 0.9)
        if kind != "arcade" or (apex(w, fraction, ys) + bar < y_top and ys > rail + 2 * bar):
            break
        rho *= 0.92
    assert ys > rail + 2 * bar, "railing: too low for its arches (fewer arches or a lower fraction)"
    inner = []
    for bay in range(bays):
        x0 = bay * length + post / 2
        inner.append(K.rect(x0, rail, x0 + W, height - rail))
        for j in range(arches + 1):
            xj = x0 + j * w
            if 0 < j < arches:
                lines.append([Seg((xj, y_bot), (xj, ys))])
        for j in range(arches):
            xc = x0 + (j + 0.5) * w
            arch = pointed_arch(w, fraction, ys, xc)
            if kind == "intersecting":
                lines += _carried(arch, ys, y_top, x0, x0 + W)
            else:
                lines.append(arch)
            feats.append(((xc, (rail + ys) / 2), lambda cell, xc=xc: _cusp_light(cell, w, ys, r, xc, bar, foils, cusp, tol, rail)))
        if kind == "arcade":
            for j in range(1, arches):
                c = (x0 + j * w, y_top - rho)
                lines.append(_circle_path(c, rho))
                feats.append((c, lambda cell, c=c: _foil_in(cell, c, rho - bar / 2, roundel_foils, cusp, tol)))
    bars = K.union(*[K.stroke(c, bar, tol=tol) for c in lines])
    cells = K.difference(inner, bars)
    openings = []
    for shape in cells:
        hit = next((f for seed, f in feats if K.contains([shape], seed)), None)
        openings += hit([shape]) if hit else [shape]
    return K.difference(outer, openings)


def _carried(arch, ys, y_top, xmin, xmax):
    """The arch's two arcs carried on past the apex up to y_top (clipped to the bay)."""
    out = []
    for s, side in zip(arch, (1, -1)):
        h = min(1.0, (y_top - ys) / s.r)
        # left arc (centre to the right) runs from pi down toward 0; right arc from 0 up toward pi
        a1 = math.asin(h)
        start, end = (PI, PI - a1) if side == 1 else (0.0, a1)
        arc = Arc(s.c, s.r, start, end - start)
        pts = [p for p in K.sample([arc], TOL) if xmin - 1e-9 <= p[0] <= xmax + 1e-9]
        out.append(pts)
    return out


# ---------------------------------------------------------------- Graph methods
def _plate(g, id_, shapes, depth, slot, plane, at):
    return g.plate(id_, shapes, depth, plane=plane, at=at, slot=slot)


def tracery_arch(self, id_, span=0.6, spring=1.2, fraction=1.0, bar=0.03, depth=0.025, foils=3, cusp=0.8, frame=None,
                 style="bar", slot=None, plane="xz", at=(0, 0, 0), tol=TOL):
    """A single framed lancet light, plain (foils=0) or cusped (3 trefoil-headed, 5 cinquefoil...),
    sill at `at`, standing in the x-z plane. Numbers only (worked out in Python)."""
    return _plate(self, id_, arch_shapes(span, spring, fraction, bar, foils, cusp, frame, style, tol), depth, slot, plane, at)


def rosette(self, id_, radius=0.25, foils=4, bar=0.025, depth=0.02, cusp=0.8, rotate=PI / 2, frame=None, style="bar",
            slot=None, plane="xz", at=(0, 0, 0), tol=TOL):
    """A trefoil (3), quatrefoil (4) or multifoil rosette in a ring, centred on `at`."""
    return _plate(self, id_, rosette_shapes(radius, foils, bar, cusp, rotate, frame, style, tol), depth, slot, plane, at)


def tracery_window(self, id_, span=1.2, spring=1.6, lights=2, kind="y", fraction=1.0, bar=0.035, depth=0.03, foils=3,
                   head_foils=4, cusp=0.8, light_fraction=1.0, frame=None, style="bar", slot=None, plane="xz",
                   at=(0, 0, 0), tol=TOL):
    """A traceried window or panel (see window_shapes for the kinds), sill centre at `at`."""
    return _plate(self, id_, window_shapes(span, spring, lights, kind, fraction, bar, foils, head_foils, cusp,
                                           light_fraction, frame, style, tol), depth, slot, plane, at)


def rose_window(self, id_, radius=1.0, spokes=12, bar=0.035, depth=0.03, hub=0.28, spring=0.6, fraction=1.0, foils=3,
                hub_foils=6, oculus_foils=4, cusp=0.8, frame=None, style="bar", slot=None, plane="xz", at=(0, 0, 0),
                tol=TOL):
    """A rose window centred on `at` (see rose_shapes)."""
    return _plate(self, id_, rose_shapes(radius, spokes, bar, hub, spring, fraction, foils, hub_foils, oculus_foils,
                                         cusp, frame, style, tol), depth, slot, plane, at)


def tracery_railing(self, id_, length=1.6, height=1.0, bays=1, arches=4, bar=0.022, depth=0.02, post=0.06,
                    post_depth=None, rail=0.045, fraction=1.0, foils=3, roundel=0.34, roundel_foils=4, cusp=0.8,
                    kind="arcade", handrail=0.05, slot=None, at=(0, 0, 0), tol=TOL):
    """A Gothic iron walkway railing along +x from `at` (the first post's foot): square posts every
    `length` (post x post_depth, a cap on each), the pierced panel between them (see railing_shapes)
    `depth` thick in the posts' middle, and a flat handrail `handrail` deep on top (0 for none).
    Everything overlaps its neighbours, so the railing is one piece."""
    pd = post_depth or post
    panel = _plate(self, id_ + "-panel", railing_shapes(length, height, bays, arches, bar, post, rail, fraction, foils,
                                                        roundel, roundel_foils, cusp, kind, tol), depth, None, "xz", at)
    parts = [panel]
    for k in range(bays + 1):
        x = at[0] + k * length
        parts.append(self.box(f"{id_}-post{k}", [post, pd, height], [x, at[1], at[2] + height / 2]))
        parts.append(self.box(f"{id_}-cap{k}", [post * 1.35, pd * 1.35, post * 0.45],
                              [x, at[1], at[2] + height + (handrail or 0) + post * 0.2]))
    if handrail:
        total = bays * length
        parts.append(self.box(id_ + "-handrail", [total + post, max(pd, depth) * 1.1, handrail],
                              [at[0] + total / 2, at[1], at[2] + height + handrail / 2 - 0.001]))
    node = self.join(id_ + "-parts", *parts) if slot else self.join(id_, *parts)
    return self.mat(id_, node, slot) if slot else node


def _attach():
    import kitlib
    for f in (tracery_arch, rosette, tracery_window, rose_window, tracery_railing):
        setattr(kitlib.Graph, f.__name__, f)


_attach()
