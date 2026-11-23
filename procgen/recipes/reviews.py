# One review per module ahead of an improvement pass: who built it, who reviewed it, what the renders show as weak
# or unverified, and what to improve next (each item names its source: [transcript] the builder's session, [commit],
# a doc, or [review] this review). The kit builders attach these to each module's `references` and regenerate
# docs/KIT_REVIEW.md. Keep notes honest and short; set `by` and `reviewed` to the real model and effort.
from kitlib import review

OPUS = "Opus 5.5 (medium)"
FABLE = "Fable 5.2"

REVIEWS = {
    "rail-track-panel": review("Sleepers and rails only: no chairs, fishplates or keys; ballast is a smooth prism.", ["Add rail chairs and keys as detail, fishplates at the ends [review]", "Ground rail section and sleeper pitch in a reference; both are assumptions [KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "road-viaduct-bay": review("The rail bay's structure under a sett carriageway and raised pavements, as citygen's stand-in; built for the road twin id (citygen kit.cpp), not yet placed in any checked city.", ["No kerb edge, gullies or lamp standards [review]", "Check against a placed road viaduct once a preset uses one [not verified]"]),
    "road-long-span": review("The rail long span's structure under a sett carriageway and raised pavements; not yet placed in any checked city.", ["The through truss's portal clearance over the pavements is not checked [not verified]", "Check against a placed road span [not verified]"]),
    "rail-viaduct-bay": review("Masonry and inhabited arches read well; the trestle's gussets and parapet lattice are too fine to see at sheet size; no bearings or drainage.", ["Ground pier and arch proportions; both are assumptions [KIT_REFERENCES]", "Add bearings and deck drainage spouts [review]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "rail-long-span": review("The truss reads as a box of thin bars at sheet size; no bearings at the abutments.", ["Model the red tubular bowstring of s171 as a variant [KIT_REFERENCES]", "Add bearings at the abutments; keep top heights in step with citygen's walkway clearance [transcript]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "rail-hall": review("Gabled roof is plain sheets with no ridge vent; the vaulted hall's glazing grid is uniform end to end.", ["The vaulted variant stretches the greenhouse bay to about 31 m, past G01's 6-18 m; find a large-span reference [transcript]", "Ground the frontispiece; add a ridge vent to the gabled roof [KIT_REFERENCES][review]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "quay-wall-bay": review("Wall faces are plain stone without coursed joints; the fender reads well.", ["Coursed joints on the wall face with the courses helper [review]", "Ground the bullnose coping [KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "quay-stair": review("No handrail; treads are plain blocks without nosings.", ["Add a handrail and stone nosings [review]", "SY04 is a steel stair; this one is stone by choice [KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "quay-bollard": review("Reads well in a close-up; anchor nuts and washers clear.", ["Confirm with the owner that nuts-as-geometry with washers and bolt ends as detail is what was meant [transcript]", "Ground the mushroom head and the 0.94 m footing, taken from geometry [KIT_REFERENCES][commit 2d017a4]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "tram-track-panel": review("Rails sit on a plain paving slab: no setts and no grooved rail section.", ["Grooved rail section and setts as coursed detail [review]", "Ground the street track; s50 only fixes scale [KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "tram-contact-wire": review("Two bare wires, invisible at sheet size: no droppers, hangers or insulators.", ["Add droppers, cross-span hangers and insulators [review]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "tram-pole": review("Very thin at sheet size; the arms are hard to read.", ["Thicken or detail the arms so they read at distance; ground the pole beyond borrowed LL01/N04 parts [review][KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "canal-footbridge": review("Girder variant's deck is plain timber with no planks; the humpback's iron ribs look light under the stone balustrade.", ["Plank joints on the girder deck; heavier ribs under the humpback [review]", "Ground rise and member sizes [KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "canal-road-bridge": review("Stone arches read well; the plate girders carry no stiffeners or rivets.", ["Find why the humpback balusters render pale though assigned iron [transcript]", "Stiffeners and rivet rows on the plate girders; pierced spandrels of s245 left out by choice [review][transcript]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "lock-gates": review("Leaves read as flat slabs patterned by their material, not as planked or framed gates; no walkway rails.", ["Model the SY09 winches and a walkway rail on the beams [KIT_REFERENCES][review]", "Plank or frame the leaves instead of a flat patterned slab [review]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "lock-cabin": review("Reads well; no gutters, downpipes or chimney.", ["Gutters, downpipes, chimney; ground the barrel roof [review][KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "rowboat": review("Reads well; no oars or rowlocks.", ["Oars and rowlocks [review]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "pier": review("Pile bents and the planked deck read in a close-up; lashings not visible at sheet size.", ["Ground bent spacing [KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "dry-dock": review("Floor is a plain slab; the walls lack altar steps; crane rails are thin.", ["Altar steps along the walls, floor blocks; proportions follow the old 23 x 3.2 m model [review][KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "hull-on-stocks": review("Plating reads only in close-ups; shores cross the frame lines in places.", ["Profile the ~120-137 ms evaluation (never profiled) [transcript]", "Chase the two light slivers where a lap crosses a facet near the sheer [transcript]", "Shores cross the frame lines; laps are faint under the rust finish [review][transcript]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gantry-crane": review("Reads well; hook block small; gusset rivets not visible at sheet size.", ["Larger hook block; gusset rivets only show close up [review]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "slipway": review("A plain slab with two rails: no ground ways, cradle or keel blocks.", ["Ground ways, a cradle and keel blocks; the piece is an assumption throughout [review][KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "erection-shed": review("Mostly bare frame; the covered bays are flat panels; truss members thin at sheet size.", ["Clad more bays with real sheeting (laps, rivets); heavier trusses [review]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "deck-capstan": review("Reads well in a close-up; the barrel's weathered shading is blotchy.", ["Confirm the nut treatment with the owner [transcript]", "Even out the weathered barrel shading [review]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "roller-fairlead": review("The two rollers render in different colours (one grey, one rusted): check whether that is intended.", ["Check the rollers' different colours (one grey, one rusted) [review]", "Confirm the nut treatment with the owner [transcript]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "atlantic-shell": review("Seams, rivet rows and the number read in close-ups; the nose is smooth, with no smokebox door.", ["Smokebox door and front detail on the nose [review]", "Built from brief and study sheets only; panel layout is assumed [transcript][KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "atlantic-driver-pair": review("Spokes, balance weight and boss read; the witness mark is tiny.", ["Coupled motion of wheels and rods is not carried over [transcript]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "atlantic-running-gear": review("Coupling rods read; no valve gear beyond them.", ["Shaped cylinder casings instead of plain drums; valve gear [transcript][review]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "atlantic-leading-truck": review("Not looked at alone; reads as a plain wheelset inside the locomotive.", ["Look at it alone; ground it beyond the exploded study view [review][KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "atlantic-trailing-axle": review("Not looked at alone; reads as a plain wheelset inside the locomotive.", ["Look at it alone; ground it beyond the Type 12 arrangement [review][KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "atlantic-tender": review("Lapped side sheets read; no coal space or water filler on top.", ["Coal space and water filler on top [review]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "rail-locomotive": review("Assembled from its kit; the default near-black livery leaves the detail low in contrast.", ["Confirm the Atlantic night mail is the locomotive the owner meant [transcript]", "13.5 m is citygen's compressed length; a lighter default livery would show the detail [transcript][review]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "rail-carriage": review("Plain body and window band: no doors, handrails or vestibules; bogies plain.", ["Doors, handrails, vestibules, detailed bogies [review]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "inspection-plate": review("The reference plate; reads well.", [], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "riveted-panel": review("Reads well.", [], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "access-hatch": review("Reads well; hinges are detail so they never stand off the plate.", ["N05 asks for a catalogue of latches and hinge covers; only one built [KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "louvre-vent": review("Blades are ramp details, so no openings show at grazing angles.", ["Real openings between blades if seen at grazing angles [review]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "number-plate": review("Reads well; figure size capped by length since 3ce02b4 (it overran tall plates).", ["Lower-case and serif lettering are missing from the generator [DETAIL_VOCABULARY]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "pressure-gauge": review("Reads well.", [], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "bolted-flange": review("Reads well; hex heads are detail and stay flat in silhouette.", [], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "tread-plate": review("Chequer bars are fine detail, lost beyond a few metres.", ["Mip or fade the chequer for distance [review]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "ashlar-panel": review("Reads well.", ["Joints round arches and openings (voussoirs) are not built [DETAIL_VOCABULARY]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "copper-vault-panel": review("Reads well.", [], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "slate-field": review("Slates are details on a flat deck: no stepped silhouette at the eaves.", ["A stepped silhouette at the eaves; cite an atlas recipe [review][KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-vault-rib": review("Thin at sheet size; its rivet rows not visible there.", ["Spans outside G01's 6-18 m are extrapolations [KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-column": review("Reads well; flutes are bead details.", [], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-splice": review("Reads well; bolts are detail.", ["The old model drilled real holes; now bolts are detail only, by reach [KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-station": review("Reads well.", ["One frame owner per joint is a hand-kept convention; a check would help [transcript]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-roof-bay": review("Reads well; uniform glazing grid.", ["Pane widths are model dimensions; G06 records none [KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-wall-bay": review("Reads well.", [], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-end-screen": review("Doorway reads; the screen is a flat uniform grid.", ["Vary the screen's grid; it is uniform [review]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-bay": review("Reads well.", ["Joints need matching section ports; mismatched spans are rejected [transcript]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-gallery": review("Reads well in a close-up.", [], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-lean-to": review("Reads well; eave minimum raised to 1.5 m in 3ce02b4 (its wall rejected less).", ["Ground the quarter-vault proportion [KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-crossing": review("Reads well; its column spring could exceed gh-column's range (gh-column widened to 40 m in 3ce02b4).", ["Reconcile portal span 0.765 x radius with citygen's rotunda before a swap [SITE_KIT_BACKLOG]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-railing": review("Leaf-ribbon panels read well in a close-up.", [], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-planter": review("Fern blades are undivided flat straps.", ["Divide the fern blades as the atlas specimen does [KIT_REFERENCES]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-pendant": review("Reads well, small.", [], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-bridge-bay": review("Reads well.", [], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-terrace": review("Reads well in a close-up.", [], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-potting-house": review("Assembly; not looked at in this review.", ["4.2 m span is below G01's range; look at the assembly [KIT_REFERENCES][review]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-orangery": review("Assembly; not looked at in this review.", ["Uses per-plant tint the previewer cannot show; look at it in the engine [transcript]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-winter-garden": review("Assembly; not looked at in this review.", ["Pack grew 1.3 to 3.1 MB after baking; glass is opaque in the engine until a transparency pass [transcript]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "gh-palm-house": review("Assembly; not looked at in this review.", ["Look at the assembly [review]"], by=FABLE, reviewed=OPUS, seen="sheet renders"),
    "tram-stop": review("Lettering checked in a close-up. Flagstone joints not seen at sheet size. The canopy brackets meet the posts by eye, not by ports.", ["Seat the canopy brackets on ports, not by eye [review]"]),
    "tram-car": review("Tinted yellow, but the previewer ignores tint, so the yellow was never seen. Lengths other than 9 m stretch the whole car, wheels included. Lifeguards are blocky.", ["Check the yellow in the engine; keep wheels fixed when the body stretches [review]"]),
    "rail-signal": review("No reference image shows a signal: every size is a design assumption. The arm has no red (no red material); its stripe is too fine to see at sheet size.", ["A red material for the arm; ground the post in a reference [review]"]),
    "bench": review("Seat bolt heads not visible at the sizes rendered; the stone variant's iron back stays barely show.", ["Larger bolt heads or a close-up check [review]"]),
    "platform-clock": review("Minute graduations too fine to see at the sizes rendered; the hands are fixed at one time.", ["Close-up check of the graduations [review]"]),
    "tree": review("Leaf clusters are dome details on a smooth crown, so the silhouette stays smooth; no bark detail; limbs hidden inside the crown.", ["Break the crown silhouette (geometry lobes or alpha cards); bark detail [review]"]),
    "steamer": review("Funnels are copper and the hull black for want of buff and red materials. Plating rivets not visible at the sizes rendered. Lifeboats sit on the deckhouse roof without chocks.", ["Buff and red materials for funnels and boot-top; chocks under the boats [review]"]),
    "tug": review("Same hull limits as the steamer; no bow fender.", ["A bow fender [review]"]),
    "barge": review("The rudder is a plain plate; the hold covers are a row of identical gables.", ["Shaped rudder; vary the hold covers [review]"]),
    "hammerhead-crane": review("Jib is level-chorded where real hammerheads often taper; lattice joints have no gussets; chord rivets not visible at the sizes rendered.", ["Taper the jib; gusset plates at the lattice joints [review]"]),
    "portal-crane": review("The cab hides behind the house from the usual view; the swan neck is a plain box section with no plating detail.", ["Bring the cab into view; plating detail on the swan neck [review]"]),
    "canal-swing-bridge": review("Stays hard to see at sheet size; no turning gear or rack. No citygen preset places it, so only its port convention is checked.", ["Turning gear and rack; heavier stays [review]"]),
    "isle-house": review("Finishes, shop signs, openings, chimney, leader and lantern follow the original's scripts; the houses are taller and deeper (citygen's 4.2 / 3.6 m storeys and 9-14 m depths against the original's 2.84 m and 3.7-9 m). Mansard and street gable are design assumptions (the original has only pitched zinc roofs). Signs read only close up.", ["Adapt to curved rows once citygen bends them: angled party walls, wedge plots, corner houses [review]", "Dormers on wide pitched houses, as in the original [isle-dimensions]", "Interiors not modelled [review]"]),
    "isle-warehouse": review("Loading doors, platforms and hoist read; the two variants differ by their sign only; the hoist hook is small.", ["Original variants differ by footprint; contents (iron stock, lantern oil) not modelled [isle-dimensions]"]),
    "isle-pump-hall": review("Hall sizes are assumptions: the original hall came from an external library; the gallery has no stair to it; tall windows are plain.", ["Gallery stairs and the pump machinery court of the original [ISLE.md]"]),
    "isle-workshops": review("Monitor roof and big doors read; the monitor is a glass box without the iron trusses below; no cart apron.", ["Iron trusses under the monitor (9 stations in the original) [isle-dimensions]", "Cart apron, if citygen leaves room for it [review]"]),
    "isle-customs": review("Passage arch, voussoirs, quoins (detail) and pier windows read; the passage has no floor slab; the window grid is simpler than the original's.", ["Window grid of 4 columns x 2 levels on each end face, as in the original [isle-dimensions]"]),
    "isle-stores": review("Arcade arches and loading doors read; no wall lantern (the front wall is set back); no hoist.", ["Cargo hoist and its apron guard [isle-dimensions]"]),
    "isle-archive": review("Facade after the first-pass script (the final facade was an external library); garden walls without planting; no interior.", ["Garden planting and the survey tables of the original interior [isle-dimensions]"]),
    "isle-harbour-house": review("Shop window, sign and the chandlery's awning read; contents not modelled.", []),
    "isle-gatehouse": review("The tide board's graduations are fine detail; the facade beyond the original's clay mass is a design assumption.", []),
    "isle-pier": review("Rebuilt to match the Swift island's piers (owner's request, so their materials and textures can be ported): battered footing courses, ashlar joints as detail (round heads included), coping over a dark reveal, iron mooring rings, oak rubbing posts with iron bands, bollards. citygen's 4.6 m depth to bed makes them lower than the Swift towers (8.7-13.8 m); at bed -12 they match. The masonry carries the Swift trim sheet's bands (wet base on the footing, ashlar on the shaft, small courses on the coping) through fitted UVs on the new pier-ashlar slot; checked with tools/texture_preview.py, not yet in the engine. Low tide (citygen da42f5c): the 1.4 m below the high-water mark (control tide, -1.8) carry the sheet's whole wet zone as a tide band (a first try with stacked 0-0.048 courses read as streaks in the engine). Checked in tools/texture_preview.py, not yet in the engine.", ["Compare the tide band's look with the Swift pier at low water [owner]", "Bind the Swift texture set to the pier-ashlar slot in the engine; the UVs are already fitted to its trim sheet (docs/TEXTURE_FITTING.md) [owner]", "Ask Worldgen whether the island's piers should stand taller (Swift heights) [review]", "Joints on the cutwater and octagon head faces [review]"]),
    "isle-berth": review("Deck, piles, collars, hooks and ladder read; no cords or safety chain; piles a fixed 3 m (no bed control).", ["Cords and the safety chain [isle-dimensions]"]),
    "isle-fountain": review("Basins, water and benches read; the paving's every-fifth blue slab is not split out (one material).", []),
    "isle-monument": review("Plinth, plaque and benches read; the figure is a simplified bronze.", ["A figure closer to the original statue [review]"]),
    "isle-strand-edge": review("A shelving wet slope and a damp band; reads as the waterline only once Engine binds strand-wet and strand-damp.",
                        ["Uneven swash line (displace or a wavy outline) instead of a straight band [render]",
                         "Engine materials for strand-gravel, strand-shingle, strand-damp, strand-wet (stand-in colours now) [not verified]",
                         "citygen does not lay it along the shore yet [contract]"]),
    "isle-wall-foot": review("A battered toe with one bed joint and a damp band; plain.",
                        ["Single rubble blocks instead of one prism [render]", "Head joints [render]",
                         "citygen does not place it under the quay walls yet [contract]"]),
    "isle-halt": review("Two flagged platforms on raking brackets, railings, lamps, a straight stair to the ground; matches the curved-isle contract.",
                        ["The stair's stringer wall is a blank triangle: add coursed joints or an arch under it [render]",
                         "Platforms float until the viaduct seats them: check against rail-viaduct-bay in a placed city [not verified]",
                         "Benches and a name board [KIT_REFERENCES]"]),
}

# Improvement pass 1 (Opus 5.5 medium, 2026-11-15): what was done and what is left, per module touched. Modules not
# listed were not touched: their whole improve list is left.
PASS1 = {
    "quay-wall-bay": ("Coursed joints on the water face (seam detail).", ["Ground the bullnose coping [KIT_REFERENCES]"]),
    "quay-stair": ("Bead nosing on every tread; iron handrail on standards on the +y side.",
                   ["Handrail on one side only; standard spacing is an assumption [review]", "SY04 is a steel stair; this one is stone by choice [KIT_REFERENCES]"]),
    "tram-track-panel": ("Setts as coursed joints in the paving; a flangeway groove inside each rail.", ["Ground the street track; s50 only fixes scale [KIT_REFERENCES]"]),
    "rail-track-panel": ("Cast chairs under both rails on every sleeper (detail).", ["Fishplates and keys; ground rail section and sleeper pitch [review][KIT_REFERENCES]"]),
    "tram-contact-wire": ("Bronze ears clamp each wire every ~4.5 m.", ["Cross-span hangers and insulators at the poles [review]"]),
    "lock-cabin": ("Brick chimney, zinc gutters on both eaves and a downpipe.", ["Ground the barrel roof [KIT_REFERENCES]"]),
    "rowboat": ("A pair of oars along the thwarts and two bronze rowlocks.", ["The oars read faintly from above [review]"]),
    "pier": ("Rechecked: the deck is already planked; the sheet was too small to show it.", ["Ground bent spacing [KIT_REFERENCES]"]),
    "canal-road-bridge": ("Investigated the pale humpback balusters (canal-footbridge 5): a close-up shows them iron; at sheet size the pale run is "
                          "the far side's newels and deck seen through the balustrade. No change.", ["Stiffeners and rivet rows on the plate girders [review]"]),
    "roller-fairlead": ("Investigated the mismatched rollers: not a module defect. The previewer rusts weathered-iron with noise about 0.4 m across, "
                        "so two rollers 0.34 m apart differ.", ["Finer rust noise in the previewer [review]", "Confirm the nut treatment with the owner [transcript]"]),
    "tug": ("A rope fender round the bow.", ["The fender reads faintly at sheet size [review]"]),
    "steamer": ("Chocks under the lifeboats.", ["Buff and red materials for funnels and boot-top; materials start on the engine side [review]"]),
    "barge": ("Shaped rudder blade on a stock, in place of the plate.", ["Vary the hold covers [review]"]),
    "canal-swing-bridge": ("Stays and posts made heavier (0.26 and 0.18 m) so they read at sheet size.", ["Turning gear and rack [review]"]),
    "portal-crane": ("Cab moved to the -y side, where the usual views see it.", ["Plating detail on the swan neck [review]"]),
    "tram-car": ("Only the body stretches with length; the truck keeps its wheelbase.", ["Check the yellow in the engine [review]"]),
}

# Contact audit (pgm audit, built in pass 1): pieces it found detached and pass 1 seated.
AUDIT_FIXED = {
    "rail-signal": "Audit: arm and spectacle seated on the upright, lenses on the spectacle.",
    "tram-car": "Audit: lifeguards up to the floor, end lights from dash to roof, roof shell seated on the body.",
    "tram-stop": "Audit: screen mullions and transom, panes from sill to head, flat roof on its posts; canopy on eaves and cross beams.",
    "tug": "Audit: mast on the wheelhouse, hook from its post, tyres against the hull.",
    "barge": "Audit: bollards follow the hull's breadth and sheer; coamings seated in the deck at short lengths.",
    "canal-swing-bridge": "Audit: cross girders over the roller path carry the deck at every width.",
    "rowboat": "Audit: thwarts reach the planking, a breasthook carries the cleat, rowlocks sit in the gunwale (also the steamer's boats).",
    "lock-cabin": "Audit: lights fill the band between dado and lintel, with mullions.",
    "quay-stair": "Audit: handrail standards run from the treads up to the rail.",
    "tram-contact-wire": "Audit: declared as two pieces (the two wires).",
    # baseline findings fixed 2026-11-16 (Opus 5.5 medium); the audit baseline is now empty
    "gh-roof-bay": "Audit: panes reach the purlins (inset 0.035 -> 0.012 of a strip).",
    "gh-wall-bay": "Audit: panes reach the mullions and transoms; the gutter runs back to the wall.",
    "gh-end-screen": "Audit: panes reach their bars (inset 0.04 -> 0.015).",
    "gh-bay": "Audit: panes reach their bars; the floor tiles are laid in a mortar bed.",
    "gh-gallery": "Audit: as gh-bay (panes on bars, tiles in a bed).",
    "gh-lean-to": "Audit: as gh-bay; eave minimum 2.0 m (the column folds below ~1.8 m).",
    "gh-crossing": "Audit: glazing bars added to the portal spandrels; panes reach the bars; lantern capped at 1.15 x span; span minimum 6 m.",
    "gh-column": "Audit: spring minimum 2.0 m (below ~1.8 m the shaft profile folds into the pedestal).",
    "gh-station": "Audit: spring minimum 2.0 m, as gh-column.",
    "gh-bridge-bay": "Audit: deck slab up to the deck port (z 0), meeting the railings.",
    "gh-terrace": "Audit: slab up to the entry port (z 0), piers into the slab.",
    "gh-orangery": "Audit: the pendants' mount is inside the ridge beam.",
    "gh-palm-house": "Audit: fixed through gh-bay and gh-gallery.",
    "gh-potting-house": "Audit: fixed through gh-bay.",
    "gh-winter-garden": "Audit: fixed through gh-bay, gh-gallery and gh-terrace.",
    "rail-hall": "Audit: platforms down onto the podium (8 cm gap); gabled wall lights framed with bars and a head rail; vaulted glazing via gh-bay.",
    "rail-long-span": "Audit: bearing shoes (E02) on the pier caps carry the floor; hangers run between tie and rib chord.",
    "rail-viaduct-bay": "Audit: trestle gussets against the leg faces (5 mm gap).",
    "canal-footbridge": "Audit: humpback spandrel posts from inside the rib up into the deck slab; rib sampled finer.",
    "lock-gates": "Audit: balance beams rest on the heel and mitre posts. Sluice fittings added as on/off controls (rack, grating, chain, ladder) for the Elbe island.",
    "pier": "Audit: the first bollard stays on the deck at short lengths.",
    "hull-on-stocks": "Audit: keel blocks reach the keel where it rises toward the ends.",
    "atlantic-driver-pair": "Audit: cast crank bosses on the wheels carry the crank pins.",
    "atlantic-leading-truck": "Audit: frame on the axles, bolster on the frame.",
    "atlantic-running-gear": "Audit: valve chest against the cylinder.",
    "atlantic-shell": "Audit: running board carries the casing on the chassis; bogie pivot and trailing horns reach the truck and axle seats.",
    "atlantic-tender": "Audit: axleboxes between axles and frame; a drawbar to the engine.",
    "rail-locomotive": "Audit: fixed through its parts; holds together from 6 to 27 m.",
}


def audit_baseline():
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "tests" / "data" / "audit-baseline.json"
    out = {}
    for doc, r in json.loads(p.read_text()).get("documents", {}).items():
        out[Path(doc).stem] = f"Contact audit: up to {r['components']} pieces; {r['why']} [audit]"
    return out

CROSS_CUTTING = [
    "One mesh per material; baked prototypes are overlapping closed solids, not one volume [transcript]",
    "Ports, collision boxes and walk triangles are not written to packs yet (engine reads 0x8002, 0x8004, 0x8005) [ENGINE_PACK_NOTES]",
    "The previewer ignores tint and the UV charts [transcript]",
    "The engine does not read the detail section (0x8007) and has no bevel shader; the owner has not signed off the detailing design [ENGINE_PACK_NOTES][DETAILING]",
    "Missing detail helpers: hoops round sleeves, voussoir joints, leaded glass, sgraffito, lower-case and serif lettering [DETAIL_VOCABULARY]",
    "Missing materials: no red, buff or livery colours [review]",
    "Sheet renders are too small to check rivets and fine detail; close-ups are needed per module [review]",
    "Many sizes are design assumptions; the tram and street pieces are the least grounded [KIT_REFERENCES]",
    "pgm assemble for citygen's placement list is not built [SITE_KIT_BACKLOG]",
    "Pass 1 fixed the node reference: instance reads translation as well as rotation and scale [review]",
    "Pass 1 built the contact audit (pgm audit, gate stage) and fixed all 28 findings it raised in older modules; every recipe document holds together across its control sweep and the baseline is empty [audit]",
]


def attach_reviews(refs):
    found = audit_baseline()
    for module, r in REVIEWS.items():
        if module in refs:
            r = dict(r)
            done, left = PASS1.get(module, ("Not touched.", r.get("improve", [])))
            if module in AUDIT_FIXED:
                done = AUDIT_FIXED[module] if done == "Not touched." else done + " " + AUDIT_FIXED[module]
            left = list(left) + ([found[module]] if module in found else [])
            r["pass1"], r["left"] = done, left
            refs[module]["review"] = r
    return refs
