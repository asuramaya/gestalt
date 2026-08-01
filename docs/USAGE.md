# Usage

## The pill

GNOME Quick Settings gets a Gestalt tile after install (log out/in once for
the extension to load). Click it to arm; the top-bar HUD shows a
colour-coded glance (searching/tracking/degraded/lost) and fps. The pill's
diagnostics toggle opens a window with the live camera feed, mesh/skeleton
overlay, comfort envelope and gaze/fixation readouts — the deep tier of the
three observability tiers (see [ARCHITECTURE.md](ARCHITECTURE.md)).

## `gestaltctl` — the verb CLI

```
gestaltctl status                  # full health/tracking/config snapshot
gestaltctl arm | disarm            # start/stop tracking (also toggled by the pill)
gestaltctl recenter                # re-seat the neutral head pose

gestaltctl mode [mouse|joystick|comfort]   # switch control mode (no arg = report)
gestaltctl record [on|off]          # log training data (signal stream + click anchors)
gestaltctl calibrate [on|off]       # look-and-pinch calibration (generates clean labels)
gestaltctl diag [on|off]            # toggle (or set) the diagnostics window
gestaltctl recal [reset|on|off]     # reset / toggle the implicit recalibration
gestaltctl envelope reset           # re-seed the comfort ROM range from priors
                                     #   (fixes a range stretched by one big reach)
gestaltctl set <key> <value>        # value parsed as JSON, falls back to string
```

Every command is one JSON line over `$XDG_RUNTIME_DIR/gestalt/control.sock`;
`gestaltctl` is a thin client (`sutra.request`) — `gestaltctl status | jq` for
scripting, or `nc -U` the socket directly if you're debugging the wire
format itself.

## Config

`$XDG_CONFIG_HOME/gestalt/config.json` (usually `~/.config/gestalt/`), seeded
on first install, never overwritten by a reinstall. Hot-reloadable: `gestaltctl
set <key> <value>` writes through `sanitize_config` — the one chokepoint
every load and every socket `set` passes through — so a bad value clamps to
range rather than corrupting state. See [ARCHITECTURE.md](ARCHITECTURE.md)
§"Adding a config field" for the fields' own reference (`gestalt/config.py`'s
`DEFAULTS`/`_RANGES` are the source of truth; this file doesn't duplicate
the table, since it drifts the moment a field is added and the doc isn't).

## Panic / safety

The pointer is always killable by keyboard — a global kill-hotkey and
`systemctl --user stop gestalt.service` both SIGKILL the daemon; the kernel
tears down the uinput device and any held click releases with it. That's
the failsafe invariant this daemon compiles in, the family's
cool-beats-quiet analog (see [FAMILY-AUDIT.md](FAMILY-AUDIT.md)).

## Troubleshooting

- **`gestaltd not reachable`** from `gestaltctl` — the daemon isn't running:
  `systemctl --user status gestalt.service`.
- **Cursor stuck / stretched comfort range** — `gestaltctl envelope reset`
  re-seeds the comfort zone from priors after one big reach stretched it.
- **Camera not found** — `gestaltctl status` reports `no_engine`/"No
  camera/CV" when the CV stack or the webcam device isn't available; the
  daemon still runs in status-only mode rather than crashing.
- **Extension not showing after install** — log out and back in once;
  Wayland reloads the shell for a newly-enabled extension, and code changes
  after that need the same (`gnome-extensions disable/enable` alone only
  re-fires lifecycle hooks, it doesn't re-import the JS module).
