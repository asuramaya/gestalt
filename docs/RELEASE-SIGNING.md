# Release signing

Status: **unarmed.** `packaging/release-signing/allowed_signers` ships empty — gestalt has never
cut a release, and the ceremony that arms it hasn't run yet. `install.sh`'s one-line-install
bootstrap enforces verification against this anchor already (see "Client verification" below); it
just has nothing armed to check against yet, so every install today would refuse outright rather
than degrade — see [RELEASING.md](RELEASING.md) for the running order — **arm, then tag, then
seal, always in that order**; `release.yml` refuses to build a release tarball from a tag whose
anchor is still empty, so this is enforced, not just documented (Vajra found the ordering trap
this guards against, in mudra's own doc — mail #2891).

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

Also rebuilds `install.sh`'s embedded `RELEASE_ALLOWED_SIGNERS` twin from the same anchor content
(coldspot's `packaging/sync-signers.sh` is the reference shape): `install.sh`'s curl-pipe-bash
bootstrap fetches only itself over the network, so it can't read the sibling `allowed_signers` at
that point — the same content is embedded directly, byte-for-byte. CI's `signing-sync` check
compares the two exactly; drift between them is a failed build, not a warning.

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

## Client verification — `install.sh`'s one-line-install bootstrap

```sh
sha256sum -c gestalt.tar.gz.sha256                          # artifact matches the manifest
ssh-keygen -Y verify -f packaging/release-signing/allowed_signers \
  -I gestalt -n gestalt-release -s gestalt.tar.gz.sha256.sig \
  < gestalt.tar.gz.sha256                                    # manifest carries the operator's hand
```

Exit 0 = valid signature from the pinned principal, over exactly those checksum bytes. Anything
else is a hard failure. `install.sh`'s `verify_release_tarball()` implements exactly this: when run
without its sibling files (`curl -fsSL .../install.sh | bash`), it fetches the named
`gestalt.tar.gz` release asset (never GitHub's auto-generated `tarball_url` — that artifact isn't
something `release.yml` uploads, so no checksum or signature can ever cover it; gestalt used to
fetch that one, phanspeed hit and fixed the identical pattern first), verifies its sha256, then
verifies the signature against the embedded `RELEASE_ALLOWED_SIGNERS` twin. **No soft degrade,
unlike coldspot's own bootstrap:** an empty anchor, a missing `.sig` (release published but not yet
sealed), or a signature that doesn't verify are all a hard, loud refusal — never a silent
fallthrough to an unverified install. This is stricter than coldspot's own policy on purpose:
coldspot's degrade-to-sha256-only branch exists to accommodate releases that predate its own first
arming, and gestalt has no such era — `release.yml` refuses to tag while the anchor is empty (see
above), so a real gestalt release is always already armed by the time it exists, and a soft
fallback would just be an unused, riskier escape hatch. There is no `gestalt-update` (gestalt has
no auto-update timer at all today) — only the one-line installer runs this check, currently.

## Vendored commons (Wave B)

`src/share/gestalt/lib/sutra.py` + `sutra_update.py` + `sutra_xen.py` (+ their `.version`/`.commit`
anchors) and `src/extension/gestalt@asuramaya/pill.js` are vendored, never hand-edited — `make
check-sutra` / `make check-pill-js` are the drift guards (integrity always; freshness as a
three-way LAG/DRIFT read against the canonical `~/code/REPOS/sutra` checkout when present).
