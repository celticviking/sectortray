import unittest

from sectortray.layout import Bed, LayoutError, _split_axis, compute, for_project, max_bin_units

KITCHEN = (365.125, 495.3, 69.85)      # 14 3/8" × 19 1/2" × 2 3/4"
WORKSHOP = (533.4, 412.75, 88.9)       # 21" × 16 1/4" × 3 1/2"


class SplitTests(unittest.TestCase):
    def test_balanced_extras_first(self):
        self.assertEqual(_split_axis(11, 16, 16, 220), [4, 4, 3])
        self.assertEqual(_split_axis(8, 14, 14, 250), [4, 4])

    def test_edges_hold_fewer_when_filler_is_wide(self):
        # 6 cells, bed 220: edges cap at 4 with 30 mm filler, interior 5 → 2 pieces of 3
        self.assertEqual(_split_axis(6, 30, 30, 220), [3, 3])
        # 9 cells, edge cap 4, interior cap 5 → 3 pieces, middle takes the extra
        parts = _split_axis(9, 30, 30, 220)
        self.assertEqual(sum(parts), 9)
        self.assertLessEqual(parts[0] * 42 + 30, 220)
        self.assertLessEqual(parts[-1] * 42 + 30, 220)

    def test_single_piece_carries_both_fillers(self):
        self.assertEqual(_split_axis(4, 10, 10, 250), [4])
        # 5 cells + 20 mm filler = 230 mm: too long for 220, so two pieces
        self.assertEqual(_split_axis(5, 10, 10, 220), [3, 2])


class LayoutTests(unittest.TestCase):
    def check_tiling(self, L):
        p = L["plate"]
        for axis, size, key in (("ci", p["width"], "w"), ("ri", p["depth"], "d")):
            first = [q for q in L["pieces"] if q["ci" if axis == "ri" else "ri"] == 0]
            self.assertAlmostEqual(sum(q[key] for q in first), size, places=2)
        for q in L["pieces"]:
            self.assertTrue(q["fits"], q)
            self.assertGreaterEqual(q["sx"], -1e-6)
            self.assertGreaterEqual(q["sy"], -1e-6)

    def test_kitchen_drawer(self):
        L = compute(*KITCHEN)
        self.assertEqual((L["nx"], L["ny"]), (8, 11))
        self.assertEqual((L["cols"], L["rows"]), ([4, 4], [4, 4, 3]))
        self.assertEqual(len(L["pieces"]), 6)
        self.assertAlmostEqual(L["plate"]["filler_x"], 14.0625, places=2)
        self.assertAlmostEqual(L["plate"]["filler_y"], 16.15, places=2)
        self.assertEqual(L["binU"], 8)
        self.check_tiling(L)

    def test_workshop_drawer_uses_rotation(self):
        L = compute(*WORKSHOP)
        self.assertEqual((L["nx"], L["ny"]), (12, 9))
        self.assertEqual(len(L["pieces"]), 6)  # 3 × 2, pieces rotated on the bed
        self.check_tiling(L)

    def test_piece_padding_only_on_outer_edges(self):
        L = compute(*KITCHEN)
        a1 = next(p for p in L["pieces"] if p["id"] == "A1")
        b2 = next(p for p in L["pieces"] if p["id"] == "B2")
        self.assertGreater(a1["pad"]["left"], 0)
        self.assertGreater(a1["pad"]["back"], 0)
        self.assertEqual((a1["pad"]["right"], a1["pad"]["front"]), (0, 0))
        self.assertEqual((b2["pad"]["back"], b2["pad"]["front"], b2["pad"]["left"]), (0, 0, 0))
        self.assertGreater(b2["pad"]["right"], 0)

    def test_bin_height_by_style(self):
        self.assertEqual(max_bin_units(69.85, "lite"), 9)
        self.assertEqual(max_bin_units(69.85, "standard"), 8)
        self.assertEqual(max_bin_units(69.85, "weighted"), 8)
        self.assertEqual(max_bin_units(10, "weighted"), 0)
        self.assertIsNone(max_bin_units(None, "lite"))

    def test_lite_has_no_magnets(self):
        self.assertEqual(compute(*KITCHEN, style="lite")["plate"]["magnet_count"], 0)
        self.assertEqual(compute(*KITCHEN, style="standard")["plate"]["magnet_count"], 352)

    def test_too_small(self):
        with self.assertRaises(LayoutError):
            compute(40, 300)

    def test_other_bed(self):
        L = compute(*KITCHEN, bed=Bed(180, 180, "Mini"))
        self.check_tiling(L)
        self.assertGreater(len(L["pieces"]), 6)

    def test_for_project(self):
        self.assertIsNone(for_project({"drawer": {"width_mm": None}}))
        self.assertIn("error", for_project({"drawer": {"width_mm": 30, "depth_mm": 30}}))
        doc = {"drawer": {"width_mm": KITCHEN[0], "depth_mm": KITCHEN[1], "height_mm": KITCHEN[2]},
               "baseplate": {"style": "weighted", "magnets": False}}
        self.assertEqual(for_project(doc)["plate"]["magnet_count"], 0)


if __name__ == "__main__":
    unittest.main()


class GridPositionTest(unittest.TestCase):
    def test_filler_goes_to_one_side(self):
        from sectortray.layout import compute
        even = compute(414.337, 573.087)
        tight = compute(414.337, 573.087, align=("left", "back"))
        self.assertEqual((tight["nx"], tight["ny"]), (even["nx"], even["ny"]))
        self.assertEqual((tight["fx"], tight["fy"]), (0.5, 0.5))  # only the clearance left at the back-left
        f = tight["plate"]["filler"]
        self.assertAlmostEqual(f["right"], 2 * even["plate"]["filler"]["left"], places=2)
        self.assertEqual((f["left"], f["back"]), (0.0, 0.0))
        for p in tight["pieces"]:  # every piece still fits the bed
            self.assertTrue(p["fits"])
