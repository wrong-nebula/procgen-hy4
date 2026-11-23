## The object: `pier` — a heavy timber-and-iron harbour finger pier

A working late-19th-century harbour finger jetty extending from the quay wall at **`x = 0`** seaward along **`+x`** to
`x = length`, with its walking/working deck surface at **`z = 0`** and its driven timber piles reaching down to the
harbour bed at **`z = bed`** (negative, `-12.0 .. -1.5 m`).

### Frame, controls & port (must match existing callers exactly)
- **Module name:** `pier`
- **Controls:** `length` `30.0` in `[6.0, 60.0]`; `width` `6.5` in `[2.0, 12.0]`; `bed` `-4.6` in `[-12.0, -1.5]`.
- **Port:** `"root"` at `[0, 0, 0]`, normal `[-1, 0, 0]`, type `"deck"`, `{"width": "$width"}`.
- Keep all geometry self-contained (do not rely on `moduleImports` so the module bakes cleanly, while modelling crisp
  cast-`iron` twin-horn mooring bollards directly on the pier!).

### How it is built
1. **Driven timber pile bents & iron hardware (`z = bed .. -0.20 m`):**
   - Bents spaced every `~3.2 m` along `x` (`mx(2, rnd(length / 3.2))`): 3–5 stout tapered `wood` bearing piles per bent
     (`z = bed .. -0.32`), flanked by slightly battered outer fender piles protecting the pier flanks, `strand-damp` /
     `black` tarred tidal sleeves near the waterline, heavy corbelled double-clamp crosshead cap beams, transverse
     diagonal X-braces (`z` scaled cleanly inside `[bed + 0.25, -0.45]` so nothing ever drops below `bed` even when
     `bed = -1.5`!), and bolted `weathered-iron` / `iron` gusset strap plates with hex bolt details.
2. **Stringers, cambered plank deck & heavy wheel kerbs (`z = -0.28 .. +0.22 m`):**
   - Deep longitudinal `wood` stringers (`4–6` lines across `width`), transverse timber deck planking (`wood` with crisp
     plank-seam detail grooves and spike rows rather than hundreds of loose disconnected boards, keeping triangle counts
     tight even at `length = 60`!), heavy chamfered timber wheel-guard kerbs shod with `weathered-iron` angle rubbing
     strips along `y = ±(width/2 - 0.12)` and across the seaward nose `x = length`.
3. **Berthing gear, fender dolphins & iron access ladder (`z = bed .. +1.10 m`):**
   - Paired cast-`iron` mooring bollards with `bronze` cap rings and bolted base plates along both kerbs (`x` clamped inside
     `[0.8, length - 0.8]` so short `6 m` piers stay valid!).
   - Vertical timber rubbing fenders with `iron` wear straps along both sides, two clustered 3-pile bound **fender
     dolphins** (`wood` piles bound with `iron` hoops and conical `bronze`/`iron` weather caps rising to `z ≈ +0.95 m`)
     guarding the two seaward corners at `x ≈ length`, and a recessed wrought-`iron` boatman's ladder on one flank.
