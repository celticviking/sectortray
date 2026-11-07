"""Local HTTP server: serves web/ and a small JSON API. Listens on 127.0.0.1 only.

API
  GET    /api/health                  app, data dir, OpenSCAD + library versions
  POST   /api/layout                  {drawer, baseplate} -> layout (see layout.py), nothing saved
  GET    /api/projects                all saved drawers, newest first (each with its computed `layout`)
  POST   /api/projects                {name, tag?} -> new drawer
  GET    /api/projects/<id>
  PATCH  /api/projects/<id>           {name?, tag?, drawer?, baseplate?, material?, bins?}
  DELETE /api/projects/<id>           moves the folder to <data>/.trash
  GET    /api/projects/<id>/share     the drawer as a .sectortray file (design only)
  POST   /api/import                  a .sectortray file's contents -> new drawer
  POST   /api/data-folder             {path} moves the saved drawers there (or switches to an existing SectorTray folder)
  POST   /api/projects/<id>/export/baseplate   renders every baseplate piece to <id>/exports/
  POST   /api/projects/<id>/preview/baseplate  renders pieces to <id>/.cache/ for the Planner's 3D view
  POST   /api/projects/<id>/reveal    {what?: "exports"} opens the folder in Explorer
  POST   /api/projects/<id>/bins/render         {bin, part?} -> preview STL (cached by settings), infill size;
                                         part front|back renders one half of a two-piece tray
  POST   /api/projects/<id>/bins/<bin>/export   saved bin -> exports/<name>_<w>x<d>_<h>u.stl
  GET    /api/projects/<id>/files/(cache|exports)/<file>.stl
  GET    /api/projects/<id>/parts               print list: every part with export status
  POST   /api/projects/<id>/export/all          baseplate pieces + every bin
  POST   /api/projects/<id>/open                {files?: [...]} | {plate: n} opens exported parts in the slicer as a 3MF
  POST   /api/projects/<id>/printed             {key, n} n copies of a part printed (0 clears) | {plate: n, on} | {reset: true} → parts
  GET    /api/settings / PATCH /api/settings    printer, fit, filaments, slicer_path, new_bins (+ detected slicer,
                                               printer_confirmed: false on a fresh install)

Project responses carry a computed `layout` (null until measured); it is never stored.
"""
import json
import mimetypes
import os
import re
import subprocess
import sys
import traceback
from functools import lru_cache
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__, bins, datafolder, export, layout, parts, paths, split, tray
from .settings import Settings, slicer_version
from .openscad import OpenSCADError
from .store import ProjectStore, StoreError, _clean_bin

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")

PROJECT_RE = re.compile(r"^/api/projects/([a-z0-9-]+)(/reveal|/export/baseplate|/preview/baseplate|/export/all|/parts|/open|/printed|/share|/bins/render|/bins/[a-z0-9]{1,12}/export)?$")
FILE_RE = re.compile(r"^/api/projects/([a-z0-9-]+)/files/(\.cache|cache|exports)/([A-Za-z0-9_.-]{1,120}\.stl)$")


MAX_BODY = 1_000_000


@lru_cache(maxsize=1)
def openscad_version() -> str | None:
    if not paths.OPENSCAD.exists():
        return None
    try:
        r = subprocess.run([str(paths.OPENSCAD), "--version"], capture_output=True, text=True, timeout=20)
        return (r.stdout + r.stderr).strip().removeprefix("OpenSCAD version ") or None
    except (OSError, subprocess.TimeoutExpired):
        return None


@lru_cache(maxsize=1)
def library_versions() -> list[dict]:
    libs = []
    for name, license_ in (("gridfinity-rebuilt-openscad", "MIT"), ("gridfinity_extended_openscad", "GPL-3.0")):
        d = paths.VENDOR / name
        commit = None
        try:  # portable bundle: no git there, tools/build_dist.py writes the version to this file
            commit = (d / ".sectortray-version").read_text(encoding="utf-8").strip() or None
        except OSError:
            try:
                r = subprocess.run(["git", "-C", str(d), "log", "-1", "--format=%h %cs"], capture_output=True, text=True, timeout=10)
                commit = r.stdout.strip() or None
            except (OSError, subprocess.TimeoutExpired):
                pass
        libs.append({"name": name, "license": license_, "present": d.is_dir(), "commit": commit})
    return libs


class Handler(SimpleHTTPRequestHandler):
    server_version = f"SectorTray/{__version__}"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(paths.WEB), **kwargs)

    @property
    def store(self) -> ProjectStore:
        return self.server.store

    @property
    def settings(self) -> Settings:
        return self.server.settings

    def _layout(self, doc: dict):
        clearance, headroom = self.settings.fit()
        return layout.for_project(doc, self.settings.bed(), clearance, headroom)

    def _pitch(self, doc: dict) -> list:
        lay = self._layout(doc)
        return (lay or {}).get("pitch") or [42.0, 42.0] if lay and "error" not in lay else [42.0, 42.0]

    def _pitched(self, doc: dict) -> dict:
        """The drawer with each bin tagged with the drawer's square size (not stored; renders use it), and the
        default filament (parts without one use it; the Print List keeps filaments on separate plates)."""
        p = self._pitch(doc)
        return {**doc, "bins": [{**b, "_pitch": p} for b in doc["bins"]],
                "_filament": self.settings.get()["filaments"][0]["id"]}

    def _bed(self) -> tuple:
        b = self.settings.bed()
        return (b.width, b.depth)

    def with_layout(self, doc: dict) -> dict:
        return {**doc, "layout": self._layout(doc)}

    def _slicer_info(self) -> dict:
        return self.settings.slicer_info()

    # ---- guards ----
    def _host_ok(self) -> bool:
        """Blocks DNS-rebinding: only answer requests addressed to this machine by name or IP."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost", "::1")

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        o = urlsplit(origin)
        return o.hostname in ("127.0.0.1", "localhost", "::1") and o.port == self.server.server_address[1]

    def _mutation_ok(self) -> bool:
        # Requiring JSON forces a CORS preflight from any other website, which we never approve.
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        return self._origin_ok() and ctype == "application/json"

    # ---- helpers ----
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/") or (args and str(args[1])[0] in "45"):
            sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def _json(self, status: int, body) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise StoreError("Request too large.", 413)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            raise StoreError("Body isn't valid JSON.")

    def _dispatch(self, method: str) -> None:
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "Wrong host.")
        path = urlsplit(self.path).path
        if not path.startswith("/api/"):
            if method == "GET":
                if path == "/":
                    self.path = "/projects.html"
                return super().do_GET()
            return self._error(HTTPStatus.METHOD_NOT_ALLOWED, "Not allowed.")
        if method != "GET" and not self._mutation_ok():
            return self._error(HTTPStatus.FORBIDDEN, "Requests must come from SectorTray itself.")
        try:
            self._api(method, path)
        except StoreError as e:
            self._error(e.status, str(e))
        except layout.LayoutError as e:
            self._error(HTTPStatus.BAD_REQUEST, str(e))
        except OpenSCADError as e:
            self.log_error("OpenSCAD: %s", e)
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e), "log": e.log[-4000:]})
        except Exception as e:  # keep the server alive; report the failure to the page
            self.log_error("API error: %r\n%s", e, traceback.format_exc())
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Something went wrong on the SectorTray server.")

    def _api(self, method: str, path: str) -> None:
        if path == "/api/health" and method == "GET":
            return self._json(200, {
                "app": "sectortray", "version": __version__, "dev": paths.DEV, "data_dir": str(self.store.root),
                "data_movable": self.server.movable, "data_cloud": datafolder.cloud_synced(self.store.root),
                "openscad": {"present": paths.OPENSCAD.exists(), "version": openscad_version()},
                "libraries": library_versions(), "slicer": self._slicer_info(),
            })
        if path == "/api/settings":
            if method == "GET":
                return self._json(200, {**self.settings.get(), "slicer": self._slicer_info(),
                                        "printer_confirmed": self.settings.printer_confirmed()})
            if method == "PATCH":
                return self._json(200, {**self.settings.update(self._body()), "slicer": self._slicer_info(),
                                        "printer_confirmed": True})
        if path == "/api/joint-test" and method == "POST":
            body = self._body()
            r = bins.joint_test(self.store.root, self._bed(), self.settings.joint_gap())
            folder = Path(r["folder"])
            if body.get("open"):
                slicer = self.settings.slicer()
                if not slicer:
                    raise StoreError(f"{self.settings.slicer_name()} wasn't found. Set its location in Settings.", 404)
                parts.launch_slicer(slicer, [str(folder / f) for f in r["files"]])
            elif body.get("reveal") and sys.platform == "win32":
                os.startfile(folder)  # noqa: S606 — our own data folder
            return self._json(200, r)
        if path == "/api/layout" and method == "POST":
            b = self._body()
            dr, bp = b.get("drawer") or {}, b.get("baseplate") or {}
            if not (isinstance(dr.get("width_mm"), (int, float)) and isinstance(dr.get("depth_mm"), (int, float))):
                raise StoreError("Need drawer width_mm and depth_mm.")
            h = dr.get("height_mm")
            clearance, headroom = self.settings.fit()
            return self._json(200, layout.compute(dr["width_mm"], dr["depth_mm"], h if isinstance(h, (int, float)) else None,
                                                  bp.get("style", "standard"), bool(bp.get("magnets", True)),
                                                  self.settings.bed(), clearance, headroom,
                                                  ((bp.get("align") or {}).get("x", "center"), (bp.get("align") or {}).get("y", "center")),
                                                  bp.get("grid", "standard")))
        if path == "/api/projects":
            if method == "GET":
                return self._json(200, {"projects": [self.with_layout(p) for p in self.store.list()]})
            if method == "POST":
                b = self._body()
                return self._json(201, self.with_layout(self.store.create(b.get("name"), b.get("tag"))))
        if path == "/api/data-folder" and method == "POST":
            return self._json(200, datafolder.move(self.server, self._body().get("path")))
        if path == "/api/import" and method == "POST":
            return self._json(201, self.with_layout(self.store.import_shared(self._body())))
        m = FILE_RE.match(path)
        if m and method == "GET":
            sub = bins.CACHE if m.group(2) in ("cache", ".cache") else "exports"
            f = self.store._dir(m.group(1)) / sub / m.group(3)
            if ".." in m.group(3) or not f.is_file():
                raise StoreError("No such file.", 404)
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "model/stl")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        m = PROJECT_RE.match(path)
        if m:
            pid, action = m.group(1), m.group(2)
            if action == "/bins/render" and method == "POST":
                doc = self.store.get(pid)  # 404 if the drawer doesn't exist
                body = self._body()
                pitch = self._pitch(doc)
                b = {**_clean_bin(body.get("bin") or {}, "preview", pitch), "_pitch": pitch}
                part = body.get("part", "whole")
                if part in ("front", "back") and b["kind"] == "tray" and tray.parts_of(b, self._bed()) == ["front", "back"]:
                    return self._json(200, tray.render(self.store._dir(pid), b, part, self._bed()))
                if part == "pieces" and split.is_split(b, self._bed()):
                    return self._json(200, bins.render_pieces(self.store._dir(pid), b, self._bed(), self.settings.joint_gap()))
                return self._json(200, bins.render_preview(self.store._dir(pid), b, self._bed()))
            if action and action.startswith("/bins/") and action.endswith("/export") and method == "POST":
                doc = self._pitched(self.store.get(pid))
                return self._json(200, bins.export_bin(self.store._dir(pid), doc["bins"], action.split("/")[2], self._bed(),
                                                       self.settings.joint_gap()))
            if action == "/reveal" and method == "POST":
                folder = self.store._dir(pid)
                if not folder.is_dir():
                    raise StoreError("No such drawer.", 404)
                body = self._body()
                if body.get("what") == "exports" and (folder / "exports").is_dir():
                    folder = folder / "exports"
                if body.get("open", True) and sys.platform == "win32":
                    os.startfile(folder)  # noqa: S606 — opens Explorer on our own data folder
                return self._json(200, {"path": str(folder)})
            if action in ("/export/baseplate", "/preview/baseplate") and method == "POST":
                doc = self.store.get(pid)
                lay = self._layout(doc)
                if not lay or "error" in lay:
                    raise StoreError((lay or {}).get("error") or "Measure the drawer first.")
                fn = export.export_baseplate if action == "/export/baseplate" else export.preview_baseplate
                return self._json(200, fn(self.store._dir(pid), lay))
            if action in ("/parts", "/export/all", "/open", "/printed"):
                doc = self._pitched(self.store.get(pid))
                lay = self._layout(doc)
                lay = lay if lay and "error" not in lay else None
                folder = self.store._dir(pid)
                if action == "/parts" and method == "GET":
                    return self._json(200, parts.list_parts(folder, doc, lay, self._bed(), self.settings.joint_gap()))
                if action == "/export/all" and method == "POST":
                    return self._json(200, parts.export_all(folder, doc, lay, self._bed(), self.settings.joint_gap()))
                if action == "/open" and method == "POST":
                    body = self._body()
                    r = parts.open_in_slicer(self.settings.slicer(), folder, doc, lay, body.get("files"), self._bed(),
                                             self.settings.joint_gap(), body.get("plate"), self.settings.slicer_name())
                    self.store.track(pid, "opened", r.pop("sent"))
                    return self._json(200, r)
                if action == "/printed" and method == "POST":
                    body = self._body()
                    if body.get("reset") is True:
                        self.store.track(pid, "printed", None)
                    elif "plate" in body:
                        full = parts.list_parts(folder, doc, lay, self._bed(), self.settings.joint_gap())
                        self.store.track(pid, "printed", parts.plate_printed(full, body["plate"], bool(body.get("on"))))
                    else:
                        listing = parts.list_parts(folder, doc, lay, self._bed(), self.settings.joint_gap())["parts"]
                        self.store.track(pid, "printed", parts.set_printed(listing, body.get("key"), body.get("n")))
                    doc = self._pitched(self.store.get(pid))
                    return self._json(200, parts.list_parts(folder, doc, lay, self._bed(), self.settings.joint_gap()))
            if action == "/share" and method == "GET":
                return self._json(200, self.store.share(pid))
            if not action:
                if method == "GET":
                    return self._json(200, self.with_layout(self.store.get(pid)))
                if method == "PATCH":
                    return self._json(200, self.with_layout(self.store.update(pid, self._body())))
                if method == "DELETE":
                    return self._json(200, {"trashed_to": self.store.delete(pid)})
        self._error(HTTPStatus.NOT_FOUND, "No such API route.")

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        if self._host_ok():
            super().do_HEAD()
        else:
            self.send_error(HTTPStatus.FORBIDDEN)

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")


class SectorTrayServer(ThreadingHTTPServer):
    daemon_threads = True
    movable = False  # True when started normally: Settings may then move the data folder

    def __init__(self, address, store: ProjectStore, settings: Settings):
        super().__init__(address, Handler)
        self.store = store
        self.settings = settings

    def handle_error(self, request, client_address):
        # A page closed or left while its request was still being answered: harmless, so keep it out of the log.
        if isinstance(sys.exc_info()[1], (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def make_server(host: str, port: int, data_dir: Path) -> SectorTrayServer:
    store, settings = ProjectStore(data_dir), Settings(data_dir)

    def pitch_for(doc):  # trays size themselves in the drawer's squares
        lay = layout.for_project(doc, settings.bed(), *settings.fit())
        return (lay or {}).get("pitch") if lay and "error" not in lay else None
    store.pitch_for = pitch_for
    return SectorTrayServer((host, port), store, settings)
