# Security Policy

Gestalt runs a **user-session daemon** (`gestaltd`) that reads your webcam,
injects input via `/dev/uinput`, and answers a local Unix socket — no root,
but real physical control over your pointer and keyboard, so it's taken
seriously.

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Instead use
GitHub's private reporting:

1. Go to the repo's **Security** tab → **Report a vulnerability**.
2. Describe the issue, affected version, and a reproduction if possible.

You'll get a response as soon as reasonably possible.

## Threat model

The relevant attacker is an **unprivileged local process** on the same
session abusing `gestaltd` over its control socket, or a compromised app
feeding it hostile target data. There is no network attack surface —
`gestaltd` never opens a network socket.

Hardening in place (see `src/gestalt/ipc.py`, `src/bin/gestaltd`,
`src/data/systemd/user/gestalt.service`):

- **Unlike a root-daemon pill (PhanSpeed, byebyte, RAMstein), gestaltd's
  socket faces its owner only** — `$XDG_RUNTIME_DIR/gestalt/` is a
  user-private `0700` directory, so SO_PEERCRED / world-reachable-socket
  concerns are moot by construction; `sutra.allow_uids({os.getuid()})` is
  the belt-and-suspenders check on top.
- **Hostile-input doctrine** — socket input is hostile by default: bounded
  reads, JSON only, a fixed command set; unknown or malformed input answers
  `{"error": ...}` and the connection dies — the daemon never crashes on
  input.
- **Config is the seed, never the master** — every key is typed and clamped
  on load (`sanitize_config`, the one chokepoint every load and every
  socket `set` passes through). A tampered config can tune numbers within
  clamps; it cannot grant new behaviour or weaken an invariant.
- **status.json seam** — written atomically (tmp + rename); readers never
  see a torn write.
- **Sandboxed unit** — `NoNewPrivileges`, `RestrictNamespaces`,
  `RestrictRealtime`, `LockPersonality` on the systemd unit; no
  `PrivateDevices`/`AF_UNIX`-only restriction, since the daemon genuinely
  needs the webcam, `/dev/uinput`, and the X/Wayland session sockets.

### The kill invariant (compiled in, never configurable)

**The pointer is always killable by keyboard.** A global kill-hotkey and
`systemctl --user stop gestalt.service` both go around the daemon's own
socket entirely (a wedged daemon may never read a command) straight through
the kernel: SIGTERM, escalating to SIGKILL in 3s if the daemon can't stop
cleanly. Any change that routes the kill path through something the daemon
itself has to cooperate with, or makes it configurable, is rejected as a
matter of policy, not review taste.

### gestalt-mcp (opt-in, not part of the default install)

`src/mcp/gestalt_mcp.py` exposes the same target perception + uinput
actuation to an MCP client (an agent driving the desktop instead of a
head). Its injecting tools (`click`/`scroll`/`drag`) refuse to run unless
the currently-focused window's `wm_class` matches
`GESTALT_MCP_ALLOWLIST` — empty/unset means refuse everywhere, secure by
default. This is a coarse first gate, not a complete safety story: it
checks the globally-focused window, not the window under the actual click
point, and `uinput` input is kernel-level with no sandbox boundary
underneath it. See `src/mcp/README.md`'s own safety section before setting
`GESTALT_MCP_ALLOWLIST` to anything, and prefer a scoped/sandboxed session
over a daily-driver desktop for anything autonomous.

## Update path

No update mechanism ships yet (pre-alpha, no tagged releases — see
[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)'s exemptions table). When
one lands it inherits the family's fail-closed doctrine: click-to-install,
never unattended, and never installs what can't be verified against a
signed checksum.
