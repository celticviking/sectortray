"""Export a drawer's baseplate pieces as STLs into <data>/<id>/exports/.

Each piece is rendered by kennetek's gridfinity-rebuilt-baseplate.scad with its own grid count
and its share of the filler. A manifest remembers what each file was rendered from, so
unchanged pieces are not rendered again.
"""
import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import kennetek, openscad, paths, stl
from .layout import GRID

BASEPLATE_SCAD = paths.VENDOR / "gridfinity-rebuilt-openscad" / "gridfinity-rebuilt-baseplate.scad"
MANIFEST = "manifest.json"
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _project_lock(pid: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(pid, threading.Lock())


def _fit(lo: float, hi: float) -> float:
    """kennetek fit offset: -1 puts all padding on the negative side, +1 on the positive side."""
    return 0.0 if lo + hi <= 0 else round(2 * hi / (lo + hi) - 1, 6)


def plate_pitch(plate: dict) -> tuple[float, float]:
    p = plate.get("pitch") or kennetek.STANDARD
    return float(p[0]), float(p[1])


def piece_params(piece: dict, plate: dict) -> dict:
    pad = piece["pad"]
    px, py = pad["left"] + pad["right"], pad["back"] + pad["front"]
    gx, gy = plate_pitch(plate)
    return {
        "gridx": piece["cx"], "gridy": piece["cy"],
        # OpenSCAD +X = drawer right, +Y = drawer back (the plan's top)
        "distancex": round(piece["cx"] * gx + px, 4) if px > 0 else 0,
        "distancey": round(piece["cy"] * gy + py, 4) if py > 0 else 0,
        "fitx": _fit(pad["left"], pad["right"]),
        "fity": _fit(pad["front"], pad["back"]),
        "style_plate": plate["kennetek_style"],
        "enable_magnet": plate["magnets"],
        "style_hole": 0,
    }


def piece_hash(piece: dict, plate: dict) -> str:
    """Cache / export key of one baseplate piece. Standard squares hash exactly as before custom sizes existed."""
    params, pitch = piece_params(piece, plate), plate_pitch(plate)
    return _hash(params) if kennetek.is_standard(pitch) else _hash({**params, "$pitch": list(pitch)})


# The pinned kennetek commit (vendor/gridfinity-rebuilt-openscad), part of every part's hash so a library update
# re-renders everything. Written here rather than asked of git: the installed app has no git checkout, and asking
# there gave a different hash, so every export looked changed (0.5.0). tests/test_export.py checks it matches.
LIBRARY_COMMIT = "910e22d8607fd7f5f51ad5e5cbc5287a76810bfd"


def _hash(params: dict) -> str:
    """Changes when the piece's settings or the pinned library version change."""
    blob = json.dumps({"p": params, "lib": LIBRARY_COMMIT}, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def turned(size, bed) -> bool:
    """True when a part (w, d mm) fits the bed only turned 90°: its STL is then saved turned, so the slicer
    opens it ready to print."""
    if not size:
        return False
    (w, d), (bw, bd) = size, bed
    return not (w <= bw and d <= bd) and w <= bd and d <= bw


def export_hash(h: str, is_turned: bool) -> str:
    """What the manifest stores for an exported file: the part's hash, plus whether the file was saved turned
    (unturned parts keep their old hashes, so their exports don't go stale)."""
    return _hash({"part": h, "turned": True}) if is_turned else h


def _read_manifest(folder: Path) -> dict:
    try:
        return json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_manifest(folder: Path, manifest: dict) -> None:
    tmp = folder / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(tmp, folder / MANIFEST)


def export_baseplate(project_dir: Path, layout: dict, workers: int = 4) -> dict:
    """Renders every piece (skipping unchanged ones). Returns {folder, files[], warnings[]}."""
    if not layout or "pieces" not in layout:
        raise openscad.OpenSCADError("Measure the drawer first.")
    folder = project_dir / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    with _project_lock(project_dir.name):
        manifest = _read_manifest(folder)
        jobs = []
        for p in layout["pieces"]:
            params = piece_params(p, layout["plate"])
            name = f"baseplate_{p['id']}.stl"
            h = export_hash(piece_hash(p, layout["plate"]), bool(p.get("turned")))
            cached = manifest.get(name, {}).get("hash") == h and (folder / name).is_file()
            jobs.append((p, name, params, h, cached))

        def run(job):
            p, name, params, h, cached = job
            if cached:
                return {"piece": p["id"], "file": name, "cached": True, "seconds": 0.0, "warning": None}
            res = openscad.render(kennetek.script("baseplate", plate_pitch(layout["plate"]), project_dir / PREVIEW_CACHE), params, folder / name)
            if p.get("turned"):
                stl.copy_turned(folder / name, folder / name)
            volume = round(stl.volume(folder / name) / 1000, 2)  # cm³, for filament estimates
            got = openscad.echo_padding(res.echo)
            want = {"+X": p["pad"]["right"], "-X": p["pad"]["left"], "+Y": p["pad"]["back"], "-Y": p["pad"]["front"]}
            off = [k for k in want if abs(got[k] - want[k]) > 0.01]
            warning = f"{p['id']}: OpenSCAD padding {got} differs from the plan {want}" if off else None
            return {"piece": p["id"], "file": name, "cached": False, "seconds": round(res.seconds, 2),
                    "warning": warning, "hash": h, "params": params, "volume": volume}

        with ThreadPoolExecutor(max_workers=max(1, min(workers, os.cpu_count() or 1))) as pool:
            results = list(pool.map(run, jobs))

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        current = {r["file"] for r in results}
        new_manifest = {}
        for r in results:
            if r["cached"]:
                new_manifest[r["file"]] = manifest[r["file"]]
            else:
                new_manifest[r["file"]] = {"hash": r.pop("hash"), "params": r.pop("params"), "rendered": now,
                                           "seconds": r["seconds"], "volume": r.pop("volume")}
        # Remove baseplate files this tool made earlier for pieces that no longer exist.
        for old in set(manifest) - current:
            if old.startswith("baseplate_") and old.endswith(".stl"):
                (folder / old).unlink(missing_ok=True)
            else:
                new_manifest[old] = manifest[old]
        tmp = folder / (MANIFEST + ".tmp")
        tmp.write_text(json.dumps(new_manifest, indent=2), encoding="utf-8")
        os.replace(tmp, folder / MANIFEST)

    return {
        "folder": str(folder),
        "files": [{k: v for k, v in r.items() if k != "warning"} for r in results],
        "warnings": [r["warning"] for r in results if r["warning"]],
        "rendered": sum(1 for r in results if not r["cached"]),
        "seconds": round(sum(r["seconds"] for r in results), 2),
    }


PREVIEW_CACHE = ".cache"  # shared with bins.py previews
PREVIEW_KEEP = 60


def preview_baseplate(project_dir: Path, layout: dict, workers: int = 4) -> dict:
    """Renders every piece into <id>/.cache/plate_<hash>.stl for the Planner's 3D view (not the exports)."""
    if not layout or "pieces" not in layout:
        raise openscad.OpenSCADError("Measure the drawer first.")
    cache = project_dir / PREVIEW_CACHE
    cache.mkdir(parents=True, exist_ok=True)

    def run(p):
        params = piece_params(p, layout["plate"])
        stl = cache / f"plate_{piece_hash(p, layout['plate'])}.stl"
        with openscad.path_lock(stl):  # identical pieces (B1 = B2) share one file
            if stl.is_file():
                os.utime(stl)
                return {"piece": p["id"], "file": stl.name, "cached": True, "seconds": 0.0}
            res = openscad.render(kennetek.script("baseplate", plate_pitch(layout["plate"]), cache), params, stl)
        return {"piece": p["id"], "file": stl.name, "cached": False, "seconds": round(res.seconds, 2)}

    with ThreadPoolExecutor(max_workers=max(1, min(workers, os.cpu_count() or 1))) as pool:
        files = list(pool.map(run, layout["pieces"]))
    openscad.prune_cache(cache, "plate", max(PREVIEW_KEEP, len(files)))
    return {"files": files, "rendered": sum(not f["cached"] for f in files),
            "seconds": round(sum(f["seconds"] for f in files), 2)}
