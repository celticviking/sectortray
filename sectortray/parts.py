"""The print list: every part of a drawer with its export status, Export all, and Open in PrusaSlicer."""
import os
import re
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import bins as bins_mod
from . import export, split, stl, threemf, tray
from .openscad import OpenSCADError
from .store import StoreError

MAX_COPIES = 20  # per part when opening in the slicer
SLICER_DIR = "3MF"  # exports/3MF/: the files Open in PrusaSlicer builds
TURNED_NOTE = " · turned to fit the bed"
CLIP_ROOM = 8.0  # a split bin's clips reach this far past its cut edge


def _pack_size(w: float, d: float, turned: bool) -> tuple[float, float]:
    return (d, w) if turned else (w, d)


def _status(manifest: dict, folder: Path, name: str, want_hash: str, owner_key: str, owner: str) -> dict:
    entry = manifest.get(name)
    if entry and entry.get("hash") == want_hash and (folder / name).is_file():
        return {"status": "done", "exported": entry.get("rendered")}
    # an earlier export of this same part with different settings (or under its old name)
    stale = entry is not None or any(e.get(owner_key) == owner for e in manifest.values())
    return {"status": "stale" if stale else "none", "exported": None}


def _code_letters(b: dict, taken: set) -> str:
    """One letter from the name (trays: T), or two when that letter is a baseplate column (A1, B2…), so a bin
    never reads like a baseplate piece: "Bin 1×1" next to columns A–B → BI. Letters only ("2nd Front Pens" → F)."""
    if b.get("kind") == "tray":
        return "TR" if "T" in taken else "T"
    words = re.findall(r"\b[A-Za-z]+", b["name"])  # words that start with a letter ("2nd" doesn't)
    if not words:
        return "BN" if "B" in taken else "B"
    first = words[0][0].upper()
    if first not in taken:
        return first
    second = words[1][0] if len(words) > 1 else (words[0][1] if len(words[0]) > 1 else "X")
    return first + second.upper()


def _bin_codes(bins: list[dict], taken=frozenset()) -> dict:
    """Short ids for the print list: letter(s) from the name + running number (U1, U2, S1…); trays are T1, T2…
    `taken` = the baseplate's column letters."""
    counts, out = {}, {}
    for b in bins:
        letter = _code_letters(b, set(taken))
        counts[letter] = counts.get(letter, 0) + 1
        out[b["id"]] = f"{letter}{counts[letter]}"
    return out


def hardware(doc: dict, layout: dict | None) -> dict:
    """Shopping list: 6 × 2 mm magnets (4 per square in the baseplate and in bins; a half-grid bin has its 4 in
    the outer corners) and M3 screws for bins with screw holes (4 per square). Counts every copy."""
    plate = 0
    if layout and "pieces" in layout and layout["plate"].get("magnets"):
        plate = sum(p["cx"] * p["cy"] * 4 for p in layout["pieces"])
    bins = screws = 0
    for b in doc.get("bins", []):
        per = 4 if b.get("half_grid") else b["w"] * b["d"] * 4
        if b.get("magnets"):
            bins += per * b["qty"]
        if b.get("screws") and b.get("kind") != "tray":
            screws += per * b["qty"]
    return {"magnets": {"plate": plate, "bins": bins}, "screws": screws}


def fits_bed(w_mm: float, d_mm: float, bed) -> bool:
    """Same rule as ST.binFit in web/js/shell.js: fits either way round on the bed."""
    bw, bd = bed
    return (w_mm <= bw and d_mm <= bd) or (w_mm <= bd and d_mm <= bw)


def list_parts(project_dir: Path, doc: dict, layout: dict | None, bed=bins_mod.DEFAULT_BED, gap=bins_mod.DEFAULT_GAP) -> dict:
    folder = project_dir / "exports"
    manifest = export._read_manifest(folder)
    parts = []
    if layout and "pieces" in layout:
        for p in layout["pieces"]:
            name = f"baseplate_{p['id']}.stl"
            h, turned = export.piece_hash(p, layout["plate"]), bool(p.get("turned"))
            sides = [k for k in ("left", "right", "back", "front") if p["pad"][k] > 0]
            parts.append({
                "kind": "baseplate", "id": p["id"], "code": p["id"], "name": f"Baseplate {p['id']}", "turned": turned,
                "detail": f"{p['cx']}×{p['cy']} squares" + (f" · {', '.join(sides)} filler" if sides else "") + (TURNED_NOTE if turned else ""),
                "key": f"plate:{p['id']}", "hash": h,
                "qty": 1, "w": p["w"], "d": p["d"], "h": layout["plate"]["height"], "file": name,
                "fits": fits_bed(p["w"], p["d"], bed), "pack": _pack_size(p["w"], p["d"], turned),
                "filament": (doc.get("baseplate") or {}).get("filament"),
                **_status(manifest, folder, name, export.export_hash(h, turned), "piece", p["id"]),
            })
    columns = {p["id"][0] for p in (layout or {}).get("pieces", [])}
    names, codes = bins_mod.file_names(doc["bins"]), _bin_codes(doc["bins"], columns)
    for b in doc["bins"]:
        is_tray = b.get("kind") == "tray"
        if is_tray:
            spec = b["tray"]
            slots = [u for u in spec["utensils"] if not u.get("out")]
            feats = [f"{len(slots)} slots", f"{spec['margin'].lower()} margin"]
        else:
            feats = [f for f, on in (("label tab", b["label_tab"] and b["h"] >= 3), ("scoop", b["scoop"]),
                                     ("magnets", b["magnets"]), ("no lip", not b["lip"])) if on]
            if b["divx"] * b["divy"] > 1:
                feats.insert(0, f"{b['divx'] * b['divy']} compartments")
        files = bins_mod.part_files(b, names[b["id"]], bed)
        halves = tray.halves(b["w"], b["d"], bed, tray.pitch_of(b)) if is_tray and len(files) == 2 else None
        gx, gy = split.pitch_of(b)
        pl = split.plan(b, bed) if not is_tray and len(files) > 1 else None
        pieces = {p["id"]: p for p in pl["pieces"]} if pl else {}
        joined = {"clips": "clips to the next piece", "keys": "joins with bow-tie keys", "none": "sits beside the next piece"}
        h = b["h"] * 7 + (3.548 if b["lip"] else 0)
        for part, name in files:
            sw, sd = b["w"], b["d"]
            code, label, extra, qty = codes[b["id"]], b["name"], "", b["qty"]
            if halves:
                sd = halves[part]
                code += {"front": "·F", "back": "·B"}[part]
                label += {"front": " · front half", "back": " · back half"}[part]
                extra = " · clips to the other half"
            elif part == "keys":
                code += "·K"
                label += f" · bow-tie keys ×{pl['keys']}"
            elif part in pieces:
                sw, sd = pieces[part]["squares"]
                code += f"·{pieces[part]['n']}"
                label += f" · piece {pieces[part]['n']} of {len(pieces)}"
                extra = f" · {joined[pl['joint']]}"
            keys = part == "keys"
            ph = bins_mod.part_hash(b, part, bed, gap)
            size = bins_mod.part_size(b, part, bed)
            turned = export.turned(size, bed)
            if keys:  # laid out in a row by split_bin.scad
                size = (pl["keys"] * 14 - 4, 18)
            elif part in pieces:
                size = (size[0] + CLIP_ROOM, size[1] + CLIP_ROOM)
            parts.append({
                "pack": _pack_size(*size, turned),
                "key": f"{b['id']}:{part}", "hash": ph, "turned": turned,
                "kind": "tray" if is_tray else "bin", "id": b["id"], "part": part,
                "code": code, "name": label,
                "detail": ("Press into the floor pockets across each seam" if keys else
                           f"{sw}×{sd} of {b['w']}×{b['d']}" if pieces else f"{b['w']}×{b['d']}") +
                          ("" if keys else (f" · {', '.join(feats)}" if feats else "") + extra) + (TURNED_NOTE if turned else ""),
                "qty": qty, "w": None if keys else sw * gx - 0.5, "d": None if keys else sd * gy - 0.5,
                "h": 1.4 if keys else h, "u": None if keys else b["h"], "file": name, "filament": b.get("filament"),
                "fits": True if keys else fits_bed(sw * gx - 0.5, sd * gy - 0.5, bed),
                **_status(manifest, folder, name, export.export_hash(ph, turned), "bin", b["id"]),
            })
    printed, opened = doc.get("printed") or {}, doc.get("opened") or {}
    for p in parts:
        pr, op = printed.get(p["key"]), opened.get(p["key"])
        p["printed"] = min(pr["n"], p["qty"]) if pr else 0
        p["printed_old"] = bool(pr) and pr["hash"] != p["hash"]  # ticked, but the part has changed since
        p["opened"] = op["at"] if op and op["hash"] == p["hash"] else None
    _volumes(folder, manifest, parts)
    plates = plan_plates(parts, bed, doc.get("_filament"))
    return {"folder": str(folder), "parts": parts, "bed": {"width": bed[0], "depth": bed[1]},
            "prints": sum(p["qty"] for p in parts), "designs": len(parts),
            "printed": sum(p["printed"] for p in parts if not p["printed_old"]), "plates": plates,
            "hardware": hardware(doc, layout)}


def _volumes(folder: Path, manifest: dict, parts: list[dict]) -> None:
    """Each exported part's volume in cm³ (for filament estimates; None until exported). Exports made before
    volumes were recorded get theirs measured once and remembered in the manifest."""
    todo = [p for p in parts if p["status"] == "done" and "volume" not in manifest.get(p["file"], {})]
    if todo:
        with export._project_lock(folder.parent.name):
            manifest.clear()
            manifest.update(export._read_manifest(folder))
            for p in todo:
                entry = manifest.get(p["file"])
                if entry is not None and "volume" not in entry:
                    try:
                        entry["volume"] = round(stl.volume(folder / p["file"]) / 1000, 2)
                    except (OSError, ValueError):
                        pass
            export.write_manifest(folder, manifest)
    for p in parts:
        p["volume"] = manifest.get(p["file"], {}).get("volume") if p["status"] == "done" else None


def plan_plates(parts: list[dict], bed, default_filament=None) -> list[dict]:
    """Which copies go on which bed load ("plate"), packed from the parts' planned sizes, one filament per plate
    (in the order filaments first appear). Each plate has its filament and lists {key, copies, before}
    (before = copies of that part on earlier plates), and whether it's all printed (counting plates in order:
    plate 2 is printed once plate 1's copies and its own are)."""
    groups = {}
    for p in parts:
        groups.setdefault(p["filament"] or default_filament, []).extend([p] * min(MAX_COPIES, p["qty"]))
    done = {p["key"]: 0 if p["printed_old"] else p["printed"] for p in parts}
    row = {p["key"]: i for i, p in enumerate(parts)}
    out, before = [], {}
    for fil, items in groups.items():
        by_bed = {}
        for p, (b, _, _) in zip(items, threemf.pack([p["pack"] for p in items], bed)):
            on = by_bed.setdefault(b, {})
            on[p["key"]] = on.get(p["key"], 0) + 1
        for b in sorted(by_bed, key=lambda b: min(row[k] for k in by_bed[b])):  # in Print List order: baseplate first
            rows = [{"key": k, "copies": c, "before": before.get(k, 0)} for k, c in by_bed[b].items()]
            for r in rows:
                before[r["key"]] = r["before"] + r["copies"]
            out.append({"n": len(out) + 1, "filament": fil, "parts": rows,
                        "printed": all(done[r["key"]] >= r["before"] + r["copies"] for r in rows)})
    return out


def plate_printed(listing: dict, n, on) -> dict:
    """{key: entry} for store.track: marks plate n printed (each part's count goes up to cover it and the
    plates before it) or not printed (down to the plates before it)."""
    plate = next((pl for pl in listing["plates"] if pl["n"] == n), None) if isinstance(n, int) else None
    if plate is None:
        raise StoreError("Unknown plate.")
    by_key, out = {p["key"]: p for p in listing["parts"]}, {}
    for r in plate["parts"]:
        p = by_key[r["key"]]
        have = 0 if p["printed_old"] else p["printed"]
        want = max(have, r["before"] + r["copies"]) if on else min(have, r["before"])
        out.update(set_printed(listing["parts"], r["key"], want))
    return out


def export_all(project_dir: Path, doc: dict, layout: dict | None, bed=bins_mod.DEFAULT_BED, gap=bins_mod.DEFAULT_GAP) -> dict:
    results, errors = [], []
    if layout and "pieces" in layout:
        try:
            r = export.export_baseplate(project_dir, layout)
            results += [f["file"] for f in r["files"]]
        except OpenSCADError as e:
            errors.append(f"Baseplate: {e}")

    def one(b):
        try:
            return bins_mod.export_bin(project_dir, doc["bins"], b["id"], bed, gap)["files"], None
        except OpenSCADError as e:
            return None, f"{b['name']}: {e}"

    with ThreadPoolExecutor(max_workers=max(1, min(4, os.cpu_count() or 1))) as pool:
        for f, err in pool.map(one, doc["bins"]):
            if f:
                results += f
            if err:
                errors.append(err)
    return {"folder": str(project_dir / "exports"), "files": results, "errors": errors}


def _file_title(text: str) -> str:
    """A name that's safe as a Windows file name (the slicer shows it as the project's title)."""
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text).strip(" .")
    return t[:100] or "SectorTray"


def _keep_saved_project(path: Path) -> None:
    """If the slicer saved its own project over one of our 3MF files (it holds print settings then), keep it
    under a new name instead of overwriting it."""
    try:
        with zipfile.ZipFile(path) as z:
            saved = "Metadata/Slic3r_PE.config" in z.namelist()
    except (OSError, zipfile.BadZipFile):
        return
    if saved:
        path.rename(path.with_name(f"{path.stem} (saved {datetime.now():%Y-%m-%d %H%M}).3mf"))


def write_3mf(project_dir: Path, doc: dict, chosen: list[dict], bed, title: str,
              copies: dict | None = None, plates: dict | None = None) -> dict:
    """One 3MF with the chosen (exported) parts, named like the Print List and laid out on the bed: qty copies
    each unless `copies` ({key: n}) says otherwise; `plates` ({key: [plate per copy]}) keeps plates on separate beds."""
    folder = project_dir / "exports"
    path = folder / SLICER_DIR / f"{_file_title(title)}.3mf"
    objects = [{"name": p["name"] if p["kind"] == "baseplate" else f"{p['code']} {p['name']}",
                "stl": folder / p["file"], "copies": (copies or {}).get(p["key"], min(MAX_COPIES, p["qty"])),
                "plates": (plates or {}).get(p["key"])} for p in chosen]
    if path.exists():
        _keep_saved_project(path)
    r = threemf.write(path, objects, bed, title)
    return {"path": path, **r}


def open_in_slicer(slicer: Path | None, project_dir: Path, doc: dict, layout: dict | None, files: list | None,
                   bed=bins_mod.DEFAULT_BED, gap=bins_mod.DEFAULT_GAP, plate=None, slicer_name="PrusaSlicer") -> dict:
    """Opens exported parts in the slicer as one 3MF (named parts, qty copies each, laid out on the bed; parts
    for more than one bed go on the next bed, plate by plate). `files=None` means every exported part;
    `plate` = only that plate's copies."""
    if not slicer:
        raise StoreError(f"{slicer_name} wasn't found. Set its location in Settings.", 404)
    full = list_parts(project_dir, doc, layout, bed, gap)
    listing = full["parts"]
    if plate is not None:
        pl = next((x for x in full["plates"] if x["n"] == plate), None) if isinstance(plate, int) else None
        if pl is None:
            raise StoreError("Unknown plate.")
        by_key = {p["key"]: p for p in listing}
        ready = [by_key[r["key"]] for r in pl["parts"]]
        missing = [p["file"] for p in ready if p["status"] != "done"]
        if missing:
            raise StoreError("Export it first: " + ", ".join(missing))
        r = write_3mf(project_dir, doc, ready, bed, f"{doc['name']} - plate {plate} of {len(full['plates'])}",
                      copies={x["key"]: x["copies"] for x in pl["parts"]})
        launch_slicer(slicer, [str(r["path"])])
        return {"opened": r["copies"], "beds": r["beds"], "file": r["path"].name, "skipped": [],
                "sent": {p["key"]: {"hash": p["hash"]} for p in ready}}
    by_file = {p["file"]: p for p in listing}
    chosen = list(by_file) if files is None else files
    if not isinstance(chosen, list) or not all(isinstance(f, str) and f in by_file for f in chosen):
        raise StoreError("Unknown file.")
    folder = project_dir / "exports"
    missing = [f for f in chosen if by_file[f]["status"] != "done"]
    if files is not None and missing:
        raise StoreError("Export it first: " + ", ".join(missing))
    ready = [by_file[f] for f in chosen if f not in missing]
    if not ready:
        raise StoreError("Nothing exported yet. Use Export all first.")
    ids = {(p["kind"] == "baseplate", p["id"] if p["kind"] != "baseplate" else "") for p in ready}
    if files is None or len(ids) > 1:
        title = doc["name"]
    elif len(ready) == 1:
        title = f"{doc['name']} - {ready[0]['name']}"
    elif ready[0]["kind"] == "baseplate":
        title = f"{doc['name']} - Baseplate"
    else:
        title = f"{doc['name']} - " + next(b["name"] for b in doc["bins"] if b["id"] == ready[0]["id"])
    plates = None
    if files is None:  # everything: plate by plate, one bed each (as the Print List's Plates)
        plates = {}
        for pl in full["plates"]:
            for x in pl["parts"]:
                plates.setdefault(x["key"], []).extend([pl["n"]] * x["copies"])
    r = write_3mf(project_dir, doc, ready, bed, title, plates=plates)
    launch_slicer(slicer, [str(r["path"])])
    return {"opened": r["copies"], "beds": r["beds"], "file": r["path"].name, "skipped": missing if files is None else [],
            "sent": {p["key"]: {"hash": p["hash"]} for p in ready}}


def set_printed(listing: list[dict], key, n) -> dict:
    """{key: entry or None} for store.track: n of that part printed (0 clears it), stamped with its current version."""
    p = next((x for x in listing if x["key"] == key), None)
    if p is None:
        raise StoreError("Unknown part.")
    if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= p["qty"]:
        raise StoreError(f"Printed must be a whole number from 0 to {p['qty']}.")
    return {key: {"n": n, "hash": p["hash"]} if n else None}


def launch_slicer(slicer: Path, paths: list[str]) -> None:
    flags = (0x00000008 | 0x00000200) if os.name == "nt" else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(slicer), *paths], creationflags=flags, close_fds=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
