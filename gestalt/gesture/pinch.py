# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Pinch detection — independent thumb-to-finger tap detectors (index + pinky by
default; far apart so they never mix up). Ported from the prototype, with the
commit upgraded to Steady-Clicks (Trewin, ASSETS 2006):

  * a tap fires only when the head is *settled* (head speed <= commit gate) — so
    a click never lands mid-flight, and the cursor can't slip off-target during
    the pinch (we commit at the still moment, at the live cursor position).
  * each finger must re-open past `pinch_rearm` before it can fire again, giving
    fast deliberate bursts without chatter.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

# fingertip landmark index, and the extension gate (tip-to-wrist / palm) that
# rejects fists/curls — a real pinch has the acting finger extended.
TIP = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}
EXT_MIN = {"index": 0.90, "middle": 0.85, "ring": 0.80, "pinky": 0.70}
THUMB_TIP = 4
PALM_REF = 9          # middle-finger MCP; |wrist..PALM_REF| normalizes for hand distance


@dataclass
class Fire:
    action: str       # the bound action (e.g. "left_click")
    finger: str       # which finger fired (debug)
    x: float          # commit position (settled cursor)
    y: float
    kind: str = "tap"  # "tap" = atomic click (hold off); press/release go via engaged


class PinchDetector:
    def __init__(self, cfg: dict):
        self.apply_config(cfg)
        self._last_fire = 0.0
        self.engaged = None       # action currently HELD (hold mode) -> drives drag
        self._eg_finger = None
        self._rel = 0

    def apply_config(self, cfg: dict):
        self.cfg = cfg
        # active fingers come from the bindings: keys are "pinch_<finger>"
        self._fingers = [k[len("pinch_"):] for k in cfg["bindings"]
                         if k.startswith("pinch_") and k[len("pinch_"):] in TIP]
        # per-finger state: armed (re-opened since last fire), consecutive frames
        self._st = {f: {"armed": False, "below": 0} for f in self._fingers}
        self._waiting = {f: False for f in self._fingers}   # confirmed but head still moving
        self.engaged = None        # drop any hold on a config change (engine releases)
        self._eg_finger = None

    def reset(self):
        """Hand left frame — require a fresh open before the next fire. Any held
        button is released by the engine (engaged -> None reconciliation)."""
        for f in self._fingers:
            self._st[f] = {"armed": False, "below": 0}
            self._waiting[f] = False
        self.engaged = None
        self._eg_finger = None

    def readiness(self) -> dict:
        """Per-finger pinch state for the diagnostics view: 'ready' (re-opened,
        can fire), 'waiting' (pinched but head still moving — Steady-Clicks hold),
        or 'idle'. The diagnostics window colour-codes these."""
        out = {}
        for f in self._fingers:
            if self._waiting[f]:
                out[f] = "waiting"
            elif self._st[f]["armed"]:
                out[f] = "ready"
            else:
                out[f] = "idle"
        return out

    def update(self, hand_landmarks, cursor_xy, head_speed: float,
               armed: bool) -> tuple[list[Fire], str | None, list[float]]:
        """Returns (fires, pinching_finger_for_debug, per-finger thumb distances)."""
        if hand_landmarks is None:
            self.reset()
            return [], None, []

        hl = hand_landmarks
        wx, wy = hl[0].x, hl[0].y
        palm = math.hypot(hl[PALM_REF].x - wx, hl[PALM_REF].y - wy) or 1e-6
        tx, ty = hl[THUMB_TIP].x, hl[THUMB_TIP].y

        now = time.time()
        fires: list[Fire] = []
        pinching_dbg = None
        dists = []

        close = self.cfg["pinch_close"]
        rearm = self.cfg["pinch_rearm"]

        # HELD (drag): keep holding while the engaged finger stays pinched; release
        # (debounced) once it re-opens past rearm. Engine reads `engaged` to drag.
        if self.engaged is not None:
            d = math.hypot(hl[TIP[self._eg_finger]].x - tx,
                           hl[TIP[self._eg_finger]].y - ty) / palm
            if d < rearm:
                self._rel = 0
            else:
                self._rel += 1
                if self._rel >= self.cfg["gesture_release_frames"]:
                    self.engaged = None
                    self._eg_finger = None
            return [], self._eg_finger, [round(d, 3)]

        confirm = self.cfg["pinch_confirm_frames"]
        cooldown = self.cfg["cooldown_s"]
        gate = self.cfg["commit_velocity_gate"]

        for finger in self._fingers:
            tip = TIP[finger]
            d = math.hypot(hl[tip].x - tx, hl[tip].y - ty) / palm
            ext = math.hypot(hl[tip].x - wx, hl[tip].y - wy) / palm
            dists.append(d)
            st = self._st[finger]

            pinching = d < close and ext > EXT_MIN[finger]
            if pinching and pinching_dbg is None:
                pinching_dbg = finger

            if st["armed"] and pinching:
                st["below"] += 1
                confirmed = st["below"] >= confirm and (now - self._last_fire) > cooldown
                if confirmed and armed:
                    # Steady-Clicks: commit only when the head is settled. If still
                    # moving, hold (don't disarm) and fire the instant it settles.
                    if head_speed <= gate:
                        action = self.cfg["bindings"].get(f"pinch_{finger}")
                        if action and action != "none":
                            if self.cfg["gesture_hold"]:
                                self.engaged = action      # -> engine presses + drags
                                self._eg_finger = finger
                                self._rel = 0
                            else:
                                fires.append(
                                    Fire(action, finger, cursor_xy[0], cursor_xy[1], "tap"))
                        self._last_fire = now
                        st["armed"] = False
                        st["below"] = 0
                        self._waiting[finger] = False
                    else:
                        self._waiting[finger] = True   # confirmed, waiting for stillness
            else:
                st["below"] = 0
                self._waiting[finger] = False
                if not st["armed"] and d > rearm:
                    st["armed"] = True

        return fires, pinching_dbg, dists
