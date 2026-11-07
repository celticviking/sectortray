"""Start SectorTray:  python -m sectortray  [--port 8742] [--data DIR] [--no-browser]"""
import argparse
import json
import os
import sys
import urllib.request
import webbrowser
from pathlib import Path

from . import __version__, paths
from .server import make_server

DEFAULT_PORT = 8742  # fixed, so browser-saved preferences (skin, units) stay put


def already_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "api/health", timeout=2) as r:
            return json.load(r).get("app") == "sectortray"
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(prog="sectortray", description="Offline Gridfinity drawer planner.")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--data", type=Path, default=None, help="where saved drawers live (default: Documents/SectorTray)")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = ap.parse_args()

    host = "127.0.0.1"
    url = f"http://{host}:{args.port}/"
    data_dir = (args.data or paths.default_data_dir()).resolve()
    try:
        server = make_server(host, args.port, data_dir)
        server.movable = not (args.data or os.environ.get("SECTORTRAY_DATA"))  # Settings may move the data folder
    except OSError:
        if already_running(url):
            print(f"SectorTray is already running at {url}")
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        print(f"Port {args.port} is in use by another program. Try: python -m sectortray --port 8743", file=sys.stderr)
        return 1

    print(f"SectorTray {__version__}")
    print(f"  Open:   {url}")
    print(f"  Drawers saved in: {data_dir}")
    if not paths.OPENSCAD.exists():
        print("  Note: OpenSCAD not found. Run: python tools/setup.py")
    print("  Close this window (or press Ctrl+C) to stop.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
