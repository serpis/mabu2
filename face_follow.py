#!/usr/bin/env python3
"""Low-latency face-following loop using camera tracking and direct gaze poses."""
from __future__ import annotations

import argparse
import json
import math
import socket
import threading
import time

from camera.face_detect import BOUNDARY, FaceTrack, RpicamFaceTracker, TrackedFaceFrame
from robot_engine import RobotEngine, default_engine_config


class SharedCameraMjpegServer:
    def __init__(self, camera: RpicamFaceTracker, *, port: int) -> None:
        self.camera = camera
        self.port = port
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

    def set_debug(self, snapshot: dict) -> None:
        with self.debug_lock:
            self.debug_snapshot = snapshot

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

        if req.startswith(b"GET / "):
            html = self._index_html()
            conn.sendall(
                b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n"
                b"Content-Length: " + str(len(html)).encode() + b"\r\n\r\n" + html
            )
            conn.close()
            return

        if req.startswith(b"GET /debug"):
            with self.debug_lock:
                body = json.dumps(
                    {**self.debug_snapshot, "served_at": time.time()},
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

        if not req.startswith(b"GET /stream"):
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
aside{border-left:1px solid #333;background:#181818;overflow:auto}
h1{font-size:16px;margin:12px 14px}
.row{display:grid;grid-template-columns:120px 1fr;gap:8px;padding:4px 14px;border-top:1px solid #252525}
.k{color:#9ca3af}
.v{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
pre{margin:12px 14px 18px;padding:10px;background:#0b0b0b;border:1px solid #333;white-space:pre-wrap}
@media(max-width:900px){main{grid-template-columns:1fr;grid-template-rows:60vh auto}aside{border-left:0;border-top:1px solid #333}}
</style>
</head>
<body>
<main>
  <section class="video"><img src="/stream" alt="Annotated camera stream"></section>
  <aside>
    <h1>Face Follow Debug</h1>
    <div class="row"><div class="k">state</div><div class="v" id="state">-</div></div>
    <div class="row"><div class="k">frame</div><div class="v" id="frame">-</div></div>
    <div class="row"><div class="k">age ms</div><div class="v" id="age">-</div></div>
    <div class="row"><div class="k">active</div><div class="v" id="active">-</div></div>
    <div class="row"><div class="k">target</div><div class="v" id="target">-</div></div>
    <div class="row"><div class="k">sent</div><div class="v" id="sent">-</div></div>
    <pre id="raw">{}</pre>
  </aside>
</main>
<script>
function fmt(n,d=1){return Number.isFinite(n)?n.toFixed(d):"-";}
async function tick(){
  try{
    const r=await fetch("/debug",{cache:"no-store"});
    const d=await r.json();
    document.getElementById("state").textContent=d.state || "-";
    document.getElementById("frame").textContent=`seq=${d.frame_seq ?? "-"} faces=${d.visible_faces ?? 0}/${d.detections ?? 0}`;
    document.getElementById("age").textContent=fmt(d.frame_age_ms);
    document.getElementById("active").textContent=`active=${d.active_id ?? "-"} selected=${d.selected_id ?? "-"}`;
    document.getElementById("target").textContent=d.target ? `yaw=${fmt(d.target.yaw)} pitch=${fmt(d.target.pitch)}` : "-";
    document.getElementById("sent").textContent=d.sent ? `yes, ${fmt(d.last_sent_age_ms)} ms ago` : `no, ${fmt(d.last_sent_age_ms)} ms ago`;
    document.getElementById("raw").textContent=JSON.stringify(d,null,2);
  }catch(e){
    document.getElementById("state").textContent="debug fetch failed";
  }
}
setInterval(tick,250);
tick();
</script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Track one face and drive gaze with direct pose commands."
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
    parser.add_argument("--max-yaw", type=float, default=35.0)
    parser.add_argument("--max-pitch", type=float, default=18.0)
    parser.add_argument("--send-hz", type=float, default=15.0)
    parser.add_argument("--min-angle-delta", type=float, default=1.0)
    parser.add_argument("--eyelid-offset", type=float, default=-2.0)
    parser.add_argument("--loop-sleep", type=float, default=0.003)
    parser.add_argument("--mjpeg-port", type=int, default=8080)
    parser.add_argument("--debug-jpeg-quality", type=int, default=80)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


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
    cx, cy = face.center
    norm_x = (cx - frame.width / 2.0) / max(frame.width / 2.0, 1.0)
    norm_y = (cy - frame.height / 2.0) / max(frame.height / 2.0, 1.0)
    yaw = norm_x * horizontal_fov * 0.5 * yaw_scale
    pitch = -norm_y * vertical_fov * 0.5 * pitch_scale
    return clamp(yaw, -max_yaw, max_yaw), clamp(pitch, -max_pitch, max_pitch)


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


def debug_snapshot(
    *,
    now: float,
    frame: TrackedFaceFrame,
    active_id: int | None,
    face: FaceTrack | None,
    target: tuple[float, float] | None,
    sent: bool,
    last_sent_at: float,
    send_hz: float,
    min_angle_delta: float,
) -> dict:
    selected_id = face.track_id if face is not None else None
    target_dict = None
    if target is not None:
        yaw, pitch = target
        target_dict = {"yaw": round(yaw, 3), "pitch": round(pitch, 3)}
    frame_dict = frame.as_dict()
    return {
        "state": "tracking" if face is not None else "no_visible_face",
        "frame_seq": frame.seq,
        "frame_timestamp": frame.timestamp,
        "frame_monotonic_timestamp": frame.monotonic_timestamp,
        "frame_age_ms": round((now - frame.monotonic_timestamp) * 1000.0, 2),
        "width": frame.width,
        "height": frame.height,
        "detections": frame.detections,
        "visible_faces": len(frame.visible_tracks),
        "active_id": active_id,
        "selected_id": selected_id,
        "target": target_dict,
        "sent": sent,
        "last_sent_age_ms": (
            round((now - last_sent_at) * 1000.0, 2)
            if last_sent_at > 0
            else None
        ),
        "send_hz": send_hz,
        "min_angle_delta": min_angle_delta,
        "faces": frame_dict["faces"],
    }


def run(args: argparse.Namespace) -> int:
    if args.send_hz <= 0:
        raise ValueError("--send-hz must be > 0")
    if args.loop_sleep < 0:
        raise ValueError("--loop-sleep must be >= 0")
    if args.mjpeg_port < 0:
        raise ValueError("--mjpeg-port must be >= 0")

    engine = RobotEngine(
        default_engine_config(
            port=args.port,
            baudrate=args.baudrate,
            eyelid_offset=args.eyelid_offset,
            idle_blink=False,
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
    mjpeg_server = SharedCameraMjpegServer(camera, port=args.mjpeg_port)

    active_id: int | None = None
    last_sent_at = 0.0
    last_target: tuple[float, float] | None = None
    send_interval = 1.0 / args.send_hz

    engine.open()
    camera.start()
    mjpeg_server.start()
    print("face follow ready. Ctrl-C to stop.", flush=True)
    try:
        while True:
            now = time.monotonic()
            engine.read_serial()

            if camera.has_frame():
                frame = camera.get_latest()
                if frame is None:
                    continue
                face = choose_face(frame, active_id)
                target = None
                sent = False
                if face is None:
                    active_id = None
                else:
                    active_id = face.track_id
                    target = face_to_gaze(
                        face,
                        frame,
                        horizontal_fov=args.horizontal_fov,
                        vertical_fov=args.vertical_fov,
                        yaw_scale=args.yaw_scale,
                        pitch_scale=args.pitch_scale,
                        max_yaw=args.max_yaw,
                        max_pitch=args.max_pitch,
                    )
                    if should_send(
                        now,
                        target,
                        last_target,
                        last_sent_at,
                        send_interval=send_interval,
                        min_angle_delta=args.min_angle_delta,
                    ):
                        engine.send_direct_gaze(*target)
                        last_sent_at = now
                        last_target = target
                        sent = True
                        if args.verbose:
                            yaw, pitch = target
                            print(
                                f"track={active_id} yaw={yaw:.1f} pitch={pitch:.1f} "
                                f"seq={frame.seq}",
                                flush=True,
                            )
                mjpeg_server.set_debug(
                    debug_snapshot(
                        now=now,
                        frame=frame,
                        active_id=active_id,
                        face=face,
                        target=target,
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
