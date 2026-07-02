# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Configuration defaults and the single sanitize chokepoint.

Mirrors the PhanSpeed discipline: every config load AND every socket `set`
passes through `sanitize_config()`. It clamps numerics and validates enums so a
tampered config file or a hostile socket command can never push the daemon into
a bad state. Gestalt runs as the *user* (not root), so the threat model is far
lighter than PhanSpeed's — but keeping one chokepoint means the pill, the file,
and the CLI all share exactly one set of invariants.

Adding a config field (checklist):
  1. add it to DEFAULTS
  2. clamp / validate it here
  3. accept it in the daemon's `set` handler
  4. consume it in the relevant module (input/pointing/gesture/...)
  5. surface it in status() and the pill
"""
from __future__ import annotations

import math

# Action verbs the gesture map may bind to. Kept abstract on purpose — Gestalt
# is general, NOT hardcoded to any one app (no "focus Warp pane 3" here).
ACTIONS = {
    "left_click", "right_click", "middle_click", "double_click",
    "key_enter", "key_escape", "key_tab", "scroll_up", "scroll_down", "none",
}

# Actions that make sense to HOLD (press-and-drag / press-and-hold). A commit
# bound to anything else (double_click, scroll_*) is always an atomic tap even in
# gesture_hold mode — a drag is meaningless for them. Pure data so config.py stays
# stdlib-only; the detectors import this to decide tap-vs-engage.
HOLD_ACTIONS = {
    "left_click", "right_click", "middle_click",
    "key_enter", "key_escape", "key_tab",
}

# Fingers the pinch detector can arm (thumb-to-X distance). index + pinky are
# the reliable pair found in the prototype; middle/ring offered but off by default.
FINGERS = {"index", "middle", "ring", "pinky"}

# MediaPipe GestureRecognizer canonical labels (extension-based, trained,
# confidence-scored — robust on degraded IR where pinch-distance is not).
GESTURES = {"Closed_Fist", "Open_Palm", "Pointing_Up", "Thumb_Up",
            "Thumb_Down", "Victory", "ILoveYou"}

DEFAULTS = {
    # ---- lifecycle ---------------------------------------------------------
    "armed": True,                 # gestures + cursor active on start
    "calibrate": False,            # look-and-pinch calibration mode (label bootstrap)
    "record": False,               # log the signal stream + click anchors to JSONL
    "diag": False,                 # show the diagnostics window (camera + pipeline view)
    "cursor_in_shell": False,      # render the cursor in the GNOME extension (top layer, ABOVE
                                   #   shell menus) instead of the XWayland dot, which can't.
                                   #   Daemon writes the live pos to RUNTIME/cursor for it.
    "target_overlay": False,       # draw live AT-SPI target boxes over the desktop, coloured
                                   #   by role (buttons / links / entries / menus) — coverage debug
    "fps_cap": 60,                 # camera/processing loop ceiling
    "camera": "auto",              # "auto" = brightest /dev/video* node, or an index
                                   #   (pin an index to force the IR node; auto avoids it)
    "cam_width": 640,              # capture width  (set to the IR sensor's native, e.g. 576)
    "cam_height": 480,             # capture height (e.g. 360 for the IR node)
    "cam_normalize": False,        # CLAHE contrast lift — makes a dim IR face legible to
                                   #   MediaPipe; leave off for RGB
    "cam_fps": 0,                  # request this capture rate (0 = leave sensor default);
                                   #   bump it if a strobing IR cam halves your tracked fps
    "cam_strobe_ratio": 0.4,       # skip a frame only if it's < this fraction of the recent
                                   #   PEAK brightness — drops the IR strobe's black frames at
                                   #   ANY distance (a dim far face still passes; absolute
                                   #   thresholds ate the far frames → flicker on lean-back)
    # MediaPipe FaceLandmarker confidences — LOW so it clings to the face through
    # dim/small IR frames (fewer drops > precise detection for a continuous pointer).
    "face_min_detection": 0.2,     # to first acquire a face
    "face_min_presence": 0.1,      # to believe a face is still present
    "face_min_tracking": 0.1,      # to keep tracking it frame-to-frame (anti-flicker)
    "coast_interp": True,          # on a skipped (dark) frame, COAST the cursor instead of
                                   #   freezing — extrapolates head motion so the ~halved
                                   #   IR framerate still renders smooth (comfort mode only).
    "coast_predict": 0.5,          # extrapolation per coast frame (0 = persistence, 1 = a full
                                   #   inter-sample step; higher = smoother but risks overshoot)
    # ---- multi-monitor: comfort maps to the ACTIVE monitor; over-deflecting toward
    # a neighbour monitor (look hard past your range) warps + recentres onto it.
    "multimonitor": True,          # per-monitor comfort mapping + directional switching
    "monitor_cross_deg": 30.0,     # look this many degrees past neutral toward a neighbour
                                   #   monitor to cross to it (fixed angle, envelope-independent)
    "monitor_switch_cooldown": 0.6,  # seconds after a switch before another can fire

    # ---- input / head signal (1€ filter; Casiez CHI 2012) ------------------
    # Defaults are the prototype's proven head-tracking values (not the generic
    # 1€ reference 1.0/0.0): the head forward-vector signal is noisier than a mouse.
    "oneeuro_mincutoff": 2.0,      # raise to fight jitter at rest
    "oneeuro_beta": 0.8,           # raise to cut lag on fast moves
    "oneeuro_dcutoff": 1.0,        # derivative cutoff (rarely needs tuning)
    "pitch_limit_deg": 32.0,       # MediaPipe loses pose past ~this; flag in status

    # ---- layer 3: torso-referenced body-drift compensation (experimental) --
    # Absorbs head-signal drift that coincides with torso rotation (lean/slouch).
    # Default off + observable; see docs/POINTING.md §recentering.
    "torso_correction": False,     # enable body-drift compensation (needs Pose model)
    "torso_motion_deadband": 0.004,  # torso rotation rate below this absorbs nothing
    "torso_attribute_gain": 1.0,   # 1.0 = fully attribute coincident head change to body

    # ---- control mode ------------------------------------------------------
    "control_mode": "mouse",       # "mouse" = displacement (head delta -> cursor delta)
                                   # "joystick" = rate (head deflection -> cursor velocity)
                                   # "comfort" = absolute, self-ranging to your comfort ROM

    # ---- comfort mode: learned per-direction range -> screen (AGC for the neck)
    "comfort_lambda": 0.003,       # quantile adaptation rate (signal units/frame)
    "comfort_motion_thresh": 0.0015,  # head speed above which the envelope extremes learn
    "comfort_deadzone": 0.06,      # centre dead-zone as a fraction of each half-range
    "comfort_rest_alpha": 0.02,    # how fast neutral re-centres once genuinely parked
    "comfort_stationary": 0.03,    # max pose spread over ~2s to count as "at rest"
    "comfort_overscan": 0.15,      # map comfort range beyond the edge → corners reach early
    # ---- RubberEdge hybrid gain (Casiez UIST 2007): low-gain position control in
    # the comfortable range (precise, jitter shrinks with gain) + an elastic rate
    # assist near the extreme that GLIDES the cursor to the corner, so you never
    # strain past your learned range. Bounded offset, decays back when you relax.
    "comfort_edge_assist": True,   # enable the edge rate-assist + gain reduction
    "comfort_edge_reach": 0.85,    # position covers this fraction of the half at the extreme
                                   #   (lower = more precision; rate covers the rest)
    "comfort_edge_start": 0.8,     # deflection fraction where the rate-assist starts ramping
    "comfort_edge_rate": 2000.0,   # px/s glide speed at full deflection
    "comfort_edge_expo": 2.0,      # rate response curve (higher = gentler until near the edge)
    "comfort_edge_decay": 4.0,     # per-second ease-back of the elastic offset when relaxed
    "comfort_edge_speed": 0.008,   # head speed for full edge-assist. Below ~a third of this the
                                   #   assist is INERT + FROZEN, so a slow fine aim near the edge
                                   #   isn't eaten by the glide (only a fast reach triggers it)
    "comfort_smooth_mincut": 0.8,  # output 1€ cutoff at rest (lower = steadier when still)
    "comfort_smooth_beta": 0.7,    # output 1€ speed term (higher = more responsive moving)
    # ---- comfort fine-aim: directional-consistency follow (Angle Mouse; Wobbrock
    # CHI 2009). Replaces the speed-adaptive 1€ output stage. Follow-gain is driven
    # by path STRAIGHTNESS not speed, so a slow precise aim keeps gain while a
    # still-hold (pure jitter) floors it. See docs/POINTING.md §fine-aiming.
    "comfort_follow": True,        # use the straightness follow instead of output 1€
    "comfort_follow_window": 8,    # frames of target motion to measure straightness over
    "comfort_follow_decay": 0.6,   # recency weight (newest frame dominates; drops fast on stop)
    "comfort_follow_gmin": 0.05,   # follow-gain when motion is scattered (jitter → frozen)
    "comfort_follow_gmax": 0.9,    # follow-gain when motion is straight (intent → tracks)
    "comfort_follow_k": 2.0,       # straightness exponent (higher = only directed motion opens it)
    # ---- comfort fine-aim: gaze-fixation precision gate (clutch-free) -------
    # Eyes settling on a target (I-DT fixation; Salvucci & Goldberg ETRA 2000)
    # leads the head by ~200ms — so it gentles the follow gain BEFORE the head
    # settles. Calibration-free: only iris dispersion matters, never position.
    "gaze_fixation": False,        # gaze-fixation precision gate — OFF: iris-landmark
                                   #   gaze measured incoherent on BOTH camera nodes
                                   #   (2026-07-02, glasses; see docs/POINTING.md
                                   #   §VERDICT). A noise-only channel stays out of
                                   #   the pipeline; flip on only after the gaze dot
                                   #   passes a retest on changed hardware.
    "gaze_fix_window": 6,          # frames of iris signal for the dispersion test
    # SELF-CALIBRATING fixation threshold (the fixed 0.08 was measured stuck-ON on
    # one session and stuck-OFF on another — see docs/POINTING.md). Fixation =
    # dispersion < k × YOUR rolling-median dispersion, floored.
    "gaze_fix_k": 0.6,             # fraction of your median dispersion = fixating
    "gaze_fix_floor": 0.02,        # absolute threshold floor (a long stare collapses
                                   #   the median; the floor stops gate chatter there)
    "gaze_fix_baseline": 240,      # frames of dispersion history for the median
                                   #   (~30s @8fps strobed; slow enough to ride out
                                   #   a read/stare, fast enough to track lighting)
    "comfort_fix_gmax": 0.35,      # follow gmax while fully fixating (lower = finer approach)
    "comfort_fix_smooth": 0.2,     # engage slew toward fixation (per-frame; avoids snapping)
    # ---- stillness-freeze: lock the cursor when it SETTLES so rest drift stops
    # leaking (the straightness gate can't reject a slow involuntary drift — it
    # reads as "directed"). Engages on small cursor net-displacement, releases on
    # real head motion; a residual gain floor lets a directed push still break out.
    "comfort_freeze": True,        # enable the settle-freeze
    "comfort_freeze_floor": 0.05,  # residual gain fraction when fully frozen (0 = hard lock)
    "comfort_freeze_speed": 0.0018,   # smoothed head speed BELOW which the cursor locks (rest)
    "comfort_freeze_release": 0.0032,  # head speed above which a deliberate move unlocks it
    "comfort_freeze_attack": 0.25,  # per-frame ramp into the freeze (higher = locks faster)
    # ---- deceleration-aware gain (submovement model): after a ballistic reach, the
    # corrective HOMING phase is a deceleration — drop the gain there for a fine
    # landing on small targets (straightness can't see it; the freeze only catches
    # the final stop). Fast reach + slow steady creep both stay at full gain.
    "comfort_decel": True,         # enable deceleration-aware gain
    "comfort_decel_floor": 0.30,   # gain fraction at full deceleration (lower = finer landing)
    "comfort_decel_decay": 0.90,   # speed-peak decay/frame (~10-frame memory of the reach)
    "comfort_decel_min_peak": 0.005,  # only engage after a reach whose peak head speed exceeds this
    # ---- brow clutch → precision mode (HOLD the brows up for a low-gain crawl) ---
    # The brow's rest is pinned at the bottom of its range, so it's a poor AXIS but
    # an ideal discrete CLUTCH (frontalis, high-SNR, decoupled from mouth/head).
    # Precision is engaged WHILE the brows are held raised (a hold, not a toggle);
    # the cursor then moves precision_gain of its normal travel per head movement,
    # for the last inch. See docs/POINTING.md §precision.
    "brow_clutch": False,          # eyebrow precision HOLD — OFF: a manual gear-shift fights
                                   #   "it just works"; precision is automatic (freeze/decel/gaze)
    # thresholds are SELF-CALIBRATING — K × the live rest-noise (MAD), not a hardcoded
    # amplitude, so the SAME dimensionless K works for any face/rig (you're one of a
    # million). engage when lift > K_on × your-own-jitter; release below K_off × it.
    "brow_k_on": 5.0,              # engage at this many rest-noise-widths (MAD) above rest
    "brow_k_off": 3.0,             # release threshold in MAD units (hysteresis, k_off<k_on)
    "brow_floor": 0.020,           # absolute floor on the engage threshold — guards the
                                   #   ultra-still case where the noise (MAD) collapses
    "brow_confirm_frames": 2,      # raise must persist this many frames (anti IR noise)
    "brow_window": 45,             # frames of brow history for the MEDIAN rest baseline
                                   #   (~6s @7.5fps). rest = the median (auto-tunes, never
                                   #   sticks); a hold persists while raised frames stay a
                                   #   minority (~half the window). higher = steadier rest +
                                   #   shorter max hold; lower = longer holds but noisier rest
    "precision_gain": 0.35,        # CD-gain multiplier while precision is engaged (lower = finer)
    "precision_decay": 3.0,        # per-sec bleed of the precision offset during a fast re-aim
    "precision_timeout_s": 45.0,   # watchdog: release a STUCK raise after this long (needs a drop)
    # ---- gaze debug dot: draw the (uncalibrated) gaze estimate on screen, so you
    # can SEE whether the eye signal is even trackable before betting on it as a
    # pointer. NOT a pointer — a diagnostic. Sign/scale via gaze_debug_gain.
    "gaze_debug": False,           # show an on-screen dot at the raw gaze estimate
    "gaze_debug_gain": 4000.0,     # px per unit iris-offset (rough linear map)
    "comfort_prior_yaw_deg": 20.0,    # priors (Youdas comfort ROM) — refined online per-user
    "comfort_prior_pitch_up_deg": 13.0,    # up compressed: strains + tracks worst
    "comfort_prior_pitch_down_deg": 22.0,  # down generous: comfortable + well-tracked

    # ---- mouse mode: PRISM speed-scaled relative aiming --------------------
    # (slow head = fine precision, fast = full reach, tremor = frozen). These
    # work in head-signal-unit/frame speed; magnetism (below) works in px/s.
    "cd_base": 11000.0,            # px per head-signal-unit at full speed (reach)
    "head_min_speed": 0.0015,      # below this per-frame head speed: freeze (no chase)
    "head_max_speed": 0.012,       # above this: full CD gain
    "max_step_px": 200.0,          # per-frame clamp; kills flick-induced jumps

    # ---- joystick mode: rate control around a neutral pose -----------------
    "joystick_max_speed": 2600.0,  # px/s at full head deflection
    "joystick_deadzone": 0.02,     # head-deflection magnitude below which cursor is still
    "joystick_expo": 2.0,          # response curve: higher = gentler near centre, fast at edge
    # ---- neutral re-anchoring (stillness-gated; ZUPT analog) --------------
    "stillness_speed": 0.0012,     # per-frame head speed below this = "at rest"
    "stillness_ms": 400,           # rest hold before the neutral re-anchors
    "reanchor_alpha": 0.05,        # neutral slew per still-frame toward the rest pose

    # ---- magnetism (DynaSpot catch-radius + velocity-gated pull) -----------
    "dynaspot_min_speed": 100.0,   # cursor px/s; below this the catch-radius collapses
    "dynaspot_max_radius": 130.0,  # px; catch-radius ceiling at speed
    "snap_pull": 0.40,             # 0..1 soft attraction toward nearest centroid (legacy path)
    "snap_velocity_gate": 0.30,    # engage pull only below 30% of peak speed (Worden)

    # ---- temporal target stabilization (the anti-jiggle layer) -------------
    # Pixel-derived CV centroids flicker frame-to-frame; tracking them (associate
    # → smooth → debounce) turns them into stable, persistent targets so the
    # cursor doesn't chase a vibrating attractor. See docs/POINTING.md §CV.
    "target_track": True,          # enable cross-frame target tracking
    "target_assoc_px": 70.0,       # match a detection to a track within this distance
    "target_pos_alpha": 0.35,      # position EMA per read-cycle (lower = steadier targets)
    "target_min_hits": 2,          # appearance debounce: seen N reads before it's a target
    "target_max_miss": 3,          # persistence: keep a vanished target N missed reads

    # ---- focus-hysteresis magnetism (iPad-style: acquire → stick → break) --
    # Replaces the memoryless nearest-pull with a commit state machine: grab a
    # target when you slow near it, hold it (no flip-flop), release only on clear
    # directed intent away. acquire_px < break_px gives the sticky hysteresis.
    "focus_acquire": True,         # use the focus state machine (else legacy soft-pull)
    "focus_acquire_px": 90.0,      # grab a target within this radius when settling
    "focus_break_px": 200.0,       # release once the intended cursor leaves this radius
    "focus_pull": 1.0,             # snap toward the held target when settled (1.0 = hard lock,
                                   #   no head-jitter leak; the break radius still releases it)
    "focus_pull_move": 0.20,       # pull while moving (light, so you can slide off then break)

    # ---- KTM endpoint prediction → target posterior (see POINTING.md §endpoint)
    # Once a ballistic reach decelerates, the minimum-jerk profile says how much
    # distance remains — fuse that predicted endpoint with the target list and a
    # click-history prior, and pre-acquire focus BEFORE arrival (the job the eye
    # was supposed to do; §VERDICT). Fail-safe: every ambiguity → no intent →
    # behaves exactly like plain focus acquisition.
    "endpoint_predict": True,      # enable early (pre-arrival) focus acquisition
    "endpoint_min_peak_pxs": 900.0,  # cursor px/s a reach must hit to count as ballistic
    "endpoint_decel_ratio": 0.85,  # predict only once speed < this × the reach's peak
    "endpoint_sigma_frac": 0.35,   # posterior σ as a fraction of predicted remaining px
    "endpoint_sigma_min_px": 60.0,  # σ floor — never sharper than target-sized
    "endpoint_confidence": 2.0,    # winner must beat runner-up by this posterior ratio
    "endpoint_gate_px": 350.0,     # ignore targets further than this from the endpoint
    "endpoint_history_w": 0.25,    # click-history prior weight (0 = geometry only)

    # ---- implicit recalibration (PACE/EyeO-style; RLS from confirmed pinches)
    # Each confirmed click at a magnetized centroid is a (raw-pose, intended)
    # sample; an online affine map corrects mapping drift. See docs/POINTING.md.
    "recalibrate": True,           # learn a drift correction from confirmed clicks
    "recal_forgetting": 0.99,      # RLS forgetting factor (~1/(1-λ) ≈ 100-click memory)
    "recal_max_correction_px": 250.0,  # bound: one update can never fling the cursor further

    # ---- commit (Steady Clicks freeze; Trewin ASSETS 2006) -----------------
    "commit_velocity_gate": 0.012, # reject pinch-click above this head speed

    # ---- gestures ----------------------------------------------------------
    "pinch_close": 0.55,           # thumb-tip distance / palm to fire
    "pinch_rearm": 0.64,           # must re-open past this before next fire
    "pinch_confirm_frames": 2,     # debounce
    "cooldown_s": 0.12,            # min time between fires
    # gesture -> action map (general, user-editable). Default: AVP-style pair.
    "bindings": {
        "pinch_index": "left_click",
        "pinch_pinky": "key_enter",
    },
    # ---- trained-classifier gestures (alt to pinch; robust on IR / low fps) -
    # Uses MediaPipe's GestureRecognizer hand-shape labels (extension-based,
    # trained, debounced) instead of thumb-tip distance. No haptic, but far less
    # sensitive to the noisy fingertip landmarks IR/low-res produce.
    "gesture_mode": "pinch",       # "pinch" (haptic tap) | "gestures" (classifier)
    "gesture_confidence": 0.6,     # min classifier score to accept a gesture
    # ---- hold / drag: press on commit (settled), release when the gesture ends.
    # A quick gesture = a clean click (frozen until you move past drag_start_px);
    # a held gesture that moves = a drag; a held key = press-and-hold.
    "gesture_hold": True,          # press/release lifecycle (drag-capable) vs atomic tap
    "drag_start_px": 45.0,         # cursor must leave the press point by this to become a drag
    "gesture_release_frames": 2,   # gesture absent this many frames before release (anti-dropout)
    "hold_timeout_s": 20.0,        # watchdog: force-release a stuck hold after this long
    "gesture_bindings": {          # MediaPipe gesture label -> action (editable)
        "Pointing_Up": "left_click",
        "Victory": "right_click",
        "Thumb_Up": "key_enter",
        "Open_Palm": "key_escape",
    },

    # ---- target providers (the "cheap universal boxes" layer) --------------
    # Ordered list; the daemon merges their streams. Each runs as a subprocess
    # so the CV venv and system-python (AT-SPI) interpreters stay separate.
    # AT-SPI (the app's own accessibility tree) is the ONLY trustworthy target
    # source — exact, semantic, stable, ~free. Pixel CV was a dead end (drains on
    # capture, hallucinates on text, inaccurate on the panes it was meant for), so
    # it's off by default. No-a11y apps (Warp/canvas) get the raw fine-aim pointer
    # instead of fake targets — their targets (panes) are huge and easy to aim at.
    # The cv provider remains available (add "cv" + set cv_apps) but unrecommended.
    "providers": ["atspi"],
    "atspi_active_only": True,      # box only the ACTIVE (foreground) window's elements
                                   #   (+ gnome-shell chrome) — else occluded background
                                   #   windows' elements get boxed in "random places"
    "provider_poll_ms": 500,       # how often providers refresh their boxes (UI is static
                                   #   between polls; higher = less CV drain)
    # CV is the EXPENSIVE fallback (it captures + edge-detects the whole window). It
    # only runs when the ACTIVE window's WM_CLASS matches one of these (substring,
    # case-insensitive) — i.e. apps that expose no accessibility tree. Everywhere
    # else it sits idle (no capture, no drain). Empty list = CV never captures.
    "cv_apps": ["warp"],
    "cv_poll_ms": 1500,            # CV captures this rarely even when active (panes are
                                   #   static; the costly full-window grab needn't be frequent)
}

# clamp ranges: field -> (lo, hi)
_RANGES = {
    "fps_cap": (15, 144),
    "cam_width": (320, 1920),
    "cam_height": (240, 1080),
    "cam_fps": (0, 120),
    "cam_strobe_ratio": (0.0, 0.9),
    "face_min_detection": (0.05, 0.9),
    "face_min_presence": (0.05, 0.9),
    "face_min_tracking": (0.05, 0.9),
    "coast_predict": (0.0, 1.0),
    "monitor_cross_deg": (10.0, 60.0),
    "monitor_switch_cooldown": (0.1, 3.0),
    "oneeuro_mincutoff": (0.1, 10.0),
    "oneeuro_beta": (0.0, 5.0),
    "oneeuro_dcutoff": (0.1, 10.0),
    "pitch_limit_deg": (10.0, 60.0),
    "torso_motion_deadband": (0.0, 0.1),
    "torso_attribute_gain": (0.0, 2.0),
    "cd_base": (1000.0, 50000.0),
    "head_min_speed": (0.0, 0.05),
    "head_max_speed": (0.001, 0.1),
    "max_step_px": (20.0, 2000.0),
    "joystick_max_speed": (200.0, 10000.0),
    "joystick_deadzone": (0.0, 0.3),
    "joystick_expo": (1.0, 4.0),
    "comfort_lambda": (0.0001, 0.05),
    "comfort_motion_thresh": (0.0, 0.02),
    "comfort_deadzone": (0.0, 0.4),
    "comfort_rest_alpha": (0.0, 0.3),
    "comfort_stationary": (0.005, 0.3),
    "comfort_overscan": (0.0, 0.6),
    "comfort_edge_reach": (0.3, 1.0),
    "comfort_edge_start": (0.3, 1.0),
    "comfort_edge_rate": (0.0, 8000.0),
    "comfort_edge_expo": (1.0, 4.0),
    "comfort_edge_decay": (0.5, 20.0),
    "comfort_edge_speed": (0.0005, 0.05),
    "gesture_confidence": (0.0, 1.0),
    "drag_start_px": (5.0, 400.0),
    "gesture_release_frames": (1, 10),
    "hold_timeout_s": (2.0, 120.0),
    "comfort_smooth_mincut": (0.1, 8.0),
    "comfort_smooth_beta": (0.0, 5.0),
    "comfort_follow_window": (2, 30),
    "comfort_follow_decay": (0.1, 0.99),
    "comfort_follow_gmin": (0.0, 1.0),
    "comfort_follow_gmax": (0.0, 1.0),
    "comfort_follow_k": (0.5, 6.0),
    "gaze_fix_window": (2, 30),
    "gaze_fix_k": (0.1, 1.0),
    "gaze_fix_floor": (0.0, 0.5),
    "gaze_fix_baseline": (30, 1000),
    "comfort_fix_gmax": (0.0, 1.0),
    "comfort_fix_smooth": (0.01, 1.0),
    "comfort_freeze_floor": (0.0, 1.0),
    "comfort_freeze_speed": (0.0001, 0.02),
    "comfort_freeze_release": (0.0005, 0.05),
    "comfort_freeze_attack": (0.02, 1.0),
    "comfort_decel_floor": (0.05, 1.0),
    "comfort_decel_decay": (0.5, 0.99),
    "comfort_decel_min_peak": (0.001, 0.05),
    "brow_k_on": (1.0, 20.0),
    "brow_k_off": (0.5, 15.0),
    "brow_floor": (0.0, 0.2),
    "brow_confirm_frames": (1, 10),
    "brow_window": (10, 120),
    "precision_gain": (0.05, 1.0),
    "precision_decay": (0.0, 20.0),
    "precision_timeout_s": (5.0, 300.0),
    "gaze_debug_gain": (500.0, 12000.0),
    "comfort_prior_yaw_deg": (5.0, 45.0),
    "comfort_prior_pitch_up_deg": (5.0, 45.0),
    "comfort_prior_pitch_down_deg": (5.0, 45.0),
    "stillness_speed": (0.0, 0.02),
    "stillness_ms": (0, 3000),
    "reanchor_alpha": (0.0, 0.5),
    "dynaspot_min_speed": (10.0, 1000.0),
    "dynaspot_max_radius": (0.0, 600.0),
    "snap_pull": (0.0, 1.0),
    "snap_velocity_gate": (0.0, 1.0),
    "target_assoc_px": (10.0, 400.0),
    "target_pos_alpha": (0.02, 1.0),
    "target_min_hits": (1, 20),
    "target_max_miss": (0, 60),
    "endpoint_min_peak_pxs": (200.0, 6000.0),
    "endpoint_decel_ratio": (0.3, 0.98),
    "endpoint_sigma_frac": (0.05, 1.0),
    "endpoint_sigma_min_px": (10.0, 400.0),
    "endpoint_confidence": (1.0, 10.0),
    "endpoint_gate_px": (50.0, 1500.0),
    "endpoint_history_w": (0.0, 2.0),
    "focus_acquire_px": (10.0, 600.0),
    "focus_break_px": (20.0, 1200.0),
    "focus_pull": (0.0, 1.0),
    "focus_pull_move": (0.0, 1.0),
    "commit_velocity_gate": (0.0, 1.0),
    "pinch_close": (0.1, 1.5),
    "pinch_rearm": (0.1, 2.0),
    "pinch_confirm_frames": (1, 10),
    "cooldown_s": (0.0, 2.0),
    "provider_poll_ms": (50, 2000),
    "cv_poll_ms": (200, 5000),
    "recal_forgetting": (0.9, 0.9999),
    "recal_max_correction_px": (0.0, 1000.0),
}

_KNOWN_PROVIDERS = {"atspi", "cv"}


def _clamp(v, lo, hi):
    # Reject NaN/inf up front: NaN slips every comparison (all False), and inf
    # raises OverflowError on int() conversion — either way, fall back to lo.
    if isinstance(v, float) and not math.isfinite(v):
        return lo
    try:
        v = type(lo)(v)
    except (TypeError, ValueError, OverflowError):
        return lo
    return lo if v < lo else hi if v > hi else v


def sanitize_config(raw: dict) -> dict:
    """Return a fully-populated, invariant-safe config from arbitrary input."""
    cfg = dict(DEFAULTS)
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in DEFAULTS:
                cfg[k] = v

    cfg["armed"] = bool(cfg["armed"])
    cfg["calibrate"] = bool(cfg["calibrate"])
    cfg["record"] = bool(cfg["record"])
    cfg["diag"] = bool(cfg["diag"])
    cfg["target_overlay"] = bool(cfg["target_overlay"])
    cfg["cursor_in_shell"] = bool(cfg["cursor_in_shell"])
    cfg["atspi_active_only"] = bool(cfg["atspi_active_only"])
    cfg["cam_normalize"] = bool(cfg["cam_normalize"])
    cfg["coast_interp"] = bool(cfg["coast_interp"])
    cfg["multimonitor"] = bool(cfg["multimonitor"])
    cfg["gesture_hold"] = bool(cfg["gesture_hold"])
    cfg["recalibrate"] = bool(cfg["recalibrate"])
    cfg["torso_correction"] = bool(cfg["torso_correction"])
    cfg["comfort_follow"] = bool(cfg["comfort_follow"])
    cfg["gaze_fixation"] = bool(cfg["gaze_fixation"])
    cfg["brow_clutch"] = bool(cfg["brow_clutch"])
    cfg["gaze_debug"] = bool(cfg["gaze_debug"])
    cfg["comfort_edge_assist"] = bool(cfg["comfort_edge_assist"])
    cfg["comfort_freeze"] = bool(cfg["comfort_freeze"])
    cfg["comfort_decel"] = bool(cfg["comfort_decel"])
    cfg["target_track"] = bool(cfg["target_track"])
    cfg["focus_acquire"] = bool(cfg["focus_acquire"])
    cfg["endpoint_predict"] = bool(cfg["endpoint_predict"])
    if cfg["control_mode"] not in ("mouse", "joystick", "comfort"):
        cfg["control_mode"] = "mouse"

    for field, (lo, hi) in _RANGES.items():
        cfg[field] = _clamp(cfg[field], lo, hi)

    # camera: "auto" or a non-negative int index
    if cfg["camera"] != "auto":
        try:
            cfg["camera"] = max(0, int(cfg["camera"]))
        except (TypeError, ValueError, OverflowError):
            cfg["camera"] = "auto"

    # pinch_rearm must exceed pinch_close (open-then-close hysteresis)
    if cfg["pinch_rearm"] <= cfg["pinch_close"]:
        cfg["pinch_rearm"] = cfg["pinch_close"] + 0.05

    # brow release K must sit below engage K (raise/release hysteresis)
    if cfg["brow_k_off"] >= cfg["brow_k_on"]:
        cfg["brow_k_off"] = cfg["brow_k_on"] * 0.6

    # freeze release speed must exceed the lock speed (settle/unlock hysteresis)
    if cfg["comfort_freeze_release"] <= cfg["comfort_freeze_speed"]:
        cfg["comfort_freeze_release"] = cfg["comfort_freeze_speed"] * 1.8

    # bindings: keys must be pinch_<finger>, values must be known actions
    clean = {}
    src = cfg["bindings"] if isinstance(cfg["bindings"], dict) else {}
    for key, action in src.items():
        if (isinstance(key, str) and key.startswith("pinch_")
                and key[len("pinch_"):] in FINGERS and action in ACTIONS):
            clean[key] = action
    cfg["bindings"] = clean or dict(DEFAULTS["bindings"])

    # gesture mode + gesture_bindings: keys must be known MediaPipe labels
    if cfg["gesture_mode"] not in ("pinch", "gestures"):
        cfg["gesture_mode"] = "pinch"
    gclean = {}
    gsrc = cfg["gesture_bindings"] if isinstance(cfg["gesture_bindings"], dict) else {}
    for key, action in gsrc.items():
        if key in GESTURES and action in ACTIONS:
            gclean[key] = action
    cfg["gesture_bindings"] = gclean or dict(DEFAULTS["gesture_bindings"])

    # cv_apps: a clean list of lowercased non-empty WM_CLASS substrings
    apps = cfg["cv_apps"] if isinstance(cfg["cv_apps"], list) else []
    cfg["cv_apps"] = [str(a).strip().lower() for a in apps if str(a).strip()]

    # providers: known names only, order preserved, deduped
    seen, prov = set(), []
    src = cfg["providers"] if isinstance(cfg["providers"], list) else []
    for p in src:
        if p in _KNOWN_PROVIDERS and p not in seen:
            seen.add(p)
            prov.append(p)
    cfg["providers"] = prov

    return cfg
