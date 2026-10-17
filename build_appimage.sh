#!/usr/bin/env bash
# ----------------------------------------------------------------------
# Builds BluetoothDeviceControl as an AppImage (Linux).
# Put this file next to the GUI script and the core script, then run:
#     chmod +x build_appimage.sh && ./build_appimage.sh
# Output: dist/BluetoothDeviceControl-<arch>.AppImage
#
# Every setting can be overridden from the environment, for example:
#     USE_VENV=0 QT_PACKAGE=PyQt6 ./build_appimage.sh
#     APPIMAGETOOL=/path/to/appimagetool ./build_appimage.sh   (offline use)
# ----------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

APP_NAME="${APP_NAME:-BluetoothDeviceControl}"
APP_TITLE="${APP_TITLE:-Bluetooth Device Control}"
GUI="${GUI:-bt_device_control_gui.py}"
CORE="${CORE:-bluetooth_re_pairing.py}"
QT_PACKAGE="${QT_PACKAGE:-PySide6}"      # PySide6 or PyQt6
USE_VENV="${USE_VENV:-1}"                # 1 = private venv in .venv-build
SMOKE_TEST="${SMOKE_TEST:-1}"            # 1 = start the AppImage once with --help
APPIMAGETOOL="${APPIMAGETOOL:-}"         # path to appimagetool (downloaded if empty)
EXTRA_ARGS="${EXTRA_ARGS:-}"             # extra PyInstaller options
ARCH="${ARCH:-$(uname -m)}"              # x86_64, aarch64, ...

ICON_NAME="bt-device-control"
APPDIR="build/AppDir"
OUT="dist/$APP_NAME-$ARCH.AppImage"

info() { printf '[*] %s\n' "$*"; }
ok()   { printf '[+] %s\n' "$*"; }
warn() { printf '[!] %s\n' "$*" >&2; }
die()  { printf '[x] %s\n' "$*" >&2; exit 1; }

if [ "$QT_PACKAGE" = "PyQt6" ]; then OTHER_QT="PySide6"; else OTHER_QT="PyQt6"; fi

# ---- Locate the scripts (auto-detect if the names above are wrong) ----
if [ ! -f "$GUI" ]; then
    GUI="$(grep -l 'QSystemTrayIcon' -- *.py 2>/dev/null | head -n1 || true)"
fi
if [ ! -f "$CORE" ]; then
    CORE="$(grep -l 'class WindowsBackend' -- *.py 2>/dev/null | head -n1 || true)"
fi
[ -n "$GUI" ]  && [ -f "$GUI" ]  || die "GUI script not found. Run with GUI=your_file.py"
[ -n "$CORE" ] && [ -f "$CORE" ] || die "Core script not found. Run with CORE=your_file.py"
info "GUI  : $GUI"
info "Core : $CORE"

# ---- Tools -------------------------------------------------------------
command -v python3 >/dev/null || die "python3 not found (sudo apt install python3)"
command -v objdump >/dev/null || die "objdump not found (sudo apt install binutils)"

PYTHON="python3"
if [ "$USE_VENV" = "1" ]; then
    if [ ! -x ".venv-build/bin/python" ]; then
        info "Creating virtual environment .venv-build ..."
        python3 -m venv .venv-build \
            || die "Could not create a venv (sudo apt install python3-venv)"
    fi
    PYTHON="$PWD/.venv-build/bin/python"
fi
info "Python: $("$PYTHON" --version) ($PYTHON)"

# ---- appimagetool ------------------------------------------------------
if [ -z "$APPIMAGETOOL" ]; then
    if command -v appimagetool >/dev/null; then
        APPIMAGETOOL="$(command -v appimagetool)"
    else
        APPIMAGETOOL=".tools/appimagetool-$ARCH.AppImage"
        if [ ! -x "$APPIMAGETOOL" ]; then
            URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
            info "Downloading appimagetool ..."
            mkdir -p .tools
            if command -v curl >/dev/null; then
                curl -fL --retry 3 -o "$APPIMAGETOOL" "$URL" || { rm -f "$APPIMAGETOOL"; die "Download failed. Get appimagetool manually and run with APPIMAGETOOL=/path/to/it"; }
            elif command -v wget >/dev/null; then
                wget -O "$APPIMAGETOOL" "$URL" || { rm -f "$APPIMAGETOOL"; die "Download failed. Get appimagetool manually and run with APPIMAGETOOL=/path/to/it"; }
            else
                die "Neither curl nor wget found. Install one, or run with APPIMAGETOOL=/path/to/appimagetool"
            fi
            chmod +x "$APPIMAGETOOL"
        fi
    fi
fi
[ -x "$APPIMAGETOOL" ] || die "appimagetool is not executable: $APPIMAGETOOL"
info "appimagetool: $APPIMAGETOOL"

# ---- Build dependencies ----------------------------------------------
info "Installing build dependencies (the first run takes a while) ..."
"$PYTHON" -m pip install --upgrade pip >/dev/null 2>&1 || true
"$PYTHON" -m pip install --upgrade pyinstaller "$QT_PACKAGE" \
    || die "pip failed. On Debian/Ubuntu keep USE_VENV=1 (system pip is locked)."

# ---- PyInstaller (folder mode: an AppImage is already a single file) ----
#  --add-data CORE:.    ships the core script next to the GUI
#  --hidden-import      asyncio/shutil/datetime are used by the core only
#  --exclude-module     PyInstaller aborts if two Qt bindings are installed
rm -rf build/pyi-dist build/pyi-work "$APPDIR"
ARGS=(--noconfirm --clean
      --name "$APP_NAME"
      --distpath build/pyi-dist --workpath build/pyi-work
      --specpath build
      --paths "$PWD"
      --add-data "$PWD/$CORE:."
      --hidden-import asyncio --hidden-import shutil --hidden-import datetime
      --exclude-module "$OTHER_QT")
for m in QtWebEngineCore QtWebEngineWidgets QtQml QtQuick Qt3DCore \
         QtMultimedia QtCharts QtPdf; do
    ARGS+=(--exclude-module "$QT_PACKAGE.$m")
done
if [ -n "$EXTRA_ARGS" ]; then
    # shellcheck disable=SC2206   # word splitting is intended here
    ARGS+=($EXTRA_ARGS)
fi

info "Building the program with PyInstaller ..."
"$PYTHON" -m PyInstaller "${ARGS[@]}" "$GUI"
BUNDLE="build/pyi-dist/$APP_NAME"
[ -x "$BUNDLE/$APP_NAME" ] || die "PyInstaller build failed. Read the messages above."

# ---- AppDir ------------------------------------------------------------
info "Assembling the AppDir ..."
mkdir -p "$APPDIR/usr/bin"
cp -a "$BUNDLE" "$APPDIR/usr/bin/$APP_NAME"

cat > "$APPDIR/AppRun" <<APPRUN
#!/bin/sh
HERE="\$(dirname "\$(readlink -f "\$0")")"
exec "\$HERE/usr/bin/$APP_NAME/$APP_NAME" "\$@"
APPRUN
chmod +x "$APPDIR/AppRun"

# Icon: a 256x256 PNG drawn with Qt (same artwork as the tray icon);
# if that fails, fall back to an SVG with the same drawing.
ICON_FILE="$ICON_NAME.png"
if ! QT_QPA_PLATFORM=offscreen "$PYTHON" - "$APPDIR/$ICON_FILE" >/dev/null 2>&1 <<'PY'
import sys
try:
    from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
    from PySide6.QtCore import Qt
except ImportError:
    from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
    from PyQt6.QtCore import Qt
app = QGuiApplication([])
pm = QPixmap(256, 256)
pm.fill(Qt.GlobalColor.transparent)
p = QPainter(pm)
p.setRenderHint(QPainter.RenderHint.Antialiasing)
p.scale(4, 4)
p.setPen(Qt.PenStyle.NoPen)
p.setBrush(QColor("#1976d2"))
p.drawEllipse(2, 2, 60, 60)
pen = QPen(QColor("white"))
pen.setWidth(5)
pen.setCapStyle(Qt.PenCapStyle.RoundCap)
pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
p.setPen(pen)
p.setBrush(Qt.BrushStyle.NoBrush)
path = QPainterPath()
path.moveTo(23, 23)
for x, y in ((41, 41), (32, 50), (32, 14), (41, 23), (23, 41)):
    path.lineTo(x, y)
p.drawPath(path)
p.end()
sys.exit(0 if pm.save(sys.argv[1], "PNG") else 1)
PY
then
    warn "Could not render the PNG icon; using an SVG icon instead."
    ICON_FILE="$ICON_NAME.svg"
    cat > "$APPDIR/$ICON_FILE" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 64 64">
  <circle cx="32" cy="32" r="30" fill="#1976d2"/>
  <polyline points="23,23 41,41 32,50 32,14 41,23 23,41" fill="none" stroke="#fff"
            stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
SVG
fi
ln -sf "$ICON_FILE" "$APPDIR/.DirIcon"

cat > "$APPDIR/$ICON_NAME.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=$APP_TITLE
Comment=Re-pair Bluetooth devices from the tray
Exec=$APP_NAME
Icon=$ICON_NAME
Categories=Utility;
Terminal=false
DESKTOP

# ---- Pack ----------------------------------------------------------------
info "Creating the AppImage ..."
mkdir -p dist
rm -f "$OUT"
# APPIMAGE_EXTRACT_AND_RUN lets appimagetool run even without FUSE installed
APPIMAGE_EXTRACT_AND_RUN=1 ARCH="$ARCH" "$APPIMAGETOOL" "$APPDIR" "$OUT" \
    || die "appimagetool failed. Read the messages above."
[ -f "$OUT" ] || die "Build failed - $OUT was not created."
chmod +x "$OUT"
ok "Built: $PWD/$OUT ($(du -h "$OUT" | cut -f1))"

# ---- Smoke test ------------------------------------------------------------
if [ "$SMOKE_TEST" = "1" ]; then
    info "Smoke test: starting the AppImage with --help ..."
    if APPIMAGE_EXTRACT_AND_RUN=1 "$OUT" --help >/dev/null 2>&1; then
        ok "The AppImage starts correctly."
    else
        warn "The AppImage failed to start. Run it in a terminal to see why: $OUT --help"
        warn "A common cause is a missing Qt system library. On Debian/Ubuntu try:"
        warn "  sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libxcb-icccm4 \\"
        warn "       libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \\"
        warn "       libxcb-shape0 libegl1 libgl1"
    fi
fi

cat <<EOT

Notes
  * Build on the oldest distro you want to support: the AppImage runs on the
    same or a newer glibc, not an older one.
  * Running an AppImage needs FUSE 2. On Ubuntu 22.04+ install it with
    "sudo apt install libfuse2" (libfuse2t64 on 24.04), or run the file with
    --appimage-extract-and-run.
  * BlueZ (bluetoothctl) must be installed on the system; it is not bundled.
  * The tray icon needs a system tray: on GNOME install the
    "AppIndicator and KStatusNotifierItem Support" extension.
  * Start at login stores the path of the .AppImage file in
    ~/.config/autostart. If you move or rename it, turn the option off and on.
EOT
