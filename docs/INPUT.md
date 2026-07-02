# Input: gestures, clicks, drag, injection, cursor rendering

How a hand shape becomes a click/drag, and how the cursor is drawn. Code:
`gestalt/gesture/pinch.py`, `gestalt/gesture/gestures.py`, `gestalt/gesture/inject.py`,
`gestalt/overlay/cursor.py`, `gestalt@asuramaya/extension.js` (ShellCursor).

## Hand detection confidence (`hand_min_detection/presence/tracking`)

Upstream of BOTH modes below: MediaPipe's `GestureRecognizer` has to see a hand
at all before pinch/gesture geometry runs. A hand far from the camera or at an
off-axis angle is fewer pixels and a less canonical shape — the same condition
`face_min_detection/presence/tracking` (see POINTING.md) exist to survive for
the face, at deliberately LOW defaults. The hand path never got the same
treatment: hardcoded at `0.3/0.3/0.3` until 2026-07 (an oversight, not a
tradeoff — reported as "struggles from weird angles or odd distances" arming
the hand). Now exposed, default `0.15/0.15/0.15`, hot-settable (`set
hand_min_detection 0.1` takes effect immediately, no restart — same pattern as
`camera`). Raise them back if a lower floor starts false-triggering on
non-hand shapes; MediaPipe's own gesture-confidence gate (`gesture_confidence`,
gestures mode only) is a second, independent filter downstream of this one.

## Two gesture modes (`gesture_mode: pinch | gestures`)

### pinch (`pinch.py`) — the haptic default
Independent thumb-to-fingertip tap detectors (default index→left, pinky→enter, far
apart so they never mix). A tap = thumb-tip↔finger-tip distance / palm < `pinch_close`,
with an extension gate (finger must be extended, not a fist). Hysteresis: must
re-open past `pinch_rearm` before the next fire. Its edge is **free haptic feedback**
(your fingers touch). Its weakness: the distance keys on the two NOISIEST landmarks
(both fingertips), a small jitter-sensitive value with self-occlusion — which
**regressed hard on IR** (low-res 576×360, dim, 7.5fps).

### gestures (`gestures.py`) — IR-robust trained classifier
Consumes MediaPipe GestureRecognizer's TRAINED hand-shape labels (we were already
computing them and throwing them away). Extension-based, confidence-scored, combos
resolved as units (no Pointing_Up flicker into Victory). Default `gesture_bindings`:
Pointing_Up→left, Victory→right, Thumb_Up→enter, Open_Palm→escape (editable;
`gesture_confidence` 0.6). No haptic — that's pinch's edge — but far less sensitive
to fingertip-landmark noise. Both detectors are held; `gesture_mode` picks per-frame
(live A/B). Obs: `status().gesture`, `gesture_score`, `gesture_mode`.

**Pitfalls we navigated:** the `Action` interface is unreliable; thumb extension is
the least reliable finger; hand-rolled combos (index+middle) are fragile because the
transition passes through index-only — the TRAINED classifier avoids that.

## Steady-Clicks commit (both detectors)

A confirmed gesture fires only when the head is SETTLED (`head_speed <=
commit_velocity_gate`) — never mid-flight. If still moving, it HOLDS (`waiting`
state in readiness) and fires the instant it settles, at the live cursor position.
Plus debounce (`pinch_confirm_frames`), cooldown (`cooldown_s`), and rearm (the
gesture must change/neutralize before re-firing — no chatter while held).

## Hold / drag (`gesture_hold`, default ON)

Clicks WERE atomic (`inject.click_at`: warp+press+20ms+release; the old
`commit_freeze_ms` was a dead unwired knob). Now a press/release lifecycle:
- Detectors expose `.engaged` (the held action, or None) once a press commits
  (settled+confirmed+rearmed). Release is debounced (`gesture_release_frames`,
  anti-IR-dropout).
- `Engine._reconcile_hold(ps)` drives the PHYSICAL press/drag/release by
  RECONCILING against `engaged` every frame — START on the press edge (button/key
  down at the settled point), CONTINUE (freeze at the press point until the cursor
  leaves `drag_start_px` 45, then DRAG — real pointer follows the head via
  `inject.move_to`), RELEASE when `engaged` clears. **Reconciling against `engaged`
  every frame is what guarantees a button is NEVER left stuck down.**
- A quick gesture = clean frozen click; held + moved = drag; held key = press-and-
  hold (OS repeat). Safeties: watchdog `hold_timeout_s` (20s) force-release,
  `close()` releases, `apply_config` clears `engaged` (so a `set`/mode-switch can't
  strand a button), calibration force-releases, and **disarm releases** (else a
  mid-drag button stays down while paused).
- **Burst** (rapid repeats) is framerate-limited (7.5fps IR), NOT shape — won't
  improve until the framerate is back. **Hold** is fps-tolerant and works now.

## Injection (`gesture/inject.py`, evdev/uinput)

`/dev/uinput` is world-writable → no root, no ydotool. An absolute pointer device
(ABS range = the virtual desktop) + a keyboard device. `click_at` (atomic), and the
hold primitives `begin`/`end` (button/key down/up) + `move_to` (warp only, for
drag) + `is_click`. **The ABS range is fixed at device creation**, so a display
change recreates the Injector (`Engine.refresh_layout`). Injection is at the input
layer BELOW the compositor — so clicks land correctly even when the cursor dot is
hidden behind shell chrome.

## Cursor rendering — two surfaces

- **XWayland dot** (`overlay/cursor.py`): borderless, override-redirect (always-
  above layer), XShape empty INPUT region (click-through). It CANNOT draw above
  GNOME Shell's own chrome (top bar, quick settings, notifications) — those are
  Clutter actors above all X windows, and Mutter has no wlr-layer-shell. So the dot
  is occluded by shell menus (clicks still work — injection is below the compositor).
- **In-shell cursor** (`cursor_in_shell`, needs 1 logout to load the new extension):
  the daemon writes the live "x y snapped" to `RUNTIME/cursor` every frame
  (`Engine._render_cursor`); when on, it HIDES the XWayland dot
  (`Cursor.set_hidden`, opacity 0) and the extension's `ShellCursor` (a non-reactive
  St.Widget ring added to `Main.uiGroup`, fast 33ms poll) draws the ring ABOVE
  menus, re-raised each tick. Position = `coord / scale_factor` (physical→logical;
  HiDPI). Gated on `status().cursor_in_shell`; pill switch "Cursor above menus".
  KEEP it FALSE until after the logout, or you'll have no visible cursor (old
  extension can't render it). HiDPI fractional-scale offset is untested (uses
  integer `scale_factor`).
