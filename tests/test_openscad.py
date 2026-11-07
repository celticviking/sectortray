import os
import tempfile
import unittest
from pathlib import Path

from sectortray import openscad


class PruneCacheTest(unittest.TestCase):
    def test_keeps_newest_and_skips_in_progress_renders(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            for i in range(5):
                f = cache / f"bin_{i}.stl"
                f.write_bytes(b"x")
                f.with_suffix(".json").write_text("{}")
                os.utime(f, (1000 + i, 1000 + i))
            part = cache / "bin_9.part.stl"  # another thread's render, about to be renamed
            part.write_bytes(b"x")
            os.utime(part, (1, 1))
            (cache / "tray_0.stl").write_bytes(b"x")
            openscad.prune_cache(cache, "bin", 2)
            self.assertEqual(sorted(f.name for f in cache.iterdir()),
                             ["bin_3.json", "bin_3.stl", "bin_4.json", "bin_4.stl", "bin_9.part.stl", "tray_0.stl"])


if __name__ == "__main__":
    unittest.main()
