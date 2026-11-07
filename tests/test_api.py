import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from sectortray import paths
from sectortray.server import make_server


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.server = make_server("127.0.0.1", 0, Path(cls.tmp.name))
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def req(self, method, path, body=None, headers=None):
        h = {"Host": f"127.0.0.1:{self.port}"}
        if body is not None:
            h["Content-Type"] = "application/json"
        if method in ("DELETE", "POST") and body is None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)  # tray renders take ~6 s
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        ctype = r.getheader("Content-Type") or ""
        return r.status, (json.loads(data) if "json" in ctype else data)

    def test_health(self):
        s, b = self.req("GET", "/api/health")
        self.assertEqual(s, 200)
        self.assertEqual(b["app"], "sectortray")

    def test_project_lifecycle(self):
        s, p = self.req("POST", "/api/projects", {"name": "Bench drawer 3"})
        self.assertEqual(s, 201)
        pid = p["id"]
        s, p = self.req("PATCH", f"/api/projects/{pid}", {"drawer": {"width_mm": 533.4, "input": {"w": '21"'}}})
        self.assertEqual((s, p["drawer"]["width_mm"]), (200, 533.4))
        s, b = self.req("GET", "/api/projects")
        self.assertIn(pid, [x["id"] for x in b["projects"]])
        s, b = self.req("DELETE", f"/api/projects/{pid}")
        self.assertEqual(s, 200)
        s, b = self.req("GET", f"/api/projects/{pid}")
        self.assertEqual(s, 404)

    def test_validation_error_is_400_with_message(self):
        s, p = self.req("POST", "/api/projects", {"name": "V"})
        s, b = self.req("PATCH", f"/api/projects/{p['id']}", {"material": "ABS"})
        self.assertEqual(s, 400)
        self.assertIn("Material", b["error"])

    def test_layout_route(self):
        s, L = self.req("POST", "/api/layout", {"drawer": {"width_mm": 365.125, "depth_mm": 495.3, "height_mm": 69.85},
                                                "baseplate": {"style": "standard"}})
        self.assertEqual((s, L["nx"], L["ny"], len(L["pieces"])), (200, 8, 11, 6))
        s, b = self.req("POST", "/api/layout", {"drawer": {"width_mm": 30, "depth_mm": 30}})
        self.assertEqual(s, 400)
        self.assertIn("smaller", b["error"])

    def test_project_carries_layout(self):
        s, p = self.req("POST", "/api/projects", {"name": "Layout carrier"})
        self.assertIsNone(p["layout"])
        s, p = self.req("PATCH", f"/api/projects/{p['id']}", {"drawer": {"width_mm": 200, "depth_mm": 200}})
        self.assertEqual(p["layout"]["nx"], 4)

    @unittest.skipUnless(paths.OPENSCAD.exists(), "OpenSCAD not installed")
    def test_export_baseplate(self):
        s, p = self.req("POST", "/api/projects", {"name": "Export test"})
        pid = p["id"]
        s, b = self.req("POST", f"/api/projects/{pid}/export/baseplate")
        self.assertEqual(s, 400)  # not measured yet
        self.req("PATCH", f"/api/projects/{pid}", {"drawer": {"width_mm": 365.125, "depth_mm": 495.3, "height_mm": 69.85}})
        s, r = self.req("POST", f"/api/projects/{pid}/export/baseplate")
        self.assertEqual(s, 200, r)
        self.assertEqual(sorted(f["piece"] for f in r["files"]), ["A1", "A2", "A3", "B1", "B2", "B3"])
        self.assertEqual(r["warnings"], [])
        folder = Path(r["folder"])
        self.assertTrue(all((folder / f["file"]).stat().st_size > 1000 for f in r["files"]))
        s, r2 = self.req("POST", f"/api/projects/{pid}/export/baseplate")
        self.assertEqual(r2["rendered"], 0)  # unchanged → all cached
        # Shrink the drawer: fewer pieces, stale files removed
        self.req("PATCH", f"/api/projects/{pid}", {"drawer": {"width_mm": 200, "depth_mm": 200}})
        s, r3 = self.req("POST", f"/api/projects/{pid}/export/baseplate")
        self.assertEqual([f["piece"] for f in r3["files"]], ["A1"])
        self.assertEqual(sorted(x.name for x in folder.glob("baseplate_*.stl")), ["baseplate_A1.stl"])

    @unittest.skipUnless(paths.OPENSCAD.exists(), "OpenSCAD not installed")
    def test_bin_render_and_export(self):
        s, p = self.req("POST", "/api/projects", {"name": "Bin test"})
        pid = p["id"]
        draft = {"name": "Utensil bin", "w": 2, "d": 3, "h": 3, "divx": 2}
        s, r = self.req("POST", f"/api/projects/{pid}/bins/render", {"bin": draft})
        self.assertEqual(s, 200, r)
        self.assertFalse(r["cached"])
        self.assertEqual(len(r["infill_mm"]), 3)
        self.assertGreater(r["infill_mm"][0], 70)  # ~2×42 minus walls
        s, stl = self.req("GET", f"/api/projects/{pid}/files/cache/{r['file']}")
        self.assertEqual(s, 200)
        self.assertGreater(len(stl), 10_000)
        s, r2 = self.req("POST", f"/api/projects/{pid}/bins/render", {"bin": draft})
        self.assertTrue(r2["cached"])
        s, bad = self.req("POST", f"/api/projects/{pid}/bins/render", {"bin": {**draft, "w": 0}})
        self.assertEqual(s, 400)
        # export needs the bin saved
        s, doc = self.req("PATCH", f"/api/projects/{pid}", {"bins": [draft]})
        bid = doc["bins"][0]["id"]
        s, e = self.req("POST", f"/api/projects/{pid}/bins/{bid}/export")
        self.assertEqual((s, e["file"]), (200, "utensil-bin_2x3_3u.stl"))
        self.assertTrue((Path(e["folder"]) / e["file"]).is_file())
        # resize → new file name replaces the old export
        self.req("PATCH", f"/api/projects/{pid}", {"bins": [{**doc["bins"][0], "h": 6}]})
        s, e2 = self.req("POST", f"/api/projects/{pid}/bins/{bid}/export")
        self.assertEqual(e2["file"], "utensil-bin_2x3_6u.stl")
        self.assertFalse((Path(e["folder"]) / e["file"]).exists())

    def test_settings_drive_layout(self):
        s, st = self.req("GET", "/api/settings")
        self.assertEqual(st["printer"]["bed_w"], 250)
        self.assertIn("slicer", st)
        s, st = self.req("PATCH", "/api/settings", {"printer": {"bed_w": 180, "bed_d": 180}})
        self.assertEqual(s, 200)
        try:
            s, L = self.req("POST", "/api/layout", {"drawer": {"width_mm": 365.125, "depth_mm": 495.3}})
            self.assertEqual(L["bed"]["width"], 180)
            self.assertGreater(len(L["pieces"]), 6)
        finally:
            self.req("PATCH", "/api/settings", {"printer": {"bed_w": 250, "bed_d": 220}})

    @unittest.skipUnless(paths.OPENSCAD.exists(), "OpenSCAD not installed")
    def test_parts_export_all_and_open(self):
        from unittest import mock
        s, p = self.req("POST", "/api/projects", {"name": "Parts test"})
        pid = p["id"]
        self.req("PATCH", f"/api/projects/{pid}", {"drawer": {"width_mm": 200, "depth_mm": 200, "height_mm": 60},
                                                    "bins": [{"name": "Spoons", "w": 1, "d": 4, "qty": 3}]})
        s, r = self.req("GET", f"/api/projects/{pid}/parts")
        self.assertEqual([(x["code"], x["status"]) for x in r["parts"]], [("A1", "none"), ("S1", "none")])
        self.assertEqual(r["prints"], 4)
        # (Popen is patched only around the slicer calls: OpenSCAD renders go through subprocess too.)
        with mock.patch("sectortray.parts.subprocess.Popen"):
            s, e = self.req("POST", f"/api/projects/{pid}/open")
            self.assertIn(s, (400, 404))  # nothing exported yet (or no PrusaSlicer)
        s, ex = self.req("POST", f"/api/projects/{pid}/export/all")
        self.assertEqual((s, sorted(ex["files"]), ex["errors"]), (200, ["baseplate_A1.stl", "spoons_1x4_3u.stl"], []))
        s, r = self.req("GET", f"/api/projects/{pid}/parts")
        self.assertEqual({x["status"] for x in r["parts"]}, {"done"})
        self.assertTrue(all(x["volume"] > 5 for x in r["parts"]), [x["volume"] for x in r["parts"]])  # cm³
        with mock.patch("sectortray.parts.subprocess.Popen") as popen:
            s, o = self.req("POST", f"/api/projects/{pid}/open")
            if s == 404:
                self.skipTest("PrusaSlicer not installed")
            self.assertEqual((o["opened"], o["beds"], o["file"]), (4, 2, "Parts test.3mf"))  # 1 plate + 3 copies of the bin; a 199 mm plate leaves no room for them
            args = popen.call_args[0][0]
            self.assertEqual(len(args), 2)  # the slicer + one 3MF
            import zipfile
            with zipfile.ZipFile(args[1]) as z:
                model = z.read("3D/3dmodel.model").decode()
            self.assertEqual((model.count("<item "), model.count('name="S1 Spoons"'), model.count('name="Baseplate A1"')), (4, 1, 1))
            s, bad = self.req("POST", f"/api/projects/{pid}/open", {"files": ["../project.json"]})
            self.assertEqual(s, 400)
        s, r = self.req("GET", f"/api/projects/{pid}/parts")
        self.assertTrue(all(x["opened"] for x in r["parts"]))  # remembered as sent to PrusaSlicer
        # change the bin → its export is stale, and it no longer counts as opened
        s, doc = self.req("GET", f"/api/projects/{pid}")
        self.req("PATCH", f"/api/projects/{pid}", {"bins": [{**doc["bins"][0], "scoop": False}]})
        s, r = self.req("GET", f"/api/projects/{pid}/parts")
        self.assertEqual([x["status"] for x in r["parts"]], ["done", "stale"])
        self.assertEqual([bool(x["opened"]) for x in r["parts"]], [True, False])

    def test_printed_tracking(self):
        s, p = self.req("POST", "/api/projects", {"name": "Printed test"})
        pid = p["id"]
        self.req("PATCH", f"/api/projects/{pid}", {"drawer": {"width_mm": 200, "depth_mm": 200, "height_mm": 60},
                                                    "bins": [{"name": "Spoons", "w": 1, "d": 4, "qty": 3}]})
        s, r = self.req("GET", f"/api/projects/{pid}/parts")
        self.assertEqual([(x["key"], x["printed"]) for x in r["parts"]], [("plate:A1", 0), ("b1:whole", 0)])
        s, r = self.req("POST", f"/api/projects/{pid}/printed", {"key": "b1:whole", "n": 2})
        self.assertEqual((s, r["printed"], r["parts"][1]["printed"]), (200, 2, 2))
        for bad in ({"key": "b1:whole", "n": 4}, {"key": "b1:whole", "n": True}, {"key": "nope", "n": 1}):
            self.assertEqual(self.req("POST", f"/api/projects/{pid}/printed", bad)[0], 400)
        s, r = self.req("POST", f"/api/projects/{pid}/printed", {"key": "plate:A1", "n": 1})
        self.assertEqual(r["printed"], 3)
        # survives a reload of the drawer; renaming keeps it, a real change marks it as an older version
        s, doc = self.req("GET", f"/api/projects/{pid}")
        self.assertEqual(doc["printed"]["b1:whole"]["n"], 2)
        self.req("PATCH", f"/api/projects/{pid}", {"bins": [{**doc["bins"][0], "name": "Forks"}]})
        s, r = self.req("GET", f"/api/projects/{pid}/parts")
        self.assertFalse(r["parts"][1]["printed_old"])
        self.req("PATCH", f"/api/projects/{pid}", {"bins": [{**doc["bins"][0], "h": 4}]})
        s, r = self.req("GET", f"/api/projects/{pid}/parts")
        self.assertEqual((r["parts"][1]["printed"], r["parts"][1]["printed_old"], r["printed"]), (2, True, 1))
        # deleting the bin forgets it; reset clears the rest
        self.req("PATCH", f"/api/projects/{pid}", {"bins": []})
        s, doc = self.req("GET", f"/api/projects/{pid}")
        self.assertEqual(list(doc["printed"]), ["plate:A1"])
        s, r = self.req("POST", f"/api/projects/{pid}/printed", {"reset": True})
        self.assertEqual(r["printed"], 0)

    def test_slicer_choice(self):
        s, st = self.req("GET", "/api/settings")
        self.assertEqual((st["slicer_kind"], st["slicer"]["name"]), ("prusaslicer", "PrusaSlicer"))
        self.assertEqual([x["kind"] for x in st["slicer"]["all"]], ["prusaslicer", "bambu", "orca", "cura"])
        try:
            self.assertEqual(self.req("PATCH", "/api/settings", {"slicer_path": r"C:\Somewhere\prusa-slicer.exe"})[0], 400)  # no such file
            s, st = self.req("PATCH", "/api/settings", {"slicer_path": sys.executable})  # any real .exe
            self.assertEqual(st["slicer_path"], sys.executable)
            s, st = self.req("PATCH", "/api/settings", {"slicer_kind": "orca"})
            self.assertEqual((s, st["slicer"]["name"], st["slicer_path"], st["slicer"]["multibed"]), (200, "OrcaSlicer", None, False))
            self.assertEqual(self.req("PATCH", "/api/settings", {"slicer_kind": "simplify3d"})[0], 400)
        finally:
            self.req("PATCH", "/api/settings", {"slicer_kind": "prusaslicer", "slicer_path": None})

    def test_plates(self):
        s, p = self.req("POST", "/api/projects", {"name": "Plates test"})
        pid = p["id"]
        self.req("PATCH", f"/api/projects/{pid}", {"drawer": {"width_mm": 200, "depth_mm": 200, "height_mm": 60},
                                                    "bins": [{"name": "Spoons", "w": 1, "d": 4, "qty": 3}]})
        s, r = self.req("GET", f"/api/projects/{pid}/parts")
        # the 199 mm baseplate fills a bed; the three 41.5 × 167.5 bins share the next one
        self.assertEqual([[(x["key"], x["copies"], x["before"]) for x in pl["parts"]] for pl in r["plates"]],
                         [[("plate:A1", 1, 0)], [("b1:whole", 3, 0)]])
        s, r = self.req("POST", f"/api/projects/{pid}/printed", {"plate": 2, "on": True})
        self.assertEqual((r["parts"][1]["printed"], [pl["printed"] for pl in r["plates"]]), (3, [False, True]))
        s, r = self.req("POST", f"/api/projects/{pid}/printed", {"plate": 2, "on": False})
        self.assertEqual(r["parts"][1]["printed"], 0)
        self.assertEqual(self.req("POST", f"/api/projects/{pid}/printed", {"plate": 9, "on": True})[0], 400)

    @unittest.skipUnless(paths.OPENSCAD.exists(), "OpenSCAD not installed")
    def test_baseplate_preview_goes_to_cache_not_exports(self):
        s, p = self.req("POST", "/api/projects", {"name": "Preview test"})
        pid = p["id"]
        self.req("PATCH", f"/api/projects/{pid}", {"drawer": {"width_mm": 365.125, "depth_mm": 495.3, "height_mm": 69.85}})
        s, r = self.req("POST", f"/api/projects/{pid}/preview/baseplate")
        self.assertEqual(s, 200, r)
        self.assertEqual(sorted(f["piece"] for f in r["files"]), ["A1", "A2", "A3", "B1", "B2", "B3"])
        s, stl = self.req("GET", f"/api/projects/{pid}/files/cache/{r['files'][0]['file']}")
        self.assertEqual(s, 200)
        s, r2 = self.req("POST", f"/api/projects/{pid}/preview/baseplate")
        self.assertEqual(r2["rendered"], 0)
        s, parts_ = self.req("GET", f"/api/projects/{pid}/parts")
        self.assertTrue(all(x["status"] == "none" for x in parts_["parts"]))  # previews aren't exports

    @unittest.skipUnless(paths.OPENSCAD.exists(), "OpenSCAD not installed")
    def test_tray_in_halves_end_to_end(self):
        import struct
        s, p = self.req("POST", "/api/projects", {"name": "Tray test"})
        pid = p["id"]
        us = [{"name": "Dinner fork", "kind": "fork", "len": 202, "handle": 15, "head": 28, "count": 8},
              {"name": "Dinner knife", "kind": "knife", "len": 230.2, "handle": 16, "head": 22, "count": 8},
              {"name": "Soup spoon", "kind": "spoon", "len": 191, "handle": 15, "head": 40, "count": 8},
              {"name": "Dessert spoon", "kind": "spoon", "len": 180, "handle": 14, "head": 36, "count": 8},
              {"name": "Teaspoon", "kind": "spoon", "len": 155, "handle": 12, "head": 33, "count": 8}]
        bad = {"id": "t1", "name": "One", "kind": "tray", "h": 3, "tray": {"utensils": us[:1], "margin": "Normal"}}
        s, e = self.req("PATCH", f"/api/projects/{pid}", {"bins": [bad]})
        self.assertEqual(s, 400)
        self.assertIn("two utensil", e["error"])
        t = {"id": "t1", "name": "Silverware tray", "kind": "tray", "h": 3, "magnets": False,
             "tray": {"utensils": us, "margin": "Normal", "halves": True}}
        s, doc = self.req("PATCH", f"/api/projects/{pid}", {"bins": [t]})
        self.assertEqual((s, doc["bins"][0]["w"], doc["bins"][0]["d"]), (200, 5, 6))  # footprint comes from the utensils
        s, r = self.req("GET", f"/api/projects/{pid}/parts")
        self.assertEqual([(x["part"], x["status"]) for x in r["parts"]], [("front", "none"), ("back", "none")])
        s, ex = self.req("POST", f"/api/projects/{pid}/bins/t1/export")
        self.assertEqual(s, 200, ex)
        self.assertEqual(ex["files"], ["silverware-tray_5x6_3u_front-half.stl", "silverware-tray_5x6_3u_back-half.stl"])

        def depth(f):
            data = f.read_bytes(); n = struct.unpack_from("<I", data, 80)[0]
            ys = [struct.unpack_from("<9f", data, 84 + i * 50 + 12)[k] for i in range(n) for k in (1, 4, 7)]
            return max(ys) - min(ys)
        for f in ex["files"]:
            self.assertAlmostEqual(depth(Path(ex["folder"]) / f), 128.8, delta=0.5)  # 3 squares + clips
        s, r = self.req("GET", f"/api/projects/{pid}/parts")
        self.assertEqual({x["status"] for x in r["parts"]}, {"done"})

    def test_file_route_rejects_tricks(self):
        s, p = self.req("POST", "/api/projects", {"name": "Files"})
        for bad in ("cache/../project.json", "exports/x.txt", "secret/x.stl"):
            s, _ = self.req("GET", f"/api/projects/{p['id']}/files/{bad}")
            self.assertIn(s, (400, 404))

    def test_blocks_foreign_host(self):
        s, _ = self.req("GET", "/api/projects", headers={"Host": "evil.example:80"})
        self.assertEqual(s, 403)

    def test_blocks_cross_site_writes(self):
        s, _ = self.req("POST", "/api/projects", {"name": "X"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(s, 403)
        s, _ = self.req("POST", "/api/projects", None, headers={"Content-Type": "text/plain"})
        self.assertEqual(s, 403)

    def test_serves_pages_and_blocks_traversal(self):
        s, body = self.req("GET", "/")
        self.assertEqual(s, 200)
        self.assertIn(b"SectorTray", body)
        s, _ = self.req("GET", "/../sectortray/server.py")
        self.assertIn(s, (400, 404))


if __name__ == "__main__":
    unittest.main()
