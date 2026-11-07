import json
import tempfile
import unittest
from pathlib import Path

from sectortray.store import ProjectStore, StoreError, slugify


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = ProjectStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_drawer_label_tabs(self):
        doc = self.store.create("Screws")
        self.assertIsNone(doc["label_tabs"])
        self.assertTrue(self.store.update(doc["id"], {"label_tabs": True})["label_tabs"])
        with self.assertRaises(StoreError):
            self.store.update(doc["id"], {"label_tabs": "yes"})
        self.assertFalse(self.store.update(doc["id"], {"bins": [{"name": "B"}]})["bins"][0]["label_tab"])  # default off

    def test_saves_from_a_stale_page_are_refused(self):
        doc = self.store.create("Desk")
        self.assertNotIn("rev", doc)  # older drawers have none: counts as 0
        a = self.store.update(doc["id"], {"name": "Desk A", "rev": 0})
        self.assertEqual(a["rev"], 1)
        b = self.store.update(doc["id"], {"name": "Desk B", "rev": 1})  # same page, next save
        self.assertEqual(b["rev"], 2)
        with self.assertRaises(StoreError) as e:  # a tab that still has revision 1
            self.store.update(doc["id"], {"name": "Old tab", "rev": 1})
        self.assertEqual(e.exception.status, 409)
        self.assertEqual(self.store.get(doc["id"])["name"], "Desk B")
        self.assertEqual(self.store.update(doc["id"], {"name": "No rev"})["rev"], 3)  # without rev: saved (old pages)
        self.store.track(doc["id"], "printed", {"plate:A1": {"n": 1, "hash": "x"}})  # print marks aren't edits
        self.assertEqual(self.store.get(doc["id"])["rev"], 3)

    def test_share_and_import(self):
        doc = self.store.create("Desk")
        doc = self.store.update(doc["id"], {"drawer": {"width_mm": 400, "depth_mm": 500, "height_mm": 70},
                                            "bins": [{"name": "Pens", "w": 1, "d": 4, "h": 3, "qty": 2}],
                                            "placements": [{"key": "b1-1", "bin": "b1", "x": 0, "y": 0, "rot": 90}]})
        self.store.track(doc["id"], "printed", {"b1:whole": {"n": 1, "hash": "x"}})
        shared = json.loads(json.dumps(self.store.share(doc["id"])))  # as it travels in a file
        self.assertEqual(shared["format"], "sectortray-drawer")
        self.assertNotIn("printed", shared["drawer"])
        new = self.store.import_shared(shared)
        self.assertEqual(new["id"], "desk-2")  # the name was taken
        self.assertEqual((new["name"], new["drawer"]["width_mm"], new["bins"][0]["name"], new["placements"][0]["rot"]), ("Desk", 400, "Pens", 90))
        self.assertEqual(new.get("printed") or {}, {})
        for bad in ({}, {"format": "x", "drawer": {}}, {"format": "sectortray-drawer", "version": 9, "drawer": {}}):
            with self.assertRaises(StoreError):
                self.store.import_shared(bad)
        broken = {**shared, "drawer": {**shared["drawer"], "name": "Broken", "bins": "nope"}}
        with self.assertRaises(StoreError):
            self.store.import_shared(broken)
        self.assertNotIn("broken", [p["id"] for p in self.store.list()])  # nothing half-made left behind

    def test_typed_sizes_are_kept_one_at_a_time(self):
        doc = self.store.create("Desk")
        self.store.update(doc["id"], {"drawer": {"width_mm": 414.3, "input": {"w": '16 5/16"'}}})
        doc = self.store.update(doc["id"], {"drawer": {"depth_mm": 573.1, "input": {"d": '22 9/16"'}}})
        self.assertEqual(doc["drawer"]["input"], {"w": '16 5/16"', "d": '22 9/16"', "h": ""})

    def test_slugify(self):
        self.assertEqual(slugify("Kitchen — silverware drawer"), "kitchen-silverware-drawer")
        self.assertEqual(slugify("  !!! "), "drawer")

    def test_create_assigns_unique_ids(self):
        a = self.store.create("Workshop drawer")
        b = self.store.create("Workshop drawer")
        self.assertEqual(a["id"], "workshop-drawer")
        self.assertEqual(b["id"], "workshop-drawer-2")
        self.assertEqual(a["tag"], "Workshop drawer")
        self.assertTrue((self.root / "workshop-drawer" / "project.json").is_file())

    def test_create_rejects_blank_name(self):
        with self.assertRaises(StoreError):
            self.store.create("   ")

    def test_update_merges_drawer_and_keeps_raw_input(self):
        p = self.store.create("Kitchen")
        self.store.update(p["id"], {"drawer": {"width_mm": 365.125, "input": {"w": "14 3/8"}}})
        doc = self.store.update(p["id"], {"drawer": {"depth_mm": 495.3}})
        self.assertEqual(doc["drawer"]["width_mm"], 365.125)
        self.assertEqual(doc["drawer"]["depth_mm"], 495.3)
        self.assertIsNone(doc["drawer"]["height_mm"])
        saved = json.loads((self.root / p["id"] / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["drawer"]["width_mm"], 365.125)

    def test_update_validates(self):
        p = self.store.create("Kitchen")
        for bad in ({"drawer": {"width_mm": -3}}, {"drawer": {"width_mm": "wide"}}, {"baseplate": {"style": "huge"}},
                    {"material": "ABS"}, {"id": "hack"}, {"name": ""}):
            with self.subTest(bad=bad), self.assertRaises(StoreError):
                self.store.update(p["id"], bad)

    def test_bins_get_defaults_and_stable_ids(self):
        p = self.store.create("Kitchen")
        doc = self.store.update(p["id"], {"bins": [{"name": "Utensil bin", "w": 2, "d": 5}, {"name": "Spoons"}]})
        self.assertEqual([b["id"] for b in doc["bins"]], ["b1", "b2"])
        self.assertEqual(doc["bins"][0]["h"], 3)
        self.assertFalse(doc["bins"][0]["lip"])  # off by default since 2026-10-23 (user request)
        # keep existing ids, new bin gets the next free one, duplicates are renumbered
        bins = doc["bins"] + [{"name": "New"}, {**doc["bins"][0], "name": "Copy"}]
        doc = self.store.update(p["id"], {"bins": bins})
        self.assertEqual([b["id"] for b in doc["bins"]], ["b1", "b2", "b3", "b4"])
        doc = self.store.update(p["id"], {"bins": doc["bins"][1:]})
        self.assertEqual([b["id"] for b in doc["bins"]], ["b2", "b3", "b4"])

    def test_bins_validated(self):
        p = self.store.create("Kitchen")
        for bad in ({"w": 0}, {"h": 2.5}, {"qty": 100}, {"tab_style": "wavy"}, {"name": ""}, {"x": -1}):
            with self.subTest(bad=bad), self.assertRaises(StoreError):
                self.store.update(p["id"], {"bins": [{"name": "B", **bad}]})

    def test_placements_saved_and_validated(self):
        p = self.store.create("Kitchen")
        doc = self.store.update(p["id"], {"bins": [{"id": "b1", "name": "Utensil", "w": 2, "d": 5, "qty": 2}],
                                          "placements": [{"key": "b1-1", "bin": "b1", "x": 0, "y": 0},
                                                         {"key": "b1-2", "bin": "b1", "x": 2, "y": 0, "rot": True}]})
        self.assertEqual([(q["key"], q["rot"]) for q in doc["placements"]], [("b1-1", 0), ("b1-2", 90)])  # true = 90° (older drawers)
        for bad in ([{"key": "nope", "bin": "b1", "x": 0, "y": 0}], [{"key": "b1-1", "bin": "b1", "x": 1.5, "y": 0}],
                    [{"key": "b1-1", "bin": "b1", "x": 99, "y": 0}], "x"):
            with self.subTest(bad=bad), self.assertRaises(StoreError):
                self.store.update(p["id"], {"placements": bad})

    def test_placements_follow_bins(self):
        p = self.store.create("Kitchen")
        bins = [{"id": "b1", "name": "A", "qty": 2}, {"id": "b2", "name": "B"}]
        pl = [{"key": "b1-1", "bin": "b1", "x": 0, "y": 0}, {"key": "b1-2", "bin": "b1", "x": 2, "y": 0},
              {"key": "b2-1", "bin": "b2", "x": 4, "y": 0}]
        doc = self.store.update(p["id"], {"bins": bins, "placements": pl})
        self.assertEqual(len(doc["placements"]), 3)
        # lower qty → copy 2 goes back to the shelf; delete b2 → its copy goes too
        doc = self.store.update(p["id"], {"bins": [{**doc["bins"][0], "qty": 1}]})
        self.assertEqual([q["key"] for q in doc["placements"]], ["b1-1"])

    def test_rename_keeps_id(self):
        p = self.store.create("Kitchen")
        doc = self.store.update(p["id"], {"name": "Kitchen — silverware", "tag": "Kitchen · Silverware"})
        self.assertEqual(doc["id"], "kitchen")
        self.assertEqual(doc["tag"], "Kitchen · Silverware")

    def test_delete_moves_to_trash(self):
        p = self.store.create("Kitchen")
        dest = Path(self.store.delete(p["id"]))
        self.assertFalse((self.root / "kitchen").exists())
        self.assertTrue((dest / "project.json").is_file())
        self.assertEqual(self.store.list(), [])

    def test_rejects_path_tricks(self):
        for pid in ("../x", "..", "A", "", ".trash"):
            with self.subTest(pid=pid), self.assertRaises(StoreError):
                self.store.get(pid)

    def test_list_skips_damaged_files(self):
        self.store.create("Good")
        bad = self.root / "bad"
        bad.mkdir()
        (bad / "project.json").write_text("{not json", encoding="utf-8")
        self.assertEqual([p["id"] for p in self.store.list()], ["good"])


if __name__ == "__main__":
    unittest.main()
