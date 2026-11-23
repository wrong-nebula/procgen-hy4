"""Reusable parts for kit builders: arches, mouldings, walling, flanges, wheels, ties, and SDS/FWN castings.

Distilled from the best-judged bench entries of 2026-11-17 (the arched bay by Opus 5.5, the repo's own flange and
handwheel) plus OpenSubdiv-grade SDS & face-weighted-normal (FWN) hard-surface parts so that a builder can assemble
an object from named, proportioned parts instead of writing every profile by hand. Every function:

- takes the Graph first and an id, and keeps its internal node ids under that id (`<id>-...`);
- accepts plain numbers or kitlib expressions (C("pitch"), sums, mn/mx ...), so parts follow the controls;
- returns the id of its output node (geometry you can join, transform or give a material);
- names the values it derives from the controls with `G.let`, so each is written into the document once (as a
  `derived` value read like a control) instead of being copied into every point that uses it.

Frames are Z-up, metres. Arches lie in the x-z plane, centred on x = 0, and are extruded across y; mouldings run
along x. Use `G.xf(...)` to place a part elsewhere.

    from kitparts import (fit_arch, fit_ring_depth, arched_wall, channel_joints, voussoirs, moulding, ashlar, bolted_flange,
                          spoked_wheel, tie_rod, cast_housing, sds_cylinder, filleted_box, curved_boss_patch)
"""
from kitlib import INDEX, PI, atan2, cos, iff, lt, mn, mx, rnd, sin, sq, sqrt
from kittype import arch_inscribe, arch_sign_board, inscribe, number_plaque, sign_board  # noqa: F401

# ---------------------------------------------------------------- extrusions
def extrude_x(G, id_, profile_yz, length, x0=None):
    """Extrude a closed (y, z) profile along x: centred on x = 0, or starting at x0."""
    G.n(id_ + "-stock", "extrude", profile=profile_yz, depth=length)
    return G.xf(id_, id_ + "-stock", t=[length * -0.5 if x0 is None else x0, 0, 0], r=[PI / 2, 0, PI / 2])


def extrude_y(G, id_, profile_xz, depth, y_far):
    """Extrude a closed (x, z) profile along y; the solid spans y = y_far - depth .. y_far."""
    G.n(id_ + "-stock", "extrude", profile=profile_xz, depth=depth)
    return G.xf(id_, id_ + "-stock", t=[0, y_far, 0], r=[PI / 2, 0, 0])


# ---------------------------------------------------------------- mouldings
# Profiles as (projection beyond the wall face, height above the moulding's base), metres, for a bay about 4.2 m
# high; `scale` resizes them. Projection 0 is the wall face; the profile is closed back into the wall (-0.12).
MOULDINGS = {
    "cornice": [[-0.12, 0], [0.03, 0], [0.03, 0.05], [0.06, 0.07], [0.08, 0.1], [0.09, 0.13], [0.2, 0.15], [0.22, 0.15],
                [0.22, 0.24], [0.24, 0.25], [0.255, 0.27], [0.265, 0.3], [0.27, 0.34], [-0.12, 0.34]],
    "string-course": [[-0.1, 0], [0.02, 0], [0.02, 0.03], [0.05, 0.05], [0.06, 0.1], [0.07, 0.1], [0.07, 0.14], [-0.1, 0.14]],
    "impost": [[-0.1, 0], [0.025, 0], [0.025, 0.09], [0.055, 0.1], [0.055, 0.195], [0.085, 0.2], [0.085, 0.3], [-0.1, 0.3]],
    "plinth": [[-0.1, 0], [0.06, 0], [0.06, 0.35], [0.035, 0.36], [0.035, 0.42], [-0.1, 0.42]],
    "coping": [[-0.1, 0], [0.08, 0], [0.08, 0.04], [0.1, 0.06], [0.1, 0.22], [0.06, 0.26], [-0.1, 0.26]],
}


def moulding_height(kind, scale=1.0):
    """Height of a named moulding at a scale (the top of its profile)."""
    return max(z for _, z in MOULDINGS[kind]) * scale


def moulding(G, id_, kind, length, face_y, z, scale=1.0, outward=-1, x0=None):
    """A moulded course running along x: `kind` is a MOULDINGS name or your own (projection, height) profile.
    Its base sits at height z on a wall face at y = face_y, projecting toward `outward` (-1: toward -y, the street
    side in the site frame). Centred on x = 0 (or starting at x0), `length` long."""
    prof = MOULDINGS[kind] if isinstance(kind, str) else kind
    pts = [[face_y + p * scale * outward, z + h * scale] for p, h in prof]
    if outward > 0:
        pts.reverse()  # keep the profile's winding when it is mirrored
    return extrude_x(G, id_, pts, length, x0)


# ---------------------------------------------------------------- arches
def fit_arch(G, id_, half_span, crown, spring_min):
    """Proportion an arch: semicircular when the crown leaves room above `spring_min`, else the segmental arch whose
    intrados crown is at `crown` and springs at `spring_min`. Returns a dict of values (named `<id>-...` in the
    document): half_span, crown, spring (springing height), rise, rho (intrados radius), zo (centre height),
    phi (half angle). Pass the dict to arched_wall, voussoirs, channel_joints and fit_ring_depth."""
    L = lambda k, v: G.let(f"{id_}-{k}", v)
    R, top = L("half-span", half_span), L("crown", crown)
    spring = L("spring", mx(spring_min, top - R))
    rise = L("rise", mn(R, top - spring))
    rho = L("rho", (sq(R) + sq(rise)) / (rise * 2))
    zo = L("zo", spring + rise - rho)
    phi = L("phi", atan2(R, rho - rise))
    return {"half_span": R, "crown": top, "spring": spring, "rise": rise, "rho": rho, "zo": zo, "phi": phi,
            "_G": G, "_id": id_}


def fit_ring_depth(arch, half_width, top, wanted=0.42, margin=0.05):
    """A voussoir ring depth that fits: at most `wanted`, and small enough that the ring stays inside the half
    piers (|x| <= half_width) and under `top`. Use the same depth for `arched_wall` and `voussoirs`."""
    crown_extrados_room = top - (arch["zo"] + arch["rho"]) - margin
    side_room = half_width - arch["rho"] - margin       # semicircular case: the extrados reaches x = rho + depth
    return arch["_G"].let(arch["_id"] + "-ring-depth", mx(0.12, mn(wanted, mn(side_room, crown_extrados_room))))


def wedge(a, r0, r1, w0=0.0, w1=0.0):
    """(x, z) corners of one stone between radii r0 and r1 about +z, half angle a, widened by w0 / w1 at its ends."""
    return [[r0 * sin(a) * -1 - w0, r0 * cos(a)], [r0 * sin(a) + w0, r0 * cos(a)],
            [r1 * sin(a) + w1, r1 * cos(a)], [r1 * sin(a) * -1 - w1, r1 * cos(a)]]


def arch_points(arch, radius, segments=28):
    """Points (x, z) along a circle of `radius` about the arch centre, from the west springing to the east."""
    phi, zo = arch["phi"], arch["zo"]
    return [[radius * sin(phi * (2.0 * i / segments - 1)), zo + radius * cos(phi * (2.0 * i / segments - 1))]
            for i in range(segments + 1)]


def arched_wall(G, id_, arch, ring_depth, half_width, top, y_back, thickness, segments=28):
    """A wall in the x-z plane from x = -half_width to +half_width and z = 0 to `top`, with the arch opening (and the
    seat of its voussoir ring, `ring_depth` deep) cut out: half piers and spandrels in one solid. Extruded across y
    from y_back - thickness to y_back. Put `voussoirs` in the notch."""
    R, hs = arch["half_span"], arch["spring"]
    notch = arch_points(arch, G.let(id_ + "-notch-r", arch["rho"] + ring_depth - 0.02), segments)
    profile = ([[half_width * -1, 0], [R * -1, 0], [R * -1, hs]] + notch +
               [[R, hs], [R, 0], [half_width, 0], [half_width, top], [half_width * -1, top]])
    return extrude_y(G, id_, profile, thickness, y_back)


def voussoirs(G, id_, arch, ring_depth, y_back, thickness, stone=0.32, proud=0.04, fascia=True, keystone=True,
              key_rise=0.12, key_proud=0.1, joints=True):
    """The arch ring: an odd number of wedge voussoirs (about `stone` metres on the face, the count follows the
    span) radiating from the arch centre, `ring_depth` deep, standing `proud` in front of a wall `thickness` thick
    whose back face is y_back. Optional outer fascia step, a wider keystone rising `key_rise` above the extrados and
    `key_proud` prouder, and joint grooves on both faces and the soffit. Returns the joined stones."""
    rho, zo, phi = arch["rho"], arch["zo"], arch["phi"]
    L = lambda k, v: G.let(f"{id_}-{k}", v)
    re = L("re", rho + ring_depth)
    n = L("count", mx(3, rnd(phi * (rho + ring_depth * 0.5) / stone)) * 2 + 1)
    a = L("half-angle", phi / n)                                 # half angle of one stone
    vy = y_back + 0.01
    yf = y_back - thickness
    parts = [extrude_y(G, id_ + "-main", wedge(a, rho, re), thickness + proud + 0.01, vy)]
    if fascia:
        parts.append(extrude_y(G, id_ + "-band", wedge(a, rho + ring_depth * 0.6, re), thickness + proud + 0.04, vy))
    G.join(id_ + "-stone", *parts)
    stone_id = id_ + "-stone"
    if joints:
        eps = L("joint-eps", 0.007 / (rho + ring_depth * 0.5))
        jl = []
        for side, sg in (("l", -1), ("r", 1)):
            for face, y in (("f", yf - 0.1), ("b", vy + 0.03)):
                jl.append(G.guide(f"{id_}-j-{side}-{face}", [rho * sin(a - eps) * sg, y, rho * cos(a - eps)],
                                  [re * sin(a - eps) * sg, y, re * cos(a - eps)]))
            jl.append(G.guide(f"{id_}-j-{side}-soffit", [(rho - 0.03) * sin(a - eps) * sg, yf - 0.04, (rho - 0.03) * cos(a - eps)],
                              [(rho - 0.03) * sin(a - eps) * sg, vy, (rho - 0.03) * cos(a - eps)]))
        G.join(id_ + "-joint-lines", *jl)
        G.stroke(id_ + "-jointed", stone_id, id_ + "-joint-lines", profile="groove", width=0.012, depth=0.008, step=0.05, reach=0.3)
        stone_id = id_ + "-jointed"
    G.n(id_ + "-at", "line", start=[0, 0, zo], step=[0, 0, 0], count=n)
    G.n(id_ + "-ring", "instance", [id_ + "-at", stone_id], rotation=[0, phi * -1 + a * (INDEX * 2 + 1), 0])
    if not keystone:
        return G.join(id_, id_ + "-ring")
    extrude_y(G, id_ + "-key-stock", wedge(a, rho - 0.03, re + key_rise, 0.03, 0.05), thickness + proud + key_proud, vy + 0.01)
    G.xf(id_ + "-key", id_ + "-key-stock", t=[0, 0, zo])
    return G.join(id_, id_ + "-ring", id_ + "-key")


def channel_joints(G, id_, wall, arch, ring_depth, half_width, z0, z1, face_y, courses=9, width=0.028, depth=0.014):
    """Horizontal channel joints (rusticated refends, surface detail) on the street face (y = face_y) of an
    `arched_wall`: `courses` courses between z0 and z1, each joint clipped to the pier face below the springing and
    to the arch ring above it. Returns the jointed wall."""
    R, hs, zo = arch["half_span"], arch["spring"], arch["zo"]
    L = lambda k, v: G.let(f"{id_}-{k}", v)
    re = L("re", arch["rho"] + ring_depth)
    xes, zse = L("xes", re * sin(arch["phi"])), L("zse", zo + re * cos(arch["phi"]))
    z0, z1 = L("z0", z0), L("z1", z1)
    lines = []
    for j in range(1, courses):
        z = L(f"course-{j}", z0 + (z1 - z0) * (j / courses))
        lin = R + (z - hs) * (xes - R) / mx(zse - hs, 0.001)
        circ = sqrt(mx(sq(re) - sq(z - zo), 0))
        xb = iff(lt(z, hs), R, iff(lt(z, zse), lin, circ))
        for side, sg in (("e", 1), ("w", -1)):
            lines.append(G.guide(f"{id_}-{j}-{side}", [xb * sg, face_y - 0.03, z], [half_width * sg, face_y - 0.03, z]))
    G.join(id_ + "-lines", *lines)
    return G.stroke(id_, wall, id_ + "-lines", profile="groove", width=width, depth=depth, step=0.25, reach=0.6)


# ---------------------------------------------------------------- walling
def ashlar(G, id_, surface, at, r, size, course=0.38, block=0.75, bond=0.5, width=0.012, depth=0.006):
    """Coursed joints (surface detail) on one face of `surface`: a rectangle `size` = [w, h] centred at `at`,
    rotated by `r` (e.g. [PI / 2, 0, 0] for a face square to y). Returns the jointed surface."""
    G.courses(id_ + "-lines", at=at, r=r, size=size, course=course, block=block, bond=bond)
    return G.stroke(id_, surface, id_ + "-lines", profile="groove", width=width, depth=depth, step=0.25, reach=0.3)


# ---------------------------------------------------------------- machinery
_AXIS_R = {"x": [0, PI / 2, 0], "y": [PI / 2 * -1, 0, 0], "z": None}


def bolted_flange(G, id_, axis, at, outer_r, bore_r, thickness, bolts=12, bolt_circle=None, face=1,
                  head="hex", bolt_r=0.026, washer=0.04, sides=32):
    """A bored flange ring on an axis ("x", "y" or "z") centred at `at`, `thickness` thick, with `bolts` heads (detail)
    on a bolt circle (default midway between bore and rim) on the face toward `face` (+1 or -1 along the axis).
    The bore stays open (pipe ends show the hole)."""
    G.annulus(id_ + "-turned", [[bore_r, thickness * -0.5], [outer_r, thickness * -0.5], [outer_r, thickness * 0.5],
                                [bore_r, thickness * 0.5]], sides)
    G.xf(id_ + "-ring", id_ + "-turned", t=list(at), r=_AXIS_R[axis])
    bc = (bore_r + outer_r) * 0.5 if bolt_circle is None else bolt_circle
    off = [0, 0, 0]
    off["xyz".index(axis)] = face * (thickness * 0.5 + 0.2)
    G.outline(id_ + "-circle", at=[at[i] + off[i] for i in range(3)], r=_AXIS_R[axis], shape="circle", radius=bc)
    return G.fasteners(id_, id_ + "-ring", id_ + "-circle", head=head, radius=bolt_r, height=bolt_r * 0.85,
                       washer=washer, count=bolts, seed=7, reach=0.4)


def spoked_wheel(G, id_, at, rim_r, rim_section=0.022, spokes=6, hub_r=None, hub_h=None, spoke_r=None, sides=8):
    """A horizontal handwheel (in the x-y plane) centred at `at`: a round rim of section radius `rim_section`, a
    turned hub and `spokes` round spokes. Rotate it with G.xf for another plane."""
    hub_r = rim_r * 0.2 if hub_r is None else hub_r
    hub_h = rim_r * 0.3 if hub_h is None else hub_h
    spoke_r = rim_section * 0.6 if spoke_r is None else spoke_r
    from kitlib import T
    G.tube(id_ + "-rim", [rim_r * cos(T * 2 * PI), rim_r * sin(T * 2 * PI), 0], 48, rim_section, sides, closed=1)
    G.rod(id_ + "-spoke", [hub_r * 0.8, 0, 0], [rim_r, 0, 0], spoke_r, 6)
    G.ring(id_ + "-spokes", id_ + "-spoke", spokes, PI * 2 / spokes)
    G.lathe(id_ + "-hub", [[0, hub_h * -0.5], [hub_r, hub_h * -0.5], [hub_r, hub_h * 0.5], [0, hub_h * 0.5]], 16)
    G.join(id_ + "-parts", id_ + "-rim", id_ + "-spokes", id_ + "-hub")
    return G.xf(id_, id_ + "-parts", t=list(at))


def tie_rod(G, id_, axis, start, end, at, radius=0.016, plate_r=0.075, plate_t=0.025):
    """An iron tie rod along an axis ("x", "y" or "z") from `start` to `end` (coordinates on that axis), passing
    through `at` on the other two, with a round anchor plate at each end."""
    i = "xyz".index(axis)
    a, b = list(at), list(at)
    a[i], b[i] = start, end
    G.rod(id_ + "-rod", a, b, radius, 8)
    plates = []
    for k, p in (("a", a), ("b", b)):
        G.n(f"{id_}-plate-{k}-stock", "cylinder", radius=plate_r, depth=plate_t, sides=12)
        plates.append(G.xf(f"{id_}-plate-{k}", f"{id_}-plate-{k}-stock", t=p, r=_AXIS_R[axis]))
    return G.join(id_, id_ + "-rod", *plates)


# ---------------------------------------------------------------- SDS & hard-surface cast parts
def cast_housing(G, id_, size, at=None, boss_inset=0.0, boss_height=0.0, wall_crease=1.8, rim_crease=3.2,
                 levels=2, lod_levels=None):
    """A cast-iron or moulded hard-surface housing with optional raised top boss, variable Chaikin semi-sharp
    creases and exact limit-surface normals (zero shrinkage at mating faces)."""
    src = G.n(id_ + "-box", "cube", size=list(size))
    if boss_inset or boss_height:
        src = G.face_extrude(id_ + "-boss", src, inset=boss_inset, offset=boss_height,
                             selection={"normal": [0, 0, 1], "angle": 10})
    G.crease(id_ + "-cr-wall", src, sharpness=wall_crease, min_angle=30)
    G.crease(id_ + "-cr-rim", id_ + "-cr-wall", sharpness=rim_crease, min_angle=30,
             selection={"normal": [0, 0, 1], "angle": 15})
    opts = {"creaseMethod": "chaikin"}
    if lod_levels is not None:
        opts["lodLevels"] = list(lod_levels)
    sds = G.subdivide(id_ + "-sds", id_ + "-cr-rim", levels=levels, limit=1, limit_normals=1, **opts)
    return G.xf(id_, sds, t=list(at)) if at is not None else sds


def sds_cylinder(G, id_, radius, depth, at=None, axis="z", sides=12, rings=2, rim_crease=2.8,
                 levels=2, lod_levels=None):
    """An SDS-clean cylindrical drum, capacitor or vessel using an all-quad grid cap (zero rim pinching)."""
    G.n(id_ + "-cage", "cylinder", radius=radius, depth=depth, sides=sides, rings=rings, cap="quad")
    G.crease(id_ + "-cr", id_ + "-cage", sharpness=rim_crease, min_angle=45)
    opts = {"creaseMethod": "chaikin"}
    if lod_levels is not None:
        opts["lodLevels"] = list(lod_levels)
    sds = G.subdivide(id_ + "-sds", id_ + "-cr", levels=levels, limit=1, limit_normals=1, **opts)
    r = _AXIS_R[axis]
    return G.xf(id_, sds, t=list(at) if at is not None else None, r=r) if (at is not None or r is not None) else sds


def filleted_box(G, id_, size, at=None, width=0.015, segments=3, mode="fwn", levels=2, angle=55):
    """A hard-surface rectangular block with controlled industrial edges:
    - mode="fwn": mid-poly multi-segment circular fillet + face-area-squared weighted normals (fast, flat panels).
    - mode="sds": all-quad support loops (segments=2, profile=1.0) -> Catmull-Clark limit surface."""
    src = G.n(id_ + "-box", "cube", size=list(size))
    if mode == "sds":
        G.support_loops(id_ + "-hold", src, width=width, min_angle=30)
        body = G.subdivide(id_ + "-sds", id_ + "-hold", levels=levels, limit=1, limit_normals=1)
    else:
        G.chamfer(id_ + "-bev", src, width=width, min_angle=30, segments=segments, profile=0.5, clampOverlap=1)
        body = G.smooth(id_ + "-sm", id_ + "-bev", angle=angle, weighting="areaSq")
    return G.xf(id_, body, t=list(at)) if at is not None else body


def curved_boss_patch(G, id_, target, size, at, r=None, divisions=(2, 6, 5), offset=0.010, thickness=0.014,
                      crease=2.2, levels=2, back_normal=(0, 1, 0), front_normal=(0, -1, 0)):
    """A thin nameplate, collar or mounting boss shrinkwrapped onto a curved SDS surface (`target`) without pinching."""
    G.n(id_ + "-box", "cube", size=list(size), divisions=list(divisions))
    G.xf(id_ + "-pos", id_ + "-box", t=list(at), r=list(r) if r is not None else None)
    G.shrinkwrap(id_ + "-back", id_ + "-pos", target, offset=offset, method="nearest",
                 selection={"normal": list(back_normal), "angle": 35})
    G.shrinkwrap(id_ + "-front", id_ + "-back", target, offset=offset + thickness, method="nearest",
                 selection={"normal": list(front_normal), "angle": 35})
    G.crease(id_ + "-cr", id_ + "-front", sharpness=crease, min_angle=35)
    return G.subdivide(id_, id_ + "-cr", levels=levels, limit=1, limit_normals=1)


def bored_nut(G, id_, outer_r, bore_r, depth, at=None, axis="z", bevel=None):
    """A true open-bore all-quad hexagon nut (`revolve` closed chamfered ring, sides=6 + FWN normals) so studs
    and bolts of radius < `bore_r` pass cleanly through the bore with zero physical solid interpenetration."""
    hl = depth * 0.5
    b = mn(outer_r * 0.14, hl * 0.30) if bevel is None else bevel
    # Inscribed circle of the 6-sided bore equals bore_r -> vertex radius = bore_r / cos(30 deg) = bore_r * 1.155.
    r_in = bore_r * 1.16
    sec = [[r_in, -hl], [outer_r - b, -hl], [outer_r, -hl + b],
           [outer_r, hl - b], [outer_r - b, hl], [r_in, hl]]
    G.n(id_ + "-raw", "revolve", profile=sec + [sec[0]], sides=6)
    r = _AXIS_R[axis]
    if at is not None or r is not None:
        G.smooth(id_ + "-sm", id_ + "-raw", angle=42, weighting="areaAngle")
        return G.xf(id_, id_ + "-sm", t=list(at) if at is not None else None, r=r)
    return G.smooth(id_, id_ + "-raw", angle=42, weighting="areaAngle")


def bored_collar(G, id_, outer_r, bore_r, length, at=None, axis="z", sides=24, bevel=0.003):
    """A true open-bore spacer sleeve, keeper tube or thrust washer (`revolve` closed ring off-axis + FWN)
    so pins and through-bolts pass cleanly through the bore with real mechanical clearance."""
    hl = length * 0.5
    b = mn(bevel, mn((outer_r - bore_r) * 0.35, hl * 0.35))
    sec = [[bore_r, -hl], [outer_r - b, -hl], [outer_r, -hl + b],
           [outer_r, hl - b], [outer_r - b, hl], [bore_r, hl]]
    G.n(id_ + "-raw", "revolve", profile=sec + [sec[0]], sides=sides)
    r = _AXIS_R[axis]
    if at is not None or r is not None:
        G.smooth(id_ + "-sm", id_ + "-raw", angle=45, weighting="areaAngle")
        return G.xf(id_, id_ + "-sm", t=list(at) if at is not None else None, r=r)
    return G.smooth(id_, id_ + "-raw", angle=45, weighting="areaAngle")


def drafted_plinth(G, id_, size, at=None, bevel=0.014, courses=2, margin=0.055, block=0.45):
    """A crisp FWN chamfered limestone masonry plinth with chiselled top margin border and coursed ashlar sides.
    Details are applied strictly after chamfer/smooth so they survive cleanly."""
    sx, sy, sz = size
    cx, cy, cz = (at if at is not None else (0.0, 0.0, sz * 0.5))
    filleted_box(G, id_ + "-base", [sx, sy, sz], at=[cx, cy, cz], width=bevel, segments=2, mode="fwn")
    cur = id_ + "-base"
    # Drafted top margin groove
    G.outline(id_ + "-top-g", at=[cx, cy, cz + sz * 0.5 + 0.012], shape="rect",
              size=[mx(0.05, sx - 2 * margin), mx(0.05, sy - 2 * margin)])
    G.seam(id_ + "-top", cur, id_ + "-top-g", width=0.007, depth=0.004)
    cur = id_ + "-top"
    if courses and courses >= 1:
        ch = sz / courses
        for tag, pos, rot, span in (
            ("s", [cx, cy - sy * 0.5 - 0.012, cz], [PI * 0.5, 0, 0], sx - 2 * bevel),
            ("n", [cx, cy + sy * 0.5 + 0.012, cz], [-PI * 0.5, 0, 0], sx - 2 * bevel),
            ("w", [cx - sx * 0.5 - 0.012, cy, cz], [PI * 0.5, 0, -PI * 0.5], sy - 2 * bevel),
            ("e", [cx + sx * 0.5 + 0.012, cy, cz], [PI * 0.5, 0, PI * 0.5], sy - 2 * bevel),
        ):
            G.courses(id_ + f"-c-{tag}-g", at=pos, r=rot, size=[mx(0.06, span), mx(0.04, sz - 0.01)],
                      course=ch, block=block, bond=0.5)
            G.seam(id_ + f"-c-{tag}", cur, id_ + f"-c-{tag}-g", width=0.008, depth=0.005)
            cur = id_ + f"-c-{tag}"
    return cur


def spoked_sheave(G, id_, outer_r, width, bore_r, groove_r=0.022, spokes=6, at=None, axis="x", sides=32):
    """A true open-bore grooved cable sheave (turned FWN rim + open-bore hub + radial spokes) so an axle pin
    of radius < `bore_r` passes cleanly through with zero solid interpenetration."""
    hw = width * 0.5
    rim_in = mx(bore_r + 0.04, outer_r - groove_r * 2.1 - 0.032)
    hub_out = mn(rim_in - 0.02, bore_r * 1.9 + 0.018)
    hub_hw = hw * 1.08
    # Open-bore hub ring
    bored_collar(G, id_ + "-hub", hub_out, bore_r, hub_hw * 2.0, axis="z", sides=24, bevel=0.004)
    # Grooved U-channel rim revolved off-axis (closed ring profile, 100% quads)
    fl = mn(0.012, hw * 0.25)
    g_root = outer_r - groove_r * 1.45
    rim_sec = [
        [rim_in, -hw], [outer_r - 0.004, -hw], [outer_r, -hw + 0.004], [outer_r, -hw + fl],
        [g_root, -groove_r * 0.55], [g_root, groove_r * 0.55],
        [outer_r, hw - fl], [outer_r, hw - 0.004], [outer_r - 0.004, hw], [rim_in, hw],
    ]
    G.n(id_ + "-rim-raw", "revolve", profile=rim_sec + [rim_sec[0]], sides=sides)
    G.smooth(id_ + "-rim", id_ + "-rim-raw", angle=42, weighting="areaAngle")
    # Tapered oval/rectangular radial spokes bridging hub_out -> rim_in (zero overlap with bore!)
    sp_len = mx(0.02, rim_in - hub_out + 0.014)
    sp_mid = (hub_out + rim_in) * 0.5
    sp_w = mn(width * 0.42, (outer_r - bore_r) * 0.18)
    sp_t = mn(width * 0.52, sp_w * 1.15)
    filleted_box(G, id_ + "-sp-one", [sp_len, sp_w, sp_t], at=[sp_mid, 0, 0], width=0.004, segments=2, mode="fwn")
    G.ring(id_ + "-spokes", id_ + "-sp-one", spokes, 2 * PI / spokes)
    G.join(id_ + "-asm", id_ + "-hub", id_ + "-rim", id_ + "-spokes")
    r = _AXIS_R[axis]
    return G.xf(id_, id_ + "-asm", t=list(at) if at is not None else None, r=r) if (at is not None or r is not None) else id_ + "-asm"


def pillow_block_bearing(G, id_, bore_r, length=0.09, at=None, axis="x", sides=20):
    """An open-bore cast-iron split pillow-block journal bearing (bored housing + twin downward-facing bolted
    mounting ears) that cleanly seats a shaft of radius < `bore_r` with zero interpenetration."""
    outer_r = bore_r * 1.9 + 0.018
    cx, cy, cz = (at if at is not None else (0.0, 0.0, 0.0))
    bored_collar(G, id_ + "-body", outer_r, bore_r, length, at=[cx, cy, cz], axis=axis, sides=sides, bevel=0.005)
    ear_w = outer_r * 1.10
    ear_h = outer_r * 0.55
    parts = [id_ + "-body"]
    for side, sgn in (("l", -1), ("r", 1)):
        if axis == "x":
            sz = [length * 0.88, ear_w, ear_h]
            pos = [cx, cy + sgn * (outer_r * 0.92 + ear_w * 0.30), cz - outer_r + ear_h * 0.5]
        elif axis == "y":
            sz = [ear_w, length * 0.88, ear_h]
            pos = [cx + sgn * (outer_r * 0.92 + ear_w * 0.30), cy, cz - outer_r + ear_h * 0.5]
        else:
            sz = [ear_w, ear_h, length * 0.88]
            pos = [cx + sgn * (outer_r * 0.92 + ear_w * 0.30), cy - outer_r + ear_h * 0.5, cz]
        filleted_box(G, id_ + f"-ear-{side}", sz, at=pos, width=0.004, segments=2, mode="fwn")
        parts.append(id_ + f"-ear-{side}")
    return G.join(id_, *parts)


def helical_spring(G, id_, coil_r, wire_r, height, turns=5, at=None, axis="z", segs_per_turn=14, sides=8):
    """A quad-capped swept helical valve or governor spring (`curve` + `tube(cap='quad')` + FWN smooth)."""
    import math
    total = max(12, int(turns * segs_per_turn))
    pts = []
    for k in range(total + 1):
        u = k / total
        ang = u * turns * 2.0 * math.pi
        pts.append([coil_r * math.cos(ang), coil_r * math.sin(ang), (u - 0.5) * height])
    G.n(id_ + "-path", "spline", points=pts, closed=0, resolution=1, interpolation="polyline")
    G.n(id_ + "-raw", "tube", [id_ + "-path"], radius=wire_r, sides=sides, cap="quad")
    G.smooth(id_ + "-sm", id_ + "-raw", angle=55, weighting="areaAngle")
    r = _AXIS_R[axis]
    return G.xf(id_, id_ + "-sm", t=list(at) if at is not None else None, r=r) if (at is not None or r is not None) else id_ + "-sm"


def bolted_clevis(G, id_, gap, cheek_t, pin_r, length, at=None, axis="z"):
    """A forked mechanical clevis yoke with twin open-bore eyes (`bored_collar`) + cross-pin (`sds_cylinder`)
    for governor linkages, furnace balance beams, and tie rods."""
    eye_out = pin_r * 2.1 + 0.008
    hw = gap * 0.5 + cheek_t * 0.5
    parts = []
    # Base yoke block
    filleted_box(G, id_ + "-base", [gap + 2 * cheek_t, eye_out * 1.6, length * 0.35],
                 at=[0, 0, -length * 0.32], width=0.003, segments=2, mode="fwn")
    parts.append(id_ + "-base")
    for side, sgn in (("l", -1), ("r", 1)):
        filleted_box(G, id_ + f"-arm-{side}", [cheek_t, eye_out * 1.5, length * 0.68],
                     at=[sgn * hw, 0, length * 0.08], width=0.003, segments=2, mode="fwn")
        bored_collar(G, id_ + f"-eye-{side}", eye_out, pin_r * 1.08, cheek_t,
                     at=[sgn * hw, 0, length * 0.42], axis="x", sides=16, bevel=0.002)
        parts += [id_ + f"-arm-{side}", id_ + f"-eye-{side}"]
    sds_cylinder(G, id_ + "-pin", pin_r, gap + 2 * cheek_t + 0.024, at=[0, 0, length * 0.42],
                 axis="x", sides=12, levels=1, rim_crease=2.8)
    parts.append(id_ + "-pin")
    G.join(id_ + "-asm", *parts)
    r = _AXIS_R[axis]
    return G.xf(id_, id_ + "-asm", t=list(at) if at is not None else None, r=r) if (at is not None or r is not None) else id_ + "-asm"


def saddle_collar(G, id_, host_r, branch_r, fillet_r=0.025, top_z=None, at=None, axis="x", sides=24, levels=2,
                  host_proxy=None):
    """Analytical all-quad cast-iron saddle weld fillet where a +Z branch cylinder of radius `branch_r`
    meets a horizontal host cylinder of radius `host_r` (along `axis` 'x' or 'y'). Zero BSP triangle soup,
    zero solid clipping inside the host cylinder, and optional `transferNormals` onto the skirt from `host_proxy`."""
    import math
    if top_z is None:
        top_z = host_r + fillet_r * 2.2
    r_max = min(host_r * 0.94, branch_r + fillet_r)
    eff_f = max(0.005, r_max - branch_r)
    # 4 analytical 3D section rings following z_saddle(theta) = sqrt(host_r^2 - (r*sin(theta))^2)
    def saddle_ring(rad, dz, flat_z=None):
        pts = []
        for k in range(sides):
            th = k * 2.0 * math.pi / sides
            x = rad * math.cos(th)
            y = rad * math.sin(th)
            zs = math.sqrt(max(1e-6, host_r * host_r - min(y * y, (host_r * 0.98) ** 2)))
            z = flat_z if flat_z is not None else (zs + dz)
            pts.append([x, y, z] if axis == "x" else [y, x, z])
        return pts

    sections = [
        saddle_ring(r_max, -0.002),
        saddle_ring(branch_r + eff_f * 0.42, eff_f * 0.25),
        saddle_ring(branch_r, eff_f),
        saddle_ring(branch_r, 0.0, flat_z=top_z),
    ]
    G.loft(id_ + "-raw", sections=sections, segments=sides, rings=1, cap="quad")
    G.crease(id_ + "-cr-top", id_ + "-raw", sharpness=3.6, min_angle=0,
             selection={"plane": {"normal": [0, 0, 1], "dist": top_z, "tol": 0.004}})
    G.crease(id_ + "-cr-bot", id_ + "-cr-top", sharpness=3.2, min_angle=45,
             selection={"zRange": [0.0, host_r + eff_f * 0.15]})
    G.subdivide(id_ + "-sds", id_ + "-cr-bot", levels=levels, creaseMethod="chaikin",
                limit=1, limit_normals=1, subdiv_normals=1, non_uniform=1)
    cur = id_ + "-sds"
    if at is not None:
        cur = G.xf(id_ + "-xf", cur, t=list(at))
    if host_proxy is not None:
        return G.transfer_normals(id_, cur, host_proxy, max_distance=eff_f * 0.35, blend=0.85)
    return G.xf(id_, cur) if cur != id_ else cur


def pipe_tee_junction(G, id_, main_r, main_len, branch_r, branch_len, fillet_r=0.028, bore_ratio=0.72,
                      at=None, axis="x", sides=24):
    """A cast-iron flanged pipe T-junction combining an open-bore main barrel (`G.pipe`), an analytical
    all-quad `saddle_collar` weld fillet, and a raised branch flange — zero BSP artifacts or solid clipping."""
    hl = main_len * 0.5
    main_bore = main_r * bore_ratio
    br_bore = branch_r * bore_ratio
    G.pipe(id_ + "-main", axis, -hl, hl, main_r, main_bore, sides=sides)
    G.smooth(id_ + "-main-sm", id_ + "-main", angle=42, weighting="areaAngle")
    top_z = main_r + branch_len
    saddle_collar(G, id_ + "-saddle", main_r, branch_r, fillet_r=fillet_r, top_z=top_z - 0.015,
                  axis=axis, sides=sides, levels=2, host_proxy=id_ + "-main-sm")
    bored_collar(G, id_ + "-flange", branch_r * 1.42, br_bore, 0.032, at=[0, 0, top_z], axis="z", sides=sides)
    G.join(id_ + "-asm", id_ + "-main-sm", id_ + "-saddle", id_ + "-flange")
    return G.xf(id_, id_ + "-asm", t=list(at)) if at is not None else id_ + "-asm"
