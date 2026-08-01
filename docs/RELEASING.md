# Releasing gestalt

How a version becomes a signed release. The trust chain itself is described in
[RELEASE-SIGNING.md](RELEASE-SIGNING.md); this is the running order.

Two people are involved and only one of them can finish it. A maintainer prepares and tags. The
operator signs, by hand, with a physical FIDO2 key. No automation can stand in for that step, and
the signing key never goes near CI.

## 1. Prepare

Bump `packaging/VERSION` if the release warrants it — it is the one version constant; no binary in
this repo carries a literal of its own to drift (`make check-repo` asserts that). `release.yml`
checks the tag against it directly.

Write the `docs/CHANGELOG.md` entry for the release. Small, focused releases are easier to sign off
on than a pile of unrelated changes.

Run the checks:

```
make check    # lint, py_compile, node --check, hardware-free tests, check-sutra,
              # check-pill-js, check-vendored-path-all, check-repo
```

There is no `make smoke` / `make attack` here yet — gestaltd has no fixture-driven boot harness or
adversarial socket fuzzer today (see docs/ARCHITECTURE.md's Standard exemptions table). Until one
exists, `make check`'s static coverage plus a manual `gestaltctl status` against a running daemon
is what a release rests on.

## 2. Tag and publish

```
git tag vX.Y.Z && git push origin vX.Y.Z
```

`release.yml` then builds a source tarball and its SHA256 checksum, extracts the matching
`docs/CHANGELOG.md` section, and publishes the release unsigned. It signs nothing, on purpose: if CI
could sign, whoever compromised the workflow or the account could sign whatever they pushed, and
the anchor would be protecting nothing.

## 3. The operator seals it

```
make sync-signers     # only in the same act as a signing ceremony, see RELEASE-SIGNING.md
ssh-keygen -Y sign -f /path/to/id_asuramaya_master_N.pub -n gestalt-release gestalt.tar.gz.sha256
gh release upload vX.Y.Z gestalt.tar.gz.sha256.sig
```

This runs through the family's seal desk in practice, which derives its queue from published
releases and shows anything published without a `.sig` as awaiting the seal.

## Where gestalt actually stands

Never tagged. `packaging/VERSION` currently reads what the file itself says (check it directly —
this doc doesn't duplicate the single source of truth). `packaging/release-signing/allowed_signers`
ships empty, as every pill's does before its first ceremony: an anchor that starts unarmed can only
ever be armed by a deliberate, later act, never accidentally. **The first tagged release ships with
an empty anchor**, same as RAMstein's v0.9.0 — arming is a separate, deliberate ceremony the
operator chooses to run in the same act as *a* release, not necessarily the first one.

**Known gap, not yet closed:** `install.sh`'s one-line-install bootstrap (`if [[ ! -f
"$SRC/src/bin/gestaltd" ]]`) fetches GitHub's auto-generated `tarball_url` from the
`releases/latest` API response — the archive GitHub builds automatically from the tag, not the
named `gestalt.tar.gz` asset `release.yml` uploads — and performs no checksum or signature check on
it at all before executing the extracted `install.sh` as bash. This is unrelated to today's build
(the signing chain below is being stood up to match family shape and unblock a future ceremony) but
it means arming the anchor will not, by itself, protect the one-line install path the way it does
for coldspot's. See [RELEASE-SIGNING.md](RELEASE-SIGNING.md)'s own note on this. Closing it means
teaching `install.sh` to fetch the named release asset and verify it (coldspot's `install.sh` is the
reference shape) — a real behavior change to the live bootstrap, deliberately not attempted in this
pass.

## Rules that don't bend

* A sealed release is never re-cut. If something is wrong with it, the fix is the next version.
  Re-cutting breaks every copy that already verified it.
* The signing key never enters CI, in any form, for any reason.
* `make sync-signers` is not part of day-to-day dev. It rebuilds the trust anchor from the
  canonical keys and only ever runs in the same act as a signing ceremony.

## When it goes wrong

**The tag assertion fails** means `packaging/VERSION` and the tag disagree. Fix it, delete the
tag, tag again.

**A client reports "armed but release is unsigned"** means the release was published and never
sealed. Nothing is broken in the artifact; it needs the operator's signature uploaded.
