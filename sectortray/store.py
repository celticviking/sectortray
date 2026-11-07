"""Saved drawers ("projects"), one folder each under the data dir:

    <data>/<project-id>/project.json
    <data>/.trash/<project-id>-<timestamp>/      (deleted projects, recoverable by hand)

All lengths are stored in millimeters. The raw text the user typed is kept alongside,
so a field shows "14 3/8" exactly as entered when the drawer is reopened.
"""
import json
import os
import re
import shutil
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from . import split, tray
from .layout import ALIGN_X, ALIGN_Y, GRID_MODES

SCHEMA = 1
PROJECT_FILE = "project.json"
TRASH = ".trash"
SHARE_FORMAT = "sectortray-drawer"
EDITABLE = ("name", "tag", "drawer", "baseplate", "material", "bins", "placements", "label_tabs")
TRACK_FIELDS = ("printed", "opened")  # Print List tracking, keyed by part ("plate:A1", "b3:whole"); written by track()
PLATE_STYLES = ("standard", "lite", "weighted")
MATERIALS = ("PLA", "PETG")


class StoreError(Exception):
    """A request the store refuses; the message is safe to show the user."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:48].strip("-") or "drawer"


def _clean_name(value, field: str, max_len: int = 80) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StoreError(f"{field} can't be empty.")
    value = " ".join(value.split())
    if len(value) > max_len:
        raise StoreError(f"{field} is too long (max {max_len} characters).")
    return value


def _clean_mm(value, field: str):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 < value <= 1500):
        raise StoreError(f"{field} must be a length in mm between 0 and 1500.")
    return round(float(value), 3)


def _clean_drawer(d) -> dict:
    if not isinstance(d, dict):
        raise StoreError("drawer must be an object.")
    raw = d.get("input") or {}
    if not isinstance(raw, dict):
        raise StoreError("drawer.input must be an object.")
    return {
        "width_mm": _clean_mm(d.get("width_mm"), "Width"),
        "depth_mm": _clean_mm(d.get("depth_mm"), "Depth"),
        "height_mm": _clean_mm(d.get("height_mm"), "Height"),
        "input": {k: str(raw.get(k, ""))[:40] for k in ("w", "d", "h")},
    }


def _clean_baseplate(b) -> dict:
    if not isinstance(b, dict):
        raise StoreError("baseplate must be an object.")
    style = b.get("style", "standard")
    if style not in PLATE_STYLES:
        raise StoreError(f"Plate style must be one of {', '.join(PLATE_STYLES)}.")
    al = b.get("align") or {}
    if not isinstance(al, dict) or al.get("x", "center") not in ALIGN_X or al.get("y", "center") not in ALIGN_Y:
        raise StoreError("Grid position must be center, left or right across, and center, back or front.")
    grid = b.get("grid", "standard")
    if grid not in GRID_MODES:
        raise StoreError("Grid size must be standard or fit.")
    return {"magnets": bool(b.get("magnets", True)), "style": style,
            "filament": _clean_filament_ref(b.get("filament")),
            "align": {"x": al.get("x", "center"), "y": al.get("y", "center")}, "grid": grid}


TAB_STYLES = ("auto", "full", "left", "center", "right")
BIN_DEFAULTS = {
    "name": "Bin", "w": 2, "d": 2, "h": 3, "qty": 1, "divx": 1, "divy": 1,
    "label_tab": False, "tab_style": "auto", "scoop": True, "lip": False,
    "magnets": True, "screws": False, "half_grid": False, "x": None, "y": None,
    "filament": None,  # settings filament id; None → the first filament in Settings
    "kind": "bin",     # "bin" (kennetek) or "tray" (silverware wizard, Gridfinity Extended)
    "tray": None,      # tray spec when kind == "tray" (see sectortray/tray.py)
    "split": None,     # {on, joint: none|clips|keys, x: [squares…]|None, y: …} for a bin too big for the bed (split.py)
}
FILAMENT_ID = re.compile(r"[a-z0-9]{1,12}")


def _clean_filament_ref(v):
    if v is None or (isinstance(v, str) and FILAMENT_ID.fullmatch(v)):
        return v
    raise StoreError("Filament must be a filament id from Settings.")
_BIN_RANGES = {"w": (1, 20), "d": (1, 20), "h": (1, 30), "qty": (1, 99), "divx": (1, 12), "divy": (1, 12)}


def _num(v, what, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise StoreError(f"{what} must be between {lo} and {hi} mm.")
    return round(float(v), 3)


def _clean_tray(spec) -> dict:
    if not isinstance(spec, dict) or not isinstance(spec.get("utensils"), list):
        raise StoreError("A tray needs a list of utensils.")
    us = spec["utensils"]
    if not 1 <= len(us) <= tray.MAX_UTENSILS:
        raise StoreError(f"A tray holds 1 to {tray.MAX_UTENSILS} utensil types.")
    clean = []
    for i, u in enumerate(us):
        if not isinstance(u, dict):
            raise StoreError("Each utensil must be an object.")
        uid = u.get("id") if isinstance(u.get("id"), str) and re.fullmatch(r"[a-z0-9]{1,12}", u["id"]) else f"u{i + 1}"
        kind = u.get("kind") if u.get("kind") in tray.KINDS else "custom"
        count = u.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 99:
            raise StoreError("Utensil count must be a whole number from 0 to 99.")
        clean.append({"id": uid, "name": _clean_name(u.get("name") or "Utensil", "Utensil name", 40), "kind": kind,
                      "preset": str(u.get("preset") or "")[:30], "len": _num(u.get("len"), "Utensil length", 20, 600),
                      "handle": _num(u.get("handle"), "Handle width", 1, 120),
                      "head": _num(u.get("head") or 0, "Head width", 0, 150), "count": count, "out": bool(u.get("out"))})
    margin = spec.get("margin", "Normal")
    if margin not in tray.MARGINS:
        raise StoreError("Margin must be Snug, Normal or Easy.")
    if len([u for u in clean if not u["out"]]) < 2:
        raise StoreError("A tray needs at least two utensil types in it. For a single utensil, make a plain bin.")
    return {"utensils": clean, "margin": margin, "round": bool(spec.get("round", True)), "halves": bool(spec.get("halves"))}


def _clean_bin(b, bid: str, pitch=None) -> dict:
    if not isinstance(b, dict):
        raise StoreError("Each bin must be an object.")
    out = {**BIN_DEFAULTS, **{k: v for k, v in b.items() if k in BIN_DEFAULTS}}
    out["name"] = _clean_name(out["name"], "Bin name", 40)
    if out["kind"] not in ("bin", "tray"):
        raise StoreError("Bin kind must be bin or tray.")
    if out["kind"] == "tray":
        out["tray"] = _clean_tray(out["tray"])
        out["w"], out["d"] = tray.size_of(out["tray"], pitch or (42.0, 42.0))  # footprint follows from the utensils
        out["divx"] = out["divy"] = 1
    else:
        out["tray"] = None
    for k, (lo, hi) in _BIN_RANGES.items():
        v = out[k]
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise StoreError(f"Bin {k} must be a whole number from {lo} to {hi}.")
    for k in ("label_tab", "scoop", "lip", "magnets", "screws", "half_grid"):
        out[k] = bool(out[k])
    if out["tab_style"] not in TAB_STYLES:
        raise StoreError(f"Tab style must be one of {', '.join(TAB_STYLES)}.")
    for k in ("x", "y"):
        if out[k] is not None and (isinstance(out[k], bool) or not isinstance(out[k], int) or out[k] < 0):
            raise StoreError("Bin position must be a whole number of squares.")
    out["filament"] = _clean_filament_ref(out["filament"])
    out["split"] = _clean_split(out["split"], out)
    return {"id": bid, **out}


def _clean_split(sp, b: dict):
    """Split settings, or None. Piece sizes that no longer add up (the bin was resized) go back to automatic."""
    if sp is None or b["kind"] == "tray":
        return None
    if not isinstance(sp, dict):
        raise StoreError("Bin split must be an object.")
    joint = sp.get("joint", "clips")
    if joint not in split.JOINTS:
        raise StoreError(f"Joint must be one of {', '.join(split.JOINTS)}.")
    out = {"on": bool(sp.get("on")), "joint": joint}
    for axis, n in (("x", b["w"]), ("y", b["d"])):
        try:
            out[axis] = split.clean_runs(sp.get(axis), n)
        except ValueError:
            out[axis] = None
    return out


PLACEMENT_KEY = re.compile(r"([a-z0-9]{1,12})-([1-9][0-9]?)")
ROTATIONS = (0, 90, 180, 270)  # degrees clockwise seen from above; the label tab (back wall) faces back, right, front, left


def _rot(v) -> int:
    """A placement's turn: 0/90/180/270, or true/false from drawers saved before bins could face four ways."""
    if v is True:
        return 90
    return v if not isinstance(v, bool) and v in ROTATIONS else 0


def _clean_placements(items, bins: list[dict], strict: bool) -> list[dict]:
    """[{key: "<bin id>-<copy n>", bin, x, y, rot}]. x/y are whole squares from the back-left of the grid, rot in degrees;
    a little outside the grid is allowed (the editor shows it as a problem rather than refusing it)."""
    if not isinstance(items, list) or len(items) > 400:
        raise StoreError("placements must be a list (at most 400).")
    qty = {b["id"]: b["qty"] for b in bins}
    out, seen = [], set()
    for p in items:
        if not isinstance(p, dict):
            raise StoreError("Each placement must be an object.")
        m = PLACEMENT_KEY.fullmatch(str(p.get("key", "")))
        ok = m and m.group(1) == p.get("bin") and m.group(1) in qty and int(m.group(2)) <= qty[m.group(1)] and p["key"] not in seen
        if not ok:
            if strict and not (m and m.group(1) == p.get("bin")):
                raise StoreError("Placement keys look like b1-2 (bin id, copy number).")
            continue  # bin deleted or quantity lowered: that copy goes back to the shelf
        for k in ("x", "y"):
            v = p.get(k)
            if isinstance(v, bool) or not isinstance(v, int) or not -25 <= v <= 45:
                raise StoreError("Placement position must be whole squares near the grid.")
        seen.add(p["key"])
        out.append({"key": p["key"], "bin": p["bin"], "x": p["x"], "y": p["y"], "rot": _rot(p.get("rot"))})
    return out


def new_project(pid: str, name: str, tag: str | None = None) -> dict:
    now = _now()
    return {
        "schema": SCHEMA,
        "id": pid,
        "name": name,
        "tag": tag or name,
        "created": now,
        "updated": now,
        "drawer": {"width_mm": None, "depth_mm": None, "height_mm": None, "input": {"w": "", "d": "", "h": ""}},
        "baseplate": {"magnets": True, "style": "standard"},
        "material": "PLA",
        "bins": [],
        "placements": [],  # placed copies of bins on the drawer grid (Layout editor)
        "label_tabs": None,  # label tabs for this drawer's new bins; None = Settings → New bins
        "printed": {},  # part key -> {n, hash, at}: how many the user ticked as printed, and of which version
        "opened": {},   # part key -> {hash, at}: last sent to PrusaSlicer
    }


class ProjectStore:
    pitch_for = None  # set by the server: drawer doc -> square size [x, y] (trays size themselves in squares)

    def _pitch(self, doc: dict):
        try:
            return tuple(self.pitch_for(doc)) if self.pitch_for else None
        except Exception:
            return None

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---- paths ----
    def _dir(self, pid: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", pid or ""):
            raise StoreError("No such drawer.", 404)
        return self.root / pid

    def _read(self, pid: str) -> dict:
        f = self._dir(pid) / PROJECT_FILE
        if not f.is_file():
            raise StoreError("No such drawer.", 404)
        return json.loads(f.read_text(encoding="utf-8"))

    def _write(self, doc: dict) -> None:
        d = self._dir(doc["id"])
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / (PROJECT_FILE + ".tmp")
        tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / PROJECT_FILE)  # atomic: never leaves a half-written file

    # ---- API ----
    def list(self) -> list[dict]:
        out = []
        for d in self.root.iterdir():
            if d.is_dir() and not d.name.startswith(".") and (d / PROJECT_FILE).is_file():
                try:
                    out.append(json.loads((d / PROJECT_FILE).read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue  # skip a damaged file rather than break the whole list
        return sorted(out, key=lambda p: p.get("updated", ""), reverse=True)

    def get(self, pid: str) -> dict:
        return self._read(pid)

    def create(self, name, tag=None) -> dict:
        name = _clean_name(name, "Name")
        tag = _clean_name(tag, "Label", 40) if tag else None
        with self._lock:
            base = slugify(name)
            pid, n = base, 2
            while (self.root / pid).exists():
                pid, n = f"{base}-{n}", n + 1
            doc = new_project(pid, name, tag)
            self._write(doc)
            return doc

    def share(self, pid: str) -> dict:
        """A drawer as a file to give someone: its design only (no print ticks, dates or revision)."""
        doc = self.get(pid)
        from . import __version__
        return {"format": SHARE_FORMAT, "version": 1, "app": __version__,
                "drawer": {k: doc[k] for k in EDITABLE if k in doc}}

    def import_shared(self, data) -> dict:
        """Adds a drawer from a shared file as a new drawer (a new id if the name is taken). Everything in it is
        checked the same way as an edit from the app."""
        if not isinstance(data, dict) or data.get("format") != SHARE_FORMAT or not isinstance(data.get("drawer"), dict):
            raise StoreError("That isn't a SectorTray drawer file.")
        if not isinstance(data.get("version"), int) or data["version"] > 1:
            raise StoreError("That drawer file is from a newer SectorTray. Update SectorTray to open it.")
        d = data["drawer"]
        doc = self.create(d.get("name") or "Shared drawer", d.get("tag"))
        try:
            return self.update(doc["id"], {k: d[k] for k in EDITABLE if k in d and k not in ("name", "tag")})
        except StoreError:
            shutil.rmtree(self._dir(doc["id"]), ignore_errors=True)  # don't leave a half-made drawer behind
            raise

    def update(self, pid: str, changes: dict) -> dict:
        """Applies a page's changes. `rev` (optional) is the revision the page loaded: a save from a page that
        missed someone else's save (another tab or window) is refused instead of silently undoing it."""
        if not isinstance(changes, dict):
            raise StoreError("Expected an object of changes.")
        changes = dict(changes)
        base = changes.pop("rev", None)
        unknown = set(changes) - set(EDITABLE)
        if unknown:
            raise StoreError(f"Can't change: {', '.join(sorted(unknown))}.")
        with self._lock:
            doc = self._read(pid)
            if base is not None and base != doc.get("rev", 0):
                raise StoreError("This drawer was changed in another window or tab, so this change wasn't saved. "
                                 "Reload to see the latest version.", 409)
            if "name" in changes:
                doc["name"] = _clean_name(changes["name"], "Name")
            if "tag" in changes:
                doc["tag"] = _clean_name(changes["tag"], "Label", 40)
            if "drawer" in changes:
                ch = changes["drawer"] or {}
                merged = {**doc["drawer"], **ch}
                if isinstance(ch, dict) and isinstance(ch.get("input"), dict):  # one typed size at a time: keep the others
                    merged["input"] = {**(doc["drawer"].get("input") or {}), **ch["input"]}
                doc["drawer"] = _clean_drawer(merged)
            if "baseplate" in changes:
                doc["baseplate"] = _clean_baseplate({**doc["baseplate"], **(changes["baseplate"] or {})})
            if "label_tabs" in changes:
                v = changes["label_tabs"]
                if v is not None and not isinstance(v, bool):
                    raise StoreError("label_tabs must be true, false or null.")
                doc["label_tabs"] = v
            if ("drawer" in changes or "baseplate" in changes) and "bins" not in changes and any(b.get("kind") == "tray" for b in doc["bins"]):
                doc["bins"] = [_clean_bin(b, b["id"], self._pitch(doc)) for b in doc["bins"]]
            if "material" in changes:
                if changes["material"] not in MATERIALS:
                    raise StoreError(f"Material must be one of {', '.join(MATERIALS)}.")
                doc["material"] = changes["material"]
            if "bins" in changes:
                if not isinstance(changes["bins"], list) or len(changes["bins"]) > 200:
                    raise StoreError("bins must be a list (at most 200).")
                # Valid ids the page already has are kept (first use wins); new/duplicate bins get fresh ids.
                ids, taken = [], set()
                for b in changes["bins"]:
                    bid = b.get("id") if isinstance(b, dict) else None
                    ok = isinstance(bid, str) and re.fullmatch(r"[a-z0-9]{1,12}", bid) and bid not in taken
                    ids.append(bid if ok else None)
                    if ok:
                        taken.add(bid)
                n = 1
                for i, bid in enumerate(ids):
                    if bid is None:
                        while f"b{n}" in taken:
                            n += 1
                        ids[i] = f"b{n}"
                        taken.add(ids[i])
                doc["bins"] = [_clean_bin(b, bid, self._pitch(doc)) for b, bid in zip(changes["bins"], ids)]
            if "placements" in changes or "bins" in changes:
                # validates new placements, and drops copies whose bin is gone or whose number exceeds its qty
                doc["placements"] = _clean_placements(changes.get("placements", doc.get("placements", [])), doc["bins"],
                                                      strict="placements" in changes)
            if "bins" in changes:  # forget tracking of deleted bins, so a new bin that reuses the id starts clean
                ids = {b["id"] for b in doc["bins"]}
                for f in TRACK_FIELDS:
                    if doc.get(f):
                        doc[f] = {k: v for k, v in doc[f].items() if k.startswith("plate:") or k.split(":")[0] in ids}
            doc["updated"] = _now()
            doc["rev"] = doc.get("rev", 0) + 1
            self._write(doc)
            return doc

    def track(self, pid: str, field: str, entries: dict | None) -> None:
        """Print tracking: sets (stamped with the time) or, for None, clears entries of `field`; entries=None clears all.
        Not a design edit, so `updated` stays as it was."""
        assert field in TRACK_FIELDS
        with self._lock:
            doc = self._read(pid)
            cur = {} if entries is None else dict(doc.get(field) or {})
            for k, v in (entries or {}).items():
                if v is None:
                    cur.pop(k, None)
                else:
                    cur[k] = {**v, "at": _now()}
            doc[field] = cur
            self._write(doc)

    def delete(self, pid: str) -> str:
        """Moves the drawer's folder to .trash and returns where it went."""
        with self._lock:
            src = self._dir(pid)
            if not (src / PROJECT_FILE).is_file():
                raise StoreError("No such drawer.", 404)
            trash = self.root / TRASH
            trash.mkdir(exist_ok=True)
            dest = trash / f"{pid}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            shutil.move(str(src), str(dest))
            return str(dest)
