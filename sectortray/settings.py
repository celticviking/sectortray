"""App-wide settings in <data>/settings.json: printer bed, fit, filaments, which slicer and where it is.

Display preferences (unit, fraction precision, skin, theme) stay in the browser.
"""
import json
import os
import re
import subprocess
import threading
from functools import lru_cache
from pathlib import Path

from .layout import Bed, DEFAULT_CLEARANCE, DEFAULT_HEADROOM
from .store import StoreError

DEFAULT_JOINT = 0.15  # mm per side for split-bin clips and bow-tie keys

FILE = "settings.json"
PRINTER_DEFAULT = {"name": "Prusa CORE One", "bed_w": 250.0, "bed_d": 220.0, "max_h": 270.0}
FILAMENTS_DEFAULT = [
    {"id": "f1", "name": "White", "material": "PLA", "color": "#eceae4"},
    {"id": "f2", "name": "Black", "material": "PLA", "color": "#2b2d30"},
    {"id": "f3", "name": "Gray", "material": "PETG", "color": "#8a8f94"},
    {"id": "f4", "name": "Orange", "material": "PETG", "color": "#f26b1d"},
]
PROGRAM_DIRS = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
                Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))]
# Slicers SectorTray can open parts in: where their installers put them (globs under Program Files; the newest
# match wins). Only PrusaSlicer has been tried for real; it's also the only one with several beds in one project.
SLICERS = {
    "prusaslicer": {"name": "PrusaSlicer", "exe": ["Prusa3D/PrusaSlicer/prusa-slicer.exe"], "tested": True},
    "bambu": {"name": "Bambu Studio", "exe": ["Bambu Studio/bambu-studio.exe"], "tested": False},
    "orca": {"name": "OrcaSlicer", "exe": ["OrcaSlicer/orca-slicer.exe"], "tested": False},
    "cura": {"name": "UltiMaker Cura", "exe": ["UltiMaker Cura */UltiMaker-Cura.exe", "Ultimaker Cura */Ultimaker-Cura.exe"],
             "tested": False},
}


def _version_key(p: Path):
    return [int(n) for n in re.findall(r"\d+", str(p.parent.name))]


def find_slicer(kind: str) -> Path | None:
    for base in PROGRAM_DIRS:
        for pattern in SLICERS[kind]["exe"]:
            hits = sorted(base.glob(pattern), key=_version_key, reverse=True)
            if hits:
                return hits[0]
    return None


def defaults() -> dict:
    return {
        "schema": 1,
        "printer": dict(PRINTER_DEFAULT),
        "fit": {"clearance_mm": DEFAULT_CLEARANCE, "headroom_mm": DEFAULT_HEADROOM, "joint_mm": DEFAULT_JOINT},
        "filaments": [dict(f) for f in FILAMENTS_DEFAULT],
        "slicer_kind": "prusaslicer",
        "slicer_path": None,  # None = found automatically
        # defaults for bins made from now on (a drawer can override label tabs: project.label_tabs);
        # auto_split = bins too big for the bed start out printed in pieces
        "new_bins": {"label_tab": False, "lip": False, "scoop": True, "auto_split": True},
    }


def _num(v, field, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise StoreError(f"{field} must be between {lo:g} and {hi:g} mm.")
    return round(float(v), 3)


def _clean_filaments(items) -> list[dict]:
    if not isinstance(items, list) or not 1 <= len(items) <= 40:
        raise StoreError("Keep between 1 and 40 filaments.")
    out, taken = [], set()
    for f in items:
        if not isinstance(f, dict):
            raise StoreError("Each filament must be an object.")
        name = " ".join(str(f.get("name", "")).split())[:40]
        if not name:
            raise StoreError("Every filament needs a name.")
        if f.get("material") not in ("PLA", "PETG"):
            raise StoreError("Filament material must be PLA or PETG.")
        color = str(f.get("color", "")).lower()
        if not re.fullmatch(r"#[0-9a-f]{6}", color):
            raise StoreError("Filament color must look like #2f4f8f.")
        fid = f.get("id")
        if not (isinstance(fid, str) and re.fullmatch(r"[a-z0-9]{1,12}", fid)) or fid in taken:
            fid = next(f"f{n}" for n in range(1, 1000) if f"f{n}" not in taken and
                       f"f{n}" not in {x.get("id") for x in items if isinstance(x, dict)})
        taken.add(fid)
        out.append({"id": fid, "name": name, "material": f["material"], "color": color})
    return out


class Settings:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / FILE
        self._lock = threading.Lock()

    def get(self) -> dict:
        s = defaults()
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return s
        for k in ("printer", "fit", "new_bins"):
            if isinstance(saved.get(k), dict):
                s[k].update({kk: v for kk, v in saved[k].items() if kk in s[k]})
        if isinstance(saved.get("filaments"), list) and saved["filaments"]:
            s["filaments"] = saved["filaments"]
        if saved.get("slicer_kind") in SLICERS:
            s["slicer_kind"] = saved["slicer_kind"]
        s["slicer_path"] = saved.get("slicer_path")
        return s

    def update(self, changes: dict) -> dict:
        if not isinstance(changes, dict):
            raise StoreError("Expected an object of changes.")
        with self._lock:
            s = self.get()
            if "printer" in changes:
                p = {**s["printer"], **(changes["printer"] or {})}
                s["printer"] = {
                    "name": (" ".join(str(p.get("name", "")).split())[:40] or PRINTER_DEFAULT["name"]),
                    "bed_w": _num(p["bed_w"], "Bed width", 60, 1000),
                    "bed_d": _num(p["bed_d"], "Bed depth", 60, 1000),
                    "max_h": _num(p["max_h"], "Max height", 20, 1000),
                }
            if "fit" in changes:
                f = {**s["fit"], **(changes["fit"] or {})}
                s["fit"] = {"clearance_mm": _num(f["clearance_mm"], "Fit clearance", 0, 10),
                            "headroom_mm": _num(f["headroom_mm"], "Headroom", 0, 50),
                            "joint_mm": _num(f["joint_mm"], "Joint clearance", 0, 0.6)}
            if "filaments" in changes:
                s["filaments"] = _clean_filaments(changes["filaments"])
            if "new_bins" in changes:
                nb = {**s["new_bins"], **(changes["new_bins"] or {})}
                s["new_bins"] = {k: bool(nb[k]) for k in ("label_tab", "lip", "scoop", "auto_split")}
            if "slicer_kind" in changes:
                if changes["slicer_kind"] not in SLICERS:
                    raise StoreError("Unknown slicer.")
                if changes["slicer_kind"] != s["slicer_kind"]:
                    s["slicer_path"] = None  # a typed location belongs to the slicer it was typed for
                s["slicer_kind"] = changes["slicer_kind"]
            if "slicer_path" in changes:
                v = changes["slicer_path"]
                if v not in (None, "") and not (isinstance(v, str) and v.lower().endswith(".exe") and len(v) < 400):
                    raise StoreError(f"{SLICERS[s['slicer_kind']]['name']} location must point to its .exe file.")
                if v and not Path(v).is_file():
                    raise StoreError(f"There's no file at {v}. Check the location, or leave it empty to find "
                                     f"{SLICERS[s['slicer_kind']]['name']} automatically.")
                s["slicer_path"] = v or None
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
            return s

    # ---- derived ----
    def bed(self) -> Bed:
        p = self.get()["printer"]
        return Bed(p["bed_w"], p["bed_d"], p["name"])

    def fit(self) -> tuple[float, float]:
        f = self.get()["fit"]
        return f["clearance_mm"], f["headroom_mm"]

    def printer_confirmed(self) -> bool:
        """False until settings have been saved once: a fresh install asks which printer it's for."""
        try:
            return "printer" in json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False

    def joint_gap(self) -> float:
        """Clearance per side for split-bin joints (clips, bow-tie keys)."""
        return self.get()["fit"]["joint_mm"]

    def slicer(self) -> Path | None:
        s = self.get()
        if s["slicer_path"] and Path(s["slicer_path"]).is_file():
            return Path(s["slicer_path"])
        return find_slicer(s["slicer_kind"])

    def slicer_name(self) -> str:
        return SLICERS[self.get()["slicer_kind"]]["name"]

    def slicer_info(self) -> dict:
        kind, exe = self.get()["slicer_kind"], self.slicer()
        return {"kind": kind, "name": SLICERS[kind]["name"], "tested": SLICERS[kind]["tested"],
                "multibed": kind == "prusaslicer",  # PrusaSlicer 2.9+: parts for several beds in one project
                "path": str(exe) if exe else None, "found": bool(exe),
                "version": slicer_version(str(exe), kind) if exe else None,
                "all": [{"kind": k, "name": v["name"], "found": bool(find_slicer(k)), "tested": v["tested"]}
                        for k, v in SLICERS.items()]}


def exe_version(exe: str) -> str | None:
    """The version stamped in a Windows .exe (File Properties → Details), e.g. '2.3.0.70'."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    ver = ctypes.WinDLL("version")
    size = ver.GetFileVersionInfoSizeW(exe, None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(exe, 0, size, buf):
        return None
    ptr, n = ctypes.c_void_p(), wintypes.UINT()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(n)) or n.value < 52:
        return None
    ms, ls = ctypes.c_uint32.from_address(ptr.value + 8).value, ctypes.c_uint32.from_address(ptr.value + 12).value
    parts = [ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF]
    while len(parts) > 3 and parts[-1] == 0:
        parts.pop()
    return ".".join(map(str, parts))


@lru_cache(maxsize=8)
def slicer_version(exe: str, kind: str = "prusaslicer") -> str | None:
    """PrusaSlicer: '2.9.6' from the console build's --help banner (no window opens). Others: the exe's version."""
    if kind != "prusaslicer":
        try:
            return exe_version(exe)
        except OSError:
            return None
    console = Path(exe).with_name("prusa-slicer-console.exe")
    try:
        r = subprocess.run([str(console if console.is_file() else exe), "--help"], capture_output=True,
                           text=True, timeout=20, creationflags=0x08000000 if os.name == "nt" else 0)
        m = re.search(r"PrusaSlicer-(\d+(?:\.\d+)+)", r.stdout + r.stderr)
        return m.group(1) if m else None
    except (OSError, subprocess.TimeoutExpired):
        return None
