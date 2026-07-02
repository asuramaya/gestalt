# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Calibration mode — the look-and-pinch label bootstrap (Route 2 in
docs/LEARNED_TRACKER.md). Shows a target at a KNOWN screen position; the user
aims their head there and pinches; we record a clean (pose → known-point) label.

This is the fast unblock for the supervised experiment: the user's real apps
(Warp, web) expose no accessibility targets, so implicit clicks produce almost no
labels. A calibration grid yields hundreds of clean, screen-spanning labels in
minutes, app-independent. Labels write the same `fire`-anchor JSONL format
(role "calibration"), so the dataset format is unchanged; they also feed the
layer-4 recalibrator directly (high-quality ground truth).

The Calibration state machine is pure-stdlib (testable). CalibrationOverlay is the
pygame target-marker window (imported lazily).
"""
from __future__ import annotations

import random


class Calibration:
    """Grid of known screen targets; advances one per confirmed pinch, loops with
    fresh jitter so positions vary across passes (more diverse labels)."""

    def __init__(self, sw: int, sh: int, cols=4, rows=3, margin=0.12, jitter=0.04, seed=7):
        self.sw, self.sh = sw, sh
        self._jitter = jitter
        mx, my = margin * sw, margin * sh
        self._grid = []
        for r in range(rows):
            for c in range(cols):
                x = mx + (sw - 2 * mx) * (c / (cols - 1))
                y = my + (sh - 2 * my) * (r / (rows - 1))
                self._grid.append((x, y))
        self._rng = random.Random(seed)
        self._order = list(range(len(self._grid)))
        self.active = False
        self.idx = 0
        self.loop = 0
        self.labels = 0
        self._cur = self._grid[0]

    def _next_point(self):
        gx, gy = self._grid[self._order[self.idx]]
        jx = self._rng.uniform(-self._jitter, self._jitter) * self.sw
        jy = self._rng.uniform(-self._jitter, self._jitter) * self.sh
        self._cur = (min(self.sw - 1.0, max(0.0, gx + jx)),
                     min(self.sh - 1.0, max(0.0, gy + jy)))

    def start(self):
        self.active = True
        self.idx = self.loop = self.labels = 0
        self._rng.shuffle(self._order)
        self._next_point()

    def stop(self):
        self.active = False

    def advance(self):
        self.idx += 1
        if self.idx >= len(self._order):
            self.idx = 0
            self.loop += 1
            self._rng.shuffle(self._order)
        self._next_point()

    def record(self):
        self.labels += 1

    def current(self) -> tuple[float, float]:
        return self._cur

    def state(self) -> dict:
        return {
            "on": self.active,
            "labels": self.labels,
            "loop": self.loop,
            "grid": len(self._grid),
            "target": [round(self._cur[0]), round(self._cur[1])] if self.active else None,
        }


class CalibrationOverlay:
    """A bright bullseye window repositioned to each calibration target — the
    thing the user looks at. Always-on-top + click-through (same X11 trick as the
    cursor overlay)."""

    SIZE = 96

    def __init__(self):
        import os
        os.environ.setdefault("SDL_VIDEODRIVER", "x11")
        import pygame
        from pygame._sdl2.video import Renderer, Texture, Window
        from Xlib import X
        from Xlib import display as xdisplay
        from Xlib.ext import shape

        self._X = X
        self._shape = shape
        self._title = "gestalt-calibration"
        info = pygame.display.Info()
        self.sw, self.sh = info.current_w, info.current_h
        self._win = Window(self._title, size=(self.SIZE, self.SIZE),
                           position=(self.sw // 2, self.sh // 2),
                           borderless=True, always_on_top=True)
        self._ren = Renderer(self._win)
        self._tex = Texture.from_surface(self._ren, self._bullseye())
        self._xd = xdisplay.Display()
        self._xwin = self._find_window(self._title)
        self._make_click_through()

    @staticmethod
    def _bullseye():
        import pygame
        n = CalibrationOverlay.SIZE
        s = pygame.Surface((n, n), pygame.SRCALPHA)
        c = n // 2
        pygame.draw.circle(s, (0, 0, 0, 150), (c, c), 40, 6)
        pygame.draw.circle(s, (255, 90, 90), (c, c), 36, 5)      # red ring = aim here
        pygame.draw.circle(s, (255, 255, 255, 230), (c, c), 20, 3)
        pygame.draw.circle(s, (255, 90, 90), (c, c), 5)
        pygame.draw.line(s, (255, 255, 255, 200), (c - 14, c), (c + 14, c), 2)
        pygame.draw.line(s, (255, 255, 255, 200), (c, c - 14), (c, c + 14), 2)
        return s

    def _find_window(self, name):
        NET = self._xd.intern_atom("_NET_WM_NAME")

        def rec(w):
            try:
                n = w.get_wm_name()
                if n == name:
                    return w
                p = w.get_full_property(NET, 0)
                if p and p.value:
                    v = p.value
                    nm = (v.decode("utf-8", "replace")
                          if isinstance(v, (bytes, bytearray)) else str(v))
                    if nm == name:
                        return w
            except Exception:
                pass
            try:
                for ch in w.query_tree().children:
                    r = rec(ch)
                    if r:
                        return r
            except Exception:
                pass
            return None

        import time
        for _ in range(20):
            w = rec(self._xd.screen().root)
            if w:
                return w
            time.sleep(0.05)
        return None

    def _make_click_through(self):
        if not self._xwin:
            return
        X, shape = self._X, self._shape
        try:
            self._xwin.unmap()
            self._xd.sync()
            self._xwin.change_attributes(override_redirect=1)
            self._xd.sync()
            self._xwin.map()
            self._xd.sync()
            self._xwin.configure(stack_mode=X.Above)
            self._xd.sync()
            self._xwin.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, [])
            self._xd.sync()
        except Exception:
            pass

    def move_to(self, x: float, y: float):
        self._win.position = (int(x - self.SIZE / 2), int(y - self.SIZE / 2))
        if self._xwin:
            try:
                self._xwin.configure(stack_mode=self._X.Above)
                self._xd.sync()
            except Exception:
                pass
        self._ren.clear()
        self._tex.draw()
        self._ren.present()

    def close(self):
        try:
            self._win.destroy()
        except Exception:
            pass
