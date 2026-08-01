# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
KTM endpoint prediction → target posterior — know the target BEFORE arrival.

This is the surviving half of the "eyes pick the target" ambition after the
iris verdict (docs/POINTING.md §VERDICT): in eye-head coordination the eyes
lead the head to the target by ~200ms, but the same information is carried by
the head's own motion shape. An aimed (ballistic) movement has a stereotyped
bell velocity profile, so once the deceleration begins, how far along the
profile you are TELLS you how much distance remains:

  * Kinematic Template Matching — Pasqual & Wobbrock, CHI 2014 (head-coupled
    variant CHI 2020): the velocity profile predicts the endpoint within ~39px
    at 90% of the movement.
  * Kinematic endpoint prediction — Lank, Cheng, Ruiz, GI 2007: extrapolate
    the speed-over-distance curve to v=0.
  * We use the minimum-jerk closed form (Flash & Hogan 1985) instead of a
    template library: v/v_peak = 16·t²(1−t)² and s/D = 10t³−15t⁴+6t⁵, so the
    decel-side speed ratio r gives t = (1+√(1−√r))/2 and the remaining
    fraction directly — no per-user template store, two lines of algebra.

The predicted point is then fused with the AT-SPI target list into a POSTERIOR:
    P(target) ∝ exp(−d²/2σ²) · (1 + w·click_history)
with σ proportional to the estimated remaining distance — uncertainty shrinks
as the reach completes, exactly when committing early is still worth something.

FAIL-SAFE BY CONSTRUCTION: every guard failure (no ballistic peak, not yet
decelerating, ambiguous posterior, target not ahead of the motion) yields "no
intent" and the pointer behaves exactly as without this module. A WRONG early
acquisition is bounded too: focus applies only the light `focus_pull_move`
while moving, and the focus-break radius releases it as the cursor sails past.
"""
from __future__ import annotations

import math

ARRIVAL_PXS = 45.0        # keep in sync with pointer.ARRIVAL_PXS (reach end)
REACH_TIMEOUT_S = 1.6     # a "reach" longer than this is a scroll/browse, not an aim
MIN_TRAVEL_PX = 120.0     # don't predict off a twitch — need a real ballistic base
END_SPEED_FRAC = 0.08     # reach is over once speed decays below this × peak
DIR_ALPHA = 0.5           # recency weight of the direction EMA (curved-path tolerant)


def _minjerk_remaining(r: float) -> float:
    """Given decel-side speed ratio r = v/v_peak ∈ (0,1], return the fraction of
    TOTAL movement distance still remaining, under a minimum-jerk profile.
    r=1 → 0.5 (at peak, halfway); r→0 → 0 (arrived)."""
    r = min(1.0, max(1e-6, r))
    # v/v_peak = 16 t²(1−t)²  →  t(1−t) = √r / 4  →  decel root of t² − t + u = 0
    u = math.sqrt(r) / 4.0
    t = (1.0 + math.sqrt(max(0.0, 1.0 - 4.0 * u))) / 2.0
    s_frac = 10 * t**3 - 15 * t**4 + 6 * t**5          # fraction of D covered
    return max(0.0, 1.0 - s_frac)


class EndpointPredictor:
    """Segments the cursor stream into ballistic reaches and, once a reach is
    decelerating, estimates the endpoint from the minimum-jerk remaining
    fraction along the (recency-weighted) motion direction."""

    def __init__(self, cfg: dict):
        self.apply_config(cfg)
        self._reset_reach()

    def apply_config(self, cfg: dict):
        self.enabled = cfg["endpoint_predict"]
        self.min_peak = cfg["endpoint_min_peak_pxs"]
        self.decel_ratio = cfg["endpoint_decel_ratio"]

    def _reset_reach(self):
        self._in_reach = False
        self._s = 0.0             # path length so far (px)
        self._peak = 0.0          # peak speed this reach (px/s)
        self._t = 0.0             # reach duration (s)
        self._dx = 0.0            # direction EMA (unnormalized, px/frame)
        self._dy = 0.0
        self._prev = None

    def update(self, x: float, y: float, speed_pxs: float, dt: float):
        """Feed one frame of the (pre-magnetism) cursor. Returns
        (ex, ey, remaining_px, ux, uy) — predicted endpoint, estimated distance
        still to travel, and the unit motion direction — when a decelerating
        reach supports a prediction, else None. The caller treats None as
        'behave exactly as before'."""
        if not self.enabled:
            return None
        if self._prev is None:
            self._prev = (x, y)
            return None
        step_x, step_y = x - self._prev[0], y - self._prev[1]
        self._prev = (x, y)

        # reach segmentation: begins on a real launch, ends on settle/decay/timeout
        if not self._in_reach:
            if speed_pxs > 0.35 * self.min_peak:
                self._in_reach = True
                self._s = math.hypot(step_x, step_y)
                self._peak = speed_pxs
                self._t = dt
                self._dx, self._dy = step_x, step_y
            return None
        self._s += math.hypot(step_x, step_y)
        self._peak = max(self._peak, speed_pxs)
        self._t += dt
        self._dx += DIR_ALPHA * (step_x - self._dx)
        self._dy += DIR_ALPHA * (step_y - self._dy)
        if (speed_pxs < ARRIVAL_PXS or speed_pxs < END_SPEED_FRAC * self._peak
                or self._t > REACH_TIMEOUT_S):
            self._reset_reach()
            self._prev = (x, y)
            return None

        # predict only on the deceleration side of a genuine ballistic reach
        if (self._peak < self.min_peak or self._s < MIN_TRAVEL_PX
                or speed_pxs > self.decel_ratio * self._peak):
            return None
        rem_frac = _minjerk_remaining(speed_pxs / self._peak)
        s_frac = 1.0 - rem_frac
        if s_frac < 1e-3:
            return None
        remaining = self._s * rem_frac / s_frac       # D̂·rem = s·(rem/covered)
        dmag = math.hypot(self._dx, self._dy)
        if dmag < 1e-6:
            return None
        ux, uy = self._dx / dmag, self._dy / dmag
        return (x + ux * remaining, y + uy * remaining, remaining, ux, uy)


class TargetPosterior:
    """P(target | predicted endpoint) with a click-history prior. History is
    keyed by quantized screen position + role (target ids are per-run), kept
    session-only and count-capped — a cheap 'you keep clicking that button'
    prior, not a durable model."""

    _QUANT = 60      # px grid for the history key (a moved window forgets)
    _CAP = 50        # per-key count cap (recency-free, so cap the influence)

    def __init__(self, cfg: dict):
        self.apply_config(cfg)
        self._hist: dict[tuple, int] = {}

    def apply_config(self, cfg: dict):
        self.sigma_frac = cfg["endpoint_sigma_frac"]
        self.sigma_min = cfg["endpoint_sigma_min_px"]
        self.confidence = cfg["endpoint_confidence"]
        self.gate = cfg["endpoint_gate_px"]
        self.hist_w = cfg["endpoint_history_w"]

    @classmethod
    def _key(cls, cx: float, cy: float, role) -> tuple:
        return (round(cx / cls._QUANT), round(cy / cls._QUANT), role)

    def observe_click(self, cx: float, cy: float, role):
        k = self._key(cx, cy, role)
        self._hist[k] = min(self._CAP, self._hist.get(k, 0) + 1)

    def best(self, ex: float, ey: float, remaining: float, targets: list[dict],
             cx: float, cy: float, dirx: float, diry: float):
        """Return (target, confidence_ratio) for the posterior argmax, or
        (None, 0) when the evidence is ambiguous. Guards:
          * target must lie AHEAD of the motion (never grab something behind);
          * target must sit within `gate` px of the predicted endpoint;
          * the winner must beat the runner-up by `confidence`× (a flat
            posterior means the prediction can't discriminate — do nothing)."""
        sigma = max(self.sigma_min, self.sigma_frac * remaining)
        best = second = 0.0
        cand = None
        for tg in targets:
            tx, ty = tg["cx"] - cx, tg["cy"] - cy
            if tx * dirx + ty * diry <= 0:            # behind the motion
                continue
            d = math.hypot(tg["cx"] - ex, tg["cy"] - ey)
            if d > self.gate:
                continue
            score = math.exp(-(d * d) / (2 * sigma * sigma)) \
                * (1.0 + self.hist_w * self._hist.get(
                    self._key(tg["cx"], tg["cy"], tg.get("role")), 0))
            if score > best:
                best, second, cand = score, best, tg
            elif score > second:
                second = score
        if cand is None or best < 1e-6:
            return None, 0.0
        ratio = best / second if second > 1e-9 else float("inf")
        if ratio < self.confidence:
            return None, ratio
        return cand, ratio
