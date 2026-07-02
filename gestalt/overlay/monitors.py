# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Monitor geometry — the virtual-desktop bounding box plus each physical monitor's
rect, queried from XRandR. Multi-monitor support hinges on two facts the old code
got wrong (it used pygame's primary-only size):

  * the evdev injector's ABS range must span the WHOLE virtual desktop, or clicks
    can't reach a second monitor;
  * comfort (absolute) mode must map your neck ROM to ONE monitor at a time, not
    the full desktop — stretching a fixed head range across two stacked screens
    doubles the gain (jitter back) and forces strained up-gaze to reach the top
    one. So we track an ACTIVE monitor and switch between them by direction.

Single-monitor setups collapse to one rect == the virtual desktop, so all the
per-monitor logic is a no-op and behaviour is unchanged.
"""
from __future__ import annotations


def _overlap(a0: float, al: float, b0: float, bl: float) -> bool:
    """1-D interval overlap: do [a0,a0+al) and [b0,b0+bl) intersect?"""
    return a0 < b0 + bl and b0 < a0 + al


class Monitors:
    def __init__(self):
        self.rects: list[tuple] = []   # (x, y, w, h, name, primary)
        self.vw = 0
        self.vh = 0
        self.active = 0
        self.refresh()

    def refresh(self):
        try:
            from Xlib import display
            d = display.Display()
            scr = d.screen()
            self.vw, self.vh = scr.width_in_pixels, scr.height_in_pixels
            rects = []
            for m in scr.root.xrandr_get_monitors().monitors:
                try:
                    name = d.get_atom_name(m.name)
                except Exception:
                    name = "?"
                rects.append((m.x, m.y, m.width_in_pixels, m.height_in_pixels,
                              name, bool(m.primary)))
            d.close()
            self.rects = rects
        except Exception:
            self.rects = []
        if not self.rects:                       # headless / no randr
            self.vw = self.vw or 1920
            self.vh = self.vh or 1080
            self.rects = [(0, 0, self.vw, self.vh, "screen", True)]
        self.active = next((i for i, r in enumerate(self.rects) if r[5]), 0)

    @property
    def multi(self) -> bool:
        return len(self.rects) > 1

    def active_rect(self) -> tuple[int, int, int, int]:
        x, y, w, h, _, _ = self.rects[self.active]
        return x, y, w, h

    def neighbor(self, direction: str):
        """Index of the nearest monitor in `direction` ('up'/'down'/'left'/'right')
        that overlaps the active one on the perpendicular axis, or None."""
        ax, ay, aw, ah = self.active_rect()
        acx, acy = ax + aw / 2.0, ay + ah / 2.0
        best, bi = None, None
        for i, (x, y, w, h, _, _) in enumerate(self.rects):
            if i == self.active:
                continue
            cx, cy = x + w / 2.0, y + h / 2.0
            if direction == "up" and cy < acy and _overlap(ax, aw, x, w):
                dist = acy - cy
            elif direction == "down" and cy > acy and _overlap(ax, aw, x, w):
                dist = cy - acy
            elif direction == "left" and cx < acx and _overlap(ay, ah, y, h):
                dist = acx - cx
            elif direction == "right" and cx > acx and _overlap(ay, ah, y, h):
                dist = cx - acx
            else:
                continue
            if best is None or dist < best:
                best, bi = dist, i
        return bi

    def switch(self, direction: str) -> bool:
        n = self.neighbor(direction)
        if n is None:
            return False
        self.active = n
        return True

    def state(self) -> dict:
        x, y, w, h = self.active_rect()
        return {"count": len(self.rects), "active": self.rects[self.active][4],
                "rect": [x, y, w, h], "virtual": [self.vw, self.vh]}
