#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Hardware-free fuzz of sanitize_config — the single chokepoint every config load
and socket `set` passes through. Mirrors PhanSpeed's test_validation.py: prove
that arbitrary/hostile input can never produce an out-of-range or malformed
config. Runs in CI; no camera, no display, no venv deps.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gestalt.config import (  # noqa: E402
    _RANGES,
    ACTIONS,
    DEFAULTS,
    FINGERS,
    sanitize_config,
)

GARBAGE = [None, "", "x", -1, 0, 1, 1e9, -1e9, [], {}, True, False, float("nan"),
           float("inf"), "999999", {"nested": 1}, [1, 2, 3], "../etc/passwd"]


def check_invariants(cfg):
    assert set(cfg.keys()) == set(DEFAULTS.keys()), "key set must match DEFAULTS"
    for field, (lo, hi) in _RANGES.items():
        assert lo <= cfg[field] <= hi, f"{field}={cfg[field]} out of [{lo},{hi}]"
    assert cfg["pinch_rearm"] > cfg["pinch_close"], "rearm must exceed close"
    assert isinstance(cfg["armed"], bool)
    assert cfg["camera"] == "auto" or (isinstance(cfg["camera"], int) and cfg["camera"] >= 0)
    for k, v in cfg["bindings"].items():
        assert k.startswith("pinch_") and k[6:] in FINGERS, f"bad binding key {k}"
        assert v in ACTIONS, f"bad action {v}"
    assert cfg["bindings"], "bindings never empty"
    for p in cfg["providers"]:
        assert p in {"atspi", "cv"}, f"unknown provider {p}"
    assert len(cfg["providers"]) == len(set(cfg["providers"])), "providers deduped"


def main():
    rng = random.Random(1)
    # 1. defaults round-trip
    check_invariants(sanitize_config(dict(DEFAULTS)))
    # 2. empty / non-dict input
    for junk in [None, [], "", 42, {"unknown_key": 1}]:
        check_invariants(sanitize_config(junk))
    # 3. fuzz every field with garbage, 8000 cases
    keys = list(DEFAULTS.keys())
    for _ in range(8000):
        raw = {}
        for k in rng.sample(keys, rng.randint(1, len(keys))):
            raw[k] = rng.choice(GARBAGE)
        check_invariants(sanitize_config(raw))
    # 4. malformed bindings / providers specifically
    check_invariants(sanitize_config({"bindings": {"pinch_nose": "nuke", "x": "y"}}))
    check_invariants(sanitize_config({"providers": ["cv", "cv", "atspi", "evil"]}))
    print("test_config: all invariants held across 8000+ cases")


if __name__ == "__main__":
    main()
