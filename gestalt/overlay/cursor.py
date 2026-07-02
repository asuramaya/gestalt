# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
The floating cursor overlay — a borderless, always-on-top, click-through ring
that follows the head pointer without ever hijacking the real mouse.

Ported from the prototype. The mechanics that make it work on GNOME Wayland:
  * pygame `_sdl2` window under SDL_VIDEODRIVER=x11 (XWayland) so it can be
    repositioned each frame — native Wayland can't self-position a window.
  * X11 override-redirect (unmanaged by the WM) -> sits in the always-above layer.
  * XShape empty *input* region -> pointer events pass through to the app beneath.
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

WTITLE = "gestalt-overlay"
SIZE = 48


def _cursor_surface(snapped: bool, precision: bool = False) -> pygame.Surface:
    s = pygame.Surface((SIZE, SIZE), pygame.SRCALPHA)
    c = SIZE // 2
    # amber = precision (low-gain) mode; green = magnetized; teal = free aim.
    col = (255, 176, 32) if precision else (120, 255, 140) if snapped else (51, 214, 200)
    pygame.draw.circle(s, (0, 0, 0, 160), (c, c), 20, 7)   # dark halo for contrast
    pygame.draw.circle(s, col, (c, c), 18, 6 if (snapped or precision) else 4)
    if precision:                                          # inner dot ring → "fine"
        pygame.draw.circle(s, col, (c, c), 9, 2)
    pygame.draw.circle(s, (255, 255, 255, 255), (c, c), 3)
    return s


def _find_window(xd, name):
    NET = xd.intern_atom("_NET_WM_NAME")

    def wname(w):
        try:
            n = w.get_wm_name()
            if n:
                return n
        except Exception:
            pass
        try:
            p = w.get_full_property(NET, 0)
            if p and p.value:
                v = p.value
                return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v)
        except Exception:
            pass
        return None

    def rec(w):
        if wname(w) == name:
            return w
        try:
            for ch in w.query_tree().children:
                r = rec(ch)
                if r:
                    return r
        except Exception:
            pass
        return None

    return rec(xd.screen().root)


class Cursor:
    def __init__(self):
        pygame.init()
        info = pygame.display.Info()
        self.sw, self.sh = info.current_w, info.current_h
        self._win = Window(WTITLE, size=(SIZE, SIZE),
                           position=(self.sw // 2, self.sh // 2),
                           borderless=True, always_on_top=True)
        self._win.opacity = 0.9
        self._ren = Renderer(self._win)
        self._tex = Texture.from_surface(self._ren, _cursor_surface(False))
        self._tex_snap = Texture.from_surface(self._ren, _cursor_surface(True))
        self._tex_prec = Texture.from_surface(self._ren, _cursor_surface(False, precision=True))
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
            self._xwin.change_attributes(override_redirect=1)   # WM-unmanaged -> always-above layer
            self._xd.sync()
            self._xwin.map()
            self._xd.sync()
            self._xwin.configure(stack_mode=X.Above)
            self._xd.sync()
            # empty input region -> pointer events pass through to the app beneath
            self._xwin.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, [])
            self._xd.sync()
        except Exception:
            pass

    def set_hidden(self, hidden: bool):
        """Hide/show the XWayland dot (when the GNOME extension renders the cursor
        in-shell instead, so it can sit above shell menus). Idempotent + cheap."""
        if hidden == getattr(self, "_hidden", False):
            return
        self._hidden = hidden
        try:
            self._win.opacity = 0.0 if hidden else 0.9
        except Exception:
            pass

    def move(self, x: float, y: float, snapped: bool = False, precision: bool = False):
        self._win.position = (int(x - SIZE / 2), int(y - SIZE / 2))
        # periodically re-assert stacking so transient windows don't cover us
        now = time.time()
        if self._xwin and now - self._last_raise > 0.25:
            try:
                self._xwin.configure(stack_mode=X.Above)
                self._xd.sync()
            except Exception:
                pass
            self._last_raise = now
        self._ren.clear()
        tex = self._tex_prec if precision else (self._tex_snap if snapped else self._tex)
        tex.draw()
        self._ren.present()

    def pump(self) -> bool:
        """Process window events; return False if the user closed/quit the overlay."""
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return False
            if e.type == pygame.KEYDOWN and e.key == pygame.K_q:
                return False
        return True

    def close(self):
        try:
            pygame.quit()
        except Exception:
            pass
