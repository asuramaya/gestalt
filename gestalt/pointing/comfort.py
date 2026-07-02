# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Comfort-envelope mapper — "AGC for your neck" (see docs/POINTING.md §comfort).

Absolute angle→screen mapping whose range is LEARNED per-user and per-direction
from natural motion, so your comfortable head-turn fills the screen and the edges
are always reachable without strain. No hardcoded angle limits.

Mechanism (grounded in the ROM/ergonomics + adaptive-range research):
  * four independent quantile trackers — yaw low/high, pitch low/high — estimate
    the edges of where you naturally go (~5th/95th percentile), plus a median per
    axis = your neutral. Additive incremental quantile update (the signed-data
    form of DUMIQE; Yazidi & Hammer 2017).
  * the τ asymmetry gives AGC's fast-attack/slow-release for free: a 95th-pct
    tracker steps up ~19× faster than down, so the envelope EXPANDS quickly when
    you reach further and CONTRACTS slowly — it never clips a reach, never
    collapses from a pause.
  * extremes update only on purposeful motion (head speed gate) so resting at
    centre doesn't shrink your range; the neutral median tracks slowly always.
  * each half-range maps to its own screen half → asymmetric by construction
    (looking down, which is comfortable, gets more travel than looking up, which
    strains and tracks poorly). Biomechanics priors seed it so frame 1 works.

Distance-robust: the input is head ORIENTATION (rotation-derived signal), which is
geometrically distance-invariant; the percentile envelope self-corrects the
subjective-extent change as you move.
"""
from __future__ import annotations

import math
from collections import deque

STATIONARY_WINDOW = 40   # frames (~2s @20fps) the pose must stay put to count as rest


def _sig(deg: float) -> float:
    """A pitch/yaw angle (deg) in head-forward-vector signal units (~sin)."""
    return math.sin(math.radians(deg))


class _OneEuro:
    """1€ filter (Casiez CHI 2012). Inlined (stdlib-only) so this module stays
    importable without the heavy input package. Used on the px OUTPUT of comfort
    mode: at high absolute gain the pose noise is amplified, so a speed-adaptive
    output smoother is what makes the cursor rock-solid when you're nearly still."""

    def __init__(self, mincut, beta, dcut=1.0):
        self.mincut, self.beta, self.dcut = mincut, beta, dcut
        self.xp = None
        self.dxp = 0.0
        self.tp = None

    @staticmethod
    def _a(cut, dt):
        tau = 1.0 / (2 * math.pi * cut)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, t):
        if self.tp is None:
            self.tp, self.xp = t, x
            return x
        dt = max(t - self.tp, 1e-4)
        self.tp = t
        dx = (x - self.xp) / dt
        self.dxp += self._a(self.dcut, dt) * (dx - self.dxp)
        cut = self.mincut + self.beta * abs(self.dxp)
        self.xp += self._a(cut, dt) * (x - self.xp)
        return self.xp


class _AngleFollow:
    """Directional-consistency follow gain (Angle Mouse; Wobbrock CHI 2009).

    The gain that pulls the cursor toward the raw target is driven by path
    STRAIGHTNESS, not speed. Over a short, recency-weighted window of target
    movement vectors v_i:

        straightness = |Σ w_i·v_i| / Σ w_i·|v_i|   ∈ [0, 1]

      * pure jitter oscillates → the vectors cancel → straightness ≈ 0 →
        gain ≈ gmin → the cursor freezes, however large the per-frame wobble;
      * a deliberate aim moves consistently one way → straightness ≈ 1 →
        gain ≈ gmax → the cursor tracks promptly with little lag.

    Speed-blind by construction: a SLOW precise aim and a FAST reach both keep
    full gain as long as they're directed — which is exactly what the
    speed-keyed 1€ output stage could not do (jitter spikes velocity → it
    loosened just when you wanted it tightest). Recency weighting makes the
    straightness collapse within ~1-2 frames when you stop, so the cursor
    settles instead of leaking the last directed vectors onto a held target."""

    def __init__(self, window, decay, gmin, gmax, k):
        self.set_params(window, decay, gmin, gmax, k)
        self._v = deque(maxlen=self.window)
        self.cur = None
        self._prev = None
        self.last_straight = 0.0
        self.last_g = 0.0

    def set_params(self, window, decay, gmin, gmax, k):
        self.window = int(window)
        self.decay, self.gmin, self.gmax, self.k = decay, gmin, gmax, k
        # resize the window in place so live `set` keeps the running cursor
        if getattr(self, "_v", None) is not None and self._v.maxlen != self.window:
            self._v = deque(self._v, maxlen=self.window)

    def __call__(self, tx: float, ty: float, gmax: float | None = None,
                 gscale: float = 1.0) -> tuple[float, float]:
        # gmax override lets the caller lower the ceiling on directed-motion gain
        # (the gaze-fixation gate gentles the approach) without touching gmin.
        # gscale (≤1) is the stillness-freeze: it suppresses the WHOLE gain (incl.
        # gmin) toward a residual floor when the cursor has settled, so rest drift
        # stops leaking; a directed push still creeps through the residual and the
        # caller releases it on real head motion.
        gmax = self.gmax if gmax is None else gmax
        if self.cur is None:
            self.cur = [tx, ty]
            self._prev = (tx, ty)
            return tx, ty
        self._v.append((tx - self._prev[0], ty - self._prev[1]))
        self._prev = (tx, ty)
        # recency-weighted resultant vs path length → straightness
        sx = sy = total = 0.0
        n = len(self._v)
        for i, (ax, ay) in enumerate(self._v):
            w = self.decay ** (n - 1 - i)   # newest frame: decay**0 = 1
            sx += w * ax
            sy += w * ay
            total += w * math.hypot(ax, ay)
        straight = math.hypot(sx, sy) / total if total > 1e-6 else 0.0
        g = (self.gmin + (gmax - self.gmin) * (straight ** self.k)) * gscale
        self.cur[0] += g * (tx - self.cur[0])
        self.cur[1] += g * (ty - self.cur[1])
        self.last_straight, self.last_g = straight, g
        return self.cur[0], self.cur[1]


class ComfortMapper:
    def __init__(self, cfg: dict, sw: int, sh: int):
        self.sw, self.sh = sw, sh
        self.apply_config(cfg)
        self.reset()

    def apply_config(self, cfg: dict):
        self.cfg = cfg
        self.lam = cfg["comfort_lambda"]
        self.motion = cfg["comfort_motion_thresh"]
        self.deadzone = cfg["comfort_deadzone"]
        self.rest_alpha = cfg["comfort_rest_alpha"]
        self.stationary = cfg["comfort_stationary"]
        self.overscan = cfg["comfort_overscan"]
        # RubberEdge hybrid gain (low-gain position + elastic edge rate-assist)
        self.edge_assist = cfg["comfort_edge_assist"]
        self.edge_reach = cfg["comfort_edge_reach"]
        self.edge_start = cfg["comfort_edge_start"]
        self.edge_rate = cfg["comfort_edge_rate"]
        self.edge_expo = cfg["comfort_edge_expo"]
        self.edge_decay = cfg["comfort_edge_decay"]
        self.edge_speed = cfg["comfort_edge_speed"]
        self._prior_yaw = _sig(cfg["comfort_prior_yaw_deg"])
        self._prior_up = _sig(cfg["comfort_prior_pitch_up_deg"])
        self._prior_down = _sig(cfg["comfort_prior_pitch_down_deg"])
        # speed-adaptive output smoother on the px cursor (kills amplified jitter)
        self._ox = _OneEuro(cfg["comfort_smooth_mincut"], cfg["comfort_smooth_beta"])
        self._oy = _OneEuro(cfg["comfort_smooth_mincut"], cfg["comfort_smooth_beta"])
        self._t = 0.0
        # directional-consistency follow stage (Angle Mouse) — the fine-aim cure
        self.use_follow = cfg["comfort_follow"]
        self._follow_gmax = cfg["comfort_follow_gmax"]
        fargs = (cfg["comfort_follow_window"], cfg["comfort_follow_decay"],
                 cfg["comfort_follow_gmin"], cfg["comfort_follow_gmax"],
                 cfg["comfort_follow_k"])
        if getattr(self, "_follow", None) is None:
            self._follow = _AngleFollow(*fargs)
        else:
            self._follow.set_params(*fargs)   # preserve running cursor on live `set`
        # gaze-fixation precision gate: when the eyes lock a target, lower the
        # follow's effective gmax so the head's directed approach auto-gentles.
        self.fix_gmax = cfg["comfort_fix_gmax"]
        self.fix_smooth = cfg["comfort_fix_smooth"]
        # stillness-freeze: when the cursor SETTLES, suppress the follow gain toward
        # a residual floor so rest drift stops leaking (the straightness gate can't —
        # a slow involuntary drift reads as "directed"). Engaged on small cursor net
        # DISPLACEMENT (a slow aim progresses, drift oscillates in place), broken by
        # real head motion. See docs/POINTING.md §fine-aiming.
        self.freeze_on = cfg["comfort_freeze"]
        self.freeze_floor = cfg["comfort_freeze_floor"]
        self.freeze_speed = cfg["comfort_freeze_speed"]
        self.freeze_attack = cfg["comfort_freeze_attack"]
        self.freeze_release = cfg["comfort_freeze_release"]
        # deceleration-aware gain (submovement model; Woodworth 1899 / Meyer 1988):
        # aimed motion is ballistic-then-corrective. The corrective (homing) phase is
        # a DECELERATION — speed drops below its recent peak — which straightness
        # can't see (a decelerating aim still looks 'directed'). So after a real
        # ballistic reach, scale gain down as you decelerate → fine landing on small
        # targets, then the freeze locks the hold. Untouched during the fast reach.
        self.decel_on = cfg["comfort_decel"]
        self.decel_floor = cfg["comfort_decel_floor"]
        self.decel_decay = cfg["comfort_decel_decay"]
        self.decel_min_peak = cfg["comfort_decel_min_peak"]
        # brow-clutch precision lens: while engaged, the cursor moves only
        # `precision_gain` of its normal travel per head movement (a CD-gain cut for
        # the last inch). It accumulates a persistent offset (you reach further to
        # move less), which bleeds back to the absolute mapping only during a fast
        # re-aim — so the precise placement holds right after you release.
        self.precision_gain = cfg["precision_gain"]
        self.prec_decay = cfg["precision_decay"]

    def reset(self):
        # neutral (resting pose) and the four direction extremes, seeded from priors.
        self.nx = 0.0
        self.ny = 0.0
        self._hist = deque(maxlen=STATIONARY_WINDOW)
        self.parked = False
        self._fix_engage = 0.0   # smoothed fixation gate (0 = none, 1 = locked on)
        self._fz = 0.0           # stillness-freeze engagement (0 = free, 1 = locked)
        self._spd_ema = 0.0      # smoothed head speed (freeze gate; absorbs 1-frame spikes)
        self._spk = 0.0          # decaying head-speed peak (deceleration-aware gain)
        self._decel_g = 1.0      # last decel gain scale (observability)
        self._eoff_x = 0.0       # elastic rate-assist offset (px), per axis
        self._eoff_y = 0.0
        self._poff_x = 0.0       # precision-lens persistent offset (px), per axis
        self._poff_y = 0.0
        self._prec_prev = False  # precision engaged last frame (edge detect)
        self._prec_ref = None    # last full-gain output (px) — the lens' reference
        self.qx_lo, self.qx_hi = -self._prior_yaw, self._prior_yaw
        # signal sign: looking UP = positive, DOWN = negative (matches pitch_deg).
        # So the high extreme is UP (compress) and the low extreme is DOWN (room).
        # Either way the online trackers learn the true per-direction range from use.
        self.qy_lo, self.qy_hi = -self._prior_down, self._prior_up

    @property
    def primed(self) -> bool:
        """True once the follow stage has a running cursor (safe to coast)."""
        return self.use_follow and self._follow.cur is not None

    def set_neutral(self, sigx: float, sigy: float):
        """Snap the centre to the current pose (manual recenter)."""
        self.nx, self.ny = float(sigx), float(sigy)
        self._hist.clear()

    def reseat(self, sigx: float, sigy: float, sw: int, sh: int):
        """Monitor switch: retarget to the new screen size AND re-seat a fresh
        comfortable envelope centred on the current pose. Re-seeding (not just
        moving the neutral) keeps the range symmetric so you can cross BACK with
        a normal deflection; the rest-pose neutral then drifts to your true
        settled pose for the new monitor. Cursor jumps to the new centre."""
        self.sw, self.sh = sw, sh
        self.nx, self.ny = float(sigx), float(sigy)
        self.qx_lo, self.qx_hi = self.nx - self._prior_yaw, self.nx + self._prior_yaw
        self.qy_lo, self.qy_hi = self.ny - self._prior_down, self.ny + self._prior_up
        self._hist.clear()
        self.parked = False
        self._follow.cur = None
        self._eoff_x = self._eoff_y = 0.0
        self._poff_x = self._poff_y = 0.0
        self._prec_prev = False
        self._prec_ref = None

    def cross_intent(self, sigx: float, sigy: float, cross_sig: float):
        """Direction the user is deflecting past a FIXED absolute angle from
        neutral (cross_sig = sin(angle)) toward the other monitor — or None.

        Deliberately envelope-INDEPENDENT: the learned quantile range keeps
        expanding as you look far (and the down-range is generous by prior), so an
        envelope-relative threshold gets absorbed and the cross never fires — the
        bug where the bottom monitor became unreachable. A fixed angle past your
        current neutral is robust and symmetric in both directions."""
        dx, dy = sigx - self.nx, sigy - self.ny
        if abs(dy) >= abs(dx) and abs(dy) > cross_sig:
            return "up" if dy >= 0 else "down"      # up-gaze = +signal = screen top
        if abs(dx) > cross_sig:
            return "left" if dx >= 0 else "right"
        return None

    @staticmethod
    def _step(q, x, tau, lam):
        """Additive incremental quantile update toward percentile tau."""
        return q + (lam * tau if x > q else -lam * (1.0 - tau))

    def update(self, sigx: float, sigy: float, speed: float,
               hand_present: bool = False, fixating: bool = False):
        # slew the precision gate toward the raw fixation flag (attack == release;
        # comfort_fix_smooth keeps engagement from snapping the gain).
        self._fix_engage += self.fix_smooth * ((1.0 if fixating else 0.0) - self._fix_engage)
        # "Parked" = the pose has stayed within a tiny region over a ~2s WINDOW,
        # not merely slow this instant. At high gain a precise slow aim has near-zero
        # speed but the pose still drifts across the window, so it never counts as
        # parked — that's what stops the centre from chasing your aim.
        self._hist.append((sigx, sigy))
        self.parked = False
        if len(self._hist) >= STATIONARY_WINDOW:
            xs = [p[0] for p in self._hist]
            ys = [p[1] for p in self._hist]
            spread = max(max(xs) - min(xs), max(ys) - min(ys))
            self.parked = spread < self.stationary

        # re-centre the neutral ONLY when genuinely parked AND not about to click
        # (hand up = aiming, never rest). Frozen otherwise → the mapping stays put.
        if self.parked and not hand_present:
            self.nx += self.rest_alpha * (sigx - self.nx)
            self.ny += self.rest_alpha * (sigy - self.ny)

        if speed > self.motion:
            # MOVING: learn the reach extremes (fast-expand / slow-contract via tau).
            self.qx_hi = self._step(self.qx_hi, sigx, 0.95, self.lam)
            self.qx_lo = self._step(self.qx_lo, sigx, 0.05, self.lam)
            self.qy_hi = self._step(self.qy_hi, sigy, 0.95, self.lam)
            self.qy_lo = self._step(self.qy_lo, sigy, 0.05, self.lam)
        # keep each extreme on its own side of neutral (sanity, ≥ a small floor)
        floor = 0.02
        self.qx_hi = max(self.qx_hi, self.nx + floor)
        self.qx_lo = min(self.qx_lo, self.nx - floor)
        self.qy_hi = max(self.qy_hi, self.ny + floor)
        self.qy_lo = min(self.qy_lo, self.ny - floor)

    def _axis(self, x, n, qlo, qhi, extent, dt, off, speed):
        center = extent / 2.0
        d = x - n
        if d >= 0:
            span, half = max(qhi - n, 1e-4), center
        else:
            span, half = max(n - qlo, 1e-4), extent - center
        # overscan: map the comfortable extreme slightly BEYOND the edge, so the
        # corner is reached at ~1/(1+overscan) of your reach — headroom for the
        # compound (yaw+pitch) corner posture.
        half *= (1.0 + self.overscan)
        sign = 1.0 if d >= 0 else -1.0   # mouse-mode sign: + deflection -> 0 edge
        fr = (abs(d) / span - self.deadzone) / max(1.0 - self.deadzone, 1e-4)
        f = min(1.0, max(0.0, fr))
        # RubberEdge: position control reaches only `edge_reach` of the half at the
        # comfortable extreme — lower gain (finer aim, less amplified jitter). The
        # remaining travel to the true corner comes from the elastic rate-assist.
        reach = self.edge_reach if self.edge_assist else 1.0
        pos = center - sign * (f * reach) * half
        if self.edge_assist:
            # The edge-assist is a MOVING phenomenon: only a fast, directed push
            # toward the extreme should glide the cursor to the corner. A slow fine
            # aim near the edge must leave it untouched — and FROZEN, so any prior
            # glide holds in place — otherwise the edge "eats" precise edge-element
            # aims. m ramps 0→1 over [0.35·edge_speed .. edge_speed]; at m≈0 the
            # offset neither grows nor decays, so position control (low gain) alone
            # places the cursor and you can aim onto an edge element.
            lo = 0.35 * self.edge_speed
            m = min(1.0, max(0.0, (speed - lo) / max(self.edge_speed - lo, 1e-6)))
            drive = min(1.5, max(0.0, (fr - self.edge_start) / max(1.0 - self.edge_start, 1e-4)))
            if drive > 0.0:
                # GLIDE outward only on a fast directed push (gated) — so a slow
                # fine aim near the edge isn't dragged into the corner.
                off += -sign * self.edge_rate * (drive ** self.edge_expo) * m * dt
            else:
                # DECAY back whenever the deflection returns inside the extreme —
                # NOT motion-gated, so you can fine-aim your way off the corner
                # (a frozen offset there was the "stuck on the edge" feel).
                off -= off * min(1.0, self.edge_decay * dt)
            off = min(half, max(-half, off))
        else:
            off = 0.0
        return min(extent - 1.0, max(0.0, pos + off)), off

    def _apply_prec(self, bx: float, by: float, dt: float, speed: float,
                    on: bool) -> tuple[float, float]:
        """Precision lens over the full-gain output (bx, by). While engaged the
        displayed delta = precision_gain × the full delta, accumulated into a
        persistent offset; disengaged, the offset bleeds off only during a fast
        re-aim (so a slow placement right after release is preserved)."""
        if self._prec_ref is None:
            self._prec_ref = (bx, by)
        rbx, rby = self._prec_ref
        if on:
            if self._prec_prev:
                self._poff_x += (self.precision_gain - 1.0) * (bx - rbx)
                self._poff_y += (self.precision_gain - 1.0) * (by - rby)
            self._poff_x = max(-self.sw, min(self.sw, self._poff_x))
            self._poff_y = max(-self.sh, min(self.sh, self._poff_y))
        elif speed > self.motion and (self._poff_x or self._poff_y):
            k = min(1.0, self.prec_decay * dt)
            self._poff_x -= self._poff_x * k
            self._poff_y -= self._poff_y * k
        self._prec_ref = (bx, by)
        self._prec_prev = on
        ox = min(self.sw - 1.0, max(0.0, bx + self._poff_x))
        oy = min(self.sh - 1.0, max(0.0, by + self._poff_y))
        return ox, oy

    def map(self, sigx: float, sigy: float, dt: float, speed: float = 0.0,
            precision: bool = False) -> tuple[float, float]:
        mx, self._eoff_x = self._axis(
            sigx, self.nx, self.qx_lo, self.qx_hi, self.sw, dt, self._eoff_x, speed)
        my, self._eoff_y = self._axis(
            sigy, self.ny, self.qy_lo, self.qy_hi, self.sh, dt, self._eoff_y, speed)
        self._t += max(dt, 1e-4)
        if self.use_follow:
            # fixation lerps the ceiling down toward fix_gmax (finer approach).
            eff = self._follow_gmax + (self.fix_gmax - self._follow_gmax) * self._fix_engage
            gscale = self._freeze_gscale(speed) * self._decel_gscale(speed)
            bx, by = self._follow(mx, my, gmax=eff, gscale=gscale)
        else:
            bx, by = self._ox(mx, self._t), self._oy(my, self._t)
        return self._apply_prec(bx, by, dt, speed, precision)

    def _freeze_gscale(self, speed: float) -> float:
        """Stillness-freeze gain scale, gated on SMOOTHED HEAD SPEED. At rest the
        head is physically still and the cursor 'drift' is sensor noise, so head
        speed is genuinely low — a far cleaner signal than cursor displacement
        (drift and a slow aim move the cursor by similar amounts). The EMA absorbs
        single-frame sensor spikes so they can't briefly unlock and leak drift.
        Below `freeze_speed` → ramp toward a near-hard lock; a deliberate move past
        `freeze_release` unlocks instantly; the band between holds (hysteresis)."""
        if not self.freeze_on:
            return 1.0
        self._spd_ema += 0.4 * (speed - self._spd_ema)
        if self._spd_ema < self.freeze_speed:            # genuinely still → lock
            self._fz += self.freeze_attack * (1.0 - self._fz)
        elif self._spd_ema > self.freeze_release:        # deliberate move → release
            self._fz = 0.0
        # in-between: hold _fz (hysteresis deadband)
        return self.freeze_floor + (1.0 - self.freeze_floor) * (1.0 - self._fz)

    def _decel_gscale(self, speed: float) -> float:
        """Deceleration-aware gain. Track a decaying speed PEAK; once a real
        ballistic reach has happened (peak > `decel_min_peak`), the ratio
        speed/peak is ~1 at/near the peak and falls toward 0 as you decelerate to
        home in — so we scale gain down to `decel_floor` over the corrective phase.
        A slow steady creep keeps speed≈peak → full gain (only DECELERATION damps),
        and a tiny movement never builds a peak → untouched."""
        if not self.decel_on:
            self._decel_g = 1.0
            return 1.0
        self._spk = max(speed, self._spk * self.decel_decay)
        if self._spk < self.decel_min_peak:
            self._decel_g = 1.0
            return 1.0
        df = min(1.0, speed / self._spk)                 # 1 at peak → 0 fully decelerated
        self._decel_g = self.decel_floor + (1.0 - self.decel_floor) * df
        return self._decel_g

    def state(self, sigx: float, sigy: float) -> dict:
        return {
            "nx": round(self.nx, 3), "ny": round(self.ny, 3),
            "qx": [round(self.qx_lo, 3), round(self.qx_hi, 3)],
            "qy": [round(self.qy_lo, 3), round(self.qy_hi, 3)],
            "cur": [round(sigx, 3), round(sigy, 3)],
            "follow": self.use_follow,
            "straight": round(self._follow.last_straight, 2),
            "gain": round(self._follow.last_g, 2),
            "fix": round(self._fix_engage, 2),
            "freeze": round(self._fz, 2),
            "decel": round(self._decel_g, 2),
            "eoff": [round(self._eoff_x, 1), round(self._eoff_y, 1)],
            "poff": [round(self._poff_x, 1), round(self._poff_y, 1)],
        }
