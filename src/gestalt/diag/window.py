# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
The diagnostics window — a first-class, polished view of what the pipeline is
doing and *why* it's failing or succeeding. Toggled from the pill (the `diag`
control command); owned by the daemon because only it has the camera frame.

Upgraded from the prototype's number-dump to surface the real failure modes:
  * head past the pitch limit (the "looking too far up/down" dropout) — the
    forward-vector arrow turns red and the banner warns.
  * the DynaSpot catch-radius and whether a target is magnetized.
  * per-finger pinch readiness: idle / ready / waiting (Steady-Clicks holding a
    click because the head is still moving).
  * fps and the last fired action.
"""
from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "x11")

import pygame  # noqa: E402
from pygame._sdl2.video import Renderer, Texture, Window  # noqa: E402

W, H, CAM_W, CAM_H = 760, 700, 480, 360
HAND_EDGES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
              (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15),
              (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]
TIP_IDX = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}

GREEN = (90, 255, 150)
AMBER = (255, 200, 90)
RED = (255, 90, 90)
DIM = (150, 156, 170)
INK = (220, 225, 235)
READY_COL = {"ready": GREEN, "waiting": AMBER, "idle": DIM}


class DiagWindow:
    def __init__(self):
        pygame.font.init()
        self._win = Window("gestalt-diagnostics", size=(W, H), position=(40, 40))
        self._ren = Renderer(self._win)
        self._font = pygame.font.SysFont("monospace", 17)
        self._big = pygame.font.SysFont("monospace", 26)

    def render(self, rgb, head, ps, hand, pinch_dbg, dists, readiness=None,
               torso=None, record=None, calib=None, recal_on=False,
               fps=0.0, last_action=None):
        s = pygame.Surface((W, H))
        s.fill((18, 20, 26))

        # --- camera (mirrored, like a selfie) ---
        h, w = rgb.shape[:2]
        cam = pygame.transform.flip(
            pygame.transform.smoothscale(
                pygame.image.frombuffer(rgb.tobytes(), (w, h), "RGB"), (CAM_W, CAM_H)),
            True, False)
        s.blit(cam, (10, 10))
        pygame.draw.rect(s, (60, 66, 82), (10, 10, CAM_W, CAM_H), 1)

        # --- face mesh + forward-vector arrow (red when over the pitch limit) ---
        if head.landmarks:
            for p in head.landmarks:
                px = 10 + int((1 - min(1, max(0, p.x))) * CAM_W)
                py = 10 + int(min(1, max(0, p.y)) * CAM_H)
                s.set_at((px, py), (90, 150, 110))
            nose = head.landmarks[1]
            nx = 10 + int((1 - min(1, max(0, nose.x))) * CAM_W)
            ny = 10 + int(min(1, max(0, nose.y)) * CAM_H)
            fx, fy, _ = head.forward
            arrow_col = RED if head.over_pitch else AMBER
            pygame.draw.circle(s, RED, (nx, ny), 4)
            pygame.draw.line(s, arrow_col, (nx, ny), (nx - int(fx * 140), ny + int(fy * 140)), 3)
        if not head.ok:
            s.blit(self._big.render("FACE LOST", True, RED), (CAM_W - 170, 22))
        elif head.over_pitch:
            s.blit(self._big.render("HEAD TOO HIGH/LOW", True, AMBER), (40, 22))

        # --- torso: shoulder line (mirrored), tints with body-drift activity ---
        if torso is not None and getattr(torso, "present", False):
            import math as _m
            half = torso.width / 2.0
            cx0, cy0 = torso.mid
            dx = _m.cos(torso.roll_rad) * half
            dy = _m.sin(torso.roll_rad) * half
            def _pt(px, py):
                return (10 + int((1 - min(1, max(0, px))) * CAM_W),
                        10 + int(min(1, max(0, py)) * CAM_H))
            ls = _pt(cx0 - dx, cy0 - dy)
            rs = _pt(cx0 + dx, cy0 + dy)
            act = (head.body or {}).get("activity", 0.0)
            col = (int(90 + 165 * act), 200, int(150 - 60 * act))   # greener->amber w/ activity
            pygame.draw.line(s, col, ls, rs, 3)
            pygame.draw.circle(s, col, ls, 5)
            pygame.draw.circle(s, col, rs, 5)

        # --- comfort-mode envelope box (learned range + where you are in it) ---
        cm = getattr(ps, "comfort", None)
        if cm:
            bx, by, bw, bh = 510, 30, 220, 150
            pygame.draw.rect(s, (40, 44, 56), (bx, by, bw, bh))
            pygame.draw.rect(s, (90, 100, 120), (bx, by, bw, bh), 1)
            qx, qy = cm["qx"], cm["qy"]
            sx = lambda v: bx + (v - qx[0]) / max(qx[1] - qx[0], 1e-4) * bw   # noqa: E731
            sy = lambda v: by + (v - qy[0]) / max(qy[1] - qy[0], 1e-4) * bh   # noqa: E731
            # neutral crosshair
            pygame.draw.line(s, (90, 150, 110), (bx, sy(cm["ny"])), (bx + bw, sy(cm["ny"])), 1)
            pygame.draw.line(s, (90, 150, 110), (sx(cm["nx"]), by), (sx(cm["nx"]), by + bh), 1)
            # current head position within the learned envelope
            cx = max(bx, min(bx + bw, sx(cm["cur"][0])))
            cyy = max(by, min(by + bh, sy(cm["cur"][1])))
            pygame.draw.circle(s, GREEN, (int(cx), int(cyy)), 6)
            s.blit(self._font.render("comfort envelope", True, DIM), (bx, by - 22))

        # --- hand skeleton + active pinch link ---
        if hand:
            pts = [(10 + int((1 - p.x) * CAM_W), 10 + int(p.y * CAM_H)) for p in hand]
            for a, b in HAND_EDGES:
                pygame.draw.line(s, (120, 200, 255), pts[a], pts[b], 2)
            for p in pts:
                pygame.draw.circle(s, (255, 255, 255), p, 2)
            pygame.draw.circle(s, RED, pts[4], 5)        # thumb tip
            if pinch_dbg in TIP_IDX:
                pygame.draw.line(s, GREEN, pts[4], pts[TIP_IDX[pinch_dbg]], 3)

        # --- text rows: the pipeline's live state ---
        y = 10 + CAM_H + 12
        face = "OK  " if head.ok else "LOST"
        rc = getattr(ps, "recal", None) or {}
        gx, gy = rc.get("gain_x", 1), rc.get("gain_y", 1)
        ox, oy = rc.get("off_x", 0), rc.get("off_y", 0)
        recal_row = (
            f"recal {'on ' if rc.get('on') else 'off'}  "
            f"n={rc.get('samples', 0)} rej={rc.get('rejected', 0)}  "
            f"gain {gx:.2f},{gy:.2f}  off {ox:+.0f},{oy:+.0f}  res {rc.get('residual', 0):.0f}px"
        )
        mode = getattr(ps, "mode", "mouse")
        mode_row = f"mode {mode.upper():8} still {'yes' if getattr(ps, 'still', False) else 'no '}"
        if mode == "joystick":
            mode_row += f"  deflection {getattr(ps, 'deflection', 0.0):.3f}"
        bd = head.body or {}
        torso_ok = torso is not None and getattr(torso, "present", False)
        body_row = (
            f"body {'on ' if bd.get('on') else 'off'}  torso {'OK ' if torso_ok else '-- '} "
            f"act {bd.get('activity', 0.0):.2f}  drift {bd.get('mag', 0.0):.3f}"
        )
        ep = getattr(ps, "endpoint", None) or {}
        ep_row = (f"endpoint x{ep['x']},{ep['y']}  rem {ep['rem']:4d}px  "
                  f"intent {ep['tgt'] or '-'}"
                  if ep else "endpoint --  (no decelerating reach)")
        rows = [
            f"FPS {fps:4.1f}    hand {'YES' if hand else 'no '}    "
            f"FACE {face}  pitch {head.pitch_deg:+5.0f}d  yaw {head.yaw_deg:+5.0f}d",
            mode_row,
            f"cursor {int(ps.x)},{int(ps.y)}   speed {ps.speed_pxs:6.0f}px/s   "
            f"catch r={ps.catch_radius:3.0f}",
            f"targets {ps.target_count:3}   snap: {ps.snap_role or '-'}   "
            f"arrived: {'yes' if ps.arrived else 'no'}",
            ep_row,
            body_row,
            recal_row,
        ]
        for r in rows:
            s.blit(self._font.render(r, True, INK), (10, y))
            y += 24

        # per-finger pinch readiness chips: colour = state, number = thumb distance
        # (the smaller, the closer to a pinch). 'waiting' = Steady-Clicks holding
        # the click because the head is still moving.
        readiness = readiness or {}
        y += 6
        s.blit(self._font.render("pinch:", True, INK), (10, y))
        x = 90
        for i, fname in enumerate(readiness):
            state = readiness[fname]
            d = dists[i] if i < len(dists) else 9.9
            chip = f"{fname} {d:.2f} [{state}]"
            s.blit(self._font.render(chip, True, READY_COL.get(state, DIM)), (x, y))
            x += 200
        y += 30

        s.blit(self._font.render(f"last action: {last_action or '-'}", True, INK), (10, y))
        y += 28

        # --- recording status + dev-panel controls legend ---
        rec = record or {}
        if rec.get("on"):
            dot_x = 16
            pygame.draw.circle(s, RED, (dot_x, y + 9), 6)
            s.blit(self._font.render(
                f"REC  {rec.get('frames', 0)} frames · {rec.get('anchors', 0)} anchors"
                f"  →  {rec.get('file', '')}", True, RED), (32, y))
        else:
            s.blit(self._font.render("not recording", True, DIM), (10, y))
        y += 28

        if calib and calib.get("on"):
            s.blit(self._font.render(
                f"CALIBRATING — look at the bullseye + pinch.  "
                f"labels {calib.get('labels', 0)}  (loop {calib.get('loop', 0)})",
                True, (120, 255, 140)), (10, y))
            y += 26

        # controls — these keys work while THIS window is focused
        mode = getattr(ps, "mode", "mouse")
        body_on = (head.body or {}).get("on")
        legend = (
            f"[R]ec:{'on' if rec.get('on') else 'off'}  "
            f"[B]ody:{'on' if body_on else 'off'}  "
            f"[J]oy:{mode}  "
            f"r[E]cal:{'on' if recal_on else 'off'}  "
            f"ca[L]ib:{'on' if (calib and calib.get('on')) else 'off'}  "
            f"re[X]cal  re[C]enter  [A]rm"
        )
        s.blit(self._font.render(legend, True, (130, 200, 255)), (10, y))
        s.blit(self._font.render("(focus this window for keys)", True, DIM), (10, y + 22))

        tex = Texture.from_surface(self._ren, s)
        self._ren.clear()
        tex.draw()
        self._ren.present()

    def close(self):
        try:
            self._win.destroy()
        except Exception:
            pass
