"""Where saved drawers live, and moving them somewhere else (Settings → Saved drawers).

The choice is remembered outside the data folder, in %LOCALAPPDATA%/SectorTray/data-location.txt, because
settings.json lives inside the folder being moved. `--data` and SECTORTRAY_DATA still win (developers, tests).
"""
import os
import shutil
from pathlib import Path

from .store import StoreError

FILE = "data-location.txt"


def location_file() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "SectorTray" / FILE


def chosen(loc: Path | None = None) -> Path | None:
    """The folder picked in Settings, if any (and it still looks usable)."""
    try:
        p = Path((loc or location_file()).read_text(encoding="utf-8").strip())
    except OSError:
        return None
    return p if p.is_absolute() else None


def remember(target: Path, loc: Path | None = None) -> None:
    loc = loc or location_file()
    loc.parent.mkdir(parents=True, exist_ok=True)
    tmp = loc.with_suffix(".tmp")
    tmp.write_text(str(target) + "\n", encoding="utf-8")
    os.replace(tmp, loc)


def cloud_synced(p: Path) -> str | None:
    """'OneDrive' when the folder is inside a OneDrive folder (Windows often syncs Documents there)."""
    s = str(p).lower()
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(var)
        if root and (s + "\\").startswith(root.lower().rstrip("\\") + "\\"):
            return "OneDrive"
    return "OneDrive" if "\\onedrive" in s else None


def _is_sectortray(p: Path) -> bool:
    if (p / "settings.json").is_file():
        return True
    return any((d / "project.json").is_file() for d in p.iterdir() if d.is_dir())


def move(server, target: str, loc: Path | None = None) -> dict:
    """Moves everything in the current data folder to `target` (which must be new or empty), or, when `target`
    already is a SectorTray data folder, just switches to it. Then the running server uses it at once."""
    if not getattr(server, "movable", False):
        raise StoreError("This copy of SectorTray was started with a fixed data folder (--data or SECTORTRAY_DATA).")
    if not isinstance(target, str) or not target.strip():
        raise StoreError("Type or pick a folder.")
    dst = Path(os.path.expandvars(target.strip().strip('"'))).expanduser()
    if not dst.is_absolute() or not dst.drive:
        raise StoreError("Use a full folder location, like D:\\SectorTray.")
    src = Path(server.store.root).resolve()
    dst = dst.resolve()
    if dst == src:
        raise StoreError("Your drawers are already saved there.")
    if src in dst.parents:
        raise StoreError("Pick a folder outside the current one.")
    if dst in src.parents:
        raise StoreError("Pick a new folder (or an empty one), not one that holds the current folder.")
    if dst.exists() and not dst.is_dir():
        raise StoreError("That's a file, not a folder.")
    mode = "move"
    if dst.exists() and any(dst.iterdir()):
        if not _is_sectortray(dst):
            raise StoreError("That folder already has other things in it. Pick an empty folder, or type a new "
                             "name and SectorTray will make it.")
        mode = "use"  # another SectorTray folder (e.g. moved by hand): switch to it, leave this one as it is
    with server.store._lock, server.settings._lock:
        if mode == "move":
            try:
                dst.mkdir(parents=True, exist_ok=True)
                for entry in list(src.iterdir()):
                    shutil.move(str(entry), str(dst / entry.name))
            except OSError as e:
                raise StoreError(f"Couldn't move everything ({e.strerror or e}). Close any open exported files "
                                 f"and try again; what was already moved is in {dst}.", 500)
            try:
                src.rmdir()  # empty now; leave it if something else put files there
            except OSError:
                pass
        remember(dst, loc)
        server.store.root = dst
        server.settings.path = dst / server.settings.path.name
    return {"data_dir": str(dst), "mode": mode, "cloud": cloud_synced(dst)}
