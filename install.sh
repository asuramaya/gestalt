#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
# Gestalt installer — hands-free head-pointer daemon + Quick Settings pill.
#
# Unlike phanspeed, gestaltd runs as the USER (it needs your webcam, /dev/uinput,
# and a session overlay — none of which need root). So this installs a *user*
# systemd service. The only root step is an optional udev rule granting access
# to /dev/uinput (skip it if uinput is already world-writable on your box).
set -euo pipefail

# ---- refuse root, checked FIRST, before any download --------------------
# gestaltd runs as the USER (see the header above) and every step below
# writes to $HOME/$XDG_CONFIG_HOME — under sudo that's root's home, not the
# human's. Nothing downstream re-checks this, so an unguarded `sudo
# ./install.sh` builds a complete, working gestalt into /root and only fails
# at the very last `systemctl --user` call (no session bus for root) --
# incidental, not designed, and if that one command ever succeeded this
# would report SUCCESS while gestalt lived somewhere the operator never
# opens (found for real: msg 6416). Fail fast and plainly instead.
if [[ $EUID -eq 0 ]]; then
  cat >&2 <<'EOF'
gestalt installs as a per-user service (your webcam, /dev/uinput via a
group, and a session overlay — none of which need root) and writes to your
own $HOME. Run it as yourself, not under sudo:

  ./install.sh

(the one optional root step — the udev rule for /dev/uinput — prompts for
sudo on its own, only for that one command)
EOF
  exit 1
fi

REPO="asuramaya/gestalt"
# principal = WHO (the repo's stable identity); namespace = WHAT-FOR (what
# this signature authorizes). Never conflate the two — see RELEASE.md.
SIGN_PRINCIPAL="gestalt"
SIGN_NAMESPACE="gestalt-release"

# Trust anchor for the curl-pipe-bash bootstrap below, EMBEDDED directly:
# `curl .../install.sh | bash` fetches this ONE file over the network, so at
# that point there is no sibling packaging/release-signing/allowed_signers to
# read. Kept in sync with that file by `make sync-signers`
# (packaging/sync-signers.sh) — never hand-edit this. Single-quoted
# deliberately: the value can span multiple lines (one per pinned key) and
# must never be shell-interpolated.
#
# UNLIKE coldspot's own bootstrap, this never degrades to sha256-only when
# empty: gestalt's release.yml refuses to build a release tarball from a tag
# whose anchor is still empty (docs/RELEASE-SIGNING.md) — so a real gestalt
# release is ALWAYS already armed by the time it exists. There is no
# legitimate "released but unarmed" era to accommodate, unlike coldspot's own
# history, so a soft fallback here would just be an unused, riskier escape
# hatch. Empty here means this install.sh predates the ceremony (or was
# tampered with) — refuse, don't degrade.
RELEASE_ALLOWED_SIGNERS=''

SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo /nonexistent)"

# ---- verified release bootstrap (coldspot-style) ------------------------
# When run without its sibling files (i.e. `curl -fsSL .../install.sh |
# bash`), fetch the published, checksum-and-signature-verified release
# tarball and re-exec from it, rather than GitHub's auto-generated
# tarball_url (the previous approach) — that artifact is not an asset
# release.yml uploads, so SHA256SUMS/a signature can never cover it; it sits
# structurally outside the signature chain, so no seal, now or ever, could
# protect it. phanspeed hit and fixed this same pattern first; see its
# install.sh for the fuller argument.
verify_release_tarball() {
  local tarball="$1" base="https://github.com/${REPO}/releases/latest/download" \
        tmp sums sig signers want got
  command -v sha256sum >/dev/null 2>&1 || {
    echo "sha256sum not found; cannot verify the download. Install coreutils." >&2; exit 1; }
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' RETURN

  sums="$tmp/gestalt.tar.gz.sha256"
  curl -fsSL "${base}/gestalt.tar.gz.sha256" -o "$sums" \
    || { echo "could not fetch release checksum; refusing unverified download." >&2; exit 1; }
  want="$(awk '{print $1; exit}' "$sums")"
  [[ -n "$want" ]] || { echo "release checksum file is empty or malformed; aborting." >&2; exit 1; }
  got="$(sha256sum "$tarball" | awk '{print $1}')"
  [[ "$want" == "$got" ]] || { echo "checksum mismatch on gestalt.tar.gz; aborting." >&2; exit 1; }
  echo "verified release checksum."

  # No soft degrade on an empty anchor — see RELEASE_ALLOWED_SIGNERS's own
  # comment above for why gestalt's policy differs from coldspot's here.
  [[ -n "$RELEASE_ALLOWED_SIGNERS" ]] || {
    echo "no release-signing key embedded in this install.sh; refusing to" >&2
    echo "install an unsigned release. Fetch a current install.sh and re-run." >&2
    exit 1
  }
  command -v ssh-keygen >/dev/null 2>&1 || {
    echo "ssh-keygen not found; cannot verify the release signature. Aborting." >&2; exit 1; }
  sig="$tmp/gestalt.tar.gz.sha256.sig"
  curl -fsSL "${base}/gestalt.tar.gz.sha256.sig" -o "$sig" || {
    echo "no signature published for this release yet -- it exists but has" >&2
    echo "not been sealed by the operator. Refusing to install an unsigned" >&2
    echo "release. Try again once the release carries a .sig asset." >&2
    exit 1
  }
  signers="$tmp/allowed_signers"
  # No added newline: RELEASE_ALLOWED_SIGNERS is embedded byte-for-byte from
  # the anchor file by `make sync-signers`, trailing newline included — that
  # exact-copy invariant is what CI's signing-sync check enforces.
  printf '%s' "$RELEASE_ALLOWED_SIGNERS" > "$signers"
  if ! ssh-keygen -Y verify -f "$signers" -I "$SIGN_PRINCIPAL" -n "$SIGN_NAMESPACE" \
        -s "$sig" < "$sums" >/dev/null 2>&1; then
    echo "signature verification FAILED; refusing to install." >&2; exit 1
  fi
  echo "verified release signature."
}

bootstrap_from_release() {
  command -v curl >/dev/null 2>&1 || { echo "curl is required for remote install" >&2; exit 1; }
  command -v tar  >/dev/null 2>&1 || { echo "tar is required for remote install" >&2; exit 1; }
  local tmp tarball inner
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  tarball="$tmp/gestalt.tar.gz"
  echo "== fetching latest Gestalt release =="
  if ! curl -fsSL "https://github.com/${REPO}/releases/latest/download/gestalt.tar.gz" -o "$tarball"; then
    # No unreviewed-`main` fallback: falling back to a mutable branch on ANY
    # fetch hiccup (network blip, or an attacker simply interfering with the
    # release-asset request) would turn a transient failure into an
    # unverified install. Fail closed instead.
    cat >&2 <<EOF
could not fetch a published release tarball for ${REPO}. This installer
never falls back to the unreviewed main branch — clone the repo and run
install.sh from the checkout instead:

  git clone https://github.com/${REPO} && cd gestalt && ./install.sh
EOF
    exit 1
  fi
  verify_release_tarball "$tarball"
  tar -xzf "$tarball" -C "$tmp"
  inner="$(find "$tmp" -maxdepth 2 -name install.sh -type f | head -n1)"
  [[ -n "$inner" ]] || { echo "install.sh not found in archive" >&2; exit 1; }
  bash "$inner" "$@"; exit $?
}

[[ -f "$SRC/src/bin/gestaltd" ]] || bootstrap_from_release "$@"

PREFIX="$HOME/.local/share/gestalt"
CFG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/gestalt"

echo "== Gestalt installer =="

# 1. bundled venv (pinned Python 3.12 — mediapipe/opencv have no 3.13+/3.14 wheels)
if ! command -v uv >/dev/null 2>&1; then
  # "not found on PATH" is what this check actually proved; "not installed"
  # would be a diagnosis it can't back — uv can be installed and simply
  # invisible from here (a non-login shell, a different $PATH). Found for
  # real under sudo, where uv lived in the human's ~/.local/bin, off root's
  # PATH entirely (msg 6416) — "install it" sent that reader to reinstall a
  # thing that was already there.
  echo "!! 'uv' not found on PATH (https://docs.astral.sh/uv/). Install it, or" \
       "add it to PATH if it's already installed elsewhere, and re-run."
  exit 1
fi
echo "-- building venv -> $PREFIX/venv (Python 3.12 via uv)"
mkdir -p "$PREFIX/bin"
# --clear: a reinstall rebuilds the venv from scratch (uv refuses to reuse an
# existing one, and set -e would abort the whole install before the code copy)
uv venv --clear --python 3.12 "$PREFIX/venv"
uv pip install --python "$PREFIX/venv/bin/python" \
  mediapipe opencv-python numpy pygame evdev python-xlib

# 2. app code (daemon + cli + package + providers + MediaPipe models)
# No installed path changes here (REPO-STANDARD.md §6) — only the SOURCE
# tree moved (src/bin/, src/gestalt/, src/providers/, src/data/models/,
# packaging/VERSION); $PREFIX still gets bin/, gestalt/, providers/, models/,
# VERSION at the same names as before.
echo "-- installing code -> $PREFIX"
cp "$SRC/src/bin/gestaltd" "$SRC/src/bin/gestaltctl" "$PREFIX/bin/"
# Sutra install-path adoption (sutra/docs/BOOTSTRAP.md, ruling 3e44bd95):
# vendored copies live in their own private, package-owned dir instead of
# beside the binaries — every binary that imports sutra finds it there via
# a small sys.path bootstrap preamble instead of relying on co-location.
echo "-- sutra commons -> $PREFIX/share/gestalt/lib"
mkdir -p "$PREFIX/share/gestalt/lib"
for f in sutra.py sutra.version sutra.commit \
         sutra_update.py sutra_update.version sutra_update.commit \
         sutra_xen.py sutra_xen.version sutra_xen.commit; do
  [[ -f "$SRC/src/share/gestalt/lib/$f" ]] && cp "$SRC/src/share/gestalt/lib/$f" "$PREFIX/share/gestalt/lib/$f"
done
# Old-layout leftover: a machine that ran a pre-adoption install.sh has
# sutra.py/.version sitting co-located in $PREFIX/bin, owned by nothing.
# Clean it up unconditionally so it can't shadow the new bootstrap-resolved
# copy or linger forever.
rm -f "$PREFIX/bin/sutra.py" "$PREFIX/bin/sutra.version"
# Wipe the three code dirs before copying. `cp -r src dst` when dst EXISTS copies
# INTO it (dst/src) *and* leaves last install's stale modules/__pycache__ behind —
# an "upgrade" would then silently run old code. rm -rf makes the copy a clean
# replace. Only these three are code; venv/recordings/bin/config are preserved.
rm -rf "$PREFIX/gestalt" "$PREFIX/providers" "$PREFIX/models"
cp -r "$SRC/src/gestalt" "$PREFIX/gestalt"
cp -r "$SRC/src/providers" "$PREFIX/providers"
cp -r "$SRC/src/data/models" "$PREFIX/models"
cp "$SRC/packaging/VERSION" "$PREFIX/VERSION"
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
# `-w` is true only when the node exists AND we can write it -- it reads the
# same false whether uinput is unloaded entirely (no node yet) or loaded but
# permissioned to someone else. Both get the identical remedy below though:
# `static_node=uinput` is udev's own mechanism for creating the node (and
# triggering the kernel module load) on `udevadm trigger`, not just fixing
# permissions on one that already exists -- so the one branch is correct for
# both cases, not just imprecisely worded for one of them.
if [[ -w /dev/uinput ]]; then
  echo "-- /dev/uinput already writable; skipping udev rule"
else
  echo "-- /dev/uinput needs a udev rule (missing, or present but not yet accessible) — installing (sudo)"
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
cp "$SRC/src/data/systemd/user/gestalt.service" "$HOME/.config/systemd/user/gestalt.service"
systemctl --user daemon-reload
# The one failure this is meant to catch is a genuinely absent session bus
# (no graphical login yet — e.g. a scripted/first-boot install) -- systemd's
# own text for that is stable and specific ("Failed to connect to ... bus").
# Anything else (a real service-definition or dependency problem) used to
# print the SAME reassuring "will start on next login" and let this script
# reach "== done ==" regardless -- the exact shape that would have reported
# SUCCESS for a full /root install that only failed here (msg 6416). Stop
# and say so plainly instead of guessing every failure is the benign one.
_svc_err="$(mktemp)"
if ! systemctl --user enable --now gestalt.service 2>"$_svc_err"; then
  if grep -qE 'Failed to connect to.*bus' "$_svc_err"; then
    echo "   (no session bus reachable here — will start on next login)"
  else
    echo "!! systemctl --user enable --now gestalt.service failed:" >&2
    cat "$_svc_err" >&2
    rm -f "$_svc_err"
    exit 1
  fi
fi
rm -f "$_svc_err"

# 6. GNOME Shell extension — shared with `make pill` (packaging/install-pill.sh)
# so re-staging just the extension never drifts from what a full install does.
bash "$SRC/packaging/install-pill.sh" "$SRC"

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
