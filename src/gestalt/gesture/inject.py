# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Input injection via evdev/uinput — an absolute pointer (warp + click) and a
keyboard, both through the world-writable /dev/uinput (no root, no ydotool).
Ported verbatim from the prototype. The udev rule install.sh drops grants the
device access on machines where uinput isn't already world-writable.
"""
from __future__ import annotations

import time

from evdev import AbsInfo, UInput
from evdev import ecodes as ec

# action name (from config bindings) -> (kind, evdev code / arg). 'click' uses
# the pointer device (warp then press), 'key' uses the keyboard device. 'double'
# is two click cycles on the pointer; 'scroll' emits REL_WHEEL (code is the
# direction: +1 up / -1 down). Only 'click' is draggable (see is_click).
ACTION_CODE = {
    "left_click": ("click", ec.BTN_LEFT),
    "right_click": ("click", ec.BTN_RIGHT),
    "middle_click": ("click", ec.BTN_MIDDLE),
    "double_click": ("double", ec.BTN_LEFT),
    "scroll_up": ("scroll", 1),
    "scroll_down": ("scroll", -1),
    "key_enter": ("key", ec.KEY_ENTER),
    "key_escape": ("key", ec.KEY_ESC),
    "key_tab": ("key", ec.KEY_TAB),
}


class Injector:
    def __init__(self, sw: int, sh: int):
        self.ptr = UInput(
            {ec.EV_KEY: [ec.BTN_LEFT, ec.BTN_RIGHT, ec.BTN_MIDDLE],
             ec.EV_REL: [ec.REL_WHEEL],   # scroll_up/down ride the same pointer device
             ec.EV_ABS: [(ec.ABS_X, AbsInfo(0, 0, sw - 1, 0, 0, 0)),
                         (ec.ABS_Y, AbsInfo(0, 0, sh - 1, 0, 0, 0))]},
            name="gestalt-ptr")
        self.kbd = UInput(
            {ec.EV_KEY: [ec.KEY_ENTER, ec.KEY_ESC, ec.KEY_TAB]},
            name="gestalt-kbd")
        time.sleep(0.3)   # let the devices settle before first event

    def click_at(self, sx: float, sy: float, btn: int):
        self.ptr.write(ec.EV_ABS, ec.ABS_X, int(sx))
        self.ptr.write(ec.EV_ABS, ec.ABS_Y, int(sy))
        self.ptr.syn()
        time.sleep(0.02)
        self.ptr.write(ec.EV_KEY, btn, 1)
        self.ptr.syn()
        time.sleep(0.02)
        self.ptr.write(ec.EV_KEY, btn, 0)
        self.ptr.syn()

    def double_click_at(self, sx: float, sy: float, btn: int):
        # Two full click cycles at the point with a short gap; the gap must clear
        # a compositor's double-click interval yet stay under it so the two land
        # as one double-click (not two separate singles).
        self.click_at(sx, sy, btn)
        time.sleep(0.06)
        self.click_at(sx, sy, btn)

    def scroll_at(self, sx: float, sy: float, amount: int):
        # Warp first so the wheel event lands on whatever is under the gestalt
        # cursor (scroll targets the pointed element, not the last real position).
        self.ptr.write(ec.EV_ABS, ec.ABS_X, int(sx))
        self.ptr.write(ec.EV_ABS, ec.ABS_Y, int(sy))
        self.ptr.syn()
        time.sleep(0.02)
        self.ptr.write(ec.EV_REL, ec.REL_WHEEL, amount)
        self.ptr.syn()

    def tap(self, code: int):
        self.kbd.write(ec.EV_KEY, code, 1)
        self.kbd.syn()
        time.sleep(0.01)
        self.kbd.write(ec.EV_KEY, code, 0)
        self.kbd.syn()

    def fire(self, action: str, x: float, y: float) -> bool:
        """Dispatch a bound action at (x, y). Returns True if it did something."""
        spec = ACTION_CODE.get(action)
        if spec is None:
            return False
        kind, code = spec
        if kind == "click":
            self.click_at(x, y, code)
        elif kind == "double":
            self.double_click_at(x, y, code)
        elif kind == "scroll":
            self.scroll_at(x, y, code)
        else:
            self.tap(code)
        return True

    # ---- hold / drag: split press and release so a held gesture can drag -----
    def move_to(self, x: float, y: float):
        """Warp the real pointer (continuously, to drag while a button is held)."""
        self.ptr.write(ec.EV_ABS, ec.ABS_X, int(x))
        self.ptr.write(ec.EV_ABS, ec.ABS_Y, int(y))
        self.ptr.syn()

    @staticmethod
    def is_click(action: str) -> bool:
        spec = ACTION_CODE.get(action)
        return bool(spec and spec[0] == "click")

    def begin(self, action: str, x: float, y: float) -> bool:
        """Press a button (at x,y) or a key DOWN — the start of a hold/drag."""
        spec = ACTION_CODE.get(action)
        if spec is None:
            return False
        kind, code = spec
        if kind == "click":
            self.move_to(x, y)
            time.sleep(0.01)
            self.ptr.write(ec.EV_KEY, code, 1)
            self.ptr.syn()
        elif kind == "key":
            self.kbd.write(ec.EV_KEY, code, 1)
            self.kbd.syn()
        else:
            return False   # double/scroll aren't holdable; detectors tap them instead
        return True

    def end(self, action: str, x: float, y: float):
        """Release a held button (at x,y) or key UP — the end of a hold/drag."""
        spec = ACTION_CODE.get(action)
        if spec is None:
            return
        kind, code = spec
        if kind == "click":
            self.move_to(x, y)
            self.ptr.write(ec.EV_KEY, code, 0)
            self.ptr.syn()
        elif kind == "key":
            self.kbd.write(ec.EV_KEY, code, 0)
            self.kbd.syn()

    def close(self):
        for dev in (self.ptr, self.kbd):
            try:
                dev.close()
            except Exception:
                pass
