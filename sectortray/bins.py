"""Bins: render previews (cached by settings) and export STLs.

Previews go to <data>/<id>/.cache/bin_<hash>.stl and are served to the 3D viewer.
Exports copy the same file to <data>/<id>/exports/<name>_<w>x<d>_<h>u.stl.
"""
import json
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import kennetek, openscad, paths, split, stl, tray
from .export import MANIFEST, _hash, _project_lock, _read_manifest, export_hash, turned
from .store import slugify

BINS_SCAD = paths.VENDOR / "gridfinity-rebuilt-openscad" / "gridfinity-rebuilt-bins.scad"
SPLIT_SCAD = Path(__file__).resolve().parent / "scad" / "split_bin.scad"
SPLIT_SCAD_VERSION = 2  # bump when split_bin.scad's shapes change (2: clip tabs with a 45° underside)
DEFAULT_GAP = 0.15  # joint clearance per side, Settings → Fit
CACHE = ".cache"
CACHE_KEEP = 60
TAB = {"full": 0, "auto": 1, "left": 2, "center": 3, "right": 4}
INFILL_RE = re.compile(r"Infill Dimensions\*:\s*\[([-\d.e]+),\s*([-\d.e]+),\s*([-\d.e]+)\]")
BBOX_RE = re.compile(r"Bounding Box:\s*\[([-\d.e]+),\s*([-\d.e]+),\s*([-\d.e]+)\]")


def bin_params(b: dict) -> dict:
    # With half_grid on, kennetek counts gridx/gridy in half squares (21 mm feet), so double our whole-square size.
    half = 2 if b["half_grid"] else 1
    return {
        "gridx": b["w"] * half, "gridy": b["d"] * half, "gridz": b["h"], "gridz_define": 0,
        "include_lip": b["lip"], "divx": b["divx"], "divy": b["divy"],
        "style_tab": TAB[b["tab_style"]] if b["label_tab"] else 5,
        "scoop": 1 if b["scoop"] else 0,
        "refined_holes": False,  # not compatible with magnet_holes
        "magnet_holes": b["magnets"], "screw_holes": b["screws"],
        "half_grid": b["half_grid"],
    }


def pitch_of(b: dict) -> tuple[float, float]:
    return split.pitch_of(b)


def _keyed(params: dict, b: dict) -> dict:
    """What a render's cache key covers: the params, plus the square size when it isn't 42 mm."""
    p = pitch_of(b)
    return params if kennetek.is_standard(p) else {**params, "$pitch": list(p)}


def file_names(bins: list[dict]) -> dict:
    """{bin id: export file name}, unique within the drawer. Bins for a fitted grid say their square size."""
    out, seen = {}, set()
    for b in bins:
        p = pitch_of(b)
        tag = "" if kennetek.is_standard(p) else f"_sq{p[0]:.1f}x{p[1]:.1f}"
        base = f"{slugify(b['name'])}_{b['w']}x{b['d']}_{b['h']}u{tag}"
        name, n = base, 2
        while name in seen:
            name, n = f"{base}-{n}", n + 1
        seen.add(name)
        out[b["id"]] = name + ".stl"
    return out


def _parse(log: str) -> dict:
    info = {}
    if m := INFILL_RE.search(log):
        info["infill_mm"] = [round(float(v), 2) for v in m.groups()]
    if m := BBOX_RE.search(log):
        info["bbox_mm"] = [round(float(v), 2) for v in m.groups()]
    return info


DEFAULT_BED = (250.0, 220.0)


def render_preview(project_dir: Path, b: dict, bed=DEFAULT_BED) -> dict:
    """Renders (or reuses) the bin's STL — a tray renders whole. Returns {file, cached, seconds, infill_mm?, bbox_mm?}."""
    if b.get("kind") == "tray":
        return tray.render(project_dir, b, "whole", bed)
    params = bin_params(b)
    h = _hash(_keyed(params, b))
    cache = project_dir / CACHE
    stl, meta = cache / f"bin_{h}.stl", cache / f"bin_{h}.json"
    with openscad.path_lock(stl):
        if stl.is_file() and meta.is_file():
            os.utime(stl)  # keep recently used previews from being pruned
            return {"file": stl.name, "cached": True, "seconds": 0.0, **json.loads(meta.read_text(encoding="utf-8"))}
        res = openscad.render(kennetek.script("bins", pitch_of(b), cache), params, stl)
        info = _parse(res.log)
        meta.write_text(json.dumps(info), encoding="utf-8")
    openscad.prune_cache(cache, "bin", CACHE_KEEP)
    return {"file": stl.name, "cached": False, "seconds": round(res.seconds, 2), **info}


def part_files(b: dict, name: str, bed=DEFAULT_BED) -> list[tuple[str, str]]:
    """[(part, export file name)]: one file; a front and a back half for a long tray; or the pieces of a
    split bin ("p1", "p2", …) plus "keys" when its pieces join with bow-tie keys."""
    stem = name[:-4]
    if b.get("kind") == "tray" and tray.parts_of(b, bed) == ["front", "back"]:
        return [("front", f"{stem}_front-half.stl"), ("back", f"{stem}_back-half.stl")]
    if split.is_split(b, bed):
        pl = split.plan(b, bed)
        files = [(p["id"], f"{stem}_piece{p['n']}.stl") for p in pl["pieces"]]
        return files + ([("keys", f"{stem}_keys.stl")] if pl["keys"] else [])
    return [("whole", name)]


def part_size(b: dict, part: str, bed=DEFAULT_BED):
    """Planned footprint (w, d mm) of one part as it comes out of OpenSCAD; None for bow-tie keys."""
    if part == "keys":
        return None
    gx, gy = pitch_of(b)
    if part in ("front", "back"):
        sq = tray.halves(b["w"], b["d"], bed, tray.pitch_of(b))[part]
        return tray.sq_mm(b["w"], gx), tray.sq_mm(sq, gy) + tray.CLIP
    sw, sd = (b["w"], b["d"]) if part == "whole" else next(p for p in split.plan(b, bed)["pieces"] if p["id"] == part)["squares"]
    return sw * gx - 0.5, sd * gy - 0.5


def part_export_hash(b: dict, part: str, bed=DEFAULT_BED, gap=DEFAULT_GAP) -> str:
    return export_hash(part_hash(b, part, bed, gap), turned(part_size(b, part, bed), bed))


CLIP_T = 1.6        # plate thickness (split_bin.scad clip_t)
SCOOP_CLEAR = 0.3   # how far a lifted clip starts above the scoop (on top of the joint gap)


def _scoop_wall(b: dict, cl) -> bool:
    """kennetek's finger scoop curves up from the floor along the front wall (−y): clips on that wall sit on it."""
    return bool(b.get("scoop")) and cl[0] == 0 and cl[3] == 1


def _clip_poly(poly, k, lo, hi):
    """Polygon (list of xyz) cut to lo <= coordinate k <= hi (Sutherland–Hodgman, two edges)."""
    for edge, keep in ((lo, lambda p: p[k] >= lo), (hi, lambda p: p[k] <= hi)):
        out = []
        for i, p in enumerate(poly):
            q = poly[i - 1]
            if keep(p) != keep(q):
                t = (edge - q[k]) / (p[k] - q[k])
                out.append(tuple(q[j] + t * (p[j] - q[j]) for j in range(3)))
            if keep(p):
                out.append(p)
        poly = out
        if not poly:
            break
    return poly


def _top_in_band(whole: Path, xr, yr, below: float):
    """Highest point of the bin's upward-facing surfaces inside the box xr × yr and below `below` (the scoop's
    top where a clip plate would sit), or None."""
    top = None
    for t in stl.triangles(whole):
        a, b_, c = t[0:3], t[3:6], t[6:9]
        if max(a[0], b_[0], c[0]) < xr[0] or min(a[0], b_[0], c[0]) > xr[1] or max(a[1], b_[1], c[1]) < yr[0] or min(a[1], b_[1], c[1]) > yr[1]:
            continue
        ux, uy = b_[0] - a[0], b_[1] - a[1]
        vx, vy = c[0] - a[0], c[1] - a[1]
        if ux * vy - uy * vx <= 1e-9:  # not facing up (z of the normal)
            continue
        poly = _clip_poly(_clip_poly([a, b_, c], 0, *xr), 1, *yr)
        for p in poly:
            if p[2] < below and (top is None or p[2] > top):
                top = p[2]
    return top


def _lift_clips(clips: list, whole: Path, b: dict, gap: float) -> list:
    """A clip on the scoop wall would sit in front of the scoop, which is the same curve on both pieces, so the
    part reaching over the other piece got trimmed down to a flat-bottomed sliver (no 45° underside, unsupported
    while printing). Instead start the plate just above the scoop where it sits, measured from the rendered bin,
    and keep its height as far as the wall allows. Clips on other walls are unchanged."""
    if not any(_scoop_wall(b, cl) for cl in clips):
        return clips
    zmax = split.FLOOR_Z + split.cavity(b)[2] - 0.8
    out = []
    for cl in clips:
        axis, c, v0, n, d, z0, z1 = cl
        if not _scoop_wall(b, cl):
            out.append(cl)
            continue
        xr = sorted((c - d * split.CLIP_IN, c + d * split.CLIP_OUT))
        yr = (v0 + gap, v0 + gap + CLIP_T)
        top = _top_in_band(whole, xr, yr, z1)
        start = (top or 0) + gap + SCOOP_CLEAR
        if start <= z0:
            out.append(cl)
            continue
        end = min(zmax, start + (z1 - z0))
        if end - start >= split.CLIP_MIN_H:  # else no room above the scoop: this wall gets no clip
            out.append([axis, c, v0, n, d, round(start, 3), round(end, 3)])
    return out


def _split_params(project_dir: Path, b: dict, part: str, bed, gap: float, whole_file: str | None = None) -> dict:
    pl = split.plan(b, bed)
    if part == "keys":
        return {"part": '"keys"', "keys": pl["keys"], "gap": gap}
    p = next(x for x in pl["pieces"] if x["id"] == part)
    stl_path = (project_dir / CACHE / whole_file).resolve() if whole_file else None
    clips = _lift_clips(p["clips"], stl_path, b, gap) if stl_path else p["clips"]
    stl_arg = stl_path.as_posix() if stl_path else "<whole>"
    return {"part": '"piece"', "stl": f'"{stl_arg}"', "box": p["box"], "clips": clips, "pockets": p["pockets"], "gap": gap}


def part_hash(b: dict, part: str, bed=DEFAULT_BED, gap=DEFAULT_GAP) -> str:
    if b.get("kind") == "tray":
        return _hash({"tray": tray.params(b, part, bed)})
    if part != "whole":  # a piece of a split bin, or its keys
        sp = _split_params(Path("."), b, part, bed, gap)
        h = {"bin": _keyed(bin_params(b), b), "split": sp, "v": SPLIT_SCAD_VERSION}
        if any(_scoop_wall(b, cl) for cl in sp.get("clips", [])):
            h["lift"] = 1  # clips lifted above the scoop: only these pieces change
        if part == "keys":
            h["key_fit"] = 1  # keys grown on the flanks, ends trimmed (pockets unchanged)
        return _hash(h)
    return _hash(_keyed(bin_params(b), b))


def render_part(project_dir: Path, b: dict, part: str, bed=DEFAULT_BED, gap=DEFAULT_GAP) -> dict:
    """Renders (or reuses) one part: "whole", a tray half, a split bin's piece ("p1"…) or its "keys"."""
    if b.get("kind") == "tray":
        return tray.render(project_dir, b, part, bed)
    if part == "whole":
        return render_preview(project_dir, b, bed)
    whole = render_preview(project_dir, b, bed) if part != "keys" else None
    h = part_hash(b, part, bed, gap)
    cache = project_dir / CACHE
    stl = cache / f"split_{h}.stl"
    with openscad.path_lock(stl):
        if stl.is_file():
            os.utime(stl)
            return {"file": stl.name, "cached": True, "seconds": 0.0, "part": part}
        res = openscad.render(SPLIT_SCAD, _split_params(project_dir, b, part, bed, gap, whole and whole["file"]), stl)
    openscad.prune_cache(cache, "split", CACHE_KEEP)
    return {"file": stl.name, "cached": False, "seconds": round(res.seconds + (whole or {}).get("seconds", 0), 2), "part": part}


def render_pieces(project_dir: Path, b: dict, bed=DEFAULT_BED, gap=DEFAULT_GAP, spread=12.0) -> dict:
    """Every piece of a split bin, for the Bins page 3D view: placed in plan coordinates (mm from the bin's
    back-left corner, y towards the front) and pulled `spread` mm apart so the seams show."""
    pl = split.plan(b, bed)
    whole = render_preview(project_dir, b, bed)
    W, D = b["w"] * pitch_of(b)[0] - 0.5, b["d"] * pitch_of(b)[1] - 0.5
    with ThreadPoolExecutor(max_workers=4) as pool:
        done = list(pool.map(lambda p: render_part(project_dir, b, p["id"], bed, gap), pl["pieces"]))
    out = []
    for p, r in zip(pl["pieces"], done):
        x0, y1 = max(p["box"][0], -W / 2), min(p["box"][3], D / 2)
        out.append({"part": p["id"], "n": p["n"], "file": r["file"], "x": x0 + W / 2 + p["col"] * spread,
                    "y": D / 2 - y1 + p["row"] * spread})
    keys = render_part(project_dir, b, "keys", bed, gap) if pl["keys"] else None
    return {"pieces": out, "keys": keys and {"file": keys["file"], "count": pl["keys"]}, "notes": pl["notes"],
            "x": pl["x"], "y": pl["y"], "joint": pl["joint"], "infill_mm": whole.get("infill_mm"),
            "cached": whole["cached"] and all(r["cached"] for r in done),
            "seconds": round(whole["seconds"] + max([r["seconds"] for r in done] or [0]), 2)}


JOINT_TEST = ".joint-test"  # dot: never mistaken for a drawer folder


def joint_test(data_dir: Path, bed=DEFAULT_BED, gap=DEFAULT_GAP) -> dict:
    """Small coupons for checking the joint clearance before printing real split bins:
    a 2×1 3u bin in two pieces with snap clips, and a 2×1 2u bin in two pieces with one bow-tie key.
    Written to <data>/joint-test/ (its own .cache inside). Returns {folder, files}."""
    from .store import _clean_bin
    folder = Path(data_dir) / JOINT_TEST
    plain = {"w": 2, "d": 1, "label_tab": False, "scoop": False, "magnets": False}
    coupons = [("clips", _clean_bin({**plain, "name": "Clips", "h": 3, "split": {"on": True, "joint": "clips", "x": [1, 1]}}, "c")),
               ("keys", _clean_bin({**plain, "name": "Keys", "h": 2, "split": {"on": True, "joint": "keys", "x": [1, 1]}}, "k"))]
    jobs = []
    for label, b in coupons:
        render_preview(folder, b, bed)
        jobs += [(label, b, part) for part, _ in part_files(b, "x.stl", bed)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        done = list(pool.map(lambda j: (j, render_part(folder, j[1], j[2], bed, gap)), jobs))
    files = []
    for (label, _b, part), r in done:
        name = "joint-test_bow-tie-key.stl" if part == "keys" else f"joint-test_{label}_piece{part[1:]}.stl"
        shutil.copyfile(folder / CACHE / r["file"], folder / name)
        files.append(name)
    return {"folder": str(folder), "files": files, "gap": gap}


def export_bin(project_dir: Path, bins: list[dict], bid: str, bed=DEFAULT_BED, gap=DEFAULT_GAP) -> dict:
    b = next((x for x in bins if x["id"] == bid), None)
    if b is None:
        raise openscad.OpenSCADError("That bin isn't saved yet.")
    files = part_files(b, file_names(bins)[bid], bed)
    folder = project_dir / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    if len(files) > 1 and b.get("kind") != "tray":
        render_preview(project_dir, b, bed)  # the whole bin first: every piece is cut from it

    def run(pf):
        part, name = pf
        return part, name, render_part(project_dir, b, part, bed, gap)

    with ThreadPoolExecutor(max_workers=4) as pool:  # tray halves / bin pieces render side by side
        rendered = list(pool.map(run, files))
    with _project_lock(project_dir.name):
        manifest = _read_manifest(folder)
        names = {n for _, n, _ in rendered}
        for old, entry in list(manifest.items()):  # a renamed/resized bin replaces its previous export(s)
            if entry.get("bin") == bid and old not in names:
                (folder / old).unlink(missing_ok=True)
                del manifest[old]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for part, name, r in rendered:
            is_turned = turned(part_size(b, part, bed), bed)
            volume = round(stl.volume(project_dir / CACHE / r["file"]) / 1000, 2)  # cm³, for filament estimates
            if is_turned:
                stl.copy_turned(project_dir / CACHE / r["file"], folder / name)
            else:
                shutil.copyfile(project_dir / CACHE / r["file"], folder / name)
            manifest[name] = {"bin": bid, "part": part, "hash": export_hash(part_hash(b, part, bed, gap), is_turned),
                              "turned": is_turned, "volume": volume, "rendered": now}
        tmp = folder / (MANIFEST + ".tmp")
        tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(tmp, folder / MANIFEST)
    return {"file": rendered[0][1], "files": [n for _, n, _ in rendered], "folder": str(folder),
            "seconds": round(sum(r["seconds"] for _, _, r in rendered), 2), "cached": all(r["cached"] for _, _, r in rendered)}
