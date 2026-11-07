"""Run the vendored OpenSCAD CLI (Manifold backend) and collect its ECHO output."""
import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

ECHO_RE = re.compile(r'^ECHO: "?(.*?)"?\s*$')
NUM_RE = re.compile(r"\(in mm\):\s*(-?[\d.]+)")
CREATE_NO_WINDOW = 0x08000000  # don't flash a console window per render on Windows


class OpenSCADError(RuntimeError):
    def __init__(self, message: str, log: str = ""):
        super().__init__(message)
        self.log = log


@dataclass
class RenderResult:
    path: Path
    seconds: float
    echo: list[str] = field(default_factory=list)
    log: str = ""


def _fmt(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(round(v, 4))
    return str(v)


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def path_lock(path: Path) -> threading.Lock:
    """One lock per output file. Two requests for the same cached render (two bins with identical settings,
    two identical baseplate pieces) take turns: the second finds the first one's file instead of racing it."""
    with _locks_guard:
        return _locks.setdefault(os.path.normcase(os.path.abspath(path)), threading.Lock())  # not resolve(): its spelling can change once the folder exists


def render(scad: Path, params: dict, out: Path, timeout: float = 300) -> RenderResult:
    """Render `scad` with `-D` overrides to a binary STL at `out` (written atomically)."""
    if not paths.OPENSCAD.exists():
        raise OpenSCADError("SectorTray's 3D engine (OpenSCAD) is missing, so nothing can be rendered. "
                            + ("Run: python tools/setup.py" if paths.DEV else
                               "Reinstall SectorTray. If your antivirus removed openscad.exe, allow it first."))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.stem}.{uuid.uuid4().hex[:8]}.part.stl")  # unique: never shared with another render
    cmd = [str(paths.OPENSCAD), "--backend=Manifold", "--export-format=binstl", "-o", str(tmp)]
    for k, v in params.items():
        cmd += ["-D", f"{k}={_fmt(v)}"]
    cmd.append(str(scad))
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(scad.parent),
                              creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    except subprocess.TimeoutExpired:
        tmp.unlink(missing_ok=True)
        raise OpenSCADError(f"OpenSCAD took longer than {timeout:.0f} s and was stopped.")
    dt = time.perf_counter() - t0
    log = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        tail = "\n".join(log.strip().splitlines()[-6:])
        raise OpenSCADError(f"OpenSCAD failed (exit {proc.returncode}). {tail}", log)
    os.replace(tmp, out)
    echo = [m.group(1) for line in log.splitlines() if (m := ECHO_RE.match(line.strip()))]
    return RenderResult(out, dt, echo, log)


def prune_cache(cache: Path, prefix: str, keep: int) -> None:
    """Keeps the `keep` most recently used `<prefix>_*.stl` previews (and their .json). Safe while other renders
    run: their in-progress `.part.stl` files are left alone, and files that vanish meanwhile are skipped."""
    found = []
    for f in cache.glob(f"{prefix}_*.stl"):
        if f.name.endswith(".part.stl"):
            continue
        try:
            found.append((f.stat().st_mtime, f))
        except FileNotFoundError:
            pass
    found.sort(key=lambda e: e[0], reverse=True)
    for _, f in found[keep:]:
        f.unlink(missing_ok=True)
        f.with_suffix(".json").unlink(missing_ok=True)


def echo_padding(echo: list[str]) -> dict:
    """kennetek baseplate ECHO → {'+X': mm, '-X': mm, '+Y': mm, '-Y': mm} (missing → 0)."""
    pad = {"+X": 0.0, "-X": 0.0, "+Y": 0.0, "-Y": 0.0}
    for line in echo:
        for key in pad:
            if line.startswith(f"Padding {key}"):
                m = NUM_RE.search(line)
                if m:
                    pad[key] = float(m.group(1))
    return pad
