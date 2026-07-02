# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
The daemon <-> pill contract: a status JSON snapshot the pill polls, and a
line-delimited JSON control socket the pill writes.

Unlike PhanSpeed (root daemon, world-reachable socket, SO_PEERCRED auth),
gestaltd runs as the user inside the graphical session, so the socket only ever
faces its owner. Both files live under $XDG_RUNTIME_DIR/gestalt (tmpfs, 0700,
owned by the user) — no privilege boundary to defend, just a clean IPC seam.
"""
from __future__ import annotations

import json
import os
import socket
import threading


def runtime_dir() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    d = os.path.join(base, "gestalt")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def status_path() -> str:
    return os.path.join(runtime_dir(), "status.json")


def socket_path() -> str:
    return os.path.join(runtime_dir(), "control.sock")


def write_status(snapshot: dict) -> None:
    """Atomically publish the status snapshot (write-temp-then-rename)."""
    path = status_path()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(snapshot, f)
    os.replace(tmp, path)


class ControlServer(threading.Thread):
    """Line-delimited JSON command server. `handler(cmd: dict) -> dict` reply."""

    daemon = True

    def __init__(self, handler):
        super().__init__(name="gestalt-control")
        self._handler = handler
        self._sock = None

    def run(self):
        path = socket_path()
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(path)
        os.chmod(path, 0o600)
        self._sock.listen(8)
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with conn:
            data = conn.recv(64 * 1024)  # size-capped, same as PhanSpeed
            if not data:
                return
            try:
                cmd = json.loads(data.decode("utf-8").splitlines()[0])
                reply = self._handler(cmd) or {"ok": True}
            except Exception as e:  # never crash the daemon on bad input
                reply = {"ok": False, "error": str(e)}
            try:
                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
            except OSError:
                pass
