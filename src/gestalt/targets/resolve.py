# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
The shared target resolver — an approximate point (+ optional text hint) in,
the real UI element it means, out.

This is the one primitive underneath what used to be two separately-written
nearest-scans in `pointing/pointer.py` (`_focus_magnetism`'s settle-time
acquire and `_soft_pull`'s legacy candidate search) — and, per docs/POINTING.md
§dual-use, the same primitive an agent-facing `click(x, y)` would resolve
through. Same problem — an approximate point needs resolving to a specific
element — different noise source: physical head-aim tremor for a human,
a vision model's coordinate guess for an agent. `name_hint` only the agent
path is expected to use; the human pointer always calls with `name_hint=None`,
which reproduces prior behaviour exactly (pure nearest-in-radius).
"""
from __future__ import annotations

import math


def resolve_target(x: float, y: float, targets: list[dict], radius: float,
                    name_hint: str | None = None) -> dict | None:
    """Return the target nearest (x, y) within `radius`, or None. If
    `name_hint` is given (case-insensitive, whitespace-trimmed; empty/None
    means "no filter"), only targets whose accessible `name` contains it are
    considered — CV targets never carry a `name`, so they never match a real
    hint. Ties resolve to whichever is scanned first (targets order, stable)."""
    hint = (name_hint or "").strip().lower() or None
    best, nearest = radius, None
    for tg in targets:
        if hint is not None and hint not in (tg.get("name") or "").lower():
            continue
        d = math.hypot(tg["cx"] - x, tg["cy"] - y)
        if d < best:
            best, nearest = d, tg
    return nearest
