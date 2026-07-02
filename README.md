# Gestalt

[![CI](https://github.com/asuramaya/gestalt/actions/workflows/ci.yml/badge.svg)](https://github.com/asuramaya/gestalt/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

Hands-free pointer for GNOME — move the cursor with your **head**, click with a
**pinch**, living where it belongs: a **Quick Settings pill** next to Wi-Fi and
Bluetooth. Apple-Vision-Pro-style "look to target, pinch to act," on Linux.
Built and tested on a **Precision 5770** (GNOME 50, Wayland).

> **Status: foundation / pre-alpha.** The interaction model is prototyped and
> research-validated (see [docs/POINTING.md](docs/POINTING.md)); the daemon frame,
> config, IPC, pill, and target providers are in place. The CV engine is being
> ported from the prototype — until then the daemon runs in status-only mode.

## Why a head-pointer pill and not a script

The pointing is **not tuned by feel** — every stage is a published HCI technique
chosen to fix a specific failure of naïve head-tracking: a **1€ filter** for
jitter, **DynaSpot** speed-scaled magnetism so you can actually land on small
targets, **Steady Clicks** so the click doesn't slip when your hand pinches, and
**no dwell-lock** (it fights fine-aiming) — confirmation is the pinch, on a
separate channel. The full citation map is in [docs/POINTING.md](docs/POINTING.md).

## What you get

Three observability surfaces so you always know if it's working:
- **Top-bar HUD** — a colour-coded glance (green tracking / amber degraded / red
  lost) with live fps; click it to toggle the diagnostics window.
- **Quick Settings pill** — click to arm/disarm; the menu shows pitch, targets,
  snap, per-finger pinch readiness, and last action, plus recenter + diagnostics.
- **Panic kill** — a global shortcut (`Super+Alt+K`, configurable) and a red pill
  item that **hard-stop the daemon via systemd**, not the daemon's socket. When a
  head-pointer flails you can't trust it to land on a button and a wedged daemon
  may never read a socket command — so the kill goes around both, over the
  keyboard, and SIGTERM→SIGKILLs the daemon (releasing any held click) within 3s.
- **Diagnostics window** — camera + face mesh + hand skeleton overlaid with the
  pipeline's live decisions (catch-radius, the velocity gate, the pitch-limit
  dropout, why a click did/didn't fire). The polished way to see failures.

It magnetizes the cursor to on-screen UI elements via **accessibility boxes** for
most apps, with a **computer-vision fallback** for apps that expose none.

## Architecture

```
 gestalt@asuramaya  (GNOME pill, runs as you)
    │  reads  $XDG_RUNTIME_DIR/gestalt/status.json
    │  writes $XDG_RUNTIME_DIR/gestalt/control.sock
    ▼
 gestaltd  (user systemd service, bundled Python 3.12 venv)
    │  head pose → 1€ → DynaSpot magnetism → KTM arrival → Steady-Clicks commit
    ▼
 target providers (subprocesses): atspi (a11y boxes) · cv (pixel fallback)
```

Runs entirely **as your user** — webcam, `/dev/uinput`, and the overlay need no
root. Full map in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Install

Requires [`uv`](https://docs.astral.sh/uv/) (for the pinned 3.12 CV venv), GNOME
Shell 46–50 on Wayland, and a webcam.

```sh
make install     # builds the venv, installs the user service + pill
# then LOG OUT and back in once (Wayland reloads the shell)
```

Control it from the CLI too:

```sh
gestaltctl status
gestaltctl arm | disarm | recenter
gestaltctl set snap_pull 0.5
```

## Project layout

| Path | What |
|------|------|
| `bin/gestaltd` | the daemon (frame + control loop) |
| `bin/gestaltctl` | CLI client for the control socket |
| `gestalt/` | core package: `config` `ipc` `engine` + `input/ pointing/ gesture/ overlay/ targets/` |
| `providers/` | `atspi_provider.py` (system python), `cv_provider.py` (venv) |
| `extension/` | the GNOME Quick Settings pill (GJS) |
| `systemd/user/` | the user service unit |
| `docs/` | `ARCHITECTURE.md`, `POINTING.md` (the cited HCI basis) |

## Acknowledgements

The interaction model stands on a lot of HCI research — 1€ filter (Casiez),
DynaSpot (Chapuis), Bubble Cursor (Grossman), Semantic Pointing (Blanch),
Steady Clicks (Trewin), MAGIC (Zhai), Gaze+Pinch (Pfeuffer), Camera Mouse
(Betke), and the UIED / Screen Recognition / OmniParser line for CV target
detection. Cited in [docs/POINTING.md](docs/POINTING.md).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
