#!/usr/bin/env bash
# ----------------------------------------------------------------------
# Runs build_appimage.sh inside Ubuntu 22.04 (glibc 2.35) so the AppImage
# also works on older distros and passes the AppImageHub checks.
#     chmod +x build_in_docker.sh && ./build_in_docker.sh
# Needs Docker or Podman. Any variable you export (APP_ID, APP_URL,
# APP_LICENSE, QT_PACKAGE, ...) is passed through to build_appimage.sh.
# ----------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

IMAGE="${IMAGE:-ubuntu:22.04}"
ENGINE="${ENGINE:-}"
if [ -z "$ENGINE" ]; then
    if command -v docker >/dev/null; then ENGINE=docker
    elif command -v podman >/dev/null; then ENGINE=podman
    else echo "[x] Docker or Podman is required." >&2; exit 1; fi
fi

PASS=()
for v in APP_NAME APP_TITLE APP_ID APP_VERSION APP_LICENSE APP_URL APP_SUMMARY \
         SCREENSHOT_URL QT_PACKAGE EXTRA_ARGS GLIBC_MAX LINT GUI CORE; do
    if [ -n "${!v:-}" ]; then PASS+=(-e "$v=${!v}"); fi
done

echo "[*] Building inside $IMAGE with $ENGINE ..."
"$ENGINE" run --rm -v "$PWD:/src" -w /src \
    -e VENV_DIR=.venv-build-docker -e STRICT_GLIBC=1 \
    -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" "${PASS[@]}" \
    "$IMAGE" bash -c '
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip binutils curl ca-certificates file \
    desktop-file-utils appstream \
    libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libfontconfig1 libdbus-1-3 \
    libglib2.0-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0
rc=0
./build_appimage.sh || rc=$?
chown -R "$HOST_UID:$HOST_GID" build dist .tools .venv-build-docker 2>/dev/null || true
exit $rc
'
echo "[+] Done. Look in dist/"
