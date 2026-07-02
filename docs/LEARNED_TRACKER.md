# Learned tracker (decepticons-based) — design record

The endgame: replace the hand-tuned signal pipeline (1€ cutoffs, dead-zones,
DynaSpot thresholds, torso gains — every magic number) with a small **predictive
coder** that **self-calibrates online**, so tracking gets better the more you use
it. Built on [decepticons](https://github.com/asuramaya/decepticons) (the
predictive-coding kernel) and a chronohorn-style online-training runtime.

## Why decepticons fits

Its mechanisms are the textbook online self-calibrating stack: a **leaky
echo-state reservoir** (fixed random recurrent weights — no expensive training),
**linear/ridge readouts** (trained cheaply, online), a **PredictiveSurprise**
controller (prediction error = the predictive-coding signal), and
**OnlineCausalMemory**. Ecosystem fit: `decepticons` = kernel, `chronohorn` =
runtime (training/tracking), Gestalt = a descendant application.

**The one gap (small):** decepticons is token/byte-oriented today —
`reservoir.step(state, token: int)` injects input as `input_weights[:, token]` (a
column lookup). Head pose is continuous. The continuous variant is a ~one-line
change: `input_weights @ x` for a real vector `x`. A continuous-input substrate is
a legitimate kernel contribution (Gestalt is the descendant that needs it).

## Architecture

```
raw landmarks ─► continuous ESN reservoir (fixed) ─► temporal features h_t
  (fwd vector + shoulders + hand points; RAW, not atan2 features)
       ├─► readout A (self-supervised): predict next landmark vector
       │      → prediction error = SURPRISE = learned intent/noise gate
       │        (replaces 1€ + dead-zones + stillness)
       └─► readout B (supervised, online RLS): h_t → intended screen point
              trained on confirmed-click anchors (centroid = ground truth)
              (layer-4 recalibration, but nonlinear on rich features →
               learns posture/drift compensation automatically)
```

Feeding **raw landmarks** (not hand-derived angles) kills the whole class of
feature bugs like the torso 360° spin — the reservoir learns the geometry.

## Training signals (both already emitted)

- **Dense self-supervised**: the per-frame pose/torso/hand/cursor stream — free,
  every frame. Lets the coder learn to predict/denoise the next pose.
- **Sparse supervised**: each confirmed pinch at a magnetized centroid is a
  `(input, intended-target)` pair — the high-quality label the readout calibrates
  against, with the layer-4 anti-poison gating (pre-pinch stillness + residual <
  diag/12 + bounded update).

## Dataset format (`gestalt/record.py`)

JSONL, one object per frame, under `~/.local/share/gestalt/recordings/
session-<unixstamp>.jsonl`. Toggle: pill "Record (training data)" /
`gestaltctl record on|off`.

```json
{"t": 1781988761.7, "face_ok": true,
 "fwd": [0.163,-0.171,0.972], "pitch": -9.98, "yaw": 9.53,
 "sig": [0.163,-0.172], "spd": 0.00037, "mode": "mouse",
 "cursor": {"raw":[2029,1095], "corr":[2029,1095], "final":[2029,1095], "snap": null},
 "torso": {"roll":0.014, "width":0.383, "mid":[0.503,0.877]},
 "hand": {"thumb":[x,y], "index":[x,y], "pinky":[x,y], "wrist":[x,y], "palm":[x,y]},
 "fire": {"action":"left_click", "finger":"index", "at":[x,y],
          "target":[cx,cy], "role":"push button"}}
```
`torso`/`hand` present only when detected; `fire` present only on confirmed
clicks (the supervised anchor — note `target` = the intended centroid).

## Plan

1. **Log a dataset** (done — this harness). Use Gestalt normally with recording
   on; **pinch-click real UI targets** to generate `fire` anchors — those are the
   supervised labels, so deliberate clicking sessions are the valuable ones.
2. **Continuous substrate** in decepticons (the ~one-line reservoir variant) + a
   smoke test.
3. **Offline prototype**: reservoir + dual readout on the logged data; verify it
   beats the hand-tuned baseline on held-out frames before going live.
4. **Live "learned mode"**: runs alongside the hand-tuned pipeline, selectable,
   observable in diagnostics, with graceful cold-start handover (hand-tuned as the
   prior until the model earns trust). Never rips out what works.

## Label sources for the supervised readout (deferred — still wanted)

**The finding from the first 19k-frame dataset:** 146 clicks, but only **5 carried a
target label**. The user's real apps — Warp, a DJ app, pump.fun, YouTube — expose
no AT-SPI targets, so magnetism had nothing to snap to and produced no
ground-truth centroid. The self-supervised stream is fully fed (19k clean frames);
the **supervised calibration signal is starved**. The supervised route (Readout B)
is still wanted — it needs one of these label sources first:

### Route 1 — CV target provider (the big unlock)
Make `providers/cv_provider.py` actually detect targets in a11y-less apps
(OmniParser-V2 YOLO for widgets; Sobel + OCR for Warp panes — see POINTING.md
§CV). This unblocks **both** magnetism *and* implicit calibration labels in the
user's real workflow simultaneously — every confirmed click in Warp/web then
yields a `(pose, CV-centroid)` label. Highest value, highest effort. The honest
catch: CV centroids are noisier than AT-SPI rects, so the layer-4 consistency gate
matters more.

### Route 2 — calibration mode (the quick bootstrap)
A brief look-and-pinch overlay: show a target at a known screen position, user
aims + pinches, record `(pose, known-point)`; step through a grid (e.g. 3×3 or
5×5) plus randomized points. Minutes → hundreds of *clean, screen-spanning*
labels, app-independent. Does NOT violate the "no explicit calibration" goal — it
**bootstraps** the readout; implicit clicks then sustain/refresh it (exactly the
PACE/EyeO pattern: calibrate once, adapt online forever). Cheapest path to a
usable supervised set. Design: a `gestalt/calibrate/` overlay window + a
`calibrate` control command; reuse the Injector-free overlay; write the same
`fire`-anchor JSONL so the dataset format is unchanged.

### Route 3 — weak self-labels (fallback)
A *successful* click (not immediately undone/re-clicked) weakly implies the cursor
was where intended, so `fire.at` is a noisy label even without a target. Circular
for absolute calibration (the cursor already went there), so low value alone —
but usable as a regularizer alongside Routes 1–2.

**Recommendation when we return to supervised:** Route 2 (calibration mode) to
bootstrap a few hundred clean labels fast, then Route 1 (CV provider) to sustain
implicit labels in real apps. Readout B is otherwise designed and ready (the
layer-4 RLS + anti-poison gating generalizes from raw pose to reservoir features).

## Prototype finding (research/selfsup_prototype.py, 18k frames)

The offline self-supervised prototype (continuous ESN + ridge, on the recorded
runs) **did not validate the self-supervised angle** — and that's a useful result:

- One-step pose prediction: reservoir **−15% vs persistence** (loses).
- Velocity (delta) prediction: reservoir **−8% vs zero-velocity** (loses).
- Multi-step: reservoir **−29% / −50% / −94%** vs persistence at 150/300/600ms —
  the gap *widens* with horizon, the opposite of a model capturing dynamics.
- Surprise → intent: +0.50 corr, 2.5× higher error on fast vs rest — the one
  positive, but **not clearly better than the head-speed we already gate on**.

**Why:** at 20fps the head signal is smooth and persistence-dominated; there's
little forward-predictable structure for a reservoir to exploit. Reservoir
computing wins on signals with rich nonlinear temporal structure — this isn't one.

**Conclusion / redirect:** the learned approach's value was never the
self-supervised denoiser/predictor (1€ + the existing speed gate already handle a
smooth signal fine). It's the **supervised calibration readout** — a nonlinear,
user-specific pose→intended-point map that the hand-tuned *affine* recalibration
(layer 4) can't express. That is label-bound. So the priority is the **label
sources above** (calibration mode → CV provider); once a few hundred clean labels
exist, the real experiment is: does a reservoir-feature (or small MLP) readout
beat the layer-4 affine on held-out clicks? Reservoir features may still earn
their place there — as a *representation for the supervised map*, not as a
forward predictor.

## Honest risks

R&D, not a refactor: cold-start warm-up, online-learning stability (forgetting
factor + anti-poison gating), and the continuous-substrate kernel work. Mitigated
by running alongside the baseline and validating offline first.
