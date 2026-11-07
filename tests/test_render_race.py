import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sectortray import bins, paths
from sectortray.store import _clean_bin


@unittest.skipUnless(paths.OPENSCAD.exists(), "OpenSCAD not installed (python tools/setup.py)")
class SameRenderAtOnceTest(unittest.TestCase):
    def test_identical_bins_render_in_parallel(self):
        # Two bins with the same settings share one preview file; the Layout 3D view asks for both at once.
        b = _clean_bin({"name": "Pens", "w": 1, "d": 1, "h": 2, "magnets": False}, "b1")
        with tempfile.TemporaryDirectory() as d:
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: bins.render_preview(Path(d), b), range(4)))
            self.assertEqual(len({r["file"] for r in results}), 1)
            self.assertEqual(sum(not r["cached"] for r in results), 1)  # rendered once, reused three times
            self.assertTrue(all(r.get("infill_mm") for r in results))
            self.assertEqual(list(Path(d, ".cache").glob("*.part.stl")), [])


if __name__ == "__main__":
    unittest.main()
