# gestalt-mcp — the agent-facing half of the dual-use design

Exposes the same target perception (AT-SPI + CV, merged) and actuation
(uinput) substrate the human head-pointer uses, as MCP tools, for an agent
driving the desktop instead of a head. See `docs/TARGETS.md` §the name field
+ shared resolver for the design rationale, and the top of `gestalt_mcp.py`
for what is and isn't in scope.

Deliberately NOT wired into `install.sh` / `make install` — this is opt-in
tooling for a developer/power-user pointing an MCP client at their own
checkout, not part of the default human-pointer install.

## Setup

Reuses the same venv `gestaltd` already needs (it already has `evdev` and
`python-xlib`, which this depends on too):

```sh
uv pip install --python ~/.local/share/gestalt/venv/bin/python mcp
```

Register it with your MCP client (e.g. add to `.mcp.json` — note: `.mcp.json`
is gitignored in this repo on purpose, it's machine-specific):

```json
{
  "mcpServers": {
    "gestalt": {
      "command": "/home/YOU/.local/share/gestalt/venv/bin/python",
      "args": ["/path/to/gestalt/mcp/gestalt_mcp.py"],
      "env": { "GESTALT_MCP_ALLOWLIST": "" }
    }
  }
}
```

`GESTALT_MCP_ALLOWLIST` is empty above ON PURPOSE — read the safety section
below before setting it to anything.

## Requires the daemon running

The read tools (`list_targets`, `resolve`) read the SAME live target files
`gestaltd`'s `Registry` maintains under `$XDG_RUNTIME_DIR/gestalt/targets/`
— they never spawn their own provider subprocesses. If `gestaltd` isn't
running (or is disarmed with providers paused), `list_targets` returns `[]`.
`active_window` and the injection tools don't need the daemon at all — they
talk to X11/uinput directly.

## Tools

| tool | reads/writes | notes |
|---|---|---|
| `list_targets(name_hint="")` | read-only | the live merged target list |
| `active_window()` | read-only | focused window's wm_class + title |
| `resolve(x, y, radius=90, name_hint="")` | read-only | dry-run of `click`'s resolution — never injects |
| `click(x=None, y=None, name_hint="", radius=90, button="left")` | **injects** | `button`: left\|right\|middle\|double |
| `scroll(x, y, amount)` | **injects** | |
| `drag(x1, y1, x2, y2, button="left")` | **injects** | |

No `type_text()` — see the module docstring for why that's deliberately
deferred, not forgotten.

## Safety — read this before setting `GESTALT_MCP_ALLOWLIST`

The three injecting tools refuse to run unless the CURRENTLY ACTIVE window's
`wm_class` matches a comma-separated substring in `GESTALT_MCP_ALLOWLIST`.
Empty/unset = refuse everywhere (secure by default).

This is a **coarse, first gate**, not a complete safety story:

- It does not stop an agent from doing something destructive **within** an
  allowed app — it only answers "is *some* window matching the allowlist
  currently focused," not "is this specific click/action safe."
- It checks the **globally focused window** (`_NET_ACTIVE_WINDOW`), not the
  window physically under the click point — for a normal application window
  those usually coincide, but always-on desktop shell chrome (GNOME's own
  top bar/panel) typically ISN'T represented via `_NET_ACTIVE_WINDOW` at all,
  so an allowlist entry for `gnome-shell` may never actually match even when
  clicking a real, resolvable shell panel target. Known limitation, not yet
  addressed.
- `uinput` input is kernel-level and indistinguishable from a real device —
  there is no sandbox boundary underneath this gate. Prefer running this
  against a scoped/sandboxed session, not a daily-driver desktop, for
  anything autonomous.
