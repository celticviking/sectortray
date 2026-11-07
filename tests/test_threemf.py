import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from sectortray import bins, paths, stl, threemf
from sectortray.store import _clean_bin

NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}


def write_box_stl(path: Path, w: float, d: float, h: float) -> None:
    """A closed box as a binary STL (12 triangles), corner at the origin."""
    v = [(x, y, z) for z in (0, h) for y in (0, d) for x in (0, w)]
    faces = [(0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4),
             (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5)]
    out = bytearray(80) + struct.pack("<I", len(faces))
    for f in faces:
        out += struct.pack("<12fH", 0, 0, 0, *v[f[0]], *v[f[1]], *v[f[2]], 0)
    path.write_bytes(out)


class TestSTL(unittest.TestCase):
    def test_copy_turned_swaps_width_and_depth(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.stl", Path(tmp) / "b.stl"
            write_box_stl(a, 200, 40, 10)
            stl.copy_turned(a, b)
            lo, hi = stl.bbox(b)
            self.assertEqual([round(hi[i] - lo[i], 3) for i in range(3)], [40, 200, 10])
            self.assertEqual(a.stat().st_size, b.stat().st_size)


class TestThreeMF(unittest.TestCase):
    def test_bed_order_matches_prusaslicer(self):
        self.assertEqual([threemf.grid_coords(i) for i in range(9)],
                         [(0, 0), (1, 0), (0, 1), (1, 1), (2, 0), (2, 1), (0, 2), (1, 2), (2, 2)])
        x, y = threemf.bed_offset(1, (250, 220))
        self.assertAlmostEqual(x, 250 + 0.3 * (250 ** 2 + 220 ** 2) ** 0.5, 3)
        self.assertEqual(y, 0)

    def test_arrange_fills_rows_then_beds(self):
        spots = threemf.arrange([(100, 100)] * 5, (250, 220))
        self.assertEqual([s[0] for s in spots], [0, 0, 0, 0, 1])
        for b, cx, cy in spots[:4]:  # first bed: 2 × 2 inside the bed
            self.assertTrue(50 <= cx <= 200 and 50 <= cy <= 170, (cx, cy))
        self.assertGreater(spots[4][1], 250)  # the fifth went to the next bed, to the right

    def test_write_names_and_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Path(tmp) / "box.stl"
            write_box_stl(s, 80, 40, 20)
            r = threemf.write(Path(tmp) / "out.3mf", [{"name": "U1 Utensil <bin>", "stl": s, "copies": 3}], (250, 220), "Kitchen")
            self.assertEqual(r, {"beds": 1, "copies": 3})
            with zipfile.ZipFile(Path(tmp) / "out.3mf") as z:
                self.assertIn("[Content_Types].xml", z.namelist())
                root = ET.fromstring(z.read("3D/3dmodel.model"))
            obj = root.find("m:resources/m:object", NS)
            self.assertEqual(obj.get("name"), "U1 Utensil <bin>")
            self.assertEqual(len(obj.findall("m:mesh/m:vertices/m:vertex", NS)), 8)
            self.assertEqual(len(obj.findall("m:mesh/m:triangles/m:triangle", NS)), 12)
            xs = [float(v.get("x")) for v in obj.findall("m:mesh/m:vertices/m:vertex", NS)]
            self.assertEqual((min(xs), max(xs)), (-40, 40))  # centered
            self.assertEqual(len(root.findall("m:build/m:item", NS)), 3)


@unittest.skipUnless(paths.OPENSCAD.exists(), "OpenSCAD not installed")
class TestTurnedExport(unittest.TestCase):
    def test_bin_that_only_fits_sideways_is_saved_turned(self):
        bed = (180, 250)  # a 5 × 1 bin (209.5 mm wide) fits only turned
        b = _clean_bin({"name": "Long", "w": 5, "d": 1, "h": 2}, "b1")
        self.assertTrue(bins.turned(bins.part_size(b, "whole", bed), bed))
        self.assertFalse(bins.turned(bins.part_size(b, "whole", (250, 220)), (250, 220)))
        with tempfile.TemporaryDirectory() as tmp:
            r = bins.export_bin(Path(tmp), [b], "b1", bed)
            lo, hi = stl.bbox(Path(r["folder"]) / r["file"])
            self.assertLess(hi[0] - lo[0], 45)
            self.assertGreater(hi[1] - lo[1], 205)
            self.assertNotEqual(bins.part_export_hash(b, "whole", bed), bins.part_hash(b, "whole", bed))


if __name__ == "__main__":
    unittest.main()
