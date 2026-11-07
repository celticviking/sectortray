"""One-time setup for a fresh checkout: fetch the pinned OpenSCAD build and the library submodules.

Usage:  python tools/setup.py
Safe to re-run; skips anything already in place.
"""
import hashlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"

# Pinned OpenSCAD development snapshot (Manifold backend). Update all three together.
OPENSCAD_VERSION = "2026.10.15"
OPENSCAD_ZIP = f"OpenSCAD-{OPENSCAD_VERSION}-x86-64.zip"
OPENSCAD_SHA256 = "e63f70423f54e946037684ae2d7a373ec015b68dc4d4a6797d0a395fcac26b80"
OPENSCAD_URL = f"https://files.openscad.org/snapshots/{OPENSCAD_ZIP}"


def ensure_submodules():
    print("Libraries: git submodule update --init")
    subprocess.run(["git", "-C", str(ROOT), "submodule", "update", "--init"], check=True)


def ensure_openscad():
    target = VENDOR / "openscad"
    if (target / "openscad.com").exists():
        print(f"OpenSCAD: already present in {target}")
        return
    dl = VENDOR / "_downloads"
    dl.mkdir(parents=True, exist_ok=True)
    zip_path = dl / OPENSCAD_ZIP
    if not zip_path.exists():
        print(f"OpenSCAD: downloading {OPENSCAD_URL}")
        urllib.request.urlretrieve(OPENSCAD_URL, zip_path)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if digest != OPENSCAD_SHA256:
        zip_path.unlink()
        sys.exit(f"OpenSCAD: checksum mismatch ({digest}); download removed, try again.")
    print("OpenSCAD: checksum OK, extracting")
    extract = dl / "extract"
    shutil.rmtree(extract, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extract)
    inner = next(extract.iterdir())
    shutil.move(str(inner), str(target))
    shutil.rmtree(extract, ignore_errors=True)
    print(f"OpenSCAD: installed to {target}")


if __name__ == "__main__":
    ensure_submodules()
    ensure_openscad()
    sys.path.insert(0, str(ROOT))
    from sectortray import kennetek
    print(f"Custom square sizes: patched library in {kennetek.ensure()}")
    print("Setup complete. Start SectorTray with SectorTray.cmd")
