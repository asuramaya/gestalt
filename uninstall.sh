#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
# Gestalt uninstaller — reverses install.sh. Leaves your config in place unless
# you pass --purge.
set -euo pipefail

PREFIX="$HOME/.local/share/gestalt"
EXT_UUID="gestalt@asuramaya"
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$EXT_UUID"
CFG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/gestalt"

echo "== Gestalt uninstaller =="

systemctl --user disable --now gestalt.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/gestalt.service"
systemctl --user daemon-reload 2>/dev/null || true

gnome-extensions disable "$EXT_UUID" 2>/dev/null || true
rm -rf "$EXT_DIR"

rm -f "$HOME/.local/bin/gestaltctl"
rm -rf "$PREFIX"

if [[ -f /etc/udev/rules.d/60-gestalt-uinput.rules ]]; then
  echo "-- removing udev rule (sudo)"
  sudo rm -f /etc/udev/rules.d/60-gestalt-uinput.rules
  sudo udevadm control --reload-rules 2>/dev/null || true
fi

if [[ "${1:-}" == "--purge" ]]; then
  echo "-- purging config $CFG_DIR"
  rm -rf "$CFG_DIR"
else
  echo "-- keeping config at $CFG_DIR (pass --purge to remove)"
fi

echo "== done ==  (log out/in to drop the pill from the shell)"
