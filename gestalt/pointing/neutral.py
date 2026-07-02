# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Neutral-pose manager — the "where is straight-ahead" reference for joystick (rate)
control, maintained by a stillness-gated soft re-anchor (the ZUPT analog; Skog
et al., IEEE TBME 2010). See docs/POINTING.md §recentering.

The gate is the whole trick (RubberEdge, UIST 2007): the neutral only re-anchors
when the head is **at rest AND near the current neutral**. That absorbs small
postural drift when you relax at centre, but a *held* deflection (intentional
joystick aiming — low speed but far from neutral) is never re-anchored, so the
auto-recentring can't fight you. Large sustained postural drift in joystick mode
is the one case this can't catch on its own — that needs torso referencing
(layer 3, roadmap).
"""
from __future__ import annotations

import math

REANCHOR_BAND = 1.8   # re-anchor only within this × deadzone of neutral


class NeutralManager:
    def __init__(self, cfg: dict):
        self.apply_config(cfg)
        self.neutral = None      # (nx, ny) in head-signal units, or None until first sample
        self._still = 0          # consecutive still frames

    def apply_config(self, cfg: dict):
        self.cfg = cfg

    def set(self, signal):
        """Hard-set the neutral to a pose (e.g. on recenter / 'look here = centre')."""
        self.neutral = (float(signal[0]), float(signal[1]))
        self._still = 0

    def update(self, signal, head_speed: float, dt: float):
        if self.neutral is None:
            self.set(signal)
            return
        nx, ny = self.neutral
        dmag = math.hypot(signal[0] - nx, signal[1] - ny)
        still = head_speed < self.cfg["stillness_speed"]
        self._still = self._still + 1 if still else 0
        held_ms = self._still * dt * 1000.0
        band = self.cfg["joystick_deadzone"] * REANCHOR_BAND
        # gate: at rest, long enough, AND near neutral (not a held deflection)
        if still and held_ms >= self.cfg["stillness_ms"] and dmag < band:
            a = self.cfg["reanchor_alpha"]
            self.neutral = (nx + a * (signal[0] - nx), ny + a * (signal[1] - ny))

    def deflection(self, signal) -> tuple[float, float]:
        if self.neutral is None:
            return (0.0, 0.0)
        return (signal[0] - self.neutral[0], signal[1] - self.neutral[1])

    @property
    def is_still(self) -> bool:
        return self._still > 0
