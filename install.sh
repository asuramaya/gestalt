#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
# Gestalt installer — hands-free head-pointer daemon + Quick Settings pill.
#
# Unlike PhanSpeed, gestaltd runs as the USER (it needs your webcam, /dev/uinput,
# and a session overlay — none of which need root). So this installs a *user*
# systemd service. The only root step is an optional udev rule granting access
# to /dev/uinput (skip it if uinput is already world-writable on your box).
set -euo pipefail

REPO="asuramaya/gestalt"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo /nonexistent)"

# Bootstrap for the one-line install: if not next to the source, fetch a release.
if [[ ! -f "$SRC/bin/gestaltd" ]]; then
  echo "== fetching latest Gestalt release =="
  TMP="$(mktemp -d)"
  url="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
        | grep -m1 tarball_url | cut -d'"' -f4)"
  [[ -n "$url" ]] || { echo "could not find a release to download"; exit 1; }
  curl -fsSL "$url" | tar -xz -C "$TMP" --strip-components=1
  exec bash "$TMP/install.sh" "$@"
fi

PREFIX="$HOME/.local/share/gestalt"
EXT_UUID="gestalt@asuramaya"
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$EXT_UUID"
CFG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/gestalt"

echo "== Gestalt installer =="

# 1. bundled venv (pinned Python 3.12 — mediapipe/opencv have no 3.13+/3.14 wheels)
if ! command -v uv >/dev/null 2>&1; then
  echo "!! 'uv' is required (https://docs.astral.sh/uv/). Install it and re-run."
  exit 1
fi
echo "-- building venv -> $PREFIX/venv (Python 3.12 via uv)"
mkdir -p "$PREFIX/bin"
uv venv --python 3.12 "$PREFIX/venv"
uv pip install --python "$PREFIX/venv/bin/python" \
  mediapipe opencv-python numpy pygame evdev python-xlib

# 2. app code (daemon + cli + package + providers + MediaPipe models)
echo "-- installing code -> $PREFIX"
cp "$SRC/bin/gestaltd" "$SRC/bin/gestaltctl" "$PREFIX/bin/"
cp -r "$SRC/gestalt" "$PREFIX/gestalt"
cp -r "$SRC/providers" "$PREFIX/providers"
cp -r "$SRC/models" "$PREFIX/models"
cp "$SRC/VERSION" "$PREFIX/VERSION"
chmod 0755 "$PREFIX/bin/gestaltd" "$PREFIX/bin/gestaltctl"
ln -sf "$PREFIX/bin/gestaltctl" "$HOME/.local/bin/gestaltctl" 2>/dev/null || true

# 3. default config (only if absent — never clobber the user's tuning)
mkdir -p "$CFG_DIR"
if [[ ! -f "$CFG_DIR/config.json" ]]; then
  echo "-- writing default config -> $CFG_DIR/config.json"
  ( cd "$PREFIX" && "$PREFIX/venv/bin/python" -c \
    "import json; from gestalt.config import DEFAULTS; \
     json.dump(DEFAULTS, open('$CFG_DIR/config.json','w'), indent=2)" )
fi

# 4. optional udev rule for /dev/uinput (root). Skip if already accessible.
if [[ -w /dev/uinput ]]; then
  echo "-- /dev/uinput already writable; skipping udev rule"
else
  echo "-- /dev/uinput needs access; installing udev rule (sudo)"
  sudo tee /etc/udev/rules.d/60-gestalt-uinput.rules >/dev/null <<'RULE'
KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"
RULE
  sudo groupadd -f input
  sudo usermod -aG input "$USER"
  sudo udevadm control --reload-rules && sudo udevadm trigger
  echo "   added you to 'input' — LOG OUT/IN for it to take effect"
fi

# 5. user systemd service
echo "-- installing user service"
mkdir -p "$HOME/.config/systemd/user"
cp "$SRC/systemd/user/gestalt.service" "$HOME/.config/systemd/user/gestalt.service"
systemctl --user daemon-reload
systemctl --user enable --now gestalt.service || \
  echo "   (will start on next login)"

# 6. GNOME Shell extension
echo "-- installing Quick Settings pill -> $EXT_DIR"
mkdir -p "$EXT_DIR/schemas"
cp "$SRC/extension/$EXT_UUID/metadata.json" "$SRC/extension/$EXT_UUID/extension.js" "$EXT_DIR/"
# The panic-kill keyboard shortcut is a GSettings key — compile its schema into
# the extension dir so getSettings()/addKeybinding() can read it.
cp "$SRC/extension/$EXT_UUID/schemas/"*.gschema.xml "$EXT_DIR/schemas/"
glib-compile-schemas "$EXT_DIR/schemas"
gnome-extensions enable "$EXT_UUID" 2>/dev/null \
  && echo "   enabled" \
  || echo "   (enable on next login: gnome-extensions enable $EXT_UUID)"

# 7. enable the AT-SPI accessibility bus — Gestalt's primary target source. Without
# this, apps publish no widget tree and magnetism has nothing to snap to.
echo "-- enabling the accessibility bus (toolkit-accessibility)"
gsettings set org.gnome.desktop.interface toolkit-accessibility true 2>/dev/null \
  && echo "   on (relaunch apps — esp. Chrome via --force-renderer-accessibility — to expose trees)" \
  || echo "   (set manually: gsettings set org.gnome.desktop.interface toolkit-accessibility true)"

echo
echo "== done =="
echo ">>> LOG OUT and back in once <<<  (Wayland reloads the shell for the new"
echo "    pill; the 'input' group membership also needs a fresh login). The"
echo "    Gestalt pill then appears in Quick Settings — click it to arm."
