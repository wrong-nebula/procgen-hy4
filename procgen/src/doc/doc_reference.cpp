// Node reference: every node type a geometry document may use, for `pgm nodes` and the MCP tool. The evaluator
// rejects a type that is not listed here, so the list cannot fall behind the evaluator (tests/test_doc.cpp checks
// the other direction: every listed type is evaluated).
#include "pg/doc.hpp"

#include <algorithm>

namespace pg::doc {

const std::vector<NodeInfo>& node_reference() {
    static const std::vector<NodeInfo> table = {
        // shapes
        {"cube", "shape", "-", "size [x,y,z]", "Box centred on the origin."},
        {"cylinder", "shape", "-", "radius, depth, sides, rings, cap fan|ngon|inset|quad, capInset", "Prism round z, centred on the origin (z from -depth/2 to depth/2)."},
        {"grid", "shape", "-", "size [x,y], divisions [x,y]", "Flat grid in the XY plane."},
        {"surface", "shape", "-", "vertices, faces", "Mesh given by vertices and polygon faces."},
        {"extrude", "shape", "-", "profile [[x,y]...], holes [[[x,y]...]], depth, chart perimeter, cap tri|ngon|inset|quad, capInset", "Closed 2D profile (with optional through-bore holes; 1 matching hole with cap quad produces an all-quad annular solid) extruded along +z."},
        {"profileSolid", "shape", "-", "profile, depth, holes, bevel, rimEdges", "Extruded profile with holes and a bevelled rim."},
        {"revolve", "shape", "-", "profile [[r,z]...], sides, flip, cap pole|inset|quad, capInset", "Lathe: profile turned round z (UVs: girth by arc length). A profile that ends where it starts is a closed ring section and turns into a closed solid (pipe walls, bores)."},
        {"heightfield", "shape", "-", "size, divisions, heights | noise, base, skirt", "Terrain block from heights or fBm noise."},
        {"patch", "shape", "-", "point [x,y,z] in @u @v, divisions, panels, inset, thickness, closedU, closedV, flip",
         "Parametric surface; panels with inset make glazing; thickness makes a closed shell."},
        {"sweep", "shape", "-", "path [[x,y,z]...], radius, sides, cap fan|ngon|inset|quad, capInset", "Round bar along a polyline path."},
        {"voronoi", "shape", "- | points", "size [x,y,z], count, gap, shrink, seed",
         "Closed 3D Voronoi polyhedron cells clipped to a centred box (cyclopean stone, rubble, fractured slabs)."},
        // curves and points
        {"line", "curve", "-", "start, step, count", "Points start + k * step; also a straight guide."},
        {"curve", "curve", "-", "point [x,y,z] in @t, segments, closed", "Parametric curve."},
        {"spline", "curve", "-", "points, closed, resolution, interpolation catmullRom|polyline", "Curve through control points."},
        {"resample", "curve", "curve", "count", "Curve resampled to equal arc lengths."},
        {"curveFillet", "curve", "curves", "radius, segments, corners", "Rounds polyline corners with tangent circular arcs."},
        {"curveTrim", "curve", "curves", "start, end, reverse", "Trims curves to arc-length range [start, end] in [0, 1] and/or reverses."},
        {"curveOffset", "curve", "curves", "offset, up [x,y,z]", "Offsets curves sideways in the plane normal to up."},
        {"sampleCurve", "points", "curves", "positions [t...] | pitch | count, centred", "Samples points along curves by arc length."},
        {"pointArray", "points", "-", "positions [[x,y,z]...]", "Explicit points (expressions allowed)."},
        {"pointField", "points", "-", "layout grid|hex, spacing, size [x,y] | radius, inner", "Points in a plane round the origin."},
        {"raycast", "points", "surface, points", "direction [x,y,z], maxDistance, offset, alongNormal, cullBackfaces, miss drop|keep|fail",
         "Projects points onto a target surface along direction via ClosestIndex::raycast (drop props onto terrain, roofs or hulls)."},
        {"outline", "curve", "-", "shape circle|arc|rect|polygon, radius, size, corner, sides, start, sweep, segments",
         "One guide curve in the XY plane."},
        {"courses", "curve", "-", "size [x,y], course, block, bond, minimum", "Joints of coursed work in a rectangle."},
        // along curves
        {"tube", "shape", "curves", "radius (may use @t @index), sides, twist, up, cap fan|ngon|inset|quad, capInset", "Round or tapered tube along curves; closed curves become rings."},
        {"loft", "shape", "- | curves", "sections [[[x,y,z]...] | {profile,z,scale,t,twist}], segments, rings, smooth, cap quad|inset|tri|ngon|open, capInset, closed, flip, thickness", "Skin through curves or all-quad solid loft across arbitrary 2D/3D section profiles (e.g. square-to-round transitions)."},
        {"profileSweep", "shape", "curves", "profile | section rect|ibeam|channel|angle|tee|cruciform (width,height,flange,web), scale, twist, caps, cap fan|ngon|inset|quad, capInset, up",
         "2D or structural section swept along curves with optional per-station @t scale and twist."},
        {"surfaceBand", "shape", "points", "width, depth, offset, elevation", "Band of faces along an alignment."},
        // placing and combining
        {"transform", "combine", "geometry", "translation, rotation (radians, XYZ), scale", "Moves its input."},
        {"join", "combine", "geometry...", "-", "Union of inputs as separate parts (no boolean)."},
        {"instance", "combine", "points, geometry...", "translation, rotation, scale, tint, pick (may use @index @random @x @y @z), randomSeed",
         "Copies at each point; with multiple geometry inputs, picks per point by pick or seeded random."},
        {"select", "combine", "a, b", "condition", "a when condition is non-zero, else b."},
        {"empty", "combine", "-", "-", "Nothing; the other arm of a select."},
        {"group", "combine", "-", "group (alias), bindings {control: value}", "Places an imported module (moduleImports)."},
        {"groupInput", "combine", "-", "-", "Socket a group fills from the caller."},
        {"connect", "combine", "geometry", "a [module|@node, port] fixed, b [module, port] moving, translation, rotation, tolerance", "Seats the moving module's port on the fixed port."},
        {"material", "combine", "geometry", "slot, replace", "Material slot of every part (pgm materials lists slots)."},
        {"tint", "combine", "geometry", "color [r,g,b]", "Per-placement colour for tintable materials (the previewer ignores it)."},
        {"bake", "combine", "geometry", "-", "Merge into one prototype per material; keeps details."},
        // mesh operations
        {"weld", "mesh", "geometry", "tolerance", "Merge coincident vertices."},
        {"uvFit", "mesh", "geometry", "span [v0,v1] metres, band [t0,t1] image heights from the top, tile metres, uOffset",
         "Fit a chart to a band of a trim-sheet texture; u scaled alike."},
        {"smoothNormals", "mesh", "geometry", "angle, weighting uniform|area|angle|areaAngle|areaSq, splitCreases",
         "Smooth shading normals with optional Phong break angle, crease splits and face-weighted normals."},
        {"planarUV", "mesh", "geometry", "plane, metresPerTile", "Planar UV projection."},
        {"boxBevel", "mesh", "geometry", "width", "Chamfers every edge of a box, keeping its bounds."},
        {"boxBoolean", "mesh", "a, b", "operation union|intersection|difference", "Boolean of two axis-aligned boxes."},
        {"meshBoolean", "mesh", "a, b", "operation union|intersection|difference", "Boolean of two meshes (BSP)."},
        {"chamfer", "mesh", "geometry", "width, minAngle, segments, profile, clampOverlap, crease, selection {normal,angle,direction,dirAngle,box,plane,radius,zRange,indices,material,invert}",
         "Bevel sharp edges (1-seg chamfer, 2-seg support loops at profile=1, or multi-segment fillet)."},
        {"crease", "mesh", "geometry", "sharpness, vertexSharpness, vertexWeight, minAngle, mode max|set|add, selection {normal,angle,direction,dirAngle,box,plane,radius,zRange,indices,material,invert}",
         "Tag edges/vertices with semi-sharp SDS crease weights (0..10) and optional rational NURBS vertex weights."},
        {"shrinkwrap", "mesh", "geometry, target", "offset, maxDistance, method nearest|project, direction, selection",
         "Project a detail cage onto a target surface via ClosestIndex."},
        {"transferNormals", "mesh", "geometry, source", "maxDistance, blend, selection {normal,angle,direction,dirAngle,box,plane,radius,zRange,indices,material,invert}",
         "Transfer smooth interpolated normals from a clean proxy surface onto a mesh (eliminates shading ripples around holes/cutouts on curved surfaces)."},
        {"loopCut", "mesh", "geometry", "cuts | positions [...] | margin, crease, propagate, selection {direction,dirAngle,normal,angle,box,indices,material,invert}",
         "Insert all-quad edge loops across quad rings (by count, fractions or metric margin from both ends)."},
        {"faceExtrude", "mesh", "geometry[, occluder]", "offset|distance, inset, scale, individual, evenThickness, capSlot, selection",
         "Inset and/or extrude selected faces (region or individual; optional occluder query)."},
        {"faceDivide", "mesh", "geometry[, occluder]", "axis vertical|horizontal|[x,y,z], sizes [...] | repeat, selection",
         "Subdivide selected faces into storeys or bays while keeping shared edges watertight."},
        {"deleteFaces", "mesh", "geometry[, occluder]", "selection", "Remove selected faces."},
        {"bridgeLoops", "mesh", "geometry[, geometry2]", "segments, smooth, tension, profileScale, twist",
         "Bridge two open boundary loops (from 1 or 2 meshes) with smooth tangent-matched Hermite quad rings into a single watertight solid."},
        {"quadFillHole", "mesh", "geometry", "bulge, offset, selection",
         "Close even-sided boundary holes with zero-pole all-quad Coons-patch grids (Rhino/Blender Grid Fill)."},
        {"subdivide", "mesh", "geometry", "levels, linearPreSubdiv, boundary edgeAndCorner|edge, creaseMethod uniform|chaikin, triSubdiv catmark|smooth, uvRule cornersPlus1|boundaries|linear|smooth, limit, limitNormals, interpolate, interpolateWeight, subdivNormals 0|1|2, tunedEV, nonUniform, rational, adaptive, adaptiveAngle, g2Fair, g2Weight, edits [{level,selection,inset,offset,crease,normalOffset,flutes}], lodLevels, lodDistances",
         "Catmull-Clark SDS with semi-sharp creases, G2 EV fairing, Pixar hierarchical multiresolution edits, rational R^4 weights, NURSS non-uniform knots, feature-adaptive coarsening, W-PIA interpolation, subdivision shading, limit normals and multi-LODs."},
        {"displaceDetails", "mesh", "geometry", "scale, levels, clearDetails, smoothAngle",
         "Bake attached 0x8007 surface details (rivets, grooves, lettering, stamps) into real 3D silhouette geometry along analytical limit/smooth normals."},
        {"displace", "mesh", "geometry", "amplitude, frequency, octaves, seed", "fBm displacement along normals."},
        {"deform", "mesh", "geometry", "kind twist|bend|taper, amount", "Barr deformations."},
        {"scatter", "mesh", "surface, geometry", "count, minDistance, seed", "Poisson-disk copies on a surface."},
        // surface details (docs/DETAILING.md)
        {"detailStamp", "detail", "part, points", "shape disc|ring|box|polygon|cross, profile dome|flat|groove|cone|ramp, radius, inner, size, corner, "
                                                  "sides, width, height, fillet, slot, tangent, angle, reach", "One detail at each point."},
        {"fastener", "detail", "part, points|curves", "head rivet|hex|square|cap|stud|slotted|cross, radius, height, washer, pitch | count, turn, seed, "
                                                      "slot, clearance, reach", "Fastener heads at points or along curves."},
        {"rivetLine", "detail", "part, curves", "pitch, radius, height, clearance, reach", "Rivet heads along curves."},
        {"detailStroke", "detail", "part, curves", "profile groove|bead|band|lap|mark, width, depth | height, dash [on,off], step, slot, clearance, reach",
         "A stroke along curves."},
        {"detailSeam", "detail", "part, curves", "width, depth, dash, slot, clearance, reach", "A groove along curves."},
        {"detailTicks", "detail", "part, curves", "pitch | count, length, major, majorLength, width, depth | slot, side centre|left|right",
         "Graduations square to the curves."},
        {"detailText", "detail", "part, points", "text, size, direction, align, profile bead|band|groove|mark, height, width, spacing, slot, reach",
         "Figures and capitals; reads along direction seen from outside the surface."},
    };
    return table;
}

const NodeInfo* find_node_type(std::string_view type) {
    const auto& t = node_reference();
    const auto it = std::find_if(t.begin(), t.end(), [&](const NodeInfo& n) { return n.type == type; });
    return it == t.end() ? nullptr : &*it;
}

}  // namespace pg::doc
