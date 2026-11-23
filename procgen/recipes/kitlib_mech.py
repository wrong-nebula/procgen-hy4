"""Open-bore Frontier SDS & FWN mechanical hardware helpers for kit builders:
split journal pedestals (`plummer_block`), bronze-bushed spoked pulleys & flywheels (`bushed_sheave`),
pierced linkage forks (`clevis_fork`), packed spindle glands (`stuffing_box`), twin-foot cast bridge
standards (`arched_yoke`), pierced axle frames (`pierced_cheek`), and radial clearance (`mech_bore`).

Every assembly obeys three physical invariants out of the box:
1. 0.5 mm (`_BUTT = 0.0005 m`) flush-butt seating between mating parts: `<= 3.0 mm` so `pgm audit`
   sees 1 connected structure, `> 0.0 mm` so `pgm check --intersections` sees 0 self-intersections
   after `bake`, and 0% buried solid overlap in MuJoCo CoACD physics audits.
2. Open through-bores (`mech_bore(shaft_r) = shaft_r + 0.0018 m`): 1.8 mm radial clearance passes both
   `pgm audit` connectivity and zero-contact MuJoCo hinge spin tests.
3. 100% valence-4 quad SDS control cages (`revolve` off-axis rings, matching-count `pierced_extrude`,
   and `loft` sections) so `pole_gate` passes with `pinchedPoles == 0`.

Attached to `kitlib.Graph` when `kitlib` is imported:

    from kitlib import Graph, C, mech_bore
    bore = mech_bore(0.036)
    g.plummer_block("brg-w", bore, center_h=0.42, at=[-0.65, 0, 0.42], axis="x")
    g.bushed_sheave("pulley", 0.34, 0.11, bore, spokes=6, at=[0, 0, 0.42], axis="x")
    g.clevis_fork("fork", 0.044, 0.018, 0.016, 0.18, at=[0, 0, 0.90], axis="z")
    g.stuffing_box("gland", 0.028, height=0.18, at=[0, 0, 0.72], axis="z")
    g.arched_yoke("yoke", 0.52, 0.64, mech_bore(0.028), at=[0, 0, 0.72], facing="x")
    g.pierced_cheek("cheek-e", 0.56, 0.84, 0.048, 0.42, bore, at=[0.075, 0, 0], side=1)
"""
import math
from kitlib import Graph, X, PI, mx, mn, cos, sin, _num

_BUTT = 0.0005
_BORE_CLEAR = 0.0018
_AXIS_R = {"x": [0, PI * 0.5, 0], "y": [-PI * 0.5, 0, 0], "z": None}


def mech_bore(shaft_r, clearance=_BORE_CLEAR):
    """Return an open bearing or fastener bore radius `shaft_r + clearance` (default +1.8 mm)
    that simultaneously satisfies `pgm audit` (<= 3.0 mm gap) and 0-clip MuJoCo spin tests."""
    return shaft_r + clearance


def _bore_cage_r(bore_r, sides):
    """Compensate Catmull-Clark limit shrinkage on an N-sided bore so the subdivided limit radius equals `bore_r`."""
    shrink = (2.0 / 3.0) + math.cos(2.0 * math.pi / sides) / 3.0
    return bore_r * (1.0 / shrink)


def _join_many(g, id_, parts):
    cur = [p for p in parts if p is not None]
    assert cur, f"_join_many({id_!r}): empty parts list"
    if len(cur) == 1:
        return cur[0]
    batch = 0
    while len(cur) > 28:
        nxt = []
        for i in range(0, len(cur), 28):
            ch = cur[i:i + 28]
            nxt.append(ch[0] if len(ch) == 1 else g.join(f"{id_}-b{batch}", *ch))
            if len(ch) > 1:
                batch += 1
        cur = nxt
    return g.join(id_, *cur)


def _mat_or_raw(g, id_, src, slot):
    return g.mat(id_, src, slot) if slot else src


def _fwn_box(g, id_, size, at=None, bevel=0.006, segments=2):
    bw = mn(bevel, mn(size[0], mn(size[1], size[2])) * 0.30)
    raw = g.n(id_ + "-b", "cube", size=list(size))
    g.chamfer(id_ + "-c", raw, width=bw, min_angle=30, segments=segments, profile=0.5, clampOverlap=1)
    sm = g.smooth(id_ + "-s", id_ + "-c", angle=52, weighting="areaSq")
    return g.xf(id_, sm, t=list(at)) if at is not None else sm


def _fwn_rod(g, id_, radius, length, at=None, axis="z", sides=18):
    g.n(id_ + "-r", "cylinder", radius=radius, depth=length, sides=sides, cap="quad")
    sm = g.smooth(id_ + "-s", id_ + "-r", angle=46, weighting="areaAngle")
    r = _AXIS_R[axis]
    if at is not None or r is not None:
        return g.xf(id_, sm, t=list(at) if at is not None else None, r=r)
    return sm


def _open_ring(g, id_, section, sides=24, at=None, axis="z", sds=False, crease=2.8, levels=2):
    """Revolve a closed off-axis ring `section = [[r, z], ...]` (`r > 0` everywhere -> 100% valence-4 quads, 0 poles)."""
    g.n(id_ + "-raw", "revolve", profile=list(section) + [section[0]], sides=sides)
    if sds:
        g.crease(id_ + "-cr", id_ + "-raw", sharpness=crease, min_angle=32)
        body = g.subdivide(id_ + "-sds", id_ + "-cr", levels=levels, creaseMethod="chaikin",
                           limit=1, limit_normals=1, g2_fair=1, subdiv_normals=1, lodLevels=[levels, 1, 0])
    else:
        body = g.smooth(id_ + "-sm", id_ + "-raw", angle=44, weighting="areaAngle")
    r = _AXIS_R[axis]
    if at is not None or r is not None:
        return g.xf(id_, body, t=list(at) if at is not None else None, r=r)
    return body


def _open_collar(g, id_, outer_r, bore_r, length, at=None, axis="z", sides=24, bevel=0.003):
    hl = length * 0.5
    b = mn(bevel, mn((outer_r - bore_r) * 0.32, hl * 0.32))
    sec = [[bore_r, -hl], [outer_r - b, -hl], [outer_r, -hl + b],
           [outer_r, hl - b], [outer_r - b, hl], [bore_r, hl]]
    return _open_ring(g, id_ + "-collar", sec, sides=sides, at=at, axis=axis, sds=False)


def _open_nut(g, id_, outer_r, bore_r, depth, at=None, axis="z"):
    hl = depth * 0.5
    b = mn(outer_r * 0.14, hl * 0.28)
    r_in = bore_r * 1.16  # 6-sided inscribed radius >= bore_r * 1.004
    sec = [[r_in, -hl], [outer_r - b, -hl], [outer_r, -hl + b],
           [outer_r, hl - b], [outer_r - b, hl], [r_in, hl]]
    return _open_ring(g, id_ + "-nut", sec, sides=6, at=at, axis=axis, sds=False)


def _anchor_stud_nut(g, id_, stud_r, stud_h, nut_r, nut_h, at_base):
    """Protruding vertical steel anchor stud + open-bore hex nut seated with `_BUTT` above `at_base` (`[x, y, z_top]`)."""
    bx, by, bz = at_base
    st = _fwn_rod(g, id_ + "-stud-pin", stud_r, stud_h, at=[bx, by, bz + _BUTT + stud_h * 0.5], axis="z", sides=14)
    nt = _open_nut(g, id_, nut_r, mech_bore(stud_r), nut_h,
                   at=[bx, by, bz + _BUTT + nut_h * 0.5 + _BUTT], axis="z")
    return [st, nt]


def _extrude_yz_to_x(g, id_, src_node, x_start):
    """Map a local `(u=y, v=z, w=0..depth)` extrusion to global `(x = x_start + w, y = u, z = v)`."""
    r1 = g.xf(id_ + "-ry", src_node, r=[0, PI * 0.5, 0])
    r2 = g.xf(id_ + "-rx", r1, r=[PI * 0.5, 0, 0])
    return g.xf(id_, r2, t=[x_start, 0, 0])


def plummer_block(self, id_, bore_r, center_h=None, width=None, length=0.11, at=None, axis="x",
                  oil_cup=True, body_slot="patina", bush_slot="bronze", bolt_slot="steel"):
    """Open-bore split cast-iron journal pedestal (`plummer block`) with Chaikin SDS housing (`0` pinched poles),
    machined FWN soleplate, protruding flanged bronze sleeve bushing, split-cap bolts, holding-down anchor studs
    + open-bore hex nuts, and optional top bronze lubricator cup.

    `at` is the `[x, y, z]` journal bore center (default `[0, 0, center_h]`), and the flat soleplate bottom sits at
    `z = at[2] - center_h` so setting `center_h = z_axis - z_bed` lands the base cleanly on `z_bed`."""
    L = lambda k, v: self.let(f"{id_}-{k}", v)
    br = L("br", bore_r)
    r_bush = L("rbush", br + mx(0.008, br * 0.24))
    r_hous = L("rhous", r_bush + mx(0.022, br * 0.52))
    ch = L("ch", mx(r_hous + 0.042, br * 2.3 + 0.036) if center_h is None else center_h)
    sole_t = L("solet", mx(0.018, length * 0.20))
    hw = L("hw", (r_hous * 1.95 + 0.042) if width is None else width * 0.5)
    body_len = L("blen", length * 0.88)
    z_web0 = L("zweb0", ch * -1 + sole_t + _BUTT)
    ped_hw = L("pedhw", r_hous * 1.24)

    body_parts, bush_parts, bolt_parts = [], [], []

    # 1. Machined FWN soleplate sitting from z = -ch to z = -ch + sole_t
    body_parts.append(_fwn_box(self, id_ + "-sole", [length * 1.04, hw * 2.0, sole_t],
                               at=[0, 0, ch * -1 + sole_t * 0.5], bevel=0.004))

    # 2. 24-pt all-quad pierced Chaikin SDS pedestal housing (0 pinched poles, 24 distinct CCW vertices!)
    outer_24 = [[ped_hw * (-1.0 + k / 3.0), z_web0] for k in range(7)]
    for k in range(1, 6):
        u = k / 5.0
        outer_24.append([ped_hw * (1.0 - u) + r_hous * u, z_web0 * (1.0 - u)])
    for k in range(1, 8):
        ang = math.pi * k / 8.0
        outer_24.append([r_hous * math.cos(ang), r_hous * math.sin(ang)])
    x7 = r_hous * math.cos(7.0 * math.pi / 8.0)
    z7 = r_hous * math.sin(7.0 * math.pi / 8.0)
    for k in range(1, 6):
        u = k / 6.0
        outer_24.append([x7 * (1.0 - u) - ped_hw * u, z7 * (1.0 - u) + z_web0 * u])

    rh_cage = _bore_cage_r(r_bush + _BUTT, 24)
    hole_24 = [[rh_cage * math.cos(-0.75 * math.pi + 2.0 * math.pi * k / 24.0),
                rh_cage * math.sin(-0.75 * math.pi + 2.0 * math.pi * k / 24.0)] for k in range(24)]
    raw_h = self.pierced_extrude(id_ + "-hraw", outer_24, [hole_24], body_len, cap="quad")
    cut_h = self.loop_cut(id_ + "-hcut", raw_h, margin=0.008, selection={"direction": [0, 0, 1], "dirAngle": 20})
    cr_h = self.crease(id_ + "-hcr", cut_h, sharpness=2.4, min_angle=32)
    sds_h = self.subdivide(id_ + "-hsds", cr_h, levels=2, creaseMethod="chaikin", limit=1, limit_normals=1,
                           g2_fair=1, subdiv_normals=1, lodLevels=[2, 1, 0])
    placed_h = _extrude_yz_to_x(self, id_ + "-hpos", sds_h, body_len * -0.5)
    # Post-SDS split-cap parting seam + cap bolts (0x8007)
    sg_e = self.guide(id_ + "-sg-e", [-length * 0.44, r_hous + 0.015, 0], [length * 0.44, r_hous + 0.015, 0])
    sg_w = self.guide(id_ + "-sg-w", [-length * 0.44, -r_hous - 0.015, 0], [length * 0.44, -r_hous - 0.015, 0])
    self.join(id_ + "-sguides", sg_e, sg_w)
    body_parts.append(self.seam(id_ + "-hdet", placed_h, id_ + "-sguides", width=0.005, depth=0.003, reach=0.06))

    # 3. Open-bore flanged bronze journal sleeve bushing (protrudes past housing faces)
    hb = body_len * 0.5 + _BUTT
    hf = hb + 0.010
    rc_fl = r_bush + 0.008
    bush_sec = [
        [br, -hf], [rc_fl, -hf], [rc_fl, -hb], [r_bush, -hb],
        [r_bush, hb], [rc_fl, hb], [rc_fl, hf], [br, hf],
    ]
    bush_parts.append(_open_ring(self, id_ + "-bush", bush_sec, sides=24, axis="x", sds=False))

    # 4. Optional top bronze lubricator cup seated at z = r_hous + _BUTT
    if oil_cup:
        cup_r = mx(0.014, br * 0.38)
        cup_h = cup_r * 2.3
        cup_sec = [[cup_r * 0.25, 0], [cup_r * 0.52, 0], [cup_r * 0.52, cup_h * 0.28],
                   [cup_r, cup_h * 0.36], [cup_r, cup_h * 0.88], [cup_r * 0.72, cup_h], [cup_r * 0.25, cup_h]]
        bush_parts.append(_open_ring(self, id_ + "-oil", cup_sec, sides=16,
                                     at=[0, 0, r_hous + _BUTT], axis="z", sds=True, crease=2.8, levels=1))

    # 5. Twin soleplate anchor studs + open-bore hex nuts (0% buried inside soleplate!)
    by_bolt = (ped_hw + hw) * 0.5
    st_r = mx(0.008, mn(0.014, sole_t * 0.42))
    for tag, sgn in (("n", 1), ("s", -1)):
        bolt_parts += _anchor_stud_nut(self, f"{id_}-bolt-{tag}", st_r, sole_t * 1.45,
                                       st_r * 1.95, st_r * 1.55, [0, by_bolt * sgn, ch * -1 + sole_t])

    grp = [
        _mat_or_raw(self, id_ + "-mbody", _join_many(self, id_ + "-jbody", body_parts), body_slot),
        _mat_or_raw(self, id_ + "-mbush", _join_many(self, id_ + "-jbush", bush_parts), bush_slot),
        _mat_or_raw(self, id_ + "-mbolt", _join_many(self, id_ + "-jbolt", bolt_parts), bolt_slot),
    ]
    asm = self.join(id_ + "-asm", *grp)
    pos = [0, 0, ch] if at is None else list(at)
    rot = [0, 0, PI * 0.5] if axis == "y" else ([0, -PI * 0.5, 0] if axis == "z" else None)
    return self.xf(id_, asm, t=pos, r=rot)


def bushed_sheave(self, id_, outer_r, width, bore_r, groove_r=0.024, spokes=6, at=None, axis="x",
                  sides=32, wheel_slot="iron", bush_slot="bronze"):
    """Open-bore grooved pulley, flywheel, or lead sheave with a pressed flanged bronze journal bushing (`bore_r`),
    post-FWN bolted cast hub, oval-tapered Chaikin SDS spokes (`0` pinched poles, `0%` buried overlap), and turned
    grooved rim so an axle of radius `bore_r - 0.0018` spins freely in MuJoCo."""
    L = lambda k, v: self.let(f"{id_}-{k}", v)
    hw = L("hw", width * 0.5)
    br = L("br", bore_r)
    r_bush = L("rbush", br + mx(0.007, br * 0.20))
    rim_in = L("rimin", mx(r_bush + 0.052, outer_r - groove_r * 2.05 - 0.026))
    r_hub = L("rhub", mn(rim_in - 0.028, mx(r_bush + 0.018, br * 1.88 + 0.016)))
    hub_hw = L("hubhw", hw * 1.05)

    # 1. Pressed flanged open-bore bronze sleeve bushing
    bf0 = hub_hw + _BUTT
    bf1 = bf0 + 0.005
    b_fl = r_bush + 0.006
    bush_sec = [[br, -bf1], [b_fl, -bf1], [b_fl, -bf0], [r_bush, -bf0],
                [r_bush, bf0], [b_fl, bf0], [b_fl, bf1], [br, bf1]]
    bush = _open_ring(self, id_ + "-bush", bush_sec, sides=24, sds=False)

    # 2. Cast open-bore hub outside bushing + post-FWN fastener rings on both faces (0x8007)
    hub_raw = _open_collar(self, id_ + "-hub0", r_hub, r_bush + _BUTT, hub_hw * 2.0, axis="z", sides=24, bevel=0.004)
    r_bc = (r_bush + r_hub) * 0.52
    g_top = self.outline(id_ + "-hgt", at=[0, 0, hub_hw + 0.012], shape="circle", radius=r_bc)
    g_bot = self.outline(id_ + "-hgb", at=[0, 0, hub_hw * -1 - 0.012], r=[PI, 0, 0], shape="circle", radius=r_bc)
    self.join(id_ + "-hguides", g_top, g_bot)
    hub = self.fasteners(id_ + "-hub", hub_raw, id_ + "-hguides", head="hex", radius=0.006, height=0.004,
                         count=max(4, int(spokes)), reach=0.05)

    # 3. Grooved all-quad off-axis rim
    fl = mn(0.012, hw * 0.24)
    g_root = mx(rim_in + 0.010, outer_r - groove_r * 1.42)
    rim_sec = [
        [rim_in, -hw], [outer_r - 0.004, -hw], [outer_r, -hw + 0.004], [outer_r, -hw + fl],
        [g_root, groove_r * -0.52], [g_root, groove_r * 0.52],
        [outer_r, hw - fl], [outer_r, hw - 0.004], [outer_r - 0.004, hw], [rim_in, hw],
    ]
    rim = _open_ring(self, id_ + "-rim", rim_sec, sides=sides, sds=False)

    # 4. Oval-tapered Chaikin SDS spokes swelling toward hub root (strictly seated between r_hub and rim_in)
    sp_x0 = r_hub + _BUTT
    sp_x1 = rim_in - _BUTT
    sp_xm = (sp_x0 + sp_x1) * 0.5
    ry0 = mn(hw * 0.52, (outer_r - br) * 0.13)
    rz0 = mn(hw * 0.64, ry0 * 1.25)

    def _oval_sec(ry_s, rz_s, z_val):
        return [[ry_s * math.cos(2.0 * math.pi * k / 12.0), rz_s * math.sin(2.0 * math.pi * k / 12.0), z_val]
                for k in range(12)]

    self.loft(id_ + "-spoke-z", sections=[
        _oval_sec(ry0 * 1.18, rz0 * 1.12, sp_x0),
        _oval_sec(ry0 * 0.88, rz0 * 0.86, sp_xm),
        _oval_sec(ry0 * 1.04, rz0 * 0.96, sp_x1),
    ], cap="quad")
    self.crease(id_ + "-spoke-cr", id_ + "-spoke-z", sharpness=2.8, min_angle=45)
    self.subdivide(id_ + "-spoke-sds", id_ + "-spoke-cr", levels=2, creaseMethod="chaikin", limit=1, limit_normals=1,
                   g2_fair=1, subdiv_normals=1, lodLevels=[2, 1, 0])
    self.xf(id_ + "-spoke-one", id_ + "-spoke-sds", r=[0, PI * 0.5, 0])
    sp_ring = self.ring(id_ + "-spokes", id_ + "-spoke-one", spokes, 2.0 * PI / spokes)

    w_join = self.join(id_ + "-wj", hub, rim, sp_ring)
    grp = [
        _mat_or_raw(self, id_ + "-mw", w_join, wheel_slot),
        _mat_or_raw(self, id_ + "-mb", bush, bush_slot),
    ]
    asm = self.join(id_ + "-asm", *grp)
    r = _AXIS_R[axis]
    if at is not None or r is not None:
        return self.xf(id_, asm, t=list(at) if at is not None else None, r=r)
    return asm


def clevis_fork(self, id_, gap, cheek_t, pin_r, arm_len, at=None, axis="z", with_pin=True,
                body_slot="iron", pin_slot="steel", bush_slot="bronze"):
    """Open-bore Chaikin SDS forked linkage clevis (`0` pinched poles) whose cross-pin axis lies at local `[0, 0, 0]`
    (along local X) with fork arms extending to `-arm_len` along local `axis` (default `'z'`)."""
    L = lambda k, v: self.let(f"{id_}-{k}", v)
    pr = L("pr", pin_r)
    br = L("br", mech_bore(pr))
    r_bush = L("rbush", br + mx(0.004, pr * 0.22))
    r_eye = L("reye", r_bush + mx(0.010, pr * 0.62))
    z_arm0 = L("zarm0", mx(arm_len * 0.68, r_eye * 1.45) * -1)
    z_base0 = L("zbase0", arm_len * -1)
    x_in = L("xin", gap * 0.5)
    x_out = L("xout", gap * 0.5 + cheek_t)

    # 20-pt CCW eye-and-tang cheek profile in (y, z) + matching 20-pt bore hole (20 distinct CCW vertices)
    c_out = [[r_eye * (-0.85 + 1.70 * k / 4.0), z_arm0] for k in range(5)]
    for k in range(1, 4):
        u = k / 3.0
        c_out.append([r_eye * (0.85 + 0.15 * u), z_arm0 * (1.0 - u)])
    for k in range(1, 9):
        ang = math.pi * k / 9.0
        c_out.append([r_eye * math.cos(ang), r_eye * math.sin(ang)])
    x8 = r_eye * math.cos(8.0 * math.pi / 9.0)
    z8 = r_eye * math.sin(8.0 * math.pi / 9.0)
    for k in range(1, 5):
        u = k / 5.0
        c_out.append([x8 * (1.0 - u) - r_eye * 0.85 * u, z8 * (1.0 - u) + z_arm0 * u])

    rh_c = _bore_cage_r(r_bush + _BUTT, 20)
    c_hole = [[rh_c * math.cos(-0.75 * math.pi + 2.0 * math.pi * k / 20.0),
               rh_c * math.sin(-0.75 * math.pi + 2.0 * math.pi * k / 20.0)] for k in range(20)]
    raw_ck = self.pierced_extrude(id_ + "-ckraw", c_out, [c_hole], cheek_t, cap="quad")
    cut_ck = self.loop_cut(id_ + "-ckcut", raw_ck, margin=0.004, selection={"direction": [0, 0, 1], "dirAngle": 20})
    cr_ck = self.crease(id_ + "-ckcr", cut_ck, sharpness=2.4, min_angle=32)
    sds_ck = self.subdivide(id_ + "-cksds", cr_ck, levels=2, creaseMethod="chaikin", limit=1, limit_normals=1,
                            g2_fair=1, subdiv_normals=1, lodLevels=[2, 1, 0])

    body_parts, bush_parts, pin_parts = [], [], []
    for tag, x0, xc in (("e", x_in, x_in + cheek_t * 0.5), ("w", x_out * -1, (x_in + cheek_t * 0.5) * -1)):
        body_parts.append(_extrude_yz_to_x(self, f"{id_}-ck-{tag}", sds_ck, x0))
        bush_parts.append(_open_collar(self, f"{id_}-eb-{tag}", r_bush, br, cheek_t + 0.002,
                                       at=[xc, 0, 0], axis="x", sides=20, bevel=0.0015))

    yoke_h = mx(0.016, z_arm0 - z_base0 - _BUTT)
    body_parts.append(_fwn_box(self, id_ + "-yoke", [x_out * 2.0, r_eye * 1.55, yoke_h],
                               at=[0, 0, z_base0 + yoke_h * 0.5], bevel=0.003))

    if with_pin:
        nut_t = mx(0.010, pr * 0.95)
        head_t = mx(0.008, pr * 0.72)
        pin_len = (x_out + nut_t + 0.016) * 2.0
        pin_parts.append(_fwn_rod(self, id_ + "-pin", pr, pin_len, at=[0, 0, 0], axis="x", sides=18))
        pin_parts.append(_open_collar(self, id_ + "-phead", pr * 1.68, mech_bore(pr), head_t,
                                      at=[(x_out + _BUTT + head_t * 0.5) * -1, 0, 0], axis="x", sides=18, bevel=0.002))
        pin_parts.append(_open_nut(self, id_ + "-pnut", pr * 1.82, br, nut_t,
                                   at=[x_out + _BUTT + nut_t * 0.5, 0, 0], axis="x"))
        cot_r = mx(0.0022, pr * 0.16)
        cot_h = pr * 1.15
        cot_x = x_out + nut_t + cot_r + 0.003
        for ctag, csz in (("top", 1), ("bot", -1)):
            pin_parts.append(_fwn_rod(self, f"{id_}-cot-{ctag}", cot_r, cot_h,
                                      at=[cot_x, 0, (pr + _BUTT + cot_h * 0.5) * csz], axis="z", sides=10))

    grp = [
        _mat_or_raw(self, id_ + "-mbody", _join_many(self, id_ + "-jbody", body_parts), body_slot),
        _mat_or_raw(self, id_ + "-mbush", _join_many(self, id_ + "-jbush", bush_parts), bush_slot),
    ]
    if pin_parts:
        grp.append(_mat_or_raw(self, id_ + "-mpin", _join_many(self, id_ + "-jpin", pin_parts), pin_slot))
    asm = self.join(id_ + "-asm", *grp)
    r = _AXIS_R[axis]
    if at is not None or r is not None:
        return self.xf(id_, asm, t=list(at) if at is not None else None, r=r)
    return asm


def stuffing_box(self, id_, stem_r, body_r=None, height=0.18, at=None, axis="z",
                 body_slot="patina", gland_slot="bronze", bolt_slot="steel"):
    """Open-bore cast stuffing box & bronze two-stud packing gland (`0` pinched poles, `0` MuJoCo clips) seated at
    `at` and extending `+height` along `axis` around a spindle of radius `stem_r` (`bore = mech_bore(stem_r)`)."""
    L = lambda k, v: self.let(f"{id_}-{k}", v)
    sr = L("sr", stem_r)
    br = L("br", mech_bore(sr))
    rmid = L("rmid", (mx(br * 1.92, 0.056) if body_r is None else body_r))
    rfl = L("rfl", rmid * 1.42)
    rtop = L("rtop", rmid * 1.18)
    zbar = L("zbar", height * 0.66)
    fl_h = L("flh", zbar * 0.24)
    rc = _bore_cage_r(br, 20)

    # 1. Chaikin SDS revolved stuffing barrel (off-axis closed ring -> 100% valence-4 quads, 0 poles)
    bar_sec = [
        [rc, 0], [rfl, 0], [rfl, fl_h], [rmid, fl_h * 1.25],
        [rmid, zbar * 0.78], [rtop, zbar * 0.84], [rtop, zbar], [rc, zbar],
    ]
    bar_sds = _open_ring(self, id_ + "-bar0", bar_sec, sides=20, sds=True, crease=2.8, levels=2)
    fg = self.outline(id_ + "-fguide", at=[0, 0, fl_h + 0.015], shape="circle", radius=(rmid + rfl) * 0.52)
    bar_det = self.fasteners(id_ + "-bar", bar_sds, fg, head="hex", radius=0.008, height=0.005, count=6, reach=0.05)
    body_parts = [bar_det]

    # Cast stud lugs on +-Y of upper barrel
    stud_r = L("studr", mx(0.006, sr * 0.34))
    eye_r = L("eyer", stud_r * 2.25)
    stud_y = L("study", rtop + eye_r + 0.008)
    lug_h = L("lugh", zbar * 0.22)
    for tag, sgn in (("n", 1), ("s", -1)):
        lug_len = mx(0.012, stud_y + eye_r * 0.9 - rtop)
        lug_y = rtop + _BUTT + lug_len * 0.5
        body_parts.append(_fwn_box(self, f"{id_}-lug-{tag}", [eye_r * 1.8, lug_len, lug_h],
                                   at=[0, lug_y * sgn, zbar - lug_h * 0.5], bevel=0.0025))

    # 2. Open-bore bronze gland follower collar + open-bore stud eye lugs
    z_fol0 = zbar + _BUTT
    fol_h = mx(0.024, height - z_fol0)
    ear_t = fol_h * 0.46
    r_col = rmid * 0.92
    col_sec = [
        [br, z_fol0], [r_col * 1.12, z_fol0], [r_col * 1.12, z_fol0 + ear_t],
        [r_col, z_fol0 + ear_t], [r_col * 0.86, z_fol0 + fol_h], [br, z_fol0 + fol_h],
    ]
    gland_parts = [_open_ring(self, id_ + "-fcol", col_sec, sides=22, sds=False)]
    bolt_parts = []
    stud_len = fol_h + stud_r * 2.4
    nut_h = stud_r * 1.65
    br_span = mx(0.006, (stud_y - eye_r) - (r_col * 1.12) - 2.0 * _BUTT)
    br_ymid = ((r_col * 1.12 + _BUTT) + (stud_y - eye_r - _BUTT)) * 0.5
    for tag, sgn in (("n", 1), ("s", -1)):
        gland_parts.append(_open_collar(self, f"{id_}-feye-{tag}", eye_r, mech_bore(stud_r), ear_t,
                                        at=[0, stud_y * sgn, z_fol0 + ear_t * 0.5], axis="z", sides=18, bevel=0.002))
        gland_parts.append(_fwn_box(self, f"{id_}-fbr-{tag}", [eye_r * 1.35, br_span, ear_t * 0.88],
                                    at=[0, br_ymid * sgn, z_fol0 + ear_t * 0.5], bevel=0.002))
        bolt_parts.append(_fwn_rod(self, f"{id_}-gst-{tag}", stud_r, stud_len,
                                   at=[0, stud_y * sgn, zbar + _BUTT + stud_len * 0.5], axis="z", sides=14))
        bolt_parts.append(_open_nut(self, f"{id_}-gnt-{tag}", stud_r * 1.95, mech_bore(stud_r), nut_h,
                                    at=[0, stud_y * sgn, z_fol0 + ear_t + _BUTT + nut_h * 0.5], axis="z"))

    grp = [
        _mat_or_raw(self, id_ + "-mbody", _join_many(self, id_ + "-jbody", body_parts), body_slot),
        _mat_or_raw(self, id_ + "-mgland", _join_many(self, id_ + "-jgland", gland_parts), gland_slot),
        _mat_or_raw(self, id_ + "-mbolt", _join_many(self, id_ + "-jbolt", bolt_parts), bolt_slot),
    ]
    asm = self.join(id_ + "-asm", *grp)
    r = _AXIS_R[axis]
    if at is not None or r is not None:
        return self.xf(id_, asm, t=list(at) if at is not None else None, r=r)
    return asm


def arched_yoke(self, id_, span, rise, bore_r, foot_w=0.11, thickness=0.08, at=None, facing="x",
                body_slot="iron", bush_slot="bronze", bolt_slot="steel"):
    """Twin-foot cast-iron arched spindle bridge (`0` pinched poles, `0` MuJoCo clips) standing from `z = 0` to `rise`
    across `span`, carrying an open-bore central head boss and flanged bronze guide bushing (`bore_r` along +Z)."""
    L = lambda k, v: self.let(f"{id_}-{k}", v)
    br = L("br", bore_r)
    r_bush = L("rbush", br + mx(0.007, br * 0.24))
    r_head = L("rhead", r_bush + mx(0.026, thickness * 0.42))
    head_h = L("headh", mx(0.075, rise * 0.24))
    foot_t = L("foott", mx(0.024, rise * 0.065))
    st_r = L("str", mx(0.007, mn(0.013, foot_t * 0.36)))
    bolt_y = L("bolty", thickness * 0.48 + st_r * 1.95 + 0.016)
    foot_d = L("footd", mx(bolt_y * 2.55, foot_w * 2.15))
    hx = L("hx", span * 0.5)

    body_parts, bolt_parts = [], []
    # 1. Open-bore Chaikin SDS central head boss
    rc_h = _bore_cage_r(r_bush + _BUTT, 20)
    z_h0 = rise - head_h
    h_sec = [
        [rc_h, z_h0], [r_head * 0.88, z_h0], [r_head, z_h0 + head_h * 0.18],
        [r_head, rise - head_h * 0.14], [r_head * 0.90, rise], [rc_h, rise],
    ]
    body_parts.append(_open_ring(self, id_ + "-head", h_sec, sides=20, sds=True, crease=2.8, levels=2))

    # 2. Flanged open-bore bronze guide bushing
    b_top = rise + _BUTT + 0.014
    b_sec = [[br, z_h0 - 0.004], [r_bush, z_h0 - 0.004], [r_bush, rise + _BUTT],
             [r_bush + 0.012, rise + _BUTT], [r_bush + 0.012, b_top], [br, b_top]]
    bush = _open_ring(self, id_ + "-bush", b_sec, sides=22, sds=False)

    # 3. Bolted feet + curved all-quad Chaikin SDS arch legs flush-butted to feet & head boss
    for tag, sgn in (("e", 1), ("w", -1)):
        fx = hx * sgn
        body_parts.append(_fwn_box(self, f"{id_}-ft-{tag}", [foot_w, foot_d, foot_t],
                                   at=[fx, 0, foot_t * 0.5], bevel=0.004))
        for btag, bsy in (("n", 1), ("s", -1)):
            bolt_parts += _anchor_stud_nut(self, f"{id_}-fb-{tag}-{btag}", st_r, foot_t * 1.4,
                                           st_r * 1.95, st_r * 1.55, [fx, bolt_y * bsy, foot_t])
        x_s0 = fx
        x_s1 = (r_head + _BUTT + foot_w * 0.248) * sgn
        z_s0 = foot_t + _BUTT
        z_s1 = rise - head_h * 0.48
        secs = []
        for idx, u in enumerate((0.0, 0.25, 0.55, 0.82, 1.0)):
            su = math.sin(u * math.pi * 0.5)
            xc = x_s0 * (1.0 - su) + x_s1 * su
            zc = z_s0 + (z_s1 - z_s0) * (u * 0.35 + su * 0.65)
            rx_s = foot_w * (0.36 - 0.10 * u)
            ry_s = thickness * (0.48 - 0.06 * u)
            secs.append([[xc + rx_s * math.cos(2.0 * math.pi * k / 12.0),
                          ry_s * math.sin(2.0 * math.pi * k / 12.0), zc] for k in range(12)])
        self.loft(f"{id_}-leg0-{tag}", sections=secs, cap="quad")
        self.crease(f"{id_}-lcr-{tag}", f"{id_}-leg0-{tag}", sharpness=2.6, min_angle=42)
        body_parts.append(self.subdivide(f"{id_}-leg-{tag}", f"{id_}-lcr-{tag}", levels=2, creaseMethod="chaikin",
                                         limit=1, limit_normals=1, g2_fair=1, subdiv_normals=1, lodLevels=[2, 1, 0]))

    grp = [
        _mat_or_raw(self, id_ + "-mbody", _join_many(self, id_ + "-jbody", body_parts), body_slot),
        _mat_or_raw(self, id_ + "-mbush", bush, bush_slot),
        _mat_or_raw(self, id_ + "-mbolt", _join_many(self, id_ + "-jbolt", bolt_parts), bolt_slot),
    ]
    asm = self.join(id_ + "-asm", *grp)
    rot = [0, 0, PI * 0.5] if facing == "y" else None
    if at is not None or rot is not None:
        return self.xf(id_, asm, t=list(at) if at is not None else None, r=rot)
    return asm


def pierced_cheek(self, id_, width_y, height_z, thickness, bore_z, bore_r, ribs=True, at=None, side=1,
                  body_slot="patina", bush_slot="bronze", bolt_slot="steel"):
    """Open-bore Chaikin SDS cast side frame / A-standard (`0` pinched poles, `0` MuJoCo buried overlaps) whose
    inner face sits at `x = at[0]` and outer face at `x = at[0] + side * thickness`, standing from `z = at[2]` to
    `at[2] + height_z` with an outboard-flanged bronze journal bushing (`bore_r`) at `z = at[2] + bore_z`."""
    sgn = 1 if side >= 0 else -1
    L = lambda k, v: self.let(f"{id_}-{k}", v)
    br = L("br", bore_r)
    r_bush = L("rbush", br + mx(0.008, br * 0.22))
    hw = L("hw", width_y * 0.5)
    rc_top = L("rctop", mn(hw * 0.72, mx(r_bush + 0.045, height_z - bore_z)))
    z_top_c = L("ztopc", height_z - rc_top)

    # 24-pt CCW outer profile in (y, z) + matching 24-pt bore hole around (0, bore_z)
    out_24 = [[hw * (-1.0 + k / 3.0), 0] for k in range(7)]
    for k in range(1, 6):
        u = k / 5.0
        out_24.append([hw * (1.0 - u) + rc_top * u, z_top_c * u])
    for k in range(1, 8):
        ang = math.pi * k / 8.0
        out_24.append([rc_top * math.cos(ang), z_top_c + rc_top * math.sin(ang)])
    xc7 = rc_top * math.cos(7.0 * math.pi / 8.0)
    zc7 = z_top_c + rc_top * math.sin(7.0 * math.pi / 8.0)
    for k in range(1, 6):
        u = k / 6.0
        out_24.append([xc7 * (1.0 - u) - hw * u, zc7 * (1.0 - u)])

    rh_c = _bore_cage_r(r_bush + _BUTT, 24)
    hole_24 = [[rh_c * math.cos(-0.75 * math.pi + 2.0 * math.pi * k / 24.0),
                bore_z + rh_c * math.sin(-0.75 * math.pi + 2.0 * math.pi * k / 24.0)] for k in range(24)]
    raw_c = self.pierced_extrude(id_ + "-craw", out_24, [hole_24], thickness, cap="quad")
    cut_c = self.loop_cut(id_ + "-ccut", raw_c, margin=0.010, selection={"direction": [0, 0, 1], "dirAngle": 20})
    cr_c = self.crease(id_ + "-ccr", cut_c, sharpness=2.3, min_angle=30)
    sds_c = self.subdivide(id_ + "-csds", cr_c, levels=2, creaseMethod="chaikin", limit=1, limit_normals=1,
                           g2_fair=1, subdiv_normals=1, lodLevels=[2, 1, 0])
    x0 = 0 if sgn > 0 else thickness * -1
    plate = _extrude_yz_to_x(self, id_ + "-cpos", sds_c, x0)

    # Post-SDS cored recess groove on outer face (0x8007)
    x_out = thickness * sgn
    pg = self.outline(id_ + "-pguide", at=[x_out + 0.012 * sgn, 0, bore_z * 0.48],
                      r=[0, PI * 0.5 * sgn, 0], shape="rect",
                      size=[mx(0.06, bore_z * 0.42), mx(0.08, width_y * 0.44)], corner=0.02)
    body_parts = [self.seam(id_ + "-cdet", plate, pg, width=0.008, depth=0.004, reach=0.05)]

    # Outboard-flanged bronze journal bushing (never protrudes inboard past x = 0!)
    if sgn > 0:
        b_sec = [[br, 0], [r_bush, 0], [r_bush, thickness + _BUTT],
                 [r_bush + 0.014, thickness + _BUTT], [r_bush + 0.014, thickness + 0.016], [br, thickness + 0.016]]
    else:
        b_sec = [[br, -thickness - 0.016], [r_bush + 0.014, -thickness - 0.016], [r_bush + 0.014, -thickness - _BUTT],
                 [r_bush, -thickness - _BUTT], [r_bush, 0], [br, 0]]
    bush = _open_ring(self, id_ + "-bush", b_sec, sides=24, at=[0, 0, bore_z], axis="x", sds=False)

    # Outboard flush-butted foot flange + anchor bolts + optional diagonal ribs (0% buried inside cheek!)
    fl_w = mx(0.036, thickness * 0.92)
    fl_t = mx(0.018, height_z * 0.032)
    x_fl = x_out + (_BUTT + fl_w * 0.5) * sgn
    body_parts.append(_fwn_box(self, id_ + "-fl", [fl_w, width_y * 0.94, fl_t],
                               at=[x_fl, 0, fl_t * 0.5], bevel=0.003))
    bolt_parts = []
    st_r = mx(0.007, mn(0.013, fl_t * 0.40))
    for btag, bsy in (("n", 1), ("s", -1)):
        bolt_parts += _anchor_stud_nut(self, f"{id_}-fb-{btag}", st_r, fl_t * 1.45,
                                       st_r * 1.95, st_r * 1.55, [x_fl, hw * 0.68 * bsy, fl_t])
    if ribs:
        rib_t = mx(0.016, thickness * 0.40)
        rib_w = mx(0.026, width_y * 0.075)
        rib_len = mx(0.08, bore_z - fl_t - r_bush - 0.032)
        x_rib = x_out + (_BUTT + rib_t * 0.5) * sgn
        z_rib = fl_t + _BUTT + rib_len * (0.5 * math.cos(0.22)) + rib_w * (0.5 * math.sin(0.22))
        for rtag, rsy in (("n", 1), ("s", -1)):
            raw_r = _fwn_box(self, f"{id_}-r0-{rtag}", [rib_t, rib_w, rib_len], bevel=0.003)
            body_parts.append(self.xf(f"{id_}-rib-{rtag}", raw_r,
                                      t=[x_rib, hw * 0.36 * rsy, z_rib], r=[0.22 * rsy, 0, 0]))

    grp = [
        _mat_or_raw(self, id_ + "-mbody", _join_many(self, id_ + "-jbody", body_parts), body_slot),
        _mat_or_raw(self, id_ + "-mbush", bush, bush_slot),
        _mat_or_raw(self, id_ + "-mbolt", _join_many(self, id_ + "-jbolt", bolt_parts), bolt_slot),
    ]
    asm = self.join(id_ + "-asm", *grp)
    return self.xf(id_, asm, t=list(at)) if at is not None else asm


Graph.plummer_block = plummer_block
Graph.bushed_sheave = bushed_sheave
Graph.clevis_fork = clevis_fork
Graph.stuffing_box = stuffing_box
Graph.arched_yoke = arched_yoke
Graph.pierced_cheek = pierced_cheek



