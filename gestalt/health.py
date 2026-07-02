# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Health state model — the single source of truth for "is it working right now".

One derived state enum feeds all three observability tiers (top-bar HUD glance,
pill detail, diagnostics window). Pure stdlib so the daemon derives it without
the CV stack; the extension mirrors the same state->colour map in GJS.
"""
from __future__ import annotations

# state -> (label, severity). severity drives the HUD colour in the extension:
#   ok = green, warn = amber, bad = red, idle = dimmed.
STATES = {
    "off": ("Disarmed", "idle"),
    "starting": ("Starting", "warn"),
    "no_engine": ("No camera/CV", "bad"),
    "searching": ("Looking for you", "warn"),
    "tracking": ("Tracking", "ok"),
    "degraded": ("Head too high/low", "warn"),   # past the pitch limit — pose unreliable
    "lost": ("Face lost", "bad"),
}


def derive_state(*, armed: bool, engine_ok: bool, face_ok: bool,
                 over_pitch: bool, ever_tracked: bool) -> str:
    """Collapse the raw signals into one state name (key of STATES)."""
    if not engine_ok:
        return "no_engine"
    if not armed:
        return "off"
    if face_ok:
        return "degraded" if over_pitch else "tracking"
    # armed, engine running, but no face this frame
    return "lost" if ever_tracked else "searching"


def severity(state: str) -> str:
    return STATES.get(state, ("", "idle"))[1]


def label(state: str) -> str:
    return STATES.get(state, (state, "idle"))[0]
