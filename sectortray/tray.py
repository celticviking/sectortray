"""Silverware trays: sizing (twin of web/js/traymath.js) and rendering with Gridfinity Extended.

A tray is a bin with kind "tray" and a `tray` spec:
  {utensils: [{id, name, kind, preset, len, handle, head, count, out}], margin: "Snug"|"Normal"|"Easy",
   round: bool, halves: bool}
Slot order is the list order; `out` utensils are kept on the list but get no slot.
Halves: when the tray is too long for the bed, it prints as a front and a back half that clip together
(the script's "extendable" option, cut on a grid line).
"""
import json
import os
from math import ceil
from pathlib import Path

from . import openscad, paths
from .export import _hash

SCAD = paths.VENDOR / "gridfinity_extended_openscad" / "gridfinity_silverware.scad"
G, WALL, CLR, CLIP = 42.0, 4.0, 5.7, 3.4
MARGINS = {"Snug": 3.0, "Normal": 5.0, "Easy": 7.0}
KINDS = ("fork", "knife", "spoon", "custom")
MAX_UTENSILS = 14
CACHE_KEEP = 40


def _wide(u):
    return max(u["handle"], u.get("head") or 0)


def width_need(us, m):
    if not us:
        return 0.0
    s = sum(WALL + 2 * m + max(_wide(a) / 2 + b["handle"] / 2, a["handle"] / 2 + _wide(b) / 2) for a, b in zip(us, us[1:]))
    return s + 2 * m + _wide(us[0]) / 2 + _wide(us[-1]) / 2


def depth_need(us, m):
    return max(u["len"] for u in us) + 2 * m if us else 0.0


def squares(mm, g=G):
    return max(1, ceil((mm + CLR) / g - 1e-9))


def sq_mm(n, g=G):
    return n * g - 0.5


def pitch_of(b):
    """Square size of the tray's drawer (set by the server as _pitch); 42 × 42 by default."""
    p = b.get("_pitch") if isinstance(b, dict) else None
    return (float(p[0]), float(p[1])) if p else (G, G)


def footprint(us, m, pitch=(G, G)):
    w, d = width_need(us, m), depth_need(us, m)
    return {"wmm": w, "dmm": d, "gx": squares(w, pitch[0]), "gy": squares(d, pitch[1])}


def fits_bed(wmm, dmm, bed):
    return (wmm <= bed[0] and dmm <= bed[1]) or (wmm <= bed[1] and dmm <= bed[0])


def halves(gx, gy, bed, pitch=(G, G)):
    """{front, back} squares for a two-piece tray, or None when one piece fits (or no split works)."""
    px, py = pitch
    if gy < 2 or fits_bed(sq_mm(gx, px), sq_mm(gy, py), bed):
        return None
    first = ceil(gy / 2)
    for a in list(range(first, gy)) + list(range(first - 1, 0, -1)):
        b = gy - a
        if fits_bed(sq_mm(gx, px), sq_mm(a, py) + CLIP, bed) and fits_bed(sq_mm(gx, px), sq_mm(b, py) + CLIP, bed):
            return {"front": a, "back": b}
    return None


def slots_in(spec):
    return [u for u in spec["utensils"] if not u.get("out")]


def size_of(spec, pitch=(G, G)):
    """(gx, gy) footprint in squares for a cleaned spec."""
    fp = footprint(slots_in(spec), MARGINS[spec["margin"]], pitch)
    return fp["gx"], fp["gy"]


def parts_of(b, bed):
    """The STLs a tray prints as: ["whole"] or ["front", "back"]."""
    if b["tray"].get("halves") and halves(b["w"], b["d"], bed, pitch_of(b)):
        return ["front", "back"]
    return ["whole"]


def params(b, part, bed):
    spec, us = b["tray"], slots_in(b["tray"])
    p = {
        "number_of_utensils": len(us),
        "utensil_margin": MARGINS[spec["margin"]],
        "height": f"[{b['h']},0]",
        "chamber_wall_top_radius": -2 if spec.get("round", True) else 0,
        "enable_magnets": bool(b.get("magnets")),
        "lip_style": '"normal"' if b.get("lip", True) else '"none"',
    }
    for i, u in enumerate(us, 1):
        p[f"utensil_{i}"] = f"[{u['handle']},{u.get('head') or 0},{u['len']},{i}]"
    if pitch_of(b) != (G, G):  # Gridfinity Extended takes the square size directly
        p["pitch"] = [round(pitch_of(b)[0], 4), round(pitch_of(b)[1], 4), 7]
    if part in ("front", "back"):
        h = halves(b["w"], b["d"], bed, pitch_of(b))
        # The script's "back" keeps the −Y side (the drawer front); its "front" keeps the +Y side.
        p["extension_y_enabled"] = '"back"' if part == "front" else '"front"'
        p["extension_y_position"] = h["front"]
    return p


def render(project_dir: Path, b: dict, part: str, bed) -> dict:
    prm = params(b, part, bed)
    h = _hash({"tray": prm})
    cache = project_dir / ".cache"
    stl, meta = cache / f"tray_{h}.stl", cache / f"tray_{h}.json"
    with openscad.path_lock(stl):
        if stl.is_file() and meta.is_file():
            os.utime(stl)
            return {"file": stl.name, "cached": True, "seconds": 0.0, "hash": h}
        res = openscad.render(SCAD, prm, stl)
        meta.write_text(json.dumps({"part": part}), encoding="utf-8")
    openscad.prune_cache(cache, "tray", CACHE_KEEP)
    return {"file": stl.name, "cached": False, "seconds": round(res.seconds, 2), "hash": h}
