#!/usr/bin/env python3
"""Protocol test for tools/pgm_mcp.py: initialize, list tools, call each kind."""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = str(HERE.parent / "tests" / "data" / "kit-bracket.json")


def main():
    proc = subprocess.Popen([sys.executable, str(HERE / "pgm_mcp.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                            env={**os.environ, "PGM_STATE": str(HERE.parent / ".pgm" / "mcp-test")})
    n = [0]

    def rpc(method, params=None):
        n[0] += 1
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n[0], "method": method, "params": params or {}}) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    checks = []
    r = rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}})
    checks.append(("initialize", r["result"]["serverInfo"]["name"] == "procgen-pgm"))
    tools = [t["name"] for t in rpc("tools/list")["result"]["tools"]]
    checks.append(("tools/list", {"pgm_evaluate", "pgm_render", "pgm_export", "pgm_check", "pgm_inspect", "pgm_catalog", "pgm_sheet", "pgm_sweep", "pgm_audit", "pgm_nodes", "pgm_conformance", "kit_build"} <= set(tools)))

    def call(name, args):
        res = rpc("tools/call", {"name": name, "arguments": args})["result"]
        return json.loads(res["content"][0]["text"]), res

    ev, _ = call("pgm_evaluate", {"document": FIXTURE, "controls": {"length": 2.0}})
    checks.append(("evaluate", ev.get("passed") and ev["instances"] > 0))
    ins, _ = call("pgm_inspect", {"document": FIXTURE})
    checks.append(("inspect", ins.get("passed") and "length" in ins["controls"]))
    chk, _ = call("pgm_check", {"document": FIXTURE, "intersections": True})
    checks.append(("check", chk.get("passed") and chk["prototypes"] > 0))
    ex, _ = call("pgm_export", {"document": FIXTURE})
    checks.append(("export", ex.get("passed") and ex["emesh"]["meshes"] > 0 and os.path.exists(ex["emesh"]["path"])))
    rd, raw = call("pgm_render", {"document": FIXTURE, "view": "iso", "width": 320, "height": 240})
    has_png = len(raw["content"]) == 2 and raw["content"][1]["mimeType"] == "image/png" and raw["content"][1]["data"].startswith("iVBOR")
    checks.append(("render", rd.get("passed") and has_png))
    sw, _ = call("pgm_sweep", {"path": FIXTURE})
    checks.append(("sweep", sw.get("passed") and sw["documents"] == 1 and sw["cases"] > 1))
    au, _ = call("pgm_audit", {"path": FIXTURE})
    checks.append(("audit", "components" in au and au["components"] >= 1))
    nd, _ = call("pgm_nodes", {"type": "tube"})
    checks.append(("nodes", nd.get("passed") and nd["nodes"][0]["type"] == "tube"))
    bad, raw = call("pgm_evaluate", {"document": FIXTURE, "controls": {"length": 9999}})
    checks.append(("error is reported", bad.get("passed") is False and raw["isError"] and "length" in bad.get("error", "")))
    proc.stdin.close()
    proc.wait(timeout=10)
    ok = all(c[1] for c in checks)
    for name, passed in checks:
        print(f"{'ok  ' if passed else 'FAIL'} mcp {name}")
    print(f"{len(checks)} mcp checks, {sum(1 for c in checks if not c[1])} failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
