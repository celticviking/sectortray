"""Build the portable Windows bundle: a folder anyone can unzip and run, no Python or installs needed.

    python tools/build_dist.py            → out/dist/SectorTray/, SectorTray-<version>-win64.zip and SectorTray-<version>-setup.exe
    python tools/build_dist.py --no-zip   → folder and installer, no zip (--no-installer: folder only, quicker while testing)

Needs: vendor/ in place (python tools/setup.py) and the .NET SDK for the host (dotnet build).
The installer needs Inno Setup 6 (tools/installer.iss); without it the build skips the installer and says so.
Downloads (once, cached in vendor/_downloads, SHA-256 checked): the Python embeddable package.

Bundle layout (host/Server.cs looks for exactly this):
    SectorTray.exe (+ WebView2 DLLs, icon)
    python/        embeddable CPython; python3XX._pth gains ..\\app so `python -m sectortray` finds the app
    app/           sectortray/, web/, vendor/ (OpenSCAD without examples; libraries without docs/images/.git)
    LICENSES/      SectorTray's license, third-party licenses + NOTICE.txt
    README.txt
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sectortray import __version__  # noqa: E402

DIST = ROOT / "out" / "dist"
BUNDLE = DIST / "SectorTray"
DOWNLOADS = ROOT / "vendor" / "_downloads"
HOST_OUT = ROOT / "out" / "host-build"  # its own folder, so a running host/bin/…/SectorTray.exe doesn't block the build
ISCC_CANDIDATES = [Path(os.environ.get(v, "")) / sub / "ISCC.exe" for v, sub in
                   (("LOCALAPPDATA", "Programs/Inno Setup 6"), ("ProgramFiles(x86)", "Inno Setup 6"), ("ProgramFiles", "Inno Setup 6"))]
WEBVIEW2_LICENSE = Path.home() / ".nuget" / "packages" / "microsoft.web.webview2" / "1.0.4258.31" / "LICENSE.txt"

# Pinned Python embeddable package. Update all three together (hash from python.org's release API).
PY_VERSION = "3.14.4"
PY_ZIP = f"python-{PY_VERSION}-embed-amd64.zip"
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/{PY_ZIP}"
PY_SHA256 = "cda80a9b1e75c0f1b4f9872ca1b417f0d19bce32facc811aea9180e70fad5fb9"

# What goes into app/vendor/<lib>: everything except these (names at any depth).
LIB_SKIP = {".git", ".github", "docs", "images", "Images", "Renders", "tests", "demos", "combined", "scripts",
            "mkdocs.yml", "pyproject.toml", "canonicalize.py", "editor.toml", "__pycache__"}
OPENSCAD_SKIP = {"examples"}

README = (ROOT / "tools" / "bundle-README.txt").read_text(encoding="utf-8")  # {version} is filled in


NOTICE = """SectorTray {version}
=====================================================
SectorTray, copyright (C) 2026 celticviking. Free software: you can redistribute it and/or modify it
under the terms of the GNU General Public License as published by the Free Software Foundation,
either version 3 of the License, or (at your option) any later version. See SectorTray-LICENSE.txt.
Source: https://github.com/celticviking/sectortray

Gridfinity: the modular storage system by Zack Freedman (MIT), https://gridfinity.xyz/

Third-party components bundled with SectorTray
=====================================================

Python {py}                  PSF License                  Python-LICENSE.txt
  https://www.python.org/  (embeddable package, unmodified apart from python*._pth)

OpenSCAD {openscad}       GPL-2.0-or-later (distributed under GPL-3.0)   GPL-3.0.txt
  Unmodified official Windows snapshot build (the examples folder is left out).
  Exact source: https://github.com/openscad/openscad/tree/{openscad_git}  (git {openscad_git})
  Build: https://files.openscad.org/snapshots/OpenSCAD-{openscad}-x86-64.zip
  Its complete source (with the exact versions of the libraries it pins) is attached to each SectorTray
  release as OpenSCAD-{openscad}-source.zip. OpenSCAD includes further open-source libraries (Qt and
  others under LGPL-3.0, see LGPL-3.0.txt; CGAL, Manifold, Boost and more) under their own licenses.

Gridfinity Rebuilt in OpenSCAD (kennetek) {kennetek}   MIT   gridfinity-rebuilt-openscad-LICENSE.txt
  https://github.com/kennetek/gridfinity-rebuilt-openscad
  The bundle has the original library and a copy SectorTray modifies when it runs (app/vendor/_patched)
  so grid squares can be other sizes than 42 mm ("Fit to drawer").

Gridfinity Extended OpenSCAD (ostat) {extended}   GPL-3.0   GPL-3.0.txt
  https://github.com/ostat/gridfinity_extended_openscad  (unmodified; docs, images and renders left out)

three.js {three}            MIT                          three.js-LICENSE.txt
  https://github.com/mrdoob/three.js

Microsoft Edge WebView2 SDK 1.0.4258.31   Microsoft license   WebView2-LICENSE.txt

Fonts (Archivo, Barlow Semi Condensed, Chivo, Chivo Mono, Red Hat Mono)
  SIL Open Font License 1.1     app/web/fonts/licenses/
"""


def openscad_git(folder: Path) -> str:
    """The commit the bundled OpenSCAD snapshot was built from ("OpenSCAD Version: 2026.10.15 (git 5a864f17)")."""
    out = subprocess.run([str(folder / "openscad.com"), "--info"], capture_output=True, text=True, timeout=60).stdout
    return out.split("(git ", 1)[1].split(")", 1)[0] if "(git " in out else "unknown"


def run(cmd, **kw):
    print("  $", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, **kw)


def copytree(src: Path, dst: Path, skip=frozenset()):
    shutil.copytree(src, dst, ignore=lambda d, names: [n for n in names if n in skip or n == "__pycache__"])


def git_version(path: Path) -> str:
    r = subprocess.run(["git", "-C", str(path), "log", "-1", "--format=%h %cs"], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        sys.exit(f"Can't read the git version of {path}")
    return r.stdout.strip()


def fetch_python() -> Path:
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    zip_path = DOWNLOADS / PY_ZIP
    if not zip_path.exists():
        print(f"Python: downloading {PY_URL}")
        part = zip_path.with_suffix(".part")
        urllib.request.urlretrieve(PY_URL, part)
        part.replace(zip_path)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if digest != PY_SHA256:
        zip_path.unlink()
        sys.exit(f"Python: checksum mismatch ({digest}); download removed, try again.")
    print("Python: checksum OK")
    return zip_path


def build_host():
    print("Host: dotnet build")
    run(["dotnet", "build", str(ROOT / "host"), "-c", "Release", "-nologo", "-v", "q", "-o", str(HOST_OUT)])
    if not (HOST_OUT / "SectorTray.exe").exists():
        sys.exit("Host build didn't produce SectorTray.exe")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-zip", action="store_true", help="build the folder only")
    ap.add_argument("--no-host-build", action="store_true", help="reuse host/bin/Release (e.g. while SectorTray.exe is open)")
    ap.add_argument("--no-installer", action="store_true", help="skip the Inno Setup installer")
    args = ap.parse_args()

    openscad = ROOT / "vendor" / "openscad"
    if not (openscad / "openscad.com").exists():
        sys.exit("OpenSCAD missing: run python tools/setup.py first")
    kennetek_dir = ROOT / "vendor" / "gridfinity-rebuilt-openscad"
    extended_dir = ROOT / "vendor" / "gridfinity_extended_openscad"
    versions = {
        "version": __version__, "py": PY_VERSION,
        "openscad": (ROOT / "tools" / "setup.py").read_text(encoding="utf-8").split('OPENSCAD_VERSION = "')[1].split('"')[0],
        "kennetek": git_version(kennetek_dir), "extended": git_version(extended_dir),
        "three": (ROOT / "web" / "vendor" / "three" / "VERSION").read_text(encoding="utf-8").split()[1],
        "openscad_git": openscad_git(openscad),
    }

    py_zip = fetch_python()
    if args.no_host_build and (HOST_OUT / "SectorTray.exe").exists():
        print("Host: reusing the existing build")
    else:
        build_host()

    print(f"Bundle: {BUNDLE}")
    shutil.rmtree(BUNDLE, ignore_errors=True)
    BUNDLE.mkdir(parents=True)

    # Host exe and its DLLs (no debug symbols or unused loader builds)
    for f in HOST_OUT.iterdir():
        if f.is_file() and f.suffix != ".pdb":
            shutil.copy2(f, BUNDLE / f.name)
    copytree(HOST_OUT / "runtimes" / "win-x64", BUNDLE / "runtimes" / "win-x64")

    # Python, with the app folder on its path
    py = BUNDLE / "python"
    with zipfile.ZipFile(py_zip) as z:
        z.extractall(py)
    pth = next(py.glob("python3*._pth"))
    lines = pth.read_text(encoding="utf-8").splitlines()
    pth.write_text("\n".join(lines[:2] + ["..\\app"] + lines[2:]) + "\n", encoding="utf-8")

    # The app itself
    app = BUNDLE / "app"
    copytree(ROOT / "sectortray", app / "sectortray")
    copytree(ROOT / "web", app / "web")
    copytree(openscad, app / "vendor" / "openscad", OPENSCAD_SKIP)
    for lib_dir, ver in ((kennetek_dir, versions["kennetek"]), (extended_dir, versions["extended"])):
        dst = app / "vendor" / lib_dir.name
        copytree(lib_dir, dst, LIB_SKIP)
        (dst / ".sectortray-version").write_text(ver + "\n", encoding="utf-8")  # no git in the bundle; About reads this
    # kennetek patched for squares other than 42 mm ("Fit to drawer"); its stamp follows the copied files' mtimes
    from sectortray import kennetek
    copytree(kennetek.ensure(), app / "vendor" / "_patched" / "gridfinity-rebuilt-openscad")
    # -d: the .pyc files record "sectortray/…", not this PC's folder
    run([str(py / "python.exe"), "-m", "compileall", "-q", "-d", "sectortray", str(app / "sectortray")])

    # Licenses and readme
    lic = BUNDLE / "LICENSES"
    lic.mkdir()
    shutil.copy2(ROOT / "LICENSE", lic / "SectorTray-LICENSE.txt")
    shutil.copy2(py / "LICENSE.txt", lic / "Python-LICENSE.txt")
    shutil.copy2(extended_dir / "LICENSE", lic / "GPL-3.0.txt")
    shutil.copy2(ROOT / "tools" / "licenses" / "LGPL-3.0.txt", lic / "LGPL-3.0.txt")  # Qt and other parts of OpenSCAD
    shutil.copy2(kennetek_dir / "LICENSE", lic / "gridfinity-rebuilt-openscad-LICENSE.txt")
    shutil.copy2(ROOT / "web" / "vendor" / "three" / "LICENSE", lic / "three.js-LICENSE.txt")
    if not WEBVIEW2_LICENSE.exists():  # its DLLs ship in the bundle, so its license must too
        sys.exit(f"WebView2 license not found at {WEBVIEW2_LICENSE}; build the host once (dotnet build host) first")
    shutil.copy2(WEBVIEW2_LICENSE, lic / "WebView2-LICENSE.txt")
    (lic / "NOTICE.txt").write_text(NOTICE.format(**versions), encoding="utf-8")
    (BUNDLE / "README.txt").write_text(README.format(**versions), encoding="utf-8")

    size = sum(f.stat().st_size for f in BUNDLE.rglob("*") if f.is_file())
    print(f"Bundle: {size / 1e6:.0f} MB")

    if not args.no_zip:
        out = DIST / f"SectorTray-{__version__}-win64.zip"
        out.unlink(missing_ok=True)
        print(f"Zip: {out.name}")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for f in sorted(BUNDLE.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(DIST))
        print(f"Zip: {out.stat().st_size / 1e6:.0f} MB")

    if not args.no_installer:
        build_installer()


def build_installer():
    iscc = next((c for c in ISCC_CANDIDATES if c.is_file()), None)
    if not iscc:
        print("Installer: Inno Setup 6 not found, skipped (https://jrsoftware.org/isinfo.php)")
        return
    print("Installer: Inno Setup")
    run([str(iscc), "/Q", f"/DAppVersion={__version__}", f"/DSourceDir={BUNDLE}", f"/DOutputDir={DIST}",
         str(ROOT / "tools" / "installer.iss")])
    out = DIST / f"SectorTray-{__version__}-setup.exe"
    print(f"Installer: {out.name}, {out.stat().st_size / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
