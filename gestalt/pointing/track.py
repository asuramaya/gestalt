# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Temporal target tracking — the anti-jiggle layer (see docs/POINTING.md §CV).

AT-SPI hands the same widget rectangle every frame; pixel-derived CV centroids do
not — Canny+contour boxes shift a few px, split, merge, and pop in and out every
detection. Magnetizing to that vibrating point cloud is exactly why the cursor
jiggles. This is the classic multi-object-tracking fix: associate each new
detection to a persistent track (nearest within a gate), smooth the track's
position (EMA), debounce appearance (a track must be seen `min_hits` reads before
it counts) and disappearance (keep it `max_miss` missed reads before culling).

The result is a small set of stable, smoothly-moving, persistent targets with
durable IDs — which is what lets the focus state machine *commit* to one without
flip-flopping. No model: detection quality is a separate (perception) problem;
this only fixes the temporal instability of whatever boxes it's given.
"""
from __future__ import annotations

import math


class TargetTracker:
    def __init__(self, cfg: dict):
        self.apply_config(cfg)
        self._tracks: list[dict] = []
        self._next_id = 1

    def apply_config(self, cfg: dict):
        self.enabled = cfg["target_track"]
        self.gate = cfg["target_assoc_px"]
        self.alpha = cfg["target_pos_alpha"]
        self.min_hits = int(cfg["target_min_hits"])
        self.max_miss = int(cfg["target_max_miss"])

    def reset(self):
        self._tracks = []

    def update(self, raw: list[dict]) -> list[dict]:
        """Associate the latest detections to persistent tracks and return the
        confirmed, smoothed targets (each carrying a stable `id`)."""
        if not self.enabled:
            # passthrough: synthesise per-frame ids so the focus SM still works
            return [dict(t, id=f"raw{i}") for i, t in enumerate(raw)]

        used = [False] * len(raw)
        # 1. update existing tracks from their nearest unclaimed detection
        for tr in self._tracks:
            best, bi = self.gate, -1
            for i, r in enumerate(raw):
                if used[i]:
                    continue
                d = math.hypot(r["cx"] - tr["cx"], r["cy"] - tr["cy"])
                if d < best:
                    best, bi = d, i
            if bi >= 0:
                r = raw[bi]
                used[bi] = True
                a = self.alpha
                tr["cx"] += a * (r["cx"] - tr["cx"])
                tr["cy"] += a * (r["cy"] - tr["cy"])
                tr["w"] += a * (float(r.get("w", tr["w"])) - tr["w"])
                tr["h"] += a * (float(r.get("h", tr["h"])) - tr["h"])
                tr["role"] = r.get("role", tr["role"])
                tr["source"] = r.get("source", tr["source"])
                tr["hits"] += 1
                tr["miss"] = 0
            else:
                tr["miss"] += 1

        # 2. spawn tracks for unclaimed detections
        for i, r in enumerate(raw):
            if used[i]:
                continue
            self._tracks.append({
                "id": self._next_id, "cx": float(r["cx"]), "cy": float(r["cy"]),
                "w": float(r.get("w", 40)), "h": float(r.get("h", 40)),
                "role": r.get("role"), "source": r.get("source"),
                "hits": 1, "miss": 0,
            })
            self._next_id += 1

        # 3. cull long-missing tracks
        self._tracks = [t for t in self._tracks if t["miss"] <= self.max_miss]

        # 4. emit only confirmed tracks (appearance debounce)
        return [{"id": t["id"], "cx": t["cx"], "cy": t["cy"], "w": t["w"], "h": t["h"],
                 "role": t["role"], "source": t["source"]}
                for t in self._tracks if t["hits"] >= self.min_hits]

    def state(self) -> dict:
        return {"tracks": len(self._tracks)}
