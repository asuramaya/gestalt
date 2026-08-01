# Changelog

## 0.2.1 — first release

gestalt has never been tagged before this. Everything below accumulated on `main` since the
initial import; this entry is the summary a normal per-release note would never have carried on
its own, since no earlier release existed to carry it.

- **Core pointing pipeline**: MediaPipe head pose → 1€ filter → speed-scaled integration →
  DynaSpot magnetism (velocity-gated soft pull toward the nearest AT-SPI/CV target, no hard snap)
  → pinch-to-click over `uinput`, plus a GNOME Quick Settings pill and top-bar HUD. See
  `docs/POINTING.md` for the cited research basis.
- **The eye channel was tried and retired.** `docs/POINTING.md §VERDICT`: iris gaze measured dead
  on both cameras tested — not a tuning problem, a physical one. `gaze_fixation` now defaults off;
  the verdict is recorded rather than silently dropped.
- **KTM endpoint prediction replaced it.** An aimed reach is ballistic: once deceleration starts,
  the minimum-jerk closed form (Flash & Hogan 1985) converts the decel-side speed ratio into
  remaining distance, fused with AT-SPI/CV targets into a click-history-weighted posterior
  (`gestalt/pointing/endpoint.py`). A confident prediction pre-acquires focus mid-flight — the
  intent commits before arrival — with every guard failure degrading to plain settle-time
  acquisition, never a wrong hard commit. Self-tuning: the fixation threshold that fed the (now
  retired) eye channel was itself made self-calibrating (rolling-median dispersion) before the
  eye channel was cut.
- **18 audited defects fixed** across the daemon, engine, gestures, and installer in one pass —
  the highlights: a live pinch-drag could leave its button pressed forever if the loop stopped
  reconciling mid-hold; a dead camera silently froze the status at green "Tracking" instead of
  reporting loss; a second `gestaltd` could silently steal the control socket from a live one
  instead of refusing to start; the AT-SPI provider burned ~27% CPU 24/7 walking the accessibility
  tree while disarmed (now SIGSTOPped/SIGCONTed with arm state); `install.sh` is now
  reinstall-safe (a bare `cp -r` used to nest the tree and leave stale modules running after an
  "upgrade").
- **Dual-use groundwork**: a shared `resolve_target()` primitive (`gestalt/targets/resolve.py`)
  replaced two near-identical nearest-target scans that had drifted independently inside
  `pointer.py`, with an optional name-hint for label disambiguation. `gestalt-mcp` then exposed
  the same target-perception + `uinput`-actuation substrate the human pointer uses as MCP tools
  (`list_targets`/`active_window`/`resolve`/`click`/`scroll`/`drag`), gated behind an
  active-window allowlist that refuses everywhere by default — opt-in developer tooling, not
  wired into `install.sh`. Live-verified against a real running desktop, not simulated.
- **Family conformance audited** (`FAMILY-AUDIT.md`): gestalt is the family's first user-session
  daemon — camera + `uinput` via group membership, not root — which reshapes several
  socket/privilege doctrine axes into domain exemptions rather than violations.
- **REPO-STANDARD.md convergence** (this morning): root tree cut from 20 tracked-file rows to 11
  (`src/{bin,data,extension,share,gestalt,providers,mcp,research}` + the required doc/packaging
  shape), sutra backbone adopted for the control socket (0.1.0 → 0.10.1, behavior-preserving —
  same socket contract, same double-daemon guard, same error text), a real `check-repo` structural
  gate, and CI wired up for the first time.
- **CI actually went green for the first time** (it had never passed on a real runner before
  today, masked locally by dev machines already having `numpy`/`ruff` installed) — see this
  release's own `.github/workflows/ci.yml` for the fix, and `docs/RELEASING.md` /
  `docs/RELEASE-SIGNING.md` (new) for the release machinery this entry ships alongside.
