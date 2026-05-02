"""Face detection MJPEG server, runs on the Raspberry Pi.

Pulls frames from `rpicam-vid` (MJPEG on stdout), decodes with OpenCV, runs the
YuNet ONNX face detector, draws bounding boxes + 5 landmarks + score, and
serves the annotated frames over HTTP as multipart/x-mixed-replace MJPEG so a
browser can render the stream inline.

Open in a browser: http://<pi-ip>:8080/

The YuNet ONNX model is downloaded once into ./models/ on first run.
"""
from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
MODEL_PATH = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"
BOUNDARY = b"frame"


def ensure_model() -> Path:
    if not MODEL_PATH.exists():
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading YuNet model -> {MODEL_PATH}", file=sys.stderr)
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    return MODEL_PATH


def annotate(frame: np.ndarray, faces: np.ndarray | None) -> int:
    if faces is None:
        return 0
    for face in faces:
        x, y, w, h = face[:4].astype(int)
        score = float(face[-1])
        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(
            frame, f"{score:.2f}", (x, max(y - 6, 12)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA,
        )
        for i in range(5):
            px, py = face[4 + 2 * i:6 + 2 * i].astype(int)
            cv2.circle(frame, (px, py), 2, (0, 0, 255), -1)
    return len(faces)


def reader_thread(stream, slot: dict, slot_lock: threading.Lock,
                  stop: threading.Event):
    """Continuously parse JPEGs from `stream` and overwrite a single slot.

    This drains the pipe as fast as possible so kernel buffers don't fill up.
    If the consumer is slower than the producer, intermediate frames are
    silently dropped and only the most recent one is kept.
    """
    buf = b""
    while not stop.is_set():
        chunk = stream.read(8192)
        if not chunk:
            return
        buf += chunk
        last_jpeg = None
        last_end = -1
        # Greedily find the most recent complete JPEG in buf, discard older.
        while True:
            start = buf.find(b"\xff\xd8")
            end = buf.find(b"\xff\xd9", start + 2) if start >= 0 else -1
            if start < 0 or end < 0:
                if start < 0 and len(buf) > 1 << 20:
                    buf = buf[-2:]
                break
            last_jpeg = buf[start:end + 2]
            last_end = end + 2
            buf = buf[last_end:]
        if last_jpeg is not None:
            with slot_lock:
                slot["jpeg"] = last_jpeg
                slot["seq"] = slot.get("seq", 0) + 1


def capture_loop(width: int, height: int, fps: int, threshold: float,
                 jpeg_quality: int, latest: dict, lock: threading.Lock,
                 stop: threading.Event):
    detector = cv2.FaceDetectorYN.create(
        model=str(ensure_model()),
        config="",
        input_size=(width, height),
        score_threshold=threshold,
        nms_threshold=0.3,
        top_k=50,
    )
    cmd = [
        "rpicam-vid", "-t", "0", "-n",
        "--width", str(width), "--height", str(height),
        "--framerate", str(fps),
        "--codec", "mjpeg", "--inline", "-o", "-",
    ]
    print(f"starting: {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    raw_slot: dict = {}
    raw_lock = threading.Lock()
    threading.Thread(
        target=reader_thread,
        args=(proc.stdout, raw_slot, raw_lock, stop),
        daemon=True,
    ).start()

    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]
    frames = 0
    t0 = time.time()
    last_seq = -1

    try:
        while not stop.is_set():
            with raw_lock:
                jpeg_bytes = raw_slot.get("jpeg")
                seq = raw_slot.get("seq", 0)
            if jpeg_bytes is None or seq == last_seq:
                time.sleep(0.005)
                continue
            last_seq = seq

            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                continue
            fh, fw = frame.shape[:2]
            if (fw, fh) != (width, height):
                detector.setInputSize((fw, fh))
            _, faces = detector.detect(frame)
            n = annotate(frame, faces)

            frames += 1
            elapsed = time.time() - t0
            fps_now = frames / elapsed if elapsed > 0 else 0.0
            cv2.putText(
                frame, f"faces: {n}  fps: {fps_now:.1f}", (8, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA,
            )

            ok, enc = cv2.imencode(".jpg", frame, encode_params)
            if not ok:
                continue
            with lock:
                latest["jpeg"] = enc.tobytes()
                latest["seq"] = latest.get("seq", 0) + 1
    finally:
        stop.set()
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def serve_client(conn: socket.socket, latest: dict, lock: threading.Lock,
                 stop: threading.Event):
    try:
        req = conn.recv(2048)
    except Exception:
        conn.close()
        return
    if req.startswith(b"GET / "):
        html = (b"<html><body style='margin:0;background:#000'>"
                b"<img src='/stream' style='width:100vw;height:100vh;object-fit:contain'/>"
                b"</body></html>")
        conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n"
                     b"Content-Length: " + str(len(html)).encode() + b"\r\n\r\n" + html)
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
        while not stop.is_set():
            with lock:
                jpeg = latest.get("jpeg")
                seq = latest.get("seq", 0)
            if jpeg is None or seq == last_seq:
                time.sleep(0.01)
                continue
            last_seq = seq
            payload = (b"--" + BOUNDARY + b"\r\n"
                       b"Content-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                       + jpeg + b"\r\n")
            conn.sendall(payload)
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=1296)
    ap.add_argument("--height", type=int, default=972)
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--threshold", type=float, default=0.6)
    ap.add_argument("--jpeg-quality", type=int, default=80)
    args = ap.parse_args()

    latest: dict = {}
    lock = threading.Lock()
    stop = threading.Event()

    t = threading.Thread(
        target=capture_loop,
        args=(args.width, args.height, args.fps, args.threshold,
              args.jpeg_quality, latest, lock, stop),
        daemon=True,
    )
    t.start()

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", args.port))
    s.listen(8)
    print(f"listening on :{args.port}", flush=True)

    try:
        while True:
            conn, _ = s.accept()
            threading.Thread(
                target=serve_client, args=(conn, latest, lock, stop), daemon=True
            ).start()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        s.close()


if __name__ == "__main__":
    main()
