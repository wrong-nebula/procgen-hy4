#!/usr/bin/env python3
"""Write docs/KITTYPE.md from the signatures and docstrings in recipes/kittype.py."""
import inspect
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "recipes"))
import kittype  # noqa: E402

out = [
    "# kittype: panel-bound lettering and signage for kit builders",
    "",
    "Generated from `recipes/kittype.py` by `tools/kittype_doc.py`; do not edit by hand.",
    "",
    inspect.getdoc(kittype),
    "",
    "## Functions",
    "",
]
for name, fn in inspect.getmembers(kittype, inspect.isfunction):
    if fn.__module__ != "kittype" or name.startswith("_"):
        continue
    out += [f"### `{name}{inspect.signature(fn)}`", "", inspect.getdoc(fn) or "", ""]
out += [
    "## Faces",
    "",
    "- `plain`: native `detailText` single-stroke capitals and figures (1 node, ASCII-normalised).",
    "- `roman`: classical architectural capitals with bracketed serifs, curved bowls and French accents (`detailStroke`).",
    "- `nouveau`: condensed Brussels / Paris 1900 sign-painter capitals with high crossbars (`y = 3.85`) and accents (`detailStroke`).",
    "",
    "Standalone kit: `recipes/build_signage_kits.py` builds `recipes/site/signage/` (`fascia-sign`, `house-plaque`, `arch-sign`).",
    "Verification: `python3 tools/test_kittype.py`.",
    "",
]
(HERE / "docs" / "KITTYPE.md").write_text("\n".join(out))
print("wrote docs/KITTYPE.md")
