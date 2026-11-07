"""Part hashes must not depend on where SectorTray runs from (0.5.0: the installed copy had no git checkout,
got a different hash, and called every export "Changed since export")."""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from sectortray import export, paths

ROOT = Path(__file__).resolve().parents[1]
LIB = paths.VENDOR / "gridfinity-rebuilt-openscad"

PROBE = r"""
import json
from sectortray import bins, export, layout
from sectortray.store import BIN_DEFAULTS as D
L = layout.compute(414.3, 573.1, grid="fit")
b = {**D, "id": "b1", "w": 2, "d": 3, "h": 6, "_pitch": L["pitch"]}
big = {**D, "id": "b2", "w": 7, "d": 3, "h": 4, "split": {"on": True, "joint": "clips", "x": None, "y": None}}
tray = {**D, "id": "b3", "kind": "tray", "w": 4, "d": 5, "h": 3, "tray": {"utensils": [
    {"id": "u1", "name": "Fork", "kind": "fork", "preset": None, "len": 190, "handle": 22, "head": 26, "count": 4, "out": False},
    {"id": "u2", "name": "Knife", "kind": "knife", "preset": None, "len": 180, "handle": 20, "head": 0, "count": 4, "out": False}],
    "margin": "Normal", "round": True, "halves": False}}
print(json.dumps({
    "piece": export.piece_hash(L["pieces"][0], L["plate"]),
    "bin": bins.part_hash(b, "whole"),
    "split": bins.part_hash(big, "p1"),
    "tray": bins.part_hash(tray, "whole"),
}))
"""


class HashTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("git") and (LIB / ".git").exists(), "needs the git submodule")
    def test_pinned_commit_matches_submodule(self):
        head = subprocess.run(["git", "-C", str(LIB), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(export.LIBRARY_COMMIT, head, "submodule moved: update export.LIBRARY_COMMIT")

    def test_hashes_same_without_git_and_from_another_folder(self):
        def run(env, cwd):
            env = {**env, "PYTHONPATH": str(ROOT)}
            r = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, env=env, cwd=cwd)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)
        here = run(dict(os.environ), str(ROOT))
        no_git = {k: v for k, v in os.environ.items() if k.upper() != "PATH"}
        no_git["PATH"] = os.path.dirname(sys.executable)  # Python only: git can't be found
        elsewhere = run(no_git, str(Path(sys.executable).parent))
        self.assertEqual(here, elsewhere)


if __name__ == "__main__":
    unittest.main()
