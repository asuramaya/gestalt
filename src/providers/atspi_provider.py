#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
AT-SPI target provider — runs on SYSTEM python (needs gi/Atspi, absent in the
gestalt CV venv). Walks every app's accessibility tree and streams actionable
elements' screen boxes + centroids to the JSON file passed as argv[1].

The "cheap universal bounding boxes" trick: any app exposing AT-SPI hands us
every widget's screen rect + role for free, no per-app code. Caveat: some apps
(notably the Warp terminal) expose nothing — those need the cv provider.
"""
import json
import os
import sys
import time

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402

Atspi.init()
R = Atspi.Role
ST = Atspi.StateType
# Roles that are click targets by definition. NOTE: ICON is intentionally excluded
# — standalone icons are usually decorative ("random squares"); real icon-buttons
# expose as PUSH_BUTTON. The 'Action' interface would be the ideal gate, but apps
# implement it inconsistently (buttons report no Action), so we filter by role +
# state instead.
ACTION = {R.PUSH_BUTTON, R.LINK, R.MENU_ITEM, R.CHECK_BOX, R.RADIO_BUTTON,
          R.TOGGLE_BUTTON, R.ENTRY, R.LIST_ITEM, R.TABLE_CELL, R.COMBO_BOX,
          R.PAGE_TAB, R.SLIDER, R.SPIN_BUTTON, R.MENU, R.PUSH_BUTTON_MENU}
# text roles only count when actually editable (an input), not static labels
TEXT_ROLES = {R.TEXT, R.PASSWORD_TEXT}

# Actionability rank for the containment dedup. A true click LEAF (a link, a
# button) beats a container that merely WRAPS it (the list-item row around the
# link), so we keep the precise target and drop the redundant outer box. The
# container roles wrap leaves; everything else is a leaf (editable inputs included).
_CONTAINER_ROLES = {"list item", "table cell", "menu"}
RANK_LEAF, RANK_CONTAINER = 3, 2


def _rank(role_name: str) -> int:
    return RANK_CONTAINER if role_name in _CONTAINER_ROLES else RANK_LEAF


def _area(b) -> int:
    return b["w"] * b["h"]


def _frac_inside(small, big) -> float:
    ix = max(0, min(small["x"] + small["w"], big["x"] + big["w"]) - max(small["x"], big["x"]))
    iy = max(0, min(small["y"] + small["h"], big["y"] + big["h"]) - max(small["y"], big["y"]))
    sa = _area(small)
    return (ix * iy) / sa if sa > 0 else 0.0


def dedup_nested(boxes):
    """Drop redundant nested boxes: when one box sits ≥85% inside a larger one,
    keep the higher-actionability box (a link beats the list-item row wrapping it;
    a button beats a label inside it), tie → keep the smaller inner leaf. This
    removes the container+child 'stacked rows' that clutter the overlay and split
    magnetism into overlapping attractors. O(n²) on ≤500 boxes — cheap at poll rate.
    Set GESTALT_ATSPI_DEDUP=0 to disable (see every raw box for debugging)."""
    n = len(boxes)
    alive = [True] * n
    for i in range(n):
        if not alive[i]:
            continue
        for j in range(n):
            if i == j or not alive[i] or not alive[j]:
                continue
            a, b = boxes[i], boxes[j]                 # is b nested inside the larger a?
            if _area(b) < _area(a) and _frac_inside(b, a) > 0.85:
                if _rank(b["role"]) >= _rank(a["role"]):
                    alive[i] = False                  # drop the wrapping container a
                else:
                    alive[j] = False                  # drop the inner label/decoration b
    return [b for b, k in zip(boxes, alive) if k]

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/gestalt-atspi.json"
POLL = float(os.environ.get("GESTALT_PROVIDER_POLL_MS", "300")) / 1000.0
SW, SH = 7680, 4320   # generous screen-coord sanity bounds (8K)
# only box the ACTIVE (foreground) window — else occluded background windows'
# elements get boxed "in random places". gnome-shell chrome is always included
# (top bar / quick settings stay targetable).
ACTIVE_ONLY = os.environ.get("GESTALT_ATSPI_ACTIVE_ONLY", "1") == "1"
DEDUP = os.environ.get("GESTALT_ATSPI_DEDUP", "1") == "1"   # drop nested container boxes
ALWAYS = {"gnome-shell"}


def has_active_frame(app) -> bool:
    try:
        for j in range(app.get_child_count()):
            fr = app.get_child_at_index(j)
            if (fr.get_role() == Atspi.Role.FRAME
                    and fr.get_state_set().contains(Atspi.StateType.ACTIVE)):
                return True
    except Exception:
        pass
    return False


def collect(node, out, depth=0):
    if depth > 16 or len(out) > 500:
        return
    try:
        role = node.get_role()                       # leaf filter only — never gates descent
        ss = node.get_state_set()
        hit = role in ACTION or (role in TEXT_ROLES and ss.contains(ST.EDITABLE))
        if hit and ss.contains(ST.SHOWING):          # on-screen + a click-target role
            e = node.get_extents(Atspi.CoordType.SCREEN)
            # reject zero/negative and clearly container-sized boxes (not a target)
            if 0 < e.width < 2400 and 0 < e.height < 1000 and e.x > -100 and e.y > -100:
                # own try/except: a get_name() failure must never drop an
                # otherwise-good target (geometry+role outweigh the label) —
                # the outer try/except would silently do exactly that.
                try:
                    name = (node.get_name() or "")[:80]
                except Exception:
                    name = ""
                out.append({"cx": e.x + e.width // 2, "cy": e.y + e.height // 2,
                            "x": e.x, "y": e.y, "w": e.width, "h": e.height,
                            "role": node.get_role_name(), "source": "atspi",
                            "name": name})
    except Exception:
        pass
    try:
        for i in range(node.get_child_count()):
            collect(node.get_child_at_index(i), out, depth + 1)
    except Exception:
        pass


def write(targets):
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"targets": targets}, f)
    os.replace(tmp, OUT)


def main():
    while True:
        try:
            d = Atspi.get_desktop(0)
            apps = []
            for i in range(d.get_child_count()):
                try:
                    apps.append(d.get_child_at_index(i))
                except Exception:
                    pass
            active = next((a for a in apps if has_active_frame(a)), None) if ACTIVE_ONLY else None
            targets = []
            for app in apps:
                try:
                    name = app.get_name()
                except Exception:
                    name = ""
                # active-only: just the foreground app + always-on chrome (shell)
                if ACTIVE_ONLY and active is not None and app is not active and name not in ALWAYS:
                    continue
                collect(app, targets)
            if DEDUP:
                targets = dedup_nested(targets)
            write(targets)
        except Exception as e:
            try:
                write([])
                sys.stderr.write(f"[atspi] {e}\n")
            except Exception:
                pass
        time.sleep(POLL)


if __name__ == "__main__":
    main()
