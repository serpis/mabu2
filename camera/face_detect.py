"""Face detection MJPEG server, runs on the Raspberry Pi.

Pulls frames from `rpicam-vid` (MJPEG on stdout), decodes with OpenCV, runs the
YuNet ONNX face detector, tracks faces with stable short-lived IDs, draws
bounding boxes + 5 landmarks + score, and serves the annotated frames over HTTP
as multipart/x-mixed-replace MJPEG so a browser can render the stream inline.

Open in a browser: http://<pi-ip>:8080/

The YuNet ONNX model is downloaded once into ./models/ on first run.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import math
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

try:
    import zxingcpp
except ImportError:
    zxingcpp = None

MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
MODEL_PATH = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"
BOUNDARY = b"frame"


@dataclass
class FaceDetection:
    bbox: tuple[float, float, float, float]
    landmarks: tuple[tuple[float, float], ...]
    score: float

    @property
    def center(self) -> tuple[float, float]:
        x, y, w, h = self.bbox
        return x + w / 2.0, y + h / 2.0


@dataclass
class QRDetection:
    data: str
    points: tuple[tuple[float, float], ...]

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        xs = [point[0] for point in self.points]
        ys = [point[1] for point in self.points]
        x1, x2 = min(xs), max(xs)
        y1, y2 = min(ys), max(ys)
        return x1, y1, x2 - x1, y2 - y1

    @property
    def center(self) -> tuple[float, float]:
        return (
            sum(point[0] for point in self.points) / max(len(self.points), 1),
            sum(point[1] for point in self.points) / max(len(self.points), 1),
        )


@dataclass
class FaceTrack:
    track_id: int
    bbox: tuple[float, float, float, float]
    smoothed_bbox: tuple[float, float, float, float]
    score: float
    landmarks: tuple[tuple[float, float], ...]
    first_seen_seq: int
    last_seen_seq: int
    age_frames: int = 1
    missed_frames: int = 0
    velocity: tuple[float, float] = (0.0, 0.0)
    visible: bool = True
    updated_seq: int = 0
    history: list[tuple[float, float]] = field(default_factory=list)

    @property
    def center(self) -> tuple[float, float]:
        x, y, w, h = self.smoothed_bbox
        return x + w / 2.0, y + h / 2.0

    @property
    def eye_center(self) -> tuple[float, float]:
        if len(self.landmarks) < 2:
            return self.center
        right_eye = self.landmarks[0]
        left_eye = self.landmarks[1]
        return (
            (right_eye[0] + left_eye[0]) / 2.0,
            (right_eye[1] + left_eye[1]) / 2.0,
        )

    def update(self, detection: FaceDetection, seq: int, smoothing: float) -> None:
        old_center = self.center
        sx, sy, sw, sh = self.smoothed_bbox
        x, y, w, h = detection.bbox
        a = smoothing
        self.bbox = detection.bbox
        self.smoothed_bbox = (
            sx * a + x * (1.0 - a),
            sy * a + y * (1.0 - a),
            sw * a + w * (1.0 - a),
            sh * a + h * (1.0 - a),
        )
        new_center = self.center
        self.velocity = (new_center[0] - old_center[0], new_center[1] - old_center[1])
        self.score = detection.score
        self.landmarks = detection.landmarks
        self.last_seen_seq = seq
        self.updated_seq = seq
        self.age_frames += 1
        self.missed_frames = 0
        self.visible = True
        self.history.append(new_center)
        self.history = self.history[-8:]

    def mark_missed(self) -> None:
        x, y, w, h = self.smoothed_bbox
        vx, vy = self.velocity
        self.smoothed_bbox = (x + vx, y + vy, w, h)
        self.age_frames += 1
        self.missed_frames += 1
        self.visible = False
        self.history.append(self.center)
        self.history = self.history[-8:]


@dataclass
class QRTrack:
    track_id: int
    data: str
    points: tuple[tuple[float, float], ...]
    smoothed_bbox: tuple[float, float, float, float]
    first_seen_seq: int
    last_seen_seq: int
    age_frames: int = 1
    missed_frames: int = 0
    velocity: tuple[float, float] = (0.0, 0.0)
    visible: bool = True
    updated_seq: int = 0
    history: list[tuple[float, float]] = field(default_factory=list)

    @property
    def center(self) -> tuple[float, float]:
        x, y, w, h = self.smoothed_bbox
        return x + w / 2.0, y + h / 2.0

    def update(self, detection: QRDetection, seq: int, smoothing: float) -> None:
        old_center = self.center
        sx, sy, sw, sh = self.smoothed_bbox
        x, y, w, h = detection.bbox
        a = smoothing
        self.data = detection.data
        self.points = detection.points
        self.smoothed_bbox = (
            sx * a + x * (1.0 - a),
            sy * a + y * (1.0 - a),
            sw * a + w * (1.0 - a),
            sh * a + h * (1.0 - a),
        )
        new_center = self.center
        self.velocity = (new_center[0] - old_center[0], new_center[1] - old_center[1])
        self.last_seen_seq = seq
        self.updated_seq = seq
        self.age_frames += 1
        self.missed_frames = 0
        self.visible = True
        self.history.append(new_center)
        self.history = self.history[-8:]

    def mark_missed(self) -> None:
        x, y, w, h = self.smoothed_bbox
        vx, vy = self.velocity
        self.smoothed_bbox = (x + vx, y + vy, w, h)
        self.age_frames += 1
        self.missed_frames += 1
        self.visible = False
        self.history.append(self.center)
        self.history = self.history[-8:]


@dataclass(frozen=True)
class TrackedFaceFrame:
    seq: int
    timestamp: float
    monotonic_timestamp: float
    width: int
    height: int
    detections: int
    tracks: tuple[FaceTrack, ...]
    qr_detections: int
    qr_tracks: tuple[QRTrack, ...]
    processing_ms: float | None = None

    @property
    def visible_tracks(self) -> tuple[FaceTrack, ...]:
        return tuple(track for track in self.tracks if track.visible)

    @property
    def visible_qr_tracks(self) -> tuple[QRTrack, ...]:
        return tuple(track for track in self.qr_tracks if track.visible)

    def as_dict(self) -> dict:
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "monotonic_timestamp": self.monotonic_timestamp,
            "width": self.width,
            "height": self.height,
            "detections": self.detections,
            "processing_ms": self.processing_ms,
            "visible_faces": len(self.visible_tracks),
            "qr_detections": self.qr_detections,
            "visible_qrs": len(self.visible_qr_tracks),
            "faces": [track_to_dict(track, (self.width, self.height)) for track in self.tracks],
            "qrs": [qr_track_to_dict(track, (self.width, self.height)) for track in self.qr_tracks],
        }


class GreedyFaceTracker:
    def __init__(
        self,
        *,
        max_missed_frames: int,
        max_match_distance: float,
        smoothing: float,
    ) -> None:
        self.max_missed_frames = max(0, max_missed_frames)
        self.max_match_distance = max_match_distance
        self.smoothing = min(max(smoothing, 0.0), 0.95)
        self.next_track_id = 1
        self.tracks: list[FaceTrack] = []

    def update(
        self,
        detections: list[FaceDetection],
        *,
        seq: int,
        image_size: tuple[int, int],
    ) -> list[FaceTrack]:
        active_tracks = [track for track in self.tracks if track.missed_frames <= self.max_missed_frames]
        pairs: list[tuple[float, int, int]] = []
        for track_i, track in enumerate(active_tracks):
            for detection_i, detection in enumerate(detections):
                cost = self.match_cost(track, detection, image_size)
                if cost <= self.max_match_distance:
                    pairs.append((cost, track_i, detection_i))
        pairs.sort(key=lambda item: item[0])

        matched_tracks: set[int] = set()
        matched_detections: set[int] = set()
        for _, track_i, detection_i in pairs:
            if track_i in matched_tracks or detection_i in matched_detections:
                continue
            active_tracks[track_i].update(detections[detection_i], seq, self.smoothing)
            matched_tracks.add(track_i)
            matched_detections.add(detection_i)

        for track_i, track in enumerate(active_tracks):
            if track_i not in matched_tracks:
                track.mark_missed()

        for detection_i, detection in enumerate(detections):
            if detection_i in matched_detections:
                continue
            track = FaceTrack(
                track_id=self.next_track_id,
                bbox=detection.bbox,
                smoothed_bbox=detection.bbox,
                score=detection.score,
                landmarks=detection.landmarks,
                first_seen_seq=seq,
                last_seen_seq=seq,
                updated_seq=seq,
                history=[detection.center],
            )
            self.next_track_id += 1
            active_tracks.append(track)

        self.tracks = [
            track for track in active_tracks
            if track.missed_frames <= self.max_missed_frames
        ]
        return list(self.tracks)

    def match_cost(
        self,
        track: FaceTrack,
        detection: FaceDetection,
        image_size: tuple[int, int],
    ) -> float:
        width, height = image_size
        diag = math.hypot(width, height)
        tx, ty = track.center
        dx, dy = detection.center
        distance = math.hypot(tx - dx, ty - dy) / max(diag, 1.0)
        iou_penalty = 1.0 - bbox_iou(track.smoothed_bbox, detection.bbox)
        size_penalty = bbox_size_delta(track.smoothed_bbox, detection.bbox)
        return distance * 2.0 + iou_penalty * 0.4 + size_penalty * 0.2


class GreedyQRTracker:
    def __init__(
        self,
        *,
        max_missed_frames: int,
        max_match_distance: float,
        smoothing: float,
    ) -> None:
        self.max_missed_frames = max(0, max_missed_frames)
        self.max_match_distance = max_match_distance
        self.smoothing = min(max(smoothing, 0.0), 0.95)
        self.next_track_id = 1
        self.tracks: list[QRTrack] = []

    def update(
        self,
        detections: list[QRDetection],
        *,
        seq: int,
        image_size: tuple[int, int],
    ) -> list[QRTrack]:
        active_tracks = [track for track in self.tracks if track.missed_frames <= self.max_missed_frames]
        pairs: list[tuple[float, int, int]] = []
        for track_i, track in enumerate(active_tracks):
            for detection_i, detection in enumerate(detections):
                cost = self.match_cost(track, detection, image_size)
                if cost <= self.max_match_distance:
                    pairs.append((cost, track_i, detection_i))
        pairs.sort(key=lambda item: item[0])

        matched_tracks: set[int] = set()
        matched_detections: set[int] = set()
        for _, track_i, detection_i in pairs:
            if track_i in matched_tracks or detection_i in matched_detections:
                continue
            active_tracks[track_i].update(detections[detection_i], seq, self.smoothing)
            matched_tracks.add(track_i)
            matched_detections.add(detection_i)

        for track_i, track in enumerate(active_tracks):
            if track_i not in matched_tracks:
                track.mark_missed()

        for detection_i, detection in enumerate(detections):
            if detection_i in matched_detections:
                continue
            track = QRTrack(
                track_id=self.next_track_id,
                data=detection.data,
                points=detection.points,
                smoothed_bbox=detection.bbox,
                first_seen_seq=seq,
                last_seen_seq=seq,
                updated_seq=seq,
                history=[detection.center],
            )
            self.next_track_id += 1
            active_tracks.append(track)

        self.tracks = [
            track for track in active_tracks
            if track.missed_frames <= self.max_missed_frames
        ]
        return list(self.tracks)

    def match_cost(
        self,
        track: QRTrack,
        detection: QRDetection,
        image_size: tuple[int, int],
    ) -> float:
        if track.data and detection.data and track.data != detection.data:
            return math.inf

        width, height = image_size
        diag = math.hypot(width, height)
        tx, ty = track.center
        dx, dy = detection.center
        distance = math.hypot(tx - dx, ty - dy) / max(diag, 1.0)
        iou_penalty = 1.0 - bbox_iou(track.smoothed_bbox, detection.bbox)
        size_penalty = bbox_size_delta(track.smoothed_bbox, detection.bbox)
        return distance * 1.4 + iou_penalty * 0.35 + size_penalty * 0.15


class FaceTrackingPipeline:
    """Local module interface for frame -> tracked faces.

    The MJPEG server below uses this same object for debug overlays. A future
    robot gaze controller can import this class and consume TrackedFaceFrame
    directly without touching HTTP.
    """

    def __init__(
        self,
        *,
        width: int,
        height: int,
        threshold: float,
        max_missed_frames: int = 12,
        max_match_distance: float = 0.7,
        smoothing: float = 0.65,
    ) -> None:
        self.detector = cv2.FaceDetectorYN.create(
            model=str(ensure_model()),
            config="",
            input_size=(width, height),
            score_threshold=threshold,
            nms_threshold=0.3,
            top_k=50,
        )
        self.input_size = (width, height)
        self.tracker = GreedyFaceTracker(
            max_missed_frames=max_missed_frames,
            max_match_distance=max_match_distance,
            smoothing=smoothing,
        )
        self.qr_detector = cv2.QRCodeDetector()
        self.qr_tracker = GreedyQRTracker(
            max_missed_frames=max_missed_frames,
            max_match_distance=max_match_distance,
            smoothing=smoothing,
        )

    def process_frame(
        self,
        frame: np.ndarray,
        *,
        seq: int,
        timestamp: float | None = None,
        monotonic_timestamp: float | None = None,
        detect_qr: bool = True,
    ) -> TrackedFaceFrame:
        started_at = time.monotonic()
        height, width = frame.shape[:2]
        if (width, height) != self.input_size:
            self.detector.setInputSize((width, height))
            self.input_size = (width, height)
        _, faces = self.detector.detect(frame)
        detections = detections_from_yunet(faces)
        tracks = self.tracker.update(detections, seq=seq, image_size=(width, height))
        qr_detections = detect_qr_codes(self.qr_detector, frame) if detect_qr else []
        qr_tracks = self.qr_tracker.update(qr_detections, seq=seq, image_size=(width, height))
        processing_ms = (time.monotonic() - started_at) * 1000.0
        return TrackedFaceFrame(
            seq=seq,
            timestamp=time.time() if timestamp is None else timestamp,
            monotonic_timestamp=(
                time.monotonic() if monotonic_timestamp is None else monotonic_timestamp
            ),
            width=width,
            height=height,
            detections=len(detections),
            tracks=tuple(tracks),
            qr_detections=len(qr_detections),
            qr_tracks=tuple(qr_tracks),
            processing_ms=processing_ms,
        )


class RpicamFaceTracker:
    """Threaded camera primitive for polling the latest tracked faces."""

    def __init__(
        self,
        *,
        width: int = 1296,
        height: int = 972,
        fps: int = 15,
        threshold: float = 0.6,
        max_missed_frames: int = 12,
        max_match_distance: float = 0.7,
        smoothing: float = 0.65,
        debug_jpeg_quality: int | None = None,
        qr_enabled: bool = False,
    ) -> None:
        self.width = width
        self.height = height
        self.fps = fps
        self.debug_jpeg_quality = debug_jpeg_quality
        self._qr_enabled = qr_enabled
        self._qr_enabled_lock = threading.Lock()
        self.pipeline = FaceTrackingPipeline(
            width=width,
            height=height,
            threshold=threshold,
            max_missed_frames=max_missed_frames,
            max_match_distance=max_match_distance,
            smoothing=smoothing,
        )
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._raw_slot: dict = {}
        self._raw_lock = threading.Lock()
        self._latest_frame: TrackedFaceFrame | None = None
        self._latest_jpeg: bytes | None = None
        self._delivered_seq = -1
        self._thread: threading.Thread | None = None
        self._reader: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop = threading.Event()
        self._raw_slot = {}
        self._delivered_seq = -1
        cmd = [
            "rpicam-vid", "-t", "0", "-n",
            "--width", str(self.width), "--height", str(self.height),
            "--framerate", str(self.fps),
            "--buffer-count", "2", "--flush",
            "--codec", "mjpeg", "--inline", "-o", "-",
        ]
        print(f"starting: {' '.join(cmd)}", file=sys.stderr)
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._reader = threading.Thread(
            target=reader_thread,
            args=(self._proc.stdout, self._raw_slot, self._raw_lock, self._stop),
            daemon=True,
        )
        self._reader.start()
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()

    def __enter__(self) -> RpicamFaceTracker:
        self.start()
        return self

    def __exit__(self, *_exc_info) -> None:
        self.stop()

    def stop(self) -> None:
        self._stop.set()
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if self._reader is not None:
            self._reader.join(timeout=2)
            self._reader = None

    def has_frame(self) -> bool:
        with self._lock:
            return self._latest_frame is not None and self._latest_frame.seq != self._delivered_seq

    def get_latest(self, *, mark_seen: bool = True) -> TrackedFaceFrame | None:
        with self._lock:
            frame = self._latest_frame
            if frame is None:
                return None
            if mark_seen:
                self._delivered_seq = frame.seq
            return frame

    def get_debug_jpeg(self) -> tuple[bytes, int] | None:
        with self._lock:
            if self._latest_jpeg is None or self._latest_frame is None:
                return None
            return self._latest_jpeg, self._latest_frame.seq

    def set_qr_enabled(self, enabled: bool) -> None:
        with self._qr_enabled_lock:
            self._qr_enabled = enabled

    def is_qr_enabled(self) -> bool:
        with self._qr_enabled_lock:
            return self._qr_enabled

    def _process_loop(self) -> None:
        encode_params = (
            [int(cv2.IMWRITE_JPEG_QUALITY), self.debug_jpeg_quality]
            if self.debug_jpeg_quality is not None
            else None
        )
        last_seq = -1
        while not self._stop.is_set():
            with self._raw_lock:
                jpeg_bytes = self._raw_slot.get("jpeg")
                seq = self._raw_slot.get("seq", 0)
                timestamp = self._raw_slot.get("timestamp")
                monotonic_timestamp = self._raw_slot.get("monotonic_timestamp")
            if jpeg_bytes is None or seq == last_seq:
                time.sleep(0.005)
                continue
            last_seq = seq

            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                continue

            detect_qr = self.is_qr_enabled()
            tracked_frame = self.pipeline.process_frame(
                frame,
                seq=seq,
                timestamp=timestamp,
                monotonic_timestamp=monotonic_timestamp,
                detect_qr=detect_qr,
            )
            debug_jpeg = None
            if encode_params is not None:
                visible_faces, visible_qrs = annotate_tracks(
                    frame,
                    list(tracked_frame.tracks),
                    list(tracked_frame.qr_tracks),
                )
                cv2.putText(
                    frame, f"faces: {visible_faces} qr: {visible_qrs}", (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA,
                )
                ok, enc = cv2.imencode(".jpg", frame, encode_params)
                if ok:
                    debug_jpeg = enc.tobytes()

            with self._lock:
                self._latest_frame = tracked_frame
                if debug_jpeg is not None:
                    self._latest_jpeg = debug_jpeg


def ensure_model() -> Path:
    if not MODEL_PATH.exists():
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading YuNet model -> {MODEL_PATH}", file=sys.stderr)
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    return MODEL_PATH


def bbox_iou(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def bbox_size_delta(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    _, _, aw, ah = a
    _, _, bw, bh = b
    area_a = max(aw * ah, 1.0)
    area_b = max(bw * bh, 1.0)
    return abs(math.log(area_b / area_a))


def detections_from_yunet(faces: np.ndarray | None) -> list[FaceDetection]:
    if faces is None:
        return []
    detections: list[FaceDetection] = []
    for face in faces:
        landmarks = tuple(
            (float(face[4 + 2 * i]), float(face[5 + 2 * i]))
            for i in range(5)
        )
        detections.append(
            FaceDetection(
                bbox=tuple(float(value) for value in face[:4]),
                landmarks=landmarks,
                score=float(face[-1]),
            )
        )
    return detections


def detect_qr_codes(detector: cv2.QRCodeDetector, frame: np.ndarray) -> list[QRDetection]:
    zxing_detections = detect_qr_codes_zxing(frame)
    if zxing_detections:
        return zxing_detections

    detections: list[QRDetection] = []
    try:
        ok, decoded_info, points, _straight = detector.detectAndDecodeMulti(frame)
    except cv2.error:
        ok, decoded_info, points = False, (), None

    if ok and points is not None:
        for data, qr_points in zip(decoded_info, points):
            point_tuple = tuple(
                (float(point[0]), float(point[1]))
                for point in qr_points
            )
            if len(point_tuple) >= 4:
                decoded = str(data) or decode_qr_from_quad(detector, frame, point_tuple)
                if decoded:
                    detections.append(QRDetection(data=decoded, points=point_tuple))
        return detections

    try:
        data, points, _straight = detector.detectAndDecode(frame)
    except cv2.error:
        return detections
    if points is None:
        return detections

    point_array = points.reshape(-1, 2)
    point_tuple = tuple((float(point[0]), float(point[1])) for point in point_array)
    if len(point_tuple) >= 4:
        decoded = str(data) or decode_qr_from_quad(detector, frame, point_tuple)
        if decoded:
            detections.append(QRDetection(data=decoded, points=point_tuple))
    return detections


def detect_qr_codes_zxing(frame: np.ndarray) -> list[QRDetection]:
    if zxingcpp is None:
        return []

    for scale in (1.0, 2.0):
        image = (
            cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            if scale != 1.0
            else frame
        )
        try:
            barcodes = zxingcpp.read_barcodes(image)
        except Exception:
            continue

        detections: list[QRDetection] = []
        for barcode in barcodes:
            if "QRCode" not in str(barcode.format) or not barcode.text:
                continue
            position = barcode.position
            points = (
                (float(position.top_left.x) / scale, float(position.top_left.y) / scale),
                (float(position.top_right.x) / scale, float(position.top_right.y) / scale),
                (float(position.bottom_right.x) / scale, float(position.bottom_right.y) / scale),
                (float(position.bottom_left.x) / scale, float(position.bottom_left.y) / scale),
            )
            detections.append(QRDetection(data=str(barcode.text), points=points))
        if detections:
            return detections
    return []


def decode_qr_from_quad(
    detector: cv2.QRCodeDetector,
    frame: np.ndarray,
    points: tuple[tuple[float, float], ...],
) -> str:
    if len(points) < 4:
        return ""

    quad = order_quad_points(np.array(points[:4], dtype=np.float32))
    side = int(
        max(
            np.linalg.norm(quad[0] - quad[1]),
            np.linalg.norm(quad[1] - quad[2]),
            np.linalg.norm(quad[2] - quad[3]),
            np.linalg.norm(quad[3] - quad[0]),
        )
    )
    side = max(160, min(512, side))
    dst = np.array(
        [[0, 0], [side - 1, 0], [side - 1, side - 1], [0, side - 1]],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(quad, dst)
    warped = cv2.warpPerspective(frame, transform, (side, side))
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    for candidate in (
        cv2.cvtColor(otsu, cv2.COLOR_GRAY2BGR),
        cv2.copyMakeBorder(
            cv2.cvtColor(otsu, cv2.COLOR_GRAY2BGR),
            24,
            24,
            24,
            24,
            cv2.BORDER_CONSTANT,
            value=(255, 255, 255),
        ),
        cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR),
    ):
        try:
            decoded, _points, _straight = detector.detectAndDecode(candidate)
        except cv2.error:
            continue
        if decoded:
            return str(decoded)
    return ""


def order_quad_points(points: np.ndarray) -> np.ndarray:
    ordered = np.zeros((4, 2), dtype=np.float32)
    sums = points.sum(axis=1)
    diffs = np.diff(points, axis=1).reshape(-1)
    ordered[0] = points[np.argmin(sums)]
    ordered[2] = points[np.argmax(sums)]
    ordered[1] = points[np.argmin(diffs)]
    ordered[3] = points[np.argmax(diffs)]
    return ordered


def track_to_dict(track: FaceTrack, image_size: tuple[int, int]) -> dict:
    width, height = image_size
    x, y, w, h = track.smoothed_bbox
    cx, cy = track.center
    ex, ey = track.eye_center
    vx, vy = track.velocity
    return {
        "id": track.track_id,
        "visible": track.visible,
        "bbox": [round(x, 2), round(y, 2), round(w, 2), round(h, 2)],
        "center": [round(cx, 2), round(cy, 2)],
        "center_norm": [
            round((cx - width / 2.0) / max(width / 2.0, 1.0), 4),
            round((cy - height / 2.0) / max(height / 2.0, 1.0), 4),
        ],
        "eye_center": [round(ex, 2), round(ey, 2)],
        "eye_center_norm": [
            round((ex - width / 2.0) / max(width / 2.0, 1.0), 4),
            round((ey - height / 2.0) / max(height / 2.0, 1.0), 4),
        ],
        "velocity": [round(vx, 2), round(vy, 2)],
        "score": round(track.score, 4),
        "landmarks": [
            [round(px, 2), round(py, 2)]
            for px, py in track.landmarks
        ],
        "age_frames": track.age_frames,
        "missed_frames": track.missed_frames,
        "first_seen_seq": track.first_seen_seq,
        "last_seen_seq": track.last_seen_seq,
    }


def qr_track_to_dict(track: QRTrack, image_size: tuple[int, int]) -> dict:
    width, height = image_size
    x, y, w, h = track.smoothed_bbox
    cx, cy = track.center
    vx, vy = track.velocity
    return {
        "id": track.track_id,
        "visible": track.visible,
        "data": track.data,
        "bbox": [round(x, 2), round(y, 2), round(w, 2), round(h, 2)],
        "center": [round(cx, 2), round(cy, 2)],
        "center_norm": [
            round((cx - width / 2.0) / max(width / 2.0, 1.0), 4),
            round((cy - height / 2.0) / max(height / 2.0, 1.0), 4),
        ],
        "points": [
            [round(px, 2), round(py, 2)]
            for px, py in track.points
        ],
        "velocity": [round(vx, 2), round(vy, 2)],
        "age_frames": track.age_frames,
        "missed_frames": track.missed_frames,
        "first_seen_seq": track.first_seen_seq,
        "last_seen_seq": track.last_seen_seq,
    }


def annotate_tracks(frame: np.ndarray, tracks: list[FaceTrack], qr_tracks: list[QRTrack] | None = None) -> tuple[int, int]:
    visible_count = 0
    for track in tracks:
        if track.visible:
            visible_count += 1
        x, y, w, h = (int(value) for value in track.smoothed_bbox)
        color = (0, 255, 0) if track.visible else (0, 160, 255)
        label = f"id:{track.track_id} {track.score:.2f}"
        cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
        cv2.putText(
            frame, label, (x, max(y - 6, 12)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA,
        )
        if not track.visible:
            continue
        for px, py in track.landmarks:
            cv2.circle(frame, (int(px), int(py)), 2, (0, 0, 255), -1)
        ex, ey = track.eye_center
        cv2.drawMarker(
            frame,
            (int(ex), int(ey)),
            (255, 0, 255),
            cv2.MARKER_CROSS,
            10,
            1,
            cv2.LINE_AA,
        )

    visible_qr_count = 0
    for track in qr_tracks or []:
        if track.visible:
            visible_qr_count += 1
        points = np.array(track.points, dtype=np.int32).reshape((-1, 1, 2))
        color = (255, 190, 0) if track.visible else (0, 160, 255)
        x, y, w, _h = (int(value) for value in track.smoothed_bbox)
        label = f"qr:{track.track_id} {track.data[:24]}"
        cv2.polylines(frame, [points], True, color, 2)
        cv2.putText(
            frame, label, (x, max(y - 22, 12)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA,
        )
        cx, cy = track.center
        cv2.drawMarker(
            frame,
            (int(cx), int(cy)),
            color,
            cv2.MARKER_CROSS,
            12,
            1,
            cv2.LINE_AA,
        )
    return visible_count, visible_qr_count


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
            now = time.time()
            monotonic_now = time.monotonic()
            with slot_lock:
                slot["jpeg"] = last_jpeg
                slot["seq"] = slot.get("seq", 0) + 1
                slot["timestamp"] = now
                slot["monotonic_timestamp"] = monotonic_now


def capture_loop(width: int, height: int, fps: int, threshold: float,
                 jpeg_quality: int, max_missed_frames: int,
                 max_match_distance: float, smoothing: float,
                 latest: dict, lock: threading.Lock, stop: threading.Event):
    camera = RpicamFaceTracker(
        width=width,
        height=height,
        fps=fps,
        threshold=threshold,
        max_missed_frames=max_missed_frames,
        max_match_distance=max_match_distance,
        smoothing=smoothing,
        debug_jpeg_quality=jpeg_quality,
    )
    camera.start()
    last_seq = -1

    try:
        while not stop.is_set():
            item = camera.get_debug_jpeg()
            if item is None:
                time.sleep(0.01)
                continue
            jpeg, seq = item
            if seq == last_seq:
                time.sleep(0.01)
                continue
            last_seq = seq
            with lock:
                latest["jpeg"] = jpeg
                latest["seq"] = latest.get("seq", 0) + 1
    finally:
        stop.set()
        camera.stop()


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
    ap.add_argument("--track-max-missed", type=int, default=12)
    ap.add_argument("--track-max-distance", type=float, default=0.7)
    ap.add_argument("--track-smoothing", type=float, default=0.65)
    args = ap.parse_args()

    latest: dict = {}
    lock = threading.Lock()
    stop = threading.Event()

    t = threading.Thread(
        target=capture_loop,
        args=(args.width, args.height, args.fps, args.threshold,
              args.jpeg_quality, args.track_max_missed,
              args.track_max_distance, args.track_smoothing,
              latest, lock, stop),
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
