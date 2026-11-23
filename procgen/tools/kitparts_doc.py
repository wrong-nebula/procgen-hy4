#!/usr/bin/env python3
"""Write docs/KITPARTS.md from the signatures and docstrings in recipes/kitparts.py."""
import inspect
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "recipes"))
import kitlib  # noqa: E402,F401
import kitparts  # noqa: E402

out = ["# kitparts: reusable parts for kit builders", "",
       "Generated from `recipes/kitparts.py` by `tools/kitparts_doc.py`; do not edit by hand.", "",
       inspect.getdoc(kitparts), "", "## Parts", ""]
for name, fn in inspect.getmembers(kitparts, inspect.isfunction):
    if fn.__module__ != "kitparts" or name.startswith("_"):
        continue
    out += [f"### `{name}{inspect.signature(fn)}`", "", inspect.getdoc(fn) or "", ""]
out += ["## Named mouldings", "", ", ".join(f"`{k}`" for k in kitparts.MOULDINGS), "",
        "Example: `tools/test_kitparts.py` builds a street-arcade bay and a flanged pipe with a handwheel from these parts.", ""]
(HERE / "docs" / "KITPARTS.md").write_text("\n".join(out))
print("wrote docs/KITPARTS.md")
