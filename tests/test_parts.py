import unittest

from sectortray.parts import fits_bed


class FitsBedTest(unittest.TestCase):
    def test_core_one_bed(self):
        bed = (250.0, 220.0)
        sq = lambda n: n * 42 - 0.5
        self.assertTrue(fits_bed(sq(5), sq(5), bed))   # 209.5 × 209.5, the largest bin
        self.assertFalse(fits_bed(sq(6), sq(1), bed))  # 251.5 mm is past 250 either way round
        self.assertFalse(fits_bed(sq(2), sq(7), bed))

    def test_turned_on_the_bed(self):
        self.assertTrue(fits_bed(230, 100, (180.0, 256.0)))


if __name__ == "__main__":
    unittest.main()
