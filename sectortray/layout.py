"""Drawer → Gridfinity layout: grid count, filler, printable baseplate pieces, tallest bin.

This is the authoritative version of the placeholder `ST.layout` in web/js/st.js, and returns
the same shape so the plan-view SVG can draw it. Coordinates are drawer-relative millimeters:
origin at the back-left inside corner, x to the right, y towards the front.

Heights were measured from kennetek/gridfinity-rebuilt-openscad renders (2026-10-19):
  bin = 7·u mm (+3.548 mm stacking lip); bin floor sits 0.35 mm above the plate's socket floor;
  plate = 5 mm socket layer + style-dependent floor (lite 0, standard 4.35, weighted 6.4).
"""
from dataclasses import dataclass
from math import floor

GRID = 42.0
UNIT = 7.0
LIP = 3.548
SOCKET_LAYER = 5.0
BIN_SEAT = 0.35
EPS = 1e-6

# UI style → (kennetek style_plate, extra floor height under the sockets, supports magnets)
PLATE_STYLES = {
    "lite": (0, 0.0, False),        # thin open frame
    "standard": (2, 4.35, True),    # skeletonized floor, magnet pockets
    "weighted": (1, 6.4, True),     # thick floor with weight pockets
}

DEFAULT_CLEARANCE = 1.0   # mm taken off each axis in total (0.5 mm per side) so the plate drops in
DEFAULT_HEADROOM = 1.0    # mm left between the tallest bin and the drawer top


@dataclass(frozen=True)
class Bed:
    width: float = 250.0
    depth: float = 220.0
    name: str = "Prusa CORE One"


class LayoutError(ValueError):
    """The drawer can't take a Gridfinity grid; the message is safe to show the user."""


def _split_axis(n: int, pad_lo: float, pad_hi: float, limit: float, pitch: float = GRID) -> list[int] | None:
    """Fewest, most even runs of grid cells along one axis so every piece (edge pieces carry the filler) fits `limit`."""
    for k in range(1, n + 1):
        caps = []
        for i in range(k):
            extra = (pad_lo if i == 0 else 0.0) + (pad_hi if i == k - 1 else 0.0)
            caps.append(floor((limit - extra) / pitch + EPS))
        if min(caps) < 1 or sum(caps) < n:
            continue
        parts = [n // k + (1 if i < n % k else 0) for i in range(k)]  # balanced, extras first: 11 → 4+4+3
        # Edge pieces carry filler, so they may hold fewer cells; push the excess inwards.
        for _ in range(n):
            over = [i for i in range(k) if parts[i] > caps[i]]
            if not over:
                return parts
            i = over[0]
            spare = sorted((j for j in range(k) if parts[j] < caps[j]), key=lambda j: (parts[j], j))
            parts[i] -= 1
            parts[spare[0]] += 1
        return parts
    return None


def _runs(parts: list[int], start: float, pad_lo: float, pad_hi: float, pitch: float = GRID):
    """[(begin, end, cells, pad_before, pad_after)] in drawer coordinates."""
    out, pos = [], start
    for i, cells in enumerate(parts):
        lo = pad_lo if i == 0 else 0.0
        hi = pad_hi if i == len(parts) - 1 else 0.0
        size = lo + cells * pitch + hi
        out.append((pos, pos + size, cells, lo, hi))
        pos += size
    return out


def max_bin_units(height_mm: float | None, style: str, headroom: float = DEFAULT_HEADROOM) -> int | None:
    if not height_mm:
        return None
    floor_extra = PLATE_STYLES[style][1]
    room = height_mm - headroom - floor_extra - BIN_SEAT - LIP
    return max(0, floor(room / UNIT + EPS))


GRID_MODES = ("standard", "fit")  # 42 mm squares, or squares sized so the grid fills the drawer
PITCH_RANGE = (36.0, 48.0)          # fitted squares stay within this; outside it that direction keeps 42 mm


def fit_pitch(length: float) -> tuple[int, float]:
    """Squares along one direction of the plate for "Fit to drawer": the count whose size is closest to 42 mm."""
    best = None
    for n in {max(1, floor(length / GRID)), max(1, floor(length / GRID) + 1)}:
        p = length / n
        if PITCH_RANGE[0] <= p <= PITCH_RANGE[1] and (best is None or abs(p - GRID) < abs(best[1] - GRID)):
            best = (n, p)
    return best or (floor(length / GRID + EPS), GRID)


ALIGN_X = ("center", "left", "right")   # where the grid sits across the drawer; the filler goes on the other side(s)
ALIGN_Y = ("center", "back", "front")


def compute(width_mm: float, depth_mm: float, height_mm: float | None = None, style: str = "standard",
            magnets: bool = True, bed: Bed = Bed(), clearance: float = DEFAULT_CLEARANCE,
            headroom: float = DEFAULT_HEADROOM, align: tuple[str, str] = ("center", "center"),
            grid: str = "standard") -> dict:
    if style not in PLATE_STYLES:
        raise LayoutError(f"Unknown plate style: {style}")
    W, D = float(width_mm), float(depth_mm)
    if not (0 < W <= 1500 and 0 < D <= 1500) or (height_mm is not None and not 0 < height_mm <= 1500):
        raise LayoutError("Drawer sizes must be between 0 and 1500 mm.")
    pw, pd = W - clearance, D - clearance  # printed plate size
    grid = grid if grid in GRID_MODES else "standard"
    if grid == "fit":  # square size chosen per direction so the grid fills the plate
        (nx, gx), (ny, gy) = fit_pitch(pw), fit_pitch(pd)
    else:
        gx = gy = GRID
        nx, ny = floor(pw / GRID + EPS), floor(pd / GRID + EPS)
    if nx < 1 or ny < 1:
        raise LayoutError("The drawer is smaller than one 42 mm grid square (plus clearance).")
    px, py = max(0.0, (pw - nx * gx) / 2), max(0.0, (pd - ny * gy) / 2)  # printed filler per side when centered
    if px < 0.01:
        px = 0.0
    if py < 0.01:
        py = 0.0
    ax, ay = (align[0] if align[0] in ALIGN_X else "center"), (align[1] if align[1] in ALIGN_Y else "center")
    # filler before / after the grid on each axis (left/right, back/front): all of it on one side when pushed over
    fl, fr = {"center": (px, px), "left": (0.0, 2 * px), "right": (2 * px, 0.0)}[ax]
    fb, ff = {"center": (py, py), "back": (0.0, 2 * py), "front": (2 * py, 0.0)}[ay]

    best = None
    for lx, ly in ((bed.width, bed.depth), (bed.depth, bed.width)):
        cols, rows = _split_axis(nx, fl, fr, lx, gx), _split_axis(ny, fb, ff, ly, gy)
        if cols and rows and (best is None or len(cols) * len(rows) < len(best[0]) * len(best[1])):
            best = (cols, rows)
    if best is None:
        raise LayoutError("A single grid square plus filler doesn't fit the printer bed.")
    cols, rows = best

    c2 = clearance / 2
    xr, yr = _runs(cols, c2, fl, fr, gx), _runs(rows, c2, fb, ff, gy)
    pieces = []
    for ci, (x0, x1, cx, pl, pr) in enumerate(xr):
        for ri, (y0, y1, cy, pb, pf) in enumerate(yr):
            w, d = x1 - x0, y1 - y0
            land = w <= bed.width + EPS and d <= bed.depth + EPS
            rot = w <= bed.depth + EPS and d <= bed.width + EPS
            bw, bd = (bed.width, bed.depth) if land or not rot else (bed.depth, bed.width)
            pieces.append({
                "id": f"{chr(65 + ci)}{ri + 1}", "ci": ci, "ri": ri,
                "x": round(x0, 3), "y": round(y0, 3), "w": round(w, 3), "d": round(d, 3),
                "cx": cx, "cy": cy, "fits": land or rot, "turned": not land and rot,  # exported turned 90°
                "bw": bw, "bd": bd, "sx": round(bw - w, 3), "sy": round(bd - d, 3),
                "pad": {"left": round(pl, 3), "right": round(pr, 3), "back": round(pb, 3), "front": round(pf, 3)},
            })

    kp_style, floor_extra, has_magnets = PLATE_STYLES[style]
    bin_u = max_bin_units(height_mm, style, headroom)
    return {
        "W": W, "D": D, "H": height_mm,
        "nx": nx, "ny": ny,
        # square size [across, front-to-back]; 42 × 42 is standard Gridfinity
        "pitch": [round(gx, 4), round(gy, 4)], "grid": grid,
        # grid offset from the drawer wall (clearance + printed filler); the plan draws this as filler
        "fx": round(c2 + fl, 3), "fy": round(c2 + fb, 3), "align": {"x": ax, "y": ay},
        "cols": cols, "rows": rows, "pieces": pieces,
        "binU": bin_u,
        "bin_max_mm": None if bin_u is None else round(bin_u * UNIT + LIP, 3),
        "plate": {
            "width": round(pw, 3), "depth": round(pd, 3), "height": round(SOCKET_LAYER + floor_extra, 3),
            "filler_x": round(px, 3), "filler_y": round(py, 3), "clearance": clearance,
            "filler": {"left": round(fl, 3), "right": round(fr, 3), "back": round(fb, 3), "front": round(ff, 3)},
            "pitch": [round(gx, 4), round(gy, 4)],
            "style": style, "kennetek_style": kp_style, "magnets": bool(magnets and has_magnets),
            "magnets_supported": has_magnets, "magnet_count": nx * ny * 4 if magnets and has_magnets else 0,
        },
        "bed": {"width": bed.width, "depth": bed.depth, "name": bed.name},
        "headroom": headroom,
    }


def for_project(doc: dict, bed: Bed = Bed(), clearance: float = DEFAULT_CLEARANCE,
                headroom: float = DEFAULT_HEADROOM) -> dict | None:
    """Layout for a saved drawer, or None if it isn't measured yet (or can't take a grid)."""
    dr, bp = doc.get("drawer") or {}, doc.get("baseplate") or {}
    if not (dr.get("width_mm") and dr.get("depth_mm")):
        return None
    try:
        al = bp.get("align") or {}
        return compute(dr["width_mm"], dr["depth_mm"], dr.get("height_mm"), bp.get("style", "standard"),
                       bp.get("magnets", True), bed, clearance, headroom, (al.get("x", "center"), al.get("y", "center")),
                       bp.get("grid", "standard"))
    except LayoutError as e:
        return {"error": str(e)}
