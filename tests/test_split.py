import struct
import tempfile
import unittest
from pathlib import Path

from sectortray import bins, paths, split, stl
from sectortray.parts import list_parts
from sectortray.store import ProjectStore, StoreError, _clean_bin, _clean_split

CORE_ONE = (250.0, 220.0)


def stl_bounds(path: Path):
    b = path.read_bytes()
    n = struct.unpack_from("<I", b, 80)[0]
    lo, hi = [1e9] * 3, [-1e9] * 3
    for i in range(n):
        v = struct.unpack_from("<12f", b, 84 + i * 50)[3:]
        for k in range(3):
            for c in (v[k], v[k + 3], v[k + 6]):
                lo[k], hi[k] = min(lo[k], c), max(hi[k], c)
    return lo, hi


class RunsTest(unittest.TestCase):
    def test_fewest_balanced_pieces(self):
        self.assertEqual(split.auto_runs(2, 7, CORE_ONE), ([2], [4, 3]))
        self.assertEqual(split.auto_runs(11, 2, CORE_ONE), ([4, 4, 3], [2]))
        self.assertEqual(split.auto_runs(6, 6, CORE_ONE), ([3, 3], [3, 3]))
        self.assertEqual(split.auto_runs(5, 5, CORE_ONE), ([5], [5]))  # fits: one piece

    def test_custom_runs_must_add_up(self):
        self.assertEqual(split.clean_runs([5, 3, 3], 11), [5, 3, 3])
        self.assertIsNone(split.clean_runs(None, 11))
        with self.assertRaises(ValueError):
            split.clean_runs([5, 5], 11)


class PlanTest(unittest.TestCase):
    def bin(self, **kw):
        return _clean_bin({"name": "B", "w": 2, "d": 7, "h": 3, **kw}, "b1")

    def test_clips_one_per_wall_alternating(self):
        pl = split.plan(self.bin(split={"on": True, "joint": "clips"}), CORE_ONE)
        self.assertEqual([p["squares"] for p in pl["pieces"]], [[2, 3], [2, 4]])  # back piece first
        clips = [c for p in pl["pieces"] for c in p["clips"]]
        self.assertEqual(len(clips), 2)
        self.assertEqual(sorted(c[3] for c in clips), [-1, 1])  # one on each side wall
        self.assertEqual(pl["keys"], 0)

    def test_keys_avoid_dividers_on_the_seam(self):
        pl = split.plan(self.bin(split={"on": True, "joint": "keys"}), CORE_ONE)
        self.assertEqual(pl["keys"], 2)  # one per square along the seam
        on_cut = split.plan(self.bin(divy=7, split={"on": True, "joint": "keys"}), CORE_ONE)  # dividers on every grid line
        self.assertEqual(on_cut["keys"], 0)
        self.assertTrue(on_cut["notes"])

    def test_shallow_bin_has_no_clips(self):
        pl = split.plan(self.bin(h=1, split={"on": True, "joint": "clips"}), CORE_ONE)
        self.assertFalse(any(p["clips"] for p in pl["pieces"]))
        self.assertIn("This bin is too shallow for clips.", pl["notes"])


class StoreTest(unittest.TestCase):
    def test_split_is_cleaned(self):
        b = _clean_bin({"name": "B", "w": 2, "d": 7, "h": 3, "split": {"on": 1, "joint": "keys", "y": [5, 5]}}, "b1")
        self.assertEqual(b["split"], {"on": True, "joint": "keys", "x": None, "y": None})  # runs not adding up → automatic
        with self.assertRaises(StoreError):
            _clean_bin({"name": "B", "w": 2, "d": 7, "h": 3, "split": {"joint": "glue"}}, "b1")
        self.assertIsNone(_clean_split({"on": True}, {"kind": "tray", "w": 5, "d": 6}))  # trays split themselves


@unittest.skipUnless(paths.OPENSCAD.exists(), "OpenSCAD not installed (python tools/setup.py)")
class RenderTest(unittest.TestCase):
    def test_pieces_export_and_print_list(self):
        with tempfile.TemporaryDirectory() as d:
            st = ProjectStore(Path(d))
            doc = st.create("Split")
            doc = st.update(doc["id"], {"bins": [{"name": "Keyed", "w": 2, "d": 7, "h": 3, "magnets": False,
                                                   "split": {"on": True, "joint": "keys"}}]})
            folder = st._dir(doc["id"])
            r = bins.export_bin(folder, doc["bins"], "b1", CORE_ONE, 0.15)
            self.assertEqual(r["files"], ["keyed_2x7_3u_piece1.stl", "keyed_2x7_3u_piece2.stl", "keyed_2x7_3u_keys.stl"])
            lo, hi = stl_bounds(folder / "exports" / "keyed_2x7_3u_piece1.stl")  # back piece: 2 × 3 squares
            self.assertAlmostEqual(hi[0] - lo[0], 83.5, places=1)
            self.assertAlmostEqual(hi[1] - lo[1], 3 * 42 - 0.25 - 0.1, places=1)
            lo, hi = stl_bounds(folder / "exports" / "keyed_2x7_3u_keys.stl")
            self.assertAlmostEqual(hi[2] - lo[2], 1.4, places=2)
            # keys stop 0.3 mm short of each pocket end (pocket 18 + 2 × 0.15 long), so only the flanks grip (keys lie along Y)
            self.assertAlmostEqual(hi[1] - lo[1], 18 + 2 * 0.15 - 2 * 0.3, places=2)
            rows = list_parts(folder, doc, None, CORE_ONE, 0.15)["parts"]
            self.assertEqual([p["code"] for p in rows], ["K1·1", "K1·2", "K1·K"])
            self.assertTrue(all(p["status"] == "done" and p["fits"] for p in rows))
            # a different joint clearance makes the exports stale
            self.assertTrue(all(p["status"] == "stale" for p in list_parts(folder, doc, None, CORE_ONE, 0.2)["parts"]))

    def test_clip_on_the_scoop_wall_sits_above_the_scoop(self):
        """2026-11-06: the scoop trimmed the front-wall clip to a flat-bottomed sliver that hung in the air.
        Now it starts above the scoop and keeps its 45° underside; the back-wall clip is unchanged."""
        b = _clean_bin({"name": "Misc", "w": 7, "d": 2, "h": 5, "scoop": True, "lip": False,
                        "split": {"on": True, "joint": "clips"}}, "b1")
        plain = dict(b, scoop=False)
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            whole = bins.render_preview(d, b, CORE_ONE)["file"]
            for part in ("p1", "p2"):
                planned = split.plan(b, CORE_ONE)["pieces"][int(part[1]) - 1]["clips"]
                used = bins._split_params(d, b, part, CORE_ONE, 0.15, whole)["clips"]
                for p_cl, u_cl in zip(planned, used):
                    if p_cl[3] == 1:  # front wall, where the scoop is: lifted, full height
                        self.assertGreater(u_cl[5], p_cl[5] + 5)
                        self.assertAlmostEqual(u_cl[6] - u_cl[5], p_cl[6] - p_cl[5], places=2)
                    else:
                        self.assertEqual(u_cl, p_cl)
                # nothing past the seam has a flat underside
                f = d / ".cache" / bins.render_part(d, b, part, CORE_ONE, 0.15)["file"]
                seam = planned[0][1]
                for t in stl.triangles(f):
                    a, b2, c = t[0:3], t[3:6], t[6:9]
                    cx = (a[0] + b2[0] + c[0]) / 3
                    if (cx > seam + 0.05) if part == "p1" else (cx < seam - 0.05):
                        ux, uy, uz = (b2[i] - a[i] for i in range(3))
                        vx, vy, vz = (c[i] - a[i] for i in range(3))
                        nz, ln = ux * vy - uy * vx, ((uy * vz - uz * vy) ** 2 + (uz * vx - ux * vz) ** 2 + (ux * vy - uy * vx) ** 2) ** .5
                        if ln / 2 < 0.05:  # slivers far thinner than a printed line (0.01 mm steps from the trim)
                            continue
                        self.assertGreater(nz / ln, -0.9, f"{part}: flat underside past the seam")
        # bins without a scoop keep their old hashes (their exports stay valid)
        self.assertNotEqual(bins.part_hash(b, "p1", CORE_ONE), bins.part_hash(dict(b, scoop=False), "p1", CORE_ONE))
        self.assertEqual(bins.part_hash(plain, "p2", CORE_ONE), bins._hash({"bin": bins._keyed(bins.bin_params(plain), plain),
                         "split": bins._split_params(Path("."), plain, "p2", CORE_ONE, 0.15), "v": bins.SPLIT_SCAD_VERSION}))


if __name__ == "__main__":
    unittest.main()
