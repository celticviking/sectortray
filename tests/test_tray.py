import json
import shutil
import subprocess
import unittest
from pathlib import Path

from sectortray import tray

ROOT = Path(__file__).resolve().parent.parent
FORK = {"len": 202, "handle": 15, "head": 28}
KNIFE = {"len": 230.2, "handle": 16, "head": 22}
SOUP = {"len": 191, "handle": 15, "head": 40}
DESSERT = {"len": 180, "handle": 14, "head": 36}
TEA = {"len": 155, "handle": 12, "head": 33}
CASES = [
    ([FORK, KNIFE, SOUP, DESSERT, TEA], 5),
    ([FORK, KNIFE, SOUP, DESSERT, TEA], 3),
    ([SOUP, DESSERT, TEA], 3),
    ([SOUP, DESSERT, TEA], 7),
    ([{"len": 150, "handle": 15, "head": 28}, {"len": 155, "handle": 12, "head": 33}], 5),
    ([{"len": 150, "handle": 12, "head": 20}] * 14, 5),
    ([{"len": 120, "handle": 10, "head": 0}, {"len": 140, "handle": 30, "head": 0}], 4),
]
BED = (250, 220)


class TrayMathTests(unittest.TestCase):
    def test_matches_real_renders(self):
        # footprints measured from OpenSCAD renders of the silverware script (2026-10-20)
        self.assertEqual(tray.footprint(CASES[0][0], 5)["gx"], 5)
        self.assertEqual(tray.footprint(CASES[0][0], 5)["gy"], 6)
        self.assertEqual((tray.footprint(CASES[4][0], 5)["gx"], tray.footprint(CASES[4][0], 5)["gy"]), (3, 5))
        self.assertEqual((tray.footprint(CASES[5][0], 5)["gx"], tray.footprint(CASES[5][0], 5)["gy"]), (11, 4))

    def test_halves(self):
        self.assertIsNone(tray.halves(5, 5, BED))            # fits in one piece
        self.assertEqual(tray.halves(5, 6, BED), {"front": 3, "back": 3})
        self.assertEqual(tray.halves(4, 7, BED), {"front": 4, "back": 3})
        self.assertIsNone(tray.halves(5, 11, BED))           # even halves would be 6 squares

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_twin_agrees(self):
        script = ("const T = require(%s); const cases = %s; const bed = %s;"
                  "console.log(JSON.stringify(cases.map(([us, m]) => { const f = T.footprint(us, m);"
                  " return [f.gx, f.gy, +f.wmm.toFixed(6), T.halves(f.gx, f.gy, bed)]; })));"
                  % (json.dumps(str(ROOT / "web" / "js" / "traymath.js")), json.dumps(CASES), json.dumps(list(BED))))
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        js = json.loads(out.stdout)
        for (us, m), got in zip(CASES, js):
            f = tray.footprint(us, m)
            with self.subTest(m=m, n=len(us)):
                self.assertEqual(got[:2], [f["gx"], f["gy"]])
                self.assertAlmostEqual(got[2], f["wmm"], places=5)
                self.assertEqual(got[3], tray.halves(f["gx"], f["gy"], BED))


if __name__ == "__main__":
    unittest.main()
