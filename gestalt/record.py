# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Session recorder — persists the raw per-frame signal stream + confirmed-click
anchors to JSONL, so the decepticons-based learned tracker can be prototyped and
trained offline (see docs/LEARNED_TRACKER.md).

Two kinds of training signal are captured:
  * dense self-supervised stream — raw head forward vector, torso, hand points,
    and the cursor — every frame; lets a predictive coder learn to predict/denoise
    the next pose.
  * sparse supervised anchors — on a confirmed click, the (input, *intended
    target centroid*) pair, the ground-truth label the online readout calibrates
    against.

Deliberately logs RAW inputs (forward vector, raw landmark points), NOT
hand-derived features (no atan2 angles), so the learned model can discover the
geometry itself instead of inheriting our feature bugs.
"""
from __future__ import annotations

import json
import os
import time

# 5 hand points are enough to reconstruct pinch (thumb/finger distances) and the
# pointing ray: thumb tip, index tip, pinky tip, wrist, palm reference (mid MCP).
HAND_POINTS = {"thumb": 4, "index": 8, "pinky": 20, "wrist": 0, "palm": 9}


def _record_dir() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    d = os.path.join(base, "gestalt", "recordings")
    os.makedirs(d, exist_ok=True)
    return d


class Recorder:
    def __init__(self):
        self._fh = None
        self.path = None
        self.frames = 0
        self.anchors = 0
        self._since_flush = 0

    @property
    def active(self) -> bool:
        return self._fh is not None

    def start(self, session_stamp: int):
        if self._fh is not None:
            return
        self.path = os.path.join(_record_dir(), f"session-{session_stamp}.jsonl")
        self._fh = open(self.path, "w")
        self.frames = 0
        self.anchors = 0

    def stop(self):
        if self._fh is not None:
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:
                pass
        self._fh = None

    def write(self, head, ps, torso, hand, fire_anchor, t: float):
        """Append one frame. `fire_anchor` is the (target, action, finger) of a
        confirmed click this frame, or None."""
        if self._fh is None:
            return
        rec = {
            "t": round(t, 4),
            "face_ok": bool(head.ok),
            "fwd": [round(v, 5) for v in head.forward],
            "pitch": round(head.pitch_deg, 2),
            "yaw": round(head.yaw_deg, 2),
            "sig": [round(head.signal[0], 5), round(head.signal[1], 5)],
            "spd": round(head.speed, 6),
            "mode": ps.mode,
            "cursor": {
                "raw": [round(ps.raw[0], 1), round(ps.raw[1], 1)],
                "corr": [round(ps.corrected[0], 1), round(ps.corrected[1], 1)],
                "final": [round(ps.x, 1), round(ps.y, 1)],
                "snap": ps.snap_role,
            },
        }
        if getattr(head, "perioral", None) is not None:
            rec["mouth"] = head.perioral      # perioral landmarks in head-local frame
        if getattr(head, "ok", False):
            rec["brow"] = round(getattr(head, "brow_lift", 0.0), 5)   # eyebrow lift above rest
            # iris-in-eye gaze vector + I-DT dispersion + fixation flag — to MEASURE
            # whether the IR iris signal can drive the anticipatory gaze precision gate
            # (does it track gaze direction? is dispersion bimodal: fixation vs saccade?)
            g = getattr(head, "gaze", (0.0, 0.0))
            rec["gaze"] = [round(g[0], 5), round(g[1], 5),
                           round(getattr(head, "gaze_disp", 1.0), 5),
                           int(getattr(head, "fixating", False))]
        # comfort envelope internals — so we can SEE whether the rest-pose neutral
        # TRACKS the user's posture or LAGS it (the non-stationarity = the real
        # accuracy-across-pose lever, since body-comp is bypassed in comfort).
        if getattr(ps, "comfort", None):
            rec["comfort"] = ps.comfort          # nx,ny (neutral), qx,qy (ranges), cur, gain…
        if getattr(ps, "endpoint", None):
            rec["endpoint"] = ps.endpoint        # KTM prediction — eval it against anchors
        if getattr(head, "body", None):
            rec["body"] = head.body              # torso drift (unused in comfort; logged)
        if torso is not None and getattr(torso, "present", False):
            rec["torso"] = {"roll": round(torso.roll_rad, 4),
                            "width": round(torso.width, 4),
                            "mid": [round(torso.mid[0], 4), round(torso.mid[1], 4)]}
        if hand is not None:
            rec["hand"] = {k: [round(hand[i].x, 4), round(hand[i].y, 4)]
                           for k, i in HAND_POINTS.items()}
        if fire_anchor is not None:
            rec["fire"] = fire_anchor
            self.anchors += 1
        self._fh.write(json.dumps(rec) + "\n")
        self.frames += 1
        self._since_flush += 1
        if self._since_flush >= 60:        # flush ~2–3s of frames at a time
            self._fh.flush()
            self._since_flush = 0

    def state(self) -> dict:
        return {"on": self.active, "frames": self.frames,
                "anchors": self.anchors,
                "file": os.path.basename(self.path) if self.path else None}


def session_stamp() -> int:
    return int(time.time())
