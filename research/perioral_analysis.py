#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Does the mouth out-resolve the head? — analyse a recorded session.

For the head signal and every perioral landmark/axis we compute, unit-free:
  * NOISE FLOOR  — the quietest the signal gets (5th-pct of 0.5s-window stds);
                   how still it holds = the precision limit.
  * RANGE        — robust span (5th..95th pct) you actually moved it over.
  * LEVELS       — range / noise = number of distinguishable positions.
  * BITS         — log2(levels) = effective resolution.

More BITS on an axis = finer controllable pointing on that axis. The hypothesis
is the mouth wins the *vertical* and *fine* case despite its narrow range, because
its noise floor is far lower than the neck's tremor. We also report the
lateral/vertical asymmetry (the broad-sideways / narrow-up-down anisotropy).

Usage:  python3 research/perioral_analysis.py [session.jsonl]
        (defaults to the newest recording)
"""
from __future__ import annotations

import glob
import json
import math
import os
import sys

WIN = 0.5   # seconds per window for the noise-floor estimate


def _newest():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    files = glob.glob(os.path.join(base, "gestalt", "recordings", "*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def _pct(xs, p):
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, int(p * (len(s) - 1))))]


def _std(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _metrics(times, vals):
    """noise floor, range, levels, bits for one scalar series."""
    if len(vals) < 10:
        return None
    # noise floor: 5th-pct of per-window stds (the quietest holds)
    stds, i = [], 0
    while i < len(times):
        j = i
        while j < len(times) and times[j] - times[i] < WIN:
            j += 1
        if j - i >= 4:
            stds.append(_std(vals[i:j]))
        i = j
    noise = _pct(stds, 0.05) if stds else _std(vals)
    noise = max(noise, 1e-6)
    rng = _pct(vals, 0.95) - _pct(vals, 0.05)
    levels = rng / noise
    return {"noise": noise, "range": rng, "levels": levels,
            "bits": math.log2(levels) if levels > 1 else 0.0}


def _series(rows, getter):
    ts, vs = [], []
    for r in rows:
        v = getter(r)
        if v is not None:
            ts.append(r["t"])
            vs.append(v)
    return ts, vs


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else _newest()
    if not path or not os.path.exists(path):
        sys.exit("no recording found — record a session first (gestaltctl record on/off)")
    rows = [json.loads(line) for line in open(path) if line.strip()]
    rows = [r for r in rows if r.get("face_ok")]
    print(f"== {os.path.basename(path)} — {len(rows)} face frames ==\n")

    # candidate signals: (label, axis getter)
    chans = [
        ("HEAD yaw (h)", lambda r: r["sig"][0]),
        ("HEAD pitch(v)", lambda r: r["sig"][1]),
    ]
    def mouth(name, axis):
        return lambda r, n=name, a=axis: r["mouth"][n][a] if "mouth" in r else None
    for name in ("ulip_out", "ulip_in", "corner_l", "corner_r", "nose_tip"):
        chans.append((f"{name} (h)", mouth(name, 0)))
        chans.append((f"{name} (v)", mouth(name, 1)))

    print(f"{'signal':16s} {'noise':>10s} {'range':>10s} {'levels':>8s} {'bits':>6s}")
    print("-" * 54)
    for label, get in chans:
        ts, vs = _series(rows, get)
        m = _metrics(ts, vs)
        if m:
            print(f"{label:16s} {m['noise']:10.5f} {m['range']:10.4f} "
                  f"{m['levels']:8.1f} {m['bits']:6.1f}")
    print("\nMore BITS = finer controllable pointing on that axis.")
    print("Compare each mouth axis' bits to HEAD yaw/pitch — the mouth 'wins' where")
    print("its bits exceed the head's (esp. expected on the fine/vertical case).")


if __name__ == "__main__":
    main()
