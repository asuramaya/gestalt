# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Trained-classifier gesture detection — the IR-robust alternative to pinch.

Pinch keys on the thumb-tip↔finger-tip distance: the two noisiest landmarks, a
small distance very sensitive to jitter, and they self-occlude when they touch —
all of which degrade badly on low-res IR. This detector instead consumes
MediaPipe GestureRecognizer's *trained* hand-shape labels (Pointing_Up, Victory,
Thumb_Up, Open_Palm, …), which key on whole-finger extension, carry a confidence
score, and resolve combos as single units (no Pointing_Up flicker on the way into
Victory). No haptic feedback — that's pinch's edge — but far more reliable when
the fingertip landmarks are mush.

Same contract as PinchDetector so the engine can swap them:
  * Steady-Clicks commit — fire only when the head is settled (else hold + fire
    on stillness), so a click never lands mid-flight.
  * rearm — the gesture must change (to neutral or a different bound gesture)
    before the same one can fire again, giving deliberate taps without chatter.
"""
from __future__ import annotations

import time

from .pinch import Fire


class GestureDetector:
    def __init__(self, cfg: dict):
        self.apply_config(cfg)
        self._last_fire = 0.0
        self._fired_g = None      # the gesture that last fired (rearm reference)
        self._armed = True        # may fire (gesture differs from the last that fired)
        self._cand = None         # gesture currently accumulating confirm frames
        self._below = 0
        self._waiting = False     # confirmed, holding for head stillness
        self._cur = None          # current accepted gesture (debug/readiness)
        self.engaged = None       # action currently HELD (hold mode); drives the drag
        self._eg_gesture = None   # the gesture holding it
        self._rel = 0             # consecutive frames the held gesture has been absent

    def apply_config(self, cfg: dict):
        self.cfg = cfg
        self.engaged = None        # drop any hold on a config change (engine releases)
        self._eg_gesture = None

    def reset(self):
        """Hand left frame — drop to neutral. The engine releases any held button
        on its own (engaged -> None reconciliation), so this never sticks."""
        self._cand = None
        self._below = 0
        self._waiting = False
        self._cur = None
        self._armed = True
        self._fired_g = None
        self.engaged = None
        self._eg_gesture = None

    def readiness(self) -> dict:
        if self.engaged is not None:
            return {self._eg_gesture or "—": "holding"}
        state = "waiting" if self._waiting else ("ready" if self._armed else "idle")
        return {self._cur or "—": state}

    def update(self, gname, gscore, cursor_xy, head_speed: float,
               armed: bool) -> tuple[list[Fire], str | None, list[float]]:
        """gname/gscore = top GestureRecognizer label + score for the hand (or
        None). In hold mode the press/release is driven by `self.engaged` (read by
        the engine); in tap mode it returns an atomic Fire. Returns (fires,
        current_gesture_dbg, [score])."""
        if gname is None or gname == "None" or gscore < self.cfg["gesture_confidence"]:
            gname = None
        self._cur = gname
        if gname != self._fired_g:       # rearm: gesture changed since last fire
            self._armed = True

        # --- HELD: drag in progress; release (debounced) when the gesture ends ---
        if self.engaged is not None:
            if gname == self._eg_gesture:
                self._rel = 0
            else:
                self._rel += 1
                if self._rel >= self.cfg["gesture_release_frames"]:
                    self.engaged = None      # engine sees this and releases
                    self._eg_gesture = None
            return [], gname, [round(gscore, 2)]

        action = self.cfg["gesture_bindings"].get(gname) if gname else None
        if gname is None or action is None or action == "none":
            self._cand = None
            self._below = 0
            self._waiting = False
            return [], gname, [round(gscore, 2)]

        if gname != self._cand:          # new candidate -> restart the debounce
            self._cand = gname
            self._below = 0
        self._below += 1

        now = time.time()
        fires: list[Fire] = []
        ready = (self._armed and armed
                 and self._below >= self.cfg["pinch_confirm_frames"]
                 and (now - self._last_fire) > self.cfg["cooldown_s"])
        if ready:
            # Steady-Clicks: commit only when the head is settled; else hold.
            if head_speed <= self.cfg["commit_velocity_gate"]:
                self._last_fire = now
                self._fired_g = gname
                self._armed = False
                self._waiting = False
                if self.cfg["gesture_hold"]:
                    self.engaged = action        # -> engine presses + drags
                    self._eg_gesture = gname
                    self._rel = 0
                else:
                    fires.append(Fire(action, gname, cursor_xy[0], cursor_xy[1], "tap"))
            else:
                self._waiting = True
        return fires, gname, [round(gscore, 2)]
