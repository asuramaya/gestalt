# Release signing

Status: **unarmed.** `packaging/release-signing/allowed_signers` ships empty — gestalt has never
cut a release, and the ceremony that arms it hasn't run yet. Every client verifying a gestalt
release today degrades to sha256-only (there is no client yet either; see the known gap below).
See [RELEASING.md](RELEASING.md) for the running order — **arm, then tag, then seal, always in
that order**; `release.yml` refuses to build a release tarball from a tag whose anchor is still
empty, so this is enforced, not just documented (Vajra found the ordering trap this guards
against, in mudra's own doc — mail #2891).

## Why this exists

A SHA256 checksum published alongside a release proves a download wasn't corrupted or truncated in
transit. It proves nothing about *authenticity*: the checksum comes from the same GitHub release
it's checking, so a compromised release asset carries its own "valid" checksum. Closing that gap
needs a signature from a key that lives outside GitHub's control entirely.

## Mechanism: SSH signatures, FIDO2 hardware key

Chosen over GPG/minisign: SSH signature verification (`ssh-keygen -Y sign` / `-Y verify`) is
already in every OpenSSH install, needs no new dependency on either side, and — the reason for the
FIDO2 requirement — supports **resident, touch-required hardware keys** (`ecdsa-sk` /
`ed25519-sk`). The private key material never leaves the hardware token, and every signature needs
a physical touch. A compromised CI runner or build machine cannot forge a release; it would need
the physical key in hand. This is the same trust anchor the fleet's `rotten-apple` master-identity
ceremony established (2026-07-16) — gestalt reuses that identity rather than minting its own
(per-project keys were the ruled-out footgun — see `~/code/REPOS/RELEASE.md`).

**The signing key must never be provisioned into CI.** That's the whole point — CI compromise is
exactly the threat this defends against. Releases are signed by hand, from the operator's own
machine, with the hardware key attached.

## Identity vs role — principal is WHO, namespace is WHAT-FOR

Per `~/code/REPOS/RELEASE.md` (the fleet's release doctrine): **principal** (`-I`) is the repo's
stable identity (`gestalt`); **namespace** (`-n`) is what a given signature authorizes
(`gestalt-release`). Never pass the same string for both. `allowed_signers` line format (one line
per key, exactly 4 when populated):

```
gestalt namespaces="gestalt-release,pills-tag" sk-ssh-ed25519@openssh.com <b64> ra-master-<n>
```

## One-time setup — `make sync-signers`, never hand-edit

```sh
make sync-signers
```

Rebuilds `packaging/release-signing/allowed_signers` from all 4 canonical pubkeys in
`~/.ssh/asuramaya-master/*.pub` (the operator's own key home; override with
`KEY_HOME=/path/to/asuramaya-master`). Always a full rebuild, never an append: RA's first ceremony
left 3 of 4 keys unpinned across other repos by appending one key at a time. Refuses to run unless
it finds exactly 4 canonical keys.

No embedded `install.sh` twin to keep in sync here, unlike coldspot: gestalt's `install.sh`
bootstrap doesn't read a sibling `allowed_signers` at fetch time the way a curl-pipe-only script
would need to — it currently does no verification of its auto-fetch at all (see "Known gap"
below), so there is nothing today for `sync-signers.sh` to embed a copy into. Should `install.sh`
grow real verification, extending `sync-signers.sh` with an embedded-twin step (coldspot's
`packaging/sync-signers.sh` is the reference shape) is part of that same future change, not
something to half-do here.

**Sequencing rule (do not skip):** `make sync-signers` populates the anchor. Run it as **step 2**
of [RELEASING.md](RELEASING.md)'s running order — after `make check` passes, strictly *before* the
tag that will ship it. `release.yml` builds the release tarball with `git archive` from the tag
itself, so whatever the anchor contains in the tagged tree is what ships, permanently (a sealed
release is never re-cut). Arming after tagging — the mistake this rule exists to prevent — would
ship that tag's tarball with a permanently empty anchor; `release.yml` now refuses to build a
release tarball from a tag whose anchor is empty, so getting this wrong fails loud instead of
shipping broken. (For a pill armed long ago, re-running `sync-signers` right before a later tag
doesn't matter either way, since every tag since arming already captures a populated anchor — the
ordering only bites a repo's first-ever release, which is where gestalt is now.) CI's
`signing-sync` check (`.github/workflows/signing-sync.yml`) separately confirms the anchor is,
at any point on `main`, either empty or exactly 4 well-formed lines.

## Per-release signing (operator, needs the FIDO2 key attached + a touch)

```sh
# Sign the checksum, not the tarball directly — gestalt.tar.gz.sha256 already
# covers gestalt.tar.gz via its checksum, so signing it transitively covers
# the whole release, and it's tiny (one line).
ssh-keygen -Y sign -f /path/to/id_asuramaya_master_N.pub -n gestalt-release \
  gestalt.tar.gz.sha256
# -> produces gestalt.tar.gz.sha256.sig

gh release upload vX.Y.Z gestalt.tar.gz.sha256.sig
```

## Verification (client side — not yet built)

```sh
sha256sum -c gestalt.tar.gz.sha256                          # artifact matches the manifest
ssh-keygen -Y verify -f packaging/release-signing/allowed_signers \
  -I gestalt -n gestalt-release -s gestalt.tar.gz.sha256.sig \
  < gestalt.tar.gz.sha256                                    # manifest carries the operator's hand
```

Exit 0 = valid signature from the pinned principal, over exactly those checksum bytes. Anything
else is a hard failure. Nothing in gestalt runs this yet — there is no `gestalt-update` (gestalt has
no auto-update timer at all today) and `install.sh` doesn't call it either. See the known gap below.

## Known gap: install.sh's bootstrap does not consume or verify this chain

`install.sh`'s one-line-install path fetches `https://api.github.com/repos/$REPO/releases/latest`
and extracts `tarball_url` — GitHub's own auto-generated archive of the tag (served from
`codeload.github.com`), **not** the named `gestalt.tar.gz` asset `release.yml` uploads — then pipes
it straight into `tar` and execs the extracted `install.sh`. No checksum, no signature, no anchor
lookup, at any point. This predates today's build and isn't introduced by it, but it means arming
`allowed_signers` and sealing a release protects nothing on the install path until `install.sh` is
taught to (a) fetch the actual `gestalt.tar.gz` release asset instead of the auto tarball, and (b)
verify it against this anchor the way coldspot's `install.sh` does against its own embedded twin.
That is a real behavior change to a script users already run unattended
(`curl | bash`-equivalent), and belongs in its own dedicated, tested pass — not bundled into
standing up the signing machinery itself. Tracked, not silently dropped.

## Vendored commons (Wave B)

`src/share/gestalt/lib/sutra.py` + `sutra_update.py` + `sutra_xen.py` (+ their `.version`/`.commit`
anchors) and `src/extension/gestalt@asuramaya/pill.js` are vendored, never hand-edited — `make
check-sutra` / `make check-pill-js` are the drift guards (integrity always; freshness as a
three-way LAG/DRIFT read against the canonical `~/code/REPOS/sutra` checkout when present).
