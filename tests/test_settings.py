import tempfile
import unittest
from pathlib import Path

from sectortray.settings import Settings, defaults
from sectortray.store import StoreError


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Settings(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_without_file(self):
        self.assertEqual(self.s.get(), defaults())
        self.assertEqual((self.s.bed().width, self.s.bed().depth), (250.0, 220.0))
        self.assertEqual(self.s.fit(), (1.0, 1.0))

    def test_update_and_persist(self):
        self.s.update({"printer": {"bed_w": 180, "bed_d": 180, "name": "Mini"}, "fit": {"clearance_mm": 0.5}})
        again = Settings(Path(self.tmp.name))
        self.assertEqual(again.bed().name, "Mini")
        self.assertEqual(again.fit(), (0.5, 1.0))

    def test_new_bins_and_first_run(self):
        self.assertFalse(self.s.printer_confirmed())  # fresh install asks which printer
        self.assertEqual(self.s.get()["new_bins"], {"label_tab": False, "lip": False, "scoop": True, "auto_split": True})
        self.s.update({"new_bins": {"label_tab": 1, "auto_split": 0}, "fit": {"joint_mm": 0.2}})
        self.assertTrue(self.s.printer_confirmed())
        self.assertEqual(self.s.get()["new_bins"], {"label_tab": True, "lip": False, "scoop": True, "auto_split": False})
        self.assertEqual(self.s.joint_gap(), 0.2)

    def test_filaments_validated_and_ids_assigned(self):
        out = self.s.update({"filaments": [{"id": "f1", "name": "Blue", "material": "PLA", "color": "#2F4F8F"},
                                           {"name": "Orange", "material": "PETG", "color": "#f26b1d"}]})
        self.assertEqual([f["id"] for f in out["filaments"]], ["f1", "f2"])
        self.assertEqual(out["filaments"][0]["color"], "#2f4f8f")
        for bad in ([], [{"name": "", "material": "PLA", "color": "#000000"}],
                    [{"name": "X", "material": "ABS", "color": "#000000"}], [{"name": "X", "material": "PLA", "color": "red"}]):
            with self.subTest(bad=bad), self.assertRaises(StoreError):
                self.s.update({"filaments": bad})

    def test_rejects_bad_numbers_and_paths(self):
        for bad in ({"printer": {"bed_w": 5}}, {"fit": {"headroom_mm": -1}}, {"slicer_path": "C:/evil.bat"}):
            with self.subTest(bad=bad), self.assertRaises(StoreError):
                self.s.update(bad)


if __name__ == "__main__":
    unittest.main()
