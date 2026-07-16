# Gestalt — family conformance audit

Measured against [`~/code/REPOS/FAMILY.md`](../../FAMILY.md). Auditor: Fulcrum (agent
in repo gestalt), 2026-07-15, at alfred's request. Snapshot: `main`, VERSION 0.2.0,
working tree clean (no operator WIP to triage around).

## What gestalt is

Gestalt is the family's **input/HCI demon**: a hands-free pointer for GNOME — move
the cursor with your head, click with a pinch, driven from a Quick Settings pill
("look to target, pinch to act," Apple-Vision-Pro-style, on Linux). Where the five
resource-governors rule *machine* state (coldspot=net, phanspeed=power, ByeByte=bytes
at rest, RAMstein=bytes alive, kast=cast paths), gestalt rules the *cursor* — reading
a camera and driving uinput. That domain forces one structural divergence the rest of
this audit turns on: **gestaltd is a user-session daemon, not a root system daemon.**
Its privilege is group-based device access (video for the camera, input for uinput),
not root — so the "privileged root actor + world-reachable hardened socket" shape
becomes a user-private session socket. Legitimate reshaping, not laxity. Status:
foundation / pre-alpha (v0.2.0).

## Anatomy vs the doctrine

| Axis | Verdict | Note |
|---|---|---|
| daemon owns truth | **✓** | `gestaltd` owns head-track→cursor truth; `status.json` is the seed the pill/CLI read, never the master. |
| verb CLI over socket | **✓** | `bin/gestaltctl` — verbs: arm, disarm, recenter, mode, set, record, diag, reset, status. Sends JSON over the control socket; never sudos. |
| socket doctrine | **domain-shaped** | AF_UNIX + newline-JSON + hostile-input→`{"ok":false,"error":…}` (never a crash) all ✓. **SO_PEERCRED / mode-0660 / world-reach are moot by domain**: the socket lives in `$XDG_RUNTIME_DIR/gestalt/` (user-private 0700), not a root daemon's world-reachable path. `gestalt/ipc.py` documents this exemption in-code ("Unlike PhanSpeed…"). |
| status.json seam | **✓** | Atomic tmp-then-`os.replace` in `ipc.py`, `gestaltd`, and the cursor file in `engine.py`. 0640 is moot — it's under the user-private runtime dir. |
| config = seed, typed/clamped | **✓** | `tests/test_config.py` covers typed/clamped load. |
| failsafe invariant compiled-in | **✓ (domain-perfect)** | The family's cool>quiet analog: **"the pointer is always killable by keyboard."** A global kill-hotkey + `systemctl --user stop` SIGKILLs the whole cgroup, the kernel tears down the uinput device, any held click releases. Reachable precisely when the pointer itself can't be trusted to land on a menu. |
| smoke (`make smoke`) | **✗ TODO** | Has pytest units (`test_config`, `test_endpoint`, `test_resolve`) but no `make smoke` booting the daemon against a fake camera and asserting the `status.json` shape. |
| adversarial socket tests | **✗ TODO** | Error-envelope path exists; no fuzzer. Lower threat (user-private socket) but still owed a hostile-input test. |
| healthcheck bin | **partial** | `gestalt/health.py` holds the STATES/severity logic that feeds `status.json` + the pill HUD (the HUD *is* the live health surface), but there is no standalone `gestalt-healthcheck` bin. |
| update bin + timer | **✗ TODO** | No `gestalt-update` / check timer / polkit flow. |
| install / uninstall | **✓ / ✓** | `install.sh`, `uninstall.sh`. Privilege split is domain-shaped: user-level install (`systemctl --user`), no sudo-daemon half — arguably the *most* upstreamable install of the family. |
| Makefile | **✓** | help/install/uninstall/lint/test/check/pack/clean. |
| bin/ layout | **✓** | `bin/gestaltd`, `bin/gestaltctl`. |
| CoC / CONTRIBUTING / SECURITY | **partial** | CODE_OF_CONDUCT.md ✓; CONTRIBUTING.md ✗ TODO; SECURITY.md ✗ TODO. |
| CI | **✗ TODO** | No `.github/workflows`. |
| license | **✓ GPLv3** | LICENSE is GPLv3. |
| extension UUID | **✓ @asuramaya** | `gestalt@asuramaya`. |
| stdlib-only | **domain-exempt, UNDOCUMENTED** | mediapipe, numpy, evdev, Xlib are physically required (face/hand tracking + uinput injection + X cursor/window queries). Exemption is legitimate (cf. kast's protocol zoo) but **`packages.txt` is missing** — the exemption must be written down there. TODO. |
| pill idiom | **partial TODO** | QuickMenuToggle ✓, SystemIndicator ✓, hero HUD ✓. But it uses **custom color literals** (`#3dd7a0/#f0b840/#ff5c5c/#8a8f99`, cursor-ring colors) not the family palette, and has **no version footer / update-notice row**. Wave-2 unify work: adopt the family palette (or go stock-theme-only per the maat/kast ruling) + version footer + update row. |

## Convergence shortlist (proposed)

Wave-1 mechanical, portable from ancestors: **packages.txt** (document the CV/input dep
exemption), **CONTRIBUTING.md + SECURITY.md**, **CI**, **update bin + check timer**
(port phanspeed's polkit flow), **`make smoke`** (boot daemon vs a fake camera, assert
status.json shape) + a socket hostile-input test, expose **`gestalt-healthcheck`**,
and the **pill-idiom** unify (palette / version footer / update row).

Domain exemptions to record in FAMILY.md's table (not TODOs — settled by domain):
user-session daemon (no root actor), user-private socket (peercred/0660 moot),
non-stdlib (CV + input), user-level install.

## Operator WIP

None in the tree — `main` is clean at audit time. (Live operator research this session
is the compositor-tap grounding work; it lives in a throwaway `gestalt-probe@asuramaya`
shell extension outside this repo, not in the gestalt working tree.)
