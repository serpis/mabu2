#!/usr/bin/env python3
"""Low-latency face-following loop using camera tracking and direct gaze poses."""
from __future__ import annotations

import argparse
import math
import time

from camera.face_detect import FaceTrack, RpicamFaceTracker, TrackedFaceFrame
from robot_engine import RobotEngine, default_engine_config


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
    parser.add_argument("--debug-jpeg-quality", type=int)
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


def run(args: argparse.Namespace) -> int:
    if args.send_hz <= 0:
        raise ValueError("--send-hz must be > 0")
    if args.loop_sleep < 0:
        raise ValueError("--loop-sleep must be >= 0")

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
        debug_jpeg_quality=args.debug_jpeg_quality,
    )

    active_id: int | None = None
    last_sent_at = 0.0
    last_target: tuple[float, float] | None = None
    send_interval = 1.0 / args.send_hz

    engine.open()
    camera.start()
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
                        if args.verbose:
                            yaw, pitch = target
                            print(
                                f"track={active_id} yaw={yaw:.1f} pitch={pitch:.1f} "
                                f"seq={frame.seq}",
                                flush=True,
                            )

            time.sleep(args.loop_sleep)
    except KeyboardInterrupt:
        return 0
    finally:
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
