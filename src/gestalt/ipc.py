# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
The daemon <-> pill contract: a status JSON snapshot the pill polls, and the
paths for the line-delimited JSON control socket sutra.ControlServer serves
(src/bin/gestaltd wires it up — see src/share/gestalt/lib/sutra.py, vendored
per sutra/docs/BOOTSTRAP.md).

Unlike PhanSpeed (root daemon, world-reachable socket, SO_PEERCRED auth),
gestaltd runs as the user inside the graphical session, so the socket only ever
faces its owner (sutra.allow_uids({os.getuid()})). Both files live under
$XDG_RUNTIME_DIR/gestalt (tmpfs, 0700, owned by the user) — no privilege
boundary to defend, just a clean IPC seam.
"""
from __future__ import annotations

import json
import os


def runtime_dir() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    d = os.path.join(base, "gestalt")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def status_path() -> str:
    return os.path.join(runtime_dir(), "status.json")


def socket_path() -> str:
    return os.path.join(runtime_dir(), "control.sock")


def write_status(snapshot: dict | str) -> None:
    """Atomically publish the status snapshot (write-temp-then-rename). Accepts
    a pre-serialized str so the daemon can compare payloads and skip no-op
    rewrites — status.json is polled at ~1Hz; don't churn it at loop rate."""
    path = status_path()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        if isinstance(snapshot, str):
            f.write(snapshot)
        else:
            json.dump(snapshot, f)
    os.replace(tmp, path)
