# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""1€ filter — Casiez, Roussel, Vogel, CHI 2012.

Speed-adaptive low-pass: low speed -> low cutoff (kills resting jitter, which
people only notice when still); high speed -> high cutoff (kills lag, which
people only notice while moving). Two params: mincutoff, beta (dcutoff rarely
needs tuning). Ported verbatim from the working prototype.
"""
from __future__ import annotations

import math


class OneEuro:
    def __init__(self, mincut: float, beta: float, dcut: float):
        self.mincut, self.beta, self.dcut = mincut, beta, dcut
        self.xp = None
        self.dxp = 0.0
        self.tp = None

    @staticmethod
    def _alpha(cut: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cut)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, t: float) -> float:
        if self.tp is None:
            self.tp = t
            self.xp = x
            return x
        dt = max(t - self.tp, 1e-4)
        self.tp = t
        dx = (x - self.xp) / dt
        self.dxp += self._alpha(self.dcut, dt) * (dx - self.dxp)
        cut = self.mincut + self.beta * abs(self.dxp)
        self.xp += self._alpha(cut, dt) * (x - self.xp)
        return self.xp
