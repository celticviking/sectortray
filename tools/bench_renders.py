"""Times representative SectorTray renders with OpenSCAD's Manifold backend.

Usage:  python tools/bench_renders.py
Writes STLs + logs to out/bench/ and prints one line per case.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V = ROOT / "vendor"
OS = V / "openscad" / "openscad.com"
R = V / "gridfinity-rebuilt-openscad"
E = V / "gridfinity_extended_openscad"
OUT = ROOT / "out" / "bench"
OUT.mkdir(parents=True, exist_ok=True)

BINS = R / "gridfinity-rebuilt-bins.scad"
PLATE = R / "gridfinity-rebuilt-baseplate.scad"

CASES = [
    ("bin_1x1x3", BINS, {"gridx": 1, "gridy": 1, "gridz": 3}),
    ("bin_2x3x6_div3x2", BINS, {"gridx": 2, "gridy": 3, "gridz": 6, "divx": 3, "divy": 2}),
    ("bin_5x5x6", BINS, {"gridx": 5, "gridy": 5, "gridz": 6}),
    ("plate_4x4_thin", PLATE, {"gridx": 4, "gridy": 4, "style_plate": 0, "enable_magnet": "false"}),
    ("plate_4x4_weighted_magnets", PLATE, {"gridx": 4, "gridy": 4, "style_plate": 1, "enable_magnet": "true"}),
    ("plate_5x5_screwtogether_magnets", PLATE, {"gridx": 5, "gridy": 5, "style_plate": 3, "enable_magnet": "true"}),
    # Stress test: entire 14 3/8" x 19 1/2" drawer as ONE plate (too big for the bed).
    ("plate_full_drawer_8x11", PLATE, {"gridx": 0, "gridy": 0, "distancex": 365.1, "distancey": 495.3,
                                       "style_plate": 3, "enable_magnet": "true"}),
    ("ext_silverware_default", E / "gridfinity_silverware.scad", {}),
    ("ext_basic_cup_default", E / "gridfinity_basic_cup.scad", {}),
]

results = []
for name, scad, params in CASES:
    out = OUT / f"{name}.stl"
    out.unlink(missing_ok=True)
    cmd = [str(OS), "--backend=Manifold", "--export-format=binstl", "-o", str(out)]
    for k, v in params.items():
        cmd += ["-D", f"{k}={v}"]
    cmd.append(str(scad))
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    dt = time.perf_counter() - t0
    size_kb = out.stat().st_size / 1024 if out.exists() else 0
    (OUT / f"{name}.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    ok = proc.returncode == 0 and size_kb > 0
    print(f"{name:34s} {'OK ' if ok else 'ERR'} {dt:7.2f}s {size_kb:9.1f} KB", flush=True)
    results.append({"name": name, "ok": ok, "seconds": round(dt, 2), "kb": round(size_kb, 1)})

(OUT / "results.json").write_text(json.dumps(results, indent=2))
sys.exit(0 if all(r["ok"] for r in results) else 1)
