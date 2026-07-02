# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Iris-in-eye gaze + fixation detection (calibration-free precision trigger).

We deliberately do NOT map gaze to a screen point. Appearance-based webcam gaze
is ~2-4° with the head in motion (no IR, no chinrest) — too coarse to aim with,
and the head is ALWAYS moving here because the head is the pointer. The fovea is
~1° wide, so gaze can't out-resolve the head at the last inch regardless.

What gaze IS good for is BEHAVIOUR. A fixation — the iris going still over a short
window (dispersion-threshold identification, I-DT; Salvucci & Goldberg ETRA 2000)
— means the eyes have locked onto a target. In eye-head coordination the eyes
lead the head by ~200ms (Sidenmark & Gellersen, TOCHI 2019), so detecting the
fixation is an EARLY, clutch-free signal that you're about to fine-aim. The
pointer uses it to gentle the cursor's approach before the head even settles.

The signal is the iris centre offset from the eye-corner midpoint, normalized by
the inter-corner span — a head-relative gaze proxy that's invariant to face
distance (both numerator and scale ride the same landmarks). We never calibrate
it to the screen; only its dispersion over time matters.
"""
from __future__ import annotations

import math
from collections import deque

# MediaPipe FaceLandmarker iris-refined indices (the 478-landmark bundle).
_L_IRIS, _R_IRIS = 468, 473
_L_OUT, _L_IN = 33, 133      # left-eye outer / inner corners
_R_IN, _R_OUT = 362, 263     # right-eye inner / outer corners


def _eye_gaze(lm, iris, c0, c1):
    """Iris-centre offset from the eye-corner midpoint, normalized by the corner
    span. Distance-invariant; ~[-0.5,0.5] across the eye."""
    mx = (lm[c0].x + lm[c1].x) * 0.5
    my = (lm[c0].y + lm[c1].y) * 0.5
    span = math.hypot(lm[c0].x - lm[c1].x, lm[c0].y - lm[c1].y) + 1e-6
    return (lm[iris].x - mx) / span, (lm[iris].y - my) / span


class GazeTracker:
    def __init__(self, cfg: dict):
        self._win = None
        self._base = None        # rolling dispersion history (the self-calibration)
        self.thr = 0.0           # live fixation threshold — observable, like brow_thr
        self.apply_config(cfg)

    def apply_config(self, cfg: dict):
        self.enabled = cfg["gaze_fixation"]
        self.window = int(cfg["gaze_fix_window"])
        # SELF-CALIBRATING threshold: fixation = dispersion well below YOUR OWN
        # typical dispersion (k × rolling median), not a hardcoded amplitude.
        # Measured (2026-07, sessions 1782752796/1782780970): the old fixed 0.08
        # sat BELOW one session's median noise (gate stuck ~ON: fix 0.95 still /
        # 0.68 moving) and ABOVE another's (stuck ~OFF) — a fixed value lands on
        # the wrong side of the noise depending on rig/light. Same lesson as the
        # brow clutch: calibrate to the user's live signal, keep a floor.
        self.k = cfg["gaze_fix_k"]
        self.floor = cfg["gaze_fix_floor"]
        base = int(cfg["gaze_fix_baseline"])
        if self._win is None:
            self._win = deque(maxlen=self.window)
        elif self._win.maxlen != self.window:
            self._win = deque(self._win, maxlen=self.window)
        if self._base is None:
            self._base = deque(maxlen=base)
        elif self._base.maxlen != base:
            self._base = deque(self._base, maxlen=base)

    def reset(self):
        # face lost: drop only the SHORT I-DT window (it must re-settle clean).
        # The dispersion baseline is the user's noise character — it survives
        # brief losses so the threshold doesn't have to re-learn from scratch.
        self._win.clear()

    def update(self, landmarks) -> tuple[float, float, float, bool]:
        """Return (gaze_x, gaze_y, dispersion, fixating). Dispersion defaults high
        (= 'not fixating') whenever the iris signal is unavailable."""
        if not self.enabled or landmarks is None or len(landmarks) < 478:
            self._win.clear()
            return 0.0, 0.0, 1.0, False
        lgx, lgy = _eye_gaze(landmarks, _L_IRIS, _L_OUT, _L_IN)
        rgx, rgy = _eye_gaze(landmarks, _R_IRIS, _R_IN, _R_OUT)
        gx, gy = (lgx + rgx) * 0.5, (lgy + rgy) * 0.5
        self._win.append((gx, gy))
        if len(self._win) < self.window:
            return gx, gy, 1.0, False
        xs = [p[0] for p in self._win]
        ys = [p[1] for p in self._win]
        disp = (max(xs) - min(xs)) + (max(ys) - min(ys))   # I-DT dispersion
        self._base.append(disp)
        # need the baseline partly filled before trusting it (a cold start can't
        # fire on a couple of frames); until then the gate stays safely open.
        if len(self._base) < max(10, self._base.maxlen // 3):
            return gx, gy, disp, False
        med = sorted(self._base)[len(self._base) // 2]
        # k × median sits in the gap between the fixation cluster and saccades in
        # a normal use mix. Known trade-off: a LONG steady stare collapses the
        # median toward fixation-level dispersion and the gate drops out — the
        # cost is only that gmax stays high; the floor keeps it from chattering.
        self.thr = max(self.floor, self.k * med)
        return gx, gy, disp, disp < self.thr
