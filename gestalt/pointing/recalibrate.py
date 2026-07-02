# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Implicit recalibration — correct mapping drift online from confirmed clicks,
with no explicit calibration step. See docs/POINTING.md §recentering.

Each confirmed pinch at a magnetized centroid is a near-ground-truth
(raw-integrated-pose, intended-target) pair — far cleaner than the raw clicks
PACE (Huang, CHI 2016) and WebGazer (IJCAI 2016) must use, because magnetism
gives us the *exact* element centroid. We fit a low-order **affine** map per axis
with **recursive least squares + exponential forgetting** (λ≈0.99 ≈ 100-click
memory), so the correction tracks slow drift but stays stable.

Three guards stop a bad click from poisoning the map (PACE + EyeO, arXiv 2307.15039):
  1. pre-pinch stillness — enforced upstream by Steady-Clicks (a click only fires
     when the head is settled), so every sample we see is already stationary.
  2. consistency gate — reject a sample whose residual to the current prediction
     exceeds 1/12 of the screen diagonal (catches snap-to-wrong-element).
  3. bounded correction — the applied offset is clamped so no map can fling the
     cursor.
"""
from __future__ import annotations

import math

import numpy as np

CONSISTENCY_FRAC = 1.0 / 12.0   # PACE: reject samples beyond diag/12 of prediction
P0 = 100.0                       # initial covariance: modest -> gentle early learning


class Recalibrator:
    def __init__(self, cfg: dict, sw: int, sh: int):
        self.diag = math.hypot(sw, sh)
        self.apply_config(cfg)
        self.reset()

    def apply_config(self, cfg: dict):
        self.enabled = cfg["recalibrate"]
        self.lam = cfg["recal_forgetting"]
        self.max_corr = cfg["recal_max_correction_px"]

    def reset(self):
        # affine per axis: tx = θx·[rx, ry, 1]; identity init (tx=rx, ty=ry).
        self.theta_x = np.array([1.0, 0.0, 0.0])
        self.theta_y = np.array([0.0, 1.0, 0.0])
        self.Px = np.eye(3) * P0
        self.Py = np.eye(3) * P0
        self.samples = 0
        self.rejected = 0
        self.last_residual = 0.0

    def correct(self, rx: float, ry: float) -> tuple[float, float]:
        """Map a raw integrated cursor position through the learned correction,
        clamping the displacement so the map can never fling the cursor."""
        if not self.enabled:
            return rx, ry
        phi = np.array([rx, ry, 1.0])
        cx = float(self.theta_x @ phi)
        cy = float(self.theta_y @ phi)
        dx = max(-self.max_corr, min(self.max_corr, cx - rx))
        dy = max(-self.max_corr, min(self.max_corr, cy - ry))
        return rx + dx, ry + dy

    def observe(self, raw: tuple[float, float], target: tuple[float, float]) -> bool:
        """Feed a confirmed-click sample. Returns True if it was accepted."""
        if not self.enabled:
            return False
        rx, ry = raw
        cx, cy = self.correct(rx, ry)
        res = math.hypot(target[0] - cx, target[1] - cy)
        self.last_residual = res
        if res > self.diag * CONSISTENCY_FRAC:   # gate 2: self-consistency
            self.rejected += 1
            return False
        phi = np.array([rx, ry, 1.0])
        self.theta_x, self.Px = self._rls(self.theta_x, self.Px, phi, target[0])
        self.theta_y, self.Py = self._rls(self.theta_y, self.Py, phi, target[1])
        self.samples += 1
        return True

    def _rls(self, theta, P, phi, y):
        Pphi = P @ phi
        gain = Pphi / (self.lam + float(phi @ Pphi))
        err = y - float(theta @ phi)
        theta = theta + gain * err
        P = (P - np.outer(gain, Pphi)) / self.lam
        return theta, P

    def state(self) -> dict:
        """Compact snapshot for the diagnostics window / status."""
        return {
            "on": self.enabled,
            "samples": self.samples,
            "rejected": self.rejected,
            "gain_x": round(float(self.theta_x[0]), 3),
            "gain_y": round(float(self.theta_y[1]), 3),
            "off_x": round(float(self.theta_x[2]), 1),
            "off_y": round(float(self.theta_y[2]), 1),
            "residual": round(self.last_residual, 1),
        }
