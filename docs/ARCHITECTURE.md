# Architecture

A contributor-facing map of how Gestalt fits together. The HCI basis for the
pointing model is in [POINTING.md](./POINTING.md); subsystem deep-dives are in
[CAMERA.md](./CAMERA.md), [TARGETS.md](./TARGETS.md), [INPUT.md](./INPUT.md), and
[DAEMON.md](./DAEMON.md). This file is the index and the cross-cutting concerns.

## What shapes everything: it runs as *you*

Gestalt needs four things, all session-scoped, none privileged:

| Need | Interface |
|------|-----------|
| Head pose + hand landmarks + iris + mouth | webcam `/dev/video*` (MediaPipe FaceLandmarker + GestureRecognizer) |
| Inject cursor moves + clicks | `/dev/uinput` (evdev; no root — udev rule grants group access) |
| Draw a cursor over everything | XWayland override-redirect, click-through (XShape) overlay — and, above shell menus, a GNOME-extension Clutter actor |
| Know where the clickable things are | **AT-SPI accessibility tree** (the app's own widget hierarchy) |

`gestaltd` is a **user systemd service** bound to `graphical-session.target` (not a
root daemon). The control socket only ever faces its owner.

## The pipeline, one frame at a time (`gestalt/engine.py`)

```
 camera frame ─► dark-frame gate (skip IR strobe blacks, relative to peak)
   │              └─ skipped? -> _coast() (extrapolate cursor, keep moving)
   ├─► input.HeadTracker  (MediaPipe FaceLandmarker)
   │     ├─ facial transform matrix -> head forward vector -> 1€ filter -> signal
   │     ├─ iris-in-eye gaze + I-DT fixation         (input/gaze.py)
   │     └─ perioral (mouth/nose) in head-local frame (input/perioral.py)  [experiment]
   ├─► targets.Registry.read()  (AT-SPI + optional CV) -> TargetTracker (stabilize)
   ├─► pointing.Pointer.update(head, targets)
   │     ├─ control mode: mouse | joystick | COMFORT (absolute, the primary)
   │     ├─ comfort fine-aim stack: RubberEdge gain + Angle-Mouse follow + fixation gate
   │     ├─ multi-monitor: map to ACTIVE monitor, cross by over-deflection angle
   │     └─ focus-hysteresis magnetism (acquire->stick->break) on stable targets
   ├─► gesture: pinch detector OR trained-classifier detector -> Steady-Clicks commit
   │     └─ hold/drag lifecycle (press on settle, release on gesture-end)
   ├─► gesture.Injector (uinput): warp + click, or button-down/drag/up
   └─► overlay.Cursor (XWayland) OR RUNTIME/cursor file -> extension Clutter actor
```

## Coordinate space (READ THIS — it bites)

Everything works in **physical virtual-desktop pixels**, sourced from XRandR via
`gestalt/overlay/monitors.py` (`Monitors`): `vw×vh` bounding box + per-monitor
rects + the active monitor. The injector's evdev ABS range = `vw×vh`; the overlay
covers it; comfort maps to the *active monitor's* rect then offsets to virtual.

- **The layout is re-queried every 2s** (daemon loop) — it used to be read once at
  startup and went stale on monitor plug/unplug, offsetting every coordinate (the
  "boxes in the top-left quadrant" bug). On change, `Engine.refresh_layout()`
  re-derives bounds, the injector ABS range, and reseats comfort.
- **HiDPI:** the in-shell extension cursor divides by `scale_factor` (physical→
  logical); the XWayland overlay/injection are physical and match clicks. On the
  dev machine scale is 1.0, so this path is lightly tested.

## Component map (files)

```
 gestalt@asuramaya/extension.js   GNOME Shell extension (GJS)
   ├─ top-bar HUD (colour-coded glance, click -> diag)
   ├─ Quick Settings pill (controls + metrics)
   └─ ShellCursor (Clutter actor, ABOVE menus — reads RUNTIME/cursor at 30fps)
   reads  RUNTIME/status.json (1s), RUNTIME/cursor (33ms)
   writes RUNTIME/control.sock (line-JSON commands)

 bin/gestaltd   user daemon (bundled uv venv, Python 3.12)
   main loop: lock-guarded engine.step()/pump(), status write, 2s layout refresh
   ├─ gestalt/config.py    DEFAULTS + sanitize_config (the ONE chokepoint) + _RANGES
   ├─ gestalt/health.py    one health state for all surfaces
   ├─ gestalt/ipc.py       status.json writer + threaded control.sock server
   ├─ gestalt/engine.py    owns camera; drives the pipeline above; refresh_layout()
   ├─ gestalt/diag/        diagnostics window (camera + live pipeline overlay)
   ├─ gestalt/input/       head.py, gaze.py, perioral.py, body.py, torso.py, onefilter.py
   ├─ gestalt/pointing/    pointer.py, comfort.py, track.py, recalibrate.py, neutral.py
   ├─ gestalt/gesture/     pinch.py, gestures.py, inject.py
   ├─ gestalt/overlay/     cursor.py, monitors.py, targets_overlay.py
   ├─ gestalt/targets/     registry.py (spawns + merges providers; atspi-authoritative)
   └─ gestalt/record.py    JSONL session recorder (head + perioral + clicks)

 providers/   subprocesses (the "cheap universal boxes" layer)
   ├─ atspi_provider.py   SYSTEM python (gi/Atspi) — THE target source; active-window only
   └─ cv_provider.py      gestalt venv (cv2) — DEAD END, default OFF (see TARGETS.md)

 research/   offline analysis (perioral_analysis.py, selfsup_prototype.py)
```

## Daemon threading (the crash audit found these — see DAEMON.md)

- `ipc.ControlServer` runs `handle()` in **per-connection threads**; the engine is
  stepped on the **main thread**. A `threading.RLock` (`Daemon._lock`) serializes
  ALL engine access so a command's `apply_config`/camera-reopen/provider-restart
  can't race a live frame. RLock (not Lock) because a diag-window keypress re-enters
  on the main thread (`pump -> _key -> handle -> _apply`).
- **Disarmed must still `pump()`** the SDL/X event queue every idle tick, or it
  grows unbounded and the OOM killer SIGKILLs the daemon minutes after you pause.
- Handlers go through `Daemon._apply()` / `_engine(fn)` so the lock is uniform.

## Observability (three tiers)

| Tier | Surface | Owner | Answers |
|------|---------|-------|---------|
| Glance | top-bar HUD | extension | colour-coded state + fps |
| Detail | Quick Settings pill | extension | mode, targets, snap, pinch/gesture readiness, recal |
| Deep | diagnostics window | daemon (`gestalt/diag/`) | mesh, skeleton, comfort envelope, gaze/fixation, gains |

Health states (`health.py`, mirrored in `extension.js`): `off`, `no_engine`,
`searching`, `tracking`, `degraded` (past pitch limit), `lost`.

## CI-safe frame

`bin/gestaltd`, `config.py`, `ipc.py`, `health.py` import **stdlib only** — the
daemon frame, the config fuzz (`tests/test_config.py`, 8000+ cases), and
`node --check` on the extension all run with no camera/display/venv. The CV stack
is imported lazily in `engine.py`; missing => status-only mode, not a crash.

## Deploy / iterate (no logout for daemon code)

```
cp -r gestalt/* ~/.local/share/gestalt/gestalt/
cp providers/*.py ~/.local/share/gestalt/providers/      # providers are SEPARATE
cp bin/gestaltd bin/gestaltctl ~/.local/share/gestalt/bin/
systemctl --user restart gestalt.service                 # ~3s; status.json stale until then
```
Only `extension.js` needs a **logout** to reload (GNOME). `make check` must pass
(ruff + config fuzz + py_compile + `node --check` extension).

## Adding a config field
1. `DEFAULTS` in `config.py`  2. clamp in `sanitize_config` (+ `_RANGES`)
3. (bool/enum: add an explicit cast/whitelist line)  4. consume in the module
5. surface in `status()` + pill if user-facing  6. fuzz still passes.
