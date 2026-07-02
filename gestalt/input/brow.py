# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Eyebrow clutch — HOLDING the brows raised engages a low-gain precision mode.

WHY the brow and not another facial signal (the design arc, see docs/POINTING.md
§precision): every facial *positional* candidate fails one of three ways — the
mouth shares the neck's lateral-broad/vertical-narrow anisotropy; a pucker
displaces the NOSE, which is the coarse head channel's own landmark (co-location);
and the brow's rest position is pinned at the BOTTOM of its range, so it can't be
a bidirectional axis. But a unipolar, snaps-back-to-rest signal is a textbook
DISCRETE CLUTCH. So we don't use the brow as an axis — precision is engaged for as
long as the brows are HELD up (a momentary HOLD, not a toggle: keeping them raised
through the fine move is the intuitive thing; a toggle's mental gear-flip is not).
Frontalis is high-SNR and decoupled from the mouth/head.

Signal: the eyebrow height in the head-LOCAL frame (the same inter-eye-corner
frame perioral.py uses, so rigid head motion cancels and only the brow muscle
movement remains). `lift` = how far the brow sits above its RESTING height.

The rest baseline is a RUNNING MEDIAN of the recent brow-height samples — NOT a
seeded EMA. This is the crux, learned the hard way: a seed-then-EMA baseline
either chased a held raise (and dropped the hold) or, if frozen to hold, latched
on a bad acquisition seed and read the resting face as "always clutched" forever.
The median has neither failure: rest is the MOST COMMON state, so the median IS
your rest and auto-tunes to your face frame-to-frame; a brief raise is a minority
of the window and barely moves it (so the raise stands out as positive lift); and
nothing is ever frozen, so it can never get permanently stuck. A hold persists as
long as the raised frames stay a minority of the window (~half of it); `brow_window`
trades steadier-rest/shorter-max-hold against the reverse. Hysteresis (on>off) + a
confirm-count debounce keep IR noise from chattering the engage.
"""
from __future__ import annotations

import math
from collections import deque

# stable upper-face anchors that define the head-local frame (shared with perioral)
L_EYE_IN, R_EYE_IN = 133, 362
# eyebrow centre points (MediaPipe FaceLandmarker 478-pt mesh)
L_BROW, R_BROW = 105, 334


def _brow_vert(lm):
    """Average eyebrow height in the head-local frame, normalized by inter-eye
    span (distance-invariant). The face-down axis points down the image, so the
    brow — above the eyes — reads NEGATIVE; raising the brow makes it MORE
    negative. Returns None if the landmarks are unavailable."""
    if lm is None or len(lm) < 478:
        return None
    lx, ly = lm[L_EYE_IN].x, lm[L_EYE_IN].y
    rx, ry = lm[R_EYE_IN].x, lm[R_EYE_IN].y
    ox, oy = (lx + rx) * 0.5, (ly + ry) * 0.5
    ax, ay = rx - lx, ry - ly
    scale = math.hypot(ax, ay) or 1e-6
    ax, ay = ax / scale, ay / scale          # unit horizontal axis (left→right eye)
    px, py = -ay, ax                         # unit vertical (down the face)
    v = 0.0
    for i in (L_BROW, R_BROW):
        dx, dy = lm[i].x - ox, lm[i].y - oy
        v += (dx * px + dy * py) / scale
    return v * 0.5


class BrowClutch:
    def __init__(self, cfg: dict):
        self._win = None         # recent brow-height samples (the median rest baseline)
        self._raised = False     # hysteresis HOLD state (caller engages precision on it)
        self._hold = 0           # confirm-count toward a raise
        self.lift = 0.0          # last lift (brow height above rest), for debug/record
        self.thr_on = 0.0        # live engage threshold (K_on × MAD) — observable
        self.thr_off = 0.0       # live release threshold (K_off × MAD)
        self.apply_config(cfg)

    def apply_config(self, cfg: dict):
        self.enabled = cfg["brow_clutch"]
        # thresholds are SELF-CALIBRATING: K × the live rest-noise (MAD), so the
        # same dimensionless K works for any face/rig — no hardcoded amplitude.
        self.k_on = cfg["brow_k_on"]
        self.k_off = cfg["brow_k_off"]
        self.floor = cfg["brow_floor"]
        self.confirm = int(cfg["brow_confirm_frames"])
        win = int(cfg["brow_window"])
        if self._win is None:
            self._win = deque(maxlen=win)
        elif self._win.maxlen != win:                 # live `set brow_window`
            self._win = deque(self._win, maxlen=win)

    def reset(self):
        """Drop the rest-baseline window + state on a face loss; the median re-fills
        from the next frames (no seed to get wrong)."""
        self._win.clear()
        self._raised = False
        self._hold = 0

    def update(self, landmarks) -> tuple[float, bool, bool]:
        """Return (lift, raised, toggled). `raised` is the HOLD state — engage
        precision while it's True. `toggled` (the rising edge) is also emitted."""
        if not self.enabled:
            return 0.0, False, False
        v = _brow_vert(landmarks)
        if v is None:
            self._hold = 0
            return self.lift, self._raised, False
        self._win.append(v)
        s = sorted(self._win)
        base = s[len(s) // 2]              # running median = your resting brow height
        lift = base - v                   # >0 when the brows rise above rest
        self.lift = lift
        # SELF-CALIBRATING thresholds: K × MAD (median absolute deviation = a robust
        # estimate of YOUR rest jitter, immune to the minority of raised frames). So
        # "a raise" = K noise-widths above your own noise — the absolute amount
        # auto-scales to each face/rig. A floor guards the case where you're so still
        # the MAD collapses (else micro-movements would clear a near-zero threshold).
        mad = sorted(abs(x - base) for x in self._win)[len(self._win) // 2]
        self.thr_on = max(self.floor, self.k_on * mad)
        self.thr_off = max(self.floor * 0.6, self.k_off * mad)
        # need the window partly filled before trusting the rest/noise estimate (so a
        # cold start / reacquire can't fire on a couple of frames)
        if len(self._win) < max(4, self._win.maxlen // 3):
            return lift, self._raised, False
        toggled = False
        if not self._raised:
            if lift > self.thr_on:
                self._hold += 1
                if self._hold >= self.confirm:
                    self._raised = True
                    self._hold = 0
                    toggled = True        # rising edge of a confirmed raise
            else:
                self._hold = 0
        elif lift < self.thr_off:
            self._raised = False          # release once the brow drops back to rest
            self._hold = 0
        return lift, self._raised, toggled
