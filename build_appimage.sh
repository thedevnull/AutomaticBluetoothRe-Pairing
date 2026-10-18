#!/usr/bin/env bash
# ----------------------------------------------------------------------
# Builds BluetoothDeviceControl as an AppImage (Linux).
# Put this file next to the GUI script, the core script and
# make_app_icon.py, then run:
#     chmod +x build_appimage.sh && ./build_appimage.sh
# Output: dist/BluetoothDeviceControl-<arch>.AppImage
#
# For a build that is compatible with older distros (and passes the
# AppImageHub checks) use ./build_in_docker.sh instead: it runs this same
# script inside Ubuntu 22.04.
#
# Every setting can be overridden from the environment, for example:
#     USE_VENV=0 QT_PACKAGE=PyQt6 ./build_appimage.sh
#     APPIMAGETOOL=/path/to/appimagetool ./build_appimage.sh   (offline use)
#     APP_ID=io.github.me.BluetoothDeviceControl APP_URL=https://github.com/me/repo ./build_appimage.sh
# ----------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

APP_NAME="${APP_NAME:-BluetoothDeviceControl}"
APP_TITLE="${APP_TITLE:-Bluetooth Device Control}"
GUI="${GUI:-bt_device_control_gui.py}"
CORE="${CORE:-bluetooth_re_pairing.py}"
ICON_SCRIPT="${ICON_SCRIPT:-make_app_icon.py}"
QT_PACKAGE="${QT_PACKAGE:-PySide6}"      # PySide6 or PyQt6
QT_SPEC="${QT_SPEC:-$QT_PACKAGE}"        # pip spec, e.g. "PySide6<6.5" (Qt 6.4 needs no xcb-cursor)
EXTRA_LIBS="${EXTRA_LIBS:-}"             # more host libraries to bundle, e.g. "libEGL.so.1 libGL.so.1"
USE_VENV="${USE_VENV:-1}"                # 1 = private venv
VENV_DIR="${VENV_DIR:-.venv-build}"
SMOKE_TEST="${SMOKE_TEST:-1}"            # 1 = start the AppImage once with --version
APPIMAGETOOL="${APPIMAGETOOL:-}"         # path to appimagetool (downloaded if empty)
EXTRA_ARGS="${EXTRA_ARGS:-}"             # extra PyInstaller options
ARCH="${ARCH:-$(uname -m)}"              # x86_64, aarch64, ...

# ---- Catalog / AppStream metadata (EDIT THESE for AppImageHub) ----------
# APP_ID must be a reverse-DNS name you control, e.g. io.github.<user>.<App>
APP_ID="${APP_ID:-io.github.SEUUSUARIO.BluetoothDeviceControl}"
APP_LICENSE="${APP_LICENSE:-MIT}"        # SPDX id of YOUR license
APP_URL="${APP_URL:-https://github.com/SEUUSUARIO/SEUREPO}"
APP_SUMMARY="${APP_SUMMARY:-Re-pair Bluetooth devices from the system tray}"
SCREENSHOT_URL="${SCREENSHOT_URL:-}"     # optional https URL of a screenshot
GLIBC_MAX="${GLIBC_MAX:-2.35}"           # oldest glibc you want to support (Ubuntu 22.04)
STRICT_GLIBC="${STRICT_GLIBC:-1}"        # 1 = fail when glibc is too new (0 = only warn, local use)
LINT="${LINT:-0}"                        # 1 = run appimagelint on the result (if installed)

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

# Version: APP_VERSION env, else __version__ in the GUI, else 1.0.0
if [ -z "${APP_VERSION:-}" ]; then
    APP_VERSION="$(sed -n 's/^__version__ *= *["'\'']\([^"'\'']*\)["'\''].*/\1/p' "$GUI" | head -n1)"
    APP_VERSION="${APP_VERSION:-1.0.0}"
fi
info "Version: $APP_VERSION   AppStream id: $APP_ID"
case "$APP_ID$APP_URL" in
    *SEUUSUARIO*|*SEUREPO*)
        warn "APP_ID / APP_URL still hold the placeholders. The build works, but set"
        warn "them (see the top of this script) before submitting to AppImageHub." ;;
esac

# ---- Tools -------------------------------------------------------------
command -v python3 >/dev/null || die "python3 not found (sudo apt install python3)"
command -v objdump >/dev/null || die "objdump not found (sudo apt install binutils)"

PYTHON="python3"
if [ "$USE_VENV" = "1" ]; then
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        info "Creating virtual environment $VENV_DIR ..."
        python3 -m venv "$VENV_DIR" \
            || die "Could not create a venv (sudo apt install python3-venv)"
    fi
    PYTHON="$PWD/$VENV_DIR/bin/python"
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
"$PYTHON" -m pip install --upgrade pyinstaller "$QT_SPEC" \
    || die "pip failed. On Debian/Ubuntu keep USE_VENV=1 (system pip is locked)."

# ---- PyInstaller (folder mode: an AppImage is already a single file) ----
#  --hidden-import      the core module (the GUI imports it) and the stdlib
#                       modules that only the core uses
#  --exclude-module     PyInstaller aborts if two Qt bindings are installed
#  --noupx              UPX-compressed Qt libraries break at run time
rm -rf build/pyi-dist build/pyi-work "$APPDIR"
rm -f "$OUT"    # never leave an old AppImage behind if this build stops early
ARGS=(--noconfirm --clean --noupx
      --name "$APP_NAME"
      --distpath build/pyi-dist --workpath build/pyi-work
      --specpath build
      --paths "$PWD"
      --hidden-import "${CORE%.py}"
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

# ---- Fallback libraries ---------------------------------------------------
# PyInstaller leaves some system libraries out on purpose (libGL, libEGL ...)
# and the Qt plugins need many more (libxcb-*, libxkbcommon ...). A minimal
# system (CI runner, container, bare window manager) may lack them, and the
# program then dies at import time. So every library the bundle needs from
# THIS system is stored as a fallback, one folder per library. AppRun adds a
# folder to LD_LIBRARY_PATH only when the running system does not have that
# library, so the system's own (driver-matched) libraries are always preferred.
# Core glibc libraries are never bundled.
EXCLUDE_RE='^(libc|libm|libdl|libpthread|librt|libutil|libresolv|libnsl|libanl|libcrypt|libBrokenLocale|libthread_db|libnss_[a-z]+|ld-linux[^/]*|ld-[0-9.]+)\.so'
collect_fallback_libs() {
    local bin dest ldp list name path n=0
    bin="$(readlink -f "$1")"; dest="$2"
    ldp="$(find "$bin" -type d | paste -sd: -)"
    mkdir -p "$dest"
    list="$(find "$bin" -type f \( -name '*.so' -o -name '*.so.*' -o -perm -u+x \) \
                -exec sh -c 'for f; do LD_LIBRARY_PATH="$0" ldd "$f" 2>/dev/null; done' "$ldp" {} + \
            | awk '$2=="=>" && $3 ~ /^\// {print $1, $3} $2=="=>" && $3=="not" {print $1, "MISSING"}' \
            | sort -u || true)"
    while read -r name path; do
        [ -n "$name" ] || continue
        if [ "$path" = "MISSING" ]; then
            warn "$name is needed but not installed here (use ./build_in_docker.sh)."
            continue
        fi
        case "$(readlink -f "$path")" in "$bin"/*) continue ;; esac   # already bundled
        if printf '%s\n' "$name" | grep -qE "$EXCLUDE_RE"; then continue; fi
        mkdir -p "$dest/$name"
        cp -L "$path" "$dest/$name/$name"
        n=$((n + 1))
    done <<< "$list"
    # extra libraries requested by name
    local lib d
    for lib in $EXTRA_LIBS; do
        for d in /usr/lib/"$(uname -m)"-linux-gnu /lib/"$(uname -m)"-linux-gnu /usr/lib64 /usr/lib; do
            if [ -e "$d/$lib" ]; then
                mkdir -p "$dest/$lib"; cp -L "$d/$lib" "$dest/$lib/$lib"; n=$((n + 1)); break
            fi
        done
    done
    info "Stored $n fallback libraries ($(du -sh "$dest" | cut -f1))."
}
collect_fallback_libs "$APPDIR/usr/bin/$APP_NAME" "$APPDIR/usr/fallback-libs"

cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
# Use a bundled fallback library only if the system lacks it
SYSLIBS="$(PATH="$PATH:/sbin:/usr/sbin" ldconfig -p 2>/dev/null || true)"
for d in "$HERE"/usr/fallback-libs/*/; do
    [ -d "$d" ] || continue
    n="$(basename "$d")"
    case "$SYSLIBS" in
        *"$n ("*) ;;
        *) LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}${d%/}"; export LD_LIBRARY_PATH ;;
    esac
done
exec "$HERE/usr/bin/@APP_NAME@/@APP_NAME@" "$@"
APPRUN
sed -i "s/@APP_NAME@/$APP_NAME/g" "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"

# ---- glibc compatibility check -----------------------------------------
# An AppImage runs on the glibc it was built against or a newer one. Report
# the newest glibc symbol that any bundled binary needs.
highest_glibc() {
    local worst="0" v f
    while IFS= read -r f; do
        # objdump -p lists every required version. GLIBC_ABI_DT_RELR (packed
        # relocations) is not a number: it needs glibc 2.36 or newer.
        v="$(LC_ALL=C objdump -p "$f" 2>/dev/null \
                | grep -oE 'GLIBC_(ABI_DT_RELR|[0-9][0-9.]*)' \
                | sed -e 's/^GLIBC_ABI_DT_RELR$/GLIBC_2.36/' -e 's/^GLIBC_//' \
                | sort -V | tail -n1 || true)"
        [ -n "$v" ] || continue
        if [ "$(printf '%s\n%s\n' "$worst" "$v" | sort -V | tail -n1)" = "$v" ]; then
            worst="$v"
        fi
    done < <(find "$1" -type f \( -name '*.so' -o -name '*.so.*' -o -perm -u+x \))
    printf '%s' "$worst"
}
GLIBC_NEEDED="$(highest_glibc "$APPDIR/usr")"
info "Newest glibc symbol required: $GLIBC_NEEDED (target: $GLIBC_MAX or older)"
if [ "$GLIBC_NEEDED" != "0" ] \
   && [ "$(printf '%s\n%s\n' "$GLIBC_MAX" "$GLIBC_NEEDED" | sort -V | tail -n1)" != "$GLIBC_MAX" ]; then
    msg="This build needs glibc $GLIBC_NEEDED, newer than $GLIBC_MAX: it would fail on older distros and in the AppImageHub checks. Build with ./build_in_docker.sh (Ubuntu 22.04). For a build only for this machine: STRICT_GLIBC=0 ./build_appimage.sh"
    if [ "$STRICT_GLIBC" = "1" ]; then die "$msg"; else warn "$msg"; fi
fi

# Icon: 256x256 PNG drawn by make_app_icon.py (same artwork as the tray icon);
# if that fails, fall back to an SVG with the same drawing.
ICON_FILE="$ICON_NAME.png"
ICON_TMP="build/icon-tmp"
rm -rf "$ICON_TMP"; mkdir -p "$ICON_TMP"
if [ -f "$ICON_SCRIPT" ] \
   && QT_QPA_PLATFORM=offscreen "$PYTHON" "$ICON_SCRIPT" "$ICON_TMP" >/dev/null 2>&1 \
   && [ -s "$ICON_TMP/app_icon.png" ]; then
    cp "$ICON_TMP/app_icon.png" "$APPDIR/$ICON_FILE"
else
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

# The icon also goes to the standard hicolor location
mkdir -p "$APPDIR/usr/share/applications" "$APPDIR/usr/share/metainfo"
case "$ICON_FILE" in
    *.png) mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
           cp "$APPDIR/$ICON_FILE" "$APPDIR/usr/share/icons/hicolor/256x256/apps/$ICON_NAME.png" ;;
    *.svg) mkdir -p "$APPDIR/usr/share/icons/hicolor/scalable/apps"
           cp "$APPDIR/$ICON_FILE" "$APPDIR/usr/share/icons/hicolor/scalable/apps/$ICON_NAME.svg" ;;
esac

# ---- .desktop (its file name must match the AppStream <id>) -------------
DESKTOP_FILE="$APP_ID.desktop"
cat > "$APPDIR/$DESKTOP_FILE" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=$APP_TITLE
Comment=$APP_SUMMARY
Exec=$APP_NAME
Icon=$ICON_NAME
Categories=Utility;
Keywords=Bluetooth;pairing;devices;tray;
Terminal=false
X-AppImage-Name=$APP_TITLE
X-AppImage-Version=$APP_VERSION
DESKTOP
cp "$APPDIR/$DESKTOP_FILE" "$APPDIR/usr/share/applications/$DESKTOP_FILE"

# ---- AppStream metadata ---------------------------------------------------
SCREENSHOT_XML=""
if [ -n "$SCREENSHOT_URL" ]; then
    SCREENSHOT_XML="  <screenshots>
    <screenshot type=\"default\"><image>$SCREENSHOT_URL</image></screenshot>
  </screenshots>"
fi
cat > "$APPDIR/usr/share/metainfo/$APP_ID.appdata.xml" <<METAINFO
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>$APP_ID</id>
  <metadata_license>CC0-1.0</metadata_license>
  <project_license>$APP_LICENSE</project_license>
  <name>$APP_TITLE</name>
  <summary>$APP_SUMMARY</summary>
  <description>
    <p>Lists your Bluetooth devices and lets you remove and re-pair them with
    a click from the system tray. Requires BlueZ.</p>
  </description>
  <launchable type="desktop-id">$DESKTOP_FILE</launchable>
  <url type="homepage">$APP_URL</url>
  <provides><binary>$APP_NAME</binary></provides>
$SCREENSHOT_XML
  <releases>
    <release version="$APP_VERSION" date="$(date +%F)"/>
  </releases>
  <content_rating type="oars-1.1"/>
</component>
METAINFO

# ---- Validate before packing (skipped if the tools are missing) ----------
if command -v desktop-file-validate >/dev/null; then
    desktop-file-validate "$APPDIR/$DESKTOP_FILE" || die "Invalid .desktop file."
    ok ".desktop file is valid."
else
    warn "desktop-file-validate not found (sudo apt install desktop-file-utils)"
fi
if command -v appstreamcli >/dev/null; then
    if appstreamcli validate --no-net "$APPDIR/usr/share/metainfo/$APP_ID.appdata.xml"; then
        ok "AppStream metadata is valid."
    else
        warn "AppStream validation reported problems (see above)."
    fi
else
    warn "appstreamcli not found (sudo apt install appstream)"
fi

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
# --version is handled before Qt is imported, so it needs no display and no
# system graphics libraries.
if [ "$SMOKE_TEST" = "1" ]; then
    info "Smoke test: starting the AppImage with --version ..."
    if APPIMAGE_EXTRACT_AND_RUN=1 "$OUT" --version; then
        ok "The AppImage starts correctly."
    else
        warn "The AppImage failed to start. Run it in a terminal to see why: $OUT --version"
    fi
fi

# ---- Optional lint (same kind of checks as the catalog) --------------------
if [ "$LINT" = "1" ]; then
    if command -v appimagelint >/dev/null; then
        info "Running appimagelint ..."
        appimagelint "$OUT" || warn "appimagelint reported problems (see above)."
    else
        warn "appimagelint not found (pip install appimagelint)"
    fi
fi

cat <<EOT

Notes
  * Build on the oldest distro you want to support (./build_in_docker.sh does
    it for you): the AppImage runs on the same or a newer glibc, not an older one.
  * Running an AppImage needs FUSE 2. On Ubuntu 22.04+ install it with
    "sudo apt install libfuse2" (libfuse2t64 on 24.04), or run the file with
    --appimage-extract-and-run.
  * To open the window Qt needs some system libraries. On Debian/Ubuntu:
      sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libxcb-icccm4 \\
           libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \\
           libxcb-shape0 libegl1 libgl1
  * BlueZ (bluetoothctl) must be installed on the system; it is not bundled.
  * The tray icon needs a system tray: on GNOME install the
    "AppIndicator and KStatusNotifierItem Support" extension.
  * Start at login stores the path of the .AppImage file in
    ~/.config/autostart. If you move or rename it, turn the option off and on.
EOT
