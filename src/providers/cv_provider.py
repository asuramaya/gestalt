#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
CV target provider — runs in the gestalt venv (needs cv2/numpy/Xlib). The
fallback for apps that expose NO accessibility tree (the Warp terminal, the
original motivating case).

Two classical (cv2-only, no model) detectors over the active XWayland window:

  * dividers()/panes() — full-span Sobel edge lines → pane centroids. Clean for
    vertical splits (the 9-terminal Warp grid, the motivating case).
  * widgets() — Canny + dilation + contour bounding-rects, filtered to
    clickable-sized rectangles and merged → button / field / link centroids,
    for apps with no accessibility tree (Warp, web canvases, the DJ app).

Both feed the same DynaSpot magnetism. Targets are merged + capped (MAX_TARGETS)
so the magnetism gets a useful handful, not a noisy cloud.

Roadmap (see docs/POINTING.md §CV): swap widgets() for OmniParser-V2's ONNX
interactable-region detector behind this same JSON interface once onnxruntime is
available — the classical pass is the no-dependency stand-in, not the ceiling.
"""
import json
import os
import sys
import time

import numpy as np

try:
    import cv2
    from Xlib import X, display
except Exception as e:  # keep import-safe for CI without a display
    sys.stderr.write(f"[cv] unavailable: {e}\n")
    cv2 = None

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/gestalt-cv.json"
# CV gets its OWN (slower) poll: the full-window capture is expensive and the pane
# grid is static, so it needn't run as often as the cheap AT-SPI walk.
POLL = float(os.environ.get("GESTALT_CV_POLL_MS")
             or os.environ.get("GESTALT_PROVIDER_POLL_MS", "1500")) / 1000.0

# widget-detection filters (window-relative). Tuned to catch clickable boxes
# while rejecting noise; env-overridable for live iteration without an edit.
MIN_PX = int(os.environ.get("GESTALT_CV_MIN_PX", "26"))        # smallest box side
MAX_FRAC = float(os.environ.get("GESTALT_CV_MAX_FRAC", "0.55"))  # largest box / window
MAX_TARGETS = int(os.environ.get("GESTALT_CV_MAX_TARGETS", "20"))  # cap (conservative)
MERGE_PX = int(os.environ.get("GESTALT_CV_MERGE_PX", "22"))    # centroids closer than this fuse
# Process at this width (downscale first): cuts CPU AND collapses the fine text
# edges that hallucinate boxes. Boxes are scaled back to full-res after.
SCALE_W = int(os.environ.get("GESTALT_CV_SCALE_W", "1280"))
# UIED text/non-text split (Chen et al. FSE 2020): a real widget has a fairly
# UNIFORM interior; a text region is a dense edge field. Reject a candidate whose
# interior edge-density exceeds this — that's the text-hallucination filter.
TEXT_DENSITY = float(os.environ.get("GESTALT_CV_TEXT_DENSITY", "0.14"))
# Only capture+detect when the ACTIVE window is one of these (WM_CLASS substring,
# lowercased) — apps with no accessibility tree. Everywhere else CV stays idle so
# it never pays the (expensive) full-window pixel capture. Empty = never capture.
CV_APPS = [a.strip().lower() for a in os.environ.get("GESTALT_CV_APPS", "").split(",")
           if a.strip()]


def app_needs_cv(w) -> bool:
    """True only if the active window's WM_CLASS matches the allowlist — so CV
    skips the expensive capture entirely for apps that have accessibility."""
    if not CV_APPS:
        return False
    try:
        cls = w.get_wm_class()           # (instance, class) or None
    except Exception:
        cls = None
    if not cls:
        return False
    hay = " ".join(c for c in cls if c).lower()
    return any(a in hay for a in CV_APPS)


def active_window(d):
    root = d.screen().root
    try:
        aw = root.get_full_property(d.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType)
        if aw and aw.value:
            w = d.create_resource_object("window", aw.value[0])
            w.get_geometry()
            return w
    except Exception:
        pass
    return None


def capture(w):
    g = w.get_geometry()
    raw = w.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xffffffff)
    img = np.frombuffer(raw.data, np.uint8).reshape(g.height, g.width, 4)
    return img[:, :, :3].copy(), g.width, g.height


def dividers(gray, axis, frac=0.55, grad_thr=22, gap=8):
    if axis == 0:
        g = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
        score = (g > grad_thr).mean(axis=0)
    else:
        g = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
        score = (g > grad_thr).mean(axis=1)
    idx = np.where(score > frac)[0]
    out = []
    if len(idx):
        start = prev = idx[0]
        for i in idx[1:]:
            if i - prev > gap:
                out.append((start + prev) // 2)
                start = i
            prev = i
        out.append((start + prev) // 2)
    return out


def panes(W, H, vdivs, hdivs, min_frac=0.12):
    xs = [0] + [v for v in vdivs if 0 < v < W] + [W]
    ys = [0] + [h for h in hdivs if 0 < h < H] + [H]
    rects = []
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            x0, x1, y0, y1 = xs[i], xs[i + 1], ys[j], ys[j + 1]
            if (x1 - x0) > min_frac * W and (y1 - y0) > min_frac * H:
                rects.append((x0, y0, x1 - x0, y1 - y0))
    return rects


def widgets(gray, W, H):
    """Clickable rectangular regions via Canny + light dilation + contour boxes,
    with a UIED-style text/non-text filter: a candidate is kept only if its
    INTERIOR edge-density is low (a clean widget = uniform fill bounded by edges),
    and rejected if dense (a text block). Returns (x, y, w, h) in window coords."""
    edges = cv2.Canny(gray, 50, 150)
    # light close to bridge anti-aliased border gaps WITHOUT merging text into bands
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    closed = cv2.dilate(edges, k, iterations=1)
    cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    integ = cv2.integral((edges > 0).astype(np.uint8))   # O(1) interior-density lookups
    out = []
    max_w, max_h = MAX_FRAC * W, MAX_FRAC * H
    for c in cnts:
        x, y, w_, h_ = cv2.boundingRect(c)
        if w_ < MIN_PX or h_ < MIN_PX or w_ > max_w or h_ > max_h:
            continue
        ar = w_ / max(h_, 1)
        if ar < 0.15 or ar > 9.0:        # reject rules / scrollbar slivers
            continue
        # interior edge-density (inset 18% to drop the border ring itself)
        mx, my = int(w_ * 0.18), int(h_ * 0.18)
        x0, y0, x1, y1 = x + mx, y + my, x + w_ - mx, y + h_ - my
        if x1 > x0 and y1 > y0:
            s = integ[y1, x1] - integ[y0, x1] - integ[y1, x0] + integ[y0, x0]
            if s / ((x1 - x0) * (y1 - y0)) > TEXT_DENSITY:
                continue                 # dense interior -> text, not a widget
        out.append((x, y, w_, h_))
    return out


def merge(rects):
    """Fuse rects whose centres sit within MERGE_PX (collapses nested/overlapping
    detections to one target), then keep the MAX_TARGETS largest."""
    cents = [(x + w // 2, y + h // 2, w, h, x, y) for (x, y, w, h) in rects]
    cents.sort(key=lambda c: c[2] * c[3], reverse=True)   # largest first
    kept = []
    for cx, cy, w, h, x, y in cents:
        if any(abs(cx - kx) < MERGE_PX and abs(cy - ky) < MERGE_PX for kx, ky, *_ in kept):
            continue
        kept.append((cx, cy, w, h, x, y))
        if len(kept) >= MAX_TARGETS:
            break
    return kept


def write(targets):
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"targets": targets}, f)
    os.replace(tmp, OUT)


def main():
    if cv2 is None:
        write([])
        return
    d = display.Display()
    while True:
        targets = []
        try:
            w = active_window(d)
            if not (w and app_needs_cv(w)):     # not a CV app -> idle, NO capture
                write([])
                time.sleep(POLL)
                continue
            img, W, H = capture(w)
            if img is not None and W >= 64 and H >= 64:    # skip degenerate geometry
                co = d.screen().root.translate_coords(w, 0, 0)
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                # downscale before detection: cheaper, and collapses fine text edges
                s = SCALE_W / W if W > SCALE_W else 1.0
                g = cv2.resize(gray, (int(W * s), int(H * s))) if s < 1.0 else gray
                gw, gh = g.shape[1], g.shape[0]
                inv = 1.0 / s
                vdivs, hdivs = dividers(g, 0), dividers(g, 1)
                # only emit panes when a real split exists (skip the whole-window box)
                for (x, y, w_, h_) in (panes(gw, gh, vdivs, hdivs) if (vdivs or hdivs) else []):
                    x, y, w_, h_ = int(x * inv), int(y * inv), int(w_ * inv), int(h_ * inv)
                    targets.append({"cx": co.x + x + w_ // 2, "cy": co.y + y + h_ // 2,
                                    "x": co.x + x, "y": co.y + y, "w": w_, "h": h_,
                                    "role": "pane", "source": "cv", "name": ""})
                for (cx, cy, w_, h_, x, y) in merge(widgets(g, gw, gh)):
                    cx, cy = int(cx * inv), int(cy * inv)
                    x, y, w_, h_ = int(x * inv), int(y * inv), int(w_ * inv), int(h_ * inv)
                    targets.append({"cx": co.x + cx, "cy": co.y + cy,
                                    "x": co.x + x, "y": co.y + y, "w": w_, "h": h_,
                                    "role": "widget", "source": "cv", "name": ""})
            write(targets)
        except Exception as e:
            write([])
            sys.stderr.write(f"[cv] {e}\n")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
