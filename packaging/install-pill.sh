#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
# Stage the GNOME Quick Settings pill — the one piece of install.sh's work
# `make pill` also needs to do on its own, so it lives here once and both
# callers (install.sh's step 6, and the `pill` Makefile target) run the
# same code instead of two copies drifting apart. Never called with sudo —
# see install.sh's own root guard; this only ever writes to $HOME.
set -euo pipefail

SRC="${1:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd)}"
EXT_UUID="gestalt@asuramaya"
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$EXT_UUID"

echo "-- installing Quick Settings pill -> $EXT_DIR"
mkdir -p "$EXT_DIR/schemas"
cp "$SRC/src/extension/$EXT_UUID/metadata.json" "$SRC/src/extension/$EXT_UUID/extension.js" "$EXT_DIR/"
# pill.js is vendored (src/share/gestalt/lib's sibling commons) but not yet
# imported by extension.js — deliberately not installed until it's wired in.
# The panic-kill keyboard shortcut is a GSettings key — compile its schema into
# the extension dir so getSettings()/addKeybinding() can read it.
cp "$SRC/src/extension/$EXT_UUID/schemas/"*.gschema.xml "$EXT_DIR/schemas/"
glib-compile-schemas "$EXT_DIR/schemas"
gnome-extensions enable "$EXT_UUID" 2>/dev/null \
  && echo "   enabled" \
  || echo "   (enable on next login: gnome-extensions enable $EXT_UUID)"
echo "   code changes need a LOG OUT/IN to take effect (Wayland has no in-place"
echo "   extension reload the way X11's Alt+F2 r has one)"
