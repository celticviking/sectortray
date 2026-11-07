"""Rendered bin sizes (0.5.2 audit: half-grid bins came out a quarter of their size)."""
import tempfile
import unittest
from pathlib import Path

from sectortray import bins, stl
from sectortray.store import _clean_bin


def footprint(path: Path):
    lo, hi = stl.bbox(path)
    return round(hi[0] - lo[0], 1), round(hi[1] - lo[1], 1)


class HalfGridTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def render(self, **kw):
        b = _clean_bin({"name": "T", **kw}, "b1")
        r = bins.render_preview(self.dir, b)
        return footprint(self.dir / ".cache" / r["file"])

    def test_half_grid_keeps_the_whole_square_size(self):
        self.assertEqual(self.render(w=2, d=3, h=3, half_grid=True), (83.5, 125.5))
        self.assertEqual(self.render(w=2, d=3, h=3, half_grid=False), (83.5, 125.5))

    def test_half_grid_on_fitted_squares(self):
        b = _clean_bin({"name": "T", "w": 2, "d": 1, "h": 3, "half_grid": True}, "b1")
        b["_pitch"] = [41.33, 40.86]
        r = bins.render_preview(self.dir, b)
        self.assertEqual(footprint(self.dir / ".cache" / r["file"]), (82.2, 40.4))


class CodeTest(unittest.TestCase):
    def test_bin_codes_never_look_like_baseplate_pieces(self):
        from sectortray.parts import _bin_codes
        bs = [{"id": "b1", "name": "Bin 1"}, {"id": "b2", "name": "Bin 1×1"}, {"id": "b3", "name": "Front Pens"},
              {"id": "b4", "name": "2nd Front Pens"}, {"id": "b5", "name": "Utensils"}, {"id": "b6", "name": "Tray", "kind": "tray"},
              {"id": "b7", "name": "Ab"}, {"id": "b8", "name": "123"}]
        self.assertEqual(_bin_codes(bs, {"A", "B"}),
                         {"b1": "BI1", "b2": "BI2", "b3": "F1", "b4": "F2", "b5": "U1", "b6": "T1", "b7": "AB1", "b8": "BN1"})
        self.assertEqual(_bin_codes(bs)["b1"], "B1")  # no baseplate: plain letters
        self.assertEqual(_bin_codes([{"id": "t", "name": "x", "kind": "tray"}], set("ABCDEFGHIJKLMNOPQRST"))["t"], "TR1")



class HardwareTest(unittest.TestCase):
    def test_magnet_and_screw_counts(self):
        from sectortray.parts import hardware
        lay = {"plate": {"magnets": True}, "pieces": [{"cx": 4, "cy": 5}, {"cx": 3, "cy": 5}]}
        doc = {"bins": [{"w": 2, "d": 3, "qty": 2, "magnets": True},                      # 24 each
                        {"w": 2, "d": 2, "qty": 1, "magnets": True, "half_grid": True},   # corners only: 4
                        {"w": 1, "d": 1, "qty": 3, "magnets": False, "screws": True},     # 4 screws each
                        {"w": 4, "d": 5, "qty": 1, "magnets": True, "kind": "tray"}]}      # 80
        self.assertEqual(hardware(doc, lay), {"magnets": {"plate": 140, "bins": 132}, "screws": 12})
        lay["plate"]["magnets"] = False  # Lite plates have none
        self.assertEqual(hardware(doc, lay)["magnets"]["plate"], 0)
        self.assertEqual(hardware({"bins": []}, None), {"magnets": {"plate": 0, "bins": 0}, "screws": 0})

if __name__ == "__main__":
    unittest.main()
