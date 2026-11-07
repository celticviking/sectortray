"""Settings → Saved drawers: moving the data folder."""
import json
import tempfile
import unittest
from pathlib import Path

from sectortray import datafolder
from sectortray.server import make_server
from sectortray.store import StoreError


class MoveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.src, self.loc = t / "Documents" / "SectorTray", t / "local" / datafolder.FILE
        self.server = make_server("127.0.0.1", 0, self.src)
        self.server.movable = True
        doc = self.server.store.create("Desk")
        (self.src / doc["id"] / "exports").mkdir()
        (self.src / doc["id"] / "exports" / "a.stl").write_bytes(b"x")
        self.server.settings.update({"printer": {"name": "Test", "bed_w": 200, "bed_d": 200, "max_h": 200}})

    def tearDown(self):
        self.server.server_close()
        self.tmp.cleanup()

    def test_move_to_a_new_folder(self):
        dst = Path(self.tmp.name) / "D" / "Prints"
        r = datafolder.move(self.server, str(dst), self.loc)
        self.assertEqual((r["mode"], Path(r["data_dir"])), ("move", dst.resolve()))
        self.assertTrue((dst / "desk" / "exports" / "a.stl").is_file())
        self.assertFalse(self.src.exists())  # emptied and removed
        self.assertEqual(self.server.store.list()[0]["name"], "Desk")  # the running server uses it at once
        self.assertEqual(self.server.settings.get()["printer"]["bed_w"], 200)
        self.assertEqual(datafolder.chosen(self.loc), dst.resolve())  # remembered for next start

    def test_switch_to_another_sectortray_folder_and_refusals(self):
        other = Path(self.tmp.name) / "Other"
        other.mkdir()
        (other / "settings.json").write_text(json.dumps({}), encoding="utf-8")
        self.assertEqual(datafolder.move(self.server, str(other), self.loc)["mode"], "use")
        self.assertTrue((self.src / "desk").is_dir())  # left where it was
        junk = Path(self.tmp.name) / "Junk"
        junk.mkdir()
        (junk / "taxes.pdf").write_bytes(b"x")
        for bad in (str(junk), "relative\folder", "", str(other), str(other / "inside")):
            with self.assertRaises(StoreError, msg=bad):
                datafolder.move(self.server, bad, self.loc)
        self.server.movable = False
        with self.assertRaises(StoreError):
            datafolder.move(self.server, str(Path(self.tmp.name) / "New"), self.loc)


if __name__ == "__main__":
    unittest.main()
