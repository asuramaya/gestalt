# Target providers

A **provider** is a standalone process that discovers candidate on-screen
targets (clickable widgets, panes, icons) and streams their screen-space boxes
so the pointer can magnetize to the nearest centroid (DynaSpot; see
[../docs/POINTING.md](../docs/POINTING.md)).

Providers are separate processes on purpose: the CV engine needs a pinned 3.12
venv (mediapipe/opencv), while AT-SPI (`gi`/`Atspi`) only exists on system
python. Decoupling them as subprocesses lets each run on the interpreter it
needs, and makes the target layer **pluggable** — adding a new source never
touches the CV core. This is the "general, not hardcoded" contract.

## Protocol

Each provider is launched with one argument: the path of the JSON file it owns,
under `$XDG_RUNTIME_DIR/gestalt/targets/<name>.json`. It rewrites that file
every `provider_poll_ms` with:

```json
{"targets": [{"cx": 1920, "cy": 540, "x": 1900, "y": 520, "w": 40, "h": 40,
              "role": "push button", "source": "atspi"}]}
```

`cx,cy` is the centroid the pointer snaps to. The daemon's target registry merges
every provider's file and dedupes overlapping boxes.

## Providers

| name    | interpreter   | covers                              | gap |
|---------|---------------|-------------------------------------|-----|
| `atspi` | system python | any app exposing AT-SPI (most GTK/Qt) | Warp & other a11y-less apps emit nothing |
| `cv`    | gestalt venv  | pixel-detected panes / widgets        | horizontal splits ambiguous in text-heavy windows (needs OCR pass) |

The `cv` provider is the fallback for apps with no accessibility tree (the Warp
terminal — the original motivating case). Roadmap: swap the Sobel-divider stub
for OmniParser-V2's standalone YOLOv8 interactable-region detector + an OCR
text-line pass to disambiguate terminal panes.
