#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
gestalt-mcp — the agent-facing half of Gestalt's dual-use design (see
docs/TARGETS.md §the name field + shared resolver). Exposes the SAME target
perception (AT-SPI/CV, merged) and actuation (uinput) substrate the human
head-pointer uses, as MCP tools, for an agent driving the desktop instead of
a head.

Deliberately separate from `gestaltd`: this process never spawns its own
atspi/cv provider subprocesses (that would double-poll AT-SPI) — it reads the
SAME live target files the running daemon already maintains via
`gestalt.targets.registry.merge_provider_files`. It never imports the
pointing/ pipeline (1€, comfort, magnetism, KTM) — an agent's intent is
already discrete; none of that continuous-noisy-human-motion machinery
applies (see the design discussion this module grew out of).

SAFETY, read this before pointing GESTALT_MCP_ALLOWLIST at anything:
  * list_targets / resolve / active_window are READ-ONLY — always safe.
  * click / scroll / drag actually inject real input via uinput — same
    kernel mechanism the human pointer uses, indistinguishable from a real
    device, with NO sandbox boundary. They refuse to run unless the
    CURRENTLY ACTIVE window's wm_class matches GESTALT_MCP_ALLOWLIST (a
    comma-separated list of substrings — unset/empty = refuse everywhere,
    secure by default). This is a coarse, first gate, not a complete safety
    story: it does not stop an agent clicking somewhere destructive WITHIN
    an allowed app. Prefer running this against a scoped/sandboxed session,
    not a daily-driver desktop, for anything autonomous.
  * There is deliberately NO type_text() yet. Gestalt's uinput keyboard
    device only registers KEY_ENTER/ESC/TAB (see gesture/inject.py) — the
    human pointer never needed general text entry. Arbitrary text injection
    needs its own char->keycode table and its own safety pass (it is the
    single riskiest capability this module could add — typing into the
    wrong focused window is much harder to undo than a click); out of scope
    for this pass on purpose, not an oversight.
"""
from __future__ import annotations

import os
import sys

# Allow running straight from the source tree (mcp/ next to gestalt/) — same
# pattern as bin/gestaltd, so this works regardless of invocation cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from gestalt.targets.registry import merge_provider_files  # noqa: E402
from gestalt.targets.resolve import resolve_target  # noqa: E402

mcp = FastMCP("gestalt")


# ---- perception (read-only) ------------------------------------------------

def _targets_dir() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(base, "gestalt", "targets")


def list_targets_impl(name_hint: str = "") -> list[dict]:
    """Read the live merged target list (whatever gestaltd's providers are
    currently producing) — no side effects, no subprocess spawned here."""
    d = _targets_dir()
    if not os.path.isdir(d):
        return []
    files = {os.path.splitext(f)[0]: os.path.join(d, f)
             for f in os.listdir(d) if f.endswith(".json")}
    targets = merge_provider_files(files)
    hint = (name_hint or "").strip().lower()
    if hint:
        targets = [t for t in targets if hint in (t.get("name") or "").lower()]
    return targets


def active_window_impl() -> dict | None:
    """The currently focused window's wm_class + title — context for an agent
    deciding what it's about to act on, and what the allowlist gate checks."""
    from Xlib import X, display
    d = display.Display()
    try:
        root = d.screen().root
        aw = root.get_full_property(d.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType)
        if not (aw and aw.value):
            return None
        w = d.create_resource_object("window", aw.value[0])
        try:
            cls = list(w.get_wm_class() or ())
        except Exception:
            cls = []
        try:
            name = w.get_wm_name()
        except Exception:
            name = None
        return {"wm_class": cls, "wm_name": name}
    finally:
        d.close()


def resolve_impl(x: float, y: float, radius: float = 90.0,
                  name_hint: str = "") -> dict | None:
    """Dry-run target resolution — no injection, just resolve_target() against
    the live list. Lets a caller check what a click(...) WOULD hit first."""
    return resolve_target(x, y, list_targets_impl(), radius, name_hint or None)


@mcp.tool()
def list_targets(name_hint: str = "") -> list[dict]:
    """List currently visible, actionable UI targets (buttons, links, entries,
    menus, …). Each has cx/cy (centroid), x/y/w/h (bounds), role, source
    (atspi|cv), and name (accessible label; empty string if the app didn't
    expose one — see docs/TARGETS.md for why some targets have no name).
    Pass name_hint to filter to targets whose name contains it
    (case-insensitive substring). Read-only."""
    return list_targets_impl(name_hint)


@mcp.tool()
def active_window() -> dict | None:
    """The currently focused window's wm_class and title. Read-only."""
    return active_window_impl()


@mcp.tool()
def resolve(x: float, y: float, radius: float = 90.0, name_hint: str = "") -> dict | None:
    """Resolve an approximate point (+ optional name hint) to the real target
    it means, or null if nothing qualifies within radius px. Same primitive
    the human pointer's magnetism uses (gestalt/targets/resolve.py) — read-only,
    does not click anything."""
    return resolve_impl(x, y, radius, name_hint)


# ---- actuation (injects real input — allowlist-gated) ----------------------

def _allowlist() -> list[str]:
    raw = os.environ.get("GESTALT_MCP_ALLOWLIST", "")
    return [a.strip().lower() for a in raw.split(",") if a.strip()]


def _check_allowed() -> None:
    """Refuse to inject unless the ACTIVE window matches the allowlist.
    Secure by default: an empty/unset allowlist refuses EVERYWHERE."""
    allow = _allowlist()
    if not allow:
        raise PermissionError(
            "GESTALT_MCP_ALLOWLIST is empty — refusing to inject input anywhere "
            "(secure-by-default). Set it to a comma-separated list of wm_class "
            "substrings (e.g. 'gnome-shell,gedit') to permit injection there.")
    win = active_window_impl()
    cls = " ".join(win.get("wm_class") or []).lower() if win else ""
    if not any(a in cls for a in allow):
        raise PermissionError(
            f"active window ({cls or 'unknown'!r}) is not in GESTALT_MCP_ALLOWLIST "
            f"{allow} — refusing to inject input. (list_targets/resolve/"
            f"active_window remain available; they never inject anything.)")


_injector = None


def _get_injector():
    """Lazy: a real uinput device is only opened on the first actual
    injection call, never for a read-only tool."""
    global _injector
    if _injector is None:
        from Xlib import display

        from gestalt.gesture import Injector
        d = display.Display()
        scr = d.screen()
        vw, vh = scr.width_in_pixels, scr.height_in_pixels
        d.close()
        _injector = Injector(vw, vh)
    return _injector


@mcp.tool()
def click(x: float | None = None, y: float | None = None, name_hint: str = "",
          radius: float = 90.0, button: str = "left") -> dict:
    """Click a target: give (x, y) for a raw point, or name_hint to resolve by
    label (e.g. name_hint="Export"), or both (name_hint narrows candidates
    near (x, y)). Resolves via the SAME resolve_target the human pointer's
    magnetism uses, then injects a real click via uinput. button: left|right|
    middle|double. REQUIRES the active window to match GESTALT_MCP_ALLOWLIST —
    raises PermissionError otherwise. Raises ValueError if nothing resolves."""
    _check_allowed()
    if x is None and y is None and not name_hint:
        raise ValueError("give (x, y), name_hint, or both")
    if x is not None and y is not None:
        tg = resolve_impl(x, y, radius=radius, name_hint=name_hint) if name_hint else None
        if name_hint and tg is None:
            raise ValueError(f"no target matching name_hint={name_hint!r} "
                              f"within {radius}px of ({x}, {y})")
        px, py = (tg["cx"], tg["cy"]) if tg is not None else (x, y)
    else:
        # name_hint only, no rough point to anchor a distance resolve — match
        # by name directly. Ambiguity is a loud error, never a silent guess.
        candidates = list_targets_impl(name_hint)
        if not candidates:
            raise ValueError(f"no target matches name_hint={name_hint!r}")
        if len(candidates) > 1:
            raise ValueError(
                f"{len(candidates)} targets match name_hint={name_hint!r} "
                f"(ambiguous) — narrow the hint or provide (x, y)")
        px, py = candidates[0]["cx"], candidates[0]["cy"]
    code = {"left": "left_click", "right": "right_click",
            "middle": "middle_click", "double": "double_click"}.get(button)
    if code is None:
        raise ValueError(f"unknown button {button!r} (left|right|middle|double)")
    inj = _get_injector()
    inj.fire(code, px, py)
    return {"clicked": [px, py], "button": button}


@mcp.tool()
def scroll(x: float, y: float, amount: int) -> dict:
    """Scroll at (x, y). amount: +1 up-ish / -1 down-ish per the injector's
    convention (gesture/inject.py); pass a larger magnitude for more scroll.
    REQUIRES the active window to match GESTALT_MCP_ALLOWLIST."""
    _check_allowed()
    inj = _get_injector()
    inj.scroll_at(x, y, amount)
    return {"scrolled": [x, y], "amount": amount}


@mcp.tool()
def drag(x1: float, y1: float, x2: float, y2: float, button: str = "left") -> dict:
    """Press at (x1,y1), move to (x2,y2), release — a drag. REQUIRES the
    active window to match GESTALT_MCP_ALLOWLIST."""
    _check_allowed()
    code = {"left": "left_click", "right": "right_click",
            "middle": "middle_click"}.get(button)
    if code is None:
        raise ValueError(f"unknown button {button!r} (left|right|middle)")
    inj = _get_injector()
    inj.begin(code, x1, y1)
    inj.move_to(x2, y2)
    inj.end(code, x2, y2)
    return {"dragged": [[x1, y1], [x2, y2]], "button": button}


if __name__ == "__main__":
    mcp.run()
