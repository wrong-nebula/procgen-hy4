"""Architectural shell & relative-anchor helpers for kit builders: symbolic face anchoring (`box_on`,
`clamp_to_wall`), hipped/vented roofs (`hipped_roof`), framed sash windows (`sash_window`),
external braced stairs (`external_stair`), dressed corner quoins (`corner_quoins`), masonry window
surrounds (`window_surround`), louvred roof ventilator lanterns (`louvred_lantern`), corbelled brick chimneys
(`corbelled_chimney`), and stile-and-rail panelled doors (`panelled_door`).

Same-material joints butt with a 0.5 mm (`0.0005 m`) clearance so `pgm audit` (3 mm contact tolerance)
treats them as 1 connected structure while `pgm check --intersections` sees 0 self-intersections even
after `bake` merges same-material shells.

Attached to `kitlib.Graph` when `kitlib` is imported:

    from kitlib import Graph, C, clamp_to_wall
    g.hipped_roof("roof", C("width"), C("depth"), rise=1.35, eaves=0.36, at=[0, 0, C("height")])
    g.corner_quoins("quoins", C("width"), C("depth"), z_base=0.35, height=C("height") - 0.35)
    g.window_surround("win-sur", 1.8, 1.5, facing="-y", at=[0, -C("depth") * 0.5, 2.0])
    g.sash_window("win-front", 1.8, 1.5, sill_h=0, lintel_h=0, facing="-y", at=[0, -C("depth") * 0.5, 2.161])
    g.panelled_door("door", 0.95, 2.1, 0.056, at=[0, -C("depth") * 0.5, 0.35], facing="-y")
    g.corbelled_chimney("chimney", 0.68, 0.52, z_base=C("height"), height=1.85, pots=2, at=[-1.2, 0.8, 0])
    g.louvred_lantern("lantern", 1.3, 0.58, 0.48, z_base=C("height") + 1.35)
    g.external_stair("stair", rise=3.1, width=0.95, landing_len=1.2, facing="+x", at=[C("width") * 0.5, -0.4, 0])
"""
from kitlib import Graph, X, PI, mx, mn, ceil, floor, _facing, _num, _v, _plate

_BUTT = 0.0005  # 0.5 mm: connected in pgm audit (<= 3 mm), disjoint in pgm check --intersections (> 0 mm)


def _join_many(g, id_, parts):
    """Join arbitrarily many node ids while staying well under pgm's per-join fan-in cap."""
    cur = [p for p in parts if p is not None]
    assert cur, f"_join_many({id_!r}): empty parts list"
    if len(cur) == 1:
        return cur[0]
    batch = 0
    while len(cur) > 32:
        nxt = []
        for i in range(0, len(cur), 32):
            chunk = cur[i:i + 32]
            if len(chunk) == 1:
                nxt.append(chunk[0])
            else:
                nxt.append(g.join(f"{id_}-b{batch}", *chunk))
                batch += 1
        cur = nxt
    return g.join(id_, *cur)


def _prism_xy(g, id_, poly_xy, z0, z1):
    """Closed outward-oriented vertical prism from z0 to z1 over a CCW XY polygon."""
    n = len(poly_xy)
    verts = [[x, y, z0] for x, y in poly_xy] + [[x, y, z1] for x, y in poly_xy]
    faces = [list(reversed(range(n))), list(range(n, 2 * n))]
    for i in range(n):
        j = (i + 1) % n
        faces.append([i, j, n + j, n + i])
    return g.n(id_, "surface", vertices=verts, faces=faces)


def _prism_xz(g, id_, poly_xz, y0, y1):
    """Closed outward-oriented prism from y0 to y1 (y1 > y0) over a CCW XZ polygon."""
    n = len(poly_xz)
    verts = [[x, y0, z] for x, z in poly_xz] + [[x, y1, z] for x, z in poly_xz]
    faces = [list(range(n)), list(reversed(range(n, 2 * n)))]
    for i in range(n):
        j = (i + 1) % n
        faces.append([j, i, n + i, n + j])
    return g.n(id_, "surface", vertices=verts, faces=faces)


def _bevel_box(g, id_, size, at, bevel=0.010):
    raw = g.box(id_ + "-b", size, at)
    if not bevel:
        return raw
    bw = mn(bevel, mn(size[0], mn(size[1], size[2])) * 0.35)
    return g.n(id_, "boxBevel", [raw], width=bw)


def clamp_to_wall(coord, half_wall, half_part=0.0, margin=0.04):
    """Clamp a horizontal placement coordinate `coord` inside `[-half_wall + half_part + margin,
    +half_wall - half_part - margin]` using kitlib `mx`/`mn` expressions so brackets, shoes, pilasters,
    and signboards never overhang wall corners across parametric control sweeps.
    Example: bx = clamp_to_wall(x, C("width") * 0.5, half_part=0.06, margin=0.08)"""
    lim = mx(0.0, X(half_wall) - half_part - margin)
    return mx(-lim, mn(lim, coord))


def box_on(self, id_, parent_center, parent_size, face, size, u=0.0, v=0.0, gap=0.0005, clamp_margin=None, slot=None):
    """Place a box flush against `face` ('-y', '+y', '-x', '+x', '+z', '-z') of a parent box
    `(parent_center, parent_size)` with `gap` (default 0.5 mm: seated within `pgm audit`'s 3 mm tolerance
    and non-self-intersecting even if parent shares the same material under `bake`).

    `size` is in world `[sx, sy, sz]` metres. `(u, v)` offsets the box along the two axes tangent to `face`:
      - face '-y' or '+y': u along X, v along Z
      - face '-x' or '+x': u along Y, v along Z
      - face '-z' or '+z': u along X, v along Y
    When `clamp_margin` is set (e.g. `0.05`), `u` is clamped via `clamp_to_wall` so the child never overhangs
    the parent's corner. Emits derived `let` variables when coordinates depend on controls.
    Example: g.box_on("bracket-0", [0, 0, 1.6], [C("width"), C("depth"), 3.2], "-y", [0.10, 0.45, 0.35], u=-1.8, v=1.2, clamp_margin=0.08, slot="weathered-iron")"""
    pc = [X(c) for c in parent_center]
    ps = [X(s) for s in parent_size]
    sz = [X(s) for s in size]
    ax_map = {
        "-y": (1, -1, 0, 2),
        "+y": (1, +1, 0, 2),
        "-x": (0, -1, 1, 2),
        "+x": (0, +1, 1, 2),
        "-z": (2, -1, 0, 1),
        "+z": (2, +1, 0, 1),
    }
    assert face in ax_map, f"box_on: face must be one of {tuple(ax_map)}, got {face!r}"
    n_ax, sign, u_ax, v_ax = ax_map[face]
    u_val = clamp_to_wall(u, ps[u_ax] * 0.5, sz[u_ax] * 0.5, clamp_margin) if clamp_margin is not None else X(u)
    v_val = X(v)
    pos = [None, None, None]
    pos[n_ax] = self.let(f"{id_}.n", pc[n_ax] + sign * (ps[n_ax] * 0.5 + sz[n_ax] * 0.5 + gap))
    pos[u_ax] = self.let(f"{id_}.u", pc[u_ax] + u_val)
    pos[v_ax] = self.let(f"{id_}.v", pc[v_ax] + v_val)
    out = self.box(id_ + ("-raw" if slot else ""), sz, pos)
    return self.mat(id_, out, slot) if slot else out


def hipped_roof(self, id_, width, depth, rise=1.25, eaves=0.35, fascia_h=0.14, ridge_vent=True,
                bracket_count=4, roof_slot="slate", fascia_slot="painted-wood", trim_slot="weathered-iron",
                at=(0, 0, 0)):
    """A closed manifold hipped roof with overhanging soffit/fascia, wall-clamped eaves brackets,
    ridge roll, and optional louvred ridge ventilator. Metres, Z-up; `at` is the wall-plate top centre
    `[x, y, wall_top_z]`. `width`, `depth`, and `rise` accept numbers or kitlib `X` control expressions.
    Example: g.hipped_roof("roof", C("width"), C("depth"), rise=1.35, eaves=0.36, at=[0, 0, C("height")])"""
    W, D, R = X(width), X(depth), X(rise)
    hw_wall = self.let(f"{id_}.hww", W * 0.5)
    hd_wall = self.let(f"{id_}.hdw", D * 0.5)
    hw = self.let(f"{id_}.hw", hw_wall + eaves)
    hd = self.let(f"{id_}.hd", hd_wall + eaves)
    along_x = True
    if _num(_v(width)) and _num(_v(depth)):
        along_x = float(_v(width)) >= float(_v(depth))

    parts = []
    # 1. Fascia / soffit plate seated 0.5 mm above z=0 (z = 0.0005 .. fascia_h)
    fascia = self.box(f"{id_}-fascia-b", [hw * 2.0, hd * 2.0, fascia_h - _BUTT], [0, 0, (fascia_h + _BUTT) * 0.5])
    parts.append(self.mat(f"{id_}-fascia", fascia, fascia_slot) if fascia_slot else fascia)

    # 2. Closed hipped roof prism seated 0.5 mm above fascia top
    z0 = fascia_h + _BUTT
    z1 = self.let(f"{id_}.ztop", z0 + R)
    if along_x:
        rx = self.let(f"{id_}.rx", mx(0.20, hw - hd))
        verts = [
            [-hw, -hd, z0], [hw, -hd, z0], [hw, hd, z0], [-hw, hd, z0],
            [-rx, 0.0, z1], [rx, 0.0, z1],
        ]
    else:
        ry = self.let(f"{id_}.ry", mx(0.20, hd - hw))
        verts = [
            [-hw, -hd, z0], [hw, -hd, z0], [hw, hd, z0], [-hw, hd, z0],
            [0.0, -ry, z1], [0.0, ry, z1],
        ]
    faces = (
        [[0, 3, 2, 1], [0, 1, 5, 4], [2, 3, 4, 5], [1, 2, 5], [3, 0, 4]]
        if along_x else
        [[0, 3, 2, 1], [1, 2, 5, 4], [3, 0, 4, 5], [0, 1, 4], [2, 3, 5]]
    )
    self.n(f"{id_}-prism", "surface", vertices=verts, faces=faces)
    parts.append(self.mat(f"{id_}-slopes", f"{id_}-prism", roof_slot) if roof_slot else f"{id_}-prism")

    # 3. Optional ridge ventilator or ridge cresting seated 0.5 mm above apex
    if ridge_vent:
        vw = mn(1.5, mx(0.65, W * 0.24)) if along_x else 0.44
        vd = 0.44 if along_x else mn(1.5, mx(0.65, D * 0.24))
        vh = 0.30
        curb_h = 0.14
        # Curb straddles the ridge cleanly: sits at z = z1 + _BUTT .. z1 + curb_h + vh
        vent_body = self.box(f"{id_}-vent-b", [vw, vd, vh], [0, 0, z1 + _BUTT + vh * 0.5])
        vent_cap = self.box(f"{id_}-vent-c", [vw + 0.14, vd + 0.14, 0.06], [0, 0, z1 + _BUTT + vh + _BUTT + 0.03])
        parts.append(self.mat(f"{id_}-vent-body", vent_body, fascia_slot) if fascia_slot else vent_body)
        parts.append(self.mat(f"{id_}-vent-cap", vent_cap, trim_slot) if trim_slot else vent_cap)
    else:
        rc_sz = [rx * 2.0, 0.12, 0.08] if along_x else [0.12, ry * 2.0, 0.08]
        crest = self.box(f"{id_}-crest-b", rc_sz, [0, 0, z1 + _BUTT + 0.04])
        parts.append(self.mat(f"{id_}-crest", crest, trim_slot) if trim_slot else crest)

    # 4. Wall-clamped 1-piece corbelled eaves brackets seated 0.5 mm outside wall face and 0.5 mm under soffit
    if bracket_count and bracket_count >= 2 and eaves >= 0.16:
        bh = min(0.28, eaves * 0.82)
        bd = eaves * 0.78
        bt = 0.06
        arm_h = 0.06
        shoe_d = 0.05
        for side, sy in (("f", -1), ("b", 1)):
            for k in range(bracket_count):
                frac = -0.78 + 1.56 * (k / max(1, bracket_count - 1))
                bx = clamp_to_wall(hw_wall * frac, hw_wall, bt * 0.5, 0.12)
                y0 = sy * (hd_wall + _BUTT)
                y1 = sy * (hd_wall + _BUTT + shoe_d)
                y2 = sy * (hd_wall + _BUTT + bd)
                zt = -_BUTT
                zm = -_BUTT - arm_h
                zb = -bh
                # Closed 6-corner knee-bracket profile in YZ extruded across X=[bx-bt/2 .. bx+bt/2]
                prof = [
                    (y0, zt), (y2, zt), (y2, zm),
                    (y1, zb), (y0, zb),
                ]
                verts = [[bx - bt * 0.5, py, pz] for py, pz in prof] + [[bx + bt * 0.5, py, pz] for py, pz in prof]
                n_p = len(prof)
                # Ensure outward winding for both sy=-1 and sy=+1
                f_side_a = list(range(n_p - 1, -1, -1)) if sy > 0 else list(range(n_p))
                f_side_b = [n_p + i for i in (range(n_p) if sy > 0 else range(n_p - 1, -1, -1))]
                b_faces = [f_side_a, f_side_b]
                for i in range(n_p):
                    j = (i + 1) % n_p
                    quad = [i, j, n_p + j, n_p + i] if sy > 0 else [j, i, n_p + i, n_p + j]
                    b_faces.append(quad)
                brk = self.n(f"{id_}-brk-{side}{k}-s", "surface", vertices=verts, faces=b_faces)
                parts.append(self.mat(f"{id_}-brk-{side}{k}", brk, trim_slot) if trim_slot else brk)

    joined = self.join(f"{id_}-local", *parts)
    return self.xf(id_, joined, t=list(at))


def sash_window(self, id_, width, height, wall_depth=0.28, mullions=2, transoms=2, raised=0.0,
                sill_h=0.09, sill_proj=0.07, lintel_h=0.16, frame_slot="painted-wood", glass_slot="glass",
                stone_slot="limestone", facing="-y", at=(0, 0, 0)):
    """A complete framed sash window unit with optional projecting stone sill (`sill_h=0` omits it for nesting
    inside `window_surround`), lintel head (`lintel_h=0` omits it), outer jamb box, upper & lower meeting sash
    frames (`raised` slides the lower sash upward), glazing muntin bars butted cleanly with 0 self-intersections,
    and `glass` panes.

    Local frame before placement: sill underside (or outer frame base when `sill_h=0`) sits on `z = 0`, outward
    face sits proud of `y = 0` into `+y` (rotated by `facing`, default `'-y'`, placed at `at` on the wall face).
    Example: g.sash_window("win-f", 1.8, 1.5, mullions=3, transoms=2, facing="-y", at=[0, -1.8, 3.2])"""
    W, H = X(width), X(height)
    sh = float(sill_h or 0.0)
    fw = 0.06
    fd = min(wall_depth, 0.14)
    yc = _BUTT + fd * 0.5
    parts = []

    # Optional stone sill & lintel head (0.5 mm outside wall face y=0)
    if sh > 0.0:
        sill_d = fd + sill_proj
        sill = self.box(f"{id_}-sill-b", [W + 0.14, sill_d, sh],
                        [0, _BUTT + sill_d * 0.5, sh * 0.5])
        parts.append(self.mat(f"{id_}-sill", sill, stone_slot) if stone_slot else sill)
        z_bot = sh + _BUTT
    else:
        z_bot = 0.0
    if lintel_h and lintel_h > 0:
        ld = fd + 0.03
        lintel = self.box(f"{id_}-lintel-b", [W + 0.18, ld, lintel_h],
                          [0, _BUTT + ld * 0.5, z_bot + H + _BUTT + lintel_h * 0.5])
        parts.append(self.mat(f"{id_}-lintel", lintel, stone_slot) if stone_slot else lintel)

    # Outer timber box frame: stiles full height, head & bottom rail butted between stiles!
    inner_w = W - 2.0 * (fw + _BUTT)
    jamb_l = self.box(f"{id_}-jl", [fw, fd, H], [-W * 0.5 + fw * 0.5, yc, z_bot + H * 0.5])
    jamb_r = self.box(f"{id_}-jr", [fw, fd, H], [W * 0.5 - fw * 0.5, yc, z_bot + H * 0.5])
    head_f = self.box(f"{id_}-hf", [inner_w, fd, fw], [0, yc, z_bot + H - fw * 0.5])
    bot_f = self.box(f"{id_}-bf", [inner_w, fd, fw * 0.7], [0, yc, z_bot + fw * 0.35])
    frame_parts = [jamb_l, jamb_r, head_f, bot_f]

    clear_w = inner_w - 2.0 * _BUTT
    clear_h = H - fw * 1.7 - 2.0 * _BUTT
    sash_h = clear_h * 0.51
    r_eff = mn(mx(0.0, X(raised)), clear_h - sash_h)
    sw = 0.040
    sd = min(0.036, fd * 0.42)
    glass_parts = []

    for tag, y_off, z_base in (
        ("upper", yc - sd * 0.55 - _BUTT, z_bot + fw * 0.7 + _BUTT + clear_h - sash_h * 0.5),
        ("lower", yc + sd * 0.55 + _BUTT, z_bot + fw * 0.7 + _BUTT + sash_h * 0.5 + r_eff),
    ):
        sash_in_w = clear_w - 2.0 * (sw + _BUTT)
        sash_in_h = sash_h - 2.0 * (sw + _BUTT)
        st_l = self.box(f"{id_}-{tag}-sl", [sw, sd, sash_h], [-clear_w * 0.5 + sw * 0.5, y_off, z_base])
        st_r = self.box(f"{id_}-{tag}-sr", [sw, sd, sash_h], [clear_w * 0.5 - sw * 0.5, y_off, z_base])
        rl_t = self.box(f"{id_}-{tag}-rt", [sash_in_w, sd, sw], [0, y_off, z_base + sash_h * 0.5 - sw * 0.5])
        rl_b = self.box(f"{id_}-{tag}-rb", [sash_in_w, sd, sw], [0, y_off, z_base - sash_h * 0.5 + sw * 0.5])
        frame_parts.extend([st_l, st_r, rl_t, rl_b])

        pane = self.box(f"{id_}-{tag}-pane", [sash_in_w + 0.004, 0.008, sash_in_h + 0.004], [0, y_off, z_base])
        glass_parts.append(pane)

        # Vertical muntin bars butted between top & bottom sash rails; horizontal bars segmented between muntins!
        n_mul = max(1, int(mullions))
        mw = 0.018
        cell_w = (sash_in_w - (n_mul - 1) * mw) / float(n_mul)
        for m in range(1, n_mul):
            mx_pos = -sash_in_w * 0.5 + m * cell_w + (m - 0.5) * mw
            frame_parts.append(self.box(f"{id_}-{tag}-mul{m}", [mw, sd * 0.72, sash_in_h - 2.0 * _BUTT],
                                        [mx_pos, y_off, z_base]))
        sub_rows = max(1, int(transoms) // 2 + 1)
        for tr in range(1, sub_rows):
            tfrac = -0.5 + tr / float(sub_rows)
            for c_idx in range(n_mul):
                cx_pos = -sash_in_w * 0.5 + (c_idx + 0.5) * cell_w + c_idx * mw
                frame_parts.append(self.box(f"{id_}-{tag}-trn{tr}-c{c_idx}",
                                            [cell_w - 2.0 * _BUTT, sd * 0.70, 0.016],
                                            [cx_pos, y_off, z_base + sash_in_h * tfrac]))

    fj = self.join(f"{id_}-frames-j", *frame_parts)
    parts.append(self.mat(f"{id_}-frames", fj, frame_slot) if frame_slot else fj)
    gj = self.join(f"{id_}-glass-j", *glass_parts)
    parts.append(self.mat(f"{id_}-glass", gj, glass_slot) if glass_slot else gj)

    all_j = self.join(f"{id_}-unit", *parts)
    rz = _facing(facing) - PI / 2
    return self.xf(id_, all_j, t=list(at), r=[0, 0, rz] if abs(rz) > 1e-9 else None)


def external_stair(self, id_, rise, going=None, width=0.95, landing_len=1.15, tread_count=12,
                   hand_h=0.95, stringer_slot="weathered-iron", tread_slot="wood",
                   rail_slot="painted-iron", facing="-y", at=(0, 0, 0)):
    """An external iron-and-timber stair with top landing, ground-seated carriage posts, stringers,
    solid treads, and outer handrail/balusters — zero self-intersections under `bake` and 1 connected
    component under `pgm audit --dangling`.

    Local frame before `facing`/`at`: ground is `z = 0`, landing wall-edge sits 0.5 mm outside `y = 0`
    (`y = 0 .. landing_len`), and the flight descends along `+y` from `y = landing_len` to `y = landing_len + going`.
    Example: g.external_stair("stair", rise=3.0, width=0.95, landing_len=1.15, facing="+x", at=[3.0, 0, 0])"""
    H = X(rise)
    n_steps = max(3, int(tread_count))
    G_len = X(going) if going is not None else H * 1.12
    L = X(landing_len)
    w = width
    post_w = 0.075
    str_t, str_h = 0.045, 0.16
    deck_t = 0.065

    iron_parts = []
    wood_parts = []
    rail_parts = []

    # 1. Ground-seated outer columns at y = L - post_w/2, plus side beams & cross ledgers butted cleanly between them!
    py_post = L - post_w * 0.5
    beam_len = L - post_w - 2.0 * _BUTT
    inner_x = w - 2.0 * (post_w + _BUTT)
    z_beam_top = H - deck_t - _BUTT

    for side, sx in (("l", -w * 0.5 + post_w * 0.5), ("r", w * 0.5 - post_w * 0.5)):
        iron_parts.append(self.box(f"{id_}-land-post-{side}", [post_w, post_w, z_beam_top],
                                   [sx, py_post, z_beam_top * 0.5]))
        iron_parts.append(self.box(f"{id_}-land-beam-{side}", [str_t, beam_len, str_h],
                                   [sx, _BUTT + beam_len * 0.5, z_beam_top - str_h * 0.5]))
        # Wall ledger shoe at y = _BUTT..0.055 supporting side beam
        iron_parts.append(self.box(f"{id_}-wall-post-{side}", [post_w, 0.05, z_beam_top - str_h - _BUTT],
                                   [sx, _BUTT + 0.025, (z_beam_top - str_h - _BUTT) * 0.5]))

    iron_parts.append(self.box(f"{id_}-land-trimmer", [inner_x, 0.055, str_h],
                               [0, py_post, z_beam_top - str_h * 0.5]))
    iron_parts.append(self.box(f"{id_}-land-wall-ledger", [inner_x, 0.05, str_h],
                               [0, _BUTT + 0.025, z_beam_top - str_h * 0.5]))
    # Timber landing deck sits on top of iron frame (cross-material contact!)
    wood_parts.append(self.box(f"{id_}-land-deck", [w, L - _BUTT, deck_t],
                               [0, _BUTT + (L - _BUTT) * 0.5, H - deck_t * 0.5]))

    # 2. Inclined stringer prisms (vertical end-cuts seated 0.5 mm from landing post and ground footplate!)
    foot_h = 0.06
    y_s0 = L + _BUTT
    y_s1 = L + G_len - 0.05 - _BUTT
    for side, sx in (("l", -w * 0.5 + post_w * 0.5), ("r", w * 0.5 - post_w * 0.5)):
        iron_parts.append(self.box(f"{id_}-foot-{side}", [0.10, 0.20, foot_h],
                                   [sx, L + G_len + 0.05, foot_h * 0.5]))
        # Closed 4-corner parallelogram prism in YZ with vertical end cuts -> exact 0.5 mm butt!
        s_verts = [
            [sx - str_t * 0.5, y_s0, z_beam_top - 0.02],
            [sx - str_t * 0.5, y_s1, foot_h + 0.14],
            [sx - str_t * 0.5, y_s1, foot_h],
            [sx - str_t * 0.5, y_s0, z_beam_top - 0.18],
            [sx + str_t * 0.5, y_s0, z_beam_top - 0.02],
            [sx + str_t * 0.5, y_s1, foot_h + 0.14],
            [sx + str_t * 0.5, y_s1, foot_h],
            [sx + str_t * 0.5, y_s0, z_beam_top - 0.18],
        ]
        s_faces = [[0, 1, 2, 3], [7, 6, 5, 4], [0, 4, 5, 1], [1, 5, 6, 2], [2, 6, 7, 3], [3, 7, 4, 0]]
        iron_parts.append(self.n(f"{id_}-str-{side}", "surface", vertices=s_verts, faces=s_faces))

    # 3. Solid timber treads bridging both stringers (wood != weathered-iron, so treads tie both stringers!)
    step_rise = (H - foot_h) / n_steps
    step_go = G_len / n_steps
    for k in range(n_steps):
        tz = H - step_rise * (k + 0.85)
        ty = L + step_go * (k + 0.5)
        wood_parts.append(self.box(f"{id_}-tread-{k}", [w, step_go * 0.92, 0.052], [0, ty, mx(foot_h + _BUTT + 0.026, tz)]))

    # 4. Handrail posts & sloped rail prism (vertical end-cuts seated 0.5 mm from posts!)
    np_w = 0.042
    y_np0 = _BUTT + 0.04
    y_np1 = py_post
    y_np2 = L + G_len + 0.05
    yr_a = y_np1 + np_w * 0.5 + _BUTT
    yr_b = y_np2 - np_w * 0.5 - _BUTT
    for side, sx in (("l", -w * 0.5 + np_w * 0.5), ("r", w * 0.5 - np_w * 0.5)):
        rail_parts.append(self.box(f"{id_}-np0-{side}", [np_w, np_w, hand_h],
                                   [sx, y_np0, H + _BUTT + hand_h * 0.5]))
        rail_parts.append(self.box(f"{id_}-np1-{side}", [np_w, np_w, hand_h],
                                   [sx, y_np1, H + _BUTT + hand_h * 0.5]))
        rail_parts.append(self.box(f"{id_}-np2-{side}", [np_w, np_w, hand_h],
                                   [sx, y_np2, foot_h + _BUTT + hand_h * 0.5]))
        rail_parts.append(self.box(f"{id_}-hr-land-{side}",
                                   [0.036, (y_np1 - y_np0) - np_w - 2.0 * _BUTT, 0.036],
                                   [sx, (y_np0 + y_np1) * 0.5, H + _BUTT + hand_h - 0.02]))
        rh_v = [
            [sx - 0.018, yr_a, H + hand_h - 0.01],
            [sx - 0.018, yr_b, foot_h + hand_h - 0.01],
            [sx - 0.018, yr_b, foot_h + hand_h - 0.048],
            [sx - 0.018, yr_a, H + hand_h - 0.048],
            [sx + 0.018, yr_a, H + hand_h - 0.01],
            [sx + 0.018, yr_b, foot_h + hand_h - 0.01],
            [sx + 0.018, yr_b, foot_h + hand_h - 0.048],
            [sx + 0.018, yr_a, H + hand_h - 0.048],
        ]
        rh_f = [[0, 1, 2, 3], [7, 6, 5, 4], [0, 4, 5, 1], [1, 5, 6, 2], [2, 6, 7, 3], [3, 7, 4, 0]]
        rail_parts.append(self.n(f"{id_}-hr-flt-{side}", "surface", vertices=rh_v, faces=rh_f))

    ij = self.join(f"{id_}-iron-j", *iron_parts)
    wj = self.join(f"{id_}-wood-j", *wood_parts)
    rj = self.join(f"{id_}-rail-j", *rail_parts)
    parts = [
        self.mat(f"{id_}-iron", ij, stringer_slot) if stringer_slot else ij,
        self.mat(f"{id_}-wood", wj, tread_slot) if tread_slot else wj,
        self.mat(f"{id_}-rail", rj, rail_slot) if rail_slot else rj,
    ]
    all_j = self.join(f"{id_}-unit", *parts)
    rz = _facing(facing) - PI / 2
    return self.xf(id_, all_j, t=list(at), r=[0, 0, rz] if abs(rz) > 1e-9 else None)


def _prism_yz(g, id_, poly_yz, x0, x1):
    """Closed outward-oriented prism from x0 to x1 (x1 > x0) over a CCW YZ polygon."""
    n = len(poly_yz)
    verts = [[x0, y, z] for y, z in poly_yz] + [[x1, y, z] for y, z in poly_yz]
    faces = [list(reversed(range(n))), list(range(n, 2 * n))]
    for i in range(n):
        j = (i + 1) % n
        faces.append([i, j, n + j, n + i])
    return g.n(id_, "surface", vertices=verts, faces=faces)


def corner_quoins(self, id_, width, depth, z_base=0.0, height=2.4, courses=None, course_h=0.28,
                  long_len=0.34, short_len=0.21, proud=0.028, bevel=0.010, gap=0.002,
                  slot="chipped-limestone", at=(0, 0)):
    """Alternating long-and-short chamfered corner quoin blocks wrapping all 4 corners of a rectangular
    `(width, depth)` masonry body from `z_base` to `z_base + height`. Each stone is a single closed
    L-shaped rusticated prism seated 0.5 mm (`_BUTT`) outside the wall faces so `pgm check --intersections`
    sees 0 self-intersections even when the wall shares `slot`, while `pgm audit` bonds all 4 corners to the wall.
    Supports plain floats or kitlib `X` control expressions (`C("width")`, etc.).
    Example: g.corner_quoins("quoins", C("width"), C("depth"), z_base=0.35, height=C("height") - 0.35)"""
    W, D, Z0, H = X(width), X(depth), X(z_base), X(height)
    hw = self.let(f"{id_}.hw", W * 0.5)
    hd = self.let(f"{id_}.hd", D * 0.5)
    g_eff = max(2.0 * _BUTT, float(gap))
    nc = self.let(f"{id_}.nc", X(courses) if courses is not None else mx(2, floor(H / course_h)))
    pitch = self.let(f"{id_}.pt", H / nc)
    sh = self.let(f"{id_}.sh", mx(0.005, pitch - g_eff))
    nqe = self.let(f"{id_}.nqe", ceil(nc * 0.5))
    nqo = self.let(f"{id_}.nqo", floor(nc * 0.5))

    b = _BUTT
    p = b + float(proud)
    p_in = b + float(proud) * 0.64

    def make_l_proto(pid, sx, sy, ax, ay):
        c = min(float(bevel), float(proud) * 0.40, ax * 0.22, ay * 0.22)
        ci = c * 0.55
        rf = [
            (-ax, b), (b, b), (b, -ay),
            (p - c, -ay), (p, -ay + c),
            (p, p - c), (p - c, p),
            (-ax + c, p), (-ax, p - c),
        ]
        ri = [
            (-ax + ci, b), (b, b), (b, -ay + ci),
            (p_in - ci, -ay + ci), (p_in, -ay + 2.0 * ci),
            (p_in, p_in - ci), (p_in - ci, p_in),
            (-ax + 2.0 * ci, p_in), (-ax + ci, p_in - ci),
        ]
        rf = [(sx * x, sy * y) for x, y in rf]
        ri = [(sx * x, sy * y) for x, y in ri]
        if sx * sy < 0:
            rf.reverse()
            ri.reverse()
        m = len(rf)
        verts = ([[x, y, -0.5] for x, y in ri] + [[x, y, -0.38] for x, y in rf] +
                 [[x, y, 0.38] for x, y in rf] + [[x, y, 0.5] for x, y in ri])
        faces = [list(reversed(range(m))), list(range(3 * m, 4 * m))]
        for band in range(3):
            o0, o1 = band * m, (band + 1) * m
            for i in range(m):
                j = (i + 1) % m
                faces.append([o0 + i, o0 + j, o1 + j, o1 + i])
        return self.n(pid, "surface", vertices=verts, faces=faces)

    parts = []
    z_e0 = Z0 + pitch * 0.5
    z_o0 = Z0 + pitch * 1.5
    for tag_x, sx in (("w", -1.0), ("e", 1.0)):
        for tag_y, sy in (("f", -1.0), ("b", 1.0)):
            ctag = f"{tag_x}{tag_y}"
            pa = make_l_proto(f"{id_}-pa-{ctag}", sx, sy, float(long_len), float(short_len))
            pb = make_l_proto(f"{id_}-pb-{ctag}", sx, sy, float(short_len), float(long_len))
            parts.append(self.array(f"{id_}-ae-{ctag}", pa, [sx * hw, sy * hd, z_e0], [0, 0, pitch * 2.0], nqe, scale=[1, 1, sh]))
            parts.append(self.array(f"{id_}-ao-{ctag}", pb, [sx * hw, sy * hd, z_o0], [0, 0, pitch * 2.0], nqo, scale=[1, 1, sh]))

    joined = _join_many(self, f"{id_}-raw", parts)
    styled = self.mat(f"{id_}-mat", joined, slot) if slot else joined
    tx, ty = at[0], at[1]
    tz = at[2] if len(at) > 2 else 0.0
    if _num(_v(tx)) and _num(_v(ty)) and _num(_v(tz)) and abs(float(_v(tx))) < 1e-12 and abs(float(_v(ty))) < 1e-12 and abs(float(_v(tz))) < 1e-12:
        return self.xf(id_, styled)
    return self.xf(id_, styled, t=[tx, ty, tz])


def window_surround(self, id_, width, height, at=(0, 0, 0), jamb_w=0.14, jamb_courses=4,
                    sill_h=0.09, sill_proj=0.075, lintel_h=0.20, keystone=True, hood=True,
                    depth=0.065, slot="chipped-limestone", facing="-y"):
    """A dressed masonry opening surround: twin sill brackets, weathered projecting sill with drip undercut,
    alternating in-and-out dressed jamb blocks, and flat-arch / keystoned lintel head with weathered hood cornice.
    Leaves the entire `width x height` clear opening (`x in [-width/2, +width/2]`, `z` from `0.070 + sill_h + 2*_BUTT`
    above `at.z`) 100% unobstructed, and sits 0.5 mm proud of the wall plane (`y = 0` before `facing`/`at`).
    Example: g.window_surround("win-sur", 1.8, 1.5, facing="-y", at=[0, -C("depth") * 0.5, 2.0])"""
    W, H = X(width), X(height)
    hw = self.let(f"{id_}.hw", W * 0.5)
    jw = float(jamb_w)
    dp = float(depth)
    sp = float(sill_proj)
    sh = float(sill_h)
    lh = float(lintel_h)
    brk_h = 0.070
    parts = []

    # 1. Twin moulded sill console brackets (z = 0 .. brk_h)
    y0, y_brk = _BUTT, _BUTT + dp + sp * 0.62
    brk_prof = [
        (y0, 0.0), (y0 + dp * 0.55, 0.0), (y_brk * 0.78, brk_h * 0.42),
        (y_brk, brk_h * 0.76), (y_brk, brk_h), (y0, brk_h),
    ]
    bw_half = jw * 0.42
    for side, sx in (("l", -1.0), ("r", 1.0)):
        xc = sx * (hw + jw * 0.55)
        parts.append(_prism_yz(self, f"{id_}-sbrk-{side}", brk_prof, xc - bw_half, xc + bw_half))

    # 2. Projecting weathered sill with front drip throat (z = brk_h + _BUTT .. z_sill_top)
    z_s0 = brk_h + _BUTT
    z_s1 = z_s0 + sh
    y_s1 = _BUTT + dp + sp
    dt = min(0.022, (dp + sp) * 0.35)
    sill_prof = [
        (y0, z_s0), (y_s1 - dt, z_s0), (y_s1 - dt * 0.82, z_s0 + 0.012),
        (y_s1, z_s0 + 0.012), (y_s1, z_s0 + sh * 0.66),
        (y0 + dp, z_s1), (y0, z_s1),
    ]
    sill_hx = hw + jw * 1.18 + 0.04
    parts.append(_prism_yz(self, f"{id_}-sill", sill_prof, -sill_hx, sill_hx))

    # 3. Alternating in-and-out dressed jamb blocks strictly outside [-hw, +hw]
    z_op0 = z_s1 + _BUTT
    z_op1 = self.let(f"{id_}.zop1", z_op0 + H)
    nj = max(2, int(jamb_courses))
    j_gap = 0.0015
    j_pitch = H / float(nj)
    j_bh = mx(0.005, j_pitch - j_gap)
    for side, sx in (("l", -1.0), ("r", 1.0)):
        for k in range(nj):
            bw = jw * (1.18 if (k % 2 == 0) else 0.92)
            xc = sx * (hw + _BUTT + bw * 0.5)
            zc = z_op0 + j_pitch * (k + 0.5)
            parts.append(_bevel_box(self, f"{id_}-jb-{side}{k}", [bw, dp, j_bh],
                                    [xc, _BUTT + dp * 0.5, zc], 0.008))

    # 4. Flat-arch / keystoned lintel head + weathered hood cornice above z_op1
    z_l0 = z_op1 + _BUTT
    z_l1 = z_l0 + lh
    l_hx = hw + jw * 1.18
    if keystone:
        kw0 = self.let(f"{id_}.kw0", mn(0.068, W * 0.12))
        kw1 = self.let(f"{id_}.kw1", mn(0.098, W * 0.17))
        key_poly = [(-kw0, z_l0), (kw0, z_l0), (kw1, z_l1 + 0.026), (-kw1, z_l1 + 0.026)]
        parts.append(_prism_xz(self, f"{id_}-key", key_poly, _BUTT, _BUTT + dp + 0.030))
        jg = 0.0014
        r_poly = [(kw0 + jg, z_l0), (l_hx, z_l0), (l_hx, z_l1), (kw1 + jg, z_l1)]
        l_poly = [(-l_hx, z_l0), (-kw0 - jg, z_l0), (-kw1 - jg, z_l1), (-l_hx, z_l1)]
        parts.append(_prism_xz(self, f"{id_}-lh-r", r_poly, _BUTT, _BUTT + dp + 0.012))
        parts.append(_prism_xz(self, f"{id_}-lh-l", l_poly, _BUTT, _BUTT + dp + 0.012))
        z_hood0 = z_l1 + 0.026 + _BUTT
        if hood:
            # Bedding fillets over haunches so hood cornice seats evenly across full width
            fil_h = 0.026 - _BUTT
            fil_w = l_hx - kw1 - jg
            for side, sx in (("l", -1.0), ("r", 1.0)):
                parts.append(self.box(f"{id_}-lfil-{side}", [fil_w, dp + 0.012, fil_h],
                                      [sx * (kw1 + jg + fil_w * 0.5), _BUTT + (dp + 0.012) * 0.5, z_l1 + _BUTT + fil_h * 0.5]))
    else:
        parts.append(_bevel_box(self, f"{id_}-lintel", [l_hx * 2.0, dp + 0.012, lh],
                                [0, _BUTT + (dp + 0.012) * 0.5, z_l0 + lh * 0.5], 0.010))
        z_hood0 = z_l1 + _BUTT

    if hood:
        hh = 0.062
        yh1 = _BUTT + dp + sp * 0.85
        hood_prof = [
            (y0, z_hood0), (y0 + dp + 0.018, z_hood0),
            (yh1, z_hood0 + hh * 0.45), (yh1, z_hood0 + hh * 0.78),
            (y0 + dp * 0.65, z_hood0 + hh), (y0, z_hood0 + hh),
        ]
        hood_hx = l_hx + 0.055
        parts.append(_prism_yz(self, f"{id_}-hood", hood_prof, -hood_hx, hood_hx))

    joined = _join_many(self, f"{id_}-raw", parts)
    styled = self.mat(f"{id_}-mat", joined, slot) if slot else joined
    rz = _facing(facing) - PI / 2
    return self.xf(id_, styled, t=list(at), r=[0, 0, rz] if abs(rz) > 1e-9 else None)


def louvred_lantern(self, id_, length, width, height, z_base=0.0, louvres=4, roof="hipped",
                    roof_rise=None, overhang=0.12, finials=True, frame_slot="wood",
                    roof_slot="slate", trim_slot="terracotta", core_slot="black", at=(0, 0, 0)):
    """A timber-and-slate roof ventilator turret / louvred ridge lantern with base curb, corner posts,
    sloped louvre slats on all 4 faces, dark weather core, hipped or gabled cap roof, ridge roll, and
    turned finials (distinct from `kitlib_glazing.ridge_lantern`, which builds glazed vault monitors).
    Example: g.louvred_lantern("lantern", 1.35, 0.58, 0.48, z_base=C("height") + 1.25)"""
    assert roof in ("hipped", "gable"), f"louvred_lantern: roof must be 'hipped' or 'gable', got {roof!r}"

    L, W, H, Z0 = X(length), X(width), X(height), X(z_base)
    curb_h = self.let(f"{id_}.ch", mn(0.11, H * 0.22))
    head_h = self.let(f"{id_}.hh", mn(0.055, H * 0.14))
    post_h = self.let(f"{id_}.ph", mx(0.08, H - curb_h - head_h - 2.0 * _BUTT))
    z_post0 = Z0 + curb_h + _BUTT
    z_post_m = z_post0 + post_h * 0.5
    pw = 0.058

    frame_parts = [
        self.box(f"{id_}-curb", [L + 0.04, W + 0.04, curb_h - _BUTT], [0, 0, Z0 + (curb_h + _BUTT) * 0.5]),
        self.box(f"{id_}-head", [L + 0.06, W + 0.06, head_h], [0, 0, Z0 + H - head_h * 0.5]),
    ]
    for tx, sx in (("w", -1.0), ("e", 1.0)):
        for ty, sy in (("f", -1.0), ("b", 1.0)):
            frame_parts.append(self.box(f"{id_}-post-{tx}{ty}", [pw, pw, post_h],
                                        [sx * (L * 0.5 - pw * 0.5), sy * (W * 0.5 - pw * 0.5), z_post_m]))

    # Sloped louvre slats on all 4 faces, strictly disjoint from corner posts and dark core
    n_lv = max(1, int(louvres))
    bc = mn(0.036, post_h / (n_lv * 1.45))
    bt = mn(0.012, bc * 0.32)
    span_x = mx(0.04, L - 2.0 * (pw + _BUTT))
    span_y = mx(0.04, W - 2.0 * (pw + _BUTT))
    for k in range(n_lv):
        zk = z_post0 + post_h * ((k + 0.5) / float(n_lv))
        for ty, sy in (("f", -1.0), ("b", 1.0)):
            s_raw = self.n(f"{id_}-lvs-{ty}{k}-s", "cube", size=[span_x, bc, bt])
            frame_parts.append(self.xf(f"{id_}-lvs-{ty}{k}", s_raw,
                                       t=[0, sy * (W * 0.5 - pw * 0.5), zk], r=[sy * -0.40, 0, 0]))
        for tx, sx in (("w", -1.0), ("e", 1.0)):
            e_raw = self.n(f"{id_}-lve-{tx}{k}-s", "cube", size=[bc, span_y, bt])
            frame_parts.append(self.xf(f"{id_}-lve-{tx}{k}", e_raw,
                                       t=[sx * (L * 0.5 - pw * 0.5), 0, zk], r=[0, sx * 0.40, 0]))

    parts = []
    fj = _join_many(self, f"{id_}-frm-j", frame_parts)
    parts.append(self.mat(f"{id_}-frm", fj, frame_slot) if frame_slot else fj)

    if core_slot:
        core = self.box(f"{id_}-core-b", [mx(0.04, L - 2.0 * pw - 0.024), mx(0.04, W - 2.0 * pw - 0.024), post_h],
                        [0, 0, z_post_m])
        parts.append(self.mat(f"{id_}-core", core, core_slot))

    # Cap roof (hipped or gable) seated 0.5 mm above head plate
    ov = 0.12 if overhang is None else float(overhang)
    rhx = self.let(f"{id_}.rhx", L * 0.5 + ov)
    rhy = self.let(f"{id_}.rhy", W * 0.5 + ov)
    rr = X(roof_rise) if roof_rise is not None else W * 0.38
    z_r0 = Z0 + H + _BUTT
    z_r1 = self.let(f"{id_}.zr1", z_r0 + rr)
    rx = self.let(f"{id_}.rx", mx(0.09, rhx - rhy * 0.80) if roof == "hipped" else rhx)
    r_verts = [
        [-rhx, -rhy, z_r0], [rhx, -rhy, z_r0], [rhx, rhy, z_r0], [-rhx, rhy, z_r0],
        [-rx, 0.0, z_r1], [rx, 0.0, z_r1],
    ]
    r_faces = [[0, 3, 2, 1], [0, 1, 5, 4], [2, 3, 4, 5], [1, 2, 5], [3, 0, 4]]
    cap = self.n(f"{id_}-cap-s", "surface", vertices=r_verts, faces=r_faces)
    parts.append(self.mat(f"{id_}-cap", cap, roof_slot) if roof_slot else cap)

    trim_parts = []
    if finials:
        f_prof = [[0.0, 0.0], [0.034, 0.0], [0.034, 0.024], [0.020, 0.055],
                  [0.028, 0.095], [0.015, 0.145], [0.006, 0.195], [0.0, 0.21]]
        fin_u = self.lathe(f"{id_}-fin-u", f_prof, 12)
        trim_parts.append(self.xf(f"{id_}-fin-w", fin_u, t=[-rx, 0, z_r1 + _BUTT]))
        trim_parts.append(self.xf(f"{id_}-fin-e", fin_u, t=[rx, 0, z_r1 + _BUTT]))
        roll_len = mx(0.02, rx * 2.0 - 2.0 * (0.034 + _BUTT))
    else:
        roll_len = rx * 2.0
    trim_parts.append(_bevel_box(self, f"{id_}-roll", [roll_len, 0.044, 0.038],
                                 [0, 0, z_r1 + _BUTT + 0.019], 0.008))
    tj = _join_many(self, f"{id_}-trm-j", trim_parts)
    parts.append(self.mat(f"{id_}-trm", tj, trim_slot) if trim_slot else tj)

    all_j = _join_many(self, f"{id_}-unit", parts)
    return self.xf(id_, all_j, t=list(at))


def corbelled_chimney(self, id_, width=0.68, depth=0.52, z_base=0.0, height=1.85, pots=1,
                      corbel_steps=3, brick_slot="weathered-brick", stone_slot="chipped-limestone",
                      pot_slot="terracotta", flashing_slot="weathered-iron", at=(0, 0, 0)):
    """A coursed brick chimney stack rising from `z_base` to `z_base + height` with a 4-sided base flashing
    collar, stone necking band, oversailing brick corbel courses, weathered stone cap, and hollow cylindrical
    terracotta flue pot(s). Zero self-intersections under `bake` and 1 connected component under `pgm audit`.
    Example: g.corbelled_chimney("chimney", 0.68, 0.52, z_base=C("height"), height=1.85, pots=2, at=[-1.2, 0.8, 0])"""
    W, D, Z0, H = X(width), X(depth), X(z_base), X(height)
    n_pots = max(1, int(pots))
    n_corb = max(1, int(corbel_steps))
    along_x = True
    if _num(_v(width)) and _num(_v(depth)):
        along_x = float(_v(width)) >= float(_v(depth))

    # Ensure stack width accommodates multi-pot spacing cleanly across control sweeps
    min_long = (n_pots - 1) * 0.24 + 0.28
    W_eff = self.let(f"{id_}.we", mx(min_long, W) if along_x else W)
    D_eff = self.let(f"{id_}.de", D if along_x else mx(min_long, D))

    corb_h, neck_h, cap_h = 0.072, 0.055, 0.085
    crown_h = neck_h + _BUTT + n_corb * (corb_h + _BUTT) + cap_h
    sh_h = self.let(f"{id_}.shh", mx(0.18, H - crown_h - 2.0 * _BUTT))
    sh_z = Z0 + _BUTT + sh_h * 0.5

    parts = []
    if flashing_slot:
        ox, oy = W_eff * 0.5 + 0.042, D_eff * 0.5 + 0.042
        ix, iy = W_eff * 0.5 + _BUTT, D_eff * 0.5 + _BUTT
        fv, ff = _plate([(-ox, -oy), (ox, -oy), (ox, oy), (-ox, oy)],
                        [(-ix, -iy), (ix, -iy), (ix, iy), (-ix, iy)],
                        Z0 + 0.095, Z0 + _BUTT)
        fl = self.n(f"{id_}-flash-s", "surface", vertices=fv, faces=ff)
        parts.append(self.mat(f"{id_}-flash", fl, flashing_slot))

    # Coursed brick shaft + oversailing brick corbel courses
    sh_raw = self.box(f"{id_}-sh-raw", [W_eff, D_eff, sh_h], [0, 0, sh_z])
    c_guides = [
        self.courses(f"{id_}-cf", at=[0, -D_eff * 0.5 - 0.02, sh_z], r=[PI * 0.5, 0, 0],
                     size=[mx(0.12, W_eff - 0.05), mx(0.10, sh_h - 0.05)], course=0.085, block=0.22, bond=0.5, minimum=0.07),
        self.courses(f"{id_}-cb", at=[0, D_eff * 0.5 + 0.02, sh_z], r=[PI * 0.5, 0, 0],
                     size=[mx(0.12, W_eff - 0.05), mx(0.10, sh_h - 0.05)], course=0.085, block=0.22, bond=0.5, minimum=0.07),
        self.courses(f"{id_}-cw", at=[-W_eff * 0.5 - 0.02, 0, sh_z], r=[PI * 0.5, 0, PI * 0.5],
                     size=[mx(0.12, D_eff - 0.05), mx(0.10, sh_h - 0.05)], course=0.085, block=0.22, bond=0.5, minimum=0.07),
        self.courses(f"{id_}-ce", at=[W_eff * 0.5 + 0.02, 0, sh_z], r=[PI * 0.5, 0, PI * 0.5],
                     size=[mx(0.12, D_eff - 0.05), mx(0.10, sh_h - 0.05)], course=0.085, block=0.22, bond=0.5, minimum=0.07),
    ]
    cg_j = self.join(f"{id_}-cg", *c_guides)
    brick_parts = [self.seam(f"{id_}-sh", sh_raw, cg_j, width=0.008, depth=0.004, reach=0.06)]

    z_neck0 = Z0 + _BUTT + sh_h + _BUTT
    stone_parts = [_bevel_box(self, f"{id_}-neck", [W_eff + 0.06, D_eff + 0.06, neck_h],
                              [0, 0, z_neck0 + neck_h * 0.5], 0.008)]

    z_corb_base = z_neck0 + neck_h + _BUTT
    for k in range(n_corb):
        ov_k = 0.054 * (k + 1)
        zk = z_corb_base + k * (corb_h + _BUTT) + corb_h * 0.5
        brick_parts.append(_bevel_box(self, f"{id_}-corb-{k}", [W_eff + ov_k, D_eff + ov_k, corb_h],
                                      [0, 0, zk], 0.007))

    z_cap0 = z_corb_base + n_corb * (corb_h + _BUTT)
    ov_cap = 0.054 * n_corb + 0.06
    stone_parts.append(_bevel_box(self, f"{id_}-cap", [W_eff + ov_cap, D_eff + ov_cap, cap_h],
                                  [0, 0, z_cap0 + cap_h * 0.5], 0.011))

    bj = _join_many(self, f"{id_}-brk-j", brick_parts)
    sj = _join_many(self, f"{id_}-stn-j", stone_parts)
    parts.append(self.mat(f"{id_}-brk", bj, brick_slot) if brick_slot else bj)
    parts.append(self.mat(f"{id_}-stn", sj, stone_slot) if stone_slot else sj)

    # Hollow turned terracotta flue pot(s) seated 0.5 mm above stone cap
    ro = 0.112 if n_pots == 1 else 0.092
    rb = ro * 0.62
    pot_u = self.annulus(f"{id_}-pot-u", [
        [rb, 0.0], [ro * 1.16, 0.0], [ro * 1.14, 0.055], [ro, 0.085],
        [ro * 0.90, 0.34], [ro * 1.14, 0.375], [ro * 1.14, 0.425], [ro * 0.96, 0.44], [rb, 0.44],
    ], 16)
    z_pot = z_cap0 + cap_h + _BUTT
    pot_parts = []
    if n_pots == 1:
        pot_parts.append(self.xf(f"{id_}-pot-0", pot_u, t=[0, 0, z_pot]))
    else:
        span_p = (W_eff if along_x else D_eff) - 0.28
        for i in range(n_pots):
            off = -span_p * 0.5 + span_p * (i / float(n_pots - 1))
            pt = [off, 0, z_pot] if along_x else [0, off, z_pot]
            pot_parts.append(self.xf(f"{id_}-pot-{i}", pot_u, t=pt))
    pj = _join_many(self, f"{id_}-pot-j", pot_parts)
    parts.append(self.mat(f"{id_}-pots", pj, pot_slot) if pot_slot else pj)

    all_j = _join_many(self, f"{id_}-unit", parts)
    return self.xf(id_, all_j, t=list(at))


def panelled_door(self, id_, width=0.95, height=2.10, thickness=0.056, at=(0, 0, 0),
                  glazed_upper=True, panels_x=2, outer_frame=True, frame_depth=0.14,
                  threshold=True, door_slot="wood", frame_slot="wood", glass_slot="glass",
                  hardware_slot="bronze", stone_slot="chipped-limestone", facing="-y"):
    """A stile-and-rail panelled timber door with bottom/lock/frieze/top rails, double-sided bevelled
    fielded panels, optional glazed upper light (`glass_slot`), outer lining frame, stone threshold step,
    butt-hinge knuckles, and double-sided lever handle/escutcheon hardware. Zero self-intersections under
    `bake` even when `door_slot == frame_slot`, and 1 connected component under `pgm audit`.
    Example: g.panelled_door("door", 0.95, 2.1, 0.056, at=[0, -C("depth") * 0.5, 0.35], facing="-y")"""
    W, H, TD = X(width), X(height), X(thickness)
    fd = mx(TD, X(frame_depth)) if outer_frame else TD
    yc = self.let(f"{id_}.yc", _BUTT + fd * 0.5)
    parts = []

    if threshold:
        th_h = 0.075
        th_d = fd + 0.09
        th_w = W + (0.16 if outer_frame else 0.06)
        th = _bevel_box(self, f"{id_}-thresh-b", [th_w, th_d, th_h],
                        [0, _BUTT + th_d * 0.5, th_h * 0.5], 0.010)
        parts.append(self.mat(f"{id_}-thresh", th, stone_slot) if stone_slot else th)
        z_f0 = th_h + _BUTT
    else:
        z_f0 = 0.0

    frame_parts = []
    if outer_frame:
        jw = 0.062
        fh = H - z_f0
        frame_parts.append(self.box(f"{id_}-jamb-l", [jw, fd, fh], [-W * 0.5 + jw * 0.5, yc, z_f0 + fh * 0.5]))
        frame_parts.append(self.box(f"{id_}-jamb-r", [jw, fd, fh], [W * 0.5 - jw * 0.5, yc, z_f0 + fh * 0.5]))
        frame_parts.append(self.box(f"{id_}-head", [W - 2.0 * (jw + _BUTT), fd, jw],
                                    [0, yc, z_f0 + fh - jw * 0.5]))
        lw = self.let(f"{id_}.lw", W - 2.0 * (jw + 2.0 * _BUTT))
        lh = self.let(f"{id_}.lh", fh - jw - 2.0 * _BUTT)
        zl0 = z_f0 + _BUTT
    else:
        lw = self.let(f"{id_}.lw", W)
        lh = self.let(f"{id_}.lh", H - z_f0)
        zl0 = z_f0

    # Door leaf stiles & bottom / lock / frieze / top rails
    sw = self.let(f"{id_}.sw", mn(0.115, lw * 0.15))
    in_w = self.let(f"{id_}.inw", mx(0.08, lw - 2.0 * (sw + _BUTT)))
    rb_h = lh * 0.115
    rl_z0, rl_z1 = zl0 + lh * 0.39, zl0 + lh * 0.48
    rf_z0, rf_z1 = zl0 + lh * 0.78, zl0 + lh * 0.85
    rt_h = lh * 0.070

    door_parts = [
        self.box(f"{id_}-st-l", [sw, TD, lh], [-lw * 0.5 + sw * 0.5, yc, zl0 + lh * 0.5]),
        self.box(f"{id_}-st-r", [sw, TD, lh], [lw * 0.5 - sw * 0.5, yc, zl0 + lh * 0.5]),
        self.box(f"{id_}-rl-bot", [in_w, TD, rb_h], [0, yc, zl0 + rb_h * 0.5]),
        self.box(f"{id_}-rl-lck", [in_w, TD, rl_z1 - rl_z0], [0, yc, (rl_z0 + rl_z1) * 0.5]),
        self.box(f"{id_}-rl-frz", [in_w, TD, rf_z1 - rf_z0], [0, yc, (rf_z0 + rf_z1) * 0.5]),
        self.box(f"{id_}-rl-top", [in_w, TD, rt_h], [0, yc, zl0 + lh - rt_h * 0.5]),
    ]

    nx = max(1, int(panels_x))
    mw = sw * 0.72

    def fielded_frustum(fid, cx, cz, pw, ph, sgn):
        hw0, hh0 = mx(0.015, pw * 0.5 - 0.012), mx(0.015, ph * 0.5 - 0.012)
        hw1, hh1 = mx(0.008, pw * 0.5 - 0.040), mx(0.008, ph * 0.5 - 0.040)
        if sgn > 0:
            ya, yb = yc + TD * 0.17 + _BUTT, yc + TD * 0.44
            ra = [(cx - hw0, cz - hh0), (cx + hw0, cz - hh0), (cx + hw0, cz + hh0), (cx - hw0, cz + hh0)]
            rb = [(cx - hw1, cz - hh1), (cx + hw1, cz - hh1), (cx + hw1, cz + hh1), (cx - hw1, cz + hh1)]
        else:
            ya, yb = yc - TD * 0.44, yc - TD * 0.17 - _BUTT
            ra = [(cx - hw1, cz - hh1), (cx + hw1, cz - hh1), (cx + hw1, cz + hh1), (cx - hw1, cz + hh1)]
            rb = [(cx - hw0, cz - hh0), (cx + hw0, cz - hh0), (cx + hw0, cz + hh0), (cx - hw0, cz + hh0)]
        verts = [[x, ya, z] for x, z in ra] + [[x, yb, z] for x, z in rb]
        faces = [[0, 1, 2, 3], [7, 6, 5, 4], [1, 0, 4, 5], [2, 1, 5, 6], [3, 2, 6, 7], [0, 3, 7, 4]]
        return self.n(fid, "surface", vertices=verts, faces=faces)

    def add_panel_tier(t_tag, za, zb):
        zh = mx(0.04, zb - za)
        zm = (za + zb) * 0.5
        cw = mx(0.04, (in_w - (nx - 1) * (mw + 2.0 * _BUTT)) / float(nx))
        for m in range(1, nx):
            mx_p = -in_w * 0.5 + m * cw + (m - 0.5) * mw + (2 * m - 1) * _BUTT
            door_parts.append(self.box(f"{id_}-{t_tag}-m{m}", [mw, TD, zh], [mx_p, yc, zm]))
        for c_i in range(nx):
            cx_p = -in_w * 0.5 + (c_i + 0.5) * cw + c_i * (mw + 2.0 * _BUTT)
            door_parts.append(self.box(f"{id_}-{t_tag}-c{c_i}", [cw, TD * 0.34, zh], [cx_p, yc, zm]))
            door_parts.append(fielded_frustum(f"{id_}-{t_tag}-ff{c_i}", cx_p, zm, cw, zh, 1))
            door_parts.append(fielded_frustum(f"{id_}-{t_tag}-fb{c_i}", cx_p, zm, cw, zh, -1))

    add_panel_tier("low", zl0 + rb_h + _BUTT, rl_z0 - _BUTT)
    add_panel_tier("frz", rf_z1 + _BUTT, zl0 + lh - rt_h - _BUTT)

    zb0, zb1 = rl_z1 + _BUTT, rf_z0 - _BUTT
    if glazed_upper:
        gh = mx(0.05, zb1 - zb0)
        gm = (zb0 + zb1) * 0.5
        pane = self.box(f"{id_}-glz-b", [in_w, mn(0.012, TD * 0.28), gh], [0, yc, gm])
        parts.append(self.mat(f"{id_}-glz", pane, glass_slot) if glass_slot else pane)
        bw_m = 0.020
        if nx >= 2:
            door_parts.append(self.box(f"{id_}-gmv", [bw_m, TD * 0.58, gh], [0, yc, gm]))
            hw_seg = mx(0.02, (in_w - bw_m) * 0.5 - _BUTT)
            for s_tag, sx in (("l", -1.0), ("r", 1.0)):
                door_parts.append(self.box(f"{id_}-gmh-{s_tag}", [hw_seg, TD * 0.56, bw_m],
                                           [sx * (bw_m * 0.5 + _BUTT + hw_seg * 0.5), yc, gm]))
        else:
            door_parts.append(self.box(f"{id_}-gmh", [in_w, TD * 0.56, bw_m], [0, yc, gm]))
    else:
        add_panel_tier("mid", zb0, zb1)

    if frame_slot == door_slot:
        dj = _join_many(self, f"{id_}-wood-j", frame_parts + door_parts)
        parts.append(self.mat(f"{id_}-wood", dj, door_slot) if door_slot else dj)
    else:
        if frame_parts:
            fj = _join_many(self, f"{id_}-frm-j", frame_parts)
            parts.append(self.mat(f"{id_}-frm", fj, frame_slot) if frame_slot else fj)
        dj = _join_many(self, f"{id_}-leaf-j", door_parts)
        parts.append(self.mat(f"{id_}-leaf", dj, door_slot) if door_slot else dj)

    # Double-sided bronze escutcheon, lever handle & 3 butt-hinge knuckles
    if hardware_slot:
        hw_parts = []
        xh = lw * 0.5 - sw * 0.5
        zh = (rl_z0 + rl_z1) * 0.5
        for f_tag, sgn in (("f", 1.0), ("b", -1.0)):
            y_esc = yc + sgn * (TD * 0.5 + _BUTT + 0.005)
            y_col = yc + sgn * (TD * 0.5 + 2.0 * _BUTT + 0.022)
            y_lev = yc + sgn * (TD * 0.5 + 3.0 * _BUTT + 0.042)
            hw_parts.append(_bevel_box(self, f"{id_}-esc-{f_tag}", [0.040, 0.010, 0.150], [xh, y_esc, zh], 0.003))
            hw_parts.append(self.box(f"{id_}-col-{f_tag}", [0.022, 0.024, 0.022], [xh, y_col, zh + 0.020]))
            hw_parts.append(_bevel_box(self, f"{id_}-lev-{f_tag}", [0.100, 0.016, 0.020],
                                       [xh - 0.038, y_lev, zh + 0.020], 0.005))
        hg_u = self.lathe(f"{id_}-hg-u", [[0.0, 0.0], [0.010, 0.0], [0.010, 0.080], [0.0, 0.080]], 10)
        for hi, frac in enumerate((0.16, 0.50, 0.84)):
            hw_parts.append(self.xf(f"{id_}-hg-{hi}", hg_u,
                                    t=[-lw * 0.5 + 0.010, yc + TD * 0.5 + _BUTT + 0.010, zl0 + lh * frac - 0.040]))
        hj = _join_many(self, f"{id_}-hw-j", hw_parts)
        parts.append(self.mat(f"{id_}-hw", hj, hardware_slot))

    all_j = _join_many(self, f"{id_}-unit", parts)
    rz = _facing(facing) - PI / 2
    return self.xf(id_, all_j, t=list(at), r=[0, 0, rz] if abs(rz) > 1e-9 else None)


Graph.box_on = box_on
Graph.hipped_roof = hipped_roof
Graph.sash_window = sash_window
Graph.external_stair = external_stair
Graph.corner_quoins = corner_quoins
Graph.window_surround = window_surround
Graph.louvred_lantern = louvred_lantern
Graph.corbelled_chimney = corbelled_chimney
Graph.panelled_door = panelled_door
