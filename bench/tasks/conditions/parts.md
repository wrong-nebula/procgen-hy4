## Ready-made parts

`recipes/kitparts.py` has documented, tested parts (see `docs/KITPARTS.md`): an arch fitted to the opening
(`fit_arch`), the arched wall with its piers and spandrels (`arched_wall`), a ring of voussoirs with a keystone
(`voussoirs`, with `fit_ring_depth`), channel joints and ashlar coursing, named mouldings (cornice, string course,
impost, plinth, coping), bored and bolted flanges (`bolted_flange`), a spoked handwheel (`spoked_wheel`), tie rods with
anchor plates (`tie_rod`), and OpenSubdiv SDS / FWN hard-surface parts (`cast_housing`, `sds_cylinder`, `filleted_box`,
`curved_boss_patch`). They take kitlib expressions, so they follow your controls. `examples/parts_demo.py` builds a
street-arcade bay, a flanged pipe, and an SDS/FWN cast housing from them (read it; do not run it into your kit).

Build from these parts where they fit, and put your own effort into proportions, the parts they don't cover and
detail. You may wrap or adapt them in your builder; do not edit `kitparts.py` itself.
