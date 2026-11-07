"""Gridfinity squares other than 42 mm ("Fit to drawer").

kennetek's library assumes 42 mm squares in a few constants. `ensure()` makes a patched copy of it in
vendor/_patched/ (the submodule itself stays untouched) where the square size comes from `$pitch`, and the
baseplate handles squares that differ across and front-to-back. `$pitch` has to be set in the main file
*before* the library is included (a -D value arrives too late for its constants), so each non-standard size
renders through a two-line wrapper written next to the drawer's cache.

Standard 42 mm renders keep using the original library, so their output and cache hashes never change.
"""
import os
import shutil
import threading
from pathlib import Path

from . import paths

STANDARD = (42.0, 42.0)
PATCH_VERSION = "1"
SOURCE = paths.VENDOR / "gridfinity-rebuilt-openscad"
PATCHED = paths.VENDOR / "_patched" / "gridfinity-rebuilt-openscad"
SCRIPTS = {"bins": "gridfinity-rebuilt-bins.scad", "baseplate": "gridfinity-rebuilt-baseplate.scad"}

# (file, old, new): every edit must match exactly once, so a library update can't be patched silently wrong
EDITS = [
    ("src/core/standard.scad", "GRID_DIMENSIONS_MM = [42, 42];",
     "// SectorTray: square size from $pitch (set before including this library); standard Gridfinity is 42 mm\n"
     "GRID_DIMENSIONS_MM = is_undef($pitch) ? [42, 42] : $pitch;"),
    ("src/core/standard.scad", "BASE_TOP_DIMENSIONS = [41.5, 41.5];",
     "BASE_TOP_DIMENSIONS = GRID_DIMENSIONS_MM - [0.5, 0.5];"),
    ("src/core/gridfinity-baseplate.scad", "BASEPLATE_DIMENSIONS = [42, 42];",
     "BASEPLATE_DIMENSIONS = is_undef($pitch) ? [42, 42] : $pitch;"),
    # the baseplate passed one number for both directions
    ("gridfinity-rebuilt-baseplate.scad",
     "gridfinityBaseplate([gridx, gridy], l_grid, [distancex, distancey], style_plate, hole_options, style_hole, [fitx, fity]);",
     "gridfinityBaseplate([gridx, gridy], GRID_DIMENSIONS_MM, [distancex, distancey], style_plate, hole_options, style_hole, [fitx, fity]);"),
    ("gridfinity-rebuilt-baseplate.scad",
     "grid_size_bases[i] == 0 ? floor(min_size_mm[i]/length) : grid_size_bases[i]];",
     "grid_size_bases[i] == 0 ? floor(min_size_mm[i]/length[i]) : grid_size_bases[i]];"),
    ("gridfinity-rebuilt-baseplate.scad",
     "grid_size_mm = concat(grid_size * length, [baseplate_height_mm]);",
     "grid_size_mm = concat([grid_size.x * length.x, grid_size.y * length.y], [baseplate_height_mm]);"),
    ("gridfinity-rebuilt-baseplate.scad", "pattern_grid(grid_size, [length, length], true, true) {",
     "pattern_grid(grid_size, [length.x, length.y], true, true) {"),
    ("gridfinity-rebuilt-baseplate.scad", "baseplate_cutter([length, length], baseplate_height_mm+TOLLERANCE);",
     "baseplate_cutter([length.x, length.y], baseplate_height_mm+TOLLERANCE);"),
    ("gridfinity-rebuilt-baseplate.scad", "baseplate_cutter([length, length]);", "baseplate_cutter([length.x, length.y]);"),
    # magnet holes 8 mm in from each side, per direction (pattern_circular only works for square cells)
    ("gridfinity-rebuilt-baseplate.scad", """module hole_pattern(){
    pattern_circular(4)
    translate([l_grid/2-d_hole_from_side, l_grid/2-d_hole_from_side, 0]) {
        render();
        children();
    }
}""", """module hole_pattern(){
    for (a = [0:90:270]) {
        i = sign(cos(a + 1)); j = sign(sin(a + 1));
        translate([i * (GRID_DIMENSIONS_MM.x/2-d_hole_from_side), j * (GRID_DIMENSIONS_MM.y/2-d_hole_from_side), 0])
        rotate([0, 0, a]) {
            render();
            children();
        }
    }
}"""),
    ("gridfinity-rebuilt-baseplate.scad", """module profile_skeleton(size=l_grid) {
    l = baseplate_inner_size([size, size]).x;

    offset(r_skel)
    difference() {
        square(l-2*r_skel, center = true);

        hole_pattern()
        offset(MAGNET_HOLE_RADIUS+r_skel+2)
        square([l,l]);
    }
}""", """module profile_skeleton(size=GRID_DIMENSIONS_MM) {
    l = baseplate_inner_size([size.x, size.y]);

    offset(r_skel)
    difference() {
        square([l.x-2*r_skel, l.y-2*r_skel], center = true);

        hole_pattern()
        offset(MAGNET_HOLE_RADIUS+r_skel+2)
        square([max(l.x, l.y), max(l.x, l.y)]);
    }
}"""),
]

_lock = threading.Lock()


def is_standard(pitch) -> bool:
    return pitch is None or (abs(pitch[0] - 42) < 1e-6 and abs(pitch[1] - 42) < 1e-6)


def _stamp() -> str:
    files = ["src/core/standard.scad", "src/core/gridfinity-baseplate.scad", *SCRIPTS.values()]
    return PATCH_VERSION + ":" + ",".join(str((SOURCE / f).stat().st_mtime_ns) for f in files)


def ensure() -> Path:
    """The patched library (built on first use, rebuilt when the library or the patch changes)."""
    with _lock:
        stamp_file = PATCHED / ".sectortray-patch"
        want = _stamp()
        if stamp_file.is_file() and stamp_file.read_text(encoding="utf-8") == want:
            return PATCHED
        tmp = PATCHED.with_name(PATCHED.name + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        shutil.copytree(SOURCE / "src", tmp / "src")
        for f in (*SCRIPTS.values(), "LICENSE"):
            shutil.copy2(SOURCE / f, tmp / f)
        for rel, old, new in EDITS:
            p = tmp / rel
            s = p.read_text(encoding="utf-8")
            if s.count(old) != 1:
                shutil.rmtree(tmp, ignore_errors=True)
                raise RuntimeError(f"The Gridfinity library changed: can't patch {rel} for custom square sizes.")
            p.write_text(s.replace(old, new), encoding="utf-8")
        (tmp / ".sectortray-patch").write_text(want, encoding="utf-8")
        shutil.rmtree(PATCHED, ignore_errors=True)
        os.replace(tmp, PATCHED)
        return PATCHED


def script(kind: str, pitch, work_dir: Path) -> Path:
    """The .scad to render: the original library for 42 mm squares, else a wrapper that sets $pitch first."""
    if is_standard(pitch):
        return SOURCE / SCRIPTS[kind]
    lib = ensure() / SCRIPTS[kind]
    px, py = (round(float(v), 4) for v in pitch)
    wrapper = Path(work_dir) / f"pitch_{kind}_{px:g}x{py:g}.scad"
    text = f"// SectorTray: {px:g} x {py:g} mm squares\n$pitch = [{px}, {py}];\ninclude <{lib.as_posix()}>\n"
    if not wrapper.is_file() or wrapper.read_text(encoding="utf-8") != text:
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        tmp = wrapper.with_name(wrapper.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, wrapper)
    return wrapper
