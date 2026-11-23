"""Authoring helpers for procgen kit builders.

A kit is a directory of parametric module documents plus a modules.json that
names the kit, the kits it imports and every module's typed ports. Builders
write expressions with ordinary Python operators:

    S = C("span")                     # the control $span
    half = S * 0.5
    z = C("spring") + C("rise") * sin(T * PI)      # T is @t on a curve

Constant sub-expressions are folded; everything else becomes the document's
[operator, left, right] form.

For signage, plaques, friezes and arched inscriptions, prefer `kittype`:

    from kittype import inscribe, arch_inscribe, sign_board, number_plaque, arch_sign_board

Find a helper: python3 procgen/recipes/kitlib.py --helpers <word> [--long]  (or the kit_helpers MCP tool); every
helper also has a `def` line in the generated index inside class Graph (procgen/tools/gen_helper_index.py).

Part helpers: prefer these to hand-building the part (each has a docstring with frame, units and an example).
All are `Graph` methods unless marked as functions; the ornament and aero ones live in their own files and are
attached to `Graph` when kitlib is imported.

  sds / bevel  Graph.crease, Graph.subdivide (Chaikin semi-sharp creases, limit=1, limitNormals=1,
               lodLevels), Graph.support_loops, Graph.loop_cut, Graph.chamfer (segments=3 + profile=0.5),
               Graph.smooth (FWN weighting="areaSq"), Graph.shrinkwrap; cast_housing, sds_cylinder,
               filleted_box, curved_boss_patch in kitparts.py                                     kitlib.py / kitparts.py
  ladders      Graph.ladder (stringers, rungs, brackets, optional safety cage)                      kitlib.py
  gears        Graph.gear (involute, solid/web/spokes), Graph.rack, Graph.ring_gear (internal),
               Graph.gear_segment (sector, external or internal); functions mesh, mesh_rack,
               mesh_internal, segment_phase (centre distance and phase so teeth interleave)        kitlib.py
  ratchets     Graph.ratchet (sawtooth wheel), Graph.pawl; function seat_pawls (pawl placements)    kitlib.py
  ornament     Graph.swell_tube (radius varies along a path), Graph.round_bar, Graph.flat_bar
               (ironwork along curves, curl/leaf ends), Graph.scroll_bracket (wall bracket for a
               lamp or sign); curves volute, c_scroll, s_scroll, whiplash, lift                    kitlib_ornament.py
  aero         Graph.wing (NACA sections, taper, sweep, dihedral, twist; split= for a hinged
               rudder/elevator/aileron: wings, fins, tailplanes), Graph.propeller (twisted blades,
               hub, spinner; wood or metal), Graph.nacelle (round or boxy); naca4                    kitlib_aero.py
  iron         Graph.rolled (I/H, U, L, T, flat, round sections between two points), Graph.truss
               (Warren, Pratt, Howe, lattice, bowstring, pitched Pratt / Howe, fink roof trusses;
               gussets and rivets at every node; X spans and depths with controls=),
               Graph.plate_girder (stiffeners, optional arched soffit), Graph.laced_column,
               Graph.box_member, Graph.gusset_plate, Graph.splice_plate, Graph.base_plate;
               functions iron_section, iron_outline, mitre                                         kitlib_iron.py
  masonry      Graph.arch_ring (real voussoirs: semicircular, segmental, three-centred, pointed,
               flat; keystone, long-and-short, imposts), Graph.ashlar_wall (coursed blocks,
               rustication, openings, quoins), Graph.brick_wall (stretcher, English, Flemish,
               header bonds), Graph.rubble_wall, Graph.coping, Graph.string_course,
               Graph.cutwater_cap; joints="detail" for walls out of reach, "material" for bond
               maps (bond_pattern); spans, rises, walls and openings follow X controls             kitlib_masonry.py
  orders       Graph.column / Graph.pilaster (Tuscan, Doric, Ionic, Corinthian, Composite after
               Vignola), Graph.entablature, Graph.pediment, Graph.balustrade, Graph.surround
               (window/door with sill, keystone, hood), Graph.moulding_run (named mouldings swept
               along an outline with mitres), Graph.turned; functions moulding, profile,
               order_for_height (size an order to a storey: height= on column, pilaster,
               entablature); dimensions take control expressions, pass controls=             kitlib_orders.py
  pipes        Graph.pipe_run (bent runs, flanged/bell/riveted joints), Graph.tee, Graph.reducer,
               Graph.valve (gate, globe, check; handwheel), Graph.supports (stools, hangers, wall
               brackets); function route (orthogonal routing between two points)                  kitlib_pipes.py
  glazing      Graph.glazed_vault (ribs, purlins, bars, panes, end screens, lantern),
               Graph.glazed_dome, Graph.fan_dome, Graph.ridge_lantern, Graph.dome_lantern,
               Graph.glazed_surface (any u,v surface); leaded= for cames                           kitlib_glazing.py
  curves       2D construction (functions on polylines, exact Seg/Arc paths and shapes = outer + holes):
               offset, stroke, offset_path, fillet, chamfer, circle_3p, fillet_arc, tangent lines and
               circles, incircle, union/intersection/difference, intersections, split, resample,
               closest_point, triangulate; Graph.plate (shapes -> closed surface, faces split)        kitlib_curves.py
  tracery      Graph.tracery_arch (cusped lancet), Graph.rosette (trefoil, quatrefoil, multifoil),
               Graph.tracery_window (Y, intersecting, geometric; bar or plate), Graph.rose_window,
               Graph.tracery_railing (Gothic walkway railing); functions pointed_arch, foil_opening  kitlib_tracery.py
  mech         Graph.plummer_block, Graph.bushed_sheave, Graph.clevis_fork, Graph.stuffing_box,
               Graph.arched_yoke, Graph.pierced_cheek; function mech_bore (1.8 mm open-bore SDS/FWN) kitlib_mech.py
"""
import json
import math
import re
from pathlib import Path

PI = math.pi


class X:
    """A numeric expression: a number, "$control", "@field" or [op, ...]."""
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v.v if isinstance(v, X) else v

    def __add__(self, o): return add(self, o)
    def __radd__(self, o): return add(o, self)
    def __sub__(self, o): return sub(self, o)
    def __rsub__(self, o): return sub(o, self)
    def __mul__(self, o): return mul(self, o)
    def __rmul__(self, o): return mul(o, self)
    def __truediv__(self, o): return div(self, o)
    def __rtruediv__(self, o): return div(o, self)
    def __neg__(self): return sub(0, self)
    def __pow__(self, o): return pw(self, o)
    def __rpow__(self, o): return pw(o, self)
    def __abs__(self): return absx(self)

    def _no(self, what):
        raise TypeError(f"{what} on a kitlib expression (it depends on controls, so Python cannot evaluate it): "
                        "use lt(a, b) / gt(a, b) with iff(cond, then, otherwise) for choices, mn / mx for limits, and "
                        "kitlib's sin, cos, sqrt, atan2, pw, absx instead of math.*")
    def __bool__(self): self._no("bool() / if / and / or")
    def __lt__(self, o): self._no("<")
    def __le__(self, o): self._no("<=")
    def __gt__(self, o): self._no(">")
    def __ge__(self, o): self._no(">=")
    def __float__(self): self._no("float() / math.*")


def _v(x):
    return x.v if isinstance(x, X) else x


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _binary(op, fold):
    def f(a, b):
        a, b = _v(a), _v(b)
        return X(fold(a, b)) if _num(a) and _num(b) else X([op, a, b])
    return f


def _unary(op, fold):
    def f(a):
        a = _v(a)
        return X(fold(a)) if _num(a) else X([op, a])
    return f


add = _binary("+", lambda a, b: a + b)
sub = _binary("-", lambda a, b: a - b)
mul = _binary("*", lambda a, b: a * b)
div = _binary("/", lambda a, b: a / b)
mx = _binary("max", max)
mn = _binary("min", min)
pw = _binary("pow", lambda a, b: a ** b)
atan2 = _binary("atan2", math.atan2)
gt = _binary(">", lambda a, b: 1.0 if a > b else 0.0)
lt = _binary("<", lambda a, b: 1.0 if a < b else 0.0)
sin = _unary("sin", math.sin)
cos = _unary("cos", math.cos)
sqrt = _unary("sqrt", math.sqrt)
absx = _unary("abs", abs)
floor = _unary("floor", math.floor)
ceil = _unary("ceil", math.ceil)
rnd = _unary("round", lambda a: float(round(a)))


def iff(cond, then, otherwise):
    return X(["if", _v(cond), _v(then), _v(otherwise)])


def sq(a):
    return mul(a, a)


def C(name):
    return X("$" + name)


T, U, V, INDEX, RANDOM = X("@t"), X("@u"), X("@v"), X("@index"), X("@random")
PX, PY, PZ = X("@x"), X("@y"), X("@z")


def control(value, lo, hi, integer=False):
    c = {"value": value, "min": lo, "max": hi}
    if integer:
        c["integer"] = True
    return c


def port(origin, normal, type_, dimensions=None, up=(0, 0, 1)):
    p = {"origin": list(origin), "normal": list(normal), "up": list(up), "type": type_}
    if dimensions:
        p["dimensions"] = dimensions
    return p


_OPT_ALIASES = {
    "min_angle": "minAngle",
    "clamp_overlap": "clampOverlap",
    "vertex_sharpness": "vertexSharpness",
    "crease_method": "creaseMethod",
    "linear_pre_subdiv": "linearPreSubdiv",
    "tri_subdiv": "triSubdiv",
    "uv_rule": "uvRule",
    "limit_normals": "limitNormals",
    "lod_levels": "lodLevels",
    "lod_distances": "lodDistances",
    "split_creases": "splitCreases",
    "max_distance": "maxDistance",
    "dir_angle": "dirAngle",
    "subdiv_normals": "subdivNormals",
    "tuned_ev": "tunedEV",
    "interpolate_weight": "interpolateWeight",
    "non_uniform": "nonUniform",
    "adaptive_angle": "adaptiveAngle",
    "vertex_weight": "vertexWeight",
    "g2_fair": "g2Fair",
    "g2_weight": "g2Weight",
    "clear_details": "clearDetails",
    "smooth_angle": "smoothAngle",
}


def _norm_sel(sel):
    if not isinstance(sel, dict):
        return sel
    return {_OPT_ALIASES.get(k, k): v for k, v in sel.items() if v is not None}


def _norm_opts(options):
    out = {}
    for k, v in options.items():
        if v is None:
            continue
        nk = _OPT_ALIASES.get(k, k)
        out[nk] = _norm_sel(v) if nk == "selection" else v
    return out


class Graph:
    """Node list of one document, with shorthands for the common patterns."""

    def __init__(self):
        self.nodes, self.ids = [], set()
        self.derived = []   # [name, expression] pairs, written to the document's "derived" list

    def let(self, name, expr):
        """Name a derived value: it is computed once (after the controls) and read as "$name", so an expression
        used in many places is written once instead of copied into every use. Plain numbers come back unchanged.
        Names follow node-id rules; later lets may use earlier ones."""
        if _num(expr):
            return expr
        assert re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name), f"invalid derived name {name}"
        assert all(n != name for n, _ in self.derived), f"duplicate derived value {name}"
        self.derived.append([name, expr])
        return X("$" + name)

    def n(self, id_, type_, inputs=None, **options):
        assert id_ not in self.ids, f"duplicate node id {id_}"
        for bad in ("t", "r", "s", "at"):
            if bad in options and options[bad] is not None:
                raise ValueError(
                    f"node '{id_}' ({type_}): '{bad}=' is not a valid node option and would be silently ignored "
                    f"(placing the mesh at the origin). Wrap the node with G.xf(..., t=..., r=..., s=...) instead."
                )
        if type_ not in ("transform", "instance", "connect"):
            for bad in ("translation", "rotation"):
                if bad in options and options[bad] is not None:
                    raise ValueError(
                        f"node '{id_}' ({type_}): '{bad}=' is only valid on transform/instance/connect. "
                        f"Wrap with G.xf(..., t=..., r=...) instead."
                    )
        self.ids.add(id_)
        d = {"id": id_, "type": type_}
        if inputs:
            d["inputs"] = list(inputs)
        options = {k: v for k, v in options.items() if v is not None}
        if options:
            d["options"] = options
        self.nodes.append(d)
        return id_

    def group(self, id_, alias, **bindings):
        assert id_ not in self.ids, f"duplicate node id {id_}"
        self.ids.add(id_)
        d = {"id": id_, "type": "group", "group": alias}
        if bindings:
            d["bindings"] = bindings
        self.nodes.append(d)
        return id_

    def join(self, id_, *ids):
        return self.n(id_, "join", ids)

    def mat(self, id_, src, slot):
        return self.n(id_, "material", [src], slot=slot)

    def xf(self, id_, src, t=None, r=None, s=None):
        return self.n(id_, "transform", [src], translation=t, rotation=r, scale=s)

    def box(self, id_, size, at):
        self.n(id_ + "-stock", "cube", size=size)
        return self.xf(id_, id_ + "-stock", t=at)

    def rod(self, id_, a, b, radius, sides=8, cap=None):
        return self.n(id_, "sweep", path=[a, b], radius=radius, sides=sides, cap=cap)

    def lathe(self, id_, profile, sides=24, cap=None):
        self.n(id_ + "-turned", "revolve", profile=profile, sides=sides, cap=cap)
        return self.n(id_, "smoothNormals", [id_ + "-turned"])

    def loft(self, id_, sections, segments=0, rings=1, smooth=0, cap="quad", cap_inset=None, closed=0):
        """All-quad solid loft across 2..N closed 2D/3D sections (e.g. square-to-round flanges, flared necks).
        Each section is either [[x,y,z], ...] or {'profile': [[x,y],...], 'z': z, 'scale': s, 't': [x,y,z], 'twist': rad}."""
        return self.n(id_, "loft", sections=sections, segments=segments or None,
                      rings=rings if rings != 1 else None, smooth=smooth or None,
                      cap=cap, capInset=cap_inset, closed=closed or None)

    def pierced_extrude(self, id_, profile, holes, depth, cap="quad", cap_inset=None):
        """Extrude a 2D profile with 1..16 open through-bores (`holes`). When 1 hole matches the outer vertex count
        and `cap='quad'`, emits a 100% all-quad annular solid ready for Chaikin SDS."""
        return self.n(id_, "extrude", profile=profile, holes=holes, depth=depth, cap=cap, capInset=cap_inset)

    def annulus(self, id_, section, sides=24):
        """A ring section [[r, z]...] kept off the axis (every r > 0), turned round z into a closed solid:
        pipe walls, bored flanges and hubs, collars. The section closes itself; give it once round."""
        return self.lathe(id_, list(section) + [section[0]], sides)

    def pipe(self, id_, axis, start, end, outer, bore, at=(0, 0, 0), sides=24):
        """Straight hollow pipe along `axis` ("x", "y" or "z") from `start` to `end` through `at`, open at both
        ends so the bore shows. `outer` and `bore` are radii."""
        self.annulus(id_ + "-wall", [[bore, start], [outer, start], [outer, end], [bore, end]], sides)
        r = {"x": [0, PI / 2, 0], "y": [-PI / 2, 0, 0], "z": None}[axis]
        return self.xf(id_, id_ + "-wall", t=list(at), r=r)

    def tube(self, id_, point, segments, radius, sides=8, closed=0, up=None, twist=None, cap=None):
        self.n(id_ + "-path", "curve", point=point, segments=segments, closed=closed or None)
        return self.n(id_, "tube", [id_ + "-path"], radius=radius, sides=sides, up=up, twist=twist, cap=cap)

    def profile_sweep(self, id_, curve, profile=None, section=None, **options):
        """Sweep a 2D `profile` or rolled `section` (rect|ibeam|channel|angle|tee|cruciform) along `curve`;
        `scale` ([sx, sy] or scalar) and `twist` may use @t, @index, @x, @y, @z."""
        return self.n(id_, "profileSweep", [curve], profile=profile, section=section, **options)

    def curve_fillet(self, id_, curve, radius=0.05, segments=6, corners=None):
        return self.n(id_, "curveFillet", [curve], radius=radius, segments=segments, corners=corners)

    def curve_trim(self, id_, curve, start=0.0, end=1.0, reverse=None):
        return self.n(id_, "curveTrim", [curve], start=start, end=end, reverse=reverse)

    def curve_offset(self, id_, curve, offset, up=None):
        return self.n(id_, "curveOffset", [curve], offset=offset, up=up)

    def sample_curve(self, id_, curve, **options):
        """Sample points along `curve` by arc length: positions=[t...], pitch=..., or count=..."""
        return self.n(id_, "sampleCurve", [curve], **options)

    def instance(self, id_, points, prototypes, **options):
        """Place copies at `points`. `prototypes` may be a single node id or a list of candidate node ids
        picked per point via `pick` (expression in @index, @random, @x, @y, @z) or seeded random (`randomSeed`)."""
        protos = [prototypes] if isinstance(prototypes, str) else list(prototypes)
        return self.n(id_, "instance", [points] + protos, **options)

    def chamfer(self, id_, src, width=0.01, min_angle=30, selection=None, **options):
        """Bevel/fillet sharp edges (FWN or SDS): segments (1=chamfer, 2=support loops at profile=1, 3+=circular fillet at profile=0.5), clampOverlap=1, crease."""
        opts = {"width": width, "minAngle": min_angle}
        if selection is not None:
            opts["selection"] = _norm_sel(selection)
        opts.update(_norm_opts(options))
        return self.n(id_, "chamfer", [src], **opts)

    def support_loops(self, id_, src, width=0.01, min_angle=30, crease=None, selection=None, **options):
        """Insert tight SDS holding/support loops on both sides of sharp edges (all-quad strips, exact silhouette)."""
        opts = {"width": width, "minAngle": min_angle, "segments": 2, "profile": 1.0, "clampOverlap": 1}
        if crease is not None:
            opts["crease"] = crease
        if selection is not None:
            opts["selection"] = _norm_sel(selection)
        opts.update(_norm_opts(options))
        return self.n(id_, "chamfer", [src], **opts)

    def crease(self, id_, src, sharpness=3.0, min_angle=30, selection=None, **options):
        """Tag edges/vertices with semi-sharp SDS crease weights (0..10 OpenSubdiv scale): mode max|set|add, vertexSharpness."""
        opts = {"sharpness": sharpness, "minAngle": min_angle}
        if selection is not None:
            opts["selection"] = _norm_sel(selection)
        opts.update(_norm_opts(options))
        return self.n(id_, "crease", [src], **opts)

    def subdivide(self, id_, src, levels=2, limit=1, limit_normals=1, **options):
        """OpenSubdiv Catmull-Clark SDS: levels, creaseMethod='chaikin'|'uniform', limit=1 (zero shrinkage), limitNormals=1, lodLevels=[2,1,0], lodDistances."""
        opts = {"levels": levels, "limit": limit, "limitNormals": limit_normals}
        opts.update(_norm_opts(options))
        return self.n(id_, "subdivide", [src], **opts)

    def smooth(self, id_, src, angle=45, weighting="areaAngle", split_creases=1, **options):
        """Hard-surface FWN/Phong shading normals: break angle, splitCreases=1, weighting='areaSq' for flat beveled panels."""
        opts = {"angle": angle, "weighting": weighting, "splitCreases": split_creases}
        opts.update(_norm_opts(options))
        return self.n(id_, "smoothNormals", [src], **opts)

    def shrinkwrap(self, id_, src, target, offset=0.0, **options):
        """Project a detail cage onto a curved SDS or target surface: offset, maxDistance, method nearest|project, direction, selection."""
        opts = {"offset": offset}
        opts.update(_norm_opts(options))
        return self.n(id_, "shrinkwrap", [src, target], **opts)

    def transfer_normals(self, id_, src, proxy, max_distance=None, blend=1.0, **options):
        """Transfer smooth interpolated normals from a clean proxy surface onto `src` (eliminates shading ripples around curved holes)."""
        opts = {"blend": blend}
        if max_distance is not None:
            opts["maxDistance"] = max_distance
        opts.update(_norm_opts(options))
        return self.n(id_, "transferNormals", [src, proxy], **opts)

    def bridge_loops(self, id_, src, src2=None, segments=4, smooth=1, tension=0.45, profile_scale=1.0, twist=0):
        """Bridge two open boundary loops (from 1 or 2 meshes) with smooth tangent-matched Hermite quad rings into one watertight solid."""
        ins = [src] if src2 is None else [src, src2]
        return self.n(id_, "bridgeLoops", ins, segments=segments, smooth=smooth, tension=tension,
                      profileScale=profile_scale, twist=twist or None)

    def quad_fill_hole(self, id_, src, bulge=0.0, offset=0, selection=None):
        """Close even-sided boundary loops with zero-pole all-quad Coons-patch grids (Grid Fill)."""
        return self.n(id_, "quadFillHole", [src], bulge=bulge or None, offset=offset or None,
                      selection=_norm_sel(selection) if selection is not None else None)

    def displace_details(self, id_, src, scale=1.0, levels=0, clear_details=1, smooth_angle=55.0):
        """Bake attached 0x8007 surface details (rivets, grooves, lettering, stamps) into real 3D silhouette geometry along analytical normals."""
        return self.n(id_, "displaceDetails", [src], scale=scale, levels=levels or None,
                      clearDetails=clear_details, smoothAngle=smooth_angle)

    def loop_cut(self, id_, src, cuts=None, positions=None, margin=None, selection=None, crease=None, **options):
        """Insert all-quad SDS support/holding loops across quad edge rings by cuts, fractional positions, or metric margin."""
        opts = {}
        if cuts is not None:
            opts["cuts"] = cuts
        if positions is not None:
            opts["positions"] = positions
        if margin is not None:
            opts["margin"] = margin
        if selection is not None:
            opts["selection"] = _norm_sel(selection)
        if crease is not None:
            opts["crease"] = crease
        opts.update(_norm_opts(options))
        return self.n(id_, "loopCut", [src], **opts)

    def face_extrude(self, id_, src, occluder=None, **options):
        """Inset and/or extrude selected faces: offset, inset, scale, individual, capSlot, selection."""
        ins = [src, occluder] if occluder else [src]
        return self.n(id_, "faceExtrude", ins, **options)

    def face_divide(self, id_, src, occluder=None, **options):
        """Subdivide selected faces into storeys or bays: axis='vertical'|'horizontal'|[x,y,z], sizes=[...] or repeat=..., selection=..."""
        ins = [src, occluder] if occluder else [src]
        return self.n(id_, "faceDivide", ins, **options)

    def delete_faces(self, id_, src, selection=None, occluder=None):
        ins = [src, occluder] if occluder else [src]
        return self.n(id_, "deleteFaces", ins, selection=selection)

    def array(self, id_, src, start, step, count, **options):
        """`count` copies of src at start + k * step; options may use @index, @x, @y, @z."""
        self.n(id_ + "-at", "line", start=start, step=step, count=count)
        return self.n(id_, "instance", [id_ + "-at", src], **options)

    def ring(self, id_, src, count, step_angle, start_angle=0):
        """`count` copies of src turned about +Z by start + k * step."""
        return self.array(id_, src, [0, 0, 0], [0, 0, 0], count, rotation=[0, 0, INDEX * step_angle + start_angle])

    # --- surface details: shading-level features lying on a part (docs/DETAILING.md). Detail a part
    # after shaping it: nodes that rebuild the mesh (weld, subdivide, booleans) drop its details.
    def guide(self, id_, a, b):
        """A straight guide from a to b, for a rivet row or a seam."""
        return self.n(id_, "line", start=a, step=[sub(b[i], a[i]) for i in range(3)], count=2)

    def points(self, id_, positions):
        return self.n(id_, "pointArray", positions=positions)

    def field(self, id_, at=None, r=None, **options):
        """Points in a plane: layout grid|hex, spacing, and size=[x, y] or radius (and inner)."""
        self.n(id_ + "-flat", "pointField", **options)
        return self.xf(id_, id_ + "-flat", t=at, r=r)

    def stamp(self, id_, surface, points, **options):
        """One detail at each point: shape disc|ring|box, profile dome|flat, radius, inner, size, height, fillet, slot, tangent."""
        return self.n(id_, "detailStamp", [surface, points], **options)

    def rivets(self, id_, surface, curve, pitch, radius=0.012, height=0.005, **options):
        """Domed heads along the curve at a pitch, half a pitch clear of its ends."""
        return self.n(id_, "rivetLine", [surface, curve], pitch=pitch, radius=radius, height=height, **options)

    def seam(self, id_, surface, curve, width=0.006, depth=0.003, **options):
        """A groove following the curve on the surface."""
        return self.n(id_, "detailSeam", [surface, curve], width=width, depth=depth, **options)

    def outline(self, id_, at=None, r=None, **options):
        """One guide curve in a plane: shape circle|arc|rect|polygon, radius, size, corner, sides, start, sweep."""
        self.n(id_ + "-flat", "outline", **options)
        return self.xf(id_, id_ + "-flat", t=at, r=r)

    def courses(self, id_, at=None, r=None, **options):
        """Joints of coursed work in a rectangle: size=[x, y], course, block, bond, minimum."""
        self.n(id_ + "-flat", "courses", **options)
        return self.xf(id_, id_ + "-flat", t=at, r=r)

    def fasteners(self, id_, surface, guide, head="rivet", **options):
        """Heads at the guide's points, or along its curves when pitch or count is given.
        head rivet|hex|square|cap|stud|slotted|cross, radius, height, washer, turn aligned|random, seed, slot."""
        return self.n(id_, "fastener", [surface, guide], head=head, **options)

    def stroke(self, id_, surface, curve, profile="groove", **options):
        """A stroke along the curve: profile groove|bead|band|lap|mark, width, depth or height, dash=[on, off], slot."""
        return self.n(id_, "detailStroke", [surface, curve], profile=profile, **options)

    def ticks(self, id_, surface, curve, **options):
        """Graduations square to the curve: pitch or count, length, major, majorLength, width, depth or slot, side."""
        return self.n(id_, "detailTicks", [surface, curve], **options)

    def text(self, id_, surface, at, text, **options):
        """Single-stroke figures and capitals anchored at `at`: size, direction, align, valign, profile bead|band|groove|mark, height/depth, slot.
        IMPORTANT: `at` is ALREADY the vertical centre (mid-cap-height, -size/2 .. +size/2) of the line, NOT the baseline.
        Pass the panel's vertical centre directly (do not subtract half the font size), or set valign='baseline'|'top'
        (along `up`, default [0, 0, 1]). Prefer `kittype.inscribe(...)` to centre and auto-fit text inside a panel."""
        if options.get("align") == "center":
            options["align"] = "centre"
        valign = options.pop("valign", "centre")
        up = options.pop("up", (0, 0, 1))
        if valign not in ("centre", "center"):
            sz = options.get("size", 0.1)
            if valign in ("baseline", "bottom"):
                shift = mul(sz, 0.5)
            elif valign == "top":
                shift = mul(sz, -0.5)
            else:
                raise ValueError(f"valign must be centre, baseline or top (got {valign!r})")
            at = [add(at[i], mul(shift, up[i])) if up[i] else at[i] for i in range(3)]
        if options.get("profile") == "groove" and "height" in options and "depth" not in options:
            options["depth"] = absx(options.pop("height"))
        self.n(id_ + "-at", "pointArray", positions=[at])
        return self.n(id_, "detailText", [surface, id_ + "-at"], text=text, **options)

    def close_bolts(self, id_, plate, positions, top, radius, depth=0.04):
        """Holding-down bolts a player can walk up to. The hexagon nuts stay geometry (they show
        against the plate's edge); what would waste triangles is detail: a washer seated on the
        plate under each nut, the nut's turned face and the end of the bolt standing through it.
        `positions` are (x, y) on the plate, `top` the height of its upper face, `radius` the
        nut's corner radius. Returns (plate with washers, nuts); join both into the part.
        Bolts out of reach need no geometry at all: use fasteners(head="hex", washer=...)."""
        self.points(id_ + "-seat-at", [[x, y, top + 0.1] for x, y in positions])
        seated = self.stamp(id_ + "-plate", plate, id_ + "-seat-at", shape="ring", profile="flat", radius=radius * 1.7, inner=radius * 0.8,
                            height=0.004, fillet=0.002)
        self.n(id_ + "-nut-stock", "cylinder", radius=radius, depth=depth, sides=6)
        self.points(id_ + "-nut-top", [[0, 0, depth]])
        self.stamp(id_ + "-nut-faced", id_ + "-nut-stock", id_ + "-nut-top", profile="flat", radius=radius * 0.84, height=0.003, fillet=radius * 0.22)
        self.stamp(id_ + "-nut", id_ + "-nut-faced", id_ + "-nut-top", profile="dome", radius=radius * 0.45, height=radius * 0.22)
        self.points(id_ + "-at", [[x, y, top + depth * 0.5] for x, y in positions])
        nuts = self.n(id_, "instance", [id_ + "-at", id_ + "-nut"])
        return seated, nuts

    def bake(self, id_, src):
        """Merge src into one prototype per material (a module should be a few large meshes)."""
        return self.n(id_, "bake", [src])

    def when(self, id_, condition, src):
        if "none" not in self.ids:
            self.n("none", "empty")
        return self.n(id_, "select", [src, "none"], condition=condition)

    def layer(self, stage_min, *ids):
        """Assign node(s) to Stewart Brand pace layer `stage_min` (active whenever `$stage >= stage_min`):
          0 = site / plinth / port anchors
          1 = primary bearing skeleton (must hold together on its own under `pgm audit --sweep`)
          2 = enclosure, roof planes & sash/door joinery
          3 = fittings, hardware, surface fasteners & kittype inscriptions"""
        assert ids, "layer() needs at least one node id"
        stage_i = int(stage_min)
        if not hasattr(self, "_pace_layers"):
            self._pace_layers = {}
        self._pace_layers.setdefault(stage_i, []).extend(ids)
        return ids[0] if len(ids) == 1 else self.join(f"layer{stage_i}-{len(self._pace_layers[stage_i])}", *ids)

    def provisional(self, stage_lo, stage_hi, *ids):
        """Register temporary construction / falsework / centering geometry present ONLY while `stage_lo <= $stage <= stage_hi`."""
        assert ids, "provisional() needs at least one node id"
        lo_i, hi_i = int(stage_lo), int(stage_hi)
        assert lo_i <= hi_i, "provisional requires stage_lo <= stage_hi"
        if not hasattr(self, "_provisionals"):
            self._provisionals = []
        self._provisionals.append((lo_i, hi_i, list(ids)))
        return ids[0] if len(ids) == 1 else self.join(f"prov-{lo_i}-{hi_i}-{len(self._provisionals)}", *ids)

    def staged(self, id_, control_name="stage", always=()):
        """Assemble all `G.layer(...)` and `G.provisional(...)` registrations into a single staged output gated by `$stage`."""
        layers = getattr(self, "_pace_layers", {})
        provs = getattr(self, "_provisionals", [])
        assert layers or provs or always, "staged() called with no layers, provisionals or always nodes"
        stg = C(control_name)
        parts = list(always)
        for s_min in sorted(layers):
            grp_ids = layers[s_min]
            grp = grp_ids[0] if len(grp_ids) == 1 else self.join(f"{id_}-L{s_min}", *grp_ids)
            if s_min <= 0:
                parts.append(grp)
            else:
                parts.append(self.when(f"{id_}-gate-L{s_min}", gt(stg, s_min - 0.5), grp))
        for k, (lo_i, hi_i, p_ids) in enumerate(provs):
            pgrp = p_ids[0] if len(p_ids) == 1 else self.join(f"{id_}-P{k}", *p_ids)
            cond = mn(gt(stg, lo_i - 0.5), lt(stg, hi_i + 0.5))
            parts.append(self.when(f"{id_}-gate-P{k}", cond, pgrp))
        return parts[0] if len(parts) == 1 else self.join(id_, *parts)

    # --- common parts (Built by Opus 5.5 (medium), 2026-11-16). Each returns one node id; every member of a
    # part overlaps its neighbours, so `pgm audit` sees one piece. Tests: recipes/test_kitlib_helpers.py.
    def ladder(self, id_, height, at=(0, 0, 0), facing="+y", width=0.45, pitch=0.3, standoff=0.2,
               stringer="flat", stringer_size=(0.065, 0.012), stringer_radius=0.018, rung_radius=0.012,
               handhold=1.1, brackets=1.8, cage=False, cage_from=2.4, hoop_pitch=0.9, cage_depth=0.7,
               cage_radius=0.35, straps=5, slot=None):
        """A fixed iron ladder on a wall. Metres, Z-up.

        Frame: `at` is the point on the wall face at the foot, under the ladder's centre line; `facing` is
        the wall's outward normal (the way the climber's back faces): "+x", "-x", "+y", "-y" or an angle in
        radians from +x. Stringers stand `standoff` out from the wall (wall face to rung centres), `width`
        apart centre to centre, from the foot to `height` + `handhold` (the hand-holds above the landing).
        `height` (number or X) is the landing height above the foot: the rung count is round(height /
        pitch) (at least 1) and the rungs are spaced evenly so the top rung is level with the landing.
        stringer "flat" (bar `stringer_size` = (depth from the wall, thickness)) or "round" (`stringer_radius`).
        brackets: interval between wall stays (None for none); each stay is a flat bar from a wall plate
        to the stringer, from 0.3 m up to the landing. cage: hoops from `cage_from` to the top every
        about `hoop_pitch`, reaching `cage_depth` behind the rungs with radius `cage_radius`, tied by
        `straps` vertical flat bars; dropped when the ladder is too short for it.
        Example: g.ladder("ladder", C("height"), at=[1.0, 0, 0], facing="-x", cage=True)"""
        H = X(height)
        w, s, rr = width, standoff, rung_radius
        flat = stringer == "flat"
        assert flat or stringer == "round", "stringer is flat or round"
        d, t = stringer_size
        half = t / 2 if flat else stringer_radius          # stringer half thickness across the ladder
        top = H + handhold                                  # top of the stringers
        parts = []
        for side, sx in (("l", -w / 2), ("r", w / 2)):
            if flat:
                self.n(f"{id_}-stringer-{side}-stock", "cube", size=[t, d, top])
                parts.append(self.xf(f"{id_}-stringer-{side}", f"{id_}-stringer-{side}-stock", t=[sx, s, top * 0.5]))
            else:
                parts.append(self.n(f"{id_}-stringer-{side}", "sweep", path=[[sx, s, 0], [sx, s, top]],
                                    radius=stringer_radius, sides=10))
        # Rungs run through both stringers (ends flush with their outer faces, or past the round bars' centres).
        n = mx(1, rnd(H / pitch))
        rise = H / n
        self.n(f"{id_}-rung-stock", "cylinder", radius=rr, depth=w + (t if flat else stringer_radius), sides=8)
        self.xf(f"{id_}-rung", f"{id_}-rung-stock", r=[0, PI / 2, 0])
        parts.append(self.array(f"{id_}-rungs", f"{id_}-rung", [0, s, rise], [0, 0, rise], n))
        if brackets:
            bt, bh = 0.01, 0.05
            nb = mx(2, ceil((H - 0.3) / brackets) + 1)
            step = (H - 0.3) / (nb - 1)
            for side, sign in (("l", -1), ("r", 1)):
                bx = sign * (w / 2 + half + bt / 2 - (0.004 if flat else 0.4 * half))     # reaching into the stringer
                self.box(f"{id_}-stay-{side}", [bt, s, bh], [bx, s / 2, 0])
                self.box(f"{id_}-plate-{side}", [0.09, 0.012, 0.14], [bx + sign * 0.02, 0.006, 0])
                self.join(f"{id_}-bracket-{side}", f"{id_}-stay-{side}", f"{id_}-plate-{side}")
                parts.append(self.array(f"{id_}-brackets-{side}", f"{id_}-bracket-{side}", [0, 0, 0.3], [0, 0, step], nb))
        if cage:
            Rc, ys = cage_radius, s + cage_depth - cage_radius        # hoop radius and arc centre
            out = w / 2 + half + 0.04
            arc = [[Rc * math.cos(PI - PI * k / 12), ys + Rc * math.sin(PI - PI * k / 12), 0] for k in range(13)]
            path = [[-w / 2, s, 0], [-out, s, 0]] + arc + [[out, s, 0], [w / 2, s, 0]]
            if Rc <= out + 0.02:                                       # narrow cage: no flare
                path = [[-w / 2, s, 0]] + arc + [[w / 2, s, 0]]
            self.n(f"{id_}-hoop", "sweep", path=path, radius=0.01, sides=6)
            z0, z1 = cage_from, top - 0.03
            nh = mx(2, ceil((z1 - z0) / hoop_pitch) + 1)
            hoops = self.array(f"{id_}-hoops", f"{id_}-hoop", [0, 0, z0], [0, 0, (z1 - z0) / (nh - 1)], nh)
            straps_ = []
            for k in range(straps):
                a = PI - PI * k / max(1, straps - 1)
                straps_.append(self.n(f"{id_}-strap-{k}-stock", "cube", size=[0.04, 0.008, z1 - z0]))
                straps_[-1] = self.xf(f"{id_}-strap-{k}", straps_[-1], t=[Rc * math.cos(a), ys + Rc * math.sin(a), (z0 + z1) * 0.5],
                                      r=[0, 0, a - PI / 2])
            self.join(f"{id_}-cage-all", hoops, *straps_)
            parts.append(self.when(f"{id_}-cage", gt(z1 - z0, 0.6), f"{id_}-cage-all"))
        body = self.join(f"{id_}-parts", *parts)
        if slot:
            body = self.mat(f"{id_}-slot", body, slot)
        return self.xf(id_, body, t=list(at), r=[0, 0, _facing(facing) - PI / 2])

    def gear(self, id_, teeth, module, face, bore=None, pressure_angle=20.0, backlash=0.05, body="solid",
             hub=0.0, hub_radius=None, web=None, rim=1.6, holes=None, spoke_width=None, resolution=4,
             at=None, phase=0.0, slot=None):
        """An involute spur gear: one closed, manifold, outward-oriented solid. Metres, Z-up.

        Frame: the gear axis is Z through `at` (default the origin); the face runs from z = -face/2 to
        +face/2 (a hub boss stands `hub` beyond each face). Tooth 0 points along +x, turned by `phase`
        (radians, about Z). teeth: Python int (8 and up). module (number or X): pitch diameter =
        teeth * module; addendum 1.0 m, dedendum 1.25 m. The flanks are cut by simulating the standard
        basic rack (tip radius 0.38 m) rolling on the pitch circle, so the root fillet is the true
        trochoid and pinions under 17 teeth come out undercut as cut gears do (and still mesh).
        backlash: circular thinning of each tooth at the pitch circle, in modules (0.05: a meshed pair
        has 0.1 m of play). bore: shaft radius (default 0.16 * pitch radius). body "solid", "web"
        (thin web, `holes` round lightening holes; by default
        the count near 6 that divides the teeth evenly) or "spokes" (`holes` openings between spokes of
        `spoke_width`, default a fifth of the web's span); web: web thickness (default 0.35 face);
        rim: rim depth under the root circle in modules; hub_radius default 1.8 bore. face, bore, hub,
        hub_radius, web and spoke_width may be X too (checks are skipped then). resolution: flank
        segments (4: a solid 40-tooth gear is about 1,900 triangles).
        Why a `surface` node: extrude and profileSolid stop at 96 / 128 profile corners, a 40-tooth
        outline has ~500; the surface node takes the whole shell with shared vertices (closed by
        construction) and tessellates each tooth's cap and each web sector with the evaluator's own
        polygon triangulator.
        Example: g.gear("wheel", 40, C("module"), 0.08, body="spokes", hub=0.03)"""
        assert isinstance(teeth, int) and teeth >= 8, "gear teeth: a Python int, at least 8"
        N, m, w = teeth, X(module), X(face)
        R = N / 2.0
        alpha = math.radians(pressure_angle)
        rf, ra = R - 1.25, R + 1.0
        if bore is None:
            bore = m * (0.16 * R)
        bore = X(bore)
        rh = X(hub_radius) if hub_radius is not None else bore * 1.8
        e = X(hub)
        webbed = body in ("web", "spokes")
        assert webbed or body == "solid", "gear body is solid, web or spokes"
        nf = max(2, int(resolution))
        rs = [rf + 0.03 + (ra - rf - 0.03) * (k / nf) ** 1.5 for k in range(nf + 1)]
        psi = _rack_cut_half_angles(N, alpha, backlash, rs)
        assert psi[-1] > 0.02 / ra, f"gear {N} teeth: pointed teeth (raise teeth or lower pressure angle)"
        verts, faces = [], []

        def vtx(x, y, z):
            verts.append([x, y, z])
            return len(verts) - 1

        def ring(radius, z, count, offset):
            return [vtx(radius * math.cos(offset + 2 * PI * i / count), radius * math.sin(offset + 2 * PI * i / count), z)
                    for i in range(count)]

        # Unit-module outline of one tooth sector, from gap centre to gap centre, counter-clockwise.
        gap = PI / N
        unit = [(rf, -gap)] + [(r, -p) for r, p in zip(rs, psi)] + [(r, p) for r, p in reversed(list(zip(rs, psi)))] + [(rf, gap)]
        q = max(1, -(-32 // N))                       # cap inner ring points per tooth
        if webbed:
            # Sectors must start on ring vertices: by default take the hole count (near 6) that divides the ring.
            K = int(holes) if holes else next((k for k in (6, 5, 8, 4, 7, 9, 3) if (N * q) % k == 0), 6)
            assert K >= 3, "a web or spoked gear needs at least 3 holes or openings"
            while (N * q) % K:
                q += 1
        M = N * q
        has_hub = not (_num(e.v) and e.v == 0)
        # The caps stop at the rim's inner edge (web, spokes), at the hub boss, or at the bore.
        inner_r = m * (rf - rim) if webbed else (rh if has_hub else bore)
        _check_gear(N, m, w, bore, rh, inner_r, e, webbed, web, rf)
        top, bot = w * 0.5, w * -0.5
        outer_t, outer_b = [], []
        for k in range(N):
            th = 2 * PI * k / N
            for r, a in unit[:-1]:
                x, y = m * (r * math.cos(th + a)), m * (r * math.sin(th + a))
                outer_t.append(vtx(x, y, top))
                outer_b.append(vtx(x, y, bot))
        P = len(unit) - 1                              # outline points per tooth (the end gap centre is the next tooth's)
        # Rings of the body, from the top cap's inner edge round to the bottom cap's: (radius, z, count, web follows).
        off = -gap
        hub_m = M
        if webbed:
            hub_m = K * max(1, -(-24 // K))
            tw = X(web) if web is not None else w * 0.35
            rr = [(inner_r, top, M, False), (inner_r, tw * 0.5, M, True), (rh, tw * 0.5, hub_m, False),
                  (rh, top + e, hub_m, False), (bore, top + e, hub_m, False), (bore, bot - e, hub_m, False),
                  (rh, bot - e, hub_m, False), (rh, tw * -0.5, hub_m, True), (inner_r, tw * -0.5, M, False),
                  (inner_r, bot, M, False)]
        elif not has_hub:
            rr = [(bore, top, M, False), (bore, bot, M, False)]
        else:
            rr = [(rh, top, M, False), (rh, top + e, M, False), (bore, top + e, M, False), (bore, bot - e, M, False),
                  (rh, bot - e, M, False), (rh, bot, M, False)]
        rings = [ring(r, z, c, off) for r, z, c, _ in rr]
        # Caps: one polygon per tooth sector (outline, then the inner ring back).
        for k in range(N):
            o = [outer_t[(k * P + j) % (N * P)] for j in range(P + 1)]
            inner = [rings[0][(k * q + j) % M] for j in range(q, -1, -1)]
            faces.append(o + inner)
            ob = [outer_b[(k * P + j) % (N * P)] for j in range(P + 1)]
            ib = [rings[-1][(k * q + j) % M] for j in range(q, -1, -1)]
            faces.append(list(reversed(ob + ib)))
        for i in range(N * P):                         # tooth flanks, tips and roots
            j = (i + 1) % (N * P)
            faces.append([outer_b[i], outer_b[j], outer_t[j], outer_t[i]])
        hole_loops = []
        for a in range(len(rr) - 1):
            A, B = rings[a], rings[a + 1]
            if not rr[a][3]:                           # a band of quads between rings of equal count
                for i in range(len(A)):
                    j = (i + 1) % len(A)
                    faces.append([A[i], A[j], B[j], B[i]])
                continue
            # A flat web between rings of different counts: one polygon per sector, each with its opening.
            ca, cb = len(A) // K, len(B) // K
            z = rr[a][1]
            loops = []
            for sct in range(K):
                outline = [A[(sct * ca + j) % len(A)] for j in range(ca + 1)] + [B[(sct * cb + j) % len(B)] for j in range(cb, -1, -1)]
                pts = _opening(body, rh, inner_r, off + 2 * PI * sct / K, 2 * PI / K, spoke_width, nf)
                hole = [vtx(x, y, z) for x, y in pts]
                loops.append(hole)
                faces.append([outline, list(reversed(hole)) if a == 1 else hole])
            hole_loops.append(loops)
        if webbed:
            for T, Bl in zip(*hole_loops):             # opening walls, top web to bottom web
                for i in range(len(T)):
                    j = (i + 1) % len(T)
                    faces.append([T[i], T[j], Bl[j], Bl[i]])
        part = self.n(id_ + "-shell", "surface", vertices=verts, faces=faces)
        if slot:
            part = self.mat(id_ + "-slot", part, slot)
        return self.xf(id_, part, t=list(at) if at is not None else None, r=[0, 0, phase] if _v(phase) != 0 else None)

    def rack(self, id_, teeth, module, face, back=2.0, pressure_angle=20.0, backlash=0.05, at=None, slot=None):
        """A straight involute rack (the basic rack: straight flanks at the pressure angle, addendum
        1.0 m, dedendum 1.25 m, root fillet 0.38 m) that meshes with gear() of the same module. One
        closed solid. Metres, Z-up.
        Frame: in the XY plane like gear(): the pitch line is the X axis from x = 0 to teeth * pi *
        module, the teeth point +y (tips at y = module), the back is `back` modules below the root;
        the face runs from z = -face/2 to +face/2. The ends are cut at tooth-gap centres (tooth k is
        centred at x = (k + 0.5) * pi * module). teeth: Python int; module and face may be X.
        Example: g.rack("rack", 20, 0.01, 0.04); then mesh_rack(12, 0.01, x) seats a pinion on it."""
        assert isinstance(teeth, int) and teeth >= 1, "rack teeth: a Python int"
        m, w = X(module), X(face)
        alpha = math.radians(pressure_angle)
        ta, rho, hf = math.tan(alpha), 0.38, 1.25
        x0 = PI / 4 - backlash / 2                     # half tooth width at the pitch line, in modules
        yl = -hf + rho - rho * math.sin(alpha)         # where the root fillet meets the flank
        cx = x0 - yl * ta + rho * math.cos(alpha)      # fillet centre, from the tooth centre
        right = [(PI / 2, -hf)]                        # one side of a tooth, gap centre to tip
        for k in range(4):
            a = 1.5 * PI - (1.5 * PI - (PI + alpha)) * k / 4
            right.append((cx + rho * math.cos(a), -hf + rho + rho * math.sin(a)))
        right += [(x0 - yl * ta, yl), (x0 - ta, 1.0)]
        tooth = right + [(-x, y) for x, y in reversed(right)]      # decreasing x, gap centre to gap centre
        verts, faces = [], []

        def vtx(x, y, z):
            verts.append([x, y, z])
            return len(verts) - 1

        p, yb = PI, -hf - back
        front, rear = w * 0.5, w * -0.5
        # Boundary, counter-clockwise: the back from x = 0 to the far end, then the teeth back to x = 0.
        loop = [(p * k, yb) for k in range(teeth + 1)]
        for k in range(teeth - 1, -1, -1):
            loop += [(p * (k + 0.5) + x, y) for x, y in (tooth if k == teeth - 1 else tooth[1:])]
        F = [vtx(m * x, m * y, front) for x, y in loop]
        B = [vtx(m * x, m * y, rear) for x, y in loop]
        T = len(tooth) - 1
        for k in range(teeth):                         # one front and one back polygon per tooth
            first = teeth + 1 + (teeth - 1 - k) * T     # loop index of the gap centre at x = (k + 1) p
            chain = [first + j for j in range(T + 1)]
            if k == 0:
                chain[-1] = len(loop) - 1
            ids = [k, k + 1] + chain
            faces.append([F[i] for i in ids])
            faces.append([B[i] for i in reversed(ids)])
        for i in range(len(loop)):
            j = (i + 1) % len(loop)
            faces.append([B[i], B[j], F[j], F[i]])
        part = self.n(id_ + "-shell", "surface", vertices=verts, faces=faces)
        if slot:
            part = self.mat(id_ + "-slot", part, slot)
        return self.xf(id_, part, t=list(at) if at is not None else None)

    # --- ring gears, segments, ratchets and pawls (Built by Opus 5.5 (medium), 2026-11-16). Each is one
    # closed `surface` shell made by _band() / _plate(); tests in recipes/test_kitlib_helpers.py.
    def ring_gear(self, id_, teeth, module, face, rim=2.0, pinion=12, pressure_angle=20.0, backlash=0.05, resolution=4,
                  at=None, phase=0.0, slot=None):
        """An internal (ring) gear: involute teeth on the inside of a full ring. One closed solid. Metres, Z-up.

        Frame: axis Z through `at`, face from z = -face/2 to +face/2; tooth 0 is centred on +x, turned by
        `phase` (radians). teeth: Python int (30 and up; a pinion inside wants at least 12 fewer). module
        (number or X): pitch diameter = teeth * module. The teeth point inwards: roots at R + 1.25 m, tips
        at R - 1.0 m, raised where needed (to ~R - 0.6 m) so the ring's tips leave the smallest pinion it
        runs with, `pinion` teeth, before its involute ends (no fouling of a rack-cut pinion's root) and
        stay outside the base circle; the ring is `rim` modules deep outside the root circle. Flanks are the exact involute
        of the base circle (an internal tooth is the space of an external gear); the root corner is left
        sharp, with no shaper-cut fillet. backlash: circular thinning at the pitch circle, in modules.
        face may be X. Seat a pinion with mesh_internal(). resolution: flank segments.
        Example: g.ring_gear("ring", 72, 0.01, 0.04, rim=3)"""
        assert isinstance(teeth, int) and teeth >= 30, "ring gear teeth: a Python int, at least 30"
        unit = _internal_tooth(teeth, math.radians(pressure_angle), backlash, max(2, int(resolution)) + 1, pinion)
        m = X(module)
        _check_band(m, face, rim)
        return self._gear_band(id_, teeth, m, face, unit, m * (teeth / 2.0 + 1.25 + rim), True, -PI / teeth, teeth, True,
                               at, phase, slot)

    def gear_segment(self, id_, teeth, module, face, start=0.0, end=None, count=None, internal=False, inner=None,
                     rim=2.0, pinion=12, pressure_angle=20.0, backlash=0.05, resolution=4, at=None, slot=None):
        """A toothed segment (sector or quadrant gear): an arc of gear() or, internal=True, of ring_gear(),
        with flat closed ends. One closed solid. Metres, Z-up.

        Frame: axis Z through `at`, face from z = -face/2 to +face/2. teeth is the tooth count of the
        whole wheel (it sets the pitch radius, teeth * module / 2); the segment runs counter-clockwise from
        angle `start` (radians from +x) and carries `count` teeth, or as many whole teeth as fit before
        `end`. Both ends are cut at tooth-gap centres; tooth k is centred at start + (k + 0.5) * 2pi/teeth,
        so the segment meshes like the whole wheel at phase segment_phase(teeth, start). External: the
        body runs from the root circle down to radius `inner` (metres, may be X; default `rim` modules
        under the root; a small radius makes a fan). Internal: the body runs `rim` modules outside the
        roots; `pinion` as for ring_gear(). Tooth forms as gear() / ring_gear(); module and face may be X.
        Example: g.gear_segment("quadrant", 64, 0.01, 0.03, start=0, end=PI / 2, inner=0.04)"""
        assert isinstance(teeth, int) and teeth >= (30 if internal else 8), "segment teeth: a Python int (8 up, internal 30 up)"
        pitch = 2 * PI / teeth
        if count is None:
            assert end is not None, "gear_segment needs end or count"
            count = int(math.floor((end - start) / pitch + 1e-9))
        assert isinstance(count, int) and 1 <= count < teeth, "segment tooth count: 1 to teeth - 1"
        alpha, nf = math.radians(pressure_angle), max(2, int(resolution))
        m = X(module)
        R = teeth / 2.0
        if internal:
            unit = _internal_tooth(teeth, alpha, backlash, nf + 1, pinion)
            plain = m * (R + 1.25 + rim)
        else:
            rf, ra = R - 1.25, R + 1.0
            rs = [rf + 0.03 + (ra - rf - 0.03) * (k / nf) ** 1.5 for k in range(nf + 1)]
            psi = _rack_cut_half_angles(teeth, alpha, backlash, rs)
            assert psi[-1] > 0.02 / ra, f"segment {teeth} teeth: pointed teeth"
            gap = PI / teeth
            unit = [(rf, -gap)] + [(r, -p) for r, p in zip(rs, psi)] + [(r, p) for r, p in reversed(list(zip(rs, psi)))] + [(rf, gap)]
            plain = X(inner) if inner is not None else m * (rf - rim)
            if _num(plain.v) and _num(m.v):
                assert 0 < plain.v < m.v * (rf - 0.3), f"segment inner radius {plain.v:.3f} must lie inside the root"
        _check_band(m, face, rim)
        return self._gear_band(id_, teeth, m, face, unit, plain, internal, start, count, False, at, 0.0, slot)

    def _gear_band(self, id_, N, m, face, unit, plain_r, internal, start, count, closed, at, phase, slot):
        """Teeth (unit outline, in modules about each tooth centre) on one edge of a band, a plain arc of
        radius plain_r on the other; `count` teeth from angle `start`, the full ring when closed."""
        p = 2 * PI / N
        q = max(1, -(-32 // N))                        # plain-edge points per tooth
        toothed, plain = [], []
        for k in range(count):
            th = start + (k + 0.5) * p
            toothed += [(m * (r * math.cos(th + a)), m * (r * math.sin(th + a))) for r, a in unit[:-1]]
            plain += [(plain_r * math.cos(start + (k + j / q) * p), plain_r * math.sin(start + (k + j / q) * p)) for j in range(q)]
        if not closed:
            r, a = unit[-1]
            th = start + (count - 0.5) * p
            toothed.append((m * (r * math.cos(th + a)), m * (r * math.sin(th + a))))
            plain.append((plain_r * math.cos(start + count * p), plain_r * math.sin(start + count * p)))
        P = len(unit) - 1
        bt, bp = [k * P for k in range(count + 1)], [k * q for k in range(count + 1)]
        w = X(face)
        top, bot = w * 0.5, w * -0.5
        if internal:
            verts, faces = _band([(toothed, top), (toothed, bot)], plain, bt, bp, top, bot, closed)
        else:
            verts, faces = _band([(plain, top), (plain, bot)], toothed, bp, bt, top, bot, closed)
        part = self.n(id_ + "-shell", "surface", vertices=verts, faces=faces)
        if slot:
            part = self.mat(id_ + "-slot", part, slot)
        return self.xf(id_, part, t=list(at) if at is not None else None, r=[0, 0, phase] if _v(phase) != 0 else None)

    def ratchet(self, id_, teeth, radius, depth, face, bore=None, hub=0.0, hub_radius=None, land=0.08, free="ccw",
                at=None, phase=0.0, slot=None):
        """A ratchet wheel: asymmetric sawtooth teeth, each a radial drop face and a straight sloped back.
        One closed solid. Metres, Z-up.

        Frame: axis Z through `at`, face from z = -face/2 to +face/2 (a hub boss stands `hub` beyond each
        face). radius: tip radius; depth: drop-face height (roots at radius - depth). Drop face k lies on
        the radius at angle phase + k * 2pi/teeth. free "ccw": the wheel turns freely counter-clockwise
        seen from +Z and the drop faces face clockwise, so pawls hold it against clockwise turning; "cw"
        is the mirror image. land: flat at each tip, as a fraction of the pitch. bore: shaft radius
        (default 0.2 radius); hub_radius default 1.8 bore. radius, depth, face, bore, hub and hub_radius
        may be X. Seat pawls on it with seat_pawls().
        Example: g.ratchet("wheel", 24, 0.12, 0.015, 0.03, hub=0.01)"""
        assert isinstance(teeth, int) and teeth >= 6, "ratchet teeth: a Python int, at least 6"
        assert free in ("ccw", "cw"), "ratchet free is ccw or cw"
        assert 0 <= land < 0.4, "ratchet land: a fraction of the pitch, under 0.4"
        ro, h = X(radius), X(depth)
        rr = ro - h
        bore = X(bore) if bore is not None else ro * 0.2
        rh = X(hub_radius) if hub_radius is not None else bore * 1.8
        e, w = X(hub), X(face)
        has_hub = not (_num(e.v) and e.v == 0)
        vals = [_v(x) for x in (ro, h, bore, rh, e, w)]
        if all(_num(v) for v in vals):
            ro_, h_, b_, rh_, e_, w_ = vals
            assert ro_ > 0 and w_ > 0 and 0 < h_ < ro_ * 0.5 and e_ >= 0, "ratchet radius, depth and face must be positive"
            assert b_ > 0 and (rh_ if has_hub else b_) < (ro_ - h_) * 0.9 and (not has_hub or b_ < rh_), \
                f"ratchet {teeth}: bore {b_:.3f} / hub {rh_:.3f} must clear the roots ({ro_ - h_:.3f})"
        p = 2 * PI / teeth
        # One tooth, counter-clockwise, as (radius, angle): root at a drop face, tip, tip land; the back runs
        # straight from the land to the next tooth's first point.
        if free == "ccw":
            unit, a0 = [(rr, 0.0), (ro, 0.0), (ro, land * p)], 0.0
        else:
            unit, a0 = [(rr, -p), (ro, -land * p), (ro, 0.0)], -p
        q = max(1, -(-24 // teeth))
        outer, angles = [], []
        for k in range(teeth):
            th = phase + k * p
            outer += [(r * math.cos(th + a), r * math.sin(th + a)) for r, a in unit]
            angles += [th + a0 + j * p / q for j in range(q)]
        top, bot = w * 0.5, w * -0.5
        ring = lambda r: [(r * math.cos(a), r * math.sin(a)) for a in angles]
        if has_hub:
            layers = [(ring(rh), top), (ring(rh), top + e), (ring(bore), top + e), (ring(bore), bot - e),
                      (ring(rh), bot - e), (ring(rh), bot)]
        else:
            layers = [(ring(bore), top), (ring(bore), bot)]
        verts, faces = _band(layers, outer, [k * q for k in range(teeth + 1)], [k * 3 for k in range(teeth + 1)], top, bot, True)
        part = self.n(id_ + "-shell", "surface", vertices=verts, faces=faces)
        if slot:
            part = self.mat(id_ + "-slot", part, slot)
        return self.xf(id_, part, t=list(at) if at is not None else None)

    def pawl(self, id_, length, depth, face, beak=None, width=None, boss=None, bore=None, free="ccw",
             at=None, rotation=0.0, slot=None):
        """A ratchet pawl: a round pivot boss, a straight arm and a beak whose flat face bears on a drop
        face. One closed solid. Metres, Z-up.

        Frame (before `at`, then `rotation` about Z): the pivot is the origin and the arm runs along +x; the
        arm's upper edge is the line y = 0 (it lies on the wheel's tip tangent when seated); the beak face
        is the plane x = length, `depth` deep, the beak on the +y side for free "ccw" (the wheel's side) or
        the -y side for "cw"; its back slopes from the face's foot to y = 0 at x = length - beak. width:
        arm depth (default 0.14 length); boss: pivot boss radius (default 1.3 width); bore: pin hole radius
        (default 0.45 boss, 0 for none). face may be X; the others are numbers. seat_pawls() gives
        length, depth, beak and free for a ratchet, and the placements.
        Example: places, kw = seat_pawls(24, 0.12, 0.015, 0.1); g.pawl("pawl", face=0.03, at=places[0][0], rotation=places[0][1], **kw)"""
        L, d = float(length), float(depth)
        aw = float(width) if width is not None else 0.14 * L
        bR = float(boss) if boss is not None else 1.3 * aw
        b = float(beak) if beak is not None else 1.6 * d
        hole = float(bore) if bore is not None else 0.45 * bR
        assert free in ("ccw", "cw"), "pawl free is ccw or cw"
        assert 0 < aw < bR and bR < L - b and 0 < d and 0 <= hole < bR * 0.8, \
            f"pawl: boss {bR:.3f}, beak {b:.3f} and length {L:.3f} do not fit"
        # Outline, counter-clockwise: arm underside, beak face, beak back, arm top, boss arc round to the underside.
        xb = math.sqrt(bR * bR - aw * aw)
        pts = [(xb, -aw), (L, -aw), (L, d), (L - b, 0.0)]
        a1 = 2 * PI - math.asin(aw / bR)
        pts += [(bR * math.cos(a1 * j / 16), bR * math.sin(a1 * j / 16)) for j in range(16)]
        holes = [(hole * math.cos(2 * PI * j / 12), hole * math.sin(2 * PI * j / 12)) for j in range(12)] if hole > 0 else None
        if free == "cw":                                # mirror in y, keeping the loops counter-clockwise
            pts = [(x, -y) for x, y in reversed(pts)]
            holes = [(x, -y) for x, y in reversed(holes)] if holes else None
        w = X(face)
        verts, faces = _plate(pts, holes, w * 0.5, w * -0.5)
        part = self.n(id_ + "-shell", "surface", vertices=verts, faces=faces)
        if slot:
            part = self.mat(id_ + "-slot", part, slot)
        return self.xf(id_, part, t=list(at) if at is not None else None, r=[0, 0, rotation] if _v(rotation) != 0 else None)

    # BEGIN GENERATED HELPER INDEX (procgen/tools/gen_helper_index.py; do not edit by hand)
    # Every part helper, so `grep "def "` on this file finds them all. Query them with
    #     python3 procgen/recipes/kitlib.py --helpers <word> [--long]
    # Module-level helper functions (import from the named module, or from kitlib where it re-exports):
# def volute(turns=2.0, r_start=0.12, r_end=0.015, kind='log', hand='left', start=(0.0, 0.0), heading=0.0, lead=0.0, per_turn=40)  # kitlib_ornament.py (also Graph.volute) — A volute: a spiral from radius r_start in to its eye at r_end, `turns` turns.
# def c_scroll(height=0.3, eyes=0.02, turns=1.25, spine=0.35, belly=3.0, hand='left', start=(0.0, 0.0), heading=0.0, per_turn=40)  # kitlib_ornament.py (also Graph.c_scroll) — A C-scroll: two volutes curling to the same side, joined by a flatter spine.
# def s_scroll(height=0.4, eyes=0.02, turns=1.25, waist=1.2, hand='left', start=(0.0, 0.0), heading=0.0, per_turn=40)  # kitlib_ornament.py (also Graph.s_scroll) — An S-scroll: two volutes curling to opposite sides, joined by an inflected waist.
# def whiplash(length=1.0, sway=0.1, recoil=0.8, recoil_radius=None, eye=None, hand='left', snap=True, start=(0.0, 0.0), heading=0.0, per_turn=40)  # kitlib_ornament.py (also Graph.whiplash) — Horta's whiplash (coup de fouet): a long S that quickens, then snaps back in a tight recoil.
# def lift(points, plane='xz', origin=(0.0, 0.0, 0.0))  # kitlib_ornament.py (also Graph.lift) — 3D points of a plane curve: plane "xz" (x -> x, y -> z; normal -y), "xy" (normal +z) or "yz" (x -> y, y -> z; normal +x), offset by origin.
# def naca4_parameters(code)  # kitlib_aero.py — (camber m, camber position p, thickness t) as chord fractions from a NACA 4-digit code: "2412", 2412, or a tuple (m, p, t) passed through (t may be...
# def naca4(code='0012', points=16, x0=0.0, x1=1.0)  # kitlib_aero.py — A NACA 4-digit section as one closed loop of (x, y) in chord units: leading edge at (0, 0), trailing edge at (1, 0), y up on the suction side.
# def iron_section(spec)  # kitlib_iron.py (also Graph.iron_section) — A section as a dict of its kind and sizes in metres.
# def iron_outline(spec, sides=12)  # kitlib_iron.py (also Graph.iron_outline) — The section's outline: a counter-clockwise loop of (u, w) in metres.
# def mitre(d_in, d_out)  # kitlib_iron.py (also Graph.mitre) — Normal of the mitre plane where a member arriving along d_in meets one leaving along d_out (the plane halving the bend); pass it as an end of rolle...
# def qmax(a, b)  # kitlib_iron.py — (no docstring)
# def qmin(a, b)  # kitlib_iron.py — (no docstring)
# def evaluate(e, values)  # kitlib_masonry.py — An expression's value with controls `values` ({name: number or control dict}); X, _D or a tree.
# def arch_geometry(span, kind='semicircular', rise=None, count=None, voussoir=0.4, key=True, key_scale=1.2, length=0.45, short=None, extrados='plain', course=None, level=None, key_rise=0.0, beta=1.0471975511965976, centre_depth=None, fill_notches=False, skewback=0.12)  # kitlib_masonry.py — The voussoirs of an arch ring as 2D polygons, built from nothing (arch_ring and the walls use it).
# def courses_for(height, course)  # kitlib_masonry.py — Course lines [0, ..., height]: course a number (equal courses as near that height as fit) or a list of heights from the bottom (repeated; the last...
# def bond_pattern(bond, brick=(0.215, 0.1025, 0.065), joint=None, course=0.35, block=None, shift=0.5)  # masonry_bonds.py — One natural repeat cell of a bond: the single source of truth for brick_wall / ashlar_wall head joints and for the engine's bond maps (cell() repea...
# def cell(name)  # masonry_bonds.py — The square repeat cell of a bond-map slot (see the module docstring).
# def moulding(kind, height, x0, x1=None, z0=0.0, segments=8, depth=None)  # kitlib_orders.py (also Graph.moulding) — Points (x, z) of one moulding from its bottom to its top.
# def profile(steps, segments=8, z0=0.0)  # kitlib_orders.py (also Graph.profile) — Compose a Profile from named mouldings stacked bottom to top.
# def square(half, half_y=None)  # kitlib_orders.py (also Graph.square) — Counter-clockwise rectangle outline centred on the origin (half widths).
# def order(name)  # kitlib_orders.py (also Graph.order) — Vignola's numbers for an order (modules, module = lower radius): column, base, capital, shaft, entablature, architrave, frieze, cornice, top (top r...
# def order_for_height(name, height)  # kitlib_orders.py (also Graph.order_for_height) — An order sized to a storey (Built by Opus 5.5 (medium)): column and entablature together fill `height` (number or X), the entablature a quarter of...
# def entablature_profile(name, crown=True, segments=8)  # kitlib_orders.py (also Graph.entablature_profile) — The whole entablature of an order as one Profile in modules (x from the architrave's lower face, z from its bottom), tagged architrave, frieze, cor...
# def flange_dims(diameter)  # kitlib_pipes.py — Flange proportions for a pipe of outside diameter D (metres, number): dict with radius (flange outer), thickness, neck, bolt_radius (bolt circle),...
# def route(start, end, order='xyz', start_dir=None, end_dir=None, lead=None, wall=None, standoff=0.3, height=None, bend_radius=None)  # kitlib_pipes.py — Orthogonal (Manhattan) route for pipe_run: a polyline from start to end along the axes.
# def sample(curve, tol=0.0015, closed=None)  # kitlib_curves.py — Points of a path (arcs to sagitta tol) or a polyline.
# def length(curve, closed=False)  # kitlib_curves.py — Arc length of a path (exact) or polyline.
# def reverse(curve)  # kitlib_curves.py — (no docstring)
# def point_at(curve, s, closed=False)  # kitlib_curves.py — (point, unit tangent) at arc length s along a path or polyline.
# def resample(curve, count=None, step=None, closed=False)  # kitlib_curves.py — Points at equal arc length along a path (arcs measured exactly) or polyline: `count` points (open: both ends included; closed: the ring) or as many...
# def closest_point(curve, p, closed=False)  # kitlib_curves.py — (point, distance, s): the nearest point of a path (exact on arcs) or polyline to p and its arc length.
# def area(ring)  # kitlib_curves.py — Signed area (counter-clockwise > 0).
# def shapes_area(shapes)  # kitlib_curves.py — (no docstring)
# def orient(ring, ccw=True)  # kitlib_curves.py — (no docstring)
# def bbox(points)  # kitlib_curves.py — (no docstring)
# def winding(rings, p)  # kitlib_curves.py — Winding number of p in oriented rings.
# def contains(region, p)  # kitlib_curves.py — Whether p is inside a ring, shape or shapes (nonzero winding).
# def simplify(ring, closed=True, eps=1e-10)  # kitlib_curves.py — Drop repeated and collinear vertices (and zero-width spikes).
# def circle(c, r, tol=0.0015, start=0.0)  # kitlib_curves.py — Counter-clockwise ring of a circle (vertices on it, sagitta <= tol).
# def rect(x0, y0, x1, y1)  # kitlib_curves.py — (no docstring)
# def transform(shapes, rotate=0.0, translate=(0.0, 0.0), scale=1.0)  # kitlib_curves.py — Shapes (or one ring) turned by rotate radians about the origin, scaled, then moved.
# def circle_3p(a, b, c)  # kitlib_curves.py — (centre, radius) of the circle through three points.
# def fillet_arc(a, v, b, r)  # kitlib_curves.py — Arc of radius r tangent to the lines v-a and v-b (the fillet of corner v on a path a -> v -> b).
# def circles_tangent_to_lines(p1, d1, p2, d2, r)  # kitlib_curves.py — Centres of the circles of radius r tangent to the lines (point p1, direction d1) and (p2, d2): four when the lines cross, one (the mid line) when t...
# def circle_tangent_lines(c1, r1, c2, r2)  # kitlib_curves.py — Common tangents of two circles: {"outer": [(p1, p2), ...], "inner": [...]}, p1 on the first circle and p2 on the second; outer tangents keep both c...
# def point_tangents(p, c, r)  # kitlib_curves.py — Tangent points on the circle (c, r) of the two lines from p (none when p is inside).
# def incircle(a, b, c)  # kitlib_curves.py — (centre, radius) of the circle inscribed in a triangle.
# def perpendicular(p, a, b)  # kitlib_curves.py — Foot of the perpendicular from p onto the line a-b.
# def bisector(v, a, b)  # kitlib_curves.py — Unit direction from v bisecting the angle a-v-b.
# def perp_bisector(a, b)  # kitlib_curves.py — (midpoint, unit direction) of the perpendicular bisector of a-b.
# def intersect_lines(a, b, c, d, segments=True, eps=1e-12)  # kitlib_curves.py — Crossing of a-b with c-d (segments, or infinite lines): [] or [point]; collinear overlapping segments give the overlap's end points.
# def intersect_line_circle(a, b, c, r, segment=True, eps=1e-12)  # kitlib_curves.py — Points where a-b (segment, or infinite line) meets the circle (c, r).
# def intersect_circles(c1, r1, c2, r2)  # kitlib_curves.py — Points where two circles meet (one when they touch).
# def intersect(e1, e2)  # kitlib_curves.py — Exact intersection points of two Seg/Arc pieces.
# def intersections(a, b=None, closed_a=False, closed_b=False, tol=0.0015)  # kitlib_curves.py — Crossings of two polylines (paths are sampled) as (point, s_a, s_b), arc lengths along each, in order along a.
# def split(curve, cutters=(), closed=False, at=(), tol=0.0015)  # kitlib_curves.py — Open polylines of a polyline (or sampled path) cut where it crosses the cutters and at the given arc lengths, in order.
# def fillet(poly, radius, at=None, closed=True, tension=None, tol=0.0015)  # kitlib_curves.py — Round the corners of a polyline: at=None for every corner, a list of vertex indices or a predicate on the index.
# def chamfer(poly, dist, at=None, closed=True)  # kitlib_curves.py — Cut the corners of a polyline back by dist along each edge (at most half an edge).
# def offset(poly, d, closed=True, join='mitre', mitre_limit=2.0, tol=0.0015)  # kitlib_curves.py — Offset of a polyline or path.
# def stroke(curve, width, closed=False, join='round', cap='butt', mitre_limit=2.0, tol=0.0015)  # kitlib_curves.py — The band of `width` centred on a polyline or path, as shapes: a flat bar seen square to its face.
# def offset_shapes(shapes, d, join='mitre', mitre_limit=2.0, tol=0.0015)  # kitlib_curves.py — Shapes grown by d (shrunk for d < 0); holes shrink as the outline grows.
# def offset_path(path, d, closed=None, join='mitre')  # kitlib_curves.py — Exact offset d to the left of a path of lines and arcs: lines stay lines, arcs concentric arcs.
# def union(*regions)  # kitlib_curves.py — Union of any number of rings / paths / shapes, as shapes.
# def clean(rings)  # kitlib_curves.py — Region where a list of raw rings (taken as drawn, not re-oriented) winds positively.
# def intersection(a, b)  # kitlib_curves.py — (no docstring)
# def difference(a, b)  # kitlib_curves.py — (no docstring)
# def triangulate(outer, holes=())  # kitlib_curves.py — Triangles (index triples into outer + holes, in that order) covering a polygon with holes, counter-clockwise.
# def solid(shapes, depth, plane='xz', centre=True, at=(0.0, 0.0, 0.0))  # kitlib_curves.py — (vertices, faces) of shapes extruded by depth: triangle caps and quad walls, outward.
# def pointed_arch(span, fraction=1.0, spring=0.0, x=0.0)  # kitlib_tracery.py — Path from the left springer over the apex to the right springer (two arcs).
# def apex(span, fraction=1.0, spring=0.0)  # kitlib_tracery.py — (no docstring)
# def lancet(span, spring, fraction=1.0, sill=0.0, x=0.0)  # kitlib_tracery.py — Closed counter-clockwise path of a lancet light: sill, jambs to the springing, pointed head.
# def lancet_inset(span, spring, fraction, sill, x, d)  # kitlib_tracery.py — The lancet moved in by d all round (exact: the head keeps its centres, radius less d).
# def cusped_head(span, radius, foils=3, cusp=0.8, tol=0.0015)  # kitlib_tracery.py — Opening of a cusped pointed head in its own frame: springers at (+-span/2, 0), arch radius `radius` (centres on y = 0), apex up; open below y = 0 (...
# def cusped_opening(cell, mid, span, radius, up=(0.0, 1.0), foils=3, cusp=0.8, tol=0.0015)  # kitlib_tracery.py — Cusp the head of an opening `cell` (shapes): its arch springs from the two points span/2 either side of `mid` on the line square to `up` (the way t...
# def foil_opening(c, radius, foils=4, cusp=0.8, rotate=1.5707963267948966, tol=0.0015)  # kitlib_tracery.py — A multifoil opening (3 trefoil, 4 quatrefoil, 5 cinquefoil...): `foils` lobes tangent inside the circle (c, radius), one centred toward `rotate`; c...
# def arch_shapes(span, spring, fraction=1.0, bar=0.03, foils=0, cusp=0.8, frame=None, style='bar', tol=0.0015)  # kitlib_tracery.py — One lancet light framed by a bar (frame width `frame`, default 1.5 bar), cusped when foils > 0.
# def rosette_shapes(radius, foils=4, bar=0.03, cusp=0.8, rotate=1.5707963267948966, frame=None, style='bar', tol=0.0015)  # kitlib_tracery.py — A foiled circle.
# def window_shapes(span, spring, lights=2, kind='y', fraction=1.0, bar=0.03, foils=3, head_foils=4, cusp=0.8, light_fraction=1.0, frame=None, style='bar', tol=0.0015)  # kitlib_tracery.py — A traceried window, sill at y = 0, springing at `spring`, under a pointed arch of span and fraction.
# def rose_shapes(radius, spokes=12, bar=0.03, hub=0.28, spring=0.6, fraction=1.0, foils=3, hub_foils=6, oculus_foils=4, cusp=0.8, frame=None, style='bar', tol=0.0015)  # kitlib_tracery.py — A rose window centred on the origin: outer ring (radius = its centre line), a hub ring (hub x radius) with a foiled opening, `spokes` radial bars t...
# def railing_shapes(length, height, bays=1, arches=4, bar=0.025, post=0.06, rail=0.05, fraction=1.0, foils=3, roundel=0.34, roundel_foils=4, cusp=0.8, kind='arcade', tol=0.0015)  # kitlib_tracery.py — The pierced panel of a Gothic iron railing, `bays` bays of `length` between post centres, from y = 0 to height: post bands, bottom and top rails, a...
# def clamp_to_wall(coord, half_wall, half_part=0.0, margin=0.04)  # kitlib_arch.py — Clamp a horizontal placement coordinate `coord` inside `[-half_wall + half_part + margin, +half_wall - half_part - margin]` using kitlib `mx`/`mn`...
# def mech_bore(shaft_r, clearance=0.0018)  # kitlib_mech.py — Return an open bearing or fastener bore radius `shaft_r + clearance` (default +1.8 mm) that simultaneously satisfies `pgm audit` (<= 3.0 mm gap) an...
    # Attached Graph methods: forwarding stubs; the named module replaces each one when kitlib is imported.
    def swell_tube(self, *args, **kw):  # kitlib_ornament.py — A tube whose radius swells and tapers along its path: levers, organic posts, tendrils.
        """See kitlib_ornament.swell_tube(self, id_, path, profile=0.02, sides=12, aspect=1.0, ends='round', plane='xz', segments=48, up=None, smooth=True, at=None, rotate=None, scale=None, slot=None)."""
        import kitlib_ornament; return kitlib_ornament.swell_tube(self, *args, **kw)
    def round_bar(self, *args, **kw):  # kitlib_ornament.py — A round iron bar along a path, tapering from `radius` to radius * taper.
        """See kitlib_ornament.round_bar(self, id_, path, radius, taper=1.0, start=None, end=None, sides=10, plane='xz', segments=48, curl_turns=0.8, curl_radius=None, leaf=None, at=None, rotate=None, scale=None, slot=None)."""
        import kitlib_ornament; return kitlib_ornament.round_bar(self, *args, **kw)
    def flat_bar(self, *args, **kw):  # kitlib_ornament.py — A flat iron bar (rectangular section) along a path, bent the smith's way.
        """See kitlib_ornament.flat_bar(self, id_, path, width, thickness, start=None, end=None, plane='xz', segments=48, curl_turns=0.8, curl_radius=None, leaf=None, at=None, rotate=None, scale=None, slot=None)."""
        import kitlib_ornament; return kitlib_ornament.flat_bar(self, *args, **kw)
    def scroll_bracket(self, *args, **kw):  # kitlib_ornament.py — A wrought-iron wall bracket for a hanging lamp or sign.
        """See kitlib_ornament.scroll_bracket(self, id_, reach=0.8, drop=None, support='whiplash', at=(0.0, 0.0, 0.0), facing='+x', arm=(0.016, 0.034), bar=(0.022, 0.009), plate=(0.11, 0.012), ring=0.028, filler=True, scale=None, slot=None)."""
        import kitlib_ornament; return kitlib_ornament.scroll_bracket(self, *args, **kw)
    def wing(self, *args, **kw):  # kitlib_aero.py — A lofted wing or fin panel from root to tip: one closed solid, or two with `split`.
        """See kitlib_aero.wing(self, id_, span, root_chord, tip_chord, sweep=0.0, dihedral=0.0, twist=0.0, section='0012', tip_section=None, points=16, stations=4, tip_round=0.0, round_steps=4, split=None, gap=0.012, hinges=3, at=None, r=None, slot=None, control_slot=None, smooth=True)."""
        import kitlib_aero; return kitlib_aero.wing(self, *args, **kw)
    def propeller(self, *args, **kw):  # kitlib_aero.py — A propeller: hub (and spinner) with N blades sunk into it; closed shells in one piece.
        """See kitlib_aero.propeller(self, id_, diameter, pitch, blades=None, style='wood', turn='ccw', hub_radius=None, hub_length=None, chord=None, spinner=None, points=7, stations=7, sides=20, at=None, r=None, slot=None, hub_slot='steel', smooth=True)."""
        import kitlib_aero; return kitlib_aero.propeller(self, *args, **kw)
    def nacelle(self, *args, **kw):  # kitlib_aero.py — An engine nacelle or cowling, front face at z = 0.
        """See kitlib_aero.nacelle(self, id_, length, radius, nose=None, tail=None, tail_radius=None, front_radius=None, sides=24, at=None, r=None, slot=None, style='round', height=0.85, corner=0.35, panels=4, grille=True, exhausts=3, detail_slot=None)."""
        import kitlib_aero; return kitlib_aero.nacelle(self, *args, **kw)
    def rolled(self, *args, **kw):  # kitlib_iron.py — A rolled section swept straight from point a to point b: one closed prism. [takes controls=]
        """See kitlib_iron.rolled(self, id_, section, a, b, up=(0, 0, 1), roll=0.0, offset=(0.0, 0.0), ends=(None, None), sides=12, slot=None, controls=None)."""
        import kitlib_iron; return kitlib_iron.rolled(self, *args, **kw)
    def truss(self, *args, **kw):  # kitlib_iron.py — A riveted plane truss: chords and web of paired sections with a gusset plate at every node. [takes controls=]
        """See kitlib_iron.truss(self, id_, span, depth, panels=None, kind='pratt', chord='L80x10', web=None, vertical=None, gusset=0.012, rise='parabolic', rivets=True, pitch=0.075, rivet=None, at=(0, 0, 0), yaw=0.0, slot=None, panel_pitch=None, controls=None)."""
        import kitlib_iron; return kitlib_iron.truss(self, *args, **kw)
    def plate_girder(self, *args, **kw):  # kitlib_iron.py — A riveted plate girder: web plate, four flange angles, cover plates, stiffeners and rivet rows. [takes controls=]
        """See kitlib_iron.plate_girder(self, id_, span, depth, web=0.01, flange='L100x12', cover=(0.3, 0.012), stiffener='L80x10', stiffener_pitch=1.2, bearing='L100x12', bearing_at=0.25, soffit=0.0, segments=10, rivets=True, pitch=0.1, rivet=None, sole=True, at=(0, 0, 0), yaw=0.0, slot=None, controls=None)."""
        import kitlib_iron; return kitlib_iron.plate_girder(self, *args, **kw)
    def laced_column(self, *args, **kw):  # kitlib_iron.py — A built-up column or strut of angles tied by lacing bars, batten plates or packings. [takes controls=]
        """See kitlib_iron.laced_column(self, id_, a, b, width=0.36, depth=None, angle='L80x10', angles=4, lacing='FL50x8', system='single', incline=0.95, tie=None, batten_pitch=0.9, up=None, rivets=True, rivet=None, slot=None, controls=None)."""
        import kitlib_iron; return kitlib_iron.laced_column(self, *args, **kw)
    def box_member(self, *args, **kw):  # kitlib_iron.py — A box section built of plates and angles: two web plates (the u faces), two cover plates (the w faces, `overhang` past the webs) and four angles in... [takes controls=]
        """See kitlib_iron.box_member(self, id_, a, b, width=0.36, depth=0.36, plate=0.012, angle='L70x9', overhang=0.02, up=(0, 0, 1), rivets=True, pitch=0.12, rivet=None, slot=None, controls=None)."""
        import kitlib_iron; return kitlib_iron.box_member(self, *args, **kw)
    def gusset_plate(self, *args, **kw):  # kitlib_iron.py — A gusset plate at a node, sized to the members meeting there. [takes controls=]
        """See kitlib_iron.gusset_plate(self, id_, at, members, normal=(0, 1, 0), x_dir=(1, 0, 0), thickness=0.012, reach=0.25, edge=0.03, flush=None, rivets=True, pitch=0.075, rivet=None, slot=None, controls=None)."""
        import kitlib_iron; return kitlib_iron.gusset_plate(self, *args, **kw)
    def splice_plate(self, *args, **kw):  # kitlib_iron.py — A cover (splice) plate over a butt joint on a member's face, riveted on both sides of the joint.
        """See kitlib_iron.splice_plate(self, id_, at, along, normal, length=0.4, width=0.2, thickness=0.012, rows=2, pitch=0.07, gap=0.05, sink=0.001, rivets=True, rivet=None, slot=None)."""
        import kitlib_iron; return kitlib_iron.splice_plate(self, *args, **kw)
    def base_plate(self, *args, **kw):  # kitlib_iron.py — A column base plate with anchor bolts.
        """See kitlib_iron.base_plate(self, id_, at=(0, 0, 0), size=(0.6, 0.6), thickness=0.03, bolts=4, inset=0.07, nut=0.024, close=True, slot=None)."""
        import kitlib_iron; return kitlib_iron.base_plate(self, *args, **kw)
    def arch_ring(self, *args, **kw):  # kitlib_masonry.py — A free-standing arch ring of voussoirs (in a wall, use an ashlar_wall or brick_wall opening). [takes controls=]
        """See kitlib_masonry.arch_ring(self, id_, span, kind='semicircular', rise=None, at=None, depth=0.45, voussoir=0.4, count=None, length=0.45, short=None, key=True, key_scale=1.2, key_proud=0.0, key_rise=0.0, extrados='plain', course=0.35, level=None, impost=None, joints='gap', gap=0.002, rustic='smooth', chamfer=0.02, arris=0.0, beta=1.0471975511965976, centre_depth=None, slot='stone', key_slot=None, impost_slot=None, fill_notches=False, controls=None, material_slot=None)."""
        import kitlib_masonry; return kitlib_masonry.arch_ring(self, *args, **kw)
    def ashlar_wall(self, *args, **kw):  # kitlib_masonry.py — A wall panel of coursed ashlar. [takes controls=]
        """See kitlib_masonry.ashlar_wall(self, id_, width, height, at=None, depth=0.5, course=0.35, block=None, bond=0.5, vary=0.0, seed=1, rustic='smooth', chamfer=0.02, arris=0.0, openings=(), quoins=None, quoin=(0.75, 0.45), quoin_proud=0.0, quoin_rustic=None, quoin_slot=None, joints='gap', gap=0.002, bed=0.25, slot='stone', core_slot=None, trim_slot=None, arch=None, min_block=None, controls=None, material_slot=None)."""
        import kitlib_masonry; return kitlib_masonry.ashlar_wall(self, *args, **kw)
    def brick_wall(self, *args, **kw):  # kitlib_masonry.py — A brick face in a bond. [takes controls=]
        """See kitlib_masonry.brick_wall(self, id_, width, height, at=None, depth=0.33, bond='stretcher', brick=(0.215, 0.1025, 0.065), joint=0.01, openings=(), soldiers=(), joints='gap', recess=0.012, slot='brick', mortar_slot='concrete', trim_slot=None, arch=None, controls=None, material_slot=None)."""
        import kitlib_masonry; return kitlib_masonry.brick_wall(self, *args, **kw)
    def rubble_wall(self, *args, **kw):  # kitlib_masonry.py — A rubble wall or battered toe of irregular stones. [takes controls=]
        """See kitlib_masonry.rubble_wall(self, id_, width, height, at=None, depth=0.6, kind='random', stone=0.35, joint=0.02, batter=0.0, seed=3, joints='gap', recess=0.035, bulge=0.025, slot='stone', mortar_slot='concrete', controls=None)."""
        import kitlib_masonry; return kitlib_masonry.rubble_wall(self, *args, **kw)
    def coping(self, *args, **kw):  # kitlib_masonry.py — A coping along a wall top. [takes controls=]
        """See kitlib_masonry.coping(self, id_, length, width, kind='weathered', height=0.15, overhang=0.05, fall=0.04, bead=0.04, stone=0.9, joints='gap', gap=0.002, drip=True, at=None, slot='stone', controls=None)."""
        import kitlib_masonry; return kitlib_masonry.coping(self, *args, **kw)
    def string_course(self, *args, **kw):  # kitlib_masonry.py — A string course (a projecting band) along a wall face. [takes controls=]
        """See kitlib_masonry.string_course(self, id_, length, kind='weathered', projection=0.08, height=0.2, embed=0.1, stone=0.9, joints='gap', gap=0.002, drip=True, at=None, slot='stone', controls=None)."""
        import kitlib_masonry; return kitlib_masonry.string_course(self, *args, **kw)
    def cutwater_cap(self, *args, **kw):  # kitlib_masonry.py — The weathered cap on a pier's cutwater (the pointed or rounded nose that parts the stream).
        """See kitlib_masonry.cutwater_cap(self, id_, width, nose=None, kind='pointed', thickness=0.12, rise=0.35, overhang=0.06, at=None, slot='stone')."""
        import kitlib_masonry; return kitlib_masonry.cutwater_cap(self, *args, **kw)
    def moulding_run(self, *args, **kw):  # kitlib_orders.py — A moulding swept along an outline with true mitres at every corner: cornices, string courses, plinth courses, architraves, rails. [takes controls=]
        """See kitlib_orders.moulding_run(self, id_, prof, outline, closed=False, ends='return', z=0.0, up=(0, 0, 1), back=None, smooth=True, segments=8, slot=None, at=None, rotate=None, scale=None, module=1, controls=None)."""
        import kitlib_orders; return kitlib_orders.moulding_run(self, *args, **kw)
    def turned(self, *args, **kw):  # kitlib_orders.py — A lathe-turned piece from a profile whose x is the radius: column bases, astragals, balusters, finials.
        """See kitlib_orders.turned(self, id_, prof, sides=32, smooth=True, segments=8, base=None, top=None, slot=None, at=None, scale=None)."""
        import kitlib_orders; return kitlib_orders.turned(self, *args, **kw)
    def moulded_block(self, *args, **kw):  # kitlib_orders.py — A block whose sides carry a moulding: the plan outline (counter-clockwise (x, y) points) offset outward by the profile's x at each height z. [takes controls=]
        """See kitlib_orders.moulded_block(self, id_, outline, prof, smooth=True, segments=8, slot=None, at=None, rotate=None, scale=None, module=1, height=None, controls=None)."""
        import kitlib_orders; return kitlib_orders.moulded_block(self, *args, **kw)
    def column(self, *args, **kw):  # kitlib_orders.py — A column of an order by Vignola's rule: plinth and base, shaft with entasis, astragal and capital.
        """See kitlib_orders.column(self, id_, name='doric', module=0.25, at=None, rotate=None, flutes=None, base=None, resolution=1.0, sides=None, slot=None, height=None)."""
        import kitlib_orders; return kitlib_orders.column(self, *args, **kw)
    def pilaster(self, *args, **kw):  # kitlib_orders.py — A pilaster of an order: an engaged rectangular pier with the column's base, height and capital.
        """See kitlib_orders.pilaster(self, id_, name='doric', module=0.25, at=None, facing='-y', projection=0.5, width=2.0, flutes=None, resolution=1.0, slot=None, height=None)."""
        import kitlib_orders; return kitlib_orders.pilaster(self, *args, **kw)
    def entablature(self, *args, **kw):  # kitlib_orders.py — The entablature of an order along an outline: architrave (fasciae), frieze (plain, or Doric triglyphs and metopes) and cornice (Doric mutules, Ioni... [takes controls=]
        """See kitlib_orders.entablature(self, id_, name='doric', module=0.25, outline=((0, 0), (4, 0)), closed=False, ends='return', z=None, depth=None, crown=True, blocks=True, slot=None, height=None, controls=None)."""
        import kitlib_orders; return kitlib_orders.entablature(self, *args, **kw)
    def pediment(self, *args, **kw):  # kitlib_orders.py — A pediment over an entablature: the horizontal cornice (without its sima), raking cornices mitred at the apex (or one segmental arc) cut plumb at t... [takes controls=]
        """See kitlib_orders.pediment(self, id_, span, name='ionic', module=0.25, prof=None, kind='triangular', rise=None, at=None, facing='-y', depth=None, slot=None, controls=None)."""
        import kitlib_orders; return kitlib_orders.pediment(self, *args, **kw)
    def balustrade(self, *args, **kw):  # kitlib_orders.py — A balustrade along an open plan outline: moulded plinth, turned balusters (a vase with one belly), a moulded handrail and square piers at the ends,... [takes controls=]
        """See kitlib_orders.balustrade(self, id_, outline, height=0.95, at=None, pier_every=3.0, pitch=None, sides=16, slot=None, controls=None)."""
        import kitlib_orders; return kitlib_orders.balustrade(self, *args, **kw)
    def surround(self, *args, **kw):  # kitlib_orders.py — A window or door surround on a wall: architrave of two fasciae and a cyma round the opening (mitred at the head, cut level at the foot), a moulded... [takes controls=]
        """See kitlib_orders.surround(self, id_, width, height, at=None, facing='-y', band=None, sill=True, keystone=True, hood=None, slot=None, controls=None)."""
        import kitlib_orders; return kitlib_orders.surround(self, *args, **kw)
    def pipe_run(self, *args, **kw):  # kitlib_pipes.py — A pipe along a polyline, every corner bent at `bend_radius` (torus segments, not mitres).
        """See kitlib_pipes.pipe_run(self, id_, points, diameter, bend_radius=None, sides=16, bend_steps=8, ends='flange', joints='flange', pipe_length=3.66, bend_joints=True, bolts='hex', lagging=None, bands=None, up=(0, 0, 1), slot=None, fitting_slot=None, smooth=True)."""
        import kitlib_pipes; return kitlib_pipes.pipe_run(self, *args, **kw)
    def tee(self, *args, **kw):  # kitlib_pipes.py — A flanged cast-iron tee (or cross) set into a run: the run passes through the body, which is flanged to it on both sides (flange pairs with nuts);...
        """See kitlib_pipes.tee(self, id_, run, at, branch=(0, 0, 1), length=None, diameter=None, cross=False, end='flange', bolts='hex', slot=None)."""
        import kitlib_pipes; return kitlib_pipes.tee(self, *args, **kw)
    def reducer(self, *args, **kw):  # kitlib_pipes.py — A flanged concentric reducer (taper): one lathe with a flange at each end, nuts on the flange backs.
        """See kitlib_pipes.reducer(self, id_, at, d2, direction=None, d1=None, length=None, bolts='hex', slot=None)."""
        import kitlib_pipes; return kitlib_pipes.reducer(self, *args, **kw)
    def valve(self, *args, **kw):  # kitlib_pipes.py — A flanged valve set into a run, flanged to it on both sides (flange pairs with nuts).
        """See kitlib_pipes.valve(self, id_, run, at, kind='gate', stem=(0, 0, 1), wheel=None, spokes=5, rising=False, bolts='hex', slot=None, wheel_slot=None)."""
        import kitlib_pipes; return kitlib_pipes.valve(self, *args, **kw)
    def supports(self, *args, **kw):  # kitlib_pipes.py — Pipe supports along a run, each reaching 2 mm into the pipe and stopping on a plane.
        """See kitlib_pipes.supports(self, id_, run, kind='stool', spacing=3.0, plane=None, at=None, start=None, end=None, clearance=0.08, width=None, slot=None)."""
        import kitlib_pipes; return kitlib_pipes.supports(self, *args, **kw)
    def glazed_surface(self, *args, **kw):  # kitlib_glazing.py — Glazing over a parametric surface: members on chosen u and v lines, a pane in every cell. [takes controls=]
        """See kitlib_glazing.glazed_surface(self, id_, point, u_lines, v_lines, divisions=(1, 1), closed_u=False, open=None, gauge=1.0, sizes=None, glass=0.008, clearance=0.0015, lift=0.0015, lap=0.002, flip=False, controls=None, slot='iron', glass_slot='glass', leaded=None, frame=None, panes=True, proud=None, cap_skip=None, cap_taper=(False, False))."""
        import kitlib_glazing; return kitlib_glazing.glazed_surface(self, *args, **kw)
    def glazed_vault(self, *args, **kw):  # kitlib_glazing.py — A glazed barrel vault over a rectangle: main arched I ribs at a bay spacing, purlins along the length, T glazing bars on arch lines between the rib... [takes controls=]
        """See kitlib_glazing.glazed_vault(self, id_, span, length, rise=None, section='semicircular', bays=None, bay=4.0, bar_pitch=0.6, purlins=None, purlin_pitch=1.6, pane_length=0.9, ends=1.0, end_screens=(False, False), screen_pitch=0.9, screen_transoms=2, lantern=None, springing=None, spring=0.0, proud=0.12, wall_plate=None, gauge=1.0, controls=None, slot='iron', roof_slot='slate', glass_slot='glass', leaded=None)."""
        import kitlib_glazing; return kitlib_glazing.glazed_vault(self, *args, **kw)
    def glazed_dome(self, *args, **kw):  # kitlib_glazing.py — A glazed dome on a ring: meridian I ribs, horizontal I rings, T glazing bars between the ribs, panes lapped every pane_length, a crown ring with a... [takes controls=]
        """See kitlib_glazing.glazed_dome(self, id_, radius, rise=None, profile='hemisphere', ribs=8, bars=3, rings=None, ring_pitch=1.4, pane_length=0.8, crown=None, lantern=True, oculus=False, taper=True, min_pane=0.3, at=(0, 0, 0), proud=0.12, gauge=1.0, controls=None, slot='iron', glass_slot='glass', leaded=None)."""
        import kitlib_glazing; return kitlib_glazing.glazed_dome(self, *args, **kw)
    def fan_dome(self, *args, **kw):  # kitlib_glazing.py — An art-deco fan-ribbed (sunburst) dome: from `fans` springer points on the base ring, fan_ribs I ribs splay upward in a fan (the outer two deep I r... [takes controls=]
        """See kitlib_glazing.fan_dome(self, id_, radius, rise=None, profile='hemisphere', fans=8, fan_ribs=5, gap_bars=1, spread=0.6, waist=0.15, rings=None, ring_pitch=1.4, pane_length=0.8, crown=None, lantern=True, oculus=False, at=(0, 0, 0), proud=0.12, gauge=1.0, controls=None, slot='iron', glass_slot='glass', leaded=None)."""
        import kitlib_glazing; return kitlib_glazing.fan_dome(self, *args, **kw)
    def dome_lantern(self, *args, **kw):  # kitlib_glazing.py — A crown lantern: a polygonal glazed drum (flat panes, light I posts at the corners, sill and head rings) under a turned cap with a finial. [takes controls=]
        """See kitlib_glazing.dome_lantern(self, id_, radius, height, at=(0, 0, 0), sides=8, cap_rise=None, finial=True, gauge=1.0, controls=None, slot='iron', cap_slot='patina', glass_slot='glass', leaded=None)."""
        import kitlib_glazing; return kitlib_glazing.dome_lantern(self, *args, **kw)
    def ridge_lantern(self, *args, **kw):  # kitlib_glazing.py — A ridge (monitor) lantern: vertical glazed side and end walls standing on a vault's crown curbs, under its own small roof. [takes controls=]
        """See kitlib_glazing.ridge_lantern(self, id_, length, width, height, at=(0, 0, 0), posts=None, post_pitch=3.0, bar_pitch=0.5, roof='gable', roof_rise=None, overhang=0.2, gauge=1.0, controls=None, slot='iron', roof_slot='slate', glass_slot='glass', leaded=None)."""
        import kitlib_glazing; return kitlib_glazing.ridge_lantern(self, *args, **kw)
    def plate(self, *args, **kw):  # kitlib_curves.py — Graph method.
        """See kitlib_curves.plate(self, id_, shapes, depth, plane='xz', centre=True, at=(0.0, 0.0, 0.0), slot=None, max_vertices=15000)."""
        import kitlib_curves; return kitlib_curves.plate(self, *args, **kw)
    def tracery_arch(self, *args, **kw):  # kitlib_tracery.py — A single framed lancet light, plain (foils=0) or cusped (3 trefoil-headed, 5 cinquefoil...), sill at `at`, standing in the x-z plane.
        """See kitlib_tracery.tracery_arch(self, id_, span=0.6, spring=1.2, fraction=1.0, bar=0.03, depth=0.025, foils=3, cusp=0.8, frame=None, style='bar', slot=None, plane='xz', at=(0, 0, 0), tol=0.0015)."""
        import kitlib_tracery; return kitlib_tracery.tracery_arch(self, *args, **kw)
    def rosette(self, *args, **kw):  # kitlib_tracery.py — A trefoil (3), quatrefoil (4) or multifoil rosette in a ring, centred on `at`.
        """See kitlib_tracery.rosette(self, id_, radius=0.25, foils=4, bar=0.025, depth=0.02, cusp=0.8, rotate=1.5707963267948966, frame=None, style='bar', slot=None, plane='xz', at=(0, 0, 0), tol=0.0015)."""
        import kitlib_tracery; return kitlib_tracery.rosette(self, *args, **kw)
    def tracery_window(self, *args, **kw):  # kitlib_tracery.py — A traceried window or panel (see window_shapes for the kinds), sill centre at `at`.
        """See kitlib_tracery.tracery_window(self, id_, span=1.2, spring=1.6, lights=2, kind='y', fraction=1.0, bar=0.035, depth=0.03, foils=3, head_foils=4, cusp=0.8, light_fraction=1.0, frame=None, style='bar', slot=None, plane='xz', at=(0, 0, 0), tol=0.0015)."""
        import kitlib_tracery; return kitlib_tracery.tracery_window(self, *args, **kw)
    def rose_window(self, *args, **kw):  # kitlib_tracery.py — A rose window centred on `at` (see rose_shapes).
        """See kitlib_tracery.rose_window(self, id_, radius=1.0, spokes=12, bar=0.035, depth=0.03, hub=0.28, spring=0.6, fraction=1.0, foils=3, hub_foils=6, oculus_foils=4, cusp=0.8, frame=None, style='bar', slot=None, plane='xz', at=(0, 0, 0), tol=0.0015)."""
        import kitlib_tracery; return kitlib_tracery.rose_window(self, *args, **kw)
    def tracery_railing(self, *args, **kw):  # kitlib_tracery.py — A Gothic iron walkway railing along +x from `at` (the first post's foot): square posts every `length` (post x post_depth, a cap on each), the pierc...
        """See kitlib_tracery.tracery_railing(self, id_, length=1.6, height=1.0, bays=1, arches=4, bar=0.022, depth=0.02, post=0.06, post_depth=None, rail=0.045, fraction=1.0, foils=3, roundel=0.34, roundel_foils=4, cusp=0.8, kind='arcade', handrail=0.05, slot=None, at=(0, 0, 0), tol=0.0015)."""
        import kitlib_tracery; return kitlib_tracery.tracery_railing(self, *args, **kw)
    def box_on(self, *args, **kw):  # kitlib_arch.py — Place a box flush against `face` ('-y', '+y', '-x', '+x', '+z', '-z') of a parent box `(parent_center, parent_size)` with `gap` (default 0.5 mm: se...
        """See kitlib_arch.box_on(self, id_, parent_center, parent_size, face, size, u=0.0, v=0.0, gap=0.0005, clamp_margin=None, slot=None)."""
        import kitlib_arch; return kitlib_arch.box_on(self, *args, **kw)
    def hipped_roof(self, *args, **kw):  # kitlib_arch.py — A closed manifold hipped roof with overhanging soffit/fascia, wall-clamped eaves brackets, ridge roll, and optional louvred ridge ventilator.
        """See kitlib_arch.hipped_roof(self, id_, width, depth, rise=1.25, eaves=0.35, fascia_h=0.14, ridge_vent=True, bracket_count=4, roof_slot='slate', fascia_slot='painted-wood', trim_slot='weathered-iron', at=(0, 0, 0))."""
        import kitlib_arch; return kitlib_arch.hipped_roof(self, *args, **kw)
    def sash_window(self, *args, **kw):  # kitlib_arch.py — A complete framed sash window unit with optional projecting stone sill (`sill_h=0` omits it for nesting inside `window_surround`), lintel head (`li...
        """See kitlib_arch.sash_window(self, id_, width, height, wall_depth=0.28, mullions=2, transoms=2, raised=0.0, sill_h=0.09, sill_proj=0.07, lintel_h=0.16, frame_slot='painted-wood', glass_slot='glass', stone_slot='limestone', facing='-y', at=(0, 0, 0))."""
        import kitlib_arch; return kitlib_arch.sash_window(self, *args, **kw)
    def external_stair(self, *args, **kw):  # kitlib_arch.py — An external iron-and-timber stair with top landing, ground-seated carriage posts, stringers, solid treads, and outer handrail/balusters — zero self...
        """See kitlib_arch.external_stair(self, id_, rise, going=None, width=0.95, landing_len=1.15, tread_count=12, hand_h=0.95, stringer_slot='weathered-iron', tread_slot='wood', rail_slot='painted-iron', facing='-y', at=(0, 0, 0))."""
        import kitlib_arch; return kitlib_arch.external_stair(self, *args, **kw)
    def corner_quoins(self, *args, **kw):  # kitlib_arch.py — Alternating long-and-short chamfered corner quoin blocks wrapping all 4 corners of a rectangular `(width, depth)` masonry body from `z_base` to `z_...
        """See kitlib_arch.corner_quoins(self, id_, width, depth, z_base=0.0, height=2.4, courses=None, course_h=0.28, long_len=0.34, short_len=0.21, proud=0.028, bevel=0.01, gap=0.002, slot='chipped-limestone', at=(0, 0))."""
        import kitlib_arch; return kitlib_arch.corner_quoins(self, *args, **kw)
    def window_surround(self, *args, **kw):  # kitlib_arch.py — A dressed masonry opening surround: twin sill brackets, weathered projecting sill with drip undercut, alternating in-and-out dressed jamb blocks, a...
        """See kitlib_arch.window_surround(self, id_, width, height, at=(0, 0, 0), jamb_w=0.14, jamb_courses=4, sill_h=0.09, sill_proj=0.075, lintel_h=0.2, keystone=True, hood=True, depth=0.065, slot='chipped-limestone', facing='-y')."""
        import kitlib_arch; return kitlib_arch.window_surround(self, *args, **kw)
    def corbelled_chimney(self, *args, **kw):  # kitlib_arch.py — A coursed brick chimney stack rising from `z_base` to `z_base + height` with a 4-sided base flashing collar, stone necking band, oversailing brick...
        """See kitlib_arch.corbelled_chimney(self, id_, width=0.68, depth=0.52, z_base=0.0, height=1.85, pots=1, corbel_steps=3, brick_slot='weathered-brick', stone_slot='chipped-limestone', pot_slot='terracotta', flashing_slot='weathered-iron', at=(0, 0, 0))."""
        import kitlib_arch; return kitlib_arch.corbelled_chimney(self, *args, **kw)
    def panelled_door(self, *args, **kw):  # kitlib_arch.py — A stile-and-rail panelled timber door with bottom/lock/frieze/top rails, double-sided bevelled fielded panels, optional glazed upper light (`glass_...
        """See kitlib_arch.panelled_door(self, id_, width=0.95, height=2.1, thickness=0.056, at=(0, 0, 0), glazed_upper=True, panels_x=2, outer_frame=True, frame_depth=0.14, threshold=True, door_slot='wood', frame_slot='wood', glass_slot='glass', hardware_slot='bronze', stone_slot='chipped-limestone', facing='-y')."""
        import kitlib_arch; return kitlib_arch.panelled_door(self, *args, **kw)
    def louvred_lantern(self, *args, **kw):  # kitlib_arch.py — A timber-and-slate roof ventilator turret / louvred ridge lantern with base curb, corner posts, sloped louvre slats on all 4 faces, dark weather co...
        """See kitlib_arch.louvred_lantern(self, id_, length, width, height, z_base=0.0, louvres=4, roof='hipped', roof_rise=None, overhang=0.12, finials=True, frame_slot='wood', roof_slot='slate', trim_slot='terracotta', core_slot='black', at=(0, 0, 0))."""
        import kitlib_arch; return kitlib_arch.louvred_lantern(self, *args, **kw)
    def plummer_block(self, *args, **kw):  # kitlib_mech.py — Open-bore split cast-iron journal pedestal (`plummer block`) with Chaikin SDS housing (`0` pinched poles), machined FWN soleplate, protruding flang...
        """See kitlib_mech.plummer_block(self, id_, bore_r, center_h=None, width=None, length=0.11, at=None, axis='x', oil_cup=True, body_slot='patina', bush_slot='bronze', bolt_slot='steel')."""
        import kitlib_mech; return kitlib_mech.plummer_block(self, *args, **kw)
    def bushed_sheave(self, *args, **kw):  # kitlib_mech.py — Open-bore grooved pulley, flywheel, or lead sheave with a pressed flanged bronze journal bushing (`bore_r`), post-FWN bolted cast hub, oval-tapered...
        """See kitlib_mech.bushed_sheave(self, id_, outer_r, width, bore_r, groove_r=0.024, spokes=6, at=None, axis='x', sides=32, wheel_slot='iron', bush_slot='bronze')."""
        import kitlib_mech; return kitlib_mech.bushed_sheave(self, *args, **kw)
    def clevis_fork(self, *args, **kw):  # kitlib_mech.py — Open-bore Chaikin SDS forked linkage clevis (`0` pinched poles) whose cross-pin axis lies at local `[0, 0, 0]` (along local X) with fork arms exten...
        """See kitlib_mech.clevis_fork(self, id_, gap, cheek_t, pin_r, arm_len, at=None, axis='z', with_pin=True, body_slot='iron', pin_slot='steel', bush_slot='bronze')."""
        import kitlib_mech; return kitlib_mech.clevis_fork(self, *args, **kw)
    def stuffing_box(self, *args, **kw):  # kitlib_mech.py — Open-bore cast stuffing box & bronze two-stud packing gland (`0` pinched poles, `0` MuJoCo clips) seated at `at` and extending `+height` along `axi...
        """See kitlib_mech.stuffing_box(self, id_, stem_r, body_r=None, height=0.18, at=None, axis='z', body_slot='patina', gland_slot='bronze', bolt_slot='steel')."""
        import kitlib_mech; return kitlib_mech.stuffing_box(self, *args, **kw)
    def arched_yoke(self, *args, **kw):  # kitlib_mech.py — Twin-foot cast-iron arched spindle bridge (`0` pinched poles, `0` MuJoCo clips) standing from `z = 0` to `rise` across `span`, carrying an open-bor...
        """See kitlib_mech.arched_yoke(self, id_, span, rise, bore_r, foot_w=0.11, thickness=0.08, at=None, facing='x', body_slot='iron', bush_slot='bronze', bolt_slot='steel')."""
        import kitlib_mech; return kitlib_mech.arched_yoke(self, *args, **kw)
    def pierced_cheek(self, *args, **kw):  # kitlib_mech.py — Open-bore Chaikin SDS cast side frame / A-standard (`0` pinched poles, `0` MuJoCo buried overlaps) whose inner face sits at `x = at[0]` and outer f...
        """See kitlib_mech.pierced_cheek(self, id_, width_y, height_z, thickness, bore_z, bore_r, ribs=True, at=None, side=1, body_slot='patina', bush_slot='bronze', bolt_slot='steel')."""
        import kitlib_mech; return kitlib_mech.pierced_cheek(self, *args, **kw)
    # END GENERATED HELPER INDEX


def mesh(teeth1, teeth2, module, direction=0.0, phase1=0.0):
    """Seat gear 2 on gear 1 (both from gear(), same module and pressure angle, axes parallel to Z).
    Gear 1 sits at the origin turned by phase1; gear 2's centre goes at `direction` (radians from +x).
    Returns (centre distance, phase of gear 2): place gear 2 at distance * (cos d, sin d) and pass the
    phase to gear(phase=...). The distance is the standard one, (teeth1 + teeth2) * module / 2 (X if
    module is); tooth play comes from each gear's backlash. Turning gear 1 by a turns gear 2 by
    -a * teeth1 / teeth2. Built by Opus 5.5 (medium).
    Example: a, ph = mesh(12, 40, 0.01); g.gear("wheel", 40, 0.01, 0.03, at=[a, 0, 0], phase=ph)"""
    pitch2 = 2 * PI / teeth2
    u1 = (direction - phase1) * teeth1 / (2 * PI)  # gear 1's tooth position facing gear 2, in pitches
    phase2 = (direction + PI - pitch2 * (0.5 - u1)) % pitch2
    return X(module) * ((teeth1 + teeth2) / 2), phase2


def mesh_rack(teeth, module, x=0.0):
    """Seat a pinion (gear() with `teeth`) on a rack() of the same module, its centre over x on the
    pitch line. Returns (centre [x, pitch radius, 0], phase for gear(phase=...)). Rolling the pinion
    along +x by s turns it by -s / pitch radius. x and module may be X (the phase is then X).
    Example: c, ph = mesh_rack(12, 0.01, 0.1); g.gear("pinion", 12, 0.01, 0.04, at=c, phase=ph)"""
    R = X(module) * (teeth / 2)
    phase = -PI / 2 - X(x) / R
    if _num(phase.v):
        phase = phase.v % (2 * PI / teeth)
    return [x, R, 0], phase


def segment_phase(teeth, start):
    """The phase a gear_segment() starting at `start` meshes at: its teeth sit where gear(phase=...) or
    ring_gear(phase=...) would put them. Pass it as phase1 to mesh() or phase_ring to mesh_internal().
    Example: a, ph = mesh(64, 14, 0.01, direction=0.7, phase1=segment_phase(64, 0.0))"""
    return start + PI / teeth


def mesh_internal(teeth_ring, teeth_pinion, module, direction=0.0, phase_ring=0.0):
    """Seat a pinion (gear()) inside a ring_gear() or an internal gear_segment() of the same module and
    pressure angle, axes parallel to Z. The ring sits at the origin turned by phase_ring (for a segment,
    segment_phase(teeth, start)); the pinion's centre goes at `direction` (radians from +x). Returns
    (centre distance (teeth_ring - teeth_pinion) * module / 2, X if module is; phase for gear(phase=...)).
    Keep teeth_ring - teeth_pinion at 12 or more (tip interference below that). With the pinion's axle
    held, turning the ring by a turns the pinion by a * teeth_ring / teeth_pinion, the same way.
    Built by Opus 5.5 (medium).
    Example: a, ph = mesh_internal(72, 18, 0.01); g.gear("pinion", 18, 0.01, 0.04, at=[a, 0, 0], phase=ph)"""
    assert teeth_ring - teeth_pinion >= 8, "mesh_internal: the ring needs at least 8 more teeth than the pinion"
    u = (direction - phase_ring) * teeth_ring / (2 * PI)     # ring tooth position at the pitch point, in pitches
    pitch = 2 * PI / teeth_pinion
    return X(module) * ((teeth_ring - teeth_pinion) / 2), (direction - (u + 0.5) * pitch) % pitch


def seat_pawls(teeth, radius, depth, length, count=1, start=PI / 2, phase=0.0, free="ccw", clearance=0.001,
               centre=(0.0, 0.0, 0.0)):
    """Places pawl()s so each beak seats against a drop face of a ratchet() (same teeth, radius, depth,
    phase and free; numbers, metres; the wheel at `centre`). Pawl j goes to the drop face nearest angle
    start + j * 2pi/count; its pivot lies on the tip tangent through the top of that face, `length` back
    from it, so the tooth's load runs through the pivot; the beak face stands `clearance` off the drop
    face and its foot `clearance` above the root. Returns (placements, pawl keywords): placements is a
    list of (at, rotation) for pawl(at=..., rotation=...); the keywords are length, depth, beak, free.
    Built by Opus 5.5 (medium).
    Example: places, kw = seat_pawls(24, 0.12, 0.015, 0.1, count=2)
             for i, (at, rz) in enumerate(places): g.pawl(f"pawl{i}", face=0.03, at=at, rotation=rz, **kw)"""
    ro, h, L, c = float(radius), float(depth), float(length), float(clearance)
    assert free in ("ccw", "cw") and 0 < c < h, "seat_pawls: free ccw or cw, clearance under the depth"
    p = 2 * PI / teeth
    beak = min(0.7 * ro * p, 2.5 * h)
    boss = 1.3 * 0.14 * L
    assert L * L > 2.4 * ro * boss, "seat_pawls: pawl too short for its boss to clear the wheel"
    sense = 1.0 if free == "ccw" else -1.0
    places = []
    for j in range(count):
        th = phase + p * round((start + 2 * PI * j / count - phase) / p)
        tx, ty = -math.sin(th) * sense, math.cos(th) * sense      # from the pivot towards the face
        cx, cy = centre[0] + ro * math.cos(th), centre[1] + ro * math.sin(th)
        places.append(([cx - (L + c) * tx, cy - (L + c) * ty, centre[2]], math.atan2(ty, tx)))
    return places, {"length": L, "depth": h - c, "beak": beak, "free": free}


def _facing(f):
    named = {"+x": 0.0, "+y": PI / 2, "-x": PI, "-y": -PI / 2}
    return named[f] if isinstance(f, str) else f


_CUT = {}


def _rack_cut_half_angles(N, alpha, backlash, radii):
    """Half angular thickness of a tooth at each radius (unit module), found by rolling the basic rack
    cutter (addendum 1.25, tip radius 0.38) over the blank: the involute above the base circle, the
    trochoid fillet and any undercut below it. Matches the involute formula to ~1e-4 rad."""
    key = (N, alpha, backlash, tuple(radii))
    if key in _CUT:
        return _CUT[key]
    R, ta, hc, rho = N / 2.0, math.tan(alpha), 1.25, 0.38
    x0 = PI / 4 - backlash / 2
    yl = hc - rho + rho * math.sin(alpha)
    cx, cy = x0 + yl * ta + rho * math.cos(alpha), hc - rho
    poly = [(x0 + y * ta, y) for y in (-1.4 + (yl + 1.4) * k / 12 for k in range(13))]
    poly += [(cx + rho * math.cos(a), cy + rho * math.sin(a)) for a in (PI - alpha + (alpha - PI / 2) * k / 8 for k in range(1, 9))]
    poly.append((PI / 2, hc))

    def delta(s, r):
        c, sn = math.cos(s / R), math.sin(s / R)
        best, prev = None, None
        for px, py in poly:
            wx, wy = px + s, py - R
            gx, gy = c * wx + sn * wy, -sn * wx + c * wy
            q = (math.hypot(gx, gy), math.atan2(gy, gx) + PI / 2)
            if prev and (prev[0] - r) * (q[0] - r) <= 0 and prev[0] != q[0]:
                d = prev[1] + (r - prev[0]) / (q[0] - prev[0]) * (q[1] - prev[1])
                best = d if best is None else min(best, d)
            prev = q
        return 9.0 if best is None else best

    out = []
    g = (math.sqrt(5) - 1) / 2
    for r in radii:
        d, s = min((delta(s, r), s) for s in (-4 + 8 * i / 320 for i in range(321)))
        lo, hi = s - 0.025, s + 0.025
        for _ in range(40):
            m1, m2 = hi - g * (hi - lo), lo + g * (hi - lo)
            if delta(m1, r) < delta(m2, r):
                hi = m2
            else:
                lo = m1
        out.append(min(d, delta((lo + hi) / 2, r)))
    _CUT[key] = out
    return out


def _opening(body, r_in, r_out, b0, span, spoke_width, res):
    """Counter-clockwise outline of the web opening of one sector (angles b0 .. b0 + span) between the
    hub (r_in) and the rim (r_out): a round lightening hole, or the space between two straight spokes."""
    bm = b0 + span / 2
    if body == "web":
        rc = (X(r_in) + r_out) * 0.5
        hr = mn((X(r_out) - r_in) * 0.5, rc * math.sin(span / 2)) * 0.62
        n = 12
        return [(rc * math.cos(bm) + hr * math.cos(2 * PI * j / n), rc * math.sin(bm) + hr * math.sin(2 * PI * j / n)) for j in range(n)]
    depth = X(r_out) - r_in
    a = (X(spoke_width) if spoke_width is not None else depth * 0.2) * 0.5
    gap = depth * 0.1
    ri, ro = X(r_in) + gap, X(r_out) - gap
    di, do = sqrt(ri * ri - a * a), sqrt(ro * ro - a * a)
    dli, dlo = atan2(a, di), atan2(a, do)
    b1 = b0 + span
    u = lambda b: (math.cos(b), math.sin(b))
    pts = [(d * u(b)[0] - sg * a * u(b)[1], d * u(b)[1] + sg * a * u(b)[0]) for d, b, sg in ((di, b0, 1), (do, b0, 1))]
    n_o = 4 + res
    for j in range(1, n_o):
        ang = (b0 + dlo) + (span - dlo * 2) * (j / n_o)
        pts.append((ro * cos(ang), ro * sin(ang)))
    pts += [(d * u(b)[0] - sg * a * u(b)[1], d * u(b)[1] + sg * a * u(b)[0]) for d, b, sg in ((do, b1, -1), (di, b1, -1))]
    n_i = 3
    for j in range(1, n_i):
        ang = (b1 - dli) - (span - dli * 2) * (j / n_i)
        pts.append((ri * cos(ang), ri * sin(ang)))
    return pts


def _check_gear(N, m, w, bore, rh, inner_r, e, webbed, web, rf):
    vals = [_v(x) for x in (m, w, bore, rh, inner_r, e)]
    web = _v(web) if web is not None else None
    if not all(_num(v) for v in vals) or not (web is None or _num(web)):
        return                                             # expressions: the sweep checks the result
    m, w, bore, rh, inner_r, e = vals
    assert m > 0 and w > 0 and bore > 0, "gear module, face and bore must be positive"
    assert e >= 0, "gear hub length must not be negative"
    if webbed:
        assert bore < rh and rh + 2 * m < inner_r, f"gear {N}: hub ({rh:.3f}) must clear the rim ({inner_r:.3f}); lower bore or rim"
        assert web is None or 0 < web < w, "gear web must be thinner than the face"
    elif e > 0:
        assert bore < rh < m * (rf - 0.5), f"gear {N}: hub radius {rh:.3f} must lie between the bore and the root"
    else:
        assert bore < m * (rf - 0.5), f"gear {N}: bore {bore:.3f} reaches the root"


def _internal_tooth(N, alpha, backlash, n, pinion):
    """Unit-module outline of one internal tooth, gap centre to gap centre, counter-clockwise, as
    (radius, angle from the tooth centre). The tooth space is an external involute tooth thickened by
    the backlash. The tip circle is R - 1, raised where needed so that contact with the smallest
    `pinion` (rack-cut by gear()) ends before the pinion's involute does (no fouling of its root
    fillet), and kept outside the base circle."""
    R = N / 2.0
    rb, sa = R * math.cos(alpha), math.sin(alpha)
    s_form = max(0.0, pinion / 2.0 * sa - 1.0 / sa)          # pinion involute start, along the line of action
    reach = math.hypot(rb, (N - pinion) / 2.0 * sa + s_form)
    rt, rr = max(R - 1.0, rb * 1.004, reach), R + 1.25
    inv = lambda a: math.tan(a) - a
    gap = PI / N
    tau = lambda r: gap - ((PI / 2 + backlash) / N + inv(alpha) - inv(math.acos(min(1.0, rb / r))))
    assert tau(rt) > 0.02 / rt, f"ring gear {N} teeth: pointed teeth"
    assert tau(rr) < gap - 0.05 / rr, f"ring gear {N} teeth: no room at the root"
    rs = [rt + (rr - rt) * (k / (n - 1)) ** 0.8 for k in range(n)]
    return [(rr, -gap)] + [(r, -tau(r)) for r in reversed(rs)] + [(r, tau(r)) for r in rs] + [(rr, gap)]


def _check_band(m, face, rim):
    m, w = _v(m), _v(X(face))
    if _num(m):
        assert m > 0, "gear module must be positive"
    if _num(w):
        assert w > 0, "gear face must be positive"
    assert rim > 0.3, "rim (modules) must be at least 0.3"


def _band(inner_layers, outer, bi, bo, top, bot, closed):
    """A shell between an inner and an outer edge, both counter-clockwise lists of (x, y). The outer edge
    is a prism from z = top to bot; inner_layers are (points, z) from top to bottom (equal counts: a bore
    with a hub boss is several). The caps are one polygon per sector s, from outer[bo[s]] to
    outer[bo[s + 1]] and inner[bi[s + 1]] back to inner[bi[s]] (indices wrap when closed). Open bands
    are cut with flat end walls between the edges' first and last points. Returns (vertices, faces)."""
    verts, faces = [], []

    def lay(pts, z):
        start = len(verts)
        verts.extend([x, y, z] for x, y in pts)
        return list(range(start, len(verts)))

    OT, OB = lay(outer, top), lay(outer, bot)
    L = [lay(pts, z) for pts, z in inner_layers]
    IT, IB = L[0], L[-1]
    no, ni = len(outer), len(inner_layers[0][0])
    for s in range(len(bo) - 1):
        o = range(bo[s], bo[s + 1] + 1)
        i = range(bi[s + 1], bi[s] - 1, -1)
        faces.append([OT[k % no] for k in o] + [IT[k % ni] for k in i])
        faces.append(list(reversed([OB[k % no] for k in o] + [IB[k % ni] for k in i])))
    for a in range(no if closed else no - 1):
        b = (a + 1) % no
        faces.append([OB[a], OB[b], OT[b], OT[a]])
    for A, B in zip(L, L[1:]):
        for a in range(ni if closed else ni - 1):
            b = (a + 1) % ni
            faces.append([A[a], A[b], B[b], B[a]])
    if not closed:
        faces.append([OB[-1], IB[-1], IT[-1], OT[-1]])
        faces.append([IB[0], OB[0], OT[0], IT[0]])
    return verts, faces


def _plate(outline, hole, top, bot):
    """A flat plate: a counter-clockwise outline, optionally one counter-clockwise hole, from z = top to
    bot. Returns (vertices, faces)."""
    verts, faces = [], []

    def lay(pts, z):
        start = len(verts)
        verts.extend([x, y, z] for x, y in pts)
        return list(range(start, len(verts)))

    T, B = lay(outline, top), lay(outline, bot)
    n = len(outline)
    if hole:
        HT, HB = lay(hole, top), lay(hole, bot)
        faces.append([T, list(reversed(HT))])
        faces.append([list(reversed(B)), HB])
        for a in range(len(hole)):
            b = (a + 1) % len(hole)
            faces.append([HT[a], HT[b], HB[b], HB[a]])
    else:
        faces.append(T)
        faces.append(list(reversed(B)))
    for a in range(n):
        b = (a + 1) % n
        faces.append([B[a], B[b], T[b], T[a]])
    return verts, faces


def stage_control(max_stage=3, value=None):
    """Standard integer `stage` control (`0..max_stage`, defaulting to `max_stage`) for pace-layered authoring."""
    return control(max_stage if value is None else value, 0, max_stage, integer=True)


def _dump(obj, **kw):
    return json.dumps(obj, default=lambda o: o.v, **kw)


def ref(atlas=(), sources=(), plates=(), notes="", other=(), review=None):
    """Provenance of a module: atlas design recipes (References/data.json detail ids), atlas
    source records, plate library ids (sNN), and what was taken from them. State which
    dimensions are design assumptions; the atlas keeps source measurements and design ranges apart."""
    record = {"atlas": list(atlas), "sources": list(sources), "plates": list(plates), "notes": notes}
    if other:
        record["other"] = list(other)       # material outside the atlas and the library: earlier models, briefs, studies
    if review:
        record["review"] = review
    return record


def review(weak, improve=(), by="Opus 5.5 (medium)", reviewed=None, date="2026-11-15", seen="sheet and close-up renders"):
    """Review of a module before an improvement pass: who built it, who reviewed it, what was looked at, what is weak or
    not verified, and what to improve next (each item ends with its source, e.g. [transcript], [commit abc1234])."""
    r = {"by": by, "reviewed": reviewed or by, "date": date, "seen": seen, "weak": weak}
    if improve:
        r["improve"] = list(improve)
    return r


def write_reviews(path, *ref_tables, cross=()):
    """docs/KIT_REVIEW.md from the review records of the given REFS tables (merged with the rows already there,
    so each builder keeps its own modules' rows)."""
    path = Path(path)
    rows = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.startswith("| `"):
                rows[line.split("`")[1]] = line
    for table in ref_tables:
        for module, record in table.items():
            r = record.get("review")
            if r:
                improve = "<br>".join(r.get("improve", [])) or "-"
                left = "<br>".join(r.get("left", r.get("improve", []))) or "-"
                rows[module] = (f"| `{module}` | {r['by']} | {r.get('reviewed', r['by'])}, {r['date']} | {r['weak']} | {improve} | "
                                f"{r.get('pass1', '-')} | {left} |")
            else:
                rows.pop(module, None)
    head = ("# Kit review notes\n\nGenerated by the kit builders from each module's `references.review` record: who built the "
            "module, who reviewed it, what was weak or not verified, and what to improve next (with sources), what "
            "improvement pass 1 (Opus 5.5 medium, 2026-11-15) did, and what is left. Edit recipes/reviews.py, not this file.\n\n"
            "| Module | Built by | Reviewed | Weak or not verified | Improve next (at review) | Improvement pass 1: done | Left to do |\n"
            "|---|---|---|---|---|---|---|\n")
    tail = ("\n## Across all kits\n\n" + "\n".join(f"- {c}" for c in cross) + "\n") if cross else ""
    path.write_text(head + "\n".join(rows[k] for k in sorted(rows)) + "\n" + tail)


class Kit:
    def __init__(self, root, directory, name, imports=(), references=None, bake_leaves=True):
        self.references = references
        self.bake_leaves = bake_leaves
        self.dir = Path(root) / directory
        self.dir.mkdir(parents=True, exist_ok=True)
        for stale in self.dir.glob("*.json"):
            stale.unlink()
        self.name, self.imports, self.registry = name, list(imports), {}

    def module(self, name, controls, graph, output, ports, imports=None, audit=None):
        """audit: {"components": N, "why": "..."} when the module is meant to be in N separate pieces (pgm audit)."""
        doc = {"version": 1}
        if audit:
            doc["audit"] = audit
        if self.references is not None:
            assert name in self.references, f"module {name} has no reference record"
            doc["references"] = self.references[name]
        assert isinstance(ports, dict), "ports must be a dict {name: port(...)} (a list would leave the module with none)"
        doc["controls"], doc["modulePorts"] = controls, ports
        if graph.derived:
            doc["derived"] = graph.derived
        if imports:
            doc["moduleImports"] = imports
        elif self.bake_leaves and not any(n["type"] == "bake" for n in graph.nodes):
            # A leaf module (no imported modules) is one object to the engine: a few large meshes
            # with a part per material, not hundreds of instances of its small parts.
            output = graph.bake(output + ".baked", output)
        doc["nodes"], doc["output"] = graph.nodes, output
        (self.dir / f"{name}.json").write_text(_dump(doc, indent=1))
        self.registry[name] = {"document": f"{name}.json", "ports": ports}

    def document(self, name, controls, graph, output, imports=None, ports=None):
        """A document that is not a module (an assembly)."""
        doc = {"version": 1, "controls": controls}
        if graph.derived:
            doc["derived"] = graph.derived
        if imports:
            doc["moduleImports"] = imports
        if ports:
            doc["modulePorts"] = ports
        doc["nodes"], doc["output"] = graph.nodes, output
        (self.dir / f"{name}.json").write_text(_dump(doc, separators=(",", ":")))

    def close(self):
        manifest = {"version": 1, "kit": self.name}
        if self.imports:
            manifest["imports"] = self.imports
        manifest["modules"] = self.registry
        (self.dir / "modules.json").write_text(_dump(manifest, indent=1))
        return len(self.registry)


# Ornament helpers (swell_tube, flat_bar, round_bar, scroll_bracket; curves volute, c_scroll, s_scroll, whiplash)
# attach themselves to Graph. Built by Opus 5.5 (medium).
import kitlib_ornament  # noqa: E402,F401
from kitlib_aero import naca4, naca4_parameters  # noqa: E402,F401  attaches Graph.wing, .propeller, .nacelle (aero helpers)
from kitlib_iron import iron_outline, iron_section, mitre  # noqa: E402,F401  attaches Graph.rolled, .truss, .plate_girder, ... (structural iron, Built by Opus 5.5 (medium))
import kitlib_masonry  # noqa: E402,F401  attaches Graph.arch_ring, .ashlar_wall, .brick_wall, .rubble_wall, .coping, .string_course, .cutwater_cap (masonry helpers)
import kitlib_orders  # noqa: E402,F401  attaches Graph.column, .entablature, .moulding_run... (classical orders helpers)
from kitlib_pipes import route, flange_dims  # noqa: E402,F401  attaches Graph.pipe_run, .tee, .reducer, .valve, .supports (pipework helpers)
# Curved glazing helpers (glazed_surface, glazed_vault, glazed_dome, fan_dome, ridge_lantern, dome_lantern). Built by Opus 5.5 (medium).
import kitlib_glazing  # noqa: E402,F401
# 2D construction layer (offset, fillet, tangents, booleans, Graph.plate) and Gothic tracery on it. Built by Opus 5.5 (medium).
import kitlib_curves, kitlib_tracery  # noqa: E402,F401,E401  attach Graph.plate and Graph.tracery_arch, .rosette, .tracery_window, .rose_window, .tracery_railing
from kitlib_arch import clamp_to_wall  # noqa: E402,F401  attaches Graph.box_on, .hipped_roof, .sash_window, .external_stair
from kitlib_mech import mech_bore  # noqa: E402,F401  attaches Graph.plummer_block, .bushed_sheave, .clevis_fork, .stuffing_box, .arched_yoke, .pierced_cheek


if __name__ == "__main__":
    # Helper discovery (Built by Opus 5.5 (medium)): python3 procgen/recipes/kitlib.py --helpers [words...] [--long] [--json]
    import sys
    if "--helpers" not in sys.argv[1:]:
        sys.exit("usage: kitlib.py --helpers [words...] [--long] [--json]   (kitlib is a library; builders import it)")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import gen_helper_index
    sys.exit(gen_helper_index.cli([a for a in sys.argv[1:] if a != "--helpers"]))
