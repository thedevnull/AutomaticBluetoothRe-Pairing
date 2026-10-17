#!/usr/bin/env bash
# ----------------------------------------------------------------------
# Builds BluetoothDeviceControl (Qt GUI, single executable) on Linux.
# Put this file next to the GUI script and the core script, then run:
#     chmod +x build_linux.sh && ./build_linux.sh
# Output: dist/BluetoothDeviceControl
#
# Every setting can be overridden from the environment, for example:
#     USE_VENV=0 QT_PACKAGE=PyQt6 ./build_linux.sh
# ----------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

APP_NAME="${APP_NAME:-BluetoothDeviceControl}"
GUI="${GUI:-bt_device_control_gui.py}"
CORE="${CORE:-bluetooth_re-pairing.py}"
QT_PACKAGE="${QT_PACKAGE:-PySide6}"      # PySide6 or PyQt6
USE_VENV="${USE_VENV:-1}"                # 1 = private venv in .venv-build
ONEFILE="${ONEFILE:-1}"                  # 1 = single file, 0 = folder
SMOKE_TEST="${SMOKE_TEST:-1}"            # 1 = start the binary once with --help
EXTRA_ARGS="${EXTRA_ARGS:-}"             # extra PyInstaller options

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

# ---- Tools PyInstaller needs -----------------------------------------
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

# ---- Build dependencies ----------------------------------------------
info "Installing build dependencies (the first run takes a while) ..."
"$PYTHON" -m pip install --upgrade pip >/dev/null 2>&1 || true
"$PYTHON" -m pip install --upgrade pyinstaller "$QT_PACKAGE" \
    || die "pip failed. On Debian/Ubuntu keep USE_VENV=1 (system pip is locked)."

# ---- Build -------------------------------------------------------------
#  --add-data CORE:.    ships the core script next to the GUI, so it also
#                       works when it is loaded by file path
#  --hidden-import      asyncio/shutil/datetime are used by the core only
#  --exclude-module     PyInstaller aborts if two Qt bindings are installed
ARGS=(--noconfirm --clean
      --name "$APP_NAME"
      --distpath dist --workpath build
      --paths "$PWD"
      --add-data "$CORE:."
      --hidden-import asyncio --hidden-import shutil --hidden-import datetime
      --exclude-module "$OTHER_QT")
for m in QtWebEngineCore QtWebEngineWidgets QtQml QtQuick Qt3DCore \
         QtMultimedia QtCharts QtPdf; do
    ARGS+=(--exclude-module "$QT_PACKAGE.$m")
done
if [ "$ONEFILE" = "1" ]; then ARGS+=(--onefile); fi
if [ -n "$EXTRA_ARGS" ]; then
    # shellcheck disable=SC2206   # word splitting is intended here
    ARGS+=($EXTRA_ARGS)
fi

info "Building $APP_NAME ..."
"$PYTHON" -m PyInstaller "${ARGS[@]}" "$GUI"
rm -f "$APP_NAME.spec"

if [ "$ONEFILE" = "1" ]; then BIN="dist/$APP_NAME"; else BIN="dist/$APP_NAME/$APP_NAME"; fi
[ -x "$BIN" ] || die "Build failed - $BIN was not created. Read the messages above."
ok "Built: $PWD/$BIN ($(du -h "$BIN" | cut -f1))"

# ---- Smoke test --------------------------------------------------------
if [ "$SMOKE_TEST" = "1" ]; then
    info "Smoke test: starting the binary with --help ..."
    if "$BIN" --help >/dev/null 2>&1; then
        ok "The binary starts correctly."
    else
        warn "The binary failed to start. Run it in a terminal to see why: $BIN --help"
        warn "A common cause is a missing Qt system library. On Debian/Ubuntu try:"
        warn "  sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libxcb-icccm4 \\"
        warn "       libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \\"
        warn "       libxcb-shape0 libegl1 libgl1"
    fi
fi

cat <<EOF

Notes
  * Build on the oldest distro you want to support: the binary runs on the
    same or a newer glibc, not an older one.
  * The tray icon needs a system tray: on GNOME install the
    "AppIndicator and KStatusNotifierItem Support" extension.
  * If you enabled Start at login, the binary path is stored in
    ~/.config/autostart. If you move the binary, turn the option off and on.
EOF
