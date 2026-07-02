# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Perioral signal — mouth / nasolabial landmark positions expressed in the head's
LOCAL frame, so rigid head motion cancels and only the muscle movement remains.

The hypothesis (see the project notes): the perioral muscles — levator labii
superioris alaeque nasi (the nose↔lip "snarl" muscle), zygomaticus (lateral
pull), orbicularis oris — are low-inertia and finely graded, so a small mouth
movement may out-resolve the head at the last inch, with the same lateral-broad /
vertical-narrow anisotropy as the neck. This module just EXTRACTS the candidate
signals cleanly; the recorder logs them so we can measure resolution vs the head
before building anything.

Method: build a 2-D frame from the stable inner eye corners (origin = their
midpoint, x-axis = left→right eye, scale = inter-eye distance), then express each
perioral landmark in that frame. The frame rides the head (rotation, translation,
scale all cancel), leaving a pure mouth-gesture vector per landmark.
"""
from __future__ import annotations

import math

# stable upper-face anchors that define the head-local frame
L_EYE_IN, R_EYE_IN = 133, 362
# perioral landmarks of interest (MediaPipe FaceLandmarker 478-pt mesh)
POINTS = {
    "ulip_out": 0,     # cupid's bow / philtrum base (rises on a lip-raise/snarl)
    "ulip_in": 13,     # upper-lip inner centre
    "llip": 14,        # lower-lip inner centre
    "corner_l": 61,    # left mouth corner (lateral pull / smile)
    "corner_r": 291,   # right mouth corner
    "subnasale": 2,    # nose base, top of the philtrum
    "nose_tip": 1,
}


def perioral(landmarks):
    """Return {name: [horiz, vert]} for each perioral landmark in the head-local
    frame (normalized by inter-eye distance), or None if landmarks unavailable."""
    lm = landmarks
    if lm is None or len(lm) < 478:
        return None
    lx, ly = lm[L_EYE_IN].x, lm[L_EYE_IN].y
    rx, ry = lm[R_EYE_IN].x, lm[R_EYE_IN].y
    ox, oy = (lx + rx) * 0.5, (ly + ry) * 0.5          # frame origin (between eyes)
    ax, ay = rx - lx, ry - ly
    scale = math.hypot(ax, ay) or 1e-6
    ax, ay = ax / scale, ay / scale                    # unit horizontal axis
    px, py = -ay, ax                                   # unit vertical (down the face)
    out = {}
    for name, i in POINTS.items():
        dx, dy = lm[i].x - ox, lm[i].y - oy
        out[name] = [round((dx * ax + dy * ay) / scale, 5),   # horizontal in face widths
                     round((dx * px + dy * py) / scale, 5)]    # vertical
    return out
