#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""Hardware-free test of the KTM endpoint predictor + target posterior.

Drives EndpointPredictor with synthetic minimum-jerk reaches (the profile the
estimator assumes — so this validates the algebra and the segmentation, not the
model's fit to real heads; that's what the recordings are for) and checks:
  * predictions exist only on the deceleration side of a ballistic reach;
  * error shrinks as the reach completes and lands near the true endpoint;
  * the posterior picks the on-path target and refuses ambiguous/behind cases.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gestalt.config import sanitize_config  # noqa: E402
from gestalt.pointing.endpoint import EndpointPredictor, TargetPosterior  # noqa: E402

CFG = sanitize_config({})
FPS = 60.0


def minjerk_reach(x0, y0, x1, y1, dur_s):
    """Yield (x, y, speed_pxs, dt) frames of a minimum-jerk reach."""
    n = int(dur_s * FPS)
    px, py = x0, y0
    for i in range(1, n + 1):
        t = i / n
        s = 10 * t**3 - 15 * t**4 + 6 * t**5
        x = x0 + (x1 - x0) * s
        y = y0 + (y1 - y0) * s
        dt = 1.0 / FPS
        speed = math.hypot(x - px, y - py) / dt
        yield x, y, speed, dt
        px, py = x, y


def test_convergence():
    pred = EndpointPredictor(CFG)
    tx, ty = 1900.0, 300.0
    errs = []
    for x, y, v, dt in minjerk_reach(400, 900, tx, ty, 0.6):
        p = pred.update(x, y, v, dt)
        if p is not None:
            errs.append((math.hypot(p[0] - tx, p[1] - ty), p[2]))
    assert errs, "a 1.6kpx reach at 60fps must produce predictions"
    # late predictions (last quarter) must land close; and must beat early ones
    late = [e for e, rem in errs if rem < 400]
    assert late, "expected predictions in the homing phase"
    assert min(late) < 120, f"late-phase best error too big: {min(late):.0f}px"
    assert min(late) <= errs[0][0], "error should shrink as the reach completes"


def test_no_prediction_without_ballistic():
    pred = EndpointPredictor(CFG)
    # a slow 60px/s creep never crosses the ballistic threshold
    for i in range(120):
        assert pred.update(500 + i, 500, 60.0, 1 / FPS) is None


def test_posterior_guards():
    post = TargetPosterior(CFG)
    mk = lambda cx, cy, role="button": {"cx": cx, "cy": cy, "role": role}  # noqa: E731
    # clear case: one target near the predicted endpoint, ahead of motion
    tg, ratio = post.best(1500, 500, 200, [mk(1520, 510), mk(700, 900)],
                          cx=1000, cy=700, dirx=1.0, diry=-0.4)
    assert tg is not None and tg["cx"] == 1520 and ratio > CFG["endpoint_confidence"]
    # behind the motion: never grabbed, even if near the predicted point
    tg, _ = post.best(1500, 500, 200, [mk(900, 720)],
                      cx=1000, cy=700, dirx=1.0, diry=-0.4)
    assert tg is None
    # ambiguous twins: flat posterior -> refuse
    tg, _ = post.best(1500, 500, 200, [mk(1450, 500), mk(1550, 500)],
                      cx=1000, cy=700, dirx=1.0, diry=-0.4)
    assert tg is None
    # click history breaks the tie once one twin has been chosen before
    for _ in range(30):
        post.observe_click(1550, 500, "button")
    tg, _ = post.best(1500, 500, 200, [mk(1450, 500), mk(1550, 500)],
                      cx=1000, cy=700, dirx=1.0, diry=-0.4)
    assert tg is not None and tg["cx"] == 1550


def main():
    test_convergence()
    test_no_prediction_without_ballistic()
    test_posterior_guards()
    print("test_endpoint: predictor converges, guards hold")


if __name__ == "__main__":
    main()
