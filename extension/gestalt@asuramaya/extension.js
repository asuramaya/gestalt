// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 asuramaya and Gestalt contributors
//
// Gestalt — hands-free head-pointer. Two native surfaces, both reading the
// daemon's status snapshot:
//   * top-bar HUD  — a colour-coded glance ("is it working right now?"),
//                    click to toggle the diagnostics window.
//   * Quick Settings pill — controls + the detail metrics.
// Control commands go to the daemon's Unix socket (it runs as you, in-session).

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {QuickMenuToggle, SystemIndicator} from 'resource:///org/gnome/shell/ui/quickSettings.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const RUNTIME = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'gestalt']);
const STATUS_PATH = GLib.build_filenamev([RUNTIME, 'status.json']);
const SOCK_PATH = GLib.build_filenamev([RUNTIME, 'control.sock']);
const CURSOR_PATH = GLib.build_filenamev([RUNTIME, 'cursor']);   // live "x y snapped"

// severity -> colour (mirrors gestalt/health.py STATES severities).
const SEV_COLOR = {ok: '#3dd7a0', warn: '#f0b840', bad: '#ff5c5c', idle: '#8a8f99'};
const ARMED_ICON = 'face-smile-symbolic';
const DISARMED_ICON = 'face-plain-symbolic';

let _cancellable = null;

function readStatus() {
    try {
        const [ok, bytes] = GLib.file_get_contents(STATUS_PATH);
        if (!ok)
            return null;
        const o = JSON.parse(new TextDecoder().decode(bytes));
        return (o && typeof o === 'object' && !Array.isArray(o)) ? o : null;
    } catch (_e) {
        return null;
    }
}

function sendCmd(obj) {
    const client = new Gio.SocketClient();
    client.timeout = 2;
    const addr = new Gio.UnixSocketAddress({path: SOCK_PATH});
    const payload = new TextEncoder().encode(JSON.stringify(obj) + '\n');
    const cancel = _cancellable;
    client.connect_async(addr, cancel, (src, res) => {
        let conn;
        try {
            conn = src.connect_finish(res);
        } catch (e) {
            if (!e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                logError(e, 'Gestalt connect');
            return;
        }
        conn.get_output_stream().write_all_async(
            payload, GLib.PRIORITY_DEFAULT, cancel, (out, ores) => {
                try {
                    out.write_all_finish(ores);
                    conn.close(null);
                } catch (e) {
                    logError(e, 'Gestalt write');
                }
            });
    });
}

// ---- the reliable kill switch -----------------------------------------------
// A misbehaving head-pointer is exactly when you CAN'T trust the pointer to land
// on a menu item, and a wedged daemon may never service a socket 'disarm'. So the
// panic path deliberately goes around gestaltd to the user's systemd manager:
// `systemctl --user stop` SIGTERM→SIGKILLs the whole cgroup (daemon + providers),
// the kernel tears down the virtual uinput device, and any held click is released.
// It is reachable two ways — a global keyboard shortcut (the keyboard still works
// when the pointer doesn't) and the red item in this menu. The displayed accel.
let _killAccel = '';

function systemctlUser(verb) {
    try {
        const p = Gio.Subprocess.new(
            ['systemctl', '--user', verb, 'gestalt.service'],
            Gio.SubprocessFlags.NONE);
        p.wait_async(null, null);   // fire-and-forget; don't block the shell
    } catch (e) {
        logError(e, `Gestalt systemctl ${verb}`);
    }
}

function panicKill() {
    systemctlUser('stop');
    Main.notify('Gestalt killed', 'Daemon stopped — pointer and clicks released.');
}

function prettyAccel(accel) {
    if (!accel)
        return '—';
    return accel.replace(/</g, '').replace(/>/g, '+')
        .replace(/\+$/, '').replace(/\b\w/g, c => c.toUpperCase());
}

// ---- in-shell cursor: a ring drawn in the shell's TOP layer, so it sits above
// quick-settings / notifications / menus (an XWayland overlay window cannot). The
// daemon writes the live position to RUNTIME/cursor; we poll it fast and place a
// non-reactive St.Widget. Click injection is separate (evdev), so this is purely
// the visual the XWayland dot couldn't provide over shell chrome.
const RING = 'rgba(51,214,200,0.95)';
const RING_SNAP = 'rgba(120,255,140,0.98)';
const ShellCursor = GObject.registerClass(
class ShellCursor extends St.Widget {
    _init() {
        const size = 36;
        super._init({reactive: false, can_focus: false, track_hover: false,
            width: size, height: size});
        this._size = size;
        this._snapped = null;
        this._paint(false);
        this.hide();
    }

    _paint(snapped) {
        if (snapped === this._snapped)
            return;
        this._snapped = snapped;
        this.style = `border: 4px solid ${snapped ? RING_SNAP : RING}; ` +
            `border-radius: ${this._size / 2}px; background-color: rgba(0,0,0,0);`;
    }

    place(x, y, snapped) {
        // daemon coords are physical px; shell actors are logical -> divide by scale
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor || 1;
        this.set_position(Math.round(x / scale - this._size / 2),
            Math.round(y / scale - this._size / 2));
        this._paint(snapped);
    }
});

// ---- top-bar HUD: one colour-coded glance, click toggles diagnostics --------
const GestaltHUD = GObject.registerClass(
class GestaltHUD extends PanelMenu.Button {
    _init() {
        super._init(0.0, 'Gestalt HUD', true);   // dontCreateMenu: it's a glance + click
        this._label = new St.Label({
            text: '○ Gestalt',
            y_align: Clutter.ActorAlign.CENTER,
            style: 'font-family: monospace;',
        });
        this.add_child(this._label);
        this.connect('button-press-event', () => {
            sendCmd({cmd: 'diag'});               // click HUD -> toggle the deep view
            return Clutter.EVENT_STOP;
        });
    }

    refresh(s) {
        if (!s) {
            this._label.text = '○ off';
            this._label.style = `color: ${SEV_COLOR.idle}; font-family: monospace;`;
            return;
        }
        const color = SEV_COLOR[s.severity] || SEV_COLOR.idle;
        const dot = s.armed ? '●' : '○';   // ● armed / ○ disarmed
        const fps = s.fps ? `  ${Math.round(s.fps)}fps` : '';
        this._label.text = `${dot} ${s.state_label || s.state || 'Gestalt'}${fps}`;
        this._label.style = `color: ${color}; font-family: monospace;`;
    }
});

// ---- Quick Settings pill: controls + detail metrics -------------------------
const GestaltToggle = GObject.registerClass(
class GestaltToggle extends QuickMenuToggle {
    _init() {
        super._init({title: 'Gestalt', toggleMode: true});
        this.menu.setHeader(ARMED_ICON, 'Gestalt', 'Hands-free pointer');

        this._detail = new PopupMenu.PopupMenuItem('', {reactive: false});
        this._detail.label.clutter_text.line_wrap = true;
        this.menu.addMenuItem(this._detail);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // Panic kill (hard stop via systemd). Relabels to a green "Start daemon"
        // when the daemon is already down — you can't kill what's dead, and the
        // socket can't revive it, so reviving is also a systemd action.
        this._killStarts = false;
        this._kill = new PopupMenu.PopupMenuItem('');
        this._kill.connect('activate', () =>
            this._killStarts ? systemctlUser('start') : panicKill());
        this.menu.addMenuItem(this._kill);
        this._paintKill(false);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._recenter = new PopupMenu.PopupMenuItem('Recenter cursor');
        this._recenter.connect('activate', () => sendCmd({cmd: 'recenter'}));
        this.menu.addMenuItem(this._recenter);

        this._joystick = new PopupMenu.PopupSwitchMenuItem('Joystick mode', false);
        this._joystick.connect('toggled', (_i, state) =>
            sendCmd({cmd: 'mode', mode: state ? 'joystick' : 'mouse'}));
        this.menu.addMenuItem(this._joystick);

        this._body = new PopupMenu.PopupSwitchMenuItem('Body drift correction', false);
        this._body.connect('toggled', (_i, state) =>
            sendCmd({cmd: 'set', values: {torso_correction: state}}));
        this.menu.addMenuItem(this._body);

        this._record = new PopupMenu.PopupSwitchMenuItem('Record (training data)', false);
        this._record.connect('toggled', (_i, state) => sendCmd({cmd: 'record', on: state}));
        this.menu.addMenuItem(this._record);

        this._diag = new PopupMenu.PopupSwitchMenuItem('Diagnostics window', false);
        this._diag.connect('toggled', (_i, state) => sendCmd({cmd: 'diag', on: state}));
        this.menu.addMenuItem(this._diag);

        this._shellCur = new PopupMenu.PopupSwitchMenuItem('Cursor above menus', false);
        this._shellCur.connect('toggled', (_i, state) =>
            sendCmd({cmd: 'set', values: {cursor_in_shell: state}}));
        this.menu.addMenuItem(this._shellCur);

        this.connect('clicked', () => {
            // Soft arm/disarm is the everyday control (instant, over the socket).
            // But the socket can't reach a stopped daemon, so toggling on while it
            // is down means "bring it back" — a systemd start, not an 'arm'.
            if (this.checked)
                this._killStarts ? systemctlUser('start') : sendCmd({cmd: 'arm'});
            else
                sendCmd({cmd: 'disarm'});
        });
    }

    _paintKill(starts) {
        this._killStarts = starts;
        if (starts) {
            this._kill.label.text = '▶  Start daemon';
            this._kill.label.style = 'color: #3dd7a0; font-weight: bold;';
        } else {
            const hint = _killAccel ? `   (${_killAccel})` : '';
            this._kill.label.text = `⏻  Kill daemon — panic${hint}`;
            this._kill.label.style = 'color: #ff5c5c; font-weight: bold;';
        }
    }

    refresh(s) {
        if (!s) {
            this.checked = false;
            this.iconName = DISARMED_ICON;
            this.subtitle = 'daemon off';
            this._detail.label.text = 'gestaltd not running';
            this._diag.setToggleState(false);
            this._paintKill(true);   // offer to start it back up
            return;
        }
        this._paintKill(false);
        this.checked = !!s.armed;
        this.iconName = s.armed ? ARMED_ICON : DISARMED_ICON;
        const modeTag = s.control_mode === 'joystick' ? ' · joystick' : '';
        this.subtitle = (s.state_label || (s.armed ? 'on' : 'off')) + modeTag;
        this._joystick.setToggleState(s.control_mode === 'joystick');
        this._body.setToggleState(!!s.torso_correction);
        this._record.setToggleState(!!s.record);
        this._diag.setToggleState(!!s.diag);
        this._shellCur.setToggleState(!!s.cursor_in_shell);

        const pinch = Object.entries(s.pinch || {})
            .map(([f, st]) => `${f}:${st}`).join('  ') || '—';
        const rc = s.recal || {};
        const recal = s.recalibrate
            ? `recal ${rc.samples ?? 0} samples (${rc.residual ?? 0}px)`
            : 'recal off';
        const rec = s.rec || {};
        const recLine = s.record
            ? `\n● REC ${rec.frames ?? 0} frames · ${rec.anchors ?? 0} anchors`
            : '';
        this._detail.label.text =
            `${s.state_label || s.state}\n` +
            `${Math.round(s.fps || 0)} fps · ${s.targets ?? 0} targets · ` +
            `snap ${s.snap || '—'}\n` +
            `pitch ${s.pitch_deg ?? 0}° · hand ${s.hand ? 'yes' : 'no'}\n` +
            `pinch ${pinch}\n` +
            `${recal} · last ${s.last_action || '—'}` + recLine;
    }
});

const GestaltIndicator = GObject.registerClass(
class GestaltIndicator extends SystemIndicator {
    _init() {
        super._init();
        this._toggle = new GestaltToggle();
        this.quickSettingsItems.push(this._toggle);
    }
});

export default class GestaltExtension extends Extension {
    enable() {
        _cancellable = new Gio.Cancellable();

        // Global panic shortcut — the reliable trigger. A keyboard binding owned by
        // the shell fires no matter what the pointer is doing; ActionMode.ALL means
        // it works even with a menu/overview open. The pill just mirrors/labels it.
        this._settings = this.getSettings();
        _killAccel = prettyAccel(this._settings.get_strv('kill-hotkey')[0] || '');
        Main.wm.addKeybinding(
            'kill-hotkey', this._settings,
            Meta.KeyBindingFlags.IGNORE_AUTOREPEAT,
            Shell.ActionMode.ALL,
            () => panicKill());

        this._hud = new GestaltHUD();
        Main.panel.addToStatusArea('gestalt-hud', this._hud, 0, 'right');

        this._indicator = new GestaltIndicator();
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);

        // top-layer cursor (above shell menus)
        this._cursorInShell = false;
        this._cursor = new ShellCursor();
        Main.uiGroup.add_child(this._cursor);

        const tick = () => {
            const s = readStatus();
            this._hud.refresh(s);
            this._indicator._toggle.refresh(s);
            this._cursorInShell = !!(s && s.cursor_in_shell);
            return GLib.SOURCE_CONTINUE;
        };
        this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1000, tick);

        // fast cursor poll: read the live position file, place the ring on top
        this._cursorTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 33, () => {
            this._tickCursor();
            return GLib.SOURCE_CONTINUE;
        });
        tick();
    }

    _tickCursor() {
        if (!this._cursorInShell) {
            if (this._cursor.visible)
                this._cursor.hide();
            return;
        }
        try {
            const [ok, bytes] = GLib.file_get_contents(CURSOR_PATH);
            if (!ok) {
                this._cursor.hide();
                return;
            }
            const p = new TextDecoder().decode(bytes).trim().split(/\s+/);
            const x = parseFloat(p[0]), y = parseFloat(p[1]);
            if (!isFinite(x) || !isFinite(y)) {
                this._cursor.hide();
                return;
            }
            this._cursor.place(x, y, p[2] === '1');
            if (!this._cursor.visible)
                this._cursor.show();
            Main.uiGroup.set_child_above_sibling(this._cursor, null);   // keep on top
        } catch (_e) {
            this._cursor.hide();
        }
    }

    disable() {
        Main.wm.removeKeybinding('kill-hotkey');
        this._settings = null;
        _cancellable?.cancel();
        _cancellable = null;
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = null;
        }
        if (this._cursorTimer) {
            GLib.source_remove(this._cursorTimer);
            this._cursorTimer = null;
        }
        this._cursor?.destroy();
        this._cursor = null;
        this._hud?.destroy();
        this._hud = null;
        this._indicator?.quickSettingsItems.forEach(i => i.destroy());
        this._indicator?.destroy();
        this._indicator = null;
    }
}
