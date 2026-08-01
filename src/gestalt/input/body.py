# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Body-drift compensator — the core of layer 3 (see docs/POINTING.md §recentering).

Principle: only torso *rotation* (lean/slouch/tilt) drifts the head-relative-to-
camera signal. So we watch the torso for rotation, and when it moves we attribute
the *coincident* change in the head signal to the body and absorb it into a
running offset `bd`; when the torso is still, head-signal change is intentional
aiming and passes straight through.

    corrected = raw_signal − bd
    bd += attribute_gain · w(torso_rotation_rate) · Δraw_signal

w ramps 0→1 with torso rotation rate past a deadband, so a still torso never
absorbs intent (w≈0) and an actively-leaning torso absorbs the drift (w≈1). This
is novel for a head-pointer; it ships default-off and observable so it can be
evaluated/tuned before being trusted. Honest limit: 2D shoulders can't fully
separate torso rotation from translation, so it's conservative by design.
"""
from __future__ import annotations

import math


class BodyCompensator:
    def __init__(self, cfg: dict):
        self.apply_config(cfg)
        self.bd = [0.0, 0.0]
        self._prev_signal = None
        self._prev_roll = None
        self._prev_width = None
        self.activity = 0.0      # last w (for diagnostics)

    def apply_config(self, cfg: dict):
        self.enabled = cfg["torso_correction"]
        self.deadband = cfg["torso_motion_deadband"]
        self.gain = cfg["torso_attribute_gain"]

    def reset(self):
        self.bd = [0.0, 0.0]
        self._prev_signal = None
        self.activity = 0.0

    def __call__(self, signal, torso) -> tuple[float, float]:
        sx, sy = float(signal[0]), float(signal[1])
        if not self.enabled or torso is None or not torso.present:
            self._prev_signal = (sx, sy)
            self._prev_roll = getattr(torso, "roll_rad", None) if torso else None
            self._prev_width = getattr(torso, "width", None) if torso else None
            self.activity = 0.0
            return sx, sy

        if self._prev_signal is None or self._prev_roll is None:
            self._prev_signal = (sx, sy)
            self._prev_roll = torso.roll_rad
            self._prev_width = torso.width
            return sx - self.bd[0], sy - self.bd[1]

        # torso rotation rate = how much the shoulders rolled / changed width.
        d_roll = abs(torso.roll_rad - self._prev_roll)
        d_width = abs(torso.width - self._prev_width)
        rot = d_roll + 2.0 * d_width            # width weighted up (lean/yaw cue)
        w = max(0.0, min(1.0, (rot - self.deadband) / max(self.deadband, 1e-6)))
        self.activity = w

        # attribute the coincident head-signal change to the body, scaled by w.
        dsx = sx - self._prev_signal[0]
        dsy = sy - self._prev_signal[1]
        self.bd[0] += self.gain * w * dsx
        self.bd[1] += self.gain * w * dsy

        self._prev_signal = (sx, sy)
        self._prev_roll = torso.roll_rad
        self._prev_width = torso.width
        return sx - self.bd[0], sy - self.bd[1]

    def state(self) -> dict:
        return {
            "on": self.enabled,
            "bd": (round(self.bd[0], 4), round(self.bd[1], 4)),
            "activity": round(self.activity, 2),
            "mag": round(math.hypot(self.bd[0], self.bd[1]), 4),
        }
