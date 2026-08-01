# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Gaze debug dot — a magenta on-screen dot at the (uncalibrated) gaze estimate.

This is a DIAGNOSTIC, not a pointer. Appearance-based webcam gaze is ~2-4° with
the head moving (gaze.py never maps it to the screen for real aiming). But before
betting on the eye as a fine VERTICAL channel (head/mouth/brow all fail that axis,
see docs/POINTING.md §precision), we need to SEE whether the raw iris signal is
even coherent on this IR rig — does looking left move the dot left, is it stable,
does it cover range? This draws the head-relative iris offset through a crude
linear map so that question is answerable by eye.

Reuses the cursor overlay's X plumbing (override-redirect always-above window +
empty input shape for click-through). Gated on cfg["gaze_debug"]; off by default.
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("SDL_VIDEODRIVER", "x11")

import pygame  # noqa: E402
from pygame._sdl2.video import Renderer, Texture, Window  # noqa: E402
from Xlib import X  # noqa: E402
from Xlib import display as xdisplay  # noqa: E402
from Xlib.ext import shape  # noqa: E402

from .cursor import _find_window  # noqa: E402

WTITLE = "gestalt-gazedot"
SIZE = 40


def _dot_surface() -> pygame.Surface:
    s = pygame.Surface((SIZE, SIZE), pygame.SRCALPHA)
    c = SIZE // 2
    pygame.draw.circle(s, (0, 0, 0, 150), (c, c), 14, 5)   # dark halo
    pygame.draw.circle(s, (235, 90, 235), (c, c), 12, 0)   # magenta = raw gaze
    pygame.draw.circle(s, (255, 255, 255, 255), (c, c), 3)
    return s


class GazeDot:
    def __init__(self):
        pygame.init()                                      # idempotent (Cursor inits too)
        self._win = Window(WTITLE, size=(SIZE, SIZE), position=(0, 0),
                           borderless=True, always_on_top=True)
        self._win.opacity = 0.8
        self._ren = Renderer(self._win)
        self._tex = Texture.from_surface(self._ren, _dot_surface())
        self._xd = None
        self._xwin = None
        self._last_raise = 0.0
        self._make_click_through()

    def _make_click_through(self):
        self._xd = xdisplay.Display()
        for _ in range(20):
            self._xwin = _find_window(self._xd, WTITLE)
            if self._xwin:
                break
            time.sleep(0.1)
        if not self._xwin:
            return
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

    def move(self, x: float, y: float):
        self._win.position = (int(x - SIZE / 2), int(y - SIZE / 2))
        now = time.time()
        if self._xwin and now - self._last_raise > 0.25:
            try:
                self._xwin.configure(stack_mode=X.Above)
                self._xd.sync()
            except Exception:
                pass
            self._last_raise = now
        self._ren.clear()
        self._tex.draw()
        self._ren.present()

    def close(self):
        try:
            self._win.destroy()           # NOT pygame.quit() — the main cursor shares it
        except Exception:
            pass
