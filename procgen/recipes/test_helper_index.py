"""Tests of helper discovery. Built by Opus 5.5 (medium), 2026-11-17.

The generated index inside class Graph in kitlib.py matches the live helpers (run
procgen/tools/gen_helper_index.py after adding one); a helper attached without regenerating is caught;
`kitlib.py --helpers <words>` lists the right helper first; the stubs are replaced by the real helpers.

    python3 procgen/recipes/test_helper_index.py
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tools"))
import gen_helper_index as G  # noqa: E402
import kitlib  # noqa: E402


def helpers(*words):
    r = subprocess.run([sys.executable, str(HERE / "kitlib.py"), "--helpers", "--json", *words],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


class HelperIndex(unittest.TestCase):
    def test_block_is_current(self):
        _, block, _ = G._split(G.KITLIB.read_text())
        self.assertEqual(block, G.render_block(), "stale helper index: run python3 procgen/tools/gen_helper_index.py")

    def test_new_helper_makes_block_stale(self):
        def lattice_girder(self, id_, span):
            """A made-up helper."""
        lattice_girder.__module__ = "kitlib_iron"
        kitlib.Graph.lattice_girder = lattice_girder
        try:
            self.assertIn("def lattice_girder(self, *args, **kw):  # kitlib_iron.py", G.render_block())
            _, block, _ = G._split(G.KITLIB.read_text())
            self.assertNotEqual(block, G.render_block())
        finally:
            del kitlib.Graph.lattice_girder

    def test_every_attached_helper_has_a_def_line(self):
        text = G.KITLIB.read_text()
        for h in G.catalogue():
            if h["module"] == "kitlib":
                continue
            line = f"    def {h['name']}(self," if h["kind"] == "method" else f"# def {h['name']}("
            self.assertIn(line, text, h["name"])

    def test_stubs_are_replaced_at_import(self):
        import kitlib_iron
        import kitlib_masonry
        self.assertIs(kitlib.Graph.truss, kitlib_iron.truss)
        self.assertIs(kitlib.Graph.arch_ring, kitlib_masonry.arch_ring)

    def test_queries_rank_the_right_helper_first(self):
        for words, name, file in ((("truss",), "truss", "kitlib_iron.py"), (("arch",), "arch_ring", "kitlib_masonry.py"),
                                  (("pipe", "valve"), "valve", "kitlib_pipes.py"),
                                  (("pilaster",), "pilaster", "kitlib_orders.py"),
                                  (("vault",), "glazed_vault", "kitlib_glazing.py")):
            found = helpers(*words)
            self.assertTrue(found, words)
            self.assertEqual((found[0]["name"], found[0]["file"]), (name, file), words)
        self.assertIn("pipe_run", [h["name"] for h in helpers("pipe", "valve")[:3]])

    def test_no_words_lists_all_by_family(self):
        r = subprocess.run([sys.executable, str(HERE / "kitlib.py"), "--helpers"], capture_output=True, text=True, check=True)
        out = r.stdout.splitlines()
        for family in ("[core]", "[iron]", "[masonry]", "[orders]", "[pipes]", "[glazing]", "[tracery]"):
            self.assertIn(family, out)
        self.assertTrue(any(line.startswith("Graph.truss  kitlib_iron.py — ") for line in out))


if __name__ == "__main__":
    unittest.main()
