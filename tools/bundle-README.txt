SectorTray {version}
================

Offline Gridfinity drawer planner: measure a drawer, design bins and silverware trays,
export STL or 3MF files and open them in your slicer. No telemetry of any kind.

Start
-----
Double-click SectorTray.exe. (Tip: right-click it > Show more options > Send to > Desktop
(create shortcut) for a desktop icon.)

The first time, Windows may say "Windows protected your PC" because the program isn't signed.
Click "More info", then "Run anyway".

Currently requires Windows 10 or 11 (64-bit) with the Microsoft Edge WebView2 Runtime, which Windows 11
already has. If it's missing, SectorTray offers to open in your web browser instead, or get the
runtime from https://developer.microsoft.com/microsoft-edge/webview2/ ("Evergreen Bootstrapper").

Your files
----------
Saved drawers and exported STLs:  Documents\SectorTray
Window and browser cache:         %LOCALAPPDATA%\SectorTray  (safe to delete)
To keep your drawers somewhere else: Settings > About > Saved drawers > Move.

Uninstall: if you used the installer, Windows Settings > Apps > Installed apps > SectorTray > Uninstall.
Otherwise delete this folder. Either way your drawers stay in Documents\SectorTray until you delete it.

Slicer: SectorTray finds PrusaSlicer, Bambu Studio, OrcaSlicer or UltiMaker Cura in their standard
install folders (pick yours in Settings > Printer). If yours is elsewhere, set its location there.

Gridfinity is the open modular storage system created by Zack Freedman (https://gridfinity.xyz/).
SectorTray is an independent tool for it.

SectorTray is free software under the GNU General Public License v3.0 or later (LICENSES\SectorTray-LICENSE.txt).
It bundles OpenSCAD (GPL), Gridfinity Rebuilt (MIT), Gridfinity Extended (GPL-3.0), three.js (MIT),
Python (PSF) and open-license fonts (OFL); see LICENSES\NOTICE.txt.
