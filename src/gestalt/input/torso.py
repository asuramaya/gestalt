# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Torso tracker — MediaPipe Pose shoulders → a slow body-orientation reference, so
the cursor can be made immune to whole-body lean/slouch drift (layer 3; see
docs/POINTING.md §recentering).

Only rotation of the torso changes head-relative-to-camera orientation; pure
translation does not (our signal is the face rotation matrix, already
translation-invariant). So we track the cues for torso *rotation* — shoulder-line
roll and shoulder width (lean/yaw proxies) — heavily smoothed, since the torso
moves slowly. Pose runs at a reduced rate (the engine calls it every few frames)
to keep CPU free for the face/gesture pipeline.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

L_SHOULDER, R_SHOULDER = 11, 12
EMA = 0.25   # heavy smoothing — torso is low-frequency


@dataclass
class TorsoState:
    present: bool = False
    roll_rad: float = 0.0        # shoulder-line tilt (rotation cue)
    width: float = 0.0           # normalized shoulder separation (lean/yaw cue)
    mid: tuple = (0.0, 0.0)      # shoulder midpoint (debug / overlay only)


class TorsoTracker:
    def __init__(self, model_path: str):
        self._pl = vision.PoseLandmarker.create_from_options(
            vision.PoseLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=model_path),
                running_mode=vision.RunningMode.VIDEO, num_poses=1,
                min_pose_detection_confidence=0.5,
                min_pose_presence_confidence=0.5,
                min_tracking_confidence=0.5))
        self._state = TorsoState()

    def process(self, mp_image, ts_ms: int) -> TorsoState:
        res = self._pl.detect_for_video(mp_image, ts_ms)
        if not res.pose_landmarks:
            self._state = TorsoState(present=False)
            return self._state
        lm = res.pose_landmarks[0]
        ls, rs = lm[L_SHOULDER], lm[R_SHOULDER]
        if min(getattr(ls, "visibility", 1.0), getattr(rs, "visibility", 1.0)) < 0.5:
            self._state = TorsoState(present=False)
            return self._state
        # tilt of the shoulder line from horizontal, in [-pi/2, pi/2]. Using
        # abs(dx) makes it independent of left/right ordering and keeps it away
        # from the ±pi wrap boundary, so EMA smoothing can't spin 360°.
        roll = math.atan2(rs.y - ls.y, abs(rs.x - ls.x))
        width = math.hypot(rs.x - ls.x, rs.y - ls.y)
        mid = ((ls.x + rs.x) / 2.0, (ls.y + rs.y) / 2.0)
        if not self._state.present:
            self._state = TorsoState(True, roll, width, mid)   # seed (no smoothing jump)
        else:
            p = self._state
            self._state = TorsoState(
                present=True,
                roll_rad=p.roll_rad + EMA * (roll - p.roll_rad),
                width=p.width + EMA * (width - p.width),
                mid=(p.mid[0] + EMA * (mid[0] - p.mid[0]),
                     p.mid[1] + EMA * (mid[1] - p.mid[1])))
        return self._state

    def close(self):
        try:
            self._pl.close()
        except Exception:
            pass
