# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Target registry — spawns the configured providers as subprocesses and merges
their streamed boxes into one deduped target list the pointer magnetizes to.

Each provider runs on the interpreter it needs (atspi on system python for
gi/Atspi; cv in this venv for cv2) and writes to its own JSON file under
$XDG_RUNTIME_DIR/gestalt/targets/<name>.json. See providers/README.md.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

# providers/ sits next to the gestalt/ package (PREFIX/providers, repo/providers).
_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROVIDERS_DIR = os.path.join(os.path.dirname(_PKG), "providers")

# how to launch each provider: (interpreter, script). atspi needs SYSTEM python
# (gi/Atspi absent from the CV venv); cv runs in this venv (sys.executable).
LAUNCH = {
    "atspi": ("/usr/bin/python3", "atspi_provider.py"),
    "cv": (sys.executable, "cv_provider.py"),
}


def _targets_dir() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    d = os.path.join(base, "gestalt", "targets")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _inside_any(cx, cy, boxes, margin: int = 8) -> bool:
    """Is (cx, cy) within any (x, y, w, h) box (+ margin)?"""
    for x, y, w, h in boxes:
        if x - margin <= cx <= x + w + margin and y - margin <= cy <= y + h + margin:
            return True
    return False


def merge_provider_files(files: dict[str, str]) -> list[dict]:
    """Merge every provider's current boxes from their JSON files. AT-SPI is
    AUTHORITATIVE: it gives exact, stable, semantic rects from the app's own
    widget tree, so a CV box (a pixel guess) landing inside an AT-SPI box is
    dropped — CV only survives where AT-SPI is silent (Warp panes, canvases).
    This is what kills CV's jitter/hallucinations wherever accessibility
    actually speaks.

    Pulled out of Registry.read() (2026-07) so a SEPARATE process — the MCP
    server, which must never spawn its own competing atspi/cv subprocesses —
    can read the SAME live files the running gestaltd already maintains,
    rather than re-polling AT-SPI a second time from a second process."""
    merged: list[dict] = []
    seen: set[tuple[int, int]] = set()
    atspi_boxes: list[tuple] = []
    # process AT-SPI first so its boxes are known when we vet CV boxes
    names = sorted(files, key=lambda n: 0 if n == "atspi" else 1)
    for name in names:
        try:
            with open(files[name]) as f:
                items = json.load(f).get("targets", [])
        except (OSError, ValueError):
            continue
        for tg in items:
            try:
                cx, cy = tg["cx"], tg["cy"]
                key = (round(cx / 12), round(cy / 12))   # ~12px grid
            except (KeyError, TypeError):
                continue
            if key in seen:
                continue
            if tg.get("source") == "cv" and _inside_any(cx, cy, atspi_boxes):
                continue                             # AT-SPI already owns this spot
            seen.add(key)
            merged.append(tg)
            if name == "atspi":
                try:
                    atspi_boxes.append((tg["x"], tg["y"], tg["w"], tg["h"]))
                except (KeyError, TypeError):
                    pass
    return merged


class Registry:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._procs: dict[str, subprocess.Popen] = {}
        self._files: dict[str, str] = {}

    def start(self):
        tdir = _targets_dir()
        active_only = "1" if self.cfg.get("atspi_active_only", True) else "0"
        env = dict(os.environ, GESTALT_PROVIDER_POLL_MS=str(self.cfg["provider_poll_ms"]),
                   GESTALT_CV_POLL_MS=str(self.cfg.get("cv_poll_ms", 1500)),
                   GESTALT_CV_APPS=",".join(self.cfg.get("cv_apps", [])),
                   GESTALT_ATSPI_ACTIVE_ONLY=active_only)
        for name in self.cfg["providers"]:
            spec = LAUNCH.get(name)
            if not spec:
                continue
            interp, script = spec
            out = os.path.join(tdir, f"{name}.json")
            self._files[name] = out
            try:
                self._procs[name] = subprocess.Popen(
                    [interp, os.path.join(PROVIDERS_DIR, script), out],
                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                sys.stderr.write(f"[targets] provider {name} failed to start: {e}\n")

    def read(self) -> list[dict]:
        """Merge every provider's current boxes (see merge_provider_files)."""
        return merge_provider_files(self._files)

    def apply_config(self, cfg: dict):
        # provider set changes need a restart; the daemon handles that on `set`.
        self.cfg = cfg

    def pause(self):
        """SIGSTOP the providers. Targets are only consumed while armed, yet
        every poll is a synchronous D-Bus round trip that taxes gnome-shell and
        the focused app (atspi alone ~27% CPU 24/7) — freeze the processes
        outright while disarmed instead of letting them spin."""
        for p in self._procs.values():
            try:
                p.send_signal(signal.SIGSTOP)
            except Exception:
                pass

    def resume(self):
        for p in self._procs.values():
            try:
                p.send_signal(signal.SIGCONT)
            except Exception:
                pass

    def close(self):
        for p in self._procs.values():
            try:
                # a SIGSTOPped process can't handle SIGTERM until continued —
                # thaw first, then terminate, then REAP (terminate() alone
                # leaves zombies) with a bounded wait + kill escalation.
                p.send_signal(signal.SIGCONT)
                p.terminate()
                p.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                try:
                    p.kill()
                    p.wait(timeout=1.5)
                except Exception:
                    pass
            except Exception:
                pass
        self._procs.clear()
