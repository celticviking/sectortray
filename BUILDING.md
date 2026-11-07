# Building SectorTray

For running SectorTray from its source code, changing it, or making your own release. To just use it, download the installer from [Releases](https://github.com/celticviking/sectortray/releases).

## Run from source (Windows)

Needs Python 3.11 or newer (standard library only) and git.

```bash
git clone --recurse-submodules https://github.com/celticviking/sectortray.git
```

```bash
python tools/setup.py
```

That fetches the two Gridfinity libraries (git submodules) and the pinned OpenSCAD build into `vendor/`, with a SHA-256 check.

Then double-click **`SectorTray.cmd`**, which opens SectorTray in your browser at <http://127.0.0.1:8742/>. Close the console window to stop it.

## The desktop window

`host/` is **SectorTray.exe**, a small C# launcher that shows SectorTray in its own window (Microsoft Edge WebView2) and starts and stops the server. It targets .NET Framework 4.8 (part of Windows) and needs the .NET SDK to build:

```bash
dotnet build host -c Release
```

Run `host/bin/Release/net48/SectorTray.exe`: it finds the repository and uses `python` from PATH (or `SECTORTRAY_PYTHON`).

## Release build

```bash
python tools/build_dist.py
```

This makes, in `out/dist/`:

- `SectorTray/`: the portable app (embedded Python, OpenSCAD, the libraries, licenses).
- `SectorTray-<version>-win64.zip`: that folder zipped.
- `SectorTray-<version>-setup.exe`: a per-user installer, if [Inno Setup 6](https://jrsoftware.org/isinfo.php) is installed (`tools/installer.iss`).

It downloads the Python embeddable package once (SHA-256 pinned, cached in `vendor/_downloads`). The version comes from `sectortray/__init__.py`; keep `host/SectorTray.csproj` in step. Publish the SHA-256 of each file with the release.

## Develop

```bash
python -m sectortray --no-browser --port 8750 --data out/dev-data
```

```bash
python -m unittest discover -s tests -t .
```

Open pages at `http://127.0.0.1:8750/` rather than `localhost` (on Windows `localhost` tries IPv6 first, which adds a delay to every request).

- `sectortray/`: the local server (standard-library `http.server`, 127.0.0.1 only), drawer storage, layout maths, OpenSCAD runner, exports, 3MF writer.
- `web/`: the interface, plain HTML/CSS/JavaScript, with three.js in `web/vendor/three/`.
- `vendor/`: OpenSCAD (fetched by setup, not in git) and the Gridfinity libraries (git submodules).
- `host/`: the desktop window. `tools/`: setup, release build, installer script, icon maker.
- `guide/`: the first-drawer guide and its pictures.

The server only listens on 127.0.0.1, checks the Host and Origin headers, and requires JSON for every change, so web pages can't drive it.
