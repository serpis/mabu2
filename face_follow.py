#!/usr/bin/env python3
"""Face-following loop using camera tracking with selectable gaze output."""
from __future__ import annotations

import argparse
import json
import math
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from camera.face_detect import BOUNDARY, FaceTrack, MarkerTrack, RpicamFaceTracker, TrackedFaceFrame
from robot_engine import BoardState, PendingGaze, RobotEngine, default_engine_config


GAZE_MODE_ANIMATION = "animation_engine"
GAZE_MODE_DIRECT = "direct_pose"
GAZE_MODES = {GAZE_MODE_ANIMATION, GAZE_MODE_DIRECT}
TARGET_MODE_FACES = "faces"
TARGET_MODE_MARKERS = "markers"
TARGET_MODES = {TARGET_MODE_FACES, TARGET_MODE_MARKERS}


class SharedCameraMjpegServer:
    def __init__(
        self,
        camera: RpicamFaceTracker,
        *,
        port: int,
        calibration_file: str,
        settings_file: str,
        idle_blink: bool,
        blink_interval_s: float,
        gaze_mode: str,
        target_mode: str,
        eyelid_offset: float,
    ) -> None:
        self.camera = camera
        self.port = port
        self.calibration_file = Path(calibration_file)
        self.settings_file = Path(settings_file)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.socket: socket.socket | None = None
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
        self.camera.set_marker_enabled(target_mode == TARGET_MODE_MARKERS)
        self.expression_lock = threading.Lock()
        self.eyelid_offset = eyelid_offset
        self._save_runtime_settings()

    def set_debug(self, snapshot: dict) -> None:
        with self.debug_lock:
            self.debug_snapshot = snapshot

    def is_calibration_enabled(self) -> bool:
        with self.calibration_lock:
            return self.calibration_enabled

    def pop_pending_manual_gaze(self) -> tuple[float, float] | None:
        with self.calibration_lock:
            target = self.pending_manual_gaze
            self.pending_manual_gaze = None
            return target

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

    def expression_state_snapshot(self) -> float:
        with self.expression_lock:
            return self.eyelid_offset

    def _expression_state(self) -> dict:
        with self.expression_lock:
            return {"eyelid_offset": self.eyelid_offset}

    def _settings_values(self) -> dict:
        with self.gaze_lock:
            gaze_mode = self.gaze_mode
        with self.target_lock:
            target_mode = self.target_mode
        with self.blink_lock:
            idle_blink_enabled = self.idle_blink_enabled
            blink_interval_s = self.blink_interval_s
        with self.expression_lock:
            eyelid_offset = self.eyelid_offset
        return {
            "gaze_mode": gaze_mode,
            "target_mode": target_mode,
            "idle_blink_enabled": idle_blink_enabled,
            "blink_interval_s": blink_interval_s,
            "eyelid_offset": eyelid_offset,
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

    def _gaze_state(self) -> dict:
        with self.gaze_lock:
            return {"mode": self.gaze_mode}

    def _target_state(self) -> dict:
        with self.target_lock:
            return {"mode": self.target_mode}

    def _handle_target_request(self, path: str) -> dict:
        parsed = urlsplit(path)
        params = parse_qs(parsed.query)
        action = params.get("action", ["state"])[0]

        if action == "state":
            return self._target_state()

        if action == "set":
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
            with self.target_lock:
                self.target_mode = mode
            self.camera.set_marker_enabled(mode == TARGET_MODE_MARKERS)
            self._save_runtime_settings()
            return self._target_state()

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
            point = self._record_calibration_point(yaw, pitch)
            result = self._calibration_state()
            result["recorded"] = point
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
label{display:block;margin:8px 14px;color:#c9c9c9}
.row{display:grid;grid-template-columns:120px 1fr;gap:8px;padding:4px 14px;border-top:1px solid #252525}
.k{color:#9ca3af}
.v{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.panel{border-top:1px solid #333;padding:8px 0}
.controls{padding:4px 14px}
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
    <div class="row"><div class="k">state</div><div class="v" id="state">-</div></div>
    <div class="row"><div class="k">frame</div><div class="v" id="frame">-</div></div>
    <div class="row"><div class="k">age ms</div><div class="v" id="age">-</div></div>
    <div class="row"><div class="k">process ms</div><div class="v" id="processMs">-</div></div>
    <div class="row"><div class="k">active</div><div class="v" id="active">-</div></div>
    <div class="row"><div class="k">target</div><div class="v" id="target">-</div></div>
    <div class="row"><div class="k">target source</div><div class="v" id="targetSource">-</div></div>
    <div class="row"><div class="k">sent</div><div class="v" id="sent">-</div></div>
    <div class="panel">
      <h1>Target</h1>
      <label><input id="markerTarget" type="checkbox"> Look at markers</label>
      <div class="row"><div class="k">mode</div><div class="v" id="targetMode">-</div></div>
    </div>
    <div class="panel">
      <h1>Gaze Output</h1>
      <label><input id="animationGaze" type="checkbox" checked> Animation engine</label>
      <div class="row"><div class="k">mode</div><div class="v" id="gazeMode">-</div></div>
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
const animationGaze=document.getElementById("animationGaze");
const eyelidOffset=document.getElementById("eyelidOffset");
const idleBlink=document.getElementById("idleBlink");
const blinkInterval=document.getElementById("blinkInterval");
const gazePad=document.getElementById("gazePad");
const gazeMarker=document.getElementById("gazeMarker");
let manualYaw=0;
let manualPitch=0;
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
function renderGaze(g){
  if(!g)return;
  animationGaze.checked=g.mode !== "direct_pose";
  document.getElementById("gazeMode").textContent=g.mode || "-";
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
    renderExpression(d.expression);
    renderBlink(d.blink);
    renderCalibration(d.calibration);
    document.getElementById("raw").textContent=JSON.stringify(d,null,2);
  }catch(e){
    document.getElementById("state").textContent="debug fetch failed";
  }
}
markerTarget.addEventListener("change",()=>target("set",{mode:markerTarget.checked ? "markers" : "faces"}));
animationGaze.addEventListener("change",()=>gaze("set",{mode:animationGaze.checked ? "animation_engine" : "direct_pose"}));
eyelidOffset.addEventListener("change",()=>expression("set",expressionValues()));
idleBlink.addEventListener("change",()=>blink("set",blinkValues()));
blinkInterval.addEventListener("change",()=>blink("set",blinkValues()));
manualMode.addEventListener("change",()=>cal("enable",{enabled:manualMode.checked ? "1" : "0"}));
gazePad.addEventListener("pointerdown",(ev)=>{
  const target=padTarget(ev);
  updateManual(target.yaw,target.pitch);
  cal("set",{...target,apply:"1"}).catch(()=>{});
});
document.getElementById("apply").addEventListener("click",()=>cal("set",{...manualValues(),apply:"1"}));
document.getElementById("record").addEventListener("click",()=>cal("record",manualValues()));
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
    parser.add_argument("--gaze-ms", type=float, default=350.0)
    parser.add_argument("--gaze-sample-ms", type=float, default=50.0)
    parser.add_argument("--gaze-blink-threshold", type=float, default=999.0)
    parser.add_argument("--idle-blink", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--blink-interval", type=float, default=4.0)
    parser.add_argument("--feedback-silence", type=float, default=0.20)
    parser.add_argument("--done-fallback", type=float, default=0.60)
    parser.add_argument("--eyelid-offset", type=float, default=-2.0)
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
    if engine.state != BoardState.IDLE or engine.gaze_events:
        return False
    yaw, pitch = target
    engine.schedule_gaze(PendingGaze(yaw=yaw, pitch=pitch, dwell_ms=dwell_ms))
    engine.maybe_start_next()
    return True


def send_direct_gaze_if_ready(engine: RobotEngine, target: tuple[float, float]) -> bool:
    if engine.state != BoardState.IDLE:
        return False
    engine.send_direct_gaze(*target)
    return True


def drive_gaze_if_ready(
    engine: RobotEngine,
    target: tuple[float, float],
    *,
    dwell_ms: float,
    gaze_mode: str,
) -> bool:
    if gaze_mode == GAZE_MODE_DIRECT:
        return send_direct_gaze_if_ready(engine, target)
    return schedule_gaze_if_ready(engine, target, dwell_ms=dwell_ms)


def schedule_blink_if_ready(engine: RobotEngine) -> bool:
    if engine.state != BoardState.IDLE or engine.gaze_events or engine.blink_events:
        return False
    if not engine.schedule_blink_at(time.monotonic(), reason="idle", force=True):
        return False
    engine.maybe_start_next()
    return True


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
        "gaze_mode": gaze_mode,
        "engine_state": engine.state.value,
        "engine_timeline_gazes": len(engine.gaze_events),
        "engine_timeline_blinks": len(engine.blink_events),
        "engine_timeline_stretches": len(engine.neck_stretch_events),
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
    )
    mjpeg_server = SharedCameraMjpegServer(
        camera,
        port=args.mjpeg_port,
        calibration_file=args.calibration_file,
        settings_file=args.settings_file,
        idle_blink=args.idle_blink,
        blink_interval_s=args.blink_interval,
        gaze_mode=args.gaze_mode,
        target_mode=args.target_mode,
        eyelid_offset=args.eyelid_offset,
    )

    active_face_id: int | None = None
    active_marker_id: int | None = None
    last_sent_at = 0.0
    last_target: tuple[float, float] | None = None
    send_interval = 1.0 / args.send_hz
    applied_blink_settings: tuple[bool, float] | None = None
    applied_eyelid_offset: float | None = None
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
            engine.maybe_start_next()
            gaze_mode = mjpeg_server.gaze_mode_snapshot()
            target_mode = mjpeg_server.target_mode_snapshot()

            manual_target = mjpeg_server.pop_pending_manual_gaze()
            if manual_target is not None:
                if drive_gaze_if_ready(
                    engine,
                    manual_target,
                    dwell_ms=args.gaze_ms,
                    gaze_mode=gaze_mode,
                ):
                    last_sent_at = now
                    last_target = manual_target

            if camera.has_frame():
                frame = camera.get_latest()
                if frame is None:
                    continue
                target_track: FaceTrack | MarkerTrack | None
                selected_kind: str | None
                if target_mode == TARGET_MODE_MARKERS:
                    target_track = choose_marker(frame, active_marker_id)
                    selected_kind = TARGET_MODE_MARKERS if target_track is not None else None
                else:
                    target_track = choose_face(frame, active_face_id)
                    selected_kind = TARGET_MODE_FACES if target_track is not None else None
                target = None
                target_source = "none"
                sent = False
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
                        gaze_mode=gaze_mode,
                        sent=sent,
                        last_sent_at=last_sent_at,
                        send_hz=args.send_hz,
                        min_angle_delta=args.min_angle_delta,
                    )
                )

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
