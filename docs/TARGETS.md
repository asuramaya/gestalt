# Targets & magnetism

Where the clickable things are, and how the cursor snaps to them. Code:
`gestalt/targets/registry.py`, `providers/atspi_provider.py`,
`providers/cv_provider.py` (disabled), `gestalt/pointing/track.py`,
`gestalt/pointing/pointer.py` (focus magnetism), `gestalt/overlay/targets_overlay.py`.

## The core lesson: structure beats pixels

A magnetism target needs a screen rectangle. There are two ways to get one, and the
whole arc of this project converged on the answer:

- **AT-SPI (the accessibility tree) = THE source.** It reads the app's own widget
  hierarchy over D-Bus: exact, semantic (this is a button, that's a link), stable,
  ~free, no per-app code. Covers GTK, Qt, Electron, Firefox, Chrome, LibreOffice —
  most apps.
- **CV (pixel detection) = a DEAD END.** Capturing the window and guessing boxes
  from edges is backwards — it reconstructs from pixels what the app already knows.
  It drains (the capture is expensive), hallucinates on text, and was inaccurate
  even on the panes it was meant for. **Default OFF.** A trained ONNX detector
  wouldn't fix the capture cost; it'd be a fancier guess.

Magnetism lives ONLY where targets are trustworthy (AT-SPI). In no-a11y apps
(Warp/canvas) there are NO targets and you use the raw fine-aim pointer — which is
fine, because those targets (terminal panes) are huge and easy to aim at.

## AT-SPI provider (`providers/atspi_provider.py`, SYSTEM python)

Walks `Atspi.get_desktop(0)` and emits actionable elements' screen rects.

- **THE BUS MUST BE ON.** `gsettings get org.gnome.desktop.interface
  toolkit-accessibility` was `false` by default → apps publish NO tree (Chrome=3
  nodes, only gnome-shell exposed). FIX: `gsettings set ... toolkit-accessibility
  true` (now in `install.sh` step 7). **Running apps must RELAUNCH** to expose
  trees; **Chrome needs `--force-renderer-accessibility`** (lazy a11y, only exposes
  its DOM if an AT client is present at launch).
- **Role filter:** ACTION roles (push button, link, menu item, check/radio/toggle,
  entry, list item, table cell, combo, page tab, slider, spin, menu) PLUS editable
  text (TEXT/PASSWORD_TEXT gated by `ST.EDITABLE`). **ICON dropped** — standalone
  icons are decorative ("random purple squares"); real icon-buttons expose as
  PUSH_BUTTON. Require `ST.SHOWING`, size cap w<2400/h<1000 (reject containers).
  NOTE: the `Action` interface would be the ideal interactivity gate, but apps
  implement it inconsistently here (buttons reported 0 Action, text reported
  Action) — so filter by ROLE+STATE, not Action.
- **ACTIVE-WINDOW ONLY** (`atspi_active_only`, default True, env
  `GESTALT_ATSPI_ACTIVE_ONLY`): box only the app whose frame has `ST.ACTIVE` (plus
  always gnome-shell, so the top bar / quick settings stay targetable). Else EVERY
  app's elements get boxed — including OCCLUDED background windows — and you see
  "boxes in random places" (measured: 110 of 120 boxes were an occluded Firefox).
  Edge case: if the active window is itself occluded its boxes still draw (rare;
  active is normally topmost).

## Containment dedup (`dedup_nested`, `GESTALT_ATSPI_DEDUP`, default on)

A content-heavy page (a dashboard in a browser) exposes ~200 actionable boxes, and
~28% of them are REDUNDANT NESTING — a `list item` row whose only child is the
`link` you'd click, a `push button` wrapping its own text label. Drawn, they're the
"stacked rows" that clutter the overlay; as targets they split magnetism into
overlapping attractors at the same spot. The fix is general (geometry + role, no
per-app rules): when one box sits ≥85% inside a larger one, keep the
higher-ACTIONABILITY box — a true click leaf (button/link/menu item/tab/entry…,
rank 3) beats a wrapping container (`list item`/`table cell`/`menu`, rank 2); a tie
keeps the smaller inner leaf. Measured on a live Cloudflare dashboard: 199 → 163
boxes, the 36 dropped being exactly the wrapper rows + inner labels. This is a
density/clutter cleanup, NOT the alignment fix — see below.

## The 32px overlay-placement offset (the real "boxes in the wrong place" bug)

Symptom: boxes rendered consistently LOW — for a ~24px menu item, a full
element-height off, so they clearly didn't sit on their elements. It was NOT a
coordinate or scale bug: AT-SPI coords are correct (single 3840×2400 @ origin 0,0,
boxes at true positions 0–3840), the renderer is 1:1, and the box math is right.
The bug was the overlay WINDOW's on-screen placement. `TargetOverlay` sets its
position ONCE in the `Window(position=(0,0))` constructor — but the window is
briefly WM-MANAGED at creation, and Mutter placed it BELOW the top-bar strut at
**(0, 32)**; the subsequent override-redirect then froze that offset. So the whole
overlay was shifted down 32px. (The cursor dot is immune because it re-sets its
position every frame; the overlay set it once.) FIX: after the override-redirect
map, explicitly `xwin.configure(x=0, y=0)` (now unmanaged, it sticks) and
re-assert it on each redraw. Verified the window reports (0,0,3840,2400). Lesson:
an override-redirect window's REQUESTED position can be silently overridden by the
WM during its managed moment — re-assert origin after going unmanaged. Diagnose by
querying the real geometry via `translate_coords(root,0,0)`, not what you asked for.

## Coordinates: physical, active-monitor-aware

AT-SPI returns physical screen coords (verified: top-bar elements at y≈16 across the
full width, max_x≈3840). These are global virtual-desktop coords and match the
overlay/injection space directly. The earlier "top-left quadrant" misalignment was
NOT scaling — it was the **stale monitor layout** (Gestalt read it once at startup;
a dual→single display change left everything offset). Fixed by the 2s
`Engine.refresh_layout()` (see ARCHITECTURE.md). Display scale on the dev box is
1.0; HiDPI logical/physical conversion is only applied to the in-shell cursor.

## TargetTracker (`pointing/track.py`) — the anti-jiggle layer

CV centroids flicker; even AT-SPI re-reads can churn order. Magnetizing to a moving
attractor jiggles. The tracker is classic multi-object tracking: associate each
detection to a persistent track (nearest within `target_assoc_px`), EMA-smooth its
position (`target_pos_alpha`), debounce appearance (`target_min_hits`) and
disappearance (`target_max_miss`), emit stable targets with **durable IDs**. Runs
at the read cadence (every 10 frames). The durable IDs are what let the focus state
machine commit without flip-flopping.

## Focus-hysteresis magnetism (`Pointer._focus_magnetism`) — the iPad feel

Pixel/point "soft pull toward nearest centroid" jiggles and never commits. The iPad
cursor is a **focus state machine over stable elements with hysteresis**, not a
force field. So:
- **Acquire** a target when the cursor settles (`arrived`) within `focus_acquire_px`
  (90).
- **Hold** it — `focus_pull` (1.0 = HARD LOCK, cursor sits exactly on the centroid,
  no head-jitter leak) when settled; `focus_pull_move` (0.2) light pull while
  moving so you can slide off.
- **Break** only when the *intended* (head-driven) cursor leaves `focus_break_px`
  (200). `acquire_px < break_px` = the stickiness. Break is computed on the intended
  cursor, not the locked output, so a hard lock still releases on directed motion.
- Legacy memoryless soft-pull kept under `focus_acquire=false` for A/B.

This is NOT ML — Apple doesn't learn the pointer magnetism; the feel is stable
semantic targets + hit-test + focus + hysteresis.

## Target debug overlay (`overlay/targets_overlay.py`, `target_overlay` toggle)

Draws the live AT-SPI boxes over the desktop, coloured by role (green=button,
blue=link, orange=entry, purple=menu, grey=other, yellow=the focus-grabbed box),
so you can SEE coverage and watch the lock. Two gotchas solved:
- **No alpha:** the 2nd SDL window has no alpha channel (clear-to-transparent
  painted opaque BLACK over the screen). FIX: don't use alpha — CLIP the window via
  the X **bounding shape** (`shape_rectangles(SO.Set, SK.Bounding, ...)`, dict rects)
  to ONLY the outline bars; everything else is physically cut out, desktop shows
  through. python-xlib accepts dict-rect lists.
- **GPU spike:** re-shaping a full-desktop window every frame forced whole-screen
  recomposite. FIX: signature-skip — redraw only when the box set/focus changes
  (~1/s).

## CV provider — DISABLED, for reference (`providers/cv_provider.py`)

Classical cv2-only (no torch/onnx/OCR in the venv; YOLO on Intel Xe ~0.5fps would
break iteration). It captured the active window and did Canny→contours→boxes, with
a UIED-style (Chen et al. FSE'20) interior-edge-density TEXT filter (reject dense
interiors via `cv2.integral`), downscale to 1280 first, conservative cap, and an
app-gate (`cv_apps` allowlist, default `["warp"]`, slow `cv_poll_ms` 1500) so it
sat IDLE except in Warp. Even so: too expensive (the `get_image` full-window pixel
grab is the cost), inaccurate, and dropped by the atspi-authority dedup wherever
a11y exists. Re-enable via `set providers '["atspi","cv"]'` + `cv_apps` if ever
revisited (the capture + provider plumbing would be reused for an ONNX detector).
