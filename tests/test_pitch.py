import struct
import tempfile
import unittest
from pathlib import Path

from sectortray import bins, export, kennetek, layout, paths, split
from sectortray.store import ProjectStore, _clean_bin

DESK = (414.337, 573.087)


def size(path: Path):
    b = path.read_bytes()
    n = struct.unpack_from("<I", b, 80)[0]
    lo, hi = [1e9] * 3, [-1e9] * 3
    for i in range(n):
        v = struct.unpack_from("<12f", b, 84 + i * 50)[3:]
        for k in range(3):
            for c in (v[k], v[k + 3], v[k + 6]):
                lo[k], hi[k] = min(lo[k], c), max(hi[k], c)
    return [round(hi[k] - lo[k], 2) for k in range(3)]


class FitLayoutTest(unittest.TestCase):
    def test_fit_fills_the_drawer(self):
        std, fit = layout.compute(*DESK), layout.compute(*DESK, grid="fit")
        self.assertEqual((std["nx"], std["ny"], std["pitch"]), (9, 13, [42.0, 42.0]))
        self.assertEqual((fit["nx"], fit["ny"]), (10, 14))
        self.assertAlmostEqual(fit["pitch"][0], 413.337 / 10, places=3)
        self.assertEqual(fit["plate"]["filler"], {"left": 0.0, "right": 0.0, "back": 0.0, "front": 0.0})
        self.assertEqual((fit["fx"], fit["fy"]), (0.5, 0.5))  # only the clearance
        self.assertTrue(all(p["fits"] for p in fit["pieces"]))
        # the pieces tile the plate exactly
        self.assertAlmostEqual(sum(p["w"] for p in fit["pieces"] if p["ri"] == 0), fit["plate"]["width"], places=2)

    def test_tiny_drawer_keeps_42(self):
        self.assertEqual(layout.compute(70, 60, grid="fit")["pitch"], [42.0, 42.0])

    def test_standard_hash_unchanged(self):
        L = layout.compute(*DESK)
        p = L["pieces"][0]
        self.assertEqual(export.piece_hash(p, L["plate"]), export._hash(export.piece_params(p, L["plate"])))
        self.assertEqual(kennetek.script("bins", (42, 42), Path(".")), kennetek.SOURCE / "gridfinity-rebuilt-bins.scad")

    def test_split_and_tray_follow_the_pitch(self):
        b = _clean_bin({"name": "Long", "w": 2, "d": 7, "h": 3}, "b1")
        self.assertFalse(split.fits(2, 6, (250, 220)))                      # 6 × 42 − 0.5 = 251.5
        self.assertTrue(split.fits(2, 6, (250, 220), (41.3, 40.9)))         # 6 × 40.9 − 0.5 = 244.9
        cav = split.cavity({**b, "_pitch": [41.3, 40.9]})
        self.assertAlmostEqual(cav[0], 2 * 41.3 - 0.5 - 1.9, places=6)
        self.assertAlmostEqual(cav[1], 7 * 40.9 - 0.5 - 1.9, places=6)


@unittest.skipUnless(paths.OPENSCAD.exists(), "OpenSCAD not installed (python tools/setup.py)")
class FitRenderTest(unittest.TestCase):
    def test_bin_and_baseplate_render_at_the_drawer_size(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            b = {**_clean_bin({"name": "B", "w": 1, "d": 1, "h": 3, "magnets": True}, "b1"), "_pitch": [41.3, 40.9]}
            r = bins.render_preview(d, b)
            self.assertEqual(size(d / ".cache" / r["file"])[:2], [40.8, 40.4])
            # a drawer 4 × 2 fitted squares: 166.2 + 1 by 81.8 + 1 mm
            L = layout.compute(167.2, 82.8, grid="fit")
            self.assertEqual((L["nx"], L["ny"]), (4, 2))
            pv = export.preview_baseplate(d, L)
            got = size(d / ".cache" / pv["files"][0]["file"])
            self.assertAlmostEqual(got[0], L["plate"]["width"], places=1)
            self.assertAlmostEqual(got[1], L["plate"]["depth"], places=1)

    def test_tray_resizes_with_the_grid(self):
        with tempfile.TemporaryDirectory() as d:
            st = ProjectStore(Path(d))
            st.pitch_for = lambda doc: layout.for_project(doc)["pitch"] if doc["drawer"]["width_mm"] else None
            doc = st.create("Kitchen")
            doc = st.update(doc["id"], {"drawer": {"width_mm": 365.125, "depth_mm": 495.3}})
            tray_spec = {"utensils": [{"name": "Knife", "kind": "knife", "len": 230, "handle": 16, "head": 22, "count": 8},
                                      {"name": "Fork", "kind": "fork", "len": 202, "handle": 15, "head": 28, "count": 8}],
                         "margin": "Snug"}
            doc = st.update(doc["id"], {"bins": [{"name": "Tray", "kind": "tray", "tray": tray_spec}]})
            std = (doc["bins"][0]["w"], doc["bins"][0]["d"])
            doc = st.update(doc["id"], {"baseplate": {**doc["baseplate"], "grid": "fit"}})
            self.assertEqual(doc["baseplate"]["grid"], "fit")
            # 230 + 2·3 + 5.7 = 241.7 mm deep: 6 squares at 42 mm, still 6 at 41.19 mm
            self.assertEqual(doc["bins"][0]["d"], std[1])


if __name__ == "__main__":
    unittest.main()
