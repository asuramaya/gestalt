# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Target debug overlay — a full-virtual-desktop, click-through window that draws the
live AT-SPI target boxes the magnetism sees, right where they are on screen, so you
can SEE accessibility coverage for the focused app. Boxes are coloured by role
(button / link / entry / menu / other); the box the cursor has FOCUSED is yellow.

Same click-through mechanics as the cursor overlay (override-redirect + XShape
empty input region); it just covers the whole desktop and renders rectangles.
Toggled by cfg["target_overlay"]; never raises over the cursor dot (raised once, the
cursor re-asserts top every frame).
"""
from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "x11")

import time  # noqa: E402

import pygame  # noqa: E402
from pygame._sdl2.video import Renderer, Window  # noqa: E402
from Xlib import X  # noqa: E402
from Xlib import display as xdisplay  # noqa: E402
from Xlib.ext import shape  # noqa: E402

from .cursor import _find_window  # noqa: E402

TTITLE = "gestalt-targets"
_FOCUS = pygame.Color(255, 220, 40)      # the grabbed target (yellow)
# AT-SPI role -> colour, so coverage reads at a glance
_ROLE_COLORS = {
    "button": pygame.Color(60, 210, 110),     # green
    "link": pygame.Color(90, 180, 255),       # blue
    "entry": pygame.Color(255, 150, 60),      # orange
    "menu": pygame.Color(190, 120, 255),      # purple
    "other": pygame.Color(150, 150, 150),     # grey
}
_BUTTON = {"push button", "toggle button", "check box", "radio button",
           "push button menu", "spin button", "slider"}
_LINK = {"link"}
_ENTRY = {"entry", "combo box", "text", "password text"}
_MENU = {"menu item", "menu", "page tab", "list item", "table cell", "icon",
         "check menu item", "radio menu item"}


def _role_color(role):
    r = (role or "").lower()
    if r in _BUTTON:
        return _ROLE_COLORS["button"]
    if r in _LINK:
        return _ROLE_COLORS["link"]
    if r in _ENTRY:
        return _ROLE_COLORS["entry"]
    if r in _MENU:
        return _ROLE_COLORS["menu"]
    return _ROLE_COLORS["other"]


class TargetOverlay:
    def __init__(self, vw: int, vh: int):
        if not pygame.get_init():
            pygame.init()
        self.vw, self.vh = vw, vh
        self._win = Window(TTITLE, size=(vw, vh), position=(0, 0),
                           borderless=True, always_on_top=True)
        self._ren = Renderer(self._win)
        self._xd = None
        self._xwin = None
        self._last_sig = None         # skip redraw when boxes AND focus are unchanged
        self._last_shape = None       # skip the XShape op when geometry alone is unchanged
        self._make_click_through()

    def _make_click_through(self):
        self._xd = xdisplay.Display()
        for _ in range(20):
            self._xwin = _find_window(self._xd, TTITLE)
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
            # empty input region -> all pointer/key events pass through
            self._xwin.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, [])
            self._xd.sync()
            # Re-assert the origin to (0,0): the window was briefly WM-managed at
            # creation and got placed BELOW the top-bar strut (y=32), and
            # override-redirect then froze that offset — so every box rendered 32px
            # low (a full element-height off for small targets). Now unmanaged, this
            # placement sticks.
            self._xwin.configure(x=0, y=0)
            self._xd.sync()
        except Exception:
            pass

    @staticmethod
    def _outline(cx, cy, w, h, th):
        """The 4 border bars + centroid dot of a box, as (x,y,w,h) int rects."""
        x, y, w, h = int(cx - w / 2), int(cy - h / 2), int(w), int(h)
        return [(x, y, w, th), (x, y + h - th, w, th),          # top, bottom
                (x, y, th, h), (x + w - th, y, th, h),          # left, right
                (int(cx) - 2, int(cy) - 2, 4, 4)]               # centroid

    def render(self, targets, focus_id=None):
        # Redraw only when boxes or focus change; RESHAPE only when the GEOMETRY
        # changes. The shape must NOT depend on focus (constant border thickness,
        # focus shown by colour alone): focus churns per-frame while aiming, and
        # re-shaping a full-desktop always-on-top window per frame forces the
        # compositor to recomposite the whole screen — the "screen flickers near
        # the edge" report. A recolour is a plain redraw; the X shape op is the
        # expensive part and now fires only when targets actually move.
        shape_sig = tuple((int(t.get("cx", 0)), int(t.get("cy", 0)),
                           int(t.get("w", 0)), int(t.get("h", 0))) for t in targets)
        sig = (focus_id, tuple(t.get("id") for t in targets), shape_sig)
        if sig == self._last_sig:
            return
        self._last_sig = sig
        # The window's framebuffer has no alpha here (2nd SDL window), so instead
        # of transparency we CLIP the window to just the outline bars via the X
        # bounding shape — everything else is cut out, the desktop shows through.
        shape_rects, draws = [], []
        for t in targets:
            try:
                cx, cy = t["cx"], t["cy"]
            except (KeyError, TypeError):
                continue
            w, h = t.get("w", 40) or 40, t.get("h", 40) or 40
            focused = t.get("id") is not None and t.get("id") == focus_id
            col = _FOCUS if focused else _role_color(t.get("role"))
            for (rx, ry, rw, rh) in self._outline(cx, cy, w, h, 2):
                rw, rh = max(1, rw), max(1, rh)
                shape_rects.append({"x": rx, "y": ry, "width": rw, "height": rh})
                draws.append((pygame.Rect(rx, ry, rw, rh), col))
        if self._xwin is not None and shape_sig != self._last_shape:
            self._last_shape = shape_sig
            try:
                self._xwin.configure(x=0, y=0)            # hold the origin (anti-drift)
                self._xwin.shape_rectangles(
                    shape.SO.Set, shape.SK.Bounding, X.Unsorted, 0, 0,
                    shape_rects or [{"x": 0, "y": 0, "width": 1, "height": 1}])
                self._xd.flush()
            except Exception:
                pass
        r = self._ren
        r.draw_color = pygame.Color(0, 0, 0)
        r.clear()
        for rect, col in draws:
            r.draw_color = col
            r.fill_rect(rect)
        r.present()

    def close(self):
        try:
            self._win.destroy()
        except Exception:
            pass
