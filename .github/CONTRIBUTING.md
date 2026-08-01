# Contributing to Gestalt

Thanks for your interest! Read [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)
before changing much — it has the repo map and the daemon/CLI/pill split,
and [docs/POINTING.md](../docs/POINTING.md) for why the pointing model is
built the way it is (every stage is a cited HCI technique, not tuned by
feel).

## Dev setup

No root, no system-wide install needed to iterate on the daemon frame:

```bash
python3 -m py_compile src/bin/gestaltd src/bin/gestaltctl
python3 tests/test_config.py       # config fuzz, 8000+ cases, no camera/venv
python3 tests/test_endpoint.py     # KTM endpoint predictor, synthetic reaches
python3 tests/test_resolve.py      # target resolver + name-hint filtering
```

The full CV/input stack (mediapipe, opencv, evdev, ...) only exists in the
bundled venv `install.sh` builds — `make install` first if you need to run
the daemon for real. See [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)'s
"Deploy / iterate" section for pushing a code change to an installed daemon
without a full reinstall.

## Before opening a PR

- `make check` passes: lint (ruff + shellcheck), the hardware-free tests
  above, `node --check` on the extension, `check-sutra`/`check-vendored-path`
  (the vendored sutra commons resolve to the copy this repo actually ships),
  and `check-repo` (the family's structural gate, REPO-STANDARD.md §5).
- Any new socket/config field is **typed and clamped** in `sanitize_config`
  (`gestalt/config.py`) — the one chokepoint every config load and every
  socket `set` passes through. A tampered config can tune within clamps; it
  can never grant new behaviour.
- The failsafe invariant is compiled in, never configurable: **the pointer
  is always killable by keyboard** — the kill-hotkey and
  `systemctl --user stop` both go around the daemon's own socket (a wedged
  daemon may never read a command) straight through the kernel. A PR that
  makes this optional, or routes it through anything the daemon itself has
  to cooperate with, will be rejected.
- gestaltd runs as **you**, not root — it needs the webcam and
  `/dev/uinput` via group membership, never a privilege escalation. Keep it
  that way.
- Never hand-edit `src/share/gestalt/lib/sutra*.py` or `sutra.mk` — re-vendor
  with `bash ~/code/REPOS/sutra/vendor.sh src/share/gestalt/lib
  src/extension/gestalt@asuramaya --bootstrap=gestalt`.

## License

By contributing you agree your contributions are licensed under
**GPL-3.0-or-later**, matching the project.
