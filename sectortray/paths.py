"""Where things live: the app's own files and the user's saved drawers."""
import ctypes
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEV = (ROOT / ".git").exists()  # running from a git checkout, not the installed/portable app
WEB = ROOT / "web"
VENDOR = ROOT / "vendor"
OPENSCAD = VENDOR / "openscad" / "openscad.com"


def _windows_documents() -> Path | None:
    """The real Documents folder, even if it has been moved (e.g. to OneDrive or another drive)."""
    try:
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

        # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        fid = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(out)) != 0:
            return None
        try:
            return Path(out.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)
    except Exception:
        return None


def default_data_dir() -> Path:
    """Saved drawers go in Documents/SectorTray, or the folder picked in Settings (datafolder.py);
    SECTORTRAY_DATA overrides both."""
    env = os.environ.get("SECTORTRAY_DATA")
    if env:
        return Path(env)
    from .datafolder import chosen
    picked = chosen()
    if picked:
        return picked
    docs = _windows_documents() if sys.platform == "win32" else None
    return (docs or Path.home() / "Documents") / "SectorTray"
