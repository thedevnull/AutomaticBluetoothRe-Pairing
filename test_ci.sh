#!/usr/bin/env bash
# ----------------------------------------------------------------------
# Reproduces the AppImageHub "shows a window" test on your machine, inside a
# CLEAN Ubuntu 22.04 container that has only Xvfb (no Qt libraries), like
# the CI runner. If the program dies, it prints why.
#     ./test_ci.sh [path/to/App.AppImage]
# Needs Docker or Podman.
# ----------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

APP="${1:-$(ls -t dist/*.AppImage 2>/dev/null | head -n1)}"
[ -n "$APP" ] && [ -f "$APP" ] || { echo "[x] AppImage not found. Build it first." >&2; exit 1; }
APP="$(readlink -f "$APP")"
ENGINE="${ENGINE:-}"
if [ -z "$ENGINE" ]; then
    if command -v docker >/dev/null; then ENGINE=docker
    elif command -v podman >/dev/null; then ENGINE=podman
    else echo "[x] Docker or Podman is required." >&2; exit 1; fi
fi

"$ENGINE" run --rm -v "$APP:/test/app.AppImage:ro" -e QT_DEBUG_PLUGINS="${QT_DEBUG_PLUGINS:-1}" \
    "${IMAGE:-ubuntu:22.04}" bash -c '
export DEBIAN_FRONTEND=noninteractive
apt-get update >/dev/null && apt-get install -y --no-install-recommends \
    xvfb xauth xdotool x11-utils >/dev/null
Xvfb :99 -screen 0 1280x1024x24 >/dev/null 2>&1 &
export DISPLAY=:99 APPIMAGE_EXTRACT_AND_RUN=1
sleep 2
cp /test/app.AppImage /tmp/app.AppImage && chmod +x /tmp/app.AppImage
/tmp/app.AppImage >/tmp/app.log 2>&1 &
PID=$!
for i in $(seq 1 12); do
    sleep 1
    kill -0 $PID 2>/dev/null || break
    WIN=$(xdotool search --onlyvisible --name "." 2>/dev/null | head -n1 || true)
    [ -n "$WIN" ] && break
done
if kill -0 $PID 2>/dev/null && [ -n "${WIN:-}" ]; then
    echo "[+] PASS: the application is running and showed a window ($(xdotool getwindowname $WIN))."
    kill $PID; exit 0
fi
if kill -0 $PID 2>/dev/null; then
    echo "[x] FAIL: still running, but no window appeared."; kill $PID
else
    wait $PID; echo "[x] FAIL: the application exited (code $?) before showing a window."
fi
echo "---------------- output of the program ----------------"
tail -n 60 /tmp/app.log
echo "---------------- libraries this system lacks ----------------"
cd /tmp && ./app.AppImage --appimage-extract >/dev/null 2>&1 || true
R=/tmp/squashfs-root/usr
LDP=$(find $R -type d | paste -sd: -)
find $R/bin -type f \( -name "*.so" -o -name "*.so.*" -o -perm -u+x \) -exec sh -c \
    "for f; do LD_LIBRARY_PATH=\"\$0\" ldd \"\$f\" 2>/dev/null; done" "$LDP" {} + \
    | grep "not found" | sort -u || true
echo "(an empty list means every library was found)"
exit 1
'
