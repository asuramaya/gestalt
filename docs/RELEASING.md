# Releasing gestalt

How a version becomes a signed release. The trust chain itself is described in
[RELEASE-SIGNING.md](RELEASE-SIGNING.md); this is the running order.

Three acts, two people, and the order does not bend: **arm, then tag, then seal.** A maintainer
prepares. The operator arms the trust anchor, by hand, from their own key home. Only then does the
maintainer tag and publish. The operator then signs, by hand, with a physical FIDO2 key. No
automation can stand in for either operator step, and the signing key never goes near CI.

**Why arm-before-tag, not after:** `release.yml` builds the release tarball with `git archive`
*from the tag* — whatever `packaging/release-signing/allowed_signers` contains in the tagged
tree is what ships, permanently, because a sealed release is never re-cut. A pill that armed long
ago never notices this, since every tag since has captured an already-populated anchor. It is only
a trap on a repo's **first-ever** release, where the anchor is legitimately still empty right up
until the operator's ceremony — exactly gestalt's position today. Get the order wrong once and the
first published tarball ships an empty anchor forever. `release.yml` also refuses to build the
tarball at all if the anchor is empty at tag time, so getting the order wrong is a red CI run, not
a silent defect — but the doc order below is what keeps you from hitting that guard in the first
place. (Found by Vajra, in mudra's own RELEASING.md, the only other repo in this position — see
mail #2891.)

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

## 2. The operator arms the anchor — BEFORE any tag exists

```
make sync-signers     # operator's hand, their key home, never CI, never the maintainer's
```

Rebuilds `packaging/release-signing/allowed_signers` from all 4 canonical keys and commits it to
`main` *before* step 3 ever runs. If this step is skipped or run after tagging, the tag's tree
still holds an empty anchor — see the warning above.

## 3. Tag and publish

```
git tag vX.Y.Z && git push origin vX.Y.Z
```

`release.yml` checks the tag against `packaging/VERSION`, refuses to proceed if the anchor is
still empty, then builds a source tarball and its SHA256 checksum, extracts the matching
`docs/CHANGELOG.md` section, and publishes the release unsigned. It signs nothing, on purpose: if
CI could sign, whoever compromised the workflow or the account could sign whatever they pushed,
and the anchor would be protecting nothing.

## 4. The operator seals it

```
ssh-keygen -Y sign -f /path/to/id_asuramaya_master_N.pub -n gestalt-release gestalt.tar.gz.sha256
gh release upload vX.Y.Z gestalt.tar.gz.sha256.sig
```

This runs through the family's seal desk in practice, which derives its queue from published
releases and shows anything published without a `.sig` as awaiting the seal.

## Where gestalt actually stands

Never tagged. `packaging/VERSION` currently reads what the file itself says (check it directly —
this doc doesn't duplicate the single source of truth). `packaging/release-signing/allowed_signers`
ships empty right now, correctly — the ceremony (step 2 above) simply hasn't happened yet. It stays
that way until the operator arms it, and **arming happens before the first tag, not after**: unlike
ramstein's v0.9.0 (armed one commit after an already-published unsigned tag, safe only because no
installed base existed yet to brick), gestalt's `release.yml` won't let a tag through with an empty
anchor at all, so there is no unarmed-first-release exception here to begin with.

`install.sh`'s one-line-install bootstrap now fetches the named `gestalt.tar.gz` release asset and
verifies it against this same anchor (coldspot's shape, no soft degrade — see
[RELEASE-SIGNING.md](RELEASE-SIGNING.md)) rather than GitHub's unverifiable auto-generated
`tarball_url`, which it used to. This closed while the anchor was still empty and the bootstrap had
never once run for real (`releases/latest` 404s — gestalt has never released), so there was no
installed base to break by changing it.

## Rules that don't bend

* A sealed release is never re-cut. If something is wrong with it, the fix is the next version.
  Re-cutting breaks every copy that already verified it.
* The signing key never enters CI, in any form, for any reason.
* `make sync-signers` is not part of day-to-day dev. It rebuilds the trust anchor from the
  canonical keys and only ever runs as step 2 of a real release — never earlier (nothing to arm
  for yet), never later (see "why arm-before-tag" above).

## When it goes wrong

**The tag assertion fails** means `packaging/VERSION` and the tag disagree. Fix it, delete the
tag, tag again.

**"anchor is empty at this tag" fails the release job** means step 3 ran before step 2 — the tag
was pushed while `packaging/release-signing/allowed_signers` was still empty. Delete the tag,
have the operator run `make sync-signers` and commit the result to `main`, then tag again.

**A client reports "armed but release is unsigned"** means the release was published and never
sealed. Nothing is broken in the artifact; it needs the operator's signature uploaded.
