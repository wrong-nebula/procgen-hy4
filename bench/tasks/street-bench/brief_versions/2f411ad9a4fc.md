## The object: `bench` — a Brussels boulevard & tram-stop bench

A period public bench for squares, quays and tram halts in a Art Nouveau Brussels city, facing **`-y`** (a seated
person looks toward `-y`, with the backrest on the `+y` side) and standing on `z = 0`, centred on `x = 0`.

### Frame, controls & port (must match existing callers exactly)
- **Module name:** `bench`
- **Controls:** `length` `1.8` in `[1.0, 4.0]`; `variant` `0` in `[0, 1]` (integer).
- **Port:** `"base"` at `[0, 0, 0]`, normal `[0, 0, -1]`, **`up=(1, 0, 0)`**, type `"footing"`, `{"width": "$length"}`.
- For `length > 2.4 m`, include an intermediate centre support standard (`when(gt(L, 2.4), ...)` or dynamic count) so
  long tram-stop benches (`3.2 m`) never sag Visually!

### How it is built
1. **Ergonomic slatted timber seat & backrest (`wood` + `bronze` carriage bolts):**
   - Curved profile: 6–7 shaped `wood` slats following a gentle S-contour — a rolled front waterfall knee edge (`z ≈ 0.43 m`,
     `y ≈ -0.24 m`), dipping slightly at the hip (`z ≈ 0.40 m`, `y ≈ 0.10 m`), then rising up an inclined lumbar backrest
     (`y ≈ 0.16 .. 0.28 m`, `z ≈ 0.48 .. 0.90 m`) crowned with a thicker rounded top crest rail.
   - Domed `bronze` carriage-bolt heads (`G.fasteners`) where every slat crosses the standards, plus wrought-`iron`
     under-seat strap ribs and a longitudinal sway-tie stretcher bar beneath the seat (`z ≈ 0.20 m`) tying all standards
     together.
2. **Variant `0` — Civic limestone pedestal bench:**
   - Sculpted dressed `chipped-limestone` end standards (`profileSolid` with bevelled architectural scroll profile,
     stepped base shoe, carved sunken side panel / rosette boss, and moulded seat corbel) paired with raked wrought-`iron`
     backrest stays bolted to the stone with `bronze` collars and scrolled top finials.
3. **Variant `1` — Art Nouveau cast-iron scroll bench:**
   - Openwork painted-green cast-`iron` end frames: splayed front and rear lion-paw / volute feet on `black` base pads,
     sweeping whiplash S-scroll side frames, sculpted scrolled armrests (`z ≈ 0.63 m`) with `bronze` terminal bosses,
     and a decorative rear tie-rod stretcher with a central `bronze` collar ring so the `+y` back view is richly detailed.
