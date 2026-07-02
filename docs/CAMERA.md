# Camera & signal acquisition

How Gestalt gets a clean head/face/hand/iris/mouth signal from a webcam — RGB or
**infrared** — including the hard-won IR specifics. Code: `gestalt/engine.py`
(capture + dark gate + coast), `gestalt/input/head.py` (FaceLandmarker),
`gestalt/input/gaze.py`, `gestalt/input/perioral.py`.

## Models

- **FaceLandmarker** (`models/face_landmarker.task`, VIDEO mode, `num_faces=1`,
  `output_facial_transformation_matrixes=True`). The 478-landmark bundle — indices
  468–477 are the **iris** (no `refine_landmarks` flag in the Tasks API; the bundle
  has it). Head pose comes from the **facial transformation matrix** (rotation ×
  FORWARD vector), which is **distance-invariant** — leaning closer/further doesn't
  move the cursor. Pitch sign: looking UP = positive signal. MediaPipe loses pose
  past ~30° pitch (`pitch_limit_deg` 32 flags `degraded`).
- **GestureRecognizer** (`models/gesture_recognizer.task`, 1 hand) — gives hand
  landmarks AND trained gesture labels (the labels were unused until the gestures
  mode; see INPUT.md).

## Confidences — tuned LOW to cling

`face_min_detection` 0.2 / `face_min_presence` 0.1 / `face_min_tracking` 0.1
(config-driven; were hardcoded 0.3). For a continuous pointer, **fewer drops beats
precise detection** — low tracking confidence holds the face through dim/small
frames (lean-back, IR). Changing them rebuilds the landmarker, so it needs a
restart, not a live `set`.

## Camera selection

`camera: "auto"` picks the brightest `/dev/video*` node (`_scan_camera`) — which
deliberately AVOIDS IR (IR reads dark without its illuminator). Pin an index to
force a node. `cam_width`/`cam_height` set capture size (match the sensor's native
for IR). `_open_camera()` is re-callable; changing `camera`/`cam_width`/`cam_height`/
`cam_fps` live re-opens the device (no restart). `cam_normalize` applies CLAHE
contrast (helps a dim IR face; default off, RGB unaffected).

## INFRARED (the big pivot — frees the RGB cam for normal use)

Dell Precision: `/dev/video0` = RGB, **`/dev/video2` = IR** (native **576×360**,
3-channel; video1/3 are metadata, can't open). Recipe:
```
gestaltctl set camera 2; set cam_width 576; set cam_height 360
```
**MediaPipe tracks IR fine** — full 478 landmarks INCLUDING the iris (@468), even at
`raw_mean ~9.5` brightness. So the fixation gate survives IR.

### The IR illuminator STROBES (key hardware gotcha)
It's a Windows-Hello-style sensor: the IR LED pulses, so **every other frame is
black**. On Linux we get no strobe-sync, so tracking flickered ~50%. Two evolutions
of the fix:
1. *(legacy)* absolute `cam_min_brightness` threshold — skip frames dimmer than ~4.
   Worked when close, but **ate the dim far-frames on lean-back** (a far ON-frame
   ~3–5 brightness fell below the threshold → flicker at distance).
2. **RELATIVE gate (current):** skip a frame only if `mean < cam_strobe_ratio (0.4)
   × decaying-peak`. The peak (`self._cam_peak = max(mean, peak*0.9)`) tracks the ON
   frames, so the gate adapts to distance: black frames (~0) are always well below
   `0.4×peak`; a dim far ON-frame (~peak) passes. `status().cam_lit` = fraction
   passing (~0.5 confirms the 50% strobe). Result: face stable 8/8, lean-back holds.

### IR tradeoff: HALF the framerate
Skipping the black frames halves tracked fps to ~7.5 (the sensor is ~15fps; it
ignores `cam_fps` bumps). Mitigations:
- **Coast interpolation** (`coast_interp`, default on): on each skipped/black frame,
  `Pointer.coast()` EXTRAPOLATES the head signal `coast_predict` (0.5) along its
  last velocity, advances the comfort follow + magnetism, and moves the cursor — so
  the VISUAL cursor renders ~15Hz while pose tracking stays 7.5Hz. Validated:
  extrapolation continues motion, persistence (predict=0) just eases. Camera-bound
  at ~15Hz (the loop reads at sensor rate); true higher needs a decoupled render
  thread. Coast frames do NOT run MediaPipe and NEVER fire clicks.
- **Reclaim the lost frames (not done):** `linux-enable-ir-emitter` (NOT installed;
  needs sudo) to make the LED CONTINUOUS → then `cam_min_brightness 0` /
  `cam_strobe_ratio 0` and full ~15fps, no skip. ~15fps is this sensor's ceiling.

## Gaze (iris-in-eye) — `input/gaze.py`

Calibration-FREE: we never map gaze to a screen point (fovea ~1°, webcam ~2–4° with
the head moving — too coarse to point). We use only gaze BEHAVIOUR. The iris-centre
offset from the eye-corner midpoint, normalized by corner span (indices 468/473 vs
corners 33/133, 362/263), is a head-relative gaze proxy. A **fixation** (I-DT
dispersion below `gaze_fix_dispersion` over `gaze_fix_window` frames; Salvucci &
Goldberg ETRA'00) means the eyes locked a target — and eyes LEAD the head by ~200ms
(Sidenmark & Gellersen TOCHI'19), so it's an early precision cue (see POINTING.md
fixation gate). Obs: `status().gaze_disp`, `fixating`.

## Perioral (mouth/nose) — `input/perioral.py` [EXPERIMENT]

User discovered mouth/nasolabial micro-movement may out-resolve the head at the
last inch (low-inertia, finely-graded muscles: levator labii superioris alaeque
nasi = nose↔lip snarl, zygomaticus = lateral; SAME anisotropy as the neck —
lateral broad, vertical narrow/painful). `perioral(landmarks)` expresses mouth/nose
landmarks (ulip_out 0, ulip_in 13, llip 14, corners 61/291, subnasale 2, nose_tip
1) in the **head-local frame** (built from stable inner-eye-corners 133/362) so
rigid head motion cancels → pure mouth gesture. Logged by the recorder as
`rec["mouth"]`. `research/perioral_analysis.py` computes per-axis noise-floor /
range / LEVELS / BITS for head vs each mouth signal. **Open question:** does the
mouth out-resolve the head? Run a controlled session (hold-still for noise, sweeps
for range) and compare bits. Architecture idea: head=coarse reach, mouth=fine
vernier. CAVEAT: mouth = LANDMARK-based, so IR low-res/7.5fps jitter may hurt it vs
the matrix-based head signal — may need RGB/higher-res to be a fair test.

## Body / torso (layer-3 drift comp, default OFF, mouse-only)

`input/body.py` + `input/torso.py` (MediaPipe Pose, shoulder roll). Absorbs head
drift coincident with torso rotation. Its accumulating offset CORRUPTS absolute
comfort mode, so comfort reads `head.signal_raw` (pre-body-comp). Torso roll uses
`atan2(dy, abs(dx))` to avoid the ±π wrap that caused a 360° spin.
