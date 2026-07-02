# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
The CV engine: owns the camera and drives one frame through the pipeline,
wiring the gestalt.* modules together. Ported from ~/code/eye_demo/nose_mouse.py,
with the PRISM/settle-lock/hard-snap replaced by the no-lock pipeline in
gestalt.pointing (DynaSpot magnetism + Steady-Clicks commit; see docs/POINTING.md).

    camera ─► input.HeadTracker (MediaPipe pose ─► 1€) ─┐
    targets.Registry (atspi + cv providers) ────────────┤
                                                         ▼
                                    pointing.Pointer (DynaSpot + velocity-gated pull)
                                                         │
    GestureRecognizer hand landmarks ─► gesture.PinchDetector (Steady-Clicks)
                                                         │
                                    gesture.Injector (uinput) + overlay.Cursor
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODELS = os.path.join(os.path.dirname(_HERE), "models")
_GESTURE_MODEL = os.path.join(_MODELS, "gesture_recognizer.task")
_POSE_MODEL = os.path.join(_MODELS, "pose_landmarker.task")
TORSO_EVERY = 2   # run Pose every N frames (torso is low-frequency) to save CPU


@dataclass
class FrameResult:
    face_ok: bool = False
    over_pitch: bool = False           # head past the pitch limit (pose unreliable)
    pitch_deg: float = 0.0
    yaw_deg: float = 0.0
    snap_role: str | None = None
    target_count: int = 0
    torso_present: bool = False        # shoulders detected (layer-3 body tracking)
    body: dict = None                  # body-drift compensator state
    cursor_speed: float = 0.0          # px/s
    catch_radius: float = 0.0          # current DynaSpot catch-radius (px)
    hand_present: bool = False
    pinch: dict = None                 # finger -> 'idle'|'ready'|'waiting'
    last_action: str | None = None     # most recent fired action (for the HUD/pill)
    recal: dict = None                 # recalibrator state (samples, gain, offset)
    still: bool = False                # head at rest (neutral re-anchoring)
    deflection: float = 0.0            # joystick deflection magnitude from neutral
    record: dict = None                # session recorder state (frames, anchors, file)
    calib: dict = None                 # calibration state (labels, target, loop)
    comfort: dict = None               # comfort envelope + follow-gain (straightness)
    endpoint: dict = None              # KTM endpoint prediction + intent (observability)
    gaze_disp: float = 1.0             # iris dispersion (low = eyes settled)
    gaze_thr: float = 0.0              # live self-calibrated fixation threshold (k×median)
    fixating: bool = False             # gaze-fixation precision cue
    cam_lit: float = 1.0               # rolling fraction of frames passing the dark gate
    monitors: dict = None              # virtual desktop + active monitor (multi-monitor)
    gesture: str = None                # top trained-classifier label (gestures mode)
    gesture_score: float = 0.0         # its confidence
    precision: bool = False            # brow-clutch precision (low-gain) mode active
    brow_lift: float = 0.0             # eyebrow height above rest (head-local units)
    brow_thr: float = 0.0              # live self-calibrated engage threshold (K×MAD)


def _scan_camera():
    """Pick the brightest working /dev/video* node (IR nodes read dark)."""
    import cv2
    best, bright = None, -1.0
    for idx in range(6):
        cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            continue
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        ok, fr = False, None
        for _ in range(5):
            ok, fr = cap.read()
            if ok and fr is not None:
                break
        if ok and fr is not None and fr.mean() > bright:
            best, bright = idx, fr.mean()
        cap.release()
    if best is None:
        raise RuntimeError("no working camera found")
    return best


class Engine:
    def __init__(self, cfg: dict, on_command=None):
        self.cfg = cfg
        self._on_command = on_command   # routes diag-window keys through daemon.handle
        import cv2  # noqa: F401  — fail loudly here if the CV stack is missing
        self._cap = None
        self._head = None
        self._grec = None
        self._torso = None             # TorsoTracker, lazy-created when enabled
        self._torso_state = None
        self._pointer = None
        self._pinch = None
        self._gesture = None
        self._det = None           # the active detector (pinch | gesture), set per-frame
        self._hold = None          # active press/drag: {action, px, py, drag, t}
        self._prev_engaged = None  # detector.engaged last frame (press-edge detection)
        self._precision = False    # brow-clutch precision (low-gain) mode, comfort only
        self._prec_t = 0.0         # when precision engaged (for the watchdog)
        self._prec_block = False   # watchdog tripped — block re-engage until brow drops
        self._gaze_dot = None      # GazeDot overlay (gaze_debug) — diagnostic, not a pointer
        self._inject = None
        self._overlay = None
        self._monitors = None
        self._targets = None
        self._tracker = None
        self._cached_targets: list[dict] = []
        self._ts = 0
        self._t_prev = None
        self._fcount = 0
        self._fps = 0.0
        self._cam_lit_ema = 1.0    # rolling fraction of frames that pass the dark gate
        self._cam_peak = 0.0       # decaying peak frame brightness (relative strobe gate)
        self._read_fails = 0       # consecutive cap.read() failures (camera-death gate)
        self._reopen_t = 0.0       # last reopen attempt while lost (2s backoff)
        self.camera_lost = False   # daemon surfaces this as the camera_lost health state
        _rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        self._cursor_file = os.path.join(_rt, "gestalt", "cursor")   # live pos for the extension
        self._fps_t = None
        self._last_action = None
        from gestalt.record import Recorder
        self._recorder = Recorder()
        self._calib = None         # Calibration state machine when calibrating
        self._calib_overlay = None
        self._diag = None          # DiagWindow when cfg["diag"] is on
        self._target_overlay = None     # TargetOverlay (AT-SPI box debug) on target_overlay
        self._last_rgb = None
        self._last_hand = None
        self.quit_requested = False

    def open(self):
        import cv2
        import mediapipe as mp  # noqa: F401
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        from gestalt.gesture import GestureDetector, Injector, PinchDetector
        from gestalt.input import HeadTracker
        from gestalt.overlay import Cursor
        from gestalt.overlay.monitors import Monitors
        from gestalt.pointing import Pointer
        from gestalt.pointing.track import TargetTracker
        from gestalt.targets import Registry

        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        self._cap = None
        self._open_camera()

        self._head = HeadTracker(self.cfg)
        self._grec = vision.GestureRecognizer.create_from_options(
            vision.GestureRecognizerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=_GESTURE_MODEL),
                running_mode=vision.RunningMode.VIDEO, num_hands=1,
                min_hand_detection_confidence=0.3,
                min_hand_presence_confidence=0.3,
                min_tracking_confidence=0.3))

        self._monitors = Monitors()                    # virtual desktop + per-monitor rects
        self._overlay = Cursor()                       # creates the window
        vw, vh = self._monitors.vw, self._monitors.vh  # the WHOLE virtual desktop
        self._pointer = Pointer(self.cfg, vw, vh, monitors=self._monitors)
        self._pinch = PinchDetector(self.cfg)
        self._gesture = GestureDetector(self.cfg)      # trained-classifier alternative
        self._det = self._pinch
        self._inject = Injector(vw, vh)                # ABS range spans both monitors

        self._targets = Registry(self.cfg)
        self._targets.start()
        self._tracker = TargetTracker(self.cfg)   # stabilizes flickering centroids

    def _open_camera(self):
        """(Re)open the capture. A pinned `camera` index forces that node (the IR
        node — `auto` deliberately skips it as it reads dark); cam_width/height set
        the resolution (match the IR sensor's native, e.g. 576x360)."""
        import cv2
        if self._cap is not None:
            self._cap.release()
        cam = self.cfg["camera"]
        cam = _scan_camera() if cam == "auto" else int(cam)
        self._cap = cv2.VideoCapture(cam, cv2.CAP_V4L2)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg["cam_width"])
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg["cam_height"])
        if self.cfg.get("cam_fps", 0) > 0:
            self._cap.set(cv2.CAP_PROP_FPS, self.cfg["cam_fps"])

    def _render_cursor(self, x, y, snapped, precision=False):
        """Show the cursor. Either the XWayland dot, OR (cursor_in_shell) hand the
        live position to the GNOME extension via RUNTIME/cursor so it can draw the
        dot ABOVE shell menus (which an XWayland window can't reach)."""
        shell = self.cfg.get("cursor_in_shell", False)
        if self._overlay is not None:
            self._overlay.set_hidden(shell)
            if not shell:
                self._overlay.move(x, y, snapped=snapped, precision=precision)
        try:
            # tmp-then-replace (same pattern as ipc.write_status): the extension
            # polls this file at 30Hz, and a read landing between an in-place
            # truncate and the write parses NaN and flickers the ring.
            tmp = self._cursor_file + ".tmp"
            with open(tmp, "w") as f:
                f.write(f"{x:.1f} {y:.1f} {1 if snapped else 0}")
            os.replace(tmp, self._cursor_file)
        except Exception:
            pass

    def _reconcile_hold(self, ps):
        """Drive the physical press/drag/release from the active detector's
        `engaged` state. START on the press edge (button/key down at the settled
        point); a click stays frozen until the cursor leaves `drag_start_px`, then
        DRAGS (real pointer follows the head); RELEASE when the gesture ends. A
        watchdog force-releases a stuck hold. Reconciling against `engaged` every
        frame is what guarantees a button is never left down."""
        if not self.cfg["gesture_hold"]:
            self._release_hold(ps.x, ps.y)
            return
        desired = self._det.engaged if self._det else None
        h = self._hold
        if desired and h is None:                          # START
            self._inject.begin(desired, ps.x, ps.y)
            self._hold = {"action": desired, "px": ps.x, "py": ps.y,
                          "drag": False, "t": time.time()}
            self._last_action = desired
        elif desired and h is not None:                    # CONTINUE (maybe drag)
            if not h["drag"] and math.hypot(ps.x - h["px"], ps.y - h["py"]) \
                    > self.cfg["drag_start_px"]:
                h["drag"] = True
            if h["drag"] and self._inject.is_click(h["action"]):
                self._inject.move_to(ps.x, ps.y)
            if time.time() - h["t"] > self.cfg["hold_timeout_s"]:
                self._release_hold(ps.x, ps.y)             # watchdog
        elif h is not None and not desired:                # END
            self._release_hold(ps.x, ps.y)

    def release_hold(self):
        """Release any held button/key at its press point (e.g. on disarm, so a
        drag in progress doesn't leave a button stuck down while paused)."""
        if self._hold is not None:
            self._release_hold(self._hold["px"], self._hold["py"])

    def _release_hold(self, x, y):
        if self._hold is None:
            return
        h = self._hold
        rx, ry = (x, y) if h["drag"] else (h["px"], h["py"])
        self._inject.end(h["action"], rx, ry)
        self._hold = None

    def _coast(self) -> FrameResult | None:
        """Dark IR frame: keep the cursor gliding (extrapolate) instead of freezing,
        so the strobe-halved framerate still renders smooth. No pose, no clicks."""
        if not self.cfg.get("coast_interp", True) or self._pointer is None:
            return None
        t = time.time()
        dt = (t - self._t_prev) if self._t_prev else 1 / 30.0
        self._t_prev = t
        ps = self._pointer.coast(self._cached_targets, dt, self.cfg["coast_predict"],
                                 self._precision)
        if ps is not None:
            self._render_cursor(ps.x, ps.y, bool(ps.snap_role), self._precision)
        return None

    def pump(self):
        """Drain the window event queue WITHOUT running the pipeline. Must be
        called every idle tick (e.g. while disarmed) too — an undrained SDL/X
        queue grows without bound and the OOM killer eventually SIGKILLs us."""
        import pygame
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                self.quit_requested = True
            elif e.type == pygame.KEYDOWN:
                self._key(e.key)

    def step(self) -> FrameResult | None:
        import cv2
        import mediapipe as mp

        # event pump (drains the queue for all pygame windows incl. the overlay).
        # Keys are handled when the diagnostics window is focused — the dev panel.
        self.pump()

        ok, frame = self._cap.read()
        if not ok:
            # ~30 straight failures = the camera died (unplugged / claimed by
            # another process); _coast can't inflate this — it only follows reads
            # that SUCCEEDED. Flag it for health and retry _open_camera on a 2s
            # backoff forever. "auto" raises while no node works — catch it and
            # stay lost until a later backoff tick finds one.
            self._read_fails += 1
            if self._read_fails >= 30:
                self.camera_lost = True
                now = time.time()
                if now - self._reopen_t > 2.0:
                    self._reopen_t = now
                    try:
                        self._open_camera()
                    except Exception:
                        pass
            time.sleep(0.01)
            return None
        self._read_fails = 0
        self.camera_lost = False
        # IR illuminators strobe — every other frame is black. Drop a frame only if
        # it's much darker than the RECENT PEAK (relative gate), so the black frames
        # are skipped at any distance while a dim far face still passes — an absolute
        # threshold ate the far frames and made tracking flicker on lean-back.
        mean = float(frame.mean())
        self._cam_peak = max(mean, self._cam_peak * 0.9)   # decaying peak of lit frames
        ratio = self.cfg.get("cam_strobe_ratio", 0.0)
        if ratio > 0.0 and self._cam_peak > 2.0 and mean < self._cam_peak * ratio:
            self._cam_lit_ema += 0.05 * (0.0 - self._cam_lit_ema)
            return self._coast()      # a strobe-OFF frame — extrapolate, don't freeze
        self._cam_lit_ema += 0.05 * (1.0 - self._cam_lit_ema)
        self._fcount += 1
        # IR nodes can deliver single-channel frames; lift dim IR contrast (CLAHE)
        # so MediaPipe gets a legible face. RGB path is untouched when normalize off.
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        if self.cfg.get("cam_normalize"):
            g = self._clahe.apply(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
            frame = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
        rgb = __import__("numpy").ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        self._ts = max(self._ts + 1, int(time.monotonic() * 1000))
        mpimg = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        t = time.time()
        dt = (t - self._t_prev) if self._t_prev else 1 / 30.0
        self._t_prev = t

        # layer 3: run Pose only while body correction is enabled (lazy-loaded).
        if self.cfg.get("torso_correction"):
            if self._torso is None:
                from gestalt.input import TorsoTracker
                self._torso = TorsoTracker(_POSE_MODEL)
            if self._fcount % TORSO_EVERY == 0:
                self._torso_state = self._torso.process(mpimg, self._ts)
        else:
            self._torso_state = None

        head = self._head.process(mpimg, self._ts, t, self._torso_state)

        # brow clutch (HOLD): precision is engaged WHILE the brows are held raised —
        # holding through a fine move is intuitive; a toggle's mental gear-flip is
        # not. Comfort-only (it's an absolute-mapping lens). A watchdog releases a
        # stuck raise and BLOCKS re-engage until the brow physically drops (else it
        # would just re-fire next frame), so you can never be stranded in slow mode.
        if self.cfg["control_mode"] != "comfort":
            self._precision = False
            self._prec_block = False
        else:
            engaged = head.brow_raised
            if not engaged:
                self._prec_block = False          # released → clear any watchdog block
            if engaged and not self._precision and not self._prec_block:
                self._prec_t = t                  # engage edge → start the watchdog
            self._precision = engaged and not self._prec_block
            if self._precision and (t - self._prec_t) > self.cfg["precision_timeout_s"]:
                self._precision = False
                self._prec_block = True           # stuck raise → block until released

        if self._fcount % 10 == 0:
            self._cached_targets = self._tracker.update(self._targets.read())

        # detect the hand BEFORE the pointer step so comfort mode can gate neutral
        # re-centring on hand-presence (hand up = aiming, never treat as rest).
        gres = self._grec.recognize_for_video(mpimg, self._ts)
        hand = gres.hand_landmarks[0] if gres.hand_landmarks else None
        # top trained-gesture label + score for hand 0 (for gesture_mode="gestures")
        gname, gscore = None, 0.0
        if gres.gestures and gres.gestures[0]:
            top = gres.gestures[0][0]
            gname, gscore = top.category_name, top.score

        self._sync_calib()
        calibrating = self._calib is not None and self._calib.active
        # calibration suppresses magnetism so the ring follows raw head aim.
        ps = self._pointer.update(head, [] if calibrating else self._cached_targets, dt,
                                  hand_present=hand is not None, precision=self._precision)

        if self.cfg["gesture_mode"] == "gestures":
            self._det = self._gesture
            fires, pinch_dbg, dists = self._gesture.update(
                gname, gscore, (ps.x, ps.y), head.speed, armed=True)
        else:
            self._det = self._pinch
            fires, pinch_dbg, dists = self._pinch.update(
                hand, (ps.x, ps.y), head.speed, armed=True)
        fire_anchor = None
        # A "commit" this frame = an atomic tap (hold off) OR the engaged state just
        # turning on (hold mode press). Unifies both for recal / record / calibrate.
        engaged = self._det.engaged
        commit = None
        if fires:
            f0 = fires[0]
            commit = (f0.action, f0.x, f0.y)
        elif engaged is not None and self._prev_engaged is None:
            commit = (engaged, ps.x, ps.y)

        if calibrating:
            tx, ty = self._calib.current()
            if self._calib_overlay is not None:
                self._calib_overlay.move_to(tx, ty)
            if commit:
                self._pointer.observe_target(tx, ty)       # high-quality label
                fire_anchor = {"action": "calibrate", "at": [round(ps.x, 1), round(ps.y, 1)],
                               "target": [round(tx, 1), round(ty, 1)], "role": "calibration"}
                self._calib.record()
                self._calib.advance()
            self._det.engaged = None                       # never hold during calibration
            self._release_hold(ps.x, ps.y)
        else:
            for f in fires:                                # tap mode: atomic clicks
                if self._inject.fire(f.action, f.x, f.y):
                    self._last_action = f.action
            self._reconcile_hold(ps)                       # hold mode: press/drag/release
            if commit:
                # a click (tap or hold-press) is a (raw-pose, intended) sample —
                # feed it to the online recalibrator (gated inside observe_click).
                self._pointer.observe_click()
                fire_anchor = {"action": commit[0],
                               "at": [round(commit[1], 1), round(commit[2], 1)]}
                cand = self._pointer.last_candidate
                if cand is not None:                # supervised label: intended target
                    fire_anchor["target"] = [cand["cx"], cand["cy"]]
                    fire_anchor["role"] = cand.get("role")
        self._prev_engaged = self._det.engaged

        self._render_cursor(ps.x, ps.y, bool(ps.snap_role), self._precision)
        self._sync_gaze_dot(head)

        # fps (rolling, every 15 frames)
        if self._fcount % 15 == 0:
            now = time.time()
            self._fps = 15 / (now - self._fps_t) if self._fps_t else 0.0
            self._fps_t = now

        # diagnostics window — created/destroyed on the cfg["diag"] toggle
        self._sync_diag(rgb, head, ps, hand, pinch_dbg, dists, self._torso_state)
        # desktop target-box overlay — created/destroyed on the cfg["target_overlay"] toggle
        self._sync_target_overlay()

        # session recorder — start/stop on the cfg["record"] toggle
        self._sync_record()
        if self._recorder.active:
            self._recorder.write(head, ps, self._torso_state, hand, fire_anchor, t)

        torso_present = bool(self._torso_state and self._torso_state.present)
        return FrameResult(
            face_ok=head.ok, over_pitch=head.over_pitch,
            pitch_deg=head.pitch_deg, yaw_deg=head.yaw_deg,
            snap_role=ps.snap_role, target_count=ps.target_count,
            torso_present=torso_present, body=head.body,
            cursor_speed=ps.speed_pxs, catch_radius=ps.catch_radius,
            hand_present=hand is not None, pinch=self._det.readiness(),
            last_action=self._last_action, recal=ps.recal,
            still=ps.still, deflection=ps.deflection, record=self._recorder.state(),
            calib=(self._calib.state() if self._calib else None), comfort=ps.comfort,
            endpoint=ps.endpoint,
            gaze_disp=head.gaze_disp, gaze_thr=head.gaze_thr, fixating=head.fixating,
            cam_lit=round(self._cam_lit_ema, 2),
            monitors=self._monitors.state() if self._monitors else None,
            gesture=gname, gesture_score=round(gscore, 2),
            precision=self._precision, brow_lift=round(head.brow_lift, 4),
            brow_thr=round(head.brow_thr, 4))

    def _key(self, key):
        """Diagnostics-window keyboard controls — the dev panel. Routes through
        the daemon's command handler so toggles sanitize/save/apply just like the
        socket. (The pill + HUD stay the stable production surfaces.)"""
        if self._on_command is None:
            return
        import pygame
        cmd = None
        if key == pygame.K_r:
            cmd = {"cmd": "record"}                       # toggle recording
        elif key == pygame.K_b:
            on = not self.cfg.get("torso_correction")
            cmd = {"cmd": "set", "values": {"torso_correction": on}}
        elif key == pygame.K_j:
            order = ["mouse", "joystick", "comfort"]      # J cycles the three modes
            cur = self.cfg.get("control_mode", "mouse")
            nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "mouse"
            cmd = {"cmd": "mode", "mode": nxt}
        elif key == pygame.K_x:
            cmd = {"cmd": "recal", "op": "reset"}
        elif key == pygame.K_e:
            cmd = {"cmd": "recal", "op": "off" if self.cfg.get("recalibrate") else "on"}
        elif key == pygame.K_l:
            cmd = {"cmd": "calibrate"}                    # toggle calibration mode
        elif key == pygame.K_c:
            cmd = {"cmd": "recenter"}
        elif key == pygame.K_a:
            cmd = {"cmd": "disarm" if self.cfg.get("armed") else "arm"}
        if cmd is not None:
            try:
                self._on_command(cmd)
            except Exception:
                pass

    def _sync_calib(self):
        want = self.cfg.get("calibrate", False)
        if want and self._calib is None:
            try:
                from gestalt.calibrate import Calibration, CalibrationOverlay
                self._calib = Calibration(self._overlay.sw, self._overlay.sh)
                self._calib_overlay = CalibrationOverlay()
                self._calib.start()
            except Exception as e:
                import sys
                sys.stderr.write(f"[gestaltd] calibration unavailable: {e}\n")
                self.cfg["calibrate"] = False
                self._calib = None
        elif not want and self._calib is not None:
            self._calib.stop()
            if self._calib_overlay is not None:
                self._calib_overlay.close()
            self._calib = self._calib_overlay = None

    def _sync_record(self):
        want = self.cfg.get("record", False)
        if want and not self._recorder.active:
            from gestalt.record import session_stamp
            self._recorder.start(session_stamp())
        elif not want and self._recorder.active:
            self._recorder.stop()

    def _sync_target_overlay(self):
        want = self.cfg.get("target_overlay", False)
        if want and self._target_overlay is None:
            try:
                from gestalt.overlay.targets_overlay import TargetOverlay
                self._target_overlay = TargetOverlay(self._monitors.vw, self._monitors.vh)
            except Exception as e:           # never let the debug overlay kill the pointer
                import sys
                sys.stderr.write(f"[gestaltd] target_overlay failed: {e}\n")
                self.cfg["target_overlay"] = False
                return
        elif not want and self._target_overlay is not None:
            self._target_overlay.close()
            self._target_overlay = None
        if self._target_overlay is not None:
            try:
                self._target_overlay.render(self._cached_targets, self._pointer.focus_id)
            except Exception:
                pass

    def _sync_gaze_dot(self, head):
        """Create/destroy the gaze debug dot on the cfg["gaze_debug"] toggle and
        position it from the raw iris offset (uncalibrated; a coherence check, not
        a pointer). Never let it kill the pipeline."""
        want = self.cfg.get("gaze_debug", False)
        if want and self._gaze_dot is None:
            try:
                from gestalt.overlay.gazedot import GazeDot
                self._gaze_dot = GazeDot()
            except Exception as e:
                import sys
                sys.stderr.write(f"[gestaltd] gaze_debug dot failed: {e}\n")
                self.cfg["gaze_debug"] = False
                return
        elif not want and self._gaze_dot is not None:
            self._gaze_dot.close()
            self._gaze_dot = None
        if self._gaze_dot is None or not head.ok:
            return
        gx, gy = head.gaze
        if self._monitors is not None:
            mx, my, mw, mh = self._monitors.active_rect()
            vw, vh = self._monitors.vw, self._monitors.vh
        else:
            mx, my, mw, mh = 0, 0, self._overlay.sw, self._overlay.sh
            vw, vh = mw, mh
        g = self.cfg["gaze_debug_gain"]
        # camera is mirrored (matches the cursor sign): looking right → dot right.
        dx = mx + mw / 2.0 - gx * g
        dy = my + mh / 2.0 + gy * g
        try:
            self._gaze_dot.move(max(0.0, min(vw - 1.0, dx)), max(0.0, min(vh - 1.0, dy)))
        except Exception:
            pass

    def _sync_diag(self, rgb, head, ps, hand, pinch_dbg, dists, torso=None):
        want = self.cfg.get("diag", False)
        if want and self._diag is None:
            try:
                from gestalt.diag import DiagWindow
                self._diag = DiagWindow()
            except Exception as e:           # never let diagnostics kill the pointer
                import sys
                sys.stderr.write(f"[gestaltd] diagnostics window failed: {e}\n")
                self.cfg["diag"] = False     # stop retrying every frame
                return
        elif not want and self._diag is not None:
            self._diag.close()
            self._diag = None
        if self._diag is not None:
            try:
                self._diag.render(rgb, head, ps, hand, pinch_dbg, dists,
                                  readiness=self._det.readiness(), torso=torso,
                                  record=self._recorder.state(),
                                  calib=(self._calib.state() if self._calib else None),
                                  recal_on=self.cfg.get("recalibrate", False),
                                  fps=self._fps, last_action=self._last_action)
            except Exception as e:
                import sys
                sys.stderr.write(f"[gestaltd] diagnostics render error: {e}\n")

    @property
    def fps(self) -> float:
        return self._fps

    def recenter(self):
        if self._pointer:
            self._pointer.recenter()
        if self._head:
            self._head.reset_body()

    def recalibrate_reset(self):
        if self._pointer:
            self._pointer.recal_reset()

    def switch_monitor(self, target):
        """Manual active-monitor switch (CLI fallback so you're never stranded)."""
        return self._pointer.switch_monitor(target) if self._pointer else None

    def refresh_layout(self) -> bool:
        """Re-query the monitor layout; if it changed (display plugged/unplugged or
        rearranged) re-derive the coordinate space so targets/cursor/clicks don't
        go stale — the layout was previously read ONCE at startup and never updated.
        Returns True if it changed."""
        if self._monitors is None or self._pointer is None:
            return False
        before = (self._monitors.vw, self._monitors.vh, tuple(self._monitors.rects))
        self._monitors.refresh()
        after = (self._monitors.vw, self._monitors.vh, tuple(self._monitors.rects))
        if before == after:
            return False
        vw, vh = self._monitors.vw, self._monitors.vh
        self._pointer.set_bounds(vw, vh, self._monitors)
        if self._inject is not None:                 # ABS range is fixed at creation
            from gestalt.gesture import Injector
            try:
                self._inject.close()
            except Exception:
                pass
            self._inject = Injector(vw, vh)
        if self._target_overlay is not None:         # recreate at the new size
            self._target_overlay.close()
            self._target_overlay = None
        return True

    def set_idle(self, idle: bool):
        """Freeze/thaw the target providers (SIGSTOP/SIGCONT). Targets are only
        consumed while armed, yet every provider poll is a synchronous D-Bus
        round trip that taxes gnome-shell and the focused app — so the daemon
        parks them across the disarm edge instead of letting them spin 24/7."""
        if self._targets:
            (self._targets.pause if idle else self._targets.resume)()

    def apply_config(self, cfg: dict):
        old_cam = {k: self.cfg.get(k) for k in ("camera", "cam_width", "cam_height", "cam_fps")}
        # the tuning knobs ride into the providers via env at SPAWN time
        # (registry.start), so a hot-set of any of them is silently ignored
        # unless the subprocesses respawn — restart on those changes too.
        prov_keys = ("providers", "provider_poll_ms", "cv_poll_ms", "cv_apps",
                     "atspi_active_only")
        old_prov = {k: self.cfg.get(k) for k in prov_keys}
        self.cfg = cfg
        for comp in (self._head, self._pointer, self._pinch, self._gesture,
                     self._targets, self._tracker):
            if comp:
                comp.apply_config(cfg)
        # camera index / resolution changed -> reopen live (lets `set camera 2` swap
        # to the IR node without a service restart).
        if self._cap is not None and any(old_cam[k] != cfg.get(k) for k in old_cam):
            self._open_camera()
        # provider set or spawn-time tuning changed -> restart + drop stale tracks
        if self._targets and any(old_prov[k] != cfg.get(k) for k in prov_keys):
            self._targets.close()
            self._targets.start()
            if self._tracker:
                self._tracker.reset()
            if not cfg.get("armed", True):     # keep respawned procs parked while idle
                self._targets.pause()

    def close(self):
        if self._inject is not None:           # never leave a button/key held down
            self._release_hold(0, 0)
        self._recorder.stop()
        if self._calib_overlay is not None:
            self._calib_overlay.close()
            self._calib_overlay = None
        if self._diag is not None:
            self._diag.close()
            self._diag = None
        if self._target_overlay is not None:
            self._target_overlay.close()
            self._target_overlay = None
        if self._gaze_dot is not None:
            self._gaze_dot.close()
            self._gaze_dot = None
        for comp in (self._targets, self._inject, self._head, self._torso):
            try:
                if comp:
                    comp.close()
            except Exception:
                pass
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
        if self._overlay:
            self._overlay.close()
