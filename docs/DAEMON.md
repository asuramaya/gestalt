# Daemon, IPC, control & concurrency

`bin/gestaltd` is a single-process user daemon. This documents its loop, the
control protocol, the concurrency model, and the bugs the controls audit found.

## The main loop

```
ipc.ControlServer(self.handle).start()        # threaded socket server
engine = Engine(cfg); engine.open()           # lazy CV import; status-only if it fails
while running:
    if engine and cfg["armed"]:
        with self._lock: frame = engine.step()    # ONE camera frame, paces the loop
    else:
        with self._lock: engine.pump()            # disarmed: STILL drain the event queue
    if now - self._last_layout > 2:               # catch monitor plug/unplug
        with self._lock: engine.refresh_layout()
    ipc.write_status(self.status())
    if not stepped: time.sleep(0.05)              # idle heartbeat
```

## Concurrency model (the lock) — `Daemon._lock = threading.RLock()`

`ipc.ControlServer` runs `handle()` in **per-connection threads**; the engine is
stepped on the **main thread**. Without serialization, a command's `apply_config` /
camera-reopen / provider-restart / monitor-switch races a live `step()` reading
that state → sporadic crashes. So:
- The main loop wraps `step()` / `pump()` / `refresh_layout()` in the lock.
- Every engine-touching handler goes through `self._apply()` (lock-guarded
  `apply_config`) or `self._engine(fn)` (lock-guarded method call).
- It is an **RLock, not Lock**, because a diag-window keypress re-enters on the
  MAIN thread: `pump() -> _key() -> on_command(handle) -> _apply()` would
  self-deadlock on a plain Lock. RLock lets the same thread re-acquire.

## The "pause crashes the daemon" bug (OOM, not a code crash)

Symptom: `status=9/KILL`, NO traceback, ~10 min after disarming from the pill.
Root cause: the disarmed branch never called `engine.step()`, which is the ONLY
place the **pygame/SDL event queue is drained**. Undrained, X events pile up
unbounded → RSS climbed to ~700MB → the kernel OOM killer SIGKILLed it. FIX:
`Engine.pump()` (drain events, no pipeline) is now called every idle tick.
Validated: RSS holds FLAT at ~444MB while paused (was climbing). The IPC server
already `try/except`s the handler, which is why a malformed command can't crash it
— consistent with this being an OOM, not an exception.

## Control protocol (`gestalt/ipc.py`, `bin/gestaltctl`, `extension.js`)

Line-delimited JSON over `RUNTIME/control.sock`. Commands (handler in
`Daemon.handle`): `status`, `arm`, `disarm` (also releases any held button),
`recenter`, `monitor <next|up|down|idx>`, `diag [on|off]`, `calibrate`, `record`,
`mode <mouse|joystick|comfort>`, `recal [reset|on|off]`, `set <key> <json-value>`.
`set` merges into cfg and runs the `sanitize_config` chokepoint, so the CLI, the
pill, and the file all share one set of invariants. `gestaltctl set k v` parses `v`
as JSON (so `set cursor_in_shell false` is a real bool; lists/dicts work too).

## Config chokepoint (`gestalt/config.py`)

EVERY load and every socket `set` passes through `sanitize_config()` — it clamps
numerics (`_RANGES`), validates enums/bools, and rejects NaN/inf (NaN slips all
comparisons; inf overflows `int()`). The hardware-free fuzz (`tests/test_config.py`,
8000+ cases) is the guarantee hostile input can't corrupt state. Adding a field:
DEFAULTS → clamp/whitelist here → consume in the module → surface in status/pill →
fuzz still green.

## Live display-layout refresh — `Engine.refresh_layout()`

The monitor layout was read ONCE at `open()` and went stale on plug/unplug,
offsetting every coordinate ("boxes in the top-left quadrant"). Now re-queried every
2s; on change it re-derives: `Pointer.set_bounds(vw,vh,monitors)` (bounds +
active-monitor origin + comfort reseat), recreates the `Injector` (ABS range is
fixed at creation), and drops the target overlay to recreate at the new size.

## Persisted config

`~/.config/gestalt/config.json` (atomic write). NOTE: a default change in
`config.py` does NOT affect a machine with a saved config — you must `set` the key
live (or delete the file). This bit us when disabling CV: changing the default to
`["atspi"]` did nothing until `set providers '["atspi"]'`.

## Runtime files (`$XDG_RUNTIME_DIR/gestalt/`)

- `status.json` — health snapshot (extension reads 1s). STALE right after a restart
  (old PID's write) — wait ~3s.
- `control.sock` — command socket.
- `cursor` — live "x y snapped" for the in-shell cursor (written every frame).
- `targets/atspi.json`, `targets/cv.json` — provider outputs (merged by the Registry).
