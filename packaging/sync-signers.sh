#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# `make sync-signers` — rebuild packaging/release-signing/allowed_signers from
# the fleet's canonical pubkeys, per ~/code/REPOS/RELEASE.md's sync-signers
# doctrine. No embedded install.sh twin to sync: gestalt's install.sh
# bootstrap doesn't read a sibling allowed_signers at fetch time (it does no
# verification of its auto-fetch at all today — see docs/RELEASE-SIGNING.md's
# "Known gap" section), so there is nothing to embed a copy into yet.
#
# Canonical key home (operator ruling 13ee52ce): ~/.ssh/asuramaya-master/ —
# OUTSIDE every repo, never committed, never a sibling checkout. This is a
# LOCAL-ONLY act by construction: CI can never reach $HOME, so
# .github/workflows/signing-sync.yml asserts internal consistency only
# (well-formed anchor), never canonical equality.
#
# ALWAYS a full rebuild, never an append: RA's first ceremony left 3 of 4
# keys unpinned across other repos by appending one key at a time. Refuses to
# run unless it finds exactly 4 canonical keys, so a partial/broken key home
# can't silently produce a partial anchor.
#
# SEQUENCING: this populates the anchor. Run it as step 2 of
# docs/RELEASING.md's order — after `make check`, strictly BEFORE the tag
# that will ship it. release.yml builds the release tarball with `git
# archive` from the tag itself, so an anchor armed AFTER tagging ships that
# tag's tarball with a permanently empty anchor (a sealed release is never
# re-cut) — the trap Vajra found in mudra's own doc, mail #2891. release.yml
# refuses to build from a tag whose anchor is still empty, so getting this
# wrong fails loud rather than shipping broken. Building this script is not
# running it.
set -euo pipefail
# This file lives at packaging/sync-signers.sh, one level under the repo
# root, so reaching root needs one ".." hop.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd)"
PRINCIPAL="gestalt"
NAMESPACES="gestalt-release,pills-tag"

KEY_HOME="${KEY_HOME:-$HOME/.ssh/asuramaya-master}"
if [[ ! -d "$KEY_HOME" ]]; then
  echo "ERROR: canonical key home not found at $KEY_HOME." >&2
  echo "       Set KEY_HOME=/path/to/asuramaya-master and retry." >&2
  exit 1
fi

mapfile -t pubs < <(find "$KEY_HOME" -maxdepth 1 -name '*.pub' | LC_ALL=C sort)
if [[ "${#pubs[@]}" -ne 4 ]]; then
  echo "ERROR: expected exactly 4 canonical pubkeys in $KEY_HOME, found ${#pubs[@]}." >&2
  echo "       Never partially sync — see RELEASE.md's sync-signers section." >&2
  exit 1
fi

anchor="$HERE/packaging/release-signing/allowed_signers"
tmp="$(mktemp)"
for p in "${pubs[@]}"; do
  printf '%s namespaces="%s" %s\n' "$PRINCIPAL" "$NAMESPACES" "$(cat "$p")"
done > "$tmp"
mv "$tmp" "$anchor"
echo "rebuilt $anchor from ${#pubs[@]} canonical keys ($KEY_HOME)"
