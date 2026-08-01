# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Head pose tracker: MediaPipe FaceLandmarker -> head forward vector -> 1€-filtered
signal + per-frame speed. Distance-invariant (uses the facial transformation
matrix, not raw landmark pixels), so leaning closer/further doesn't move the
cursor. Ported from the prototype's face block.

The output `signal` (filtered fwd x,y) is what the pointer integrates; `speed`
is the per-frame head-signal speed the PRISM CD-scaling and the Steady-Clicks
commit gate both read.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from .body import BodyCompensator
from .brow import BrowClutch
from .gaze import GazeTracker
from .onefilter import OneEuro
from .perioral import perioral

_HERE = os.path.dirname(os.path.abspath(__file__))
# model lives at the package root (installed next to gestalt/ by install.sh)
MODEL = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "models", "face_landmarker.task")
FORWARD = np.array([0.0, 0.0, 1.0])   # head forward axis in the canonical face model


@dataclass
class HeadState:
    ok: bool = False
    signal: tuple[float, float] = (0.0, 0.0)       # filtered forward, body-compensated
    signal_raw: tuple[float, float] = (0.0, 0.0)   # filtered forward, NO body comp (comfort mode)
    delta: tuple[float, float] = (0.0, 0.0)    # change in signal since last frame
    speed: float = 0.0                          # |delta| — per-frame head-signal speed
    pitch_deg: float = 0.0
    yaw_deg: float = 0.0
    over_pitch: bool = False                     # past pitch_limit_deg (pose unreliable)
    landmarks: object = field(default=None)      # raw face landmarks (debug/overlay)
    forward: tuple[float, float, float] = (0.0, 0.0, 1.0)
    body: dict = field(default_factory=dict)     # body-compensator state (diagnostics)
    gaze: tuple[float, float] = (0.0, 0.0)       # iris-in-eye vector (head-relative)
    gaze_disp: float = 1.0                       # I-DT dispersion (low = eyes settled)
    gaze_thr: float = 0.0                        # live self-calibrated fixation threshold
    fixating: bool = False                       # eyes locked on a target (precision cue)
    perioral: object = field(default=None)       # mouth/nose landmarks in head-local frame
    brow_lift: float = 0.0                        # eyebrow height above rest (head-local)
    brow_raised: bool = False                     # brow currently held up (hysteresis)
    brow_toggle: bool = False                     # confirmed rising edge — flips precision
    brow_thr: float = 0.0                         # live self-calibrated engage threshold (K×MAD)


class HeadTracker:
    def __init__(self, cfg: dict, model_path: str | None = None):
        self.cfg = cfg
        path = model_path or MODEL
        self._fl = vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=path),
                running_mode=vision.RunningMode.VIDEO, num_faces=1,
                output_facial_transformation_matrixes=True,
                # low confidences: cling to the face through dim/small IR frames
                # (lean-back). Fewer drops beats precise detection for a pointer.
                min_face_detection_confidence=cfg.get("face_min_detection", 0.2),
                min_face_presence_confidence=cfg.get("face_min_presence", 0.1),
                min_tracking_confidence=cfg.get("face_min_tracking", 0.1)))
        self._reset_filters()
        self._prev = None
        self._body = BodyCompensator(cfg)
        self._gaze = GazeTracker(cfg)
        self._brow = BrowClutch(cfg)

    def _reset_filters(self):
        mc = self.cfg["oneeuro_mincutoff"]
        b = self.cfg["oneeuro_beta"]
        dc = self.cfg["oneeuro_dcutoff"]
        self._fx = OneEuro(mc, b, dc)
        self._fy = OneEuro(mc, b, dc)

    def apply_config(self, cfg: dict):
        self.cfg = cfg
        self._reset_filters()
        self._body.apply_config(cfg)
        self._gaze.apply_config(cfg)
        self._brow.apply_config(cfg)

    def reset_body(self):
        """Clear accumulated body-drift offset (on recenter)."""
        self._body.reset()

    def process(self, mp_image, ts_ms: int, t: float, torso=None) -> HeadState:
        res = self._fl.detect_for_video(mp_image, ts_ms)
        st = HeadState()
        st.landmarks = res.face_landmarks[0] if res.face_landmarks else None
        if not res.facial_transformation_matrixes:
            self._prev = None       # lost face -> next reacquire starts fresh
            self._gaze.reset()      # drop the fixation window so it re-settles clean
            self._brow.reset()      # re-seat the brow baseline on reacquire
            return st
        st.ok = True
        fwd = res.facial_transformation_matrixes[0][:3, :3].dot(FORWARD)
        st.forward = (float(fwd[0]), float(fwd[1]), float(fwd[2]))
        zc = abs(float(fwd[2])) + 1e-6
        st.yaw_deg = math.degrees(math.atan2(float(fwd[0]), zc))
        st.pitch_deg = math.degrees(math.atan2(float(fwd[1]), zc))
        st.over_pitch = abs(st.pitch_deg) > self.cfg["pitch_limit_deg"]

        # 1€-filter the raw forward vector first; this is the comfort-mode signal.
        sx = self._fx(float(fwd[0]), t)
        sy = self._fy(float(fwd[1]), t)
        st.signal_raw = (sx, sy)
        # layer 3: body-rotation drift absorbed on top — for mouse/joystick only
        # (a constant offset is harmless there; it would corrupt absolute comfort).
        bx, by = self._body((sx, sy), torso)
        st.body = self._body.state()
        st.signal = (bx, by)
        # delta/speed measure true head motion (raw), used for gating in all modes.
        if self._prev is None:
            self._prev = (sx, sy)
        dsx = sx - self._prev[0]
        dsy = sy - self._prev[1]
        self._prev = (sx, sy)
        st.delta = (dsx, dsy)
        st.speed = math.hypot(dsx, dsy)
        # iris-in-eye gaze + fixation (calibration-free precision cue, see gaze.py)
        gx, gy, disp, fix = self._gaze.update(st.landmarks)
        st.gaze = (gx, gy)
        st.gaze_disp = disp
        st.gaze_thr = self._gaze.thr
        st.fixating = fix
        # perioral (mouth/nose) landmarks in head-local frame — for the fine-pointing
        # experiment; logged by the recorder to measure resolution vs the head.
        st.perioral = perioral(st.landmarks)
        # eyebrow clutch — a confirmed brow-raise toggles precision mode (see brow.py)
        st.brow_lift, st.brow_raised, st.brow_toggle = self._brow.update(st.landmarks)
        st.brow_thr = self._brow.thr_on
        return st

    def close(self):
        try:
            self._fl.close()
        except Exception:
            pass
