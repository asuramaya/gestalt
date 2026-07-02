# The pointing model (and its research basis)

Gestalt's cursor isn't tuned by feel — every stage is a published HCI technique,
chosen to fix a specific failure of naïve head-pointing. This doc is the map from
*symptom* → *technique* → *paper* → *the constant that controls it* (all in
`gestalt/config.py`). The guiding insight from the literature: **separate target
acquisition (magnetism + commit) from the input signal (head tracking)** — they
fail independently and are fixed independently.

## The pipeline

```
head pose ─► 1€ filter ─► DynaSpot speed-scaled catch-radius ─► velocity-gated pull
          ─► KTM arrival predict ─► Steady-Clicks commit gate (on pinch)
```

## 1. Jitter at rest → **1€ filter**

*Casiez, Roussel, Vogel — "1€ Filter: A Simple Speed-based Low-pass Filter for
Noisy Input in Interactive Systems," CHI 2012.* Adaptive low-pass whose cutoff
rises with speed: low speed → low cutoff (kills resting jitter, which people only
notice when still); high speed → high cutoff (kills lag, which people only notice
while moving). Two params only.
→ `oneeuro_mincutoff` (raise to kill rest jitter), `oneeuro_beta` (raise to cut
fast-move lag). Tune `mincutoff` first with `beta=0`, then raise `beta`.

## 2. "Very difficult to lock onto anything" → **DynaSpot magnetism**

*Chapuis, Labrune, Pietriga — "DynaSpot: Speed-Dependent Area Cursor," CHI 2009.*
A catch-radius coupled to **cursor speed**, not target geometry: fast ballistic
motion → wide catch (reach), near-stationary → collapses to a point (precision +
lets you park between targets, no mode switch). Among captured targets, pick
nearest-to-center. Chosen over **Bubble Cursor** (Grossman & Balakrishnan, CHI
2005) because Bubble needs a global Voronoi tessellation of *all* targets and
**degrades to a plain point cursor in dense UI** (packed toolbars, terminal
panes) — exactly our case.
→ `dynaspot_min_speed`, `dynaspot_max_radius`.

## 3. The cursor gets "grabbed" by the wrong target → **velocity-gated soft pull**

The universal failure across every magnetism paper (sticky icons, gravity wells,
semantic pointing) is the **distractor**: a target *between* you and your goal
captures the cursor. The proven fix is to engage the pull **only on the slow
terminal approach** — *Worden et al., "Making Computers Easier for Older Adults,"
CHI 1997* (stickiness only below ~30% of peak velocity) — and to keep attraction
**below the perceptibility threshold** — *Bateman, Mandryk et al., CHI 2011* — so
it feels like the cursor settling, not fighting you. Implemented as a soft pull
(local C-D gain reduction), never a hard snap.
→ `snap_pull`, `snap_velocity_gate`.

## 4. "The lock is sluggish / the break is brutal" → drop the lock; predict arrival with **KTM**

The prototype's settle-lock is a dwell timer in disguise, and dwell has no good
setting — the **Midas Touch problem** (*Jacob, "What You Look At Is What You Get,"
CHI 1990*): fire too early and it fights fine-aiming, too late and it's sluggish.
We don't need it because confirmation lives on a separate channel (the pinch).
Instead of *waiting* for the user to stop, *predict* the endpoint from the motion
shape: **Kinematic Template Matching** (*Pasqual & Wobbrock, CHI 2014*; head-coupled
variant, *CHI 2020*) reads the velocity profile and predicts the target within
~39px at 90% of the movement — responsiveness of a snap without the sluggishness
of dwell. The head therefore stays **free at all times**; there is no lock to
break out of.

### KTM endpoint → target posterior (BUILT 2026-07) — `pointing/endpoint.py`

The predictor half of KTM, built as the eye's replacement after §VERDICT: in
eye-head coordination the eyes reach the target ~200ms before the head, but the
same before-arrival knowledge is carried by the head's own velocity profile. An
aimed reach is ballistic — a stereotyped bell profile — so once deceleration
begins, the **minimum-jerk closed form** (*Flash & Hogan 1985*; kinematic
endpoint extrapolation per *Lank, Cheng & Ruiz, GI 2007*) turns the decel-side
speed ratio directly into "distance still to travel": v/v_peak = 16·t²(1−t)²
and s/D = 10t³−15t⁴+6t⁵ — two lines of algebra, no per-user template library.
The predicted point is fused with the AT-SPI targets into a posterior,

    P(target) ∝ exp(−d²/2σ²) · (1 + w·click_history)

with σ ∝ the estimated remaining distance (uncertainty shrinks exactly when
early commitment is still worth something), and the winner — only if it beats
the runner-up by `endpoint_confidence`× AND lies ahead of the motion — is
handed to the focus state machine as a **pre-acquisition**: focus commits
mid-flight instead of waiting for arrival. Only the light `focus_pull_move`
applies while moving, so a wrong prediction is a faint tug the break radius
releases; the hard snap still waits for genuine arrival. FAIL-SAFE: every
guard failure (no ballistic peak, ambiguous posterior, target behind the
motion) degrades to plain settle-time acquisition — the pre-VERDICT behavior.
→ `endpoint_predict` (on), `endpoint_min_peak_pxs`, `endpoint_decel_ratio`,
`endpoint_sigma_frac`/`_sigma_min_px`, `endpoint_confidence`, `endpoint_gate_px`,
`endpoint_history_w`. Observability: `status().endpoint` (predicted point,
remaining px, intent role, confidence), the diag `endpoint` row, and the
recorder logs the per-frame prediction stream so it can be scored offline
against click anchors (the eval harness + its ~800px static-affine baseline
are from the 2026-07 experiment). Tested hardware-free in
`tests/test_endpoint.py` against synthetic minimum-jerk reaches.

## 5. "The click lands wrong — my head jumps during the pinch" → **Steady Clicks**

*Trewin, Keates, Moffatt — "Developing Steady Clicks," ASSETS 2006.* At
pinch-down, **freeze the cursor position** until release so the head jerk that
accompanies the pinch can't slip the cursor off-target, and **reject the click if
head angular velocity is above a threshold** (you're still moving, not aiming).
The direct fix for the prototype's "1-in-5 clicks land" problem.
→ `commit_freeze_ms`, `commit_velocity_gate`.

## Confirmation channel → **Gaze + Pinch / MAGIC**

*Pfeuffer et al., "Gaze + Pinch Interaction in VR," SUI 2017* (the academic root
of Apple Vision Pro) and *Zhai, Morimoto, Ihde, "MAGIC Pointing," CHI 1999*:
noisy modality does coarse pointing, a discrete modality (the pinch) confirms.
Never use dwell-to-click when you have a second channel. Latch the target at
pinch-*onset* and hold through completion.

## The CV target layer (the Warp gap)

Magnetism needs target centroids. AT-SPI gives them free for most apps; the
**Warp terminal exposes none**, so we need a pixel fallback. The literature is
unanimous that the winning design is a **hybrid, never one model**: *Chen et al.,
"Object Detection for GUI: Old Fashioned or Deep Learning or a Combination?" FSE
2020* (UIED); *Apple, "Screen Recognition," CHI 2021* (on-device MobileNet-SSD,
71% mAP, real-time); *Microsoft OmniParser V2, 2024*.

`providers/cv_provider.py` — **classical (cv2-only) detectors, BUILT** (the venv
has only cv2 + Xlib; torch/ultralytics/onnxruntime/OCR are NOT installed, and
YOLO on the Intel Xe would run at ~0.5fps and break live iteration):
- **Panes** (Warp grid): full-span Sobel vertical/horizontal divider projection →
  pane centroids. Only emitted when a real split exists (no whole-window box).
- **Widgets** (apps with no a11y): `Canny → horizontal-biased dilate → contour
  bounding-rects`, filtered by size (`MIN_PX`..`MAX_FRAC`·window) and aspect
  (rejects rules/scrollbars), then `merge()`d by centroid proximity and capped at
  `MAX_TARGETS` so magnetism gets a useful handful, not a noisy cloud. Filters are
  env-overridable (`GESTALT_CV_*`) for iteration without an edit. Validated to
  find buttons/toolbar-icons and reject oversized panels.
- Both feed the magnetism via `cv.json`; merged + capped.

**Temporal stabilization + focus hysteresis (the anti-jiggle fix) — BUILT.** Pixel
centroids flicker; AT-SPI boxes don't — which is why CV magnetism jiggled and
AT-SPI didn't. Two layers fix the *feel* (a perception problem, not head tracking):
- `TargetTracker` (`pointing/track.py`): associate detections → smooth (EMA) →
  debounce appearance (`target_min_hits`) and disappearance (`target_max_miss`),
  emitting stable, persistent targets with durable IDs. Turns the vibrating point
  cloud into AT-SPI-like steadiness. No model.
- Focus-hysteresis magnetism (`Pointer._focus_magnetism`): replaces the memoryless
  nearest-pull with an **acquire → stick → break** state machine. Grab a target
  when settling within `focus_acquire_px`, hold it (firm `focus_pull` when settled,
  light `focus_pull_move` when moving) without flip-flopping, release only when the
  intended cursor leaves `focus_break_px`. `acquire_px < break_px` = the iPad
  stickiness. Legacy soft-pull kept under `focus_acquire=false` for A/B.

**Next (same JSON interface, a drop-in swap):** OmniParser-V2's interactable-region
detector as **ONNX** (onnxruntime is far lighter than full torch) run at a few Hz
on a background thread; + an OCR text-line pass so a horizontal terminal divider =
a *gap between text bands*, not just an edge. IR/higher-res capture would sharpen
both. The classical pass is the no-dependency stand-in, not the ceiling.

## Recentering / continuous recalibration (the drift problem)

As the head *and whole body* drift over a session (slouch, lean, reposition), the
mapping decalibrates. The literature splits this into **three drifts at three
timescales**, each with a distinct fix, unified by one rule: *only correct the
neutral/mapping when the user is not actively aiming.*

| Drift | Timescale | Fix | Status |
|-------|-----------|-----|--------|
| Integration drift | sub-second | velocity dead-zone (mouse: `head_min_speed`; joystick: `joystick_deadzone`) | **implemented** |
| Postural/neutral drift | seconds | stillness-gated soft re-anchor (ZUPT analog) | **implemented (joystick)** |
| Postural/body drift | seconds | torso-referenced compensation (MediaPipe Pose) | **implemented (experimental, default-off)** |
| **Mapping drift** | minutes | **implicit recalibration from confirmed clicks (RLS)** | **implemented** |

## Control modes

Two paradigms, switchable from the pill (`control_mode`):
- **mouse** (default) — displacement control: head delta → cursor delta (PRISM
  speed-scaled). No fixed neutral; precise; drift handled by the dead-zone +
  recalibration.
- **joystick** — rate control: head *deflection from a neutral pose* → cursor
  *velocity*, with a dead-zone and expo curve (`joystick_max_speed`,
  `joystick_deadzone`, `joystick_expo`). The neutral is maintained by a
  stillness-gated re-anchor (`gestalt/pointing/neutral.py`): it re-anchors only
  when the head is **at rest AND near neutral**, so a held deflection (intentional
  aiming) is never re-centred — the RubberEdge (UIST'07) gate that stops
  auto-recentring from fighting the user. Sustained off-centre *postural* drift in
  joystick mode is the one case this can't catch alone — that needs layer 3.

**Implemented — implicit recalibration** (`gestalt/pointing/recalibrate.py`). Each
confirmed pinch at a magnetized centroid is a near-ground-truth `(raw-pose,
intended-target)` pair — cleaner than the raw clicks **PACE** (Huang, CHI 2016)
and **WebGazer** (Papoutsaki, IJCAI 2016) use, because magnetism gives the exact
centroid. An online **affine map per axis** is fit by **recursive least squares
with exponential forgetting** (λ≈0.99 ≈ 100-click memory; equivalent to a
steady-state Kalman filter), so it tracks slow drift but stays stable. Three
anti-poison guards (PACE + **EyeO**, Sharma arXiv 2307.15039): (1) pre-pinch
stillness — already enforced by Steady-Clicks; (2) consistency gate — reject a
sample whose residual to the current prediction exceeds 1/12 of the screen
diagonal (catches snap-to-wrong-element); (3) bounded correction. Verified to
cancel a 144px injected bias to 0.1px and reject full-diagonal misclicks.
→ `recalibrate`, `recal_forgetting`, `recal_max_correction_px`; `gestaltctl recal
reset`; live state in the diagnostics window (`recal n=… gain… off… res…`).

**Implemented — layer 3 body compensation** (`gestalt/input/torso.py`,
`gestalt/input/body.py`). MediaPipe **Pose** (lite, run every 2 frames) tracks the
shoulder line; the `BodyCompensator` watches torso *rotation* (shoulder roll +
width — the cues that actually drift the camera-relative head signal; translation
doesn't, since the signal is the face rotation matrix) and, when the torso moves,
absorbs the coincident head-signal change into an offset `bd`; when the torso is
still, head motion passes through as intent. `corrected = signal − bd`;
`bd += gain·w(torso_rotation)·Δsignal`. Default-off + observable (shoulder line +
`body` row in the diagnostics window) because it's novel — no published
head-pointer does end-to-end torso referencing. Honest limit: 2D shoulders can't
fully separate torso rotation from translation, so it's conservative.
→ `torso_correction`, `torso_motion_deadband`, `torso_attribute_gain`; "Body
drift correction" pill switch; recenter clears `bd`.

## Summary table

| Symptom | Technique | Paper | Constant |
|---------|-----------|-------|----------|
| Jitter at rest | 1€ filter | Casiez CHI'12 | `oneeuro_mincutoff`, `oneeuro_beta` |
| Can't lock on targets | DynaSpot area cursor | Chapuis CHI'09 | `dynaspot_*` |
| Grabbed by wrong target | velocity-gated soft pull | Worden CHI'97, Bateman CHI'11 | `snap_pull`, `snap_velocity_gate` |
| Lock sluggish / brutal break | drop lock; KTM arrival | Jacob CHI'90, Pasqual CHI'14 | (no lock) |
| Click slips on pinch | Steady Clicks freeze + gate | Trewin ASSETS'06 | `commit_freeze_ms`, `commit_velocity_gate` |
| Pointing vs confirm split | Gaze+Pinch / MAGIC | Pfeuffer SUI'17, Zhai CHI'99 | (architecture) |
| No a11y boxes (Warp) | hybrid CV detection | UIED FSE'20, OmniParser'24 | `providers` |

## Comfort mode + fine-aiming precision

**Comfort mode** (`gestalt/pointing/comfort.py`, `control_mode: comfort`) is the
absolute, self-ranging mapping — "AGC for the neck". Head orientation → screen
position via four per-direction quantile trackers (additive DUMIQE, λ≈0.003) that
learn your comfortable yaw/pitch envelope from natural motion and map it to the
screen edges. Biomechanics priors (yaw ±20°, pitch-up 13° compressed, pitch-down
22° generous — Youdas ROM, NeckCheck strain) seed it; the trackers personalize +
re-track on reposition. Signed-data additive update (not multiplicative) because
the signal crosses zero. Reaches `[neutral..Q95]→half-screen`, so the asymmetry
(up needs less tilt than down) falls out per-direction.

Hard-won fixes already in:
- **Reads `head.signal_raw`** (pre-body-comp), not `head.signal` — the layer-3
  body compensator's accumulating offset corrupts an *absolute* mapping (showed
  as `drift 0.287` pushing neutral off-centre). Body comp is mouse-mode-only now.
- **Rest-pose neutral, strictly gated.** Neutral re-centres to your resting pose
  ONLY when "parked" (pose spread < `comfort_stationary` over a ~2s WINDOW — not
  instantaneous speed, which jitter spikes) AND hand is down (hand-up = aiming).
  Frozen otherwise → mapping is stable during use. `comfort_rest_alpha 0` = fully
  manual (recenter only). Recenter (C key) snaps neutral instantly.
- **Edge overscan** (`comfort_overscan` 0.15): map range beyond the edge so
  corners reach at ~90% of comfortable travel (eases the compound yaw+pitch
  corner posture).
- **Output stage (two, switchable).** Absolute high gain (~5600 px/signal-unit)
  amplifies pose noise (~45 px), so the px cursor needs an output smoother:
  - *(legacy)* **1€ smoother** (`comfort_smooth_mincut/beta`) — speed-adaptive.
    Killed rest jitter but got *fooled* during slow precise aiming: jitter spikes
    the velocity term → it loosens smoothing → jitter leaks. Speed-based smoothing
    can't separate jitter from intent (both are low-but-noisy). Reachable by
    `comfort_follow false` for A/B.
  - **directional-consistency follow** (`comfort_follow`, DEFAULT) — see below.

### Directional-consistency follow (Angle Mouse; Wobbrock CHI 2009) — BUILT

The cure for the fooled-1€ problem. The follow-gain that pulls the cursor toward
the raw comfort target is driven by path **straightness, not speed**:

    straightness = |Σ wᵢ·vᵢ| / Σ wᵢ·|vᵢ|   ∈ [0,1]

over a short recency-weighted window of target-movement vectors `vᵢ`
(`_AngleFollow` in `comfort.py`). Pure jitter oscillates → the vectors cancel →
straightness ≈ 0 → `gain ≈ gmin` (0.05) → **cursor frozen**, however large the
per-frame wobble. A deliberate aim moves one way → straightness ≈ 1 →
`gain ≈ gmax` (0.9) → **tracks promptly**. Then `cursor += gain·(target−cursor)`.

Why it beats the 1€: it's **speed-blind**. A *slow* precise aim and a *fast* reach
both keep gain as long as they're directed — the exact case the 1€ mishandled.
Recency weighting (`comfort_follow_decay` 0.6, newest frame dominates) makes
straightness collapse within ~1–2 frames when you stop, so the cursor settles
instead of leaking the last directed vectors onto a held target. Target-agnostic —
works on menu bars / Warp where there are no snap centroids.

Knobs (all live via `gestaltctl set`): `comfort_follow_window` 8,
`comfort_follow_decay` 0.6, `comfort_follow_gmin` 0.05, `comfort_follow_gmax` 0.9,
`comfort_follow_k` 2.0 (straightness exponent — raise to demand cleaner directed
motion before gain opens). Observable in `status().comfort.{straight,gain}` and the
diag window. Tuning feel: jitter still leaks → lower `gmin` or raise `k`; aim feels
laggy/sticky → raise `gmax` or lower `k`/`window`.

### Gaze-fixation precision gate (I-DT; Salvucci & Goldberg ETRA 2000) — BUILT

Clutch-free precision trigger, layered on the follow. The iris-in-eye vector
(`gaze.py`, calibration-free — only dispersion matters, never screen position)
is watched for a **fixation**: when it goes still over a short window the eyes
have locked a target, which in eye-head coordination leads the head by ~200ms
(Sidenmark & Gellersen TOCHI'19). On fixation the follow's effective `gmax` lerps
down toward `comfort_fix_gmax` (0.35), so the head's directed approach
auto-gentles *before* it settles. The threshold is **SELF-CALIBRATING** (2026-07):
fixation = dispersion < `gaze_fix_k` (0.6) × your rolling-median dispersion
(`gaze_fix_baseline` frames), floored at `gaze_fix_floor` — watch
`status().gaze_disp` vs `status().gaze_thr`. Knobs: `gaze_fix_k`,
`gaze_fix_floor`, `gaze_fix_baseline`, `comfort_fix_gmax`, `comfort_fix_smooth`.
Toggle `gaze_fixation`. Honest limit: gaze can't be a *position* signal (fovea
~1°, webcam ~2-4° with the head moving), so it only ever gates — never points.
HISTORY: the original hardcoded `gaze_fix_dispersion` 0.08 was measured (2026-06
235k-frame session; re-confirmed 2026-07 on sessions 1782752796/1782780970)
sitting BELOW one session's median iris noise (gate stuck ~ON: `fix` 0.95 at
rest, 0.68 moving — it just capped gain instead of discriminating) and ABOVE
another's (stuck ~OFF) — a fixed amplitude lands on the wrong side of the noise
depending on rig/light/distance, the same failure the brow clutch hit before its
K×MAD fix. Known trade-off of the median form: a long steady stare collapses the
median and the gate drops out (cost: `gmax` just stays high; the floor prevents
chatter).

### Stillness-freeze (BUILT) — the cursor-won't-settle fix

Found from instrumented logs: 25% of frames the head is held still yet the cursor
moves **6px median / 38px p95 / 127px max** — it never settles. Mechanism: the
absolute map amplifies **~8,300 px per signal-unit** (small comfortable ROM → 4K
screen), so a 0.002 head wobble = 17px; and the straightness follow can't reject
it because a slow *involuntary* drift reads as `straightness` 0.85 — "directed" —
just like a slow aim, so gain stays up and the drift leaks. Straightness kills
*fast* jitter (it cancels); it's blind to *slow* drift.

The fix is a settle-freeze gated on **smoothed head speed**. (First attempt gated
on cursor *net-displacement* — wrong: drift moves the cursor about as much as a
slow aim, so it under-locked the drift *and* partially froze slow movement = lag.)
The right signal: at rest the head is physically still and the "drift" is *sensor
noise*, so the head-signal speed is genuinely low; any deliberate move raises it.
`_freeze_gscale` EMA-smooths the speed (so a 1-frame sensor spike can't unlock and
leak a frame of drift), ramps `_fz`→1 when it's below `comfort_freeze_speed`
(0.0018), and releases instantly when a deliberate move passes `comfort_freeze_
release` (0.0032); the band between holds (hysteresis). At full freeze the follow
gain is scaled to `comfort_freeze_floor` (0.05 — near-hard lock). Sim: rest spread
6px→**0.24px even through a sensor spike**; a deliberate move unlocks (`_fz`→0). So
hold still → cursor parks; nudge past the release speed → full gain (a "parking
brake" a deliberate move lifts). Obs: `comfort.freeze`. Toggle `comfort_freeze`;
raise `comfort_freeze_floor` if you want to creep while parked.

### RubberEdge hybrid gain (Casiez UIST 2007) — BUILT

Attacks the root cause of fine-aim jitter: comfort's high absolute gain existed
only to *reach the edges*. Now position control reaches just `comfort_edge_reach`
(0.85) of each half at the comfortable extreme — **lower gain → finer aim, and
jitter shrinks with it** — while an **elastic rate-assist** covers the rest: as
deflection nears/passes the extreme (`comfort_edge_start` 0.8) the cursor *glides*
on toward the corner (`comfort_edge_rate` px/s, `comfort_edge_expo`), so you never
strain past your learned range (the old corner-strain complaint). The offset is
bounded to ±half and eases back (`comfort_edge_decay`) when you relax — drift-free.
`_axis` in `comfort.py` is now stateful (per-axis `_eoff`, observable in
`status().comfort.eoff`). Toggle `comfort_edge_assist`. Tuning: want more
precision → lower `edge_reach` (more rate reliance, floatier reach); corners feel
laggy → raise `edge_rate`; cursor over-glides → lower `edge_rate` or raise
`edge_decay`.

#### RubberEdge velocity gate + decay-ungate (the "edge eats fine aim" fix)
The edge-assist was deflection-driven, so aiming at an element NEAR the edge
triggered the glide and carried the cursor past it. Fix: the glide is now a MOVING
phenomenon — a motion factor `m` ramps 0→1 over `[0.35·comfort_edge_speed (0.008)
.. edge_speed]` and scales the OUTWARD glide, so a slow fine aim leaves it inert.
BUT the inward DECAY is **not** motion-gated (decays whenever deflection returns
inside the extreme) — an early version gated decay too, which froze the offset at a
corner so you couldn't fine-aim back OFF it ("stuck on the edge"). So: glide gated
by motion (don't eat fine aim), decay always-on inside (can leave the corner).
Threaded `head.speed` → `comfort.map` → `_axis` (and the coast path via
`_last_speed`).

#### RubberEdge directionality (2026-07: "impossible to aim slightly inside the edge")
The speed gate above was still DIRECTION-BLIND: retreating from an overshoot is
also fast head motion, so while the deflection remained past `edge_start` the
glide kept firing OUTWARD **against** the user's inward pull — elements in the
`fr∈[edge_start, 1]` band (slightly inside the edge) were nearly unaimable, and
aim-wobble around `edge_start` toggled grow/decay into visible oscillation. Fix:
`_axis` tracks d(fr)/dt per axis (EMA'd; tremor alternates sign so it sits ≈0):
pushing out past `edge_start` → glide grows; pulling in (or back inside) → glide
decays; holding (|rate| ≤ eps) → glide FREEZES, so a corner park stays parked.
The speed gate `m` still scales the grow branch.

### Multi-monitor crossing (comfort, absolute mode)
Comfort maps to ONE monitor (stretching a fixed neck ROM across stacked screens
doubles gain and forces strained up-gaze). You cross to a neighbour by
**over-deflecting a FIXED ABSOLUTE ANGLE** past neutral (`monitor_cross_deg` 30,
symmetric, `cross_intent` in comfort.py) toward it. Originally envelope-RELATIVE,
but the learning quantiles kept expanding to absorb the over-deflection → the
bottom monitor became unreachable; a fixed angle is robust. On cross:
`monitors.switch(dir)` → `comfort.reseat(rx,ry,mw,mh)` (re-seed a FRESH symmetric
envelope on the new monitor — NOT just `set_neutral`, else the asymmetric span
blocks crossing back) → cursor warps to the new monitor centre. `monitor_switch_
cooldown` anti-thrash. Manual fallback `gestaltctl monitor next|<idx>` so you're
never stranded. See [TARGETS.md] for the layout/coords and the 2s refresh.

### The MOUTH (perioral) fine-pointing experiment
The most promising untried fine-aim idea: head=coarse reach, MOUTH=fine vernier.
Mouth/nasolabial muscles are low-inertia and finely graded — may out-resolve the
neck at the last inch, sidestepping the head-jitter we fought above. Capture is
wired (`input/perioral.py`, recorder, `research/perioral_analysis.py`); UNMEASURED.
Same lateral-broad/vertical-narrow anisotropy → reuse the comfort DUMIQE envelope.
See [CAMERA.md] §Perioral.

### The eyebrow precision clutch (BUILT) — `input/brow.py`, `comfort.py` lens
The fine-vertical hunt across facial muscles hit three anatomical walls, and the
brow clutch is the move that respects them rather than fighting them:
  * **mouth** (perioral) shares the neck's lateral-broad / vertical-narrow
    anisotropy — good *horizontal* vernier, bad vertical (the experiment above);
  * a **pucker** displaces the NOSE, which is the coarse head channel's own
    landmark — *co-location*, the fine signal corrupts the coarse one (a lateral
    half-smile stays decoupled, a pucker does not);
  * the **brow's rest is pinned at the bottom of its range** (frontalis only
    raises; the lowerers are weak and couple with squint) — so it's a poor
    bidirectional *axis* but an ideal unipolar, high-SNR *discrete trigger*.
So we DON'T use the brow as an axis. **Holding** the brows raised engages
**precision mode**: a low-gain crawl for the last inch, released when you relax —
a momentary HOLD, not a toggle (keeping them up through the fine move is intuitive;
a toggle's mental gear-flip is not — the user tested both). `BrowClutch` measures
brow height in the head-LOCAL frame (the perioral inter-eye frame, so head motion
cancels) vs a **running-median rest baseline** (`brow_window` frames). The baseline
IS the whole problem, and seed-then-EMA schemes failed both ways before landing
here: a symmetric always-adapting EMA *chased a held raise* and silently dropped
the hold after a few seconds; freezing the EMA to hold then *latched on a bad
acquisition seed* and read the resting face as "always clutched" forever (the seed
was off by more than the threshold and the freeze made it unrecoverable). The
MEDIAN has neither failure: rest is the MOST COMMON state, so the median IS your
rest and auto-tunes frame-to-frame; a brief raise is a minority of the window and
barely moves it (the raise stands out as positive `lift`); nothing is ever frozen,
so it can never get permanently stuck (`lift`≈0 at rest by construction). A hold
persists while raised frames stay a minority (~half `brow_window`). A confirm-count debounce kills IR chatter; an engine watchdog (`precision_timeout_s`)
releases a stuck raise and blocks re-engage until the brow physically drops.

The THRESHOLD is also SELF-CALIBRATING — the same "express it in the user's own
units" discipline as the comfort ROM envelope, applied so it generalizes across
faces ("I'm one of a million"). Instead of an absolute `brow_on`, the window also
yields a **MAD** (median absolute deviation = robust rest-jitter scale, immune to
the minority of raised frames), and engage/release fire at `brow_k_on`×MAD /
`brow_k_off`×MAD. So "a raise" = K noise-widths above YOUR OWN noise; the absolute
amount auto-scales per face/rig and the only constants are dimensionless K
(generalize). `brow_floor` guards the ultra-still case where MAD collapses.
Defaults K_on=5 / K_off=3 (≈ a clear raise above jitter). The live threshold is
observable: `status().brow_thr` vs `brow_lift` — watch `lift` exceed the
self-tuned `thr` on a raise. Verified self-calibrating: two faces with 4× different
noise both engage correctly on their own raise with the SAME K, no per-face tuning.
This is the prototype for the broader "knobs as hyperparameters" direction —
retire a hardcoded magic number by expressing it in units of the user's own signal
(next tier: a learned multi-feature discriminator only if a single feature can't
separate raise from rest, e.g. a head-pitch confound).

The lens (`ComfortMapper._apply_prec`): comfort is ABSOLUTE (head angle ↔ screen
px), which fights a relative gain cut. The reconciliation: while engaged, the
displayed delta = `precision_gain` × the full-gain delta, accumulated into a
**persistent offset** (you reach further to move less). Disengaged, that offset
bleeds back to the absolute mapping ONLY during a fast re-aim (`speed>motion`) —
so the precise placement right after you release is preserved, while a big move to
a new region quietly re-absorbs it (bounded drift, the same philosophy as the
edge-assist `_eoff`). Cursor turns **amber** in precision; auto-exit watchdog
`precision_timeout_s`; comfort-mode only; `set brow_clutch false` to disable.
TUNING: watch `status().brow_lift` at rest (≈0) vs a deliberate raise, set
`brow_on` just under the raise peak and `brow_off` ~0.6× of it.

### The gaze debug dot (BUILT) — `overlay/gazedot.py`, `gaze_debug`
Before betting on the EYE as the bidirectional-vertical channel the head/mouth/brow
all lack, we need to SEE if the raw iris signal is even coherent on IR. `gaze_debug`
draws a magenta dot at the (uncalibrated) gaze estimate (`gaze_debug_gain` × the
head-relative iris offset). NOT a pointer — a coherence check: does looking left
move it left, is it stable, does it cover range? `set gaze_debug true`.

### VERDICT (2026-07-02): the eye is dead on this rig — via iris landmarks

The coherence check was run, glasses on, on BOTH camera nodes: the IR node
(576×360, the daily driver) and the RGB node (1280×720, `cam_normalize` off).
**Jittery mess on both — practically noise relative to the head signal.** Why:
the iris spans ~8px on the IR sensor and the usable gaze signal (iris-centre
displacement inside the eye opening) is SUB-pixel; near-IR washes out the
limbus contrast MediaPipe's iris landmarks key on (the model was trained on
RGB); CLAHE amplifies noise exactly in the eye region; and on RGB the budget
went to lens glare and the same landmark-geometry floor. Consequences:

  * **MAGIC / gaze-coarse pointing is OFF the table on this hardware** with
    landmark-based gaze. Do not re-litigate without new evidence: changed
    glasses, a different camera, or an end-to-end learned gaze model on raw
    eye crops (the 2-4° literature numbers come from trained CNNs, not
    landmark geometry — that's a data-hungry research project, parked as
    *possible-but-not-with-landmarks*, not "impossible").
  * `gaze_fixation` now **defaults OFF** — before the threshold self-
    calibration (above) the stuck-ON gate had been silently capping the
    follow gmax toward `comfort_fix_gmax` (0.35), i.e. the fried eye channel
    was actively DEGRADING head aim. A channel that can only inject noise
    stays out of the pipeline. The instruments (gaze dot, self-calibrating
    gate) remain built for a 30-second retest if the hardware ever changes.
  * **The hybrid intent design SURVIVES without the eye.** What gaze was for
    — knowing the target BEFORE arrival — has a second source: **KTM
    endpoint prediction** (Pasqual & Wobbrock CHI'14: the velocity profile
    predicts the endpoint within ~39px at 90% of the movement). We already
    use KTM's arrival half; the unbuilt half is the predictor. Predicted
    endpoint × AT-SPI target list × click-history priors = the same target
    posterior, driven by head kinematics instead of the iris. Calibration
    recordings (pose-trajectory → known-point labels) are its training/eval
    data — and the eval harness + its number-to-beat (a static affine reads
    intent at ~800px median) already exist from the 2026-07 experiment.

**Still on the table:** Kalman denoiser on the raw angle; dwell-decay precision
(gain ramps down the longer you linger); the mouth *horizontal* vernier; and the
KTM endpoint→target posterior above (the surviving half of the AVP ambition:
intent inference + target-level commitment, now via motion shape, not eyes).
