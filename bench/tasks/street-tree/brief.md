## The object: `tree` — a Brussels boulevard tree with cast-iron pavement grate and corset guard

A pruned urban street tree (lime / plane tree after Paris & Brussels boulevards, plate s114, s178; atlas FL13, FL41)
planted in a stone-kerbed pavement pit with a radial cast-iron tree grate and an ornate wrought-iron tree-corset guard.

### Frame, controls & port (must match existing callers exactly)
- **Module name:** `tree`
- **Controls:** `height` `7.0` in `[4.0, 12.0]`; `variant` `0` in `[0, 1]` (integer).
- **Port:** `"base"` at `[0, 0, 0]`, normal `[0, 0, -1]`, **`up=(1, 0, 0)`**, type `"footing"`, `{"width": 0.4}`.

### How it is built
1. **Boulevard pit, radial cast-iron grate & wrought-iron corset (`z = 0 .. 1.85 m`):**
   - Low octagonal or circular `chipped-limestone` pavement kerb frame surrounding an `earth` pit bed at `z = 0`, overlaid
     with a perforated two-piece cast-`iron` radial/concentric boulevard tree grate (`~1.25 m` across) with ventilation
     slot reliefs.
   - Rising around the lower trunk (`z = 0.04 .. min(1.80, height * 0.30)`): a classic waisted Parisian/Brussels
     wrought-`iron` **corset tree guard** of 12–16 curved vertical bars flaring gracefully at the base and outward at the
     scrolled top collar, bound by three horizontal `iron`/`bronze` hoop rings with rivets.
2. **Trunk, root flare & scaffold boughs (`wood`):**
   - Tapered organic bole (`z = 0 .. 0.44 * height`) with buttressed root flare at the soil collar, subtle natural curve
     and bark fissure grooves (`detailStroke`), forking cleanly at the crown crotch into **5–7 primary and secondary
     ascending scaffold branches** visible inside and beneath the lower canopy.
3. **Layered architectural & natural canopies (`leaf` + `grass` accent highlights — never a single bumpy golf-ball sphere!):**
   - **Variant `0` (pleached / architectural boulevard lime, s178):** a sculpted tiered avenue crown (`z ≈ 0.38 * height .. height`)
     composed of 5–7 overlapping, softly flattened organic foliage cushions (`catmull_clark` / `subdivide` + mild `displace`
     fBm noise on closed polyhedra, or layered irregular lobes) with crisp underside clearance at `~0.38 * height` and
     varied leaf-tuft detail relief, reading as pruned architectural greenery rather than a geometric sphere.
   - **Variant `1` (open picturesque plane-tree crown, s114):** a broader, asymmetrically tiered cloud of 9–12 distinct
     billowing branch-tip foliage masses (`leaf` with lighter `grass` sun-lit upper crown clusters) leaving small airy
     gaps where the scaffold boughs show through.
