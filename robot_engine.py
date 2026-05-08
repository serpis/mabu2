#!/usr/bin/env python3

from __future__ import annotations

import argparse
import math
import select
import sys
import time
from dataclasses import dataclass
from enum import Enum

from robot_animation import (
    BLINK_DEFAULT_CLOSED_ANGLE,
    BLINK_DEFAULT_CLOSE_MS,
    BLINK_DEFAULT_HOLD_MS,
    BLINK_DEFAULT_OPEN_MS,
    BLINK_OVERLAY_SETTLE_MS,
    BlinkEvent,
    FeedbackDecoder,
    FeedbackFrame,
    GAZE_TO_STRETCH_CHANNELS,
    GAZE_TO_CHANNELS,
    GazeControllerState,
    GazeCornersConfig,
    HoldYaw,
    NECK_STRETCH_DEFAULT_DURATION_MS,
    NECK_STRETCH_DEFAULT_PITCH_DEG,
    NECK_STRETCH_DEFAULT_SAMPLE_MS,
    NECK_STRETCH_DEFAULT_TILT_DEG,
    NECK_STRETCH_DEFAULT_YAW_DEG,
    NECK_STRETCH_EYE_PITCH_LIMIT_DEG,
    NECK_STRETCH_EYE_TAU_MS,
    NECK_STRETCH_NECK_PITCH_TAU_MS,
    NECK_STRETCH_NECK_TILT_TAU_MS,
    NECK_STRETCH_NECK_YAW_TAU_MS,
    NECK_STRETCH_SETTLE_MS,
    SPEECH_MOTION_DEFAULT_CYCLE_MS,
    SPEECH_MOTION_DEFAULT_DURATION_MS,
    SPEECH_MOTION_DEFAULT_PITCH_DEG,
    SPEECH_MOTION_DEFAULT_SAMPLE_MS,
    SPEECH_MOTION_DEFAULT_SETTLE_MS,
    SPEECH_MOTION_DEFAULT_TILT_DEG,
    SPEECH_MOTION_DEFAULT_YAW_DEG,
    NeckStretchEvent,
    SpeechMotionEvent,
    blink_base_eyelid_angle,
    gaze_to_curves,
    merged_curve_end_ms,
    neck_stretch_event_end_ms,
    render_gaze_corners_curves,
    sample_ms_for_merged_script,
    speech_motion_event_end_ms,
    YawTargetCurve,
)
from robot_motion import (
    BAUDRATE,
    Command,
    POWER_OFF,
    SCRIPT_TICK_SECONDS,
    STARTUP_COMMANDS,
    RobotMotion,
    format_hex,
    look_pose_command,
    packet,
    print_rx,
    read_command,
)


DEFAULT_GAZE_MS = 1500.0
DEFAULT_GAZE_SAMPLE_MS = 50.0
DEFAULT_IDLE_BLINK_INTERVAL_S = 4.0
DEFAULT_GAZE_BLINK_THRESHOLD_DEG = 15.0
DEFAULT_GAZE_BLINK_REFRACTORY_S = 3.0
DEFAULT_FEEDBACK_SILENCE_S = 0.20
DEFAULT_DONE_FALLBACK_S = 0.60


class BoardState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    SETTLING = "settling"
    UNKNOWN = "unknown"


@dataclass
class PendingGaze:
    yaw: float
    pitch: float
    dwell_ms: float


@dataclass(frozen=True)
class TimelineGaze:
    start_s: float
    yaw: float
    pitch: float
    dwell_s: float

    @property
    def end_s(self) -> float:
        return self.start_s + self.dwell_s


@dataclass(frozen=True)
class TimelineBlink:
    start_s: float
    reason: str


@dataclass(frozen=True)
class TimelineNeckStretch:
    start_s: float
    pitch_deg: float
    yaw_deg: float
    tilt_deg: float
    duration_ms: float
    settle_ms: float


@dataclass(frozen=True)
class TimelineSpeechMotion:
    start_s: float
    duration_ms: float
    settle_ms: float
    yaw_deg: float
    pitch_deg: float
    tilt_deg: float
    cycle_ms: float


@dataclass(frozen=True)
class TimelineRender:
    command: Command
    duration_s: float
    render_duration_ms: float
    gazes: tuple[TimelineGaze, ...]
    blinks: tuple[TimelineBlink, ...]
    stretches: tuple[TimelineNeckStretch, ...]
    speech_motions: tuple[TimelineSpeechMotion, ...]
    frame_count: int


@dataclass(frozen=True)
class EngineConfig:
    port: str
    baudrate: int
    gaze_sample_ms: float
    eyelid_offset: float
    idle_blink: bool
    blink_interval_s: float
    gaze_blink_threshold_deg: float
    gaze_blink_refractory_s: float
    blink_close_ms: float
    blink_hold_ms: float
    blink_open_ms: float
    blink_closed_angle: float
    neck_stretch_sample_ms: float
    neck_stretch_pitch_deg: float
    neck_stretch_yaw_deg: float
    neck_stretch_tilt_deg: float
    neck_stretch_duration_ms: float
    neck_stretch_settle_ms: float
    speech_motion_sample_ms: float
    speech_motion_chunk_ms: float
    speech_motion_yaw_deg: float
    speech_motion_pitch_deg: float
    speech_motion_tilt_deg: float
    speech_motion_cycle_ms: float
    speech_motion_settle_ms: float
    feedback_silence_s: float
    done_fallback_s: float
    verbose: bool


def rendered_duration_s(keyframes: tuple[tuple[int, ...], ...]) -> float:
    return sum(frame[-1] for frame in keyframes) * SCRIPT_TICK_SECONDS


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Foreground animation engine that owns the motorboard UART and reads commands from stdin."
    )
    parser.add_argument("--port", default="/dev/ttyAMA0")
    parser.add_argument("--baudrate", type=int, default=BAUDRATE)
    parser.add_argument("--gaze-sample-ms", type=float, default=DEFAULT_GAZE_SAMPLE_MS)
    parser.add_argument("--eyelid-offset", type=float, default=-2.0)
    parser.add_argument("--idle-blink", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--blink-interval", type=float, default=DEFAULT_IDLE_BLINK_INTERVAL_S)
    parser.add_argument("--gaze-blink-threshold", type=float, default=DEFAULT_GAZE_BLINK_THRESHOLD_DEG)
    parser.add_argument("--gaze-blink-refractory", type=float, default=DEFAULT_GAZE_BLINK_REFRACTORY_S)
    parser.add_argument("--blink-close-ms", type=float, default=BLINK_DEFAULT_CLOSE_MS)
    parser.add_argument("--blink-hold-ms", type=float, default=BLINK_DEFAULT_HOLD_MS)
    parser.add_argument("--blink-open-ms", type=float, default=BLINK_DEFAULT_OPEN_MS)
    parser.add_argument("--blink-closed-angle", type=float, default=BLINK_DEFAULT_CLOSED_ANGLE)
    parser.add_argument("--neck-stretch-sample-ms", type=float, default=NECK_STRETCH_DEFAULT_SAMPLE_MS)
    parser.add_argument("--neck-stretch-pitch", type=float, default=NECK_STRETCH_DEFAULT_PITCH_DEG)
    parser.add_argument("--neck-stretch-yaw", type=float, default=NECK_STRETCH_DEFAULT_YAW_DEG)
    parser.add_argument("--neck-stretch-tilt", type=float, default=NECK_STRETCH_DEFAULT_TILT_DEG)
    parser.add_argument("--neck-stretch-duration-ms", type=float, default=NECK_STRETCH_DEFAULT_DURATION_MS)
    parser.add_argument("--neck-stretch-settle-ms", type=float, default=NECK_STRETCH_SETTLE_MS)
    parser.add_argument("--speech-motion-sample-ms", type=float, default=SPEECH_MOTION_DEFAULT_SAMPLE_MS)
    parser.add_argument("--speech-motion-chunk-ms", type=float, default=SPEECH_MOTION_DEFAULT_DURATION_MS)
    parser.add_argument("--speech-motion-yaw", type=float, default=SPEECH_MOTION_DEFAULT_YAW_DEG)
    parser.add_argument("--speech-motion-pitch", type=float, default=SPEECH_MOTION_DEFAULT_PITCH_DEG)
    parser.add_argument("--speech-motion-tilt", type=float, default=SPEECH_MOTION_DEFAULT_TILT_DEG)
    parser.add_argument("--speech-motion-cycle-ms", type=float, default=SPEECH_MOTION_DEFAULT_CYCLE_MS)
    parser.add_argument("--speech-motion-settle-ms", type=float, default=SPEECH_MOTION_DEFAULT_SETTLE_MS)
    parser.add_argument("--feedback-silence", type=float, default=DEFAULT_FEEDBACK_SILENCE_S)
    parser.add_argument("--done-fallback", type=float, default=DEFAULT_DONE_FALLBACK_S)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def default_engine_config(
    *,
    port: str = "/dev/ttyAMA0",
    baudrate: int = BAUDRATE,
    gaze_sample_ms: float = DEFAULT_GAZE_SAMPLE_MS,
    eyelid_offset: float = -2.0,
    idle_blink: bool = True,
    blink_interval_s: float = DEFAULT_IDLE_BLINK_INTERVAL_S,
    gaze_blink_threshold_deg: float = DEFAULT_GAZE_BLINK_THRESHOLD_DEG,
    gaze_blink_refractory_s: float = DEFAULT_GAZE_BLINK_REFRACTORY_S,
    blink_close_ms: float = BLINK_DEFAULT_CLOSE_MS,
    blink_hold_ms: float = BLINK_DEFAULT_HOLD_MS,
    blink_open_ms: float = BLINK_DEFAULT_OPEN_MS,
    blink_closed_angle: float = BLINK_DEFAULT_CLOSED_ANGLE,
    neck_stretch_sample_ms: float = NECK_STRETCH_DEFAULT_SAMPLE_MS,
    neck_stretch_pitch_deg: float = NECK_STRETCH_DEFAULT_PITCH_DEG,
    neck_stretch_yaw_deg: float = NECK_STRETCH_DEFAULT_YAW_DEG,
    neck_stretch_tilt_deg: float = NECK_STRETCH_DEFAULT_TILT_DEG,
    neck_stretch_duration_ms: float = NECK_STRETCH_DEFAULT_DURATION_MS,
    neck_stretch_settle_ms: float = NECK_STRETCH_SETTLE_MS,
    speech_motion_sample_ms: float = SPEECH_MOTION_DEFAULT_SAMPLE_MS,
    speech_motion_chunk_ms: float = SPEECH_MOTION_DEFAULT_DURATION_MS,
    speech_motion_yaw_deg: float = SPEECH_MOTION_DEFAULT_YAW_DEG,
    speech_motion_pitch_deg: float = SPEECH_MOTION_DEFAULT_PITCH_DEG,
    speech_motion_tilt_deg: float = SPEECH_MOTION_DEFAULT_TILT_DEG,
    speech_motion_cycle_ms: float = SPEECH_MOTION_DEFAULT_CYCLE_MS,
    speech_motion_settle_ms: float = SPEECH_MOTION_DEFAULT_SETTLE_MS,
    feedback_silence_s: float = DEFAULT_FEEDBACK_SILENCE_S,
    done_fallback_s: float = DEFAULT_DONE_FALLBACK_S,
    verbose: bool = False,
) -> EngineConfig:
    return EngineConfig(
        port=port,
        baudrate=baudrate,
        gaze_sample_ms=gaze_sample_ms,
        eyelid_offset=eyelid_offset,
        idle_blink=idle_blink,
        blink_interval_s=blink_interval_s,
        gaze_blink_threshold_deg=gaze_blink_threshold_deg,
        gaze_blink_refractory_s=gaze_blink_refractory_s,
        blink_close_ms=blink_close_ms,
        blink_hold_ms=blink_hold_ms,
        blink_open_ms=blink_open_ms,
        blink_closed_angle=blink_closed_angle,
        neck_stretch_sample_ms=neck_stretch_sample_ms,
        neck_stretch_pitch_deg=neck_stretch_pitch_deg,
        neck_stretch_yaw_deg=neck_stretch_yaw_deg,
        neck_stretch_tilt_deg=neck_stretch_tilt_deg,
        neck_stretch_duration_ms=neck_stretch_duration_ms,
        neck_stretch_settle_ms=neck_stretch_settle_ms,
        speech_motion_sample_ms=speech_motion_sample_ms,
        speech_motion_chunk_ms=speech_motion_chunk_ms,
        speech_motion_yaw_deg=speech_motion_yaw_deg,
        speech_motion_pitch_deg=speech_motion_pitch_deg,
        speech_motion_tilt_deg=speech_motion_tilt_deg,
        speech_motion_cycle_ms=speech_motion_cycle_ms,
        speech_motion_settle_ms=speech_motion_settle_ms,
        feedback_silence_s=feedback_silence_s,
        done_fallback_s=done_fallback_s,
        verbose=verbose,
    )


def parse_gaze_line(parts: list[str]) -> PendingGaze:
    if len(parts) < 2:
        raise ValueError("usage: gaze YAW[,PITCH] [DURATION_MS] or gaze YAW PITCH [DURATION_MS]")

    if "," in parts[1]:
        angle_parts = [part.strip() for part in parts[1].split(",")]
        if len(angle_parts) == 1:
            yaw = float(angle_parts[0])
            pitch = 0.0
        elif len(angle_parts) == 2:
            yaw = float(angle_parts[0])
            pitch = float(angle_parts[1])
        else:
            raise ValueError("gaze angle must be YAW or YAW,PITCH")
        dwell_ms = float(parts[2]) if len(parts) >= 3 else DEFAULT_GAZE_MS
    else:
        yaw = float(parts[1])
        if len(parts) >= 3:
            pitch = float(parts[2])
            dwell_ms = float(parts[3]) if len(parts) >= 4 else DEFAULT_GAZE_MS
        else:
            pitch = 0.0
            dwell_ms = DEFAULT_GAZE_MS

    if dwell_ms <= 0:
        raise ValueError("gaze duration must be > 0")
    return PendingGaze(yaw=yaw, pitch=pitch, dwell_ms=dwell_ms)


class RobotEngine:
    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self.robot = RobotMotion(config.port, config.baudrate)
        self.feedback_decoder = FeedbackDecoder()
        self.started_at = time.monotonic()
        self.state = BoardState.UNKNOWN
        self.expected_done_at = 0.0
        self.last_tx_at: float | None = None
        self.last_feedback_at: float | None = None
        self.latest_frame: FeedbackFrame | None = None
        self.gaze_events: list[TimelineGaze] = []
        self.blink_events: list[TimelineBlink] = []
        self.neck_stretch_events: list[TimelineNeckStretch] = []
        self.speech_motion_events: list[TimelineSpeechMotion] = []
        self.idle_blink_enabled = config.idle_blink
        self.next_idle_blink_at = time.monotonic() + config.blink_interval_s
        self.last_blink_at: float | None = None
        self.running = True

    def log(self, message: str) -> None:
        print(message, flush=True)

    def debug(self, message: str) -> None:
        if self.config.verbose:
            self.log(message)

    def open(self) -> None:
        self.robot.open()
        self.send_wire_command(STARTUP_COMMANDS[0], expected_duration_s=STARTUP_COMMANDS[0].delay_after)
        self.pump_for(STARTUP_COMMANDS[0].delay_after)
        reset_command = self.reset_pose()
        self.pump_for(reset_command.delay_after)
        self.refresh_pose()
        self.state = BoardState.IDLE
        self.log("engine ready. Type 'help' for commands.")

    def close(self) -> None:
        self.robot.close()

    @property
    def port(self):
        return self.robot.serial_port

    def send_wire_command(self, command: Command, *, expected_duration_s: float) -> None:
        now = time.monotonic()
        tx = packet(command.payload)
        if self.config.verbose:
            self.log(f"TX +{now - self.started_at:0.3f}s {command.name}: {format_hex(tx)}")
        self.port.write(tx)
        self.port.flush()
        self.last_tx_at = now
        self.expected_done_at = now + expected_duration_s
        self.state = BoardState.RUNNING

    def send_rendered_command(self, command: Command, *, script_duration_s: float) -> None:
        self.send_wire_command(command, expected_duration_s=script_duration_s)

    def reset_pose(self) -> Command:
        self.gaze_events.clear()
        self.blink_events.clear()
        self.neck_stretch_events.clear()
        self.speech_motion_events.clear()
        self.next_idle_blink_at = time.monotonic() + self.config.blink_interval_s
        command = look_pose_command(0.0, 0.0, self.config.eyelid_offset)
        self.send_wire_command(command, expected_duration_s=command.delay_after)
        self.log("reset pose sent")
        return command

    def send_direct_gaze(self, yaw: float, pitch: float) -> Command:
        """Send a one-frame gaze pose immediately, bypassing the timeline."""
        now = time.monotonic()
        command = look_pose_command(yaw, pitch, self.config.eyelid_offset)
        tx = packet(command.payload)
        if self.config.verbose:
            self.log(f"TX +{now - self.started_at:0.3f}s direct {command.name}: {format_hex(tx)}")
        self.port.write(tx)
        self.port.flush()
        self.last_tx_at = now
        return command

    def read_serial(self) -> None:
        waiting = self.port.in_waiting
        if not waiting:
            return
        data = self.port.read(waiting)
        now = time.monotonic()
        if self.config.verbose:
            print_rx(data, self.started_at)
        for frame in self.feedback_decoder.feed(data, now - self.started_at):
            self.latest_frame = frame
            self.last_feedback_at = now

    def pump_for(self, duration_s: float) -> None:
        deadline = time.monotonic() + max(duration_s, 0.0)
        while time.monotonic() < deadline:
            self.read_serial()
            time.sleep(0.01)
        self.read_serial()

    def refresh_pose(self) -> None:
        command = read_command("engine_read_vr_values", 0x40, timeout=0.25)
        self.send_wire_command(command, expected_duration_s=command.delay_after)
        self.pump_for(command.delay_after)

    def update_board_state(self) -> None:
        now = time.monotonic()
        if self.state == BoardState.IDLE:
            return
        if now < self.expected_done_at:
            self.state = BoardState.RUNNING
            return

        feedback_quiet = self.last_feedback_at is None or now - self.last_feedback_at >= self.config.feedback_silence_s
        fallback_elapsed = now >= self.expected_done_at + self.config.done_fallback_s
        if feedback_quiet or fallback_elapsed:
            self.state = BoardState.IDLE
        else:
            self.state = BoardState.SETTLING

    def current_controller_state(self) -> GazeControllerState:
        frame = self.latest_frame
        if frame is None:
            return GazeControllerState(0.0, 0.0, 0.0, 0.0)
        return GazeControllerState(
            eye_yaw=frame.angle("eye_leftright"),
            eye_pitch=frame.angle("eye_updown"),
            neck_yaw=frame.angle("neck_rotation"),
            neck_pitch=frame.angle("neck_elevation"),
            neck_tilt=frame.angle("neck_tilt"),
            eyelid_left=frame.angle("eyelid_left"),
            eyelid_right=frame.angle("eyelid_right"),
        )

    def current_base_eyelid_angle(self) -> float:
        eye_pitch = self.latest_frame.angle("eye_updown") if self.latest_frame is not None else 0.0
        return blink_base_eyelid_angle(eye_pitch, self.config.eyelid_offset)

    def estimated_gaze(self) -> tuple[float, float]:
        state = self.current_controller_state()
        return state.eye_yaw + state.neck_yaw, state.eye_pitch + state.neck_pitch

    def gaze_jump_degrees(self, gaze: PendingGaze) -> float:
        current_yaw, current_pitch = self.estimated_gaze()
        return math.hypot(gaze.yaw - current_yaw, gaze.pitch - current_pitch)

    def timeline_blink_event(self, blink: TimelineBlink, render_start_s: float) -> BlinkEvent:
        return self.make_blink_event(max(0.0, (blink.start_s - render_start_s) * 1000.0))

    def timeline_blink_end_s(self, blink: TimelineBlink) -> float:
        duration_ms = (
            self.config.blink_close_ms
            + self.config.blink_hold_ms
            + self.config.blink_open_ms
            + BLINK_OVERLAY_SETTLE_MS
        )
        return blink.start_s + duration_ms / 1000.0

    def timeline_neck_stretch_event(self, stretch: TimelineNeckStretch, render_start_s: float) -> NeckStretchEvent:
        return NeckStretchEvent(
            start_ms=max(0.0, (stretch.start_s - render_start_s) * 1000.0),
            pitch_deg=stretch.pitch_deg,
            yaw_deg=stretch.yaw_deg,
            tilt_deg=stretch.tilt_deg,
            duration_ms=stretch.duration_ms,
            settle_ms=stretch.settle_ms,
        )

    def timeline_neck_stretch_end_s(self, stretch: TimelineNeckStretch) -> float:
        event = NeckStretchEvent(
            start_ms=0.0,
            pitch_deg=stretch.pitch_deg,
            yaw_deg=stretch.yaw_deg,
            tilt_deg=stretch.tilt_deg,
            duration_ms=stretch.duration_ms,
            settle_ms=stretch.settle_ms,
        )
        return stretch.start_s + neck_stretch_event_end_ms(event) / 1000.0

    def timeline_speech_motion_event(
        self,
        speech: TimelineSpeechMotion,
        render_start_s: float,
    ) -> SpeechMotionEvent:
        return SpeechMotionEvent(
            start_ms=max(0.0, (speech.start_s - render_start_s) * 1000.0),
            duration_ms=speech.duration_ms,
            settle_ms=speech.settle_ms,
            yaw_deg=speech.yaw_deg,
            pitch_deg=speech.pitch_deg,
            tilt_deg=speech.tilt_deg,
            cycle_ms=speech.cycle_ms,
        )

    def timeline_speech_motion_end_s(self, speech: TimelineSpeechMotion) -> float:
        event = SpeechMotionEvent(
            start_ms=0.0,
            duration_ms=speech.duration_ms,
            settle_ms=speech.settle_ms,
            yaw_deg=speech.yaw_deg,
            pitch_deg=speech.pitch_deg,
            tilt_deg=speech.tilt_deg,
            cycle_ms=speech.cycle_ms,
        )
        return speech.start_s + speech_motion_event_end_ms(event) / 1000.0

    def timeline_event_end_s(
        self,
        event: TimelineGaze | TimelineBlink | TimelineNeckStretch | TimelineSpeechMotion,
    ) -> float:
        if isinstance(event, TimelineGaze):
            return event.end_s
        if isinstance(event, TimelineNeckStretch):
            return self.timeline_neck_stretch_end_s(event)
        if isinstance(event, TimelineSpeechMotion):
            return self.timeline_speech_motion_end_s(event)
        return self.timeline_blink_end_s(event)

    def timeline_event_duration_s(
        self,
        event: TimelineGaze | TimelineBlink | TimelineNeckStretch | TimelineSpeechMotion,
    ) -> float:
        return self.timeline_event_end_s(event) - event.start_s

    def timeline_event_effective_end_s(
        self,
        event: TimelineGaze | TimelineBlink | TimelineNeckStretch | TimelineSpeechMotion,
        render_start_s: float,
    ) -> float:
        if event.start_s <= render_start_s:
            return render_start_s + self.timeline_event_duration_s(event)
        return self.timeline_event_end_s(event)

    def latest_timeline_end_s(self, *, include_idle_blinks: bool = True) -> float | None:
        events: list[TimelineGaze | TimelineBlink | TimelineNeckStretch | TimelineSpeechMotion] = [
            *self.gaze_events,
            *self.neck_stretch_events,
            *self.speech_motion_events,
        ]
        events.extend(
            blink for blink in self.blink_events
            if include_idle_blinks or blink.reason != "idle"
        )
        if not events:
            return None
        return max(self.timeline_event_end_s(event) for event in events)

    def latest_explicit_gaze_end_s(self) -> float | None:
        if not self.gaze_events:
            return None
        return max(event.end_s for event in self.gaze_events)

    def latest_scheduled_gaze_target_before(self, at_s: float) -> tuple[float, float] | None:
        candidates = [event for event in self.gaze_events if event.start_s <= at_s]
        if not candidates:
            return None
        event = max(candidates, key=lambda item: item.start_s)
        return event.yaw, event.pitch

    def scheduled_gaze_jump_degrees(self, gaze: PendingGaze, start_s: float) -> float:
        reference = self.latest_scheduled_gaze_target_before(start_s)
        if reference is None:
            reference = self.estimated_gaze()
        yaw, pitch = reference
        return math.hypot(gaze.yaw - yaw, gaze.pitch - pitch)

    def make_blink_event(self, start_ms: float = 0.0) -> BlinkEvent:
        return BlinkEvent(
            start_ms=start_ms,
            close_ms=self.config.blink_close_ms,
            hold_ms=self.config.blink_hold_ms,
            open_ms=self.config.blink_open_ms,
            closed_angle=self.config.blink_closed_angle,
        )

    def can_auto_blink_at(self, at_s: float, previous_blink_at: float | None) -> bool:
        return (
            previous_blink_at is None
            or at_s - previous_blink_at >= self.config.gaze_blink_refractory_s
        )

    def schedule_blink_at(self, start_s: float, *, reason: str, force: bool = False) -> bool:
        if any(abs(event.start_s - start_s) < 0.001 for event in self.blink_events):
            return False
        if not force and not self.can_auto_blink_at(start_s, self.last_blink_at):
            return False
        self.blink_events.append(TimelineBlink(start_s=start_s, reason=reason))
        self.last_blink_at = start_s
        self.next_idle_blink_at = max(self.next_idle_blink_at, start_s + self.config.blink_interval_s)
        return True

    def schedule_gaze(self, gaze: PendingGaze, *, replace_pending: bool = False) -> TimelineGaze:
        gaze_to_curves(gaze.yaw, gaze.pitch, gaze.dwell_ms)
        now = time.monotonic()
        if replace_pending:
            self.gaze_events.clear()
        latest_gaze_end = self.latest_explicit_gaze_end_s()
        start_s = now
        if latest_gaze_end is not None:
            start_s = max(start_s, latest_gaze_end)
        if self.state != BoardState.IDLE:
            start_s = max(start_s, self.expected_done_at)

        jump_deg = self.scheduled_gaze_jump_degrees(gaze, start_s)
        event = TimelineGaze(
            start_s=start_s,
            yaw=gaze.yaw,
            pitch=gaze.pitch,
            dwell_s=gaze.dwell_ms / 1000.0,
        )
        self.gaze_events.append(event)

        # Idle autos are derived from the timeline. Future idle blinks are not sacred.
        self.blink_events = [
            blink for blink in self.blink_events
            if blink.reason != "idle" or blink.start_s < start_s
        ]
        self.next_idle_blink_at = max(self.next_idle_blink_at, event.end_s + self.config.blink_interval_s)

        if jump_deg > self.config.gaze_blink_threshold_deg:
            self.schedule_blink_at(start_s, reason="gaze", force=False)
        return event

    def schedule_manual_blink(self) -> TimelineBlink | None:
        now = time.monotonic()
        start_s = now
        if self.state != BoardState.IDLE:
            start_s = max(start_s, self.expected_done_at)
        if not self.schedule_blink_at(start_s, reason="manual", force=True):
            return None
        return max(self.blink_events, key=lambda event: event.start_s)

    def schedule_neck_stretch(self) -> TimelineNeckStretch:
        now = time.monotonic()
        start_s = now
        if self.state != BoardState.IDLE:
            start_s = max(start_s, self.expected_done_at)
        latest_end = self.latest_timeline_end_s(include_idle_blinks=False)
        if latest_end is not None:
            start_s = max(start_s, latest_end)

        event = TimelineNeckStretch(
            start_s=start_s,
            pitch_deg=self.config.neck_stretch_pitch_deg,
            yaw_deg=self.config.neck_stretch_yaw_deg,
            tilt_deg=self.config.neck_stretch_tilt_deg,
            duration_ms=self.config.neck_stretch_duration_ms,
            settle_ms=self.config.neck_stretch_settle_ms,
        )
        # Future idle blinks are rescheduled after explicit animation work.
        self.blink_events = [
            blink for blink in self.blink_events
            if blink.reason != "idle" or blink.start_s < start_s
        ]
        self.neck_stretch_events.append(event)
        self.next_idle_blink_at = max(
            self.next_idle_blink_at,
            self.timeline_neck_stretch_end_s(event) + self.config.blink_interval_s,
        )
        return event

    def schedule_speech_motion(self, duration_ms: float | None = None) -> TimelineSpeechMotion:
        now = time.monotonic()
        start_s = now
        if self.state != BoardState.IDLE:
            start_s = max(start_s, self.expected_done_at)

        event = TimelineSpeechMotion(
            start_s=start_s,
            duration_ms=duration_ms or self.config.speech_motion_chunk_ms,
            settle_ms=self.config.speech_motion_settle_ms,
            yaw_deg=self.config.speech_motion_yaw_deg,
            pitch_deg=self.config.speech_motion_pitch_deg,
            tilt_deg=self.config.speech_motion_tilt_deg,
            cycle_ms=self.config.speech_motion_cycle_ms,
        )
        self.blink_events = [
            blink for blink in self.blink_events
            if blink.reason != "idle" or blink.start_s < start_s
        ]
        self.speech_motion_events.append(event)
        self.next_idle_blink_at = max(
            self.next_idle_blink_at,
            self.timeline_speech_motion_end_s(event) + self.config.blink_interval_s,
        )
        return event

    def ensure_idle_blink_due(self, now: float) -> None:
        if not self.idle_blink_enabled or now < self.next_idle_blink_at:
            return
        if self.latest_explicit_gaze_end_s() is not None:
            latest_gaze_end = self.latest_explicit_gaze_end_s()
            if latest_gaze_end is not None and now < latest_gaze_end:
                self.next_idle_blink_at = latest_gaze_end + self.config.blink_interval_s
                return
        self.schedule_blink_at(now, reason="idle", force=False)
        self.next_idle_blink_at = max(self.next_idle_blink_at, now + self.config.blink_interval_s)

    def prune_timeline(self, now: float) -> None:
        self.blink_events = [
            event for event in self.blink_events
            if event.reason != "idle" or self.timeline_blink_end_s(event) > now
        ]

    def collect_render_window(
        self,
        render_start_s: float,
    ) -> tuple[
        tuple[TimelineGaze, ...],
        tuple[TimelineBlink, ...],
        tuple[TimelineNeckStretch, ...],
        tuple[TimelineSpeechMotion, ...],
        float,
    ] | None:
        all_events: list[TimelineGaze | TimelineBlink | TimelineNeckStretch | TimelineSpeechMotion] = [
            *self.gaze_events,
            *self.blink_events,
            *self.neck_stretch_events,
            *self.speech_motion_events,
        ]
        included: set[TimelineGaze | TimelineBlink | TimelineNeckStretch | TimelineSpeechMotion] = set()
        block_end_s = render_start_s

        while True:
            added = False
            for event in sorted(all_events, key=lambda item: item.start_s):
                if event in included:
                    continue
                event_end_s = self.timeline_event_effective_end_s(event, render_start_s)
                if event.start_s <= block_end_s + 0.001 and event_end_s > render_start_s:
                    included.add(event)
                    block_end_s = max(block_end_s, event_end_s)
                    added = True
            if not added:
                break

        if not included:
            return None

        gazes = tuple(event for event in included if isinstance(event, TimelineGaze))
        blinks = tuple(event for event in included if isinstance(event, TimelineBlink))
        stretches = tuple(event for event in included if isinstance(event, TimelineNeckStretch))
        speech_motions = tuple(
            event for event in included if isinstance(event, TimelineSpeechMotion)
        )
        return gazes, blinks, stretches, speech_motions, block_end_s

    def gaze_curves_for_window(
        self,
        render_start_s: float,
        render_end_s: float,
        gazes: tuple[TimelineGaze, ...],
    ) -> tuple[YawTargetCurve, YawTargetCurve]:
        current_yaw, current_pitch = self.estimated_gaze()
        if not gazes:
            duration_ms = max(1.0, (render_end_s - render_start_s) * 1000.0)
            return (
                YawTargetCurve((HoldYaw(0.0, duration_ms, current_yaw),)),
                YawTargetCurve((HoldYaw(0.0, duration_ms, current_pitch),)),
            )

        yaw_segments: list[HoldYaw] = []
        pitch_segments: list[HoldYaw] = []
        cursor_ms = 0.0
        hold_yaw = current_yaw
        hold_pitch = current_pitch

        for gaze in sorted(gazes, key=lambda event: event.start_s):
            start_ms = max(0.0, (gaze.start_s - render_start_s) * 1000.0)
            end_ms = start_ms + max(1.0, gaze.dwell_s * 1000.0)
            if start_ms > cursor_ms:
                yaw_segments.append(HoldYaw(cursor_ms, start_ms, hold_yaw))
                pitch_segments.append(HoldYaw(cursor_ms, start_ms, hold_pitch))
            yaw_segments.append(HoldYaw(start_ms, end_ms, gaze.yaw))
            pitch_segments.append(HoldYaw(start_ms, end_ms, gaze.pitch))
            cursor_ms = end_ms
            hold_yaw = gaze.yaw
            hold_pitch = gaze.pitch

        return YawTargetCurve(tuple(yaw_segments)), YawTargetCurve(tuple(pitch_segments))

    def build_timeline_render(self, render_start_s: float) -> TimelineRender | None:
        window = self.collect_render_window(render_start_s)
        if window is None:
            return None
        gazes, blinks, stretches, speech_motions, render_end_s = window
        yaw_curve, pitch_curve = self.gaze_curves_for_window(render_start_s, render_end_s, gazes)
        blink_events = tuple(self.timeline_blink_event(blink, render_start_s) for blink in blinks)
        neck_stretch_events = tuple(
            self.timeline_neck_stretch_event(stretch, render_start_s)
            for stretch in stretches
        )
        speech_motion_events = tuple(
            self.timeline_speech_motion_event(speech, render_start_s)
            for speech in speech_motions
        )
        has_neck_overlays = bool(neck_stretch_events or speech_motion_events)
        channel_count = len(GAZE_TO_STRETCH_CHANNELS) if has_neck_overlays else len(GAZE_TO_CHANNELS)
        render_duration_ms = merged_curve_end_ms(
            yaw_curve,
            pitch_curve,
            blink_events,
            neck_stretch_events,
            speech_motion_events,
        )
        sample_ms = sample_ms_for_merged_script(
            min(
                self.config.gaze_sample_ms,
                self.config.neck_stretch_sample_ms,
                self.config.speech_motion_sample_ms,
            )
            if has_neck_overlays
            else self.config.gaze_sample_ms,
            channel_count,
            yaw_curve,
            pitch_curve,
            blink_events,
            neck_stretch_events,
            speech_motion_events,
        )
        render_config = GazeCornersConfig(sample_ms=sample_ms)
        if has_neck_overlays:
            render_config = GazeCornersConfig(
                sample_ms=sample_ms,
                eye_tau_ms=NECK_STRETCH_EYE_TAU_MS,
                eye_pitch_limit_deg=NECK_STRETCH_EYE_PITCH_LIMIT_DEG,
                neck_stretch_yaw_tau_ms=NECK_STRETCH_NECK_YAW_TAU_MS,
                neck_stretch_pitch_tau_ms=NECK_STRETCH_NECK_PITCH_TAU_MS,
                neck_stretch_tilt_tau_ms=NECK_STRETCH_NECK_TILT_TAU_MS,
            )
        rendered, _samples = render_gaze_corners_curves(
            yaw_curve,
            pitch_curve,
            config=render_config,
            name="engine_timeline",
            initial_state=self.current_controller_state(),
            include_eyelids=True,
            eyelid_offset=self.config.eyelid_offset,
            blink_events=blink_events,
            neck_stretch_events=neck_stretch_events,
            speech_motion_events=speech_motion_events,
        )
        return TimelineRender(
            command=rendered.command(),
            duration_s=rendered_duration_s(rendered.keyframes),
            render_duration_ms=render_duration_ms,
            gazes=gazes,
            blinks=blinks,
            stretches=stretches,
            speech_motions=speech_motions,
            frame_count=len(rendered.keyframes),
        )

    def run_timeline_render(self, render_start_s: float) -> bool:
        rendered = self.build_timeline_render(render_start_s)
        if rendered is None:
            return False

        blink_reasons = ",".join(sorted({event.reason for event in rendered.blinks})) or "none"
        self.log(
            f"timeline render_ms={rendered.render_duration_ms:g} "
            f"frames={rendered.frame_count} gazes={len(rendered.gazes)} "
            f"blinks={len(rendered.blinks)} stretches={len(rendered.stretches)} "
            f"speech={len(rendered.speech_motions)} "
            f"blink_reasons={blink_reasons}"
        )
        self.send_rendered_command(rendered.command, script_duration_s=rendered.duration_s)
        self.gaze_events = [event for event in self.gaze_events if event not in rendered.gazes]
        self.blink_events = [event for event in self.blink_events if event not in rendered.blinks]
        self.neck_stretch_events = [
            event for event in self.neck_stretch_events
            if event not in rendered.stretches
        ]
        self.speech_motion_events = [
            event for event in self.speech_motion_events
            if event not in rendered.speech_motions
        ]
        return True

    def maybe_start_next(self) -> None:
        if self.state != BoardState.IDLE:
            return

        now = time.monotonic()
        self.prune_timeline(now)
        self.ensure_idle_blink_due(now)
        try:
            self.run_timeline_render(now)
        except ValueError as exc:
            self.log(f"timeline render error: {exc}")

    def print_status(self) -> None:
        feedback_age = "none"
        if self.last_feedback_at is not None:
            feedback_age = f"{time.monotonic() - self.last_feedback_at:.2f}s"
        now = time.monotonic()
        next_event = "none"
        all_events: list[TimelineGaze | TimelineBlink | TimelineNeckStretch | TimelineSpeechMotion] = [
            *self.gaze_events,
            *self.blink_events,
            *self.neck_stretch_events,
            *self.speech_motion_events,
        ]
        if all_events:
            event = min(all_events, key=lambda item: item.start_s)
            next_event = f"{type(event).__name__}@{max(0.0, event.start_s - now):.2f}s"
        self.log(
            f"state={self.state.value} feedback_age={feedback_age} "
            f"timeline_gazes={len(self.gaze_events)} timeline_blinks={len(self.blink_events)} "
            f"timeline_stretches={len(self.neck_stretch_events)} "
            f"timeline_speech={len(self.speech_motion_events)} "
            f"next={next_event} idle_blink={self.idle_blink_enabled}"
        )

    def process_line(self, line: str) -> None:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return

        parts = stripped.split()
        command = parts[0].lower()
        try:
            if command in {"quit", "exit"}:
                self.running = False
            elif command == "help":
                self.log(
                    "commands: gaze YAW[,PITCH] [MS] | gaze YAW PITCH [MS] | "
                    "blink | stretch | speech [MS] | reset | idle on|off | interval SEC | status | quit"
                )
            elif command == "status":
                self.print_status()
            elif command in {"reset", "reset-pose", "reset_pose"}:
                self.reset_pose()
            elif command == "blink":
                event = self.schedule_manual_blink()
                if event is None:
                    self.log("blink already scheduled")
                else:
                    self.log(f"scheduled blink at +{max(0.0, event.start_s - time.monotonic()):.2f}s")
            elif command in {"stretch", "neck-stretch", "neck_stretch"}:
                event = self.schedule_neck_stretch()
                self.log(
                    f"scheduled neck stretch at +{max(0.0, event.start_s - time.monotonic()):.2f}s"
                )
            elif command in {"speech", "talk"}:
                duration_ms = float(parts[1]) if len(parts) >= 2 else self.config.speech_motion_chunk_ms
                event = self.schedule_speech_motion(duration_ms)
                self.log(
                    f"scheduled speech motion ms={event.duration_ms:g} "
                    f"at +{max(0.0, event.start_s - time.monotonic()):.2f}s"
                )
            elif command in {"gaze", "gaze-to", "look"}:
                gaze = parse_gaze_line(parts)
                event = self.schedule_gaze(gaze)
                self.log(
                    f"scheduled gaze yaw={event.yaw:g} pitch={event.pitch:g} "
                    f"ms={event.dwell_s * 1000:g} at +{max(0.0, event.start_s - time.monotonic()):.2f}s"
                )
            elif command == "idle":
                if len(parts) != 2 or parts[1].lower() not in {"on", "off"}:
                    raise ValueError("usage: idle on|off")
                self.idle_blink_enabled = parts[1].lower() == "on"
                self.blink_events = [event for event in self.blink_events if event.reason != "idle"]
                self.next_idle_blink_at = time.monotonic() + self.config.blink_interval_s
                self.log(f"idle blink {'on' if self.idle_blink_enabled else 'off'}")
            elif command == "interval":
                if len(parts) != 2:
                    raise ValueError("usage: interval SECONDS")
                interval = float(parts[1])
                if interval <= 0:
                    raise ValueError("interval must be > 0")
                self.config = EngineConfig(
                    **{**self.config.__dict__, "blink_interval_s": interval}
                )
                self.blink_events = [event for event in self.blink_events if event.reason != "idle"]
                self.next_idle_blink_at = time.monotonic() + interval
                self.log(f"blink interval {interval:g}s")
            elif command == "poweroff":
                self.send_wire_command(POWER_OFF, expected_duration_s=POWER_OFF.delay_after)
            else:
                self.log(f"unknown command: {command}")
        except ValueError as exc:
            self.log(f"error: {exc}")

    def read_stdin(self) -> None:
        while True:
            readable, _, _ = select.select([sys.stdin], [], [], 0)
            if not readable:
                return
            line = sys.stdin.readline()
            if line == "":
                self.running = False
                return
            self.process_line(line)

    def run(self) -> None:
        self.open()
        try:
            while self.running:
                self.read_serial()
                self.update_board_state()
                self.read_stdin()
                self.maybe_start_next()
                time.sleep(0.01)
        finally:
            self.close()


def main() -> int:
    args = parse_args()
    if args.blink_interval <= 0:
        print("--blink-interval must be > 0", file=sys.stderr)
        return 2
    if args.gaze_blink_threshold < 0:
        print("--gaze-blink-threshold must be >= 0", file=sys.stderr)
        return 2
    if args.gaze_blink_refractory < 0:
        print("--gaze-blink-refractory must be >= 0", file=sys.stderr)
        return 2
    if args.neck_stretch_sample_ms <= 0:
        print("--neck-stretch-sample-ms must be > 0", file=sys.stderr)
        return 2
    if args.neck_stretch_duration_ms <= 0:
        print("--neck-stretch-duration-ms must be > 0", file=sys.stderr)
        return 2
    if args.neck_stretch_settle_ms < 0:
        print("--neck-stretch-settle-ms must be >= 0", file=sys.stderr)
        return 2
    if min(args.neck_stretch_pitch, args.neck_stretch_yaw, args.neck_stretch_tilt) < 0:
        print("--neck-stretch-* amplitudes must be >= 0", file=sys.stderr)
        return 2
    if args.speech_motion_sample_ms <= 0:
        print("--speech-motion-sample-ms must be > 0", file=sys.stderr)
        return 2
    if args.speech_motion_chunk_ms <= 0:
        print("--speech-motion-chunk-ms must be > 0", file=sys.stderr)
        return 2
    if args.speech_motion_cycle_ms <= 0:
        print("--speech-motion-cycle-ms must be > 0", file=sys.stderr)
        return 2
    if args.speech_motion_settle_ms < 0:
        print("--speech-motion-settle-ms must be >= 0", file=sys.stderr)
        return 2
    if min(args.speech_motion_yaw, args.speech_motion_pitch, args.speech_motion_tilt) < 0:
        print("--speech-motion amplitudes must be >= 0", file=sys.stderr)
        return 2
    config = EngineConfig(
        port=args.port,
        baudrate=args.baudrate,
        gaze_sample_ms=args.gaze_sample_ms,
        eyelid_offset=args.eyelid_offset,
        idle_blink=args.idle_blink,
        blink_interval_s=args.blink_interval,
        gaze_blink_threshold_deg=args.gaze_blink_threshold,
        gaze_blink_refractory_s=args.gaze_blink_refractory,
        blink_close_ms=args.blink_close_ms,
        blink_hold_ms=args.blink_hold_ms,
        blink_open_ms=args.blink_open_ms,
        blink_closed_angle=args.blink_closed_angle,
        neck_stretch_sample_ms=args.neck_stretch_sample_ms,
        neck_stretch_pitch_deg=args.neck_stretch_pitch,
        neck_stretch_yaw_deg=args.neck_stretch_yaw,
        neck_stretch_tilt_deg=args.neck_stretch_tilt,
        neck_stretch_duration_ms=args.neck_stretch_duration_ms,
        neck_stretch_settle_ms=args.neck_stretch_settle_ms,
        speech_motion_sample_ms=args.speech_motion_sample_ms,
        speech_motion_chunk_ms=args.speech_motion_chunk_ms,
        speech_motion_yaw_deg=args.speech_motion_yaw,
        speech_motion_pitch_deg=args.speech_motion_pitch,
        speech_motion_tilt_deg=args.speech_motion_tilt,
        speech_motion_cycle_ms=args.speech_motion_cycle_ms,
        speech_motion_settle_ms=args.speech_motion_settle_ms,
        feedback_silence_s=args.feedback_silence,
        done_fallback_s=args.done_fallback,
        verbose=args.verbose,
    )
    try:
        RobotEngine(config).run()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
