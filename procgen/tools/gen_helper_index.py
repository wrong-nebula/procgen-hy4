#!/usr/bin/env python3
"""Kit helper index: the catalogue behind `kitlib.py --helpers`, the `kit_helpers` MCP tool and the generated
stub block inside `class Graph` in recipes/kitlib.py. Built by Opus 5.5 (medium).

The part helpers live in kitlib.py and in attached modules (kitlib_iron, kitlib_masonry, ...) that set
`Graph.<name> = fn` when kitlib is imported, so a grep of kitlib.py alone does not find them. This script
writes, between the markers in kitlib.py, one forwarding method per attached helper (the module's own
assignment replaces it at import, so behaviour is unchanged) and a `# def name(...)` comment line per
module-level helper function, so `grep "def "` finds every helper.

    python3 procgen/tools/gen_helper_index.py            # regenerate the block in kitlib.py
    python3 procgen/tools/gen_helper_index.py --check    # exit 1 if the block is stale
"""
import importlib
import inspect
import re
import sys
from pathlib import Path

RECIPES = Path(__file__).resolve().parent.parent / "recipes"
KITLIB = RECIPES / "kitlib.py"
BEGIN = "    # BEGIN GENERATED HELPER INDEX (procgen/tools/gen_helper_index.py; do not edit by hand)"
END = "    # END GENERATED HELPER INDEX"

# Attached modules in index order, with the family name the query groups by.
FAMILIES = [
    ("kitlib", "core"), ("kitlib_ornament", "ornament"), ("kitlib_aero", "aero"), ("kitlib_iron", "iron"),
    ("kitlib_masonry", "masonry"), ("masonry_bonds", "masonry"), ("kitlib_orders", "orders"),
    ("kitlib_pipes", "pipes"), ("kitlib_glazing", "glazing"), ("kitlib_curves", "curves"),
    ("kitlib_tracery", "tracery"), ("kitlib_arch", "arch"), ("kitlib_mech", "mech"),
]
FAMILY = dict(FAMILIES)
# Graph plumbing in kitlib.py itself: listed by --helpers only when nothing else matches is not useful, so
# the query leaves these node wrappers out; the part helpers (ladder, gear, ratchet...) stay in.
CORE_PRIMITIVES = {"n", "let", "group", "join", "mat", "xf", "box", "rod", "lathe", "annulus", "pipe", "tube",
                   "profile_sweep", "curve_fillet", "curve_trim", "curve_offset", "sample_curve", "instance",
                   "chamfer", "face_extrude", "face_divide", "delete_faces", "array", "ring", "guide", "points",
                   "field", "raycast", "voronoi", "stamp", "rivets", "seam", "outline", "courses", "fasteners",
                   "stroke", "ticks", "text", "close_bolts", "bake", "when", "layer", "provisional", "staged"}
CORE_FUNCTIONS = {"mesh", "mesh_rack", "segment_phase", "mesh_internal", "seat_pawls"}


def _load():
    if str(RECIPES) not in sys.path:
        sys.path.insert(0, str(RECIPES))
    kitlib = importlib.import_module("kitlib")
    return kitlib, {m: importlib.import_module(m) for m, _ in FAMILIES}


def _first_line(doc, cap=150):
    """First sentence of the docstring's first paragraph, at most `cap` characters."""
    para = " ".join((doc or "").strip().split("\n\n")[0].split())
    m = re.search(r"(?<!\be\.g)(?<!\bi\.e)\.(\s|$)", para)
    text = para[:m.start() + 1] if m else para
    return text if len(text) <= cap else text[:cap - 3].rstrip() + "..."


def _signature(fn, drop_self=False):
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return "(...)"
    params = list(sig.parameters.values())
    if drop_self and params and params[0].name == "self":
        params = params[1:]
    return "(" + ", ".join(str(p) for p in params) + ")"


def catalogue():
    """Every helper: dicts with name, kind ('method' or 'function'), module, file, family, signature, summary, doc."""
    kitlib, mods = _load()
    out, seen = [], set()

    def add(name, kind, fn, module, drop_self):
        key = (kind, name, module)
        if key in seen:
            return
        seen.add(key)
        doc = inspect.getdoc(fn) or ""
        out.append({"name": name, "kind": kind, "module": module, "file": module + ".py", "family": FAMILY[module],
                    "signature": _signature(fn, drop_self), "summary": _first_line(doc), "doc": doc})

    # Graph methods: kitlib's own part helpers, then attached ones (their __module__ names the file).
    for name, attr in kitlib.Graph.__dict__.items():
        if name.startswith("_"):
            continue
        static = isinstance(attr, staticmethod)
        fn = attr.__func__ if static else attr
        if not inspect.isfunction(fn):
            continue
        module = fn.__module__
        if module not in FAMILY or static:
            continue  # static attachments are module functions, listed below
        if module == "kitlib" and name in CORE_PRIMITIVES:
            continue
        add(name, "method", fn, module, True)
    # Module-level functions defined in each module (public, with the defining module's own name).
    for module, mod in mods.items():
        for name, fn in vars(mod).items():
            if name.startswith("_") or not inspect.isfunction(fn) or fn.__module__ != module or fn.__name__ != name:
                continue
            if module == "kitlib" and name not in CORE_FUNCTIONS:
                continue
            if "self" in inspect.signature(fn).parameters:
                continue  # an attached method, already listed
            add(name, "function", fn, module, False)
    order = {m: i for i, (m, _) in enumerate(FAMILIES)}
    out.sort(key=lambda h: (order[h["module"]], h["kind"] != "method"))
    return out


def attached_statics():
    kitlib, _ = _load()
    return {n for n, a in kitlib.Graph.__dict__.items() if isinstance(a, staticmethod)}


def render_block():
    helpers = catalogue()
    statics = attached_statics()
    lines = [BEGIN,
             "    # Every part helper, so `grep \"def \"` on this file finds them all. Query them with",
             "    #     python3 procgen/recipes/kitlib.py --helpers <word> [--long]",
             "    # Module-level helper functions (import from the named module, or from kitlib where it re-exports):"]
    for h in helpers:
        if h["kind"] != "function" or h["module"] == "kitlib":
            continue
        also = f" (also Graph.{h['name']})" if h["name"] in statics else ""
        lines.append(f"# def {h['name']}{h['signature']}  # {h['file']}{also} — {h['summary'] or '(no docstring)'}")
    lines.append("    # Attached Graph methods: forwarding stubs; the named module replaces each one when kitlib is imported.")
    for h in helpers:
        if h["kind"] != "method" or h["module"] == "kitlib":
            continue
        sig = h["signature"][1:-1]
        takes = " [takes controls=]" if re.search(r"\bcontrols=", h["signature"]) else ""
        lines.append(f"    def {h['name']}(self, *args, **kw):  # {h['file']} — {h['summary']}{takes}")
        lines.append(f'        """See {h["module"]}.{h["name"]}(self, {sig})."""' if sig else
                     f'        """See {h["module"]}.{h["name"]}(self)."""')
        lines.append(f"        import {h['module']}; return {h['module']}.{h['name']}(self, *args, **kw)")
    lines.append(END)
    return "\n".join(lines) + "\n"


def _split(text):
    a = text.index(BEGIN)
    b = text.index(END, a) + len(END) + 1
    return text[:a], text[a:b], text[b:]


def main(argv):
    text = KITLIB.read_text()
    head, block, tail = _split(text)
    fresh = render_block()
    if "--check" in argv:
        if block != fresh:
            print("kitlib.py helper index is stale: run python3 procgen/tools/gen_helper_index.py", file=sys.stderr)
            return 1
        print("kitlib.py helper index is current")
        return 0
    if block != fresh:
        KITLIB.write_text(head + fresh + tail)
        print(f"rewrote helper index in {KITLIB}")
    else:
        print("helper index already current")
    return 0


# ------------------------------------------------------------------------------------------------- query
LIMIT = 10


def _words(text):
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
            for w in re.findall(r"[a-z0-9]+", text.lower())]


def score(h, words):
    """Keyword score: name matches outweigh docstring matches; a name that starts with the word ranks first."""
    tokens = _words(h["name"].replace("_", " "))
    summary, doc = set(_words(h["summary"])), _words(h["doc"])
    total = 0.0
    for w in words:
        s = 0.0
        if w == h["name"].lower() or [w] == tokens:
            s += 12
        elif tokens and tokens[0] == w:
            s += 8
        elif w in tokens:
            s += 6
        elif w in h["name"].lower():
            s += 3
        if w in summary:
            s += 2
        s += min(doc.count(w), 4) * 0.25
        total += s
    return total


def search(query):
    words = _words(" ".join(query)) if query else []
    helpers = catalogue()
    if not words:
        return helpers
    ranked = [(score(h, words), i, h) for i, h in enumerate(helpers)]
    return [h for s, i, h in sorted(ranked, key=lambda t: (-t[0], t[1])) if s > 0]


def format_helpers(helpers, long=False, grouped=False):
    out, family = [], None
    for h in helpers:
        if grouped and h["family"] != family:
            family = h["family"]
            out.append(f"[{family}]")
        owner = "Graph." if h["kind"] == "method" else ""
        out.append(f"{owner}{h['name']}  {h['file']} — {h['summary']}  {h['signature']}")
        if long and h["doc"]:
            out.extend("    " + line for line in h["doc"].splitlines())
            out.append("")
    return "\n".join(out)


def cli(argv):
    """`kitlib.py --helpers [words...] [--long] [--all] [--json]`: a query lists the best 10 unless --all."""
    long, as_json = "--long" in argv, "--json" in argv
    words = [a for a in argv if not a.startswith("--")]
    found = search(words)
    more = 0
    if words and "--all" not in argv and len(found) > LIMIT:
        found, more = found[:LIMIT], len(found) - LIMIT
    if as_json:
        import json
        print(json.dumps([{k: v for k, v in h.items() if long or k != "doc"} for h in found]))
    elif not found:
        print(f"no helper matches {' '.join(words)!r}; run with no words for the full list")
        return 1
    else:
        print(format_helpers(found, long=long, grouped=not words))
        if more:
            print(f"... {more} weaker matches (--all to list them)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
