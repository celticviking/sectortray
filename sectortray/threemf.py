"""3MF files for the slicer: every part under its own name, as many copies as needed, laid out on the printer bed.

A 3MF is a zip holding [Content_Types].xml, _rels/.rels and 3D/3dmodel.model: the meshes as XML, plus a <build>
list saying where each copy goes. Plain 3MF (no slicer settings inside), so any slicer opens it.
PrusaSlicer 2.9 has virtual beds side by side; parts that don't fit one bed go on the next one, placed where
PrusaSlicer puts that bed (MultipleBeds::get_bed_translation).
"""
import math
import os
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from . import __version__, stl

GAP = 8.0     # mm between parts on the bed
MARGIN = 5.0  # mm kept clear along the bed's edges

CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
RELS = ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        '</Relationships>')


def grid_coords(i: int) -> tuple[int, int]:
    """Bed number → (column, row) the way PrusaSlicer 2.9 lays out beds: square rings, 0 (0,0), 1 (1,0), 2 (0,1),
    3 (1,1), 4 (2,0), 5 (2,1), 6 (0,2)…"""
    k = math.isqrt(i)
    j = i - k * k
    return (k, j) if j < k else (j - k, k)


def bed_offset(i: int, bed) -> tuple[float, float]:
    bw, bd = bed
    gap = min(100.0, math.hypot(bw, bd) * 0.3)
    x, y = grid_coords(i)
    return x * (bw + gap), y * (bd + gap)


def _mesh(path: Path):
    """(vertices, triangles, size) with the vertices centered on X/Y and resting on Z 0."""
    index, verts, tris = {}, [], []
    for t in stl.triangles(path):
        ids = []
        for k in (0, 3, 6):
            v = t[k:k + 3]
            i = index.get(v)
            if i is None:
                i = index[v] = len(verts)
                verts.append(v)
            ids.append(i)
        if ids[0] != ids[1] and ids[1] != ids[2] and ids[0] != ids[2]:
            tris.append(ids)
    if not verts:
        raise ValueError(f"{Path(path).name} has no triangles.")
    lo = [min(v[a] for v in verts) for a in range(3)]
    hi = [max(v[a] for v in verts) for a in range(3)]
    c = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2])
    return [(x - c[0], y - c[1], z - c[2]) for x, y, z in verts], tris, (hi[0] - lo[0], hi[1] - lo[1])


def pack(sizes: list[tuple[float, float]], bed) -> list[tuple[int, float, float]]:
    """Shelf packing over as many beds as it takes: parts deepest first, each into the first shelf (on any bed)
    with room, else a new shelf, else a new bed. Returns (bed, x, y) of each part's front-left corner inside the
    usable area. A part bigger than the bed gets a bed of its own."""
    uw, ud = bed[0] - 2 * MARGIN, bed[1] - 2 * MARGIN
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], -sizes[i][0]))
    out, beds = [None] * len(sizes), []  # bed: {"shelves": [[y, depth, x used]], "top": next shelf's y}
    for i in order:
        w, d = sizes[i]
        spot = None
        for b, B in enumerate(beds):
            shelf = next((sh for sh in B["shelves"] if d <= sh[1] + 1e-6 and sh[2] + w <= uw + 1e-6), None)
            if shelf:
                spot = (b, shelf[2], shelf[0])
                shelf[2] += w + GAP
                break
            if B["top"] + d <= ud + 1e-6 and w <= uw + 1e-6:
                spot = (b, 0.0, B["top"])
                B["shelves"].append([B["top"], d, w + GAP])
                B["top"] += d + GAP
                break
        if spot is None:
            beds.append({"shelves": [[0.0, d, w + GAP]], "top": d + GAP})
            spot = (len(beds) - 1, 0.0, 0.0)
        out[i] = spot
    return out


def arrange(sizes: list[tuple[float, float]], bed, groups: list[int] | None = None) -> list[tuple[int, float, float]]:
    """(bed, center x, center y) of each part, in PrusaSlicer's coordinates (beds after the first sit where
    PrusaSlicer 2.9 puts them), each bed's parts centered on it. `groups` (one number per part) keeps parts of
    different groups (the Print List's plates) on different beds, in group order."""
    bw, bd = bed
    groups = groups or [0] * len(sizes)
    out, first = [None] * len(sizes), 0
    for g in sorted(set(groups)):
        idx = [i for i in range(len(sizes)) if groups[i] == g]
        spots = pack([sizes[i] for i in idx], bed)
        for b in sorted({s[0] for s in spots}):
            placed = [(idx[k], s[1], s[2]) for k, s in enumerate(spots) if s[0] == b]
            x1 = max(px + sizes[i][0] for i, px, _ in placed)
            y1 = max(py + sizes[i][1] for i, _, py in placed)
            ox, oy = bed_offset(first + b, bed)
            sx, sy = ox + (bw - x1) / 2, oy + (bd - y1) / 2
            for i, px, py in placed:
                out[i] = (first + b, round(sx + px + sizes[i][0] / 2, 3), round(sy + py + sizes[i][1] / 2, 3))
        first += 1 + max(s[0] for s in spots)
    return out


def write(path: Path, objects: list[dict], bed, title: str = "") -> dict:
    """objects: [{name, stl, copies, plates?}] where plates (one number per copy) puts copies on separate beds
    by plate. Writes the 3MF atomically. Returns {beds, copies}."""
    meshes = [_mesh(o["stl"]) for o in objects]
    copies, groups = [], []
    for n, (o, m) in enumerate(zip(objects, meshes)):
        k = max(1, int(o.get("copies", 1)))
        copies += [(n, m[2])] * k
        groups += list(o.get("plates") or [0] * k)
    spots = arrange([size for _, size in copies], bed, groups)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        with z.open("3D/3dmodel.model", "w") as f:
            def put(s):
                f.write(s.encode("utf-8"))
            put('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" '
                'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n')
            if title:
                put(f'<metadata name="Title">{escape(title)}</metadata>\n')
            put(f'<metadata name="Application">SectorTray {escape(__version__)}</metadata>\n<resources>\n')
            for n, (o, (verts, tris, _)) in enumerate(zip(objects, meshes)):
                put(f'<object id="{n + 1}" type="model" name={quoteattr(o["name"])}><mesh><vertices>\n')
                for i in range(0, len(verts), 2000):
                    put("".join(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>\n' for x, y, z in verts[i:i + 2000]))
                put('</vertices><triangles>\n')
                for i in range(0, len(tris), 2000):
                    put("".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>\n' for a, b, c in tris[i:i + 2000]))
                put('</triangles></mesh></object>\n')
            put('</resources>\n<build>\n')
            for (n, _), (_, cx, cy) in zip(copies, spots):
                put(f'<item objectid="{n + 1}" transform="1 0 0 0 1 0 0 0 1 {cx} {cy} 0"/>\n')
            put('</build>\n</model>\n')
    os.replace(tmp, path)
    return {"beds": 1 + max(b for b, _, _ in spots), "copies": len(copies)}
