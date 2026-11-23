#!/usr/bin/env python3
"""MCP server for the procgen modeler (stdio, newline-delimited JSON-RPC 2.0).

Every tool is a thin wrapper over one `pgm` subcommand, so the CLI and MCP
stay in step: same arguments, same JSON receipts. Renders also return the PNG
inline.

  PGM_BIN       path to the pgm binary   (default: ../build-release/pgm)
  PGM_RECIPES   recipe directory for bare document names (default: the main
                checkout's Recipes/Geometry when it exists)
  PGM_STATE     where renders and exports go (default: ../.pgm)

Register with Claude Code:
  claude mcp add procgen -- python3 /abs/path/procgen/tools/pgm_mcp.py
"""
import base64
import json
import os
import subprocess
import sys
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PGM = os.environ.get("PGM_BIN", str(HERE.parent / "build-release" / "pgm"))
STATE = Path(os.environ.get("PGM_STATE", str(HERE.parent / ".pgm")))
_default_recipes = HERE.parents[3] / "Recipes" / "Geometry" if len(HERE.parents) > 3 else None
RECIPES = os.environ.get("PGM_RECIPES") or (str(_default_recipes) if _default_recipes and _default_recipes.is_dir() else "")

DOC = {"type": "string", "description": "Path to a geometry document (.json) or .emesh pack, or a bare preset name looked up in the recipe directory."}
CONTROLS = {"type": "object", "description": "Control overrides, name -> number.", "additionalProperties": {"type": "number"}}

TOOLS = [
    {"name": "pgm_catalog", "description": "List the geometry documents and modules of a recipe directory.",
     "inputSchema": {"type": "object", "properties": {"directory": {"type": "string", "description": "Defaults to the configured recipe directory."}}}},
    {"name": "pgm_materials", "description": "Material of every slot name (class, colour, flags); optionally write the flat engine material pack (.emat) that binds them.",
     "inputSchema": {"type": "object", "properties": {"emat": {"type": "string", "description": "Output path for the material pack."}}}},
    {"name": "pgm_inspect", "description": "Controls (with ranges), module imports, typed ports, node types and material slots of a document.",
     "inputSchema": {"type": "object", "properties": {"document": DOC, "controls": CONTROLS}, "required": ["document"]}},
    {"name": "pgm_evaluate", "description": "Evaluate a document: instances, prototypes, triangles and Z-up bounds.",
     "inputSchema": {"type": "object", "properties": {"document": DOC, "controls": CONTROLS}, "required": ["document"]}},
    {"name": "pgm_check", "description": "Topology of every prototype: closed/open, non-manifold, orientation, inside-out; optional self-intersection scan.",
     "inputSchema": {"type": "object", "properties": {"document": DOC, "controls": CONTROLS, "intersections": {"type": "boolean"}}, "required": ["document"]}},
    {"name": "pgm_export", "description": "Write an engine mesh pack (.emesh, Y-up, congruent prototypes instanced) and/or a Z-up OBJ.",
     "inputSchema": {"type": "object", "properties": {"document": DOC, "controls": CONTROLS,
                     "emesh": {"type": "string", "description": "Output path; default <state>/exports/<name>.emesh"},
                     "obj": {"type": "string"}, "dedupe": {"type": "boolean", "description": "Congruent-prototype instancing (default true)."}},
                     "required": ["document"]}},
    {"name": "pgm_render", "description": "Preview a kit, rendered from its .emesh bytes. Returns a receipt and the PNG.",
     "inputSchema": {"type": "object", "properties": {"document": DOC, "controls": CONTROLS,
                     "view": {"type": "string", "enum": ["iso", "opposite", "front", "side", "top"]},
                     "pass": {"type": "string", "enum": ["beauty", "clay", "parts", "materials", "zebra", "matcap", "curvature"]},
                     "width": {"type": "integer"}, "height": {"type": "integer"}, "yaw": {"type": "number"}, "pitch": {"type": "number"},
                     "projection": {"type": "string", "enum": ["ortho", "perspective"]},
                     "backend": {"type": "string", "enum": ["software", "engine"]}, "out": {"type": "string"}},
                     "required": ["document"]}},
    {"name": "pgm_sheet", "description": "Render a multi-panel kit sheet with captions from a sheet spec. Returns a receipt and the PNG.",
     "inputSchema": {"type": "object", "properties": {"sheet": {"type": "string", "description": "Path to a sheet JSON."},
                     "root": {"type": "string", "description": "Directory panel documents are relative to; defaults to the recipe directory."},
                     "out": {"type": "string"}}, "required": ["sheet"]}},
    {"name": "pgm_sweep", "description": "Evaluate and topology-check every document under a file or directory at its defaults, at both ends of every control range and at every variant. Returns counts and only the failing settings.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string", "description": "A document or a directory (searched recursively)."},
                                                     "intersections": {"type": "boolean"}}, "required": ["path"]}},
    {"name": "pgm_audit", "description": "Contact audit: split the parts into solid islands and check they hold together within 3 mm. For one document: the detached pieces with their sources and bounds. For a directory (or with sweep): only the failing documents and settings.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "controls": CONTROLS, "sweep": {"type": "boolean"},
                                                     "baseline": {"type": "string"}}, "required": ["path"]}},
    {"name": "pgm_nodes", "description": "Node reference: every node type a geometry document may use, with its inputs and options. Give a type for one entry.",
     "inputSchema": {"type": "object", "properties": {"type": {"type": "string"}}}},
    {"name": "pgm_conformance", "description": "Compare every document of a directory against reference receipts.",
     "inputSchema": {"type": "object", "properties": {"directory": {"type": "string"}, "receipts": {"type": "string"}}, "required": ["directory", "receipts"]}},
    {"name": "kit_build", "description": "Run a kit builder (recipes/build_*.py) to regenerate its kits from the Python authoring source. Returns the module count per kit.",
     "inputSchema": {"type": "object", "properties": {"builder": {"type": "string", "description": "site, greenhouse or island, or a path to a builder script."}},
                     "required": ["builder"]}},
]


def resolve(document):
    p = Path(document)
    if p.exists():
        return str(p)
    if RECIPES:
        for cand in (Path(RECIPES) / document, Path(RECIPES) / (document + ".json")):
            if cand.exists():
                return str(cand)
    raise ValueError(f"document not found: {document}" + (f" (recipe directory {RECIPES})" if RECIPES else ""))


def set_args(controls):
    out = []
    for k, v in (controls or {}).items():
        out += ["--set", f"{k}={float(v)}"]
    return out


def run(args):
    r = subprocess.run([PGM] + args, capture_output=True, text=True, timeout=600)
    line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"passed": False, "error": (r.stderr or r.stdout or "pgm produced no output")[-800:]}


def out_path(kind, name, ext):
    d = STATE / kind
    d.mkdir(parents=True, exist_ok=True)
    return str(d / f"{Path(name).stem}-{int(time.time() * 1000) % 100000000}.{ext}")


def call(name, a):
    image = None
    if name == "pgm_catalog":
        directory = a.get("directory") or RECIPES
        if not directory:
            raise ValueError("no directory given and PGM_RECIPES is not set")
        result = run(["catalog", directory])
    elif name == "pgm_materials":
        result = run(["materials"] + (["--emat", a["emat"]] if a.get("emat") else []))
    elif name in ("pgm_inspect", "pgm_evaluate", "pgm_check"):
        args = [name[4:], resolve(a["document"])] + set_args(a.get("controls"))
        if name == "pgm_check" and a.get("intersections"):
            args.append("--intersections")
        result = run(args)
    elif name == "pgm_export":
        doc = resolve(a["document"])
        args = ["export", doc] + set_args(a.get("controls"))
        if a.get("obj"):
            args += ["--obj", a["obj"]]
        if a.get("emesh") or not a.get("obj"):
            args += ["--emesh", a.get("emesh") or out_path("exports", doc, "emesh")]
        if a.get("dedupe") is False:
            args.append("--no-dedupe")
        result = run(args)
    elif name == "pgm_render":
        doc = resolve(a["document"])
        out = a.get("out") or out_path("renders", doc, "png")
        args = ["render", doc, "--out", out] + set_args(a.get("controls"))
        for key in ("view", "pass", "projection", "backend"):
            if a.get(key):
                args += [f"--{key}", str(a[key])]
        if a.get("width") or a.get("height"):
            args += ["--size", f"{int(a.get('width', 960))}x{int(a.get('height', 720))}"]
        for key in ("yaw", "pitch"):
            if key in a:
                args += [f"--{key}", str(float(a[key]))]
        result = run(args)
        image = out if result.get("passed") else None
    elif name == "pgm_sheet":
        out = a.get("out") or out_path("renders", a["sheet"], "png")
        args = ["sheet", a["sheet"], "--out", out]
        root = a.get("root") or RECIPES
        if root:
            args += ["--root", root]
        result = run(args)
        image = out if result.get("passed") else None
    elif name == "pgm_sweep":
        result = run(["sweep", a["path"]] + (["--intersections"] if a.get("intersections") else []))
    elif name == "pgm_audit":
        target = a["path"] if Path(a["path"]).is_dir() else resolve(a["path"])
        args = ["audit", target] + set_args(a.get("controls"))
        if a.get("sweep"):
            args.append("--sweep")
        if a.get("baseline"):
            args += ["--baseline", a["baseline"]]
        result = run(args)
    elif name == "pgm_nodes":
        result = run(["nodes"] + ([a["type"]] if a.get("type") else []))
    elif name == "pgm_conformance":
        result = run(["conformance", a["directory"], a["receipts"]])
    elif name == "kit_build":
        script = Path(a["builder"])
        if not script.suffix:
            script = HERE.parent / "recipes" / f"build_{a['builder']}_kit{'s' if a['builder'] != 'island' else ''}.py"
        if not script.exists():
            raise ValueError(f"no builder {script}")
        r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=600)
        kits = {m[0]: int(m[1]) for m in re.findall(r"^([\w-]+): (\d+) modules?$", r.stdout, re.M)}
        result = {"passed": r.returncode == 0, "builder": str(script), "kits": kits}
        if r.returncode:
            result["error"] = (r.stderr or r.stdout)[-800:]
    else:
        raise ValueError(f"unknown tool {name}")
    content = [{"type": "text", "text": json.dumps(result)}]
    if image and os.path.getsize(image) <= 4_000_000:
        content.append({"type": "image", "data": base64.b64encode(open(image, "rb").read()).decode(), "mimeType": "image/png"})
    return {"content": content, "isError": not result.get("passed", False)}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:  # notification
            continue
        try:
            if method == "initialize":
                result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2024-11-05"),
                          "capabilities": {"tools": {}}, "serverInfo": {"name": "procgen-pgm", "version": "1.0"}}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                params = msg.get("params", {})
                try:
                    result = call(params.get("name"), params.get("arguments") or {})
                except (ValueError, KeyError, subprocess.TimeoutExpired) as e:
                    result = {"content": [{"type": "text", "text": json.dumps({"passed": False, "error": str(e)})}], "isError": True}
            elif method == "ping":
                result = {}
            else:
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}) + "\n")
                sys.stdout.flush()
                continue
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\n")
        except Exception as e:  # noqa: BLE001 - a server must answer, not die
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
