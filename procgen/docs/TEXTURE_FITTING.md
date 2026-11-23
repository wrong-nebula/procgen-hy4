# Texture fitting

2026-11-16, Opus 5.5 (medium), at the owner's request: textures should fit models the way the Swift island's piers did.
Small stones at the base where the model has ribs, big stones where it is flat, small again up the ridge.

## How

- **Zones come from construction.** A builder knows which part is a footing course, a shaft or a coping. Each part
  gets the material slot of its texture and a UV fit to its zone.
- **Charts.** `extrude` takes `chart: "perimeter"`: the sides unroll to u = arc length round the profile and v = height
  along the extrusion, both in metres; the caps keep the profile plane. Lathes, tubes, patches and sweeps already
  carry charts. The option is opt-in, so existing packs are unchanged.
- **`uvFit`.** Maps a chart's v from `span[0]` to `span[1]` (metres) onto the texture rows `band[0]..band[1]`. Bands are
  fractions of the image height, counted from the image's top. It scales u by the same factor, so stones keep their
  proportions round corners and curved heads, and writes metres of a texture `tile` metres wide. The engine tiles
  materials per metre, so `tile` is the material's tile size (Material Maker `tileSizeM`).
- **Convention.** In the pack, v = 0 is the top row of the image (Metal / WebGPU). `pgm export --obj` writes the same
  chart as `vt`, with v negated for tools that count v upward.
- **Checking.** `tools/texture_preview.py` renders an exported OBJ with the texture, to see the fit before the engine
  has the material.

## First use: the island's piers

`isle-pier` uses the Swift trim sheet `Materials/MaterialMaker/maps/lock-island-pier-albedo.png` (preset
`mm-lock-island-pier-ashlar`, tile 1 m) on the new slot `pier-ashlar`:

| Zone | Part | Band (image heights from the top) |
|---|---|---|
| Wet base: small courses, barnacles, algae | six battered footing courses, one sixth of the band each | 0 – 0.29 |
| Tide band | the 1.4 m of shaft below the high-water mark (control `tide`, −1.8 m; citygen da42f5c pictures the isle at low tide, −3.2 m), one zone, darkest at the bottom; replaced a stack of courses on 0 – 0.048, which Engine saw as horizontal streaks | 0 – 0.29 |
| Big ashlar rows | the shaft above the high-water mark, and below the tide band | 0.29 – 0.85 |
| Small courses | the coping | 0.85 – 1 |

## Not yet

- The engine has to bind `pier-ashlar` to the Swift texture set (albedo, height/AO). Until then it shows the slot's
  stand-in colour.
- `v = 0` at the image top: confirmed by Engine (2026-11-16; the pack stores rows top to bottom and WebGPU samples v = 0
  at the first). Binding needs a PNG import in matbuild, which Engine has proposed as its next piece of work.
- Only the pier uses fitting so far. Quay walls, facades (courses per storey) and the greenhouse plinths are next.
- A band cannot repeat within a zone: a zone taller than its band stretches the band. Zones that need repeats
  (long walls) should be split into parts, or use a texture tiled in v.
