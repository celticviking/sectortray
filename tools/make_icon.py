"""Build the Windows icon files from the Claude Design icon handoff (design/icon-handoff/icon/png).

    python tools/make_icon.py
Writes host/SectorTray.ico (16–256 px, for the exe, window and taskbar) and web/favicon.ico (16/32/48).
ICO files may hold PNG images directly (Windows Vista and later), so no image library is needed.
"""
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PNG = ROOT / "design" / "icon-handoff" / "icon" / "png"


def ico(sizes, out: Path):
    images = [(n, (PNG / f"icon-{n}.png").read_bytes()) for n in sizes]
    for n, data in images:
        assert data[:8] == b"\x89PNG\r\n\x1a\n", f"icon-{n}.png isn't a PNG"
        w, h = struct.unpack(">II", data[16:24])
        assert (w, h) == (n, n), f"icon-{n}.png is {w}×{h}"
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for n, data in images:
        dim = 0 if n >= 256 else n  # 0 means 256 in the directory entry
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    out.write_bytes(header + entries + blobs)
    print(f"{out.relative_to(ROOT)}: {', '.join(str(n) for n in sizes)} px, {out.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    ico([16, 20, 24, 32, 40, 48, 64, 128, 256], ROOT / "host" / "SectorTray.ico")
    ico([16, 32, 48], ROOT / "web" / "favicon.ico")
