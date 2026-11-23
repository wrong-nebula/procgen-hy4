# kitparts: reusable parts for kit builders

Generated from `recipes/kitparts.py` by `tools/kitparts_doc.py`; do not edit by hand.

Reusable parts for kit builders: arches, mouldings, walling, flanges, wheels, ties, and SDS/FWN castings.

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

## Parts

### `arch_points(arch, radius, segments=28)`

Points (x, z) along a circle of `radius` about the arch centre, from the west springing to the east.

### `arched_wall(G, id_, arch, ring_depth, half_width, top, y_back, thickness, segments=28)`

A wall in the x-z plane from x = -half_width to +half_width and z = 0 to `top`, with the arch opening (and the
seat of its voussoir ring, `ring_depth` deep) cut out: half piers and spandrels in one solid. Extruded across y
from y_back - thickness to y_back. Put `voussoirs` in the notch.

### `ashlar(G, id_, surface, at, r, size, course=0.38, block=0.75, bond=0.5, width=0.012, depth=0.006)`

Coursed joints (surface detail) on one face of `surface`: a rectangle `size` = [w, h] centred at `at`,
rotated by `r` (e.g. [PI / 2, 0, 0] for a face square to y). Returns the jointed surface.

### `bolted_clevis(G, id_, gap, cheek_t, pin_r, length, at=None, axis='z')`

A forked mechanical clevis yoke with twin open-bore eyes (`bored_collar`) + cross-pin (`sds_cylinder`)
for governor linkages, furnace balance beams, and tie rods.

### `bolted_flange(G, id_, axis, at, outer_r, bore_r, thickness, bolts=12, bolt_circle=None, face=1, head='hex', bolt_r=0.026, washer=0.04, sides=32)`

A bored flange ring on an axis ("x", "y" or "z") centred at `at`, `thickness` thick, with `bolts` heads (detail)
on a bolt circle (default midway between bore and rim) on the face toward `face` (+1 or -1 along the axis).
The bore stays open (pipe ends show the hole).

### `bored_collar(G, id_, outer_r, bore_r, length, at=None, axis='z', sides=24, bevel=0.003)`

A true open-bore spacer sleeve, keeper tube or thrust washer (`revolve` closed ring off-axis + FWN)
so pins and through-bolts pass cleanly through the bore with real mechanical clearance.

### `bored_nut(G, id_, outer_r, bore_r, depth, at=None, axis='z', bevel=None)`

A true open-bore all-quad hexagon nut (`revolve` closed chamfered ring, sides=6 + FWN normals) so studs
and bolts of radius < `bore_r` pass cleanly through the bore with zero physical solid interpenetration.

### `cast_housing(G, id_, size, at=None, boss_inset=0.0, boss_height=0.0, wall_crease=1.8, rim_crease=3.2, levels=2, lod_levels=None)`

A cast-iron or moulded hard-surface housing with optional raised top boss, variable Chaikin semi-sharp
creases and exact limit-surface normals (zero shrinkage at mating faces).

### `channel_joints(G, id_, wall, arch, ring_depth, half_width, z0, z1, face_y, courses=9, width=0.028, depth=0.014)`

Horizontal channel joints (rusticated refends, surface detail) on the street face (y = face_y) of an
`arched_wall`: `courses` courses between z0 and z1, each joint clipped to the pier face below the springing and
to the arch ring above it. Returns the jointed wall.

### `curved_boss_patch(G, id_, target, size, at, r=None, divisions=(2, 6, 5), offset=0.01, thickness=0.014, crease=2.2, levels=2, back_normal=(0, 1, 0), front_normal=(0, -1, 0))`

A thin nameplate, collar or mounting boss shrinkwrapped onto a curved SDS surface (`target`) without pinching.

### `drafted_plinth(G, id_, size, at=None, bevel=0.014, courses=2, margin=0.055, block=0.45)`

A crisp FWN chamfered limestone masonry plinth with chiselled top margin border and coursed ashlar sides.
Details are applied strictly after chamfer/smooth so they survive cleanly.

### `extrude_x(G, id_, profile_yz, length, x0=None)`

Extrude a closed (y, z) profile along x: centred on x = 0, or starting at x0.

### `extrude_y(G, id_, profile_xz, depth, y_far)`

Extrude a closed (x, z) profile along y; the solid spans y = y_far - depth .. y_far.

### `filleted_box(G, id_, size, at=None, width=0.015, segments=3, mode='fwn', levels=2, angle=55)`

A hard-surface rectangular block with controlled industrial edges:
- mode="fwn": mid-poly multi-segment circular fillet + face-area-squared weighted normals (fast, flat panels).
- mode="sds": all-quad support loops (segments=2, profile=1.0) -> Catmull-Clark limit surface.

### `fit_arch(G, id_, half_span, crown, spring_min)`

Proportion an arch: semicircular when the crown leaves room above `spring_min`, else the segmental arch whose
intrados crown is at `crown` and springs at `spring_min`. Returns a dict of values (named `<id>-...` in the
document): half_span, crown, spring (springing height), rise, rho (intrados radius), zo (centre height),
phi (half angle). Pass the dict to arched_wall, voussoirs, channel_joints and fit_ring_depth.

### `fit_ring_depth(arch, half_width, top, wanted=0.42, margin=0.05)`

A voussoir ring depth that fits: at most `wanted`, and small enough that the ring stays inside the half
piers (|x| <= half_width) and under `top`. Use the same depth for `arched_wall` and `voussoirs`.

### `helical_spring(G, id_, coil_r, wire_r, height, turns=5, at=None, axis='z', segs_per_turn=14, sides=8)`

A quad-capped swept helical valve or governor spring (`curve` + `tube(cap='quad')` + FWN smooth).

### `moulding(G, id_, kind, length, face_y, z, scale=1.0, outward=-1, x0=None)`

A moulded course running along x: `kind` is a MOULDINGS name or your own (projection, height) profile.
Its base sits at height z on a wall face at y = face_y, projecting toward `outward` (-1: toward -y, the street
side in the site frame). Centred on x = 0 (or starting at x0), `length` long.

### `moulding_height(kind, scale=1.0)`

Height of a named moulding at a scale (the top of its profile).

### `pillow_block_bearing(G, id_, bore_r, length=0.09, at=None, axis='x', sides=20)`

An open-bore cast-iron split pillow-block journal bearing (bored housing + twin downward-facing bolted
mounting ears) that cleanly seats a shaft of radius < `bore_r` with zero interpenetration.

### `pipe_tee_junction(G, id_, main_r, main_len, branch_r, branch_len, fillet_r=0.028, bore_ratio=0.72, at=None, axis='x', sides=24)`

A cast-iron flanged pipe T-junction combining an open-bore main barrel (`G.pipe`), an analytical
all-quad `saddle_collar` weld fillet, and a raised branch flange — zero BSP artifacts or solid clipping.

### `saddle_collar(G, id_, host_r, branch_r, fillet_r=0.025, top_z=None, at=None, axis='x', sides=24, levels=2, host_proxy=None)`

Analytical all-quad cast-iron saddle weld fillet where a +Z branch cylinder of radius `branch_r`
meets a horizontal host cylinder of radius `host_r` (along `axis` 'x' or 'y'). Zero BSP triangle soup,
zero solid clipping inside the host cylinder, and optional `transferNormals` onto the skirt from `host_proxy`.

### `sds_cylinder(G, id_, radius, depth, at=None, axis='z', sides=12, rings=2, rim_crease=2.8, levels=2, lod_levels=None)`

An SDS-clean cylindrical drum, capacitor or vessel using an all-quad grid cap (zero rim pinching).

### `spoked_sheave(G, id_, outer_r, width, bore_r, groove_r=0.022, spokes=6, at=None, axis='x', sides=32)`

A true open-bore grooved cable sheave (turned FWN rim + open-bore hub + radial spokes) so an axle pin
of radius < `bore_r` passes cleanly through with zero solid interpenetration.

### `spoked_wheel(G, id_, at, rim_r, rim_section=0.022, spokes=6, hub_r=None, hub_h=None, spoke_r=None, sides=8)`

A horizontal handwheel (in the x-y plane) centred at `at`: a round rim of section radius `rim_section`, a
turned hub and `spokes` round spokes. Rotate it with G.xf for another plane.

### `tie_rod(G, id_, axis, start, end, at, radius=0.016, plate_r=0.075, plate_t=0.025)`

An iron tie rod along an axis ("x", "y" or "z") from `start` to `end` (coordinates on that axis), passing
through `at` on the other two, with a round anchor plate at each end.

### `voussoirs(G, id_, arch, ring_depth, y_back, thickness, stone=0.32, proud=0.04, fascia=True, keystone=True, key_rise=0.12, key_proud=0.1, joints=True)`

The arch ring: an odd number of wedge voussoirs (about `stone` metres on the face, the count follows the
span) radiating from the arch centre, `ring_depth` deep, standing `proud` in front of a wall `thickness` thick
whose back face is y_back. Optional outer fascia step, a wider keystone rising `key_rise` above the extrados and
`key_proud` prouder, and joint grooves on both faces and the soffit. Returns the joined stones.

### `wedge(a, r0, r1, w0=0.0, w1=0.0)`

(x, z) corners of one stone between radii r0 and r1 about +z, half angle a, widened by w0 / w1 at its ends.

## Named mouldings

`cornice`, `string-course`, `impost`, `plinth`, `coping`

Example: `tools/test_kitparts.py` builds a street-arcade bay and a flanged pipe with a handwheel from these parts.
