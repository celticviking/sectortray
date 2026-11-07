"""Binary STL helpers: read triangles, bounding box, and save a copy turned 90° (so a part that only fits the
printer bed sideways opens in the slicer ready to print)."""
import os
import struct
from pathlib import Path

RECORD = struct.Struct("<12fH")  # normal, 3 vertices, attribute


def _body(data: bytes) -> tuple[int, memoryview]:
    if len(data) < 84:
        raise ValueError("Not a binary STL (too short).")
    n = struct.unpack_from("<I", data, 80)[0]
    if 84 + n * RECORD.size != len(data):
        raise ValueError("Not a binary STL (size doesn't match the triangle count).")
    return n, memoryview(data)[84:]


def triangles(path: Path):
    """Yields each triangle as 9 floats (x1 y1 z1 x2 … z3)."""
    _, body = _body(Path(path).read_bytes())
    for r in RECORD.iter_unpack(body):
        yield r[3:12]


def bbox(path: Path) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    lo, hi = [float("inf")] * 3, [float("-inf")] * 3
    for t in triangles(path):
        for i in range(9):
            a = i % 3
            if t[i] < lo[a]:
                lo[a] = t[i]
            if t[i] > hi[a]:
                hi[a] = t[i]
    return tuple(lo), tuple(hi)


def volume(path: Path) -> float:
    """Enclosed volume in mm³ (sum of signed tetrahedra; the STL must be closed, as OpenSCAD's are)."""
    v = 0.0
    for x1, y1, z1, x2, y2, z2, x3, y3, z3 in triangles(path):
        v += x1 * (y2 * z3 - z2 * y3) - y1 * (x2 * z3 - z2 * x3) + z1 * (x2 * y3 - y2 * x3)
    return abs(v) / 6


def copy_turned(src: Path, dst: Path) -> None:
    """Writes `src` turned 90° about Z (x, y → −y, x) to `dst`, atomically."""
    data = Path(src).read_bytes()
    n, body = _body(data)
    out = bytearray(data[:84])
    for r in RECORD.iter_unpack(body):
        out += RECORD.pack(-r[1], r[0], r[2], -r[4], r[3], r[5], -r[7], r[6], r[8], -r[10], r[9], r[11], r[12])
    tmp = Path(dst).with_name(Path(dst).name + f".{os.getpid()}.tmp")
    tmp.write_bytes(out)
    os.replace(tmp, dst)
