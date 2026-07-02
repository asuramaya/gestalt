# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
The pointing pipeline — see docs/POINTING.md for the cited basis.

    head delta ─► PRISM speed-scaled integration (raw cursor)
              ─► learned recalibration correction (RLS from confirmed clicks)
              ─► DynaSpot speed-scaled catch-radius picks nearest centroid
              ─► velocity-gated soft pull toward it (no hard snap)
              ─► KTM-style arrival detection

There is NO lock state: the head is always free. The raw integrated accumulator
is kept clean (head motion only) so it's a faithful RLS input; the correction is
a read-only transform on top, and magnetism is a per-frame soft pull that does
not feed back into the accumulator.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .comfort import ComfortMapper
from .endpoint import EndpointPredictor, TargetPosterior
from .neutral import NeutralManager
from .recalibrate import Recalibrator

# Secondary thresholds (px/s) for the magnetism state machine — derived feel, not
# the primary user-facing knobs in config.
POINT_RADIUS = 8.0        # catch-radius floor: a near-point when stopped (DynaSpot)
ARRIVAL_PXS = 45.0        # below this cursor speed = "arrived" (KTM-style settle)
MIN_PEAK_PXS = 80.0       # need a real movement before the velocity gate means anything
JOY_FULL_DEFLECTION = 0.35  # head-deflection magnitude (~24° yaw) treated as "full stick"


@dataclass
class PointerState:
    x: float                      # final displayed cursor (after correction + pull)
    y: float
    speed_pxs: float = 0.0
    catch_radius: float = 0.0
    snap_role: str | None = None
    arrived: bool = False
    target_count: int = 0
    raw: tuple = (0.0, 0.0)       # raw integrated position (head only) — RLS input
    corrected: tuple = (0.0, 0.0)  # after the learned correction, before magnetism
    recal: dict = field(default_factory=dict)
    mode: str = "mouse"           # active control mode
    deflection: float = 0.0       # joystick: head deflection magnitude from neutral
    still: bool = False           # head at rest (neutral re-anchoring)
    comfort: dict = None          # comfort mode: learned envelope + current position
    endpoint: dict = None         # KTM endpoint prediction + intent (observability)
    focus: dict = None            # focus-magnetism churn diagnostics (id, dist, events)


class Pointer:
    def __init__(self, cfg: dict, sw: int, sh: int, start=None, monitors=None):
        self.cfg = cfg
        self.sw, self.sh = sw, sh   # the VIRTUAL desktop (clamp + relative-mode bounds)
        self._mons = monitors
        # comfort maps to the ACTIVE monitor's local extent, then we offset to
        # virtual coords. Single-monitor: the rect IS the virtual desktop.
        mx, my, mw, mh = monitors.active_rect() if monitors else (0, 0, sw, sh)
        self._mon_origin = (mx, my)
        self._cross_cd = 0.0
        self.ox, self.oy = (start or (sw / 2.0, sh / 2.0))   # raw integrated accumulator
        self._prev = (self.ox, self.oy)
        self._peak = 0.0
        self._recal = Recalibrator(cfg, sw, sh)
        self._neutral = NeutralManager(cfg)
        self._comfort = ComfortMapper(cfg, mw, mh)
        self._last_raw = (self.ox, self.oy)
        self._last_signal = (0.0, 0.0)
        self._last_signal_raw = (0.0, 0.0)
        self._sig_hist = []           # last two raw signals (for coast extrapolation)
        self._last_speed = 0.0        # last head speed (for edge-assist gate on coast)
        self._last_candidate = None
        self._focus_id = None         # focus-hysteresis: the committed target's id
        self._endpoint = EndpointPredictor(cfg)   # KTM: endpoint from motion shape
        self._posterior = TargetPosterior(cfg)    # endpoint × targets × history
        self._ep_state = None         # last prediction/intent snapshot (observability)
        # cumulative focus-event counters (diagnose acquire/break churn — a healthy
        # session acquires ~once per aim; a flapping one breaks/re-acquires per frame)
        self._fev = {"as": 0, "ai": 0, "bd": 0, "bv": 0}  # settle/intent acq, dist/vanish break
        self._focus_prev_d = None     # last distance to the focused target (directional break)

    def set_bounds(self, sw: int, sh: int, monitors=None):
        """Re-target the coordinate space after a display change (monitor
        plugged/unplugged/rearranged). Updates the virtual bounds + active-monitor
        origin and reseats comfort on the new active monitor."""
        self.sw, self.sh = sw, sh
        self._mons = monitors
        mx, my, mw, mh = monitors.active_rect() if monitors else (0, 0, sw, sh)
        self._mon_origin = (mx, my)
        rx, ry = self._last_signal_raw
        self._comfort.reseat(rx, ry, mw, mh)
        self._recal.set_bounds(sw, sh)   # rescale the diag/12 consistency gate
        self.ox = min(self.sw - 1.0, max(0.0, self.ox))
        self.oy = min(self.sh - 1.0, max(0.0, self.oy))

    def apply_config(self, cfg: dict):
        self.cfg = cfg
        self._recal.apply_config(cfg)
        self._neutral.apply_config(cfg)
        self._comfort.apply_config(cfg)
        self._endpoint.apply_config(cfg)
        self._posterior.apply_config(cfg)

    def recenter(self):
        self.ox, self.oy = self.sw / 2.0, self.sh / 2.0
        self._prev = (self.ox, self.oy)
        self._peak = 0.0
        # "recenter" means: this head pose is now straight-ahead (both joystick
        # neutral and the comfort-mode resting centre snap to the current pose).
        self._neutral.set(self._last_signal)
        self._comfort.set_neutral(self._last_signal_raw[0], self._last_signal_raw[1])

    def recal_reset(self):
        self._recal.reset()

    def envelope_reset(self):
        """Re-seed the comfort envelope (qx/qy/neutral/offsets) from the priors —
        the manual fix for an outlier-stretched range (see Engine.envelope_reset).
        Cursor jumps to the re-seeded centre, same as a monitor reseat."""
        self._comfort.reset()

    def _catch_radius(self, speed_pxs: float) -> float:
        """DynaSpot: catch-radius grows with cursor speed, collapses to a point
        when stopped (so you can park in empty space without a mode switch)."""
        lo = self.cfg["dynaspot_min_speed"]
        span = max(lo, 1.0)
        norm = min(1.0, max(0.0, (speed_pxs - lo) / span))
        return POINT_RADIUS + norm * (self.cfg["dynaspot_max_radius"] - POINT_RADIUS)

    def _focus_magnetism(self, cx, cy, targets, arrived, intent=None):
        """Focus-hysteresis magnetism. Commit to one target and stick to it until
        the intended cursor clearly leaves it; acquire_px < break_px is what makes
        it sticky instead of flip-flopping between neighbours. `intent` is the
        KTM endpoint posterior's confident target (or None): it lets acquisition
        happen EARLY, mid-flight, instead of waiting for arrival."""
        by_id = {t.get("id"): t for t in targets}
        foc = by_id.get(self._focus_id) if self._focus_id is not None else None
        if foc is None:
            if self._focus_id is not None:
                self._fev["bv"] += 1         # focused target vanished (tracker cull)
            self._focus_id = None
            self._focus_prev_d = None
        else:
            d = math.hypot(foc["cx"] - cx, foc["cy"] - cy)
            # DIRECTIONAL break: distance alone can't distinguish "sailing away"
            # from "a pre-acquired target still far ahead" — an intent acquire
            # lands with d > break_px by design, and a raw distance test broke it
            # the very next frame (measured: acquire/break flapping toggling the
            # pull). Break only when far AND the gap is GROWING (motion away).
            receding = self._focus_prev_d is not None and d > self._focus_prev_d + 1.0
            self._focus_prev_d = d
            if d > self.cfg["focus_break_px"] and (receding or arrived):
                self._focus_id, foc = None, None
                self._focus_prev_d = None
                self._fev["bd"] += 1

        if foc is None and arrived:           # acquire while settling (the classic path)
            best, nt = self.cfg["focus_acquire_px"], None
            for tg in targets:
                d = math.hypot(tg["cx"] - cx, tg["cy"] - cy)
                if d < best:
                    best, nt = d, tg
            if nt is not None:
                self._focus_id, foc = nt.get("id"), nt
                self._fev["as"] += 1
        elif foc is None and intent is not None:
            # PRE-acquire the predicted target mid-flight. Only the light
            # focus_pull_move applies while moving, so a wrong prediction feels
            # like a faint tug and the break radius releases it as the cursor
            # sails past — the hard snap still waits for genuine arrival.
            self._focus_id, foc = intent.get("id"), intent
            self._fev["ai"] += 1

        self._focus_dist = (round(math.hypot(foc["cx"] - cx, foc["cy"] - cy))
                            if foc is not None else None)
        if foc is None:
            return cx, cy, None, None
        # firm snap when settled (sits ON the stable target → no jiggle), light
        # pull while moving so you can slide off and then break the focus.
        pull = self.cfg["focus_pull"] if arrived else self.cfg["focus_pull_move"]
        fx = cx + (foc["cx"] - cx) * pull
        fy = cy + (foc["cy"] - cy) * pull
        return fx, fy, foc, foc.get("role", "target")

    def _soft_pull(self, cx, cy, targets, speed_pxs, arrived, radius):
        """Legacy memoryless magnetism (kept for A/B via focus_acquire=false):
        velocity-gated soft pull toward the nearest centroid in the catch-radius."""
        cand, best = None, radius
        for tg in targets:
            d = math.hypot(tg["cx"] - cx, tg["cy"] - cy)
            if d < best:
                best, cand = d, tg
        fx, fy, snap_role = cx, cy, None
        if cand is not None:
            gate = (self._peak > MIN_PEAK_PXS
                    and speed_pxs < self.cfg["snap_velocity_gate"] * self._peak)
            if gate or arrived:
                pull = self.cfg["snap_pull"]
                fx += (cand["cx"] - cx) * pull
                fy += (cand["cy"] - cy) * pull
                snap_role = cand.get("role", "target")
        return fx, fy, cand, snap_role

    def _mouse_step(self, head) -> tuple[float, float]:
        """Displacement control: head delta -> cursor delta, PRISM speed-scaled
        (slow = fine, fast = reach, tremor frozen below head_min_speed)."""
        dsx, dsy = head.delta
        lo, hi = self.cfg["head_min_speed"], self.cfg["head_max_speed"]
        cd_scale = min(1.0, max(0.0, (head.speed - lo) / max(hi - lo, 1e-9)))
        mvx = -dsx * self.cfg["cd_base"] * cd_scale   # sign: camera is mirrored
        mvy = -dsy * self.cfg["cd_base"] * cd_scale
        mag = math.hypot(mvx, mvy)
        cap = self.cfg["max_step_px"]
        if mag > cap:
            mvx *= cap / mag
            mvy *= cap / mag
        return mvx, mvy

    def _joystick_step(self, head, dt: float) -> tuple[float, float, float]:
        """Rate control: head deflection from neutral -> cursor velocity, with a
        dead-zone and an expo response curve. Returns (dx, dy, deflection_mag)."""
        dfx, dfy = self._neutral.deflection(head.signal)
        dmag = math.hypot(dfx, dfy)
        dz = self.cfg["joystick_deadzone"]
        if dmag <= dz:
            return 0.0, 0.0, dmag
        norm = min(1.0, (dmag - dz) / max(JOY_FULL_DEFLECTION - dz, 1e-6))
        speed = self.cfg["joystick_max_speed"] * (norm ** self.cfg["joystick_expo"])
        # sign mirror matches mouse mode (head-right -> cursor-right)
        vx = -(dfx / dmag) * speed
        vy = -(dfy / dmag) * speed
        return vx * dt, vy * dt, dmag

    def _maybe_cross(self, rx: float, ry: float, dt: float):
        """Multi-monitor: if the user over-deflects toward a neighbouring monitor,
        switch the active monitor, recentre comfort on it (so the current pose
        becomes that monitor's centre — the cursor warps there and the neck
        returns to neutral), and start a cooldown so it can't thrash."""
        self._cross_cd = max(0.0, self._cross_cd - dt)
        if not (self.cfg["multimonitor"] and self._mons and self._mons.multi):
            return
        if self._cross_cd > 0.0:
            return
        cross_sig = math.sin(math.radians(self.cfg["monitor_cross_deg"]))
        direction = self._comfort.cross_intent(rx, ry, cross_sig)
        if direction is None or self._mons.neighbor(direction) is None:
            return
        self._mons.switch(direction)
        self._reseat_active(rx, ry)
        self._cross_cd = self.cfg["monitor_switch_cooldown"]

    def _reseat_active(self, rx: float, ry: float):
        mx, my, mw, mh = self._mons.active_rect()
        self._mon_origin = (mx, my)
        self._comfort.reseat(rx, ry, mw, mh)

    def switch_monitor(self, target) -> str | None:
        """Manual monitor switch (CLI/diag fallback so you're never stuck). target
        is 'next', a direction, or an index. Reseats comfort on the new monitor."""
        if not self._mons:
            return None
        n = len(self._mons.rects)
        if target == "next":
            self._mons.active = (self._mons.active + 1) % n
        elif target in ("up", "down", "left", "right"):
            if not self._mons.switch(target):
                return None
        else:
            try:
                self._mons.active = max(0, min(n - 1, int(target)))
            except (TypeError, ValueError):
                return None
        rx, ry = self._last_signal_raw
        self._reseat_active(rx, ry)
        self._cross_cd = self.cfg["monitor_switch_cooldown"]
        return self._mons.rects[self._mons.active][4]

    def update(self, head, targets, dt: float, hand_present: bool = False,
               precision: bool = False) -> PointerState:
        mode = self.cfg["control_mode"]
        deflection = 0.0
        comfort_state = None
        if head.ok:
            self._last_signal = head.signal
            self._last_signal_raw = head.signal_raw
            self._sig_hist.append(head.signal_raw)
            if len(self._sig_hist) > 2:
                self._sig_hist.pop(0)
            self._neutral.update(head.signal, head.speed, dt)   # maintain the neutral
            sx, sy = head.signal
            if mode == "comfort":
                # absolute: head orientation -> screen via the learned envelope.
                # Uses the RAW signal (no body comp) — comfort's own rest-pose
                # neutral handles drift; body comp's offset would corrupt it.
                rx, ry = head.signal_raw
                self._comfort.update(rx, ry, head.speed, hand_present, head.fixating)
                self._maybe_cross(rx, ry, dt)
                lx, ly = self._comfort.map(rx, ry, dt, head.speed, precision)
                self.ox = lx + self._mon_origin[0]   # active monitor local -> virtual
                self.oy = ly + self._mon_origin[1]
                self._last_speed = head.speed
                comfort_state = self._comfort.state(rx, ry)
            elif mode == "joystick":
                mvx, mvy, deflection = self._joystick_step(head, dt)
                self.ox = min(self.sw - 1.0, max(0.0, self.ox + mvx))
                self.oy = min(self.sh - 1.0, max(0.0, self.oy + mvy))
            else:
                mvx, mvy = self._mouse_step(head)
                self.ox = min(self.sw - 1.0, max(0.0, self.ox + mvx))
                self.oy = min(self.sh - 1.0, max(0.0, self.oy + mvy))

        # 2. learned recalibration correction (read-only on the raw accumulator).
        cx, cy = self._recal.correct(self.ox, self.oy)
        cx = min(self.sw - 1.0, max(0.0, cx))
        cy = min(self.sh - 1.0, max(0.0, cy))

        # 3. cursor speed (head-driven, pre-magnetism) + peak for the Worden gate.
        dist = math.hypot(cx - self._prev[0], cy - self._prev[1])
        speed_pxs = dist / dt if dt > 0 else 0.0
        self._prev = (cx, cy)
        arrived = speed_pxs < ARRIVAL_PXS
        if arrived:
            self._peak = 0.0
        else:
            self._peak = max(self._peak, speed_pxs)

        # 3b. KTM endpoint prediction: once the reach decelerates, the motion
        #     shape says where it will end — resolve that against the targets
        #     so magnetism can commit BEFORE arrival (see endpoint.py).
        intent = None
        pred = self._endpoint.update(cx, cy, speed_pxs, dt)
        if pred is not None:
            ex, ey, rem, ux, uy = pred
            intent, conf = self._posterior.best(ex, ey, rem, targets, cx, cy, ux, uy)
            self._ep_state = {"x": round(ex), "y": round(ey), "rem": round(rem),
                              "tgt": (intent.get("role") if intent else None),
                              "conf": (round(conf, 1) if conf != float("inf") else -1)}
        else:
            self._ep_state = None

        # 4. magnetism. Default = focus-hysteresis state machine (iPad-style:
        #    acquire when you settle near a target, HOLD it without flip-flopping,
        #    release only on clear directed intent away). Targets are temporally
        #    stable (TargetTracker), so a held focus sits rock-still.
        radius = self._catch_radius(speed_pxs)   # reported for the diag overlay
        if self.cfg["focus_acquire"]:
            fx, fy, cand, snap_role = self._focus_magnetism(cx, cy, targets, arrived,
                                                            intent=intent)
        else:
            fx, fy, cand, snap_role = self._soft_pull(cx, cy, targets, speed_pxs, arrived, radius)

        self._last_raw = (self.ox, self.oy)
        self._last_candidate = cand

        # final on-screen clamp: AT-SPI emits boxes whose centroids can sit PAST
        # the screen edge (provider accepts to −100), and magnetism happily pulls
        # the cursor onto them — measured x=−35 in an edge-crawl session. An
        # off-screen cursor also drags the overlay window across the output
        # boundary every frame, which the compositor answers with flicker.
        fx = min(self.sw - 1.0, max(0.0, fx))
        fy = min(self.sh - 1.0, max(0.0, fy))

        return PointerState(
            fx, fy, speed_pxs, radius, snap_role, arrived, len(targets),
            raw=(self.ox, self.oy), corrected=(cx, cy), recal=self._recal.state(),
            mode=mode, deflection=deflection, still=self._neutral.is_still,
            comfort=comfort_state, endpoint=self._ep_state,
            focus={"id": self._focus_id, "d": getattr(self, "_focus_dist", None),
                   "ev": dict(self._fev)})

    def coast(self, targets, dt: float, predict: float,
              precision: bool = False) -> PointerState | None:
        """Render-rate coast for a frame with NO new pose (an IR strobe black
        frame). Extrapolates the head signal a fraction `predict` along its last
        velocity, advances the comfort follow toward it, and re-runs magnetism —
        so the cursor keeps gliding between real samples at ~2x the rate. No
        envelope learning, no clicks. Comfort (absolute) mode only; relative modes
        need a real per-frame delta and can't be coasted meaningfully."""
        if self.cfg["control_mode"] != "comfort" or not self._comfort.primed:
            return None
        h = self._sig_hist
        if len(h) >= 2 and predict > 0.0:
            rx = h[-1][0] + (h[-1][0] - h[-2][0]) * predict
            ry = h[-1][1] + (h[-1][1] - h[-2][1]) * predict
        else:
            rx, ry = self._last_signal_raw
        lx, ly = self._comfort.map(rx, ry, dt, self._last_speed, precision)
        self.ox = lx + self._mon_origin[0]
        self.oy = ly + self._mon_origin[1]

        cx, cy = self._recal.correct(self.ox, self.oy)
        cx = min(self.sw - 1.0, max(0.0, cx))
        cy = min(self.sh - 1.0, max(0.0, cy))
        dist = math.hypot(cx - self._prev[0], cy - self._prev[1])
        speed_pxs = dist / dt if dt > 0 else 0.0
        self._prev = (cx, cy)
        arrived = speed_pxs < ARRIVAL_PXS
        radius = self._catch_radius(speed_pxs)
        if self.cfg["focus_acquire"]:
            fx, fy, cand, snap_role = self._focus_magnetism(cx, cy, targets, arrived)
        else:
            fx, fy, cand, snap_role = self._soft_pull(cx, cy, targets, speed_pxs, arrived, radius)
        self._last_candidate = cand
        return PointerState(
            fx, fy, speed_pxs, radius, snap_role, arrived, len(targets),
            raw=(self.ox, self.oy), corrected=(cx, cy), mode="comfort",
            comfort=self._comfort.state(rx, ry))

    @property
    def last_candidate(self):
        """The magnetized target at the most recent frame (or None)."""
        return self._last_candidate

    @property
    def focus_id(self):
        """Id of the focus-held target (for the debug overlay highlight)."""
        return self._focus_id

    def observe_click(self) -> bool:
        """Feed the most recent confirmed click to the recalibrator. Only learns
        when the click landed on a magnetized centroid (the ground-truth target)."""
        if self._last_candidate is None:
            return False
        c = self._last_candidate
        # the endpoint posterior's click-history prior: you keep clicking that
        self._posterior.observe_click(c["cx"], c["cy"], c.get("role"))
        return self._recal.observe(self._last_raw, (c["cx"], c["cy"]))

    def observe_target(self, tx: float, ty: float) -> bool:
        """Feed a calibration label: the raw integrated pose → a KNOWN target."""
        return self._recal.observe(self._last_raw, (tx, ty))
