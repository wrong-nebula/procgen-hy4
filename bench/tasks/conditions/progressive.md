## Progressive construction across shearing layers

Work from coarse to fine across three structural pace layers (`verify` and `preview` report which layer you are on):

1. **Layer 1 — Site, Envelope & Ports:** Settle closed manifold solids seated cleanly on `z = 0`, matching the contract's overall envelope `[x, y, z]` and every port's origin, normal and dimensions across control sweeps. Do not add secondary ornament yet.
2. **Layer 2 — Load-Bearing Skeleton & Clear Openings:** Establish walls, piers, roofs, stairs and glazing frames with zero detached components, zero overhanging/unattached brackets (`pgm audit --sweep --dangling`), and unobstructed walk-through / bore clearances.
3. **Layer 3 — Joinery, Surface Details & Inscriptions:** Once Layers 1–2 verify cleanly, enrich mouldings, sash muntins, analytical surface details (`detail_groove`, `detail_stamp`, `detail_bead`) and `kittype` signage while keeping `verify` green.

`preview` locks the camera to your contract envelope so proportions do not jump between turns, and retains your last valid mesh on screen if an edit fails to build.
