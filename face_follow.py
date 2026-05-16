#!/usr/bin/env python3
"""Face-following loop using camera tracking with selectable gaze output."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import wave
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from camera.face_detect import BOUNDARY, FaceTrack, MarkerTrack, RpicamFaceTracker, TrackedFaceFrame
from dialog_state import (
    APP_MODE_ACTIVE,
    APP_MODE_QUIZ,
    APP_MODES,
    AppController,
    ObservationDecoder,
    WorldState,
    WorldStateBuilder,
    load_quiz_config,
    load_quiz_selector_configs,
)
from robot_animation import (
    SPEECH_MOTION_DEFAULT_PITCH_DEG,
    SPEECH_MOTION_DEFAULT_TILT_DEG,
    SPEECH_MOTION_DEFAULT_YAW_DEG,
)
from robot_engine import BoardState, PendingGaze, RobotEngine, default_engine_config
from robot_motion import CHANNELS


GAZE_MODE_ANIMATION = "animation_engine"
GAZE_MODE_DIRECT = "direct_pose"
GAZE_MODES = {GAZE_MODE_ANIMATION, GAZE_MODE_DIRECT}
TARGET_MODE_FACES = "faces"
TARGET_MODE_MARKERS = "markers"
TARGET_MODES = {TARGET_MODE_FACES, TARGET_MODE_MARKERS}
TARGET_BEHAVIOR_STICKY = "sticky"
TARGET_BEHAVIOR_SCAN = "scan"
TARGET_BEHAVIORS = {TARGET_BEHAVIOR_STICKY, TARGET_BEHAVIOR_SCAN}
REACHABLE_YAW_MIN = CHANNELS["neck_rotation"].min_angle + CHANNELS["eye_leftright"].min_angle
REACHABLE_YAW_MAX = CHANNELS["neck_rotation"].max_angle + CHANNELS["eye_leftright"].max_angle
REACHABLE_PITCH_MIN = CHANNELS["neck_elevation"].min_angle + CHANNELS["eye_updown"].min_angle
REACHABLE_PITCH_MAX = CHANNELS["neck_elevation"].max_angle + CHANNELS["eye_updown"].max_angle
SUPPORTED_SOUND_EXTENSIONS = {".aac", ".aif", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}
SOUND_USB_CARD_HINTS = ("JOUNIVO", "USB-Audio", "USB Audio")
DEFAULT_SOUND_VOLUME_PERCENT = 90
DEFAULT_SPEECH_MOTION_AMPLITUDE_PERCENT = 100
MAX_SPEECH_MOTION_AMPLITUDE_PERCENT = 300
DEFAULT_QUIZ_FILE = str(Path(__file__).resolve().parent / "quiz" / "robot_quiz.yaml")
DEFAULT_QUIZ_RUNTIME_FILE = str(
    Path(__file__).resolve().parent / "quiz" / "robot_quiz_runtime.json"
)
DEFAULT_QUIZ_TEAMS_FILE = str(Path(__file__).resolve().parent / "quiz" / "robot_quiz_teams.json")
DEFAULT_QUIZ_SPEECH_FILE = str(
    Path(__file__).resolve().parent / "quiz" / "robot_quiz_baked_speech.json"
)
SOUND_PLAYER_COMMANDS = (
    ("mpg123", ("mpg123", "-q", "{path}")),
    ("ffplay", ("ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "{path}")),
    ("mpv", ("mpv", "--no-video", "--really-quiet", "{path}")),
    ("cvlc", ("cvlc", "--play-and-exit", "--quiet", "{path}")),
    ("play", ("play", "-q", "{path}")),
)


def normalize_sound_volume_percent(value: float | str) -> int:
    return int(round(min(max(float(value), 0.0), 100.0)))


def normalize_speech_motion_amplitude_percent(value: float | str) -> int:
    return int(
        round(
            min(
                max(float(value), 0.0),
                float(MAX_SPEECH_MOTION_AMPLITUDE_PERCENT),
            )
        )
    )


def sound_clip_duration_s(path: Path) -> float | None:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as wav:
                frame_rate = wav.getframerate()
                if frame_rate <= 0:
                    return None
                return wav.getnframes() / frame_rate
        except (OSError, EOFError, wave.Error):
            return None

    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    try:
        output = subprocess.check_output(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.0,
        ).strip()
        duration = float(output)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return duration if math.isfinite(duration) and duration > 0 else None


def clamp_gaze_target(target: tuple[float, float]) -> tuple[float, float]:
    yaw, pitch = target
    return (
        max(REACHABLE_YAW_MIN, min(REACHABLE_YAW_MAX, yaw)),
        max(REACHABLE_PITCH_MIN, min(REACHABLE_PITCH_MAX, pitch)),
    )


class SharedCameraMjpegServer:
    def __init__(
        self,
        camera: RpicamFaceTracker,
        *,
        port: int,
        calibration_file: str,
        settings_file: str,
        app_controller: AppController,
        quiz_file: str,
        quiz_runtime_file: str,
        quiz_teams_file: str,
        quiz_speech_file: str,
        idle_blink: bool,
        blink_interval_s: float,
        gaze_mode: str,
        target_mode: str,
        target_behavior: str,
        eyelid_offset: float,
        sound_volume_percent: int,
        speech_motion_amplitude_percent: int,
    ) -> None:
        self.camera = camera
        self.port = port
        self.calibration_file = Path(calibration_file)
        self.settings_file = Path(settings_file)
        self.app_controller = app_controller
        self.quiz_file = quiz_file
        self.quiz_runtime_file = quiz_runtime_file
        self.quiz_teams_file = quiz_teams_file
        self.quiz_speech_file = quiz_speech_file
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.socket: socket.socket | None = None
        self.app_lock = threading.Lock()
        self.world_lock = threading.Lock()
        self.world_debug: dict = {
            "visible_marker_ids": [],
            "stable_marker_ids": [],
            "decoded_answers": {},
            "start_marker_id": app_controller.config.start_marker_id,
            "start_marker_stable": False,
        }
        self.debug_lock = threading.Lock()
        self.debug_snapshot: dict = {
            "state": "starting",
            "active_id": None,
            "selected_id": None,
            "target": None,
            "sent": False,
            "faces": [],
        }
        self.calibration_lock = threading.Lock()
        self.calibration_enabled = False
        self.manual_yaw = 0.0
        self.manual_pitch = 0.0
        self.pending_manual_gaze: tuple[float, float] | None = None
        self.calibration_points: list[dict] = self._load_calibration_points()
        self.blink_lock = threading.Lock()
        self.idle_blink_enabled = idle_blink
        self.blink_interval_s = blink_interval_s
        self.gaze_lock = threading.Lock()
        self.gaze_mode = gaze_mode
        self.target_lock = threading.Lock()
        self.target_mode = target_mode
        self.target_behavior = target_behavior
        self.target_revision = 0
        self.camera.set_marker_enabled(self.marker_detection_required(target_mode))
        self.expression_lock = threading.Lock()
        self.eyelid_offset = eyelid_offset
        self.pose_lock = threading.Lock()
        self.pending_reset_pose = False
        self.reset_pose_requested_at: float | None = None
        self.reset_pose_sent_at: float | None = None
        self.system_lock = threading.Lock()
        self.halt_requested_at: float | None = None
        self.halt_spawned_at: float | None = None
        self.halt_error: str | None = None
        self.sound_dir = Path(__file__).resolve().parent / "sound"
        self.sound_metadata_cache: dict[str, tuple[int, int, float | None]] = {}
        self.sound_lock = threading.Lock()
        self.sound_process: subprocess.Popen | None = None
        self.sound_volume_percent = normalize_sound_volume_percent(sound_volume_percent)
        self.speech_motion_amplitude_percent = normalize_speech_motion_amplitude_percent(
            speech_motion_amplitude_percent
        )
        self.sound_last_result: dict = {
            "ok": None,
            "status": "idle",
        }
        self._save_runtime_settings()

    def set_debug(self, snapshot: dict) -> None:
        with self.debug_lock:
            self.debug_snapshot = snapshot

    def set_world(self, world: WorldState) -> None:
        with self.world_lock:
            self.world_debug = world.as_debug(
                stable_for_s=self.app_controller.config.stable_start_s,
            )

    def update_app(self, world: WorldState, robot, now: float) -> None:
        with self.app_lock:
            self.app_controller.update(world, robot, now)

    def app_should_run_active_follow(self) -> bool:
        with self.app_lock:
            return self.app_controller.should_run_active_follow()

    def app_preferred_target_mode(self) -> str | None:
        with self.app_lock:
            return self.app_controller.preferred_target_mode()

    def marker_detection_required(self, target_mode: str | None = None) -> bool:
        if target_mode is None:
            with self.target_lock:
                target_mode = self.target_mode
        with self.app_lock:
            return self.app_controller.marker_detection_required(target_mode)

    def app_mode_snapshot(self) -> str:
        with self.app_lock:
            return self.app_controller.mode

    def _app_state(self) -> dict:
        with self.app_lock:
            return self.app_controller.as_debug()

    def _quiz_state(self) -> dict:
        with self.app_lock:
            return self.app_controller.quiz.as_debug()

    def _world_state(self) -> dict:
        with self.world_lock:
            return dict(self.world_debug)

    def is_calibration_enabled(self) -> bool:
        with self.calibration_lock:
            return self.calibration_enabled

    def pop_pending_manual_gaze(self) -> tuple[float, float] | None:
        with self.calibration_lock:
            target = self.pending_manual_gaze
            self.pending_manual_gaze = None
            return target

    def restore_pending_manual_gaze_if_empty(self, target: tuple[float, float]) -> None:
        with self.calibration_lock:
            if self.pending_manual_gaze is None:
                self.pending_manual_gaze = target

    def pop_pending_reset_pose(self) -> bool:
        with self.pose_lock:
            pending = self.pending_reset_pose
            self.pending_reset_pose = False
            return pending

    def mark_reset_pose_sent(self) -> None:
        with self.pose_lock:
            self.reset_pose_sent_at = time.time()

    def start(self) -> None:
        if self.port <= 0 or self.thread is not None:
            return
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        if self.thread is not None:
            self.thread.join(timeout=2)
            self.thread = None
        with self.sound_lock:
            self._stop_sound_locked()

    def _serve(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket = server
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", self.port))
        server.listen(8)
        server.settimeout(0.5)
        print(f"MJPEG debug: http://0.0.0.0:{self.port}/", flush=True)
        while not self.stop_event.is_set():
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve_client, args=(conn,), daemon=True).start()

    def _serve_client(self, conn: socket.socket) -> None:
        try:
            req = conn.recv(2048)
        except Exception:
            conn.close()
            return
        path = self._request_path(req)

        if path == "/":
            html = self._index_html()
            conn.sendall(
                b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n"
                b"Content-Length: " + str(len(html)).encode() + b"\r\n\r\n" + html
            )
            conn.close()
            return

        if path.startswith("/debug"):
            with self.debug_lock:
                calibration = self._calibration_state()
                body = json.dumps(
                    {
                        **self.debug_snapshot,
                        "calibration": calibration,
                        "blink": self._blink_state(),
                        "gaze": self._gaze_state(),
                        "target_mode": self._target_state(),
                        "expression": self._expression_state(),
                        "pose": self._pose_state(),
                        "system": self._system_state(),
                        "sound": self._sound_state(),
                        "app": self._app_state(),
                        "quiz": self._quiz_state(),
                        "world": self._world_state(),
                        "settings": self._settings_state(),
                        "pi_temperature_c": read_pi_temperature_c(),
                        "served_at": time.time(),
                    },
                    indent=2,
                    sort_keys=True,
                ).encode()
            conn.sendall(
                b"HTTP/1.0 200 OK\r\n"
                b"Cache-Control: no-cache\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
            )
            conn.close()
            return

        if path.startswith("/calibration"):
            try:
                body = self._handle_calibration_request(path)
            except ValueError as exc:
                body = {"error": str(exc), **self._calibration_state()}
            self._send_json(conn, body)
            return

        if path.startswith("/gaze"):
            try:
                body = self._handle_gaze_request(path)
            except ValueError as exc:
                body = {"error": str(exc), **self._gaze_state()}
            self._send_json(conn, body)
            return

        if path.startswith("/target"):
            try:
                body = self._handle_target_request(path)
            except ValueError as exc:
                body = {"error": str(exc), **self._target_state()}
            self._send_json(conn, body)
            return

        if path.startswith("/expression"):
            try:
                body = self._handle_expression_request(path)
            except ValueError as exc:
                body = {"error": str(exc), **self._expression_state()}
            self._send_json(conn, body)
            return

        if path.startswith("/pose"):
            try:
                body = self._handle_pose_request(path)
            except ValueError as exc:
                body = {"error": str(exc), **self._pose_state()}
            self._send_json(conn, body)
            return

        if path.startswith("/system"):
            try:
                body = self._handle_system_request(path)
            except ValueError as exc:
                body = {"error": str(exc), **self._system_state()}
            self._send_json(conn, body)
            return

        if path.startswith("/app"):
            try:
                body = self._handle_app_request(path)
            except ValueError as exc:
                body = {"error": str(exc), **self._app_state()}
            self._send_json(conn, body)
            return

        if path.startswith("/quiz"):
            try:
                body = self._handle_quiz_request(path)
            except ValueError as exc:
                body = {"error": str(exc), **self._quiz_state()}
            self._send_json(conn, body)
            return

        if path.startswith("/sound"):
            try:
                body = self._handle_sound_request(path)
            except ValueError as exc:
                body = {**self._sound_state(), "ok": False, "error": str(exc)}
            self._send_json(conn, body)
            return

        if path.startswith("/blink"):
            try:
                body = self._handle_blink_request(path)
            except ValueError as exc:
                body = {"error": str(exc), **self._blink_state()}
            self._send_json(conn, body)
            return

        if not path.startswith("/stream"):
            conn.sendall(b"HTTP/1.0 404 Not Found\r\n\r\n")
            conn.close()
            return

        try:
            conn.sendall(
                b"HTTP/1.0 200 OK\r\n"
                b"Cache-Control: no-cache\r\n"
                b"Pragma: no-cache\r\n"
                b"Content-Type: multipart/x-mixed-replace; boundary=" + BOUNDARY + b"\r\n\r\n"
            )
            last_seq = -1
            while not self.stop_event.is_set():
                item = self.camera.get_debug_jpeg()
                if item is None:
                    time.sleep(0.01)
                    continue
                jpeg, seq = item
                if seq == last_seq:
                    time.sleep(0.01)
                    continue
                last_seq = seq
                payload = (
                    b"--" + BOUNDARY + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                    + jpeg + b"\r\n"
                )
                conn.sendall(payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            conn.close()

    def _request_path(self, req: bytes) -> str:
        first_line = req.split(b"\r\n", 1)[0]
        parts = first_line.split()
        if len(parts) < 2:
            return "/"
        return parts[1].decode("utf-8", errors="replace")

    def _send_json(self, conn: socket.socket, payload: dict, status: bytes = b"200 OK") -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode()
        conn.sendall(
            b"HTTP/1.0 " + status + b"\r\n"
            b"Cache-Control: no-cache\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )
        conn.close()

    def _calibration_state(self) -> dict:
        with self.calibration_lock:
            return {
                "enabled": self.calibration_enabled,
                "manual_yaw": self.manual_yaw,
                "manual_pitch": self.manual_pitch,
                "file": str(self.calibration_file),
                "points": list(self.calibration_points),
            }

    def calibration_points_snapshot(self) -> list[dict]:
        with self.calibration_lock:
            return list(self.calibration_points)

    def blink_state_snapshot(self) -> tuple[bool, float]:
        with self.blink_lock:
            return self.idle_blink_enabled, self.blink_interval_s

    def gaze_mode_snapshot(self) -> str:
        with self.gaze_lock:
            return self.gaze_mode

    def target_mode_snapshot(self) -> str:
        with self.target_lock:
            return self.target_mode

    def target_behavior_snapshot(self) -> str:
        with self.target_lock:
            return self.target_behavior

    def target_revision_snapshot(self) -> int:
        with self.target_lock:
            return self.target_revision

    def expression_state_snapshot(self) -> float:
        with self.expression_lock:
            return self.eyelid_offset

    def _expression_state(self) -> dict:
        with self.expression_lock:
            return {"eyelid_offset": self.eyelid_offset}

    def _pose_state(self) -> dict:
        with self.pose_lock:
            return {
                "reset_pending": self.pending_reset_pose,
                "reset_requested_at": self.reset_pose_requested_at,
                "reset_sent_at": self.reset_pose_sent_at,
            }

    def _handle_pose_request(self, path: str) -> dict:
        parsed = urlsplit(path)
        params = parse_qs(parsed.query)
        action = params.get("action", ["state"])[0]

        if action == "state":
            return self._pose_state()

        if action in {"reset", "reset_pose", "reset-pose"}:
            now = time.time()
            with self.pose_lock:
                self.pending_reset_pose = True
                self.reset_pose_requested_at = now
            with self.calibration_lock:
                self.pending_manual_gaze = None
            return {"status": "queued", **self._pose_state()}

        return {"error": f"unknown pose action: {action}", **self._pose_state()}

    def _system_state(self) -> dict:
        with self.system_lock:
            return {
                "halt_requested_at": self.halt_requested_at,
                "halt_spawned_at": self.halt_spawned_at,
                "halt_error": self.halt_error,
            }

    def _handle_system_request(self, path: str) -> dict:
        parsed = urlsplit(path)
        params = parse_qs(parsed.query)
        action = params.get("action", ["state"])[0]

        if action == "state":
            return self._system_state()

        if action == "halt":
            now = time.time()
            with self.system_lock:
                self.halt_requested_at = now
                self.halt_spawned_at = None
                self.halt_error = None
            threading.Thread(target=self._run_halt_after_response, daemon=True).start()
            return {"status": "queued", **self._system_state()}

        return {"error": f"unknown system action: {action}", **self._system_state()}

    def _handle_app_request(self, path: str) -> dict:
        parsed = urlsplit(path)
        params = parse_qs(parsed.query)
        action = params.get("action", ["state"])[0]

        if action == "state":
            return self._app_state()

        if action in {"set_mode", "mode"}:
            mode = params.get("mode", [None])[0]
            if mode not in APP_MODES:
                raise ValueError(f"app mode must be one of: {', '.join(sorted(APP_MODES))}")
            with self.app_lock:
                self.app_controller.set_mode(mode, time.monotonic())
            self.camera.set_marker_enabled(self.marker_detection_required())
            self._save_runtime_settings()
            return self._app_state()

        if action in {"set_auto_start", "auto_start"}:
            enabled = params.get("enabled", [None])[0]
            if enabled is None:
                raise ValueError("missing enabled")
            with self.app_lock:
                self.app_controller.auto_start_quiz = enabled not in {"0", "false", "off", "no"}
                self.app_controller.last_event = "auto_start_changed"
            self.camera.set_marker_enabled(self.marker_detection_required())
            self._save_runtime_settings()
            return self._app_state()

        return {"error": f"unknown app action: {action}", **self._app_state()}

    def _handle_quiz_request(self, path: str) -> dict:
        parsed = urlsplit(path)
        params = parse_qs(parsed.query)
        action = params.get("action", ["state"])[0]
        now = time.monotonic()

        if action == "state":
            return self._quiz_state()

        if action == "start":
            with self.app_lock:
                self.app_controller.start_quiz(now)
            self.camera.set_marker_enabled(self.marker_detection_required())
            self._save_runtime_settings()
            return self._quiz_state()

        if action == "stop":
            with self.app_lock:
                self.app_controller.stop_quiz(now)
            self.camera.set_marker_enabled(self.marker_detection_required())
            self._save_runtime_settings()
            return self._quiz_state()

        if action == "reset":
            with self.app_lock:
                self.app_controller.reset_quiz(now)
            return self._quiz_state()

        return {"error": f"unknown quiz action: {action}", **self._quiz_state()}

    def _run_halt_after_response(self) -> None:
        time.sleep(0.35)
        try:
            subprocess.Popen(
                ("sudo", "-n", "halt"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            with self.system_lock:
                self.halt_error = str(exc)
        else:
            with self.system_lock:
                self.halt_spawned_at = time.time()

    def _settings_values(self) -> dict:
        with self.gaze_lock:
            gaze_mode = self.gaze_mode
        with self.target_lock:
            target_mode = self.target_mode
            target_behavior = self.target_behavior
        with self.blink_lock:
            idle_blink_enabled = self.idle_blink_enabled
            blink_interval_s = self.blink_interval_s
        with self.expression_lock:
            eyelid_offset = self.eyelid_offset
        with self.sound_lock:
            sound_volume_percent = self.sound_volume_percent
            speech_motion_amplitude_percent = self.speech_motion_amplitude_percent
        with self.app_lock:
            app_mode = self.app_controller.mode
            auto_start_quiz = self.app_controller.auto_start_quiz
        return {
            "gaze_mode": gaze_mode,
            "target_mode": target_mode,
            "target_behavior": target_behavior,
            "idle_blink_enabled": idle_blink_enabled,
            "blink_interval_s": blink_interval_s,
            "eyelid_offset": eyelid_offset,
            "sound_volume_percent": sound_volume_percent,
            "speech_motion_amplitude_percent": speech_motion_amplitude_percent,
            "app_mode": app_mode,
            "auto_start_quiz": auto_start_quiz,
            "quiz_file": self.quiz_file,
            "quiz_runtime_file": self.quiz_runtime_file,
            "quiz_teams_file": self.quiz_teams_file,
            "quiz_speech_file": self.quiz_speech_file,
        }

    def _settings_state(self) -> dict:
        return {
            "file": str(self.settings_file),
            **self._settings_values(),
        }

    def _save_runtime_settings(self) -> None:
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": time.time(),
            **self._settings_values(),
        }
        self.settings_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def _handle_expression_request(self, path: str) -> dict:
        parsed = urlsplit(path)
        params = parse_qs(parsed.query)
        action = params.get("action", ["state"])[0]

        if action == "state":
            return self._expression_state()

        if action == "set":
            if "eyelid_offset" not in params:
                raise ValueError("missing eyelid_offset")
            eyelid_offset = float(params["eyelid_offset"][0])
            if not -30.0 <= eyelid_offset <= 30.0:
                raise ValueError("eyelid_offset must be between -30 and 30")
            with self.expression_lock:
                self.eyelid_offset = eyelid_offset
            self._save_runtime_settings()
            return self._expression_state()

        return {"error": f"unknown expression action: {action}", **self._expression_state()}

    def _sound_clip_metadata(self, path: Path) -> dict:
        try:
            stat = path.stat()
        except OSError:
            return {"duration_s": None}
        cache_key = str(path)
        cached = self.sound_metadata_cache.get(cache_key)
        if cached is not None:
            cached_mtime_ns, cached_size, cached_duration_s = cached
            if cached_mtime_ns == stat.st_mtime_ns and cached_size == stat.st_size:
                return {"duration_s": cached_duration_s}

        duration_s = sound_clip_duration_s(path)
        if duration_s is not None:
            duration_s = round(duration_s, 3)
        self.sound_metadata_cache[cache_key] = (stat.st_mtime_ns, stat.st_size, duration_s)
        return {"duration_s": duration_s}

    def _sound_clips(self) -> list[dict]:
        try:
            entries = list(self.sound_dir.iterdir())
        except OSError:
            return []
        clips = []
        for path in entries:
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SOUND_EXTENSIONS:
                continue
            clips.append(
                {
                    "id": path.name,
                    "label": path.stem.replace("_", " ").replace("-", " "),
                    "file": path.name,
                    **self._sound_clip_metadata(path),
                }
            )
        return sorted(clips, key=lambda item: item["label"].lower())

    def _sound_clip_path(self, clip: str) -> Path:
        if not clip or "/" in clip or "\\" in clip:
            raise ValueError(f"invalid sound clip: {clip}")
        path = self.sound_dir / clip
        if path.suffix.lower() not in SUPPORTED_SOUND_EXTENSIONS:
            raise ValueError(f"unsupported sound clip type: {clip}")
        if not path.is_file():
            raise ValueError(f"sound file not found: {path}")
        return path

    def _sound_state_locked(self) -> dict:
        running = self.sound_process is not None and self.sound_process.poll() is None
        state = {
            **self.sound_last_result,
            "clips": self._sound_clips(),
            "sound_dir": str(self.sound_dir),
            "running": running,
            "volume_percent": self.sound_volume_percent,
            "speech_motion_amplitude_percent": self.speech_motion_amplitude_percent,
        }
        now = time.time()
        started_at = state.get("started_at")
        duration_s = state.get("duration_s")
        if isinstance(started_at, (int, float)):
            state["elapsed_s"] = round(max(0.0, now - started_at), 3)
            if isinstance(duration_s, (int, float)):
                expected_end_at = started_at + duration_s
                state["expected_end_at"] = expected_end_at
                state["remaining_s"] = round(max(0.0, expected_end_at - now), 3)
        if self.sound_process is not None and not running:
            state["returncode"] = self.sound_process.returncode
            if state.get("status") == "playing":
                if self.sound_process.returncode == 0:
                    state["status"] = "finished"
                else:
                    state["ok"] = False
                    state["status"] = "failed"
        return state

    def _sound_state(self) -> dict:
        with self.sound_lock:
            return self._sound_state_locked()

    def is_sound_running(self) -> bool:
        with self.sound_lock:
            return self.sound_process is not None and self.sound_process.poll() is None

    def play_sound_clip(self, clip: str | None) -> dict:
        if not clip:
            return {**self._sound_state(), "ok": True, "status": "no_clip"}
        try:
            return self._play_sound_clip(clip)
        except ValueError as exc:
            return {**self._sound_state(), "ok": False, "error": str(exc)}

    def speech_motion_amplitude_snapshot(self) -> int:
        with self.sound_lock:
            return self.speech_motion_amplitude_percent

    def _handle_sound_request(self, path: str) -> dict:
        parsed = urlsplit(path)
        params = parse_qs(parsed.query)
        action = params.get("action", ["state"])[0]

        if action == "state":
            return self._sound_state()

        if action == "play":
            clips = self._sound_clips()
            default_clip = clips[0]["id"] if clips else ""
            clip = params.get("clip", [default_clip])[0]
            return self._play_sound_clip(clip)

        if action in {"set_volume", "volume"}:
            if "volume" not in params:
                raise ValueError("missing volume")
            volume_percent = normalize_sound_volume_percent(params["volume"][0])
            with self.sound_lock:
                self.sound_volume_percent = volume_percent
            self._set_alsa_volume(self._preferred_alsa_output(), volume_percent)
            self._save_runtime_settings()
            return self._sound_state()

        if action in {"set_speech_motion", "speech_motion"}:
            if "amplitude" not in params:
                raise ValueError("missing amplitude")
            amplitude_percent = normalize_speech_motion_amplitude_percent(params["amplitude"][0])
            with self.sound_lock:
                self.speech_motion_amplitude_percent = amplitude_percent
            self._save_runtime_settings()
            return self._sound_state()

        if any(action == item["id"] for item in self._sound_clips()):
            return self._play_sound_clip(action)

        return {**self._sound_state(), "ok": False, "error": f"unknown sound action: {action}"}

    def _preferred_alsa_output(self) -> dict | None:
        cards_path = Path("/proc/asound/cards")
        try:
            lines = cards_path.read_text().splitlines()
        except OSError:
            return None

        current: dict | None = None
        for line in lines:
            match = re.match(r"\s*(\d+)\s+\[([^\]]+)\]:\s+(.+)", line)
            if match:
                current = {
                    "card_num": match.group(1),
                    "card_id": match.group(2).strip(),
                    "summary": line.strip(),
                    "detail": "",
                }
                continue
            if current is None:
                continue
            current["detail"] = line.strip()
            haystack = " ".join(str(value) for value in current.values())
            if any(hint in haystack for hint in SOUND_USB_CARD_HINTS):
                current["device"] = f"plughw:CARD={current['card_id']},DEV=0"
                return current

        return None

    def _set_alsa_volume(self, output: dict | None, volume_percent: int | None = None) -> None:
        if output is None or shutil.which("amixer") is None:
            return
        if volume_percent is None:
            with self.sound_lock:
                volume_percent = self.sound_volume_percent
        try:
            subprocess.run(
                [
                    "amixer",
                    "-c",
                    str(output["card_num"]),
                    "set",
                    "PCM",
                    f"{volume_percent}%",
                    "unmute",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=0.5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    def _stop_sound_locked(self) -> None:
        process = self.sound_process
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            process.terminate()
        try:
            process.wait(timeout=0.35)
            time.sleep(0.03)
            return
        except subprocess.TimeoutExpired:
            pass

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            process.kill()
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass
        time.sleep(0.03)

    def _sound_player_commands(self, path: Path) -> list[tuple[str, list[str]]]:
        commands = []
        output = self._preferred_alsa_output()
        ffmpeg = shutil.which("ffmpeg")
        aplay = shutil.which("aplay")
        if output is not None and ffmpeg is not None and aplay is not None:
            self._set_alsa_volume(output)
            commands.append(
                (
                    f"ffmpeg/aplay:{output['device']}",
                    [
                        "/bin/sh",
                        "-c",
                        '"$1" -hide_banner -loglevel error -i "$2" -f wav - | "$3" -q -D "$4"',
                        "sound-play",
                        ffmpeg,
                        str(path),
                        aplay,
                        str(output["device"]),
                    ],
                )
            )

        for name, template in SOUND_PLAYER_COMMANDS:
            executable = shutil.which(name)
            if executable is None:
                continue
            command = [
                executable if part == name else str(path) if part == "{path}" else part
                for part in template
            ]
            commands.append((name, command))
        return commands

    def _play_sound_clip(self, clip: str) -> dict:
        path = self._sound_clip_path(clip)
        metadata = self._sound_clip_metadata(path)

        commands = self._sound_player_commands(path)
        if not commands:
            tried = ", ".join(name for name, _ in SOUND_PLAYER_COMMANDS)
            raise ValueError(f"no supported audio player found; tried: {tried}")

        errors = []
        with self.sound_lock:
            self._stop_sound_locked()

            for player, command in commands:
                try:
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                except OSError as exc:
                    errors.append(f"{player}: {exc}")
                    continue

                self.sound_process = process
                self.sound_last_result = {
                    "ok": True,
                    "status": "playing",
                    "clip": clip,
                    "file": str(path),
                    "player": player,
                    "pid": process.pid,
                    "started_at": time.time(),
                    **metadata,
                }
                return self._sound_state_locked()

            self.sound_last_result = {
                "ok": False,
                "status": "failed",
                "clip": clip,
                "file": str(path),
                "errors": errors,
            }
            return self._sound_state_locked()

    def _gaze_state(self) -> dict:
        with self.gaze_lock:
            return {"mode": self.gaze_mode}

    def _target_state(self) -> dict:
        with self.target_lock:
            return {
                "mode": self.target_mode,
                "behavior": self.target_behavior,
                "revision": self.target_revision,
            }

    def _handle_target_request(self, path: str) -> dict:
        parsed = urlsplit(path)
        params = parse_qs(parsed.query)
        action = params.get("action", ["state"])[0]

        if action == "state":
            return self._target_state()

        if action in {"set", "look"}:
            mode = params.get("mode", [None])[0]
            if mode is None:
                use_markers = params.get("markers", [None])[0]
                if use_markers is not None:
                    mode = (
                        TARGET_MODE_MARKERS
                        if use_markers not in {"0", "false", "off", "no"}
                        else TARGET_MODE_FACES
                    )
            if mode not in TARGET_MODES:
                raise ValueError(f"target mode must be one of: {', '.join(sorted(TARGET_MODES))}")
            behavior = params.get("behavior", [None])[0]
            if behavior is None:
                behavior = TARGET_BEHAVIOR_SCAN if action == "look" else self.target_behavior
            if behavior not in TARGET_BEHAVIORS:
                raise ValueError(
                    f"target behavior must be one of: {', '.join(sorted(TARGET_BEHAVIORS))}"
                )
            if action == "look":
                with self.app_lock:
                    self.app_controller.set_mode(APP_MODE_ACTIVE, time.monotonic())
            with self.target_lock:
                if action == "look" or self.target_mode != mode or self.target_behavior != behavior:
                    self.target_revision += 1
                self.target_mode = mode
                self.target_behavior = behavior
            self.camera.set_marker_enabled(self.marker_detection_required(mode))
            self._save_runtime_settings()
            state = self._target_state()
            if action == "look":
                state["app"] = self._app_state()
            return state

        return {"error": f"unknown target action: {action}", **self._target_state()}

    def _handle_gaze_request(self, path: str) -> dict:
        parsed = urlsplit(path)
        params = parse_qs(parsed.query)
        action = params.get("action", ["state"])[0]

        if action == "state":
            return self._gaze_state()

        if action == "set":
            mode = params.get("mode", [None])[0]
            if mode is None:
                use_animation = params.get("animation", [None])[0]
                if use_animation is not None:
                    mode = (
                        GAZE_MODE_ANIMATION
                        if use_animation not in {"0", "false", "off", "no"}
                        else GAZE_MODE_DIRECT
                    )
            if mode not in GAZE_MODES:
                raise ValueError(f"gaze mode must be one of: {', '.join(sorted(GAZE_MODES))}")
            with self.gaze_lock:
                self.gaze_mode = mode
            self._save_runtime_settings()
            return self._gaze_state()

        return {"error": f"unknown gaze action: {action}", **self._gaze_state()}

    def _blink_state(self) -> dict:
        with self.blink_lock:
            return {
                "idle_blink_enabled": self.idle_blink_enabled,
                "blink_interval_s": self.blink_interval_s,
            }

    def _handle_blink_request(self, path: str) -> dict:
        parsed = urlsplit(path)
        params = parse_qs(parsed.query)
        action = params.get("action", ["state"])[0]

        if action == "state":
            return self._blink_state()

        if action == "set":
            enabled = params.get("enabled", [None])[0]
            interval = params.get("interval", [None])[0]
            with self.blink_lock:
                if enabled is not None:
                    self.idle_blink_enabled = enabled not in {"0", "false", "off", "no"}
                if interval is not None:
                    blink_interval_s = float(interval)
                    if blink_interval_s <= 0:
                        raise ValueError("blink interval must be > 0")
                    self.blink_interval_s = blink_interval_s
            self._save_runtime_settings()
            return self._blink_state()

        return {"error": f"unknown blink action: {action}", **self._blink_state()}

    def _handle_calibration_request(self, path: str) -> dict:
        parsed = urlsplit(path)
        params = parse_qs(parsed.query)
        action = params.get("action", ["state"])[0]

        if action == "state":
            return self._calibration_state()

        if action == "enable":
            enabled = params.get("enabled", ["1"])[0] not in {"0", "false", "off", "no"}
            with self.calibration_lock:
                self.calibration_enabled = enabled
            return self._calibration_state()

        if action == "set":
            yaw = float(params.get("yaw", [self.manual_yaw])[0])
            pitch = float(params.get("pitch", [self.manual_pitch])[0])
            yaw, pitch = clamp_gaze_target((yaw, pitch))
            apply_gaze = params.get("apply", ["0"])[0] in {"1", "true", "yes"}
            with self.calibration_lock:
                self.manual_yaw = yaw
                self.manual_pitch = pitch
                self.calibration_enabled = True
                if apply_gaze:
                    self.pending_manual_gaze = (yaw, pitch)
            return self._calibration_state()

        if action == "record":
            yaw = float(params.get("yaw", [self.manual_yaw])[0])
            pitch = float(params.get("pitch", [self.manual_pitch])[0])
            yaw, pitch = clamp_gaze_target((yaw, pitch))
            point = self._record_calibration_point(yaw, pitch)
            result = self._calibration_state()
            result["recorded"] = point
            return result

        if action == "clear":
            with self.calibration_lock:
                self.calibration_points = []
                self._save_calibration_points_locked()
            result = self._calibration_state()
            result["cleared"] = True
            return result

        return {"error": f"unknown calibration action: {action}", **self._calibration_state()}

    def _record_calibration_point(self, yaw: float, pitch: float) -> dict:
        with self.debug_lock:
            snapshot = dict(self.debug_snapshot)
        selected_id = snapshot.get("selected_id") or snapshot.get("active_id")
        selected_kind = snapshot.get("selected_kind") or snapshot.get("active_kind")
        faces = snapshot.get("faces") or []
        markers = snapshot.get("markers") or []
        item = None
        item_kind = selected_kind
        if selected_id is not None:
            source_items = markers if selected_kind == TARGET_MODE_MARKERS else faces
            item = next((entry for entry in source_items if entry.get("id") == selected_id), None)
        if item is None and faces:
            item = faces[0]
            item_kind = TARGET_MODE_FACES
        if item is None and markers:
            item = markers[0]
            item_kind = TARGET_MODE_MARKERS
        if item is None:
            raise ValueError("no target available to record")

        point = {
            "timestamp": time.time(),
            "frame_seq": snapshot.get("frame_seq"),
            "target_kind": item_kind,
            "target_id": item.get("id"),
            "marker_id": item.get("marker_id") if item_kind == TARGET_MODE_MARKERS else None,
            "marker_track_id": item.get("id") if item_kind == TARGET_MODE_MARKERS else None,
            "face_id": item.get("id") if item_kind == TARGET_MODE_FACES else None,
            "eye_center": item.get("eye_center") or item.get("center"),
            "eye_center_norm": item.get("eye_center_norm") or item.get("center_norm"),
            "face_center": item.get("center") if item_kind == TARGET_MODE_FACES else None,
            "face_center_norm": item.get("center_norm") if item_kind == TARGET_MODE_FACES else None,
            "center": item.get("center"),
            "center_norm": item.get("center_norm"),
            "bbox": item.get("bbox"),
            "yaw": yaw,
            "pitch": pitch,
        }
        with self.calibration_lock:
            self.manual_yaw = yaw
            self.manual_pitch = pitch
            self.calibration_enabled = True
            self.calibration_points.append(point)
            self._save_calibration_points_locked()
        return point

    def _load_calibration_points(self) -> list[dict]:
        if not self.calibration_file.exists():
            return []
        try:
            data = json.loads(self.calibration_file.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(data, dict) and isinstance(data.get("points"), list):
            return data["points"]
        if isinstance(data, list):
            return data
        return []

    def _save_calibration_points_locked(self) -> None:
        self.calibration_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": time.time(),
            "points": self.calibration_points,
        }
        self.calibration_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def _index_html(self) -> bytes:
        return b"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Face Follow Debug</title>
<style>
html,body{margin:0;height:100%;background:#111;color:#e8e8e8;font:14px system-ui,sans-serif}
main{height:100%;display:grid;grid-template-columns:minmax(0,1fr) 380px}
.video{min-width:0;background:#000;display:grid;place-items:center}
img{width:100%;height:100%;object-fit:contain}
.status{position:fixed;top:10px;right:10px;z-index:10;display:flex;gap:8px;align-items:center;background:rgba(15,15,15,.86);border:1px solid #3a3a3a;border-radius:4px;padding:6px 9px;box-shadow:0 2px 12px rgba(0,0,0,.35)}
.status span{color:#aeb4bd}
.status strong{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#8ef0a1}
.status.warm strong{color:#ffd166}
.status.hot strong{color:#ff6b6b}
aside{border-left:1px solid #333;background:#181818;overflow:auto}
h1{font-size:16px;margin:12px 14px}
button,input{font:inherit}
button{background:#2f6fed;color:white;border:0;border-radius:4px;padding:7px 10px;margin:4px 4px 4px 0}
button.secondary{background:#333}
button.danger{background:#a52727}
label{display:block;margin:8px 14px;color:#c9c9c9}
.row{display:grid;grid-template-columns:120px 1fr;gap:8px;padding:4px 14px;border-top:1px solid #252525}
.k{color:#9ca3af}
.v{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.panel{border-top:1px solid #333;padding:8px 0}
.controls{padding:4px 14px}
.top-controls{display:flex;align-items:center;gap:8px;padding:0 14px 8px}
.sound-list{display:flex;flex-wrap:wrap;gap:6px}
.sound-list button{margin:0}
.sound-empty{color:#9ca3af}
.gaze-pad{position:relative;margin:10px 14px;height:220px;background:#080808;border:1px solid #444;touch-action:none;cursor:crosshair}
.gaze-pad:before,.gaze-pad:after{content:"";position:absolute;background:#333}
.gaze-pad:before{left:50%;top:0;bottom:0;width:1px}
.gaze-pad:after{top:50%;left:0;right:0;height:1px}
.gaze-marker{position:absolute;width:14px;height:14px;border:2px solid #ff4fd8;border-radius:50%;transform:translate(-50%,-50%);box-shadow:0 0 8px #ff4fd8;pointer-events:none}
.gaze-axis{position:absolute;color:#888;font-size:11px;pointer-events:none}
.gaze-axis.left{left:6px;top:50%;transform:translateY(-50%)}
.gaze-axis.right{right:6px;top:50%;transform:translateY(-50%)}
.gaze-axis.up{top:5px;left:50%;transform:translateX(-50%)}
.gaze-axis.down{bottom:5px;left:50%;transform:translateX(-50%)}
pre{margin:12px 14px 18px;padding:10px;background:#0b0b0b;border:1px solid #333;white-space:pre-wrap}
@media(max-width:900px){main{grid-template-columns:1fr;grid-template-rows:60vh auto}aside{border-left:0;border-top:1px solid #333}}
</style>
</head>
<body>
<main>
  <div class="status" id="piStatus"><span>Pi temp</span><strong id="piTemp">-</strong></div>
  <section class="video"><img src="/stream" alt="Annotated camera stream"></section>
  <aside>
    <h1>Face Follow Debug</h1>
    <div class="top-controls"><button id="haltSystem" class="danger">sudo halt</button><span class="v" id="systemState">-</span></div>
    <div class="row"><div class="k">state</div><div class="v" id="state">-</div></div>
    <div class="row"><div class="k">frame</div><div class="v" id="frame">-</div></div>
    <div class="row"><div class="k">age ms</div><div class="v" id="age">-</div></div>
    <div class="row"><div class="k">process ms</div><div class="v" id="processMs">-</div></div>
    <div class="row"><div class="k">active</div><div class="v" id="active">-</div></div>
    <div class="row"><div class="k">target</div><div class="v" id="target">-</div></div>
    <div class="row"><div class="k">target source</div><div class="v" id="targetSource">-</div></div>
    <div class="row"><div class="k">sent</div><div class="v" id="sent">-</div></div>
    <div class="panel">
      <h1>App</h1>
      <div class="controls">
        <button type="button" data-app-mode="active">Active</button>
        <button type="button" data-app-mode="idle" class="secondary">Idle</button>
        <button type="button" id="startQuiz" class="secondary">Start quiz</button>
        <button type="button" id="stopQuiz" class="secondary">Stop quiz</button>
      </div>
      <label><input id="autoStartQuiz" type="checkbox"> Start quiz from marker</label>
      <div class="row"><div class="k">mode</div><div class="v" id="appMode">-</div></div>
      <div class="row"><div class="k">phase</div><div class="v" id="appPhase">-</div></div>
      <div class="row"><div class="k">markers</div><div class="v" id="worldMarkers">-</div></div>
    </div>
    <div class="panel">
      <h1>Quiz</h1>
      <div class="controls"><button type="button" id="resetQuiz" class="secondary">Reset quiz</button></div>
      <div class="row"><div class="k">quiz</div><div class="v" id="quizName">-</div></div>
      <div class="row"><div class="k">registered</div><div class="v" id="quizRegistered">-</div></div>
      <div class="row"><div class="k">question</div><div class="v" id="quizQuestion">-</div></div>
      <div class="row"><div class="k">accepting</div><div class="v" id="quizAccepting">-</div></div>
      <div class="row"><div class="k">answers</div><div class="v" id="quizAnswers">-</div></div>
      <div class="row"><div class="k">missing</div><div class="v" id="quizMissing">-</div></div>
      <div class="row"><div class="k">scores</div><div class="v" id="quizScores">-</div></div>
    </div>
    <div class="panel">
      <h1>Target</h1>
      <div class="controls">
        <button type="button" id="lookFaces">Faces</button>
        <button type="button" id="lookMarkers" class="secondary">Markers</button>
      </div>
      <label><input id="markerTarget" type="checkbox"> Look at markers</label>
      <div class="row"><div class="k">mode</div><div class="v" id="targetMode">-</div></div>
      <div class="row"><div class="k">behavior</div><div class="v" id="targetBehavior">-</div></div>
    </div>
    <div class="panel">
      <h1>Gaze Output</h1>
      <label><input id="animationGaze" type="checkbox" checked> Animation engine</label>
      <div class="row"><div class="k">mode</div><div class="v" id="gazeMode">-</div></div>
    </div>
    <div class="panel">
      <h1>Robot</h1>
      <div class="controls"><button id="resetPose" class="secondary">Reset Pose</button></div>
      <div class="row"><div class="k">pose</div><div class="v" id="poseState">-</div></div>
    </div>
    <div class="panel">
      <h1>Expression</h1>
      <div class="row"><div class="k">brow height</div><div class="v"><input id="eyelidOffset" type="number" min="-30" max="30" step="0.5" value="-2.0"></div></div>
      <div class="row"><div class="k">state</div><div class="v" id="expressionState">-</div></div>
    </div>
    <div class="panel">
      <h1>Blink</h1>
      <label><input id="idleBlink" type="checkbox"> Idle blink</label>
      <div class="row"><div class="k">interval s</div><div class="v"><input id="blinkInterval" type="number" min="0.5" step="0.5" value="4.0"></div></div>
      <div class="row"><div class="k">state</div><div class="v" id="blinkState">-</div></div>
    </div>
    <div class="panel">
      <h1>Sound</h1>
      <div class="row"><div class="k">volume</div><div class="v"><input id="soundVolume" type="range" min="0" max="100" step="1" value="90"> <span id="soundVolumeValue">90%</span></div></div>
      <div class="row"><div class="k">head move</div><div class="v"><input id="speechMotionAmplitude" type="range" min="0" max="300" step="5" value="100"> <span id="speechMotionAmplitudeValue">100%</span></div></div>
      <div class="controls sound-list" id="soundList"></div>
      <div class="row"><div class="k">state</div><div class="v" id="soundState">-</div></div>
    </div>
    <div class="panel">
      <h1>Calibration</h1>
      <label><input id="manualMode" type="checkbox"> Direct from gaze pad</label>
      <div class="gaze-pad" id="gazePad">
        <div class="gaze-axis left">yaw -60</div><div class="gaze-axis right">yaw +60</div>
        <div class="gaze-axis up">pitch +30</div><div class="gaze-axis down">pitch -30</div>
        <div class="gaze-marker" id="gazeMarker"></div>
      </div>
      <div class="row"><div class="k">manual yaw</div><div class="v" id="yawValue">0.0</div></div>
      <div class="row"><div class="k">manual pitch</div><div class="v" id="pitchValue">0.0</div></div>
      <div class="controls">
        <button id="apply">Apply gaze</button><button id="record">Record</button>
        <button id="clearCalibration" class="secondary">Clear points</button>
      </div>
      <div class="row"><div class="k">file</div><div class="v" id="calFile">-</div></div>
      <div class="row"><div class="k">points</div><div class="v" id="calCount">0</div></div>
      <pre id="points">[]</pre>
    </div>
    <pre id="raw">{}</pre>
  </aside>
</main>
<script>
function fmt(n,d=1){return Number.isFinite(n)?n.toFixed(d):"-";}
function fmtDuration(n){return Number.isFinite(n)?`${n.toFixed(1)}s`:"-";}
function renderPiTemp(value){
  const status=document.getElementById("piStatus");
  const temp=document.getElementById("piTemp");
  status.classList.remove("warm","hot");
  if(!Number.isFinite(value)){
    temp.textContent="-";
    return;
  }
  temp.textContent=`${fmt(value)} C`;
  if(value >= 75) status.classList.add("hot");
  else if(value >= 65) status.classList.add("warm");
}
const manualMode=document.getElementById("manualMode");
const markerTarget=document.getElementById("markerTarget");
const lookFaces=document.getElementById("lookFaces");
const lookMarkers=document.getElementById("lookMarkers");
const animationGaze=document.getElementById("animationGaze");
const autoStartQuiz=document.getElementById("autoStartQuiz");
const startQuiz=document.getElementById("startQuiz");
const stopQuiz=document.getElementById("stopQuiz");
const resetQuiz=document.getElementById("resetQuiz");
const resetPose=document.getElementById("resetPose");
const haltSystem=document.getElementById("haltSystem");
const eyelidOffset=document.getElementById("eyelidOffset");
const idleBlink=document.getElementById("idleBlink");
const blinkInterval=document.getElementById("blinkInterval");
const soundList=document.getElementById("soundList");
const soundVolume=document.getElementById("soundVolume");
const soundVolumeValue=document.getElementById("soundVolumeValue");
const speechMotionAmplitude=document.getElementById("speechMotionAmplitude");
const speechMotionAmplitudeValue=document.getElementById("speechMotionAmplitudeValue");
const gazePad=document.getElementById("gazePad");
const gazeMarker=document.getElementById("gazeMarker");
let manualYaw=0;
let manualPitch=0;
let soundVolumeTimer=null;
let speechMotionAmplitudeTimer=null;
let renderedSoundClips="";
const yawMin=-60, yawMax=60, pitchMin=-30, pitchMax=30;
function manualValues(){return {yaw: manualYaw, pitch: manualPitch};}
function updateManual(yaw,pitch){
  manualYaw=Math.max(yawMin,Math.min(yawMax,yaw));
  manualPitch=Math.max(pitchMin,Math.min(pitchMax,pitch));
  document.getElementById("yawValue").textContent=fmt(manualYaw);
  document.getElementById("pitchValue").textContent=fmt(manualPitch);
  const x=(manualYaw-yawMin)/(yawMax-yawMin)*100;
  const y=(pitchMax-manualPitch)/(pitchMax-pitchMin)*100;
  gazeMarker.style.left=`${x}%`;
  gazeMarker.style.top=`${y}%`;
}
function padTarget(ev){
  const r=gazePad.getBoundingClientRect();
  const x=Math.max(0,Math.min(1,(ev.clientX-r.left)/r.width));
  const y=Math.max(0,Math.min(1,(ev.clientY-r.top)/r.height));
  const yaw=yawMin+x*(yawMax-yawMin);
  const pitch=pitchMax-y*(pitchMax-pitchMin);
  return {yaw: Math.round(yaw*2)/2, pitch: Math.round(pitch*2)/2};
}
async function cal(action, extra={}){
  const p=new URLSearchParams({action, ...extra});
  const r=await fetch(`/calibration?${p}`,{cache:"no-store"});
  const d=await r.json();
  renderCalibration(d);
  return d;
}
async function blink(action, extra={}){
  const p=new URLSearchParams({action, ...extra});
  const r=await fetch(`/blink?${p}`,{cache:"no-store"});
  const d=await r.json();
  renderBlink(d);
  return d;
}
async function gaze(action, extra={}){
  const p=new URLSearchParams({action, ...extra});
  const r=await fetch(`/gaze?${p}`,{cache:"no-store"});
  const d=await r.json();
  renderGaze(d);
  return d;
}
async function app(action, extra={}){
  const p=new URLSearchParams({action, ...extra});
  const r=await fetch(`/app?${p}`,{cache:"no-store"});
  const d=await r.json();
  renderApp(d);
  return d;
}
async function quizControl(action, extra={}){
  const p=new URLSearchParams({action, ...extra});
  const r=await fetch(`/quiz?${p}`,{cache:"no-store"});
  const d=await r.json();
  renderQuiz(d);
  return d;
}
function renderApp(a){
  if(!a)return;
  document.getElementById("appMode").textContent=a.mode || "-";
  document.getElementById("appPhase").textContent=a.phase || "-";
  autoStartQuiz.checked=!!a.auto_start_quiz;
  for(const button of document.querySelectorAll("button[data-app-mode]")){
    button.classList.toggle("secondary", button.dataset.appMode !== a.mode);
  }
}
function renderWorld(w){
  if(!w)return;
  const visible=(w.visible_marker_ids || []).join(",") || "-";
  const stable=(w.stable_marker_ids || []).join(",") || "-";
  document.getElementById("worldMarkers").textContent=`seen ${visible}; stable ${stable}`;
}
function renderQuiz(q){
  if(!q)return;
  document.getElementById("quizName").textContent=q.name || "-";
  const registered=q.registered_player_names || q.registered_player_ids || [];
  document.getElementById("quizRegistered").textContent=registered.length
    ? `${registered.join(", ")} (${q.registered_count || registered.length}/${q.player_count || "?"})`
    : (q.registration_open ? "waiting" : "-");
  const questionIndex=Number.isFinite(q.question_index) ? q.question_index + 1 : null;
  document.getElementById("quizQuestion").textContent=questionIndex ? `${questionIndex}/${q.question_count || "-"} ${q.phase || ""}` : (q.phase || "-");
  document.getElementById("quizAccepting").textContent=q.accepting_answers ? "yes" : "no";
  const answers=q.locked_answers || {};
  const missing=q.missing_players || [];
  const total=Object.keys(answers).length;
  const locked=total ? Math.max(0,total-missing.length) : 0;
  document.getElementById("quizAnswers").textContent=total ? `${locked}/${total}` : "-";
  document.getElementById("quizMissing").textContent=missing.length ? missing.join(", ") : (q.nudge_text || "-");
  const scores=q.scores || {};
  document.getElementById("quizScores").textContent=Object.keys(scores).length
    ? Object.entries(scores).map(([player,score])=>`${player} ${score}`).join(", ")
    : "-";
}
function renderGaze(g){
  if(!g)return;
  animationGaze.checked=g.mode !== "direct_pose";
  document.getElementById("gazeMode").textContent=g.mode || "-";
}
async function pose(action, extra={}){
  const p=new URLSearchParams({action, ...extra});
  const r=await fetch(`/pose?${p}`,{cache:"no-store"});
  const d=await r.json();
  renderPose(d);
  return d;
}
function renderPose(p){
  if(!p)return;
  const el=document.getElementById("poseState");
  if(p.reset_pending){
    el.textContent="reset queued";
  }else if(Number.isFinite(p.reset_sent_at)){
    el.textContent=`reset ${new Date(p.reset_sent_at * 1000).toLocaleTimeString()}`;
  }else{
    el.textContent="idle";
  }
}
async function system(action, extra={}){
  const p=new URLSearchParams({action, ...extra});
  const r=await fetch(`/system?${p}`,{cache:"no-store"});
  const d=await r.json();
  renderSystem(d);
  return d;
}
function renderSystem(s){
  if(!s)return;
  const el=document.getElementById("systemState");
  if(s.halt_error){
    el.textContent=`halt error: ${s.halt_error}`;
  }else if(Number.isFinite(s.halt_spawned_at)){
    el.textContent=`halt sent ${new Date(s.halt_spawned_at * 1000).toLocaleTimeString()}`;
  }else if(Number.isFinite(s.halt_requested_at)){
    el.textContent="halt queued";
  }else{
    el.textContent="-";
  }
}
async function target(action, extra={}){
  const p=new URLSearchParams({action, ...extra});
  const r=await fetch(`/target?${p}`,{cache:"no-store"});
  const d=await r.json();
  renderTarget(d);
  return d;
}
function renderTarget(t){
  if(!t)return;
  markerTarget.checked=t.mode === "markers";
  document.getElementById("targetMode").textContent=t.mode || "-";
  document.getElementById("targetBehavior").textContent=t.behavior || "-";
  lookFaces.classList.toggle("secondary", t.mode !== "faces");
  lookMarkers.classList.toggle("secondary", t.mode !== "markers");
}
async function lookAt(mode){
  const d=await target("look",{mode,behavior:"scan"});
  if(d.app)renderApp(d.app);
  return d;
}
async function expression(action, extra={}){
  const p=new URLSearchParams({action, ...extra});
  const r=await fetch(`/expression?${p}`,{cache:"no-store"});
  const d=await r.json();
  renderExpression(d);
  return d;
}
function expressionValues(){
  return {eyelid_offset: eyelidOffset.value || "-2.0"};
}
function renderExpression(e){
  if(!e)return;
  if(Number.isFinite(e.eyelid_offset) && document.activeElement !== eyelidOffset){
    eyelidOffset.value=fmt(e.eyelid_offset);
  }
  document.getElementById("expressionState").textContent=`offset ${fmt(e.eyelid_offset)}`;
}
function blinkValues(){
  return {
    enabled: idleBlink.checked ? "1" : "0",
    interval: blinkInterval.value || "4.0",
  };
}
function renderBlink(b){
  if(!b)return;
  idleBlink.checked=!!b.idle_blink_enabled;
  if(Number.isFinite(b.blink_interval_s) && document.activeElement !== blinkInterval){
    blinkInterval.value=fmt(b.blink_interval_s);
  }
  document.getElementById("blinkState").textContent=`${b.idle_blink_enabled ? "on" : "off"}, ${fmt(b.blink_interval_s)} s`;
}
async function sound(action, extra={}){
  const p=new URLSearchParams({action, ...extra});
  const r=await fetch(`/sound?${p}`,{cache:"no-store"});
  const d=await r.json();
  renderSound(d);
  return d;
}
function renderSound(s){
  if(!s)return;
  renderSoundClips(s.clips || []);
  if(Number.isFinite(s.volume_percent) && document.activeElement !== soundVolume){
    soundVolume.value=Math.round(s.volume_percent);
  }
  soundVolumeValue.textContent=`${soundVolume.value}%`;
  if(Number.isFinite(s.speech_motion_amplitude_percent) && document.activeElement !== speechMotionAmplitude){
    speechMotionAmplitude.value=Math.round(s.speech_motion_amplitude_percent);
  }
  speechMotionAmplitudeValue.textContent=`${speechMotionAmplitude.value}%`;
  for(const button of soundList.querySelectorAll("button[data-clip]")){
    button.classList.toggle("secondary", button.dataset.clip !== s.clip);
  }
  const el=document.getElementById("soundState");
  const volumeText=Number.isFinite(s.volume_percent) ? `, vol ${Math.round(s.volume_percent)}%` : "";
  const timeText=Number.isFinite(s.remaining_s)
    ? `, ${fmtDuration(s.remaining_s)} left`
    : Number.isFinite(s.duration_s)
      ? `, ${fmtDuration(s.duration_s)}`
      : "";
  if(s.ok === false){
    el.textContent=`error: ${s.error || s.status || "failed"}`;
  }else if(s.running){
    el.textContent=`playing ${s.clip || "-"} via ${s.player || "-"}${volumeText}${timeText}`;
  }else{
    el.textContent=`${s.status || "idle"}${volumeText}${timeText}`;
  }
}
function renderSoundClips(clips){
  const key=JSON.stringify(clips.map((clip)=>clip.id));
  if(key === renderedSoundClips)return;
  renderedSoundClips=key;
  soundList.replaceChildren();
  if(!clips.length){
    const empty=document.createElement("span");
    empty.className="sound-empty";
    empty.textContent="No sound files";
    soundList.appendChild(empty);
    return;
  }
  for(const clip of clips){
    const button=document.createElement("button");
    button.type="button";
    button.dataset.clip=clip.id;
    const durationText=Number.isFinite(clip.duration_s) ? ` (${fmtDuration(clip.duration_s)})` : "";
    button.title=`${clip.file || clip.id}${durationText}`;
    button.textContent=clip.label || clip.id;
    soundList.appendChild(button);
  }
}
function updateSoundVolumeLabel(){
  soundVolumeValue.textContent=`${soundVolume.value}%`;
}
function sendSoundVolume(){
  sound("set_volume",{volume:soundVolume.value || "90"}).catch(()=>{
    document.getElementById("soundState").textContent="volume failed";
  });
}
function updateSpeechMotionAmplitudeLabel(){
  speechMotionAmplitudeValue.textContent=`${speechMotionAmplitude.value}%`;
}
function sendSpeechMotionAmplitude(){
  sound("set_speech_motion",{amplitude:speechMotionAmplitude.value || "100"}).catch(()=>{
    document.getElementById("soundState").textContent="motion failed";
  });
}
function renderCalibration(c){
  if(!c)return;
  manualMode.checked=!!c.enabled;
  if(Number.isFinite(c.manual_yaw) && Number.isFinite(c.manual_pitch)){
    updateManual(c.manual_yaw,c.manual_pitch);
  }
  document.getElementById("calFile").textContent=c.file || "-";
  document.getElementById("calCount").textContent=(c.points || []).length;
  document.getElementById("points").textContent=JSON.stringify(c.points || [],null,2);
}
async function tick(){
  try{
    const r=await fetch("/debug",{cache:"no-store"});
    const d=await r.json();
    document.getElementById("state").textContent=d.state || "-";
    document.getElementById("frame").textContent=`seq=${d.frame_seq ?? "-"} faces=${d.visible_faces ?? 0}/${d.detections ?? 0} markers=${d.visible_markers ?? 0}/${d.marker_detections ?? 0}`;
    document.getElementById("age").textContent=fmt(d.frame_age_ms);
    document.getElementById("processMs").textContent=fmt(d.processing_ms);
    document.getElementById("active").textContent=`active=${d.active_id ?? "-"} selected=${d.selected_id ?? "-"}`;
    document.getElementById("target").textContent=d.target ? `yaw=${fmt(d.target.yaw)} pitch=${fmt(d.target.pitch)}` : "-";
    document.getElementById("targetSource").textContent=d.target_source || "-";
    document.getElementById("sent").textContent=d.sent ? `yes, ${fmt(d.last_sent_age_ms)} ms ago` : `no, ${fmt(d.last_sent_age_ms)} ms ago`;
    renderPiTemp(d.pi_temperature_c);
    renderTarget(d.target_mode);
    renderGaze(d.gaze);
    renderPose(d.pose);
    renderSystem(d.system);
    renderExpression(d.expression);
    renderBlink(d.blink);
    renderSound(d.sound);
    renderApp(d.app);
    renderQuiz(d.quiz);
    renderWorld(d.world);
    renderCalibration(d.calibration);
    document.getElementById("raw").textContent=JSON.stringify(d,null,2);
  }catch(e){
    document.getElementById("state").textContent="debug fetch failed";
  }
}
lookFaces.addEventListener("click",()=>lookAt("faces").catch(()=>{
  document.getElementById("targetMode").textContent="faces failed";
}));
lookMarkers.addEventListener("click",()=>lookAt("markers").catch(()=>{
  document.getElementById("targetMode").textContent="markers failed";
}));
markerTarget.addEventListener("change",()=>target("set",{mode:markerTarget.checked ? "markers" : "faces"}));
animationGaze.addEventListener("change",()=>gaze("set",{mode:animationGaze.checked ? "animation_engine" : "direct_pose"}));
document.querySelectorAll("button[data-app-mode]").forEach((button)=>{
  button.addEventListener("click",()=>app("set_mode",{mode:button.dataset.appMode}).catch(()=>{
    document.getElementById("appPhase").textContent="mode failed";
  }));
});
autoStartQuiz.addEventListener("change",()=>app("set_auto_start",{enabled:autoStartQuiz.checked ? "1" : "0"}));
startQuiz.addEventListener("click",()=>quizControl("start").catch(()=>{
  document.getElementById("quizQuestion").textContent="start failed";
}));
stopQuiz.addEventListener("click",()=>quizControl("stop").catch(()=>{
  document.getElementById("quizQuestion").textContent="stop failed";
}));
resetQuiz.addEventListener("click",()=>quizControl("reset").catch(()=>{
  document.getElementById("quizQuestion").textContent="reset failed";
}));
resetPose.addEventListener("click",()=>pose("reset").catch(()=>{
  document.getElementById("poseState").textContent="reset failed";
}));
haltSystem.addEventListener("click",()=>{
  if(!confirm("Shut down Raspberry Pi with sudo halt?"))return;
  haltSystem.disabled=true;
  system("halt").catch(()=>{
    document.getElementById("systemState").textContent="halt failed";
    haltSystem.disabled=false;
  });
});
eyelidOffset.addEventListener("change",()=>expression("set",expressionValues()));
idleBlink.addEventListener("change",()=>blink("set",blinkValues()));
blinkInterval.addEventListener("change",()=>blink("set",blinkValues()));
soundList.addEventListener("click",(ev)=>{
  const button=ev.target.closest("button[data-clip]");
  if(!button)return;
  sound("play",{clip:button.dataset.clip}).catch(()=>{
    document.getElementById("soundState").textContent="play failed";
  });
});
soundVolume.addEventListener("input",()=>{
  updateSoundVolumeLabel();
  clearTimeout(soundVolumeTimer);
  soundVolumeTimer=setTimeout(sendSoundVolume,150);
});
soundVolume.addEventListener("change",()=>{
  updateSoundVolumeLabel();
  clearTimeout(soundVolumeTimer);
  sendSoundVolume();
});
speechMotionAmplitude.addEventListener("input",()=>{
  updateSpeechMotionAmplitudeLabel();
  clearTimeout(speechMotionAmplitudeTimer);
  speechMotionAmplitudeTimer=setTimeout(sendSpeechMotionAmplitude,150);
});
speechMotionAmplitude.addEventListener("change",()=>{
  updateSpeechMotionAmplitudeLabel();
  clearTimeout(speechMotionAmplitudeTimer);
  sendSpeechMotionAmplitude();
});
manualMode.addEventListener("change",()=>cal("enable",{enabled:manualMode.checked ? "1" : "0"}));
gazePad.addEventListener("pointerdown",(ev)=>{
  const target=padTarget(ev);
  updateManual(target.yaw,target.pitch);
  cal("set",{...target,apply:"1"}).catch(()=>{});
});
document.getElementById("apply").addEventListener("click",()=>cal("set",{...manualValues(),apply:"1"}));
document.getElementById("record").addEventListener("click",()=>cal("record",manualValues()));
document.getElementById("clearCalibration").addEventListener("click",()=>{
  if(confirm("Are you sure?")){
    cal("clear").catch(()=>{});
  }
});
updateManual(0,0);
setInterval(tick,250);
tick();
</script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Track one face and drive gaze through the animation engine."
    )
    parser.add_argument("--port", default="/dev/ttyAMA0")
    parser.add_argument("--baudrate", type=int, default=57_600)
    parser.add_argument("--width", type=int, default=1296)
    parser.add_argument("--height", type=int, default=972)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--threshold", type=float, default=0.6)
    parser.add_argument("--track-max-missed", type=int, default=12)
    parser.add_argument("--track-max-distance", type=float, default=0.7)
    parser.add_argument("--track-smoothing", type=float, default=0.65)
    parser.add_argument("--horizontal-fov", type=float, default=62.0)
    parser.add_argument("--vertical-fov", type=float, default=49.0)
    parser.add_argument("--yaw-scale", type=float, default=1.0)
    parser.add_argument("--pitch-scale", type=float, default=1.0)
    parser.add_argument("--max-yaw", type=float, default=60.0)
    parser.add_argument("--max-pitch", type=float, default=30.0)
    parser.add_argument("--send-hz", type=float, default=15.0)
    parser.add_argument("--min-angle-delta", type=float, default=1.0)
    parser.add_argument(
        "--gaze-mode",
        choices=sorted(GAZE_MODES),
        default=GAZE_MODE_ANIMATION,
    )
    parser.add_argument(
        "--target-mode",
        choices=sorted(TARGET_MODES),
        default=TARGET_MODE_FACES,
    )
    parser.add_argument(
        "--target-behavior",
        choices=sorted(TARGET_BEHAVIORS),
        default=TARGET_BEHAVIOR_STICKY,
    )
    parser.add_argument("--target-scan-interval", type=float, default=2.0)
    parser.add_argument("--gaze-ms", type=float, default=350.0)
    parser.add_argument("--gaze-sample-ms", type=float, default=50.0)
    parser.add_argument("--gaze-blink-threshold", type=float, default=999.0)
    parser.add_argument("--idle-blink", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--blink-interval", type=float, default=4.0)
    parser.add_argument("--feedback-silence", type=float, default=0.20)
    parser.add_argument("--done-fallback", type=float, default=0.60)
    parser.add_argument("--eyelid-offset", type=float, default=-2.0)
    parser.add_argument("--sound-volume", type=int, default=DEFAULT_SOUND_VOLUME_PERCENT)
    parser.add_argument(
        "--speech-motion-amplitude",
        type=int,
        default=DEFAULT_SPEECH_MOTION_AMPLITUDE_PERCENT,
    )
    parser.add_argument("--app-mode", choices=sorted(APP_MODES), default=APP_MODE_ACTIVE)
    parser.add_argument("--auto-start-quiz", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--quiz-file", default=DEFAULT_QUIZ_FILE)
    parser.add_argument("--quiz-runtime-file", default=DEFAULT_QUIZ_RUNTIME_FILE)
    parser.add_argument("--quiz-teams-file", default=DEFAULT_QUIZ_TEAMS_FILE)
    parser.add_argument("--quiz-speech-file", default=DEFAULT_QUIZ_SPEECH_FILE)
    parser.add_argument("--loop-sleep", type=float, default=0.003)
    parser.add_argument("--mjpeg-port", type=int, default=8080)
    parser.add_argument("--debug-jpeg-quality", type=int, default=80)
    parser.add_argument("--calibration-file", default="face_follow_calibration.json")
    parser.add_argument("--settings-file", default="face_follow_settings.json")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def load_runtime_settings(settings_file: str) -> dict:
    path = Path(settings_file)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not load settings file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"settings file {path} must contain a JSON object")
    return data


def apply_runtime_settings(args: argparse.Namespace) -> None:
    settings = load_runtime_settings(args.settings_file)
    if not settings:
        return

    gaze_mode = settings.get("gaze_mode")
    if gaze_mode is not None:
        if gaze_mode not in GAZE_MODES:
            raise ValueError(f"settings gaze_mode must be one of: {', '.join(sorted(GAZE_MODES))}")
        args.gaze_mode = gaze_mode

    target_mode = settings.get("target_mode")
    if target_mode is not None:
        if target_mode in TARGET_MODES:
            args.target_mode = target_mode

    target_behavior = settings.get("target_behavior")
    if target_behavior is not None:
        if target_behavior in TARGET_BEHAVIORS:
            args.target_behavior = target_behavior

    idle_blink_enabled = settings.get("idle_blink_enabled")
    if idle_blink_enabled is not None:
        if not isinstance(idle_blink_enabled, bool):
            raise ValueError("settings idle_blink_enabled must be a boolean")
        args.idle_blink = idle_blink_enabled

    blink_interval_s = settings.get("blink_interval_s")
    if blink_interval_s is not None:
        args.blink_interval = float(blink_interval_s)

    eyelid_offset = settings.get("eyelid_offset")
    if eyelid_offset is not None:
        args.eyelid_offset = float(eyelid_offset)

    sound_volume_percent = settings.get("sound_volume_percent")
    if sound_volume_percent is not None:
        args.sound_volume = normalize_sound_volume_percent(sound_volume_percent)

    speech_motion_amplitude_percent = settings.get("speech_motion_amplitude_percent")
    if speech_motion_amplitude_percent is not None:
        args.speech_motion_amplitude = normalize_speech_motion_amplitude_percent(
            speech_motion_amplitude_percent
        )

    app_mode = settings.get("app_mode")
    if app_mode is not None:
        if app_mode not in APP_MODES:
            raise ValueError(f"settings app_mode must be one of: {', '.join(sorted(APP_MODES))}")
        args.app_mode = app_mode

    auto_start_quiz = settings.get("auto_start_quiz")
    if auto_start_quiz is not None:
        if not isinstance(auto_start_quiz, bool):
            raise ValueError("settings auto_start_quiz must be a boolean")
        args.auto_start_quiz = auto_start_quiz

    quiz_file = settings.get("quiz_file")
    if quiz_file is not None:
        args.quiz_file = str(quiz_file)

    quiz_runtime_file = settings.get("quiz_runtime_file")
    if quiz_runtime_file is not None:
        args.quiz_runtime_file = str(quiz_runtime_file)

    quiz_teams_file = settings.get("quiz_teams_file")
    if quiz_teams_file is not None:
        args.quiz_teams_file = str(quiz_teams_file)

    quiz_speech_file = settings.get("quiz_speech_file")
    if quiz_speech_file is not None:
        args.quiz_speech_file = str(quiz_speech_file)


def clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def read_pi_temperature_c() -> float | None:
    thermal_path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        raw = thermal_path.read_text().strip()
        value = float(raw)
        if value > 200:
            value /= 1000.0
        return round(value, 1)
    except (OSError, ValueError):
        pass

    try:
        output = subprocess.check_output(
            ["vcgencmd", "measure_temp"],
            text=True,
            timeout=0.2,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None

    if "=" in output:
        output = output.split("=", 1)[1]
    output = output.split("'", 1)[0]
    try:
        return round(float(output), 1)
    except ValueError:
        return None


def choose_face(frame: TrackedFaceFrame, active_id: int | None) -> FaceTrack | None:
    visible = frame.visible_tracks
    if not visible:
        return None

    if active_id is not None:
        for face in visible:
            if face.track_id == active_id:
                return face

    cx = frame.width / 2.0
    cy = frame.height / 2.0

    def score(face: FaceTrack) -> float:
        x, y, w, h = face.smoothed_bbox
        fx, fy = face.center
        distance = math.hypot((fx - cx) / frame.width, (fy - cy) / frame.height)
        area = (w * h) / max(frame.width * frame.height, 1)
        return face.score + area * 3.0 - distance * 0.8

    return max(visible, key=score)


def choose_marker(frame: TrackedFaceFrame, active_id: int | None) -> MarkerTrack | None:
    visible = frame.visible_marker_tracks
    if not visible:
        return None

    if active_id is not None:
        for marker in visible:
            if marker.track_id == active_id:
                return marker

    cx = frame.width / 2.0
    cy = frame.height / 2.0

    def score(marker: MarkerTrack) -> float:
        x, y, w, h = marker.smoothed_bbox
        mx, my = marker.center
        distance = math.hypot((mx - cx) / frame.width, (my - cy) / frame.height)
        area = (w * h) / max(frame.width * frame.height, 1)
        return area * 3.0 - distance * 0.8

    return max(visible, key=score)


def choose_scanning_face(
    frame: TrackedFaceFrame,
    active_id: int | None,
    *,
    now: float,
    last_switch_at: float,
    interval_s: float,
) -> tuple[FaceTrack | None, float]:
    visible = frame.visible_tracks
    if not visible:
        return None, last_switch_at
    current = next((face for face in visible if face.track_id == active_id), None)
    if current is not None and (len(visible) == 1 or now - last_switch_at < interval_s):
        return current, last_switch_at
    if current is None:
        return choose_face(frame, None), now
    ordered = sorted(visible, key=lambda face: (face.center[0], face.track_id))
    index = next((idx for idx, face in enumerate(ordered) if face.track_id == current.track_id), -1)
    if index < 0:
        return choose_face(frame, None), now
    return ordered[(index + 1) % len(ordered)], now


def choose_scanning_marker(
    frame: TrackedFaceFrame,
    active_id: int | None,
    *,
    now: float,
    last_switch_at: float,
    interval_s: float,
) -> tuple[MarkerTrack | None, float]:
    visible = frame.visible_marker_tracks
    if not visible:
        return None, last_switch_at
    current = next((marker for marker in visible if marker.track_id == active_id), None)
    if current is not None and (len(visible) == 1 or now - last_switch_at < interval_s):
        return current, last_switch_at
    if current is None:
        return choose_marker(frame, None), now
    ordered = sorted(visible, key=lambda marker: (marker.center[0], marker.track_id))
    index = next(
        (idx for idx, marker in enumerate(ordered) if marker.track_id == current.track_id),
        -1,
    )
    if index < 0:
        return choose_marker(frame, None), now
    return ordered[(index + 1) % len(ordered)], now


def frame_point_to_gaze(
    point: tuple[float, float],
    frame: TrackedFaceFrame,
    *,
    horizontal_fov: float,
    vertical_fov: float,
    yaw_scale: float,
    pitch_scale: float,
    max_yaw: float,
    max_pitch: float,
) -> tuple[float, float]:
    cx, cy = point
    norm_x = (cx - frame.width / 2.0) / max(frame.width / 2.0, 1.0)
    norm_y = (cy - frame.height / 2.0) / max(frame.height / 2.0, 1.0)
    yaw = norm_x * horizontal_fov * 0.5 * yaw_scale
    pitch = -norm_y * vertical_fov * 0.5 * pitch_scale
    return clamp(yaw, -max_yaw, max_yaw), clamp(pitch, -max_pitch, max_pitch)


def face_to_gaze(
    face: FaceTrack,
    frame: TrackedFaceFrame,
    *,
    horizontal_fov: float,
    vertical_fov: float,
    yaw_scale: float,
    pitch_scale: float,
    max_yaw: float,
    max_pitch: float,
) -> tuple[float, float]:
    return frame_point_to_gaze(
        face.eye_center,
        frame,
        horizontal_fov=horizontal_fov,
        vertical_fov=vertical_fov,
        yaw_scale=yaw_scale,
        pitch_scale=pitch_scale,
        max_yaw=max_yaw,
        max_pitch=max_pitch,
    )


def calibrated_frame_point_to_gaze(
    image_point: tuple[float, float],
    frame: TrackedFaceFrame,
    calibration_points: list[dict],
    *,
    fallback: tuple[float, float],
    max_yaw: float,
    max_pitch: float,
    neighbors: int = 4,
) -> tuple[tuple[float, float], str]:
    usable: list[tuple[float, float, float, float]] = []
    for point in calibration_points:
        xy = point.get("eye_center_norm") or point.get("center_norm")
        yaw = point.get("yaw")
        pitch = point.get("pitch")
        if not (
            isinstance(xy, list)
            and len(xy) == 2
            and isinstance(yaw, (int, float))
            and isinstance(pitch, (int, float))
        ):
            continue
        usable.append((float(xy[0]), float(xy[1]), float(yaw), float(pitch)))

    if len(usable) < 2:
        return fallback, "fov_fallback"

    ex, ey = image_point
    nx = (ex - frame.width / 2.0) / max(frame.width / 2.0, 1.0)
    ny = (ey - frame.height / 2.0) / max(frame.height / 2.0, 1.0)
    ranked = sorted(
        (
            (math.hypot(nx - px, ny - py), px, py, yaw, pitch)
            for px, py, yaw, pitch in usable
        ),
        key=lambda item: item[0],
    )
    nearest = ranked[:max(1, min(neighbors, len(ranked)))]
    if nearest[0][0] < 1e-6:
        _, _px, _py, yaw, pitch = nearest[0]
        return (
            clamp(yaw, -max_yaw, max_yaw),
            clamp(pitch, -max_pitch, max_pitch),
        ), "calibrated_exact"

    weighted_yaw = 0.0
    weighted_pitch = 0.0
    total_weight = 0.0
    for distance, _px, _py, yaw, pitch in nearest:
        weight = 1.0 / max(distance, 1e-6)
        weighted_yaw += yaw * weight
        weighted_pitch += pitch * weight
        total_weight += weight

    return (
        clamp(weighted_yaw / total_weight, -max_yaw, max_yaw),
        clamp(weighted_pitch / total_weight, -max_pitch, max_pitch),
    ), f"calibrated_idw_{len(nearest)}"


def calibrated_frame_face_to_gaze(
    face: FaceTrack,
    frame: TrackedFaceFrame,
    calibration_points: list[dict],
    *,
    fallback: tuple[float, float],
    max_yaw: float,
    max_pitch: float,
    neighbors: int = 4,
) -> tuple[tuple[float, float], str]:
    return calibrated_frame_point_to_gaze(
        face.eye_center,
        frame,
        calibration_points,
        fallback=fallback,
        max_yaw=max_yaw,
        max_pitch=max_pitch,
        neighbors=neighbors,
    )


def should_send(
    now: float,
    target: tuple[float, float],
    last_target: tuple[float, float] | None,
    last_sent_at: float,
    *,
    send_interval: float,
    min_angle_delta: float,
) -> bool:
    if now - last_sent_at < send_interval:
        return False
    if last_target is None:
        return True
    yaw, pitch = target
    last_yaw, last_pitch = last_target
    return math.hypot(yaw - last_yaw, pitch - last_pitch) >= min_angle_delta


def schedule_gaze_if_ready(
    engine: RobotEngine,
    target: tuple[float, float],
    *,
    dwell_ms: float,
) -> bool:
    target = clamp_gaze_target(target)
    yaw, pitch = target
    engine.schedule_gaze(PendingGaze(yaw=yaw, pitch=pitch, dwell_ms=dwell_ms), replace_pending=True)
    engine.maybe_start_next()
    return True


def send_direct_gaze_if_ready(engine: RobotEngine, target: tuple[float, float]) -> bool:
    if engine.state != BoardState.IDLE:
        return False
    target = clamp_gaze_target(target)
    engine.send_direct_gaze(*target)
    return True


def drive_gaze_if_ready(
    engine: RobotEngine,
    target: tuple[float, float],
    *,
    dwell_ms: float,
    gaze_mode: str,
    animation_layers_active: bool = False,
) -> bool:
    try:
        has_pending_animation_layers = bool(
            engine.blink_events or engine.neck_stretch_events or engine.speech_motion_events
        )
        if gaze_mode == GAZE_MODE_DIRECT and not animation_layers_active and not has_pending_animation_layers:
            return send_direct_gaze_if_ready(engine, target)
        return schedule_gaze_if_ready(engine, target, dwell_ms=dwell_ms)
    except ValueError as exc:
        print(f"gaze target rejected: {exc}", flush=True)
        return False


def schedule_blink_if_ready(engine: RobotEngine) -> bool:
    if engine.state != BoardState.IDLE or engine.gaze_events or engine.blink_events:
        return False
    if not engine.schedule_blink_at(time.monotonic(), reason="idle", force=True):
        return False
    engine.maybe_start_next()
    return True


def schedule_speech_motion_if_ready(engine: RobotEngine) -> bool:
    if engine.speech_motion_events:
        return False
    engine.schedule_speech_motion()
    engine.maybe_start_next()
    return True


def cancel_pending_speech_motion(engine: RobotEngine) -> bool:
    if not engine.speech_motion_events:
        return False
    engine.speech_motion_events.clear()
    return True


class FaceFollowRobotDialogAdapter:
    def __init__(self, server: SharedCameraMjpegServer) -> None:
        self.server = server

    def speak_to_group(self, clip: str | None) -> dict:
        return self.server.play_sound_clip(clip)

    def is_speaking(self) -> bool:
        return self.server.is_sound_running()


def apply_blink_settings(
    engine: RobotEngine,
    *,
    enabled: bool,
    interval_s: float,
    previous: tuple[bool, float] | None,
) -> tuple[bool, float]:
    if previous == (enabled, interval_s):
        return previous

    old_interval = engine.config.blink_interval_s
    engine.idle_blink_enabled = False
    if old_interval != interval_s:
        engine.config = type(engine.config)(
            **{**engine.config.__dict__, "blink_interval_s": interval_s}
        )

    if not enabled:
        engine.blink_events = [event for event in engine.blink_events if event.reason != "idle"]

    return enabled, interval_s


def apply_eyelid_offset(engine: RobotEngine, eyelid_offset: float, previous: float | None) -> float:
    if previous == eyelid_offset:
        return previous
    engine.config = type(engine.config)(
        **{**engine.config.__dict__, "eyelid_offset": eyelid_offset}
    )
    return eyelid_offset


def apply_speech_motion_amplitude(
    engine: RobotEngine,
    amplitude_percent: int,
    previous: int | None,
) -> int:
    amplitude_percent = normalize_speech_motion_amplitude_percent(amplitude_percent)
    if previous == amplitude_percent:
        return previous
    scale = amplitude_percent / 100.0
    engine.config = type(engine.config)(
        **{
            **engine.config.__dict__,
            "speech_motion_yaw_deg": SPEECH_MOTION_DEFAULT_YAW_DEG * scale,
            "speech_motion_pitch_deg": SPEECH_MOTION_DEFAULT_PITCH_DEG * scale,
            "speech_motion_tilt_deg": SPEECH_MOTION_DEFAULT_TILT_DEG * scale,
        }
    )
    return amplitude_percent


def debug_snapshot(
    *,
    now: float,
    frame: TrackedFaceFrame,
    engine: RobotEngine,
    active_id: int | None,
    active_kind: str | None,
    selected_id: int | None,
    selected_kind: str | None,
    target: tuple[float, float] | None,
    target_source: str,
    target_mode: str,
    target_behavior: str,
    gaze_mode: str,
    sent: bool,
    last_sent_at: float,
    send_hz: float,
    min_angle_delta: float,
) -> dict:
    target_dict = None
    if target is not None:
        yaw, pitch = target
        target_dict = {"yaw": round(yaw, 3), "pitch": round(pitch, 3)}
    frame_dict = frame.as_dict()
    return {
        "state": "tracking" if selected_id is not None else "no_visible_target",
        "frame_seq": frame.seq,
        "frame_timestamp": frame.timestamp,
        "frame_monotonic_timestamp": frame.monotonic_timestamp,
        "frame_age_ms": round((now - frame.monotonic_timestamp) * 1000.0, 2),
        "width": frame.width,
        "height": frame.height,
        "detections": frame.detections,
        "marker_detections": frame.marker_detections,
        "processing_ms": (
            round(frame.processing_ms, 2)
            if frame.processing_ms is not None
            else None
        ),
        "visible_faces": len(frame.visible_tracks),
        "visible_markers": len(frame.visible_marker_tracks),
        "active_id": active_id,
        "active_kind": active_kind,
        "selected_id": selected_id,
        "selected_kind": selected_kind,
        "target": target_dict,
        "target_source": target_source,
        "target_mode": target_mode,
        "target_behavior": target_behavior,
        "gaze_mode": gaze_mode,
        "engine_state": engine.state.value,
        "engine_timeline_gazes": len(engine.gaze_events),
        "engine_timeline_blinks": len(engine.blink_events),
        "engine_timeline_stretches": len(engine.neck_stretch_events),
        "engine_timeline_speech": len(engine.speech_motion_events),
        "sent": sent,
        "last_sent_age_ms": (
            round((now - last_sent_at) * 1000.0, 2)
            if last_sent_at > 0
            else None
        ),
        "send_hz": send_hz,
        "min_angle_delta": min_angle_delta,
        "faces": frame_dict["faces"],
        "markers": frame_dict["markers"],
    }


def run(args: argparse.Namespace) -> int:
    apply_runtime_settings(args)

    if args.send_hz <= 0:
        raise ValueError("--send-hz must be > 0")
    if args.gaze_ms <= 0:
        raise ValueError("--gaze-ms must be > 0")
    if args.gaze_sample_ms <= 0:
        raise ValueError("--gaze-sample-ms must be > 0")
    if args.gaze_blink_threshold < 0:
        raise ValueError("--gaze-blink-threshold must be >= 0")
    if args.blink_interval <= 0:
        raise ValueError("--blink-interval must be > 0")
    if not -30.0 <= args.eyelid_offset <= 30.0:
        raise ValueError("--eyelid-offset must be between -30 and 30")
    args.sound_volume = normalize_sound_volume_percent(args.sound_volume)
    args.speech_motion_amplitude = normalize_speech_motion_amplitude_percent(
        args.speech_motion_amplitude
    )
    if args.feedback_silence < 0:
        raise ValueError("--feedback-silence must be >= 0")
    if args.done_fallback < 0:
        raise ValueError("--done-fallback must be >= 0")
    if args.loop_sleep < 0:
        raise ValueError("--loop-sleep must be >= 0")
    if args.mjpeg_port < 0:
        raise ValueError("--mjpeg-port must be >= 0")
    if args.target_mode not in TARGET_MODES:
        raise ValueError(f"--target-mode must be one of: {', '.join(sorted(TARGET_MODES))}")
    if args.target_behavior not in TARGET_BEHAVIORS:
        raise ValueError(
            f"--target-behavior must be one of: {', '.join(sorted(TARGET_BEHAVIORS))}"
        )
    if args.target_scan_interval <= 0:
        raise ValueError("--target-scan-interval must be > 0")
    if args.app_mode not in APP_MODES:
        raise ValueError(f"--app-mode must be one of: {', '.join(sorted(APP_MODES))}")

    quiz_config = load_quiz_config(
        args.quiz_file,
        runtime_path=args.quiz_runtime_file,
        teams_path=args.quiz_teams_file,
        speech_path=args.quiz_speech_file,
    )
    selector_configs = load_quiz_selector_configs(
        args.quiz_runtime_file,
        teams_path=args.quiz_teams_file,
    )
    app_controller = AppController(
        quiz_config,
        mode=args.app_mode,
        auto_start_quiz=args.auto_start_quiz,
        selector_configs=selector_configs,
    )
    if args.app_mode == APP_MODE_QUIZ:
        app_controller.start_quiz(time.monotonic())
    world_builder = WorldStateBuilder(ObservationDecoder(quiz_config))

    engine = RobotEngine(
        default_engine_config(
            port=args.port,
            baudrate=args.baudrate,
            gaze_sample_ms=args.gaze_sample_ms,
            eyelid_offset=args.eyelid_offset,
            idle_blink=False,
            blink_interval_s=args.blink_interval,
            gaze_blink_threshold_deg=args.gaze_blink_threshold,
            feedback_silence_s=args.feedback_silence,
            done_fallback_s=args.done_fallback,
            verbose=args.verbose,
        )
    )
    camera = RpicamFaceTracker(
        width=args.width,
        height=args.height,
        fps=args.fps,
        threshold=args.threshold,
        max_missed_frames=args.track_max_missed,
        max_match_distance=args.track_max_distance,
        smoothing=args.track_smoothing,
        debug_jpeg_quality=args.debug_jpeg_quality if args.mjpeg_port else None,
        marker_enabled=app_controller.marker_detection_required(args.target_mode),
    )
    mjpeg_server = SharedCameraMjpegServer(
        camera,
        port=args.mjpeg_port,
        calibration_file=args.calibration_file,
        settings_file=args.settings_file,
        app_controller=app_controller,
        quiz_file=args.quiz_file,
        quiz_runtime_file=args.quiz_runtime_file,
        quiz_teams_file=args.quiz_teams_file,
        quiz_speech_file=args.quiz_speech_file,
        idle_blink=args.idle_blink,
        blink_interval_s=args.blink_interval,
        gaze_mode=args.gaze_mode,
        target_mode=args.target_mode,
        target_behavior=args.target_behavior,
        eyelid_offset=args.eyelid_offset,
        sound_volume_percent=args.sound_volume,
        speech_motion_amplitude_percent=args.speech_motion_amplitude,
    )
    robot_dialog = FaceFollowRobotDialogAdapter(mjpeg_server)

    active_face_id: int | None = None
    active_marker_id: int | None = None
    last_target_revision = mjpeg_server.target_revision_snapshot()
    last_effective_target_mode = mjpeg_server.app_preferred_target_mode() or args.target_mode
    last_face_switch_at = 0.0
    last_marker_switch_at = 0.0
    last_sent_at = 0.0
    last_target: tuple[float, float] | None = None
    send_interval = 1.0 / args.send_hz
    applied_blink_settings: tuple[bool, float] | None = None
    applied_eyelid_offset: float | None = None
    applied_speech_motion_amplitude: int | None = None
    next_auto_blink_at = time.monotonic() + args.blink_interval

    engine.open()
    camera.start()
    mjpeg_server.start()
    print("face follow ready. Ctrl-C to stop.", flush=True)
    try:
        while True:
            now = time.monotonic()
            engine.read_serial()
            engine.update_board_state()
            blink_enabled, blink_interval_s = mjpeg_server.blink_state_snapshot()
            previous_blink_settings = applied_blink_settings
            applied_blink_settings = apply_blink_settings(
                engine,
                enabled=blink_enabled,
                interval_s=blink_interval_s,
                previous=applied_blink_settings,
            )
            if applied_blink_settings != previous_blink_settings:
                next_auto_blink_at = now + blink_interval_s
            if blink_enabled and now >= next_auto_blink_at:
                if schedule_blink_if_ready(engine):
                    next_auto_blink_at = now + blink_interval_s
            applied_eyelid_offset = apply_eyelid_offset(
                engine,
                mjpeg_server.expression_state_snapshot(),
                applied_eyelid_offset,
            )
            applied_speech_motion_amplitude = apply_speech_motion_amplitude(
                engine,
                mjpeg_server.speech_motion_amplitude_snapshot(),
                applied_speech_motion_amplitude,
            )
            sound_running = mjpeg_server.is_sound_running()
            if mjpeg_server.pop_pending_reset_pose():
                engine.reset_pose()
                mjpeg_server.mark_reset_pose_sent()
                last_sent_at = now
                last_target = (0.0, 0.0)
                next_auto_blink_at = now + blink_interval_s
                time.sleep(args.loop_sleep)
                continue
            engine.maybe_start_next()
            gaze_mode = mjpeg_server.gaze_mode_snapshot()
            configured_target_mode = mjpeg_server.target_mode_snapshot()
            target_mode = mjpeg_server.app_preferred_target_mode() or configured_target_mode
            if sound_running and mjpeg_server.app_mode_snapshot() == APP_MODE_QUIZ:
                target_mode = TARGET_MODE_FACES
            target_behavior = mjpeg_server.target_behavior_snapshot()
            target_revision = mjpeg_server.target_revision_snapshot()
            if target_revision != last_target_revision or target_mode != last_effective_target_mode:
                active_face_id = None
                active_marker_id = None
                last_target = None
                last_sent_at = 0.0
                last_face_switch_at = now
                last_marker_switch_at = now
                last_target_revision = target_revision
                last_effective_target_mode = target_mode

            manual_target = mjpeg_server.pop_pending_manual_gaze()
            if manual_target is not None:
                if drive_gaze_if_ready(
                    engine,
                    manual_target,
                    dwell_ms=args.gaze_ms,
                    gaze_mode=gaze_mode,
                    animation_layers_active=sound_running,
                ):
                    last_sent_at = now
                    last_target = manual_target
                else:
                    mjpeg_server.restore_pending_manual_gaze_if_empty(manual_target)

            if camera.has_frame():
                frame = camera.get_latest()
                if frame is None:
                    continue
                frame_dict = frame.as_dict()
                world = world_builder.update_from_marker_dicts(frame_dict["markers"], now)
                mjpeg_server.set_world(world)
                mjpeg_server.update_app(world, robot_dialog, now)
                target_mode = mjpeg_server.app_preferred_target_mode() or configured_target_mode
                if sound_running and mjpeg_server.app_mode_snapshot() == APP_MODE_QUIZ:
                    target_mode = TARGET_MODE_FACES
                if target_mode != last_effective_target_mode:
                    active_face_id = None
                    active_marker_id = None
                    last_target = None
                    last_sent_at = 0.0
                    last_face_switch_at = now
                    last_marker_switch_at = now
                    last_effective_target_mode = target_mode
                camera.set_marker_enabled(mjpeg_server.marker_detection_required(target_mode))
                sound_running = mjpeg_server.is_sound_running()

                target_track: FaceTrack | MarkerTrack | None
                target_track = None
                selected_kind: str | None = None
                target = None
                target_source = mjpeg_server.app_mode_snapshot()
                sent = False

                if not mjpeg_server.app_should_run_active_follow():
                    active_face_id = None
                    active_marker_id = None
                else:
                    if target_mode == TARGET_MODE_MARKERS:
                        if target_behavior == TARGET_BEHAVIOR_SCAN:
                            target_track, last_marker_switch_at = choose_scanning_marker(
                                frame,
                                active_marker_id,
                                now=now,
                                last_switch_at=last_marker_switch_at,
                                interval_s=args.target_scan_interval,
                            )
                        else:
                            target_track = choose_marker(frame, active_marker_id)
                        selected_kind = TARGET_MODE_MARKERS if target_track is not None else None
                    else:
                        if target_behavior == TARGET_BEHAVIOR_SCAN:
                            target_track, last_face_switch_at = choose_scanning_face(
                                frame,
                                active_face_id,
                                now=now,
                                last_switch_at=last_face_switch_at,
                                interval_s=args.target_scan_interval,
                            )
                        else:
                            target_track = choose_face(frame, active_face_id)
                        selected_kind = TARGET_MODE_FACES if target_track is not None else None

                    if target_track is None:
                        if target_mode == TARGET_MODE_MARKERS:
                            active_marker_id = None
                        else:
                            active_face_id = None
                    elif mjpeg_server.is_calibration_enabled():
                        if target_mode == TARGET_MODE_MARKERS:
                            active_marker_id = target_track.track_id
                        else:
                            active_face_id = target_track.track_id
                        target_source = "manual_calibration"
                    else:
                        if target_mode == TARGET_MODE_MARKERS:
                            active_marker_id = target_track.track_id
                            image_point = target_track.center
                        else:
                            active_face_id = target_track.track_id
                            image_point = target_track.eye_center
                        fallback = frame_point_to_gaze(
                            image_point,
                            frame,
                            horizontal_fov=args.horizontal_fov,
                            vertical_fov=args.vertical_fov,
                            yaw_scale=args.yaw_scale,
                            pitch_scale=args.pitch_scale,
                            max_yaw=args.max_yaw,
                            max_pitch=args.max_pitch,
                        )
                        target, target_source = calibrated_frame_point_to_gaze(
                            image_point,
                            frame,
                            mjpeg_server.calibration_points_snapshot(),
                            fallback=fallback,
                            max_yaw=args.max_yaw,
                            max_pitch=args.max_pitch,
                        )
                        target_source = f"{target_mode}_{target_source}"
                        if should_send(
                            now,
                            target,
                            last_target,
                            last_sent_at,
                            send_interval=send_interval,
                            min_angle_delta=args.min_angle_delta,
                        ) and drive_gaze_if_ready(
                            engine,
                            target,
                            dwell_ms=args.gaze_ms,
                            gaze_mode=gaze_mode,
                            animation_layers_active=sound_running,
                        ):
                            last_sent_at = now
                            last_target = target
                            sent = True
                            if args.verbose:
                                yaw, pitch = target
                                print(
                                    f"{target_mode}_track={target_track.track_id} yaw={yaw:.1f} pitch={pitch:.1f} "
                                    f"seq={frame.seq}",
                                    flush=True,
                                )
                active_id = active_marker_id if target_mode == TARGET_MODE_MARKERS else active_face_id
                selected_id = target_track.track_id if target_track is not None else None
                mjpeg_server.set_debug(
                    debug_snapshot(
                        now=now,
                        frame=frame,
                        engine=engine,
                        active_id=active_id,
                        active_kind=target_mode if active_id is not None else None,
                        selected_id=selected_id,
                        selected_kind=selected_kind,
                        target=target,
                        target_source=target_source,
                        target_mode=target_mode,
                        target_behavior=target_behavior,
                        gaze_mode=gaze_mode,
                        sent=sent,
                        last_sent_at=last_sent_at,
                        send_hz=args.send_hz,
                        min_angle_delta=args.min_angle_delta,
                    )
                )

            if sound_running:
                schedule_speech_motion_if_ready(engine)
            else:
                cancel_pending_speech_motion(engine)

            time.sleep(args.loop_sleep)
    except KeyboardInterrupt:
        return 0
    finally:
        mjpeg_server.stop()
        camera.stop()
        engine.close()


def main() -> int:
    try:
        return run(parse_args())
    except RuntimeError as exc:
        print(str(exc), flush=True)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
