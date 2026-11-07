"""Splitting a bin that's too big for the printer bed into pieces that join back together.

The bin is cut on grid lines (so every piece still sits in whole baseplate squares), into the fewest
balanced pieces that fit the bed, either way round. Joints (`bin.split.joint`):
    none   pieces just sit side by side; the baseplate keeps them aligned
    clips  each seam gets a plate on the inside of each wall it crosses, fused to one piece and reaching
           past the seam along the other piece's wall (one per wall, alternating owner)
    keys   bow-tie keys printed separately, pressed into flush pockets in the floor across the seam

Positions are in the whole bin's STL coordinates: kennetek centers the bin on X/Y, so grid line k on the X
axis is at x = -w·21 + 42·k, and the floor of the feet is Z 0. scad/split_bin.scad draws what plan() gives.
"""
from math import ceil

PITCH = 42.0
WALL = 0.95          # kennetek wall below the lip (infill = outer − 2·0.95)
FLOOR_Z = 7.0        # top of the solid floor (kennetek BASE_HEIGHT)
SEAM = 0.1
CLIP_IN, CLIP_OUT, CLIP_MAX_H, CLIP_MIN_H = 6.0, 8.0, 12.0, 3.0
KEY_LEN, KEY_END = 18.0, 10.0
JOINTS = ("none", "clips", "keys")


def pitch_of(b: dict) -> tuple[float, float]:
    """Square size [across, front-to-back] the bin is made for: its drawer's grid (set by the server as _pitch)."""
    p = b.get("_pitch") if isinstance(b, dict) else None
    return (float(p[0]), float(p[1])) if p else (PITCH, PITCH)


def fits(w: int, d: int, bed, pitch=(PITCH, PITCH)) -> bool:
    W, D = w * pitch[0] - 0.5, d * pitch[1] - 0.5
    bw, bd = bed
    return (W <= bw and D <= bd) or (W <= bd and D <= bw)


def balanced(n: int, k: int) -> list[int]:
    """n squares in k runs as equal as possible, larger runs first: 7, 2 → [4, 3]."""
    base, extra = divmod(n, k)
    return [base + 1] * extra + [base] * (k - extra)


def auto_runs(w: int, d: int, bed, pitch=(PITCH, PITCH)) -> tuple[list[int], list[int]]:
    """Fewest pieces (then the squarest) whose largest piece fits the bed."""
    best = None
    for kx in range(1, w + 1):
        for ky in range(1, d + 1):
            if not fits(ceil(w / kx), ceil(d / ky), bed, pitch):
                continue
            key = (kx * ky, abs(kx - ky))
            if best is None or key < best[0]:
                best = (key, kx, ky)
            break  # more pieces along y won't beat this kx
    if best is None:  # even one square doesn't fit: nothing sensible to do
        return [1] * w, [1] * d
    return balanced(w, best[1]), balanced(d, best[2])


def clean_runs(runs, n: int) -> list[int] | None:
    """Validated runs along one axis (whole squares adding up to n), or None for automatic."""
    if runs is None:
        return None
    if not isinstance(runs, list) or not runs or not all(isinstance(r, int) and r >= 1 for r in runs) or sum(runs) != n:
        raise ValueError(f"Pieces must be whole squares adding up to {n}.")
    return runs


def runs_of(b: dict, bed) -> tuple[list[int], list[int]]:
    s = b.get("split") or {}
    ax, ay = auto_runs(b["w"], b["d"], bed, pitch_of(b))
    rx = s.get("x") if s.get("x") and sum(s["x"]) == b["w"] else ax
    ry = s.get("y") if s.get("y") and sum(s["y"]) == b["d"] else ay
    return rx, ry


def is_split(b: dict, bed) -> bool:
    if b.get("kind") == "tray" or not (b.get("split") or {}).get("on"):
        return False
    rx, ry = runs_of(b, bed)
    return len(rx) * len(ry) > 1


def cavity(b: dict) -> list[float]:
    """Inside of a kennetek bin [x, y, z]; matches the infill OpenSCAD reports (e.g. 3u with lip: z 12.8)."""
    gx, gy = pitch_of(b)
    return [b["w"] * gx - 0.5 - 2 * WALL, b["d"] * gy - 0.5 - 2 * WALL, b["h"] * 7 - (8.2 if b.get("lip", True) else 7.0)]


def plan(b: dict, bed) -> dict:
    """Pieces with their box / clips / pockets, the number of keys, and notes about joints that can't be made."""
    w, d = b["w"], b["d"]
    cav = cavity(b)
    joint = (b.get("split") or {}).get("joint", "clips")
    rx, ry = runs_of(b, bed)
    gx, gy = pitch_of(b)
    X = lambda k: -w * gx / 2 + gx * k
    Y = lambda k: -d * gy / 2 + gy * k
    ix_in = [-cav[0] / 2, cav[0] / 2]   # inner wall faces
    iy_in = [-cav[1] / 2, cav[1] / 2]
    cx = [sum(rx[:i]) for i in range(len(rx) + 1)]   # grid lines between columns of pieces
    cy = [sum(ry[:i]) for i in range(len(ry) + 1)]
    divx = [ix_in[0] + k * cav[0] / b["divx"] for k in range(1, b["divx"])]
    divy = [iy_in[0] + k * cav[1] / b["divy"] for k in range(1, b["divy"])]
    z0 = FLOOR_Z + 0.8
    z1 = min(FLOOR_Z + cav[2] - 0.8, z0 + CLIP_MAX_H)
    notes = set()

    pieces = {}
    for iy in range(len(ry)):
        for ix in range(len(rx)):
            box = [-1e4 if ix == 0 else X(cx[ix]) + SEAM, 1e4 if ix == len(rx) - 1 else X(cx[ix + 1]) - SEAM,
                   -1e4 if iy == 0 else Y(cy[iy]) + SEAM, 1e4 if iy == len(ry) - 1 else Y(cy[iy + 1]) - SEAM]
            pieces[(ix, iy)] = {"box": box, "clips": [], "pockets": [], "squares": [rx[ix], ry[iy]]}

    def blocked(c, lines):  # a divider sits on the cut
        return any(abs(c - L) < KEY_LEN / 2 + 1 for L in lines)

    def key_spots(k0, k1, lo, hi, G, lines):
        out = []
        for j in range(k0, k1):
            p = (G(j) + G(j + 1)) / 2
            if p - KEY_END / 2 - 3 < lo or p + KEY_END / 2 + 3 > hi:
                continue
            if any(abs(p - L) < KEY_END / 2 + 1 for L in lines):
                continue
            out.append(round(p, 3))
        return out

    keys = 0
    # Seams on x = const (between columns ix and ix+1), one segment per row of pieces
    for ix in range(len(rx) - 1):
        c = X(cx[ix + 1])
        on_divider = blocked(c, divx)
        for iy in range(len(ry)):
            a, bb = pieces[(ix, iy)], pieces[(ix + 1, iy)]
            if joint == "clips":
                if on_divider:
                    notes.add("A divider sits on a cut, so clips can't reach across it there.")
                elif z1 - z0 < CLIP_MIN_H:
                    notes.add("This bin is too shallow for clips.")
                else:
                    if iy == 0:
                        a["clips"].append([0, c, iy_in[0], 1, 1, z0, z1])
                    if iy == len(ry) - 1:
                        bb["clips"].append([0, c, iy_in[1], -1, -1, z0, z1])
            if joint == "keys":
                if on_divider:
                    notes.add("A divider sits on a cut, so there's no floor there for keys.")
                    continue
                spots = key_spots(cy[iy], cy[iy + 1], iy_in[0], iy_in[1], Y, divy)
                for p in spots:
                    a["pockets"].append([0, c, p]); bb["pockets"].append([0, c, p])
                keys += len(spots)
    # Seams on y = const (between rows iy and iy+1)
    for iy in range(len(ry) - 1):
        c = Y(cy[iy + 1])
        on_divider = blocked(c, divy)
        for ix in range(len(rx)):
            a, bb = pieces[(ix, iy)], pieces[(ix, iy + 1)]
            if joint == "clips":
                if on_divider:
                    notes.add("A divider sits on a cut, so clips can't reach across it there.")
                elif z1 - z0 < CLIP_MIN_H:
                    notes.add("This bin is too shallow for clips.")
                else:
                    if ix == 0:
                        a["clips"].append([1, c, ix_in[0], 1, 1, z0, z1])
                    if ix == len(rx) - 1:
                        bb["clips"].append([1, c, ix_in[1], -1, -1, z0, z1])
            if joint == "keys":
                if on_divider:
                    notes.add("A divider sits on a cut, so there's no floor there for keys.")
                    continue
                spots = key_spots(cx[ix], cx[ix + 1], ix_in[0], ix_in[1], X, divx)
                for p in spots:
                    a["pockets"].append([1, c, p]); bb["pockets"].append([1, c, p])
                keys += len(spots)

    # Number pieces in reading order from the back left (high y first), like the Layout grid
    order = sorted(pieces, key=lambda k: (-k[1], k[0]))
    out = []
    for n, k in enumerate(order, 1):
        p = pieces[k]
        out.append({"id": f"p{n}", "n": n, "col": k[0], "row": len(ry) - 1 - k[1], **p})
    return {"pieces": out, "keys": keys, "x": rx, "y": ry, "joint": joint, "notes": sorted(notes)}
