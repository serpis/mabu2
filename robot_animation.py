#!/usr/bin/env python3

from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass
from typing import Mapping, Sequence

from robot_motion import (
    BAUDRATE,
    CHANNELS,
    CHANNEL_ORDER,
    Command,
    fletcher16,
    KNOWN_CHANNEL_MASK,
    POWER_OFF,
    SCRIPT_TICK_SECONDS,
    STARTUP_COMMANDS,
    RobotMotion,
    format_hex,
    look_targets,
    packet,
    print_rx,
    pose_command,
    position_command,
    raw_keyframes_command,
    read_available,
    read_command,
    validate_byte_value,
)


STARTUP_TARGETS = {
    "eyelid_left": 0x7C,
    "eyelid_right": 0x7C,
    "eye_leftright": 0x7F,
    "eye_updown": 0x7F,
    "neck_elevation": 0x7F,
    "neck_rotation": 0x7F,
    "neck_tilt": 0x7F,
}
LOOK_CHANNELS = (
    "eyelid_left",
    "eyelid_right",
    "eye_leftright",
    "eye_updown",
    "neck_elevation",
    "neck_rotation",
)
YAW_GAZE_CHANNELS = ("eye_leftright", "neck_rotation")
BLINK_CHANNELS = ("eyelid_left", "eyelid_right")
CORNER_GAZE_CHANNELS = ("eye_leftright", "eye_updown", "neck_elevation", "neck_rotation")
GAZE_TO_CHANNELS = LOOK_CHANNELS
NECK_ROTATION_MASK = CHANNELS["neck_rotation"].mask
MEASURED_CHANNEL_MAX_SPEED_DPS = {
    "eyelid_left": 220.0,
    "eyelid_right": 220.0,
    "eye_leftright": 350.0,
    "eye_updown": 450.0,
    "neck_elevation": 75.0,
    "neck_rotation": 60.0,
    "neck_tilt": 55.0,
}
GAZE_YAW_DEFAULT_EYE_MAX_SPEED_DPS = MEASURED_CHANNEL_MAX_SPEED_DPS["eye_leftright"]
GAZE_YAW_DEFAULT_NECK_MAX_SPEED_DPS = MEASURED_CHANNEL_MAX_SPEED_DPS["neck_rotation"]
DEMO_GAZE_YAW_ALL_PARTS = (1, 2, 3)
BLINK_DEFAULT_CLOSED_ANGLE = -65.0
BLINK_DEFAULT_CLOSE_MS = 360.0
BLINK_DEFAULT_HOLD_MS = 0.0
BLINK_DEFAULT_OPEN_MS = 360.0
REACHABLE_RANGE_EPS_DEG = 0.05


@dataclass(frozen=True)
class AnimationFrame:
    targets: Mapping[str, int]
    duration_ms: float


@dataclass(frozen=True)
class RenderedAnimation:
    name: str
    mask: int
    channels: tuple[str, ...]
    keyframes: tuple[tuple[int, ...], ...]

    def command(self) -> Command:
        command = raw_keyframes_command(self.mask, self.keyframes)
        return Command(self.name, command.payload, command.delay_after, command.show_rx)

    def raw_keyframes_text(self) -> str:
        return ";".join(",".join(str(value) for value in frame) for frame in self.keyframes)


@dataclass(frozen=True)
class GazeYawSample:
    time_ms: float
    target_yaw: float
    eye_yaw: float
    neck_yaw: float
    eye_byte: int
    neck_byte: int


@dataclass(frozen=True)
class GazeControllerState:
    eye_yaw: float
    eye_pitch: float
    neck_yaw: float
    neck_pitch: float
    eyelid_left: float | None = None
    eyelid_right: float | None = None


@dataclass(frozen=True)
class GazeCornerSample:
    time_ms: float
    target_yaw: float
    target_pitch: float
    eye_yaw: float
    eye_pitch: float
    neck_yaw: float
    neck_pitch: float
    eye_yaw_byte: int
    eye_pitch_byte: int
    eyelid_left_byte: int
    eyelid_right_byte: int
    neck_yaw_byte: int
    neck_pitch_byte: int


@dataclass(frozen=True)
class BlinkSample:
    time_ms: float
    eyelid_left_angle: float
    eyelid_right_angle: float
    eyelid_left_byte: int
    eyelid_right_byte: int


@dataclass(frozen=True)
class BlinkEvent:
    start_ms: float
    close_ms: float = BLINK_DEFAULT_CLOSE_MS
    hold_ms: float = BLINK_DEFAULT_HOLD_MS
    open_ms: float = BLINK_DEFAULT_OPEN_MS
    closed_angle: float = BLINK_DEFAULT_CLOSED_ANGLE


@dataclass(frozen=True)
class NeckSpeedSample:
    time_s: float
    value: int
    angle: float


@dataclass(frozen=True)
class FeedbackFrame:
    time_s: float
    values: tuple[int, ...]

    def value(self, channel: str) -> int:
        validate_channel_name(channel)
        return self.values[CHANNEL_ORDER.index(channel)]

    def angle(self, channel: str) -> float:
        return CHANNELS[channel].angle_from_byte(self.value(channel))


@dataclass(frozen=True)
class ChannelSpeedSample:
    time_s: float
    value: int
    angle: float


@dataclass(frozen=True)
class ChannelSpeedSummary:
    channel: str
    sample_count: int
    initial_value: int | None
    target_value: int
    initial_angle: float | None
    target_angle: float
    peak_byte_s: float | None
    peak_deg_s: float | None
    t_10_90_s: float | None
    avg_byte_s_10_90: float | None
    avg_deg_s_10_90: float | None
    t_to_3_bytes_s: float | None
    t_to_2_deg_s: float | None


@dataclass(frozen=True)
class RobotAnimation:
    name: str
    frames: tuple[AnimationFrame, ...]

    def then(self, *others: "RobotAnimation", name: str | None = None) -> "RobotAnimation":
        frames = list(self.frames)
        names = [self.name]
        for other in others:
            frames.extend(other.frames)
            names.append(other.name)
        return RobotAnimation(name or "+".join(names), tuple(frames))

    def render(self, channels: Sequence[str] | None = None) -> RenderedAnimation:
        if not self.frames:
            raise ValueError("animation must contain at least one frame")

        render_channels = tuple(channels) if channels is not None else channels_from_frames(self.frames)
        validate_channels(render_channels)

        mask = channels_mask(render_channels)
        keyframes: list[tuple[int, ...]] = []
        current_targets = STARTUP_TARGETS.copy()

        for frame_index, frame in enumerate(self.frames, start=1):
            if frame.duration_ms <= 0:
                raise ValueError(f"frame {frame_index} duration_ms must be > 0")
            for name, value in frame.targets.items():
                validate_channel_name(name)
                current_targets[name] = validate_byte_value(value, f"frame {frame_index} {name}")

            target_values = tuple(current_targets[name] for name in render_channels)
            for duration_ticks in duration_ms_to_tick_chunks(frame.duration_ms):
                keyframes.append((*target_values, duration_ticks))

        max_frames = (255 - 3) // (len(render_channels) + 1)
        if len(keyframes) > max_frames:
            raise ValueError(
                f"rendered animation uses {len(keyframes)} frames for {len(render_channels)} channels; "
                f"max is {max_frames} in one motorboard script"
            )

        return RenderedAnimation(self.name, mask, render_channels, tuple(keyframes))


@dataclass(frozen=True)
class GazePoint:
    yaw: float
    pitch: float
    duration_ms: float
    eyelid_offset: float = 0.0


@dataclass(frozen=True)
class HoldYaw:
    start_ms: float
    end_ms: float
    yaw: float

    def sample(self, t_ms: float) -> float:
        return self.yaw


@dataclass(frozen=True)
class LinearYaw:
    start_ms: float
    end_ms: float
    start_yaw: float
    end_yaw: float

    def sample(self, t_ms: float) -> float:
        u = normalized_time(t_ms, self.start_ms, self.end_ms)
        return self.start_yaw + (self.end_yaw - self.start_yaw) * u


@dataclass(frozen=True)
class EaseYaw:
    start_ms: float
    end_ms: float
    start_yaw: float
    end_yaw: float

    def sample(self, t_ms: float) -> float:
        u = normalized_time(t_ms, self.start_ms, self.end_ms)
        eased = u * u * (3.0 - 2.0 * u)
        return self.start_yaw + (self.end_yaw - self.start_yaw) * eased


YawSegment = HoldYaw | LinearYaw | EaseYaw


@dataclass(frozen=True)
class YawTargetCurve:
    segments: tuple[YawSegment, ...]

    def __post_init__(self) -> None:
        if not self.segments:
            raise ValueError("yaw target curve must contain at least one segment")
        previous_end = None
        for index, segment in enumerate(self.segments, start=1):
            if segment.end_ms <= segment.start_ms:
                raise ValueError(f"yaw segment {index} must have end_ms > start_ms")
            if previous_end is not None and segment.start_ms < previous_end:
                raise ValueError(f"yaw segment {index} overlaps the previous segment")
            previous_end = segment.end_ms

    @property
    def start_ms(self) -> float:
        return self.segments[0].start_ms

    @property
    def end_ms(self) -> float:
        return self.segments[-1].end_ms

    @property
    def duration_ms(self) -> float:
        return self.end_ms - self.start_ms

    def sample(self, t_ms: float) -> float:
        if t_ms < self.start_ms:
            return self.segments[0].sample(self.start_ms)
        for segment in reversed(self.segments):
            if t_ms >= segment.start_ms:
                return segment.sample(t_ms)
        return self.segments[0].sample(t_ms)


@dataclass(frozen=True)
class GazeYawConfig:
    sample_ms: float = 50.0
    eye_tau_ms: float = 70.0
    neck_tau_ms: float = 650.0
    eye_max_speed_dps: float = GAZE_YAW_DEFAULT_EYE_MAX_SPEED_DPS
    neck_max_speed_dps: float = GAZE_YAW_DEFAULT_NECK_MAX_SPEED_DPS
    eye_limit_deg: float = 15.0


@dataclass(frozen=True)
class GazeCornersConfig:
    sample_ms: float = 50.0
    eye_tau_ms: float = 70.0
    eyelid_tau_ms: float = 70.0
    neck_yaw_tau_ms: float = 650.0
    neck_pitch_tau_ms: float = 500.0
    eye_yaw_max_speed_dps: float = MEASURED_CHANNEL_MAX_SPEED_DPS["eye_leftright"]
    eye_pitch_max_speed_dps: float = MEASURED_CHANNEL_MAX_SPEED_DPS["eye_updown"]
    eyelid_max_speed_dps: float = min(
        MEASURED_CHANNEL_MAX_SPEED_DPS["eyelid_left"],
        MEASURED_CHANNEL_MAX_SPEED_DPS["eyelid_right"],
    )
    neck_yaw_max_speed_dps: float = MEASURED_CHANNEL_MAX_SPEED_DPS["neck_rotation"]
    neck_pitch_max_speed_dps: float = MEASURED_CHANNEL_MAX_SPEED_DPS["neck_elevation"]
    eye_yaw_limit_deg: float = 15.0
    eye_pitch_limit_deg: float = 10.0


def gaze_animation(points: Sequence[GazePoint], *, name: str = "gaze") -> RobotAnimation:
    return RobotAnimation(
        name=name,
        frames=tuple(
            AnimationFrame(
                targets=look_targets(point.yaw, point.pitch, point.eyelid_offset),
                duration_ms=point.duration_ms,
            )
            for point in points
        ),
    )


def blink_base_eyelid_angle(eye_pitch: float, eyelid_offset: float) -> float:
    return eyelid_offset - eye_pitch


def blink_phase_ticks(duration_ms: float, name: str) -> int:
    if duration_ms <= 0:
        raise ValueError(f"{name} must be > 0")
    ticks = duration_ms_to_ticks(duration_ms)
    if ticks > 255:
        raise ValueError(f"{name} is too long for one blink phase; max is {255 * SCRIPT_TICK_SECONDS * 1000:.0f} ms")
    return ticks


def render_blink(
    base_eyelid_left_angle: float,
    base_eyelid_right_angle: float,
    *,
    closed_angle: float = BLINK_DEFAULT_CLOSED_ANGLE,
    close_ms: float = BLINK_DEFAULT_CLOSE_MS,
    hold_ms: float = BLINK_DEFAULT_HOLD_MS,
    open_ms: float = BLINK_DEFAULT_OPEN_MS,
    name: str = "blink",
) -> tuple[RenderedAnimation, tuple[BlinkSample, ...]]:
    validate_channel_angle("eyelid_left", base_eyelid_left_angle, "base eyelid_left angle")
    validate_channel_angle("eyelid_right", base_eyelid_right_angle, "base eyelid_right angle")
    validate_channel_angle("eyelid_left", closed_angle, "closed eyelid angle")
    validate_channel_angle("eyelid_right", closed_angle, "closed eyelid angle")

    close_ticks = blink_phase_ticks(close_ms, "blink close_ms")
    open_ticks = blink_phase_ticks(open_ms, "blink open_ms")
    if hold_ms < 0:
        raise ValueError("blink hold_ms must be >= 0")
    hold_ticks = duration_ms_to_ticks(hold_ms) if hold_ms > 0 else 0
    if hold_ticks > 255:
        raise ValueError(f"blink hold_ms is too long for one blink phase; max is {255 * SCRIPT_TICK_SECONDS * 1000:.0f} ms")

    closed_left = CHANNELS["eyelid_left"].byte_from_angle(closed_angle)
    closed_right = CHANNELS["eyelid_right"].byte_from_angle(closed_angle)
    base_left = CHANNELS["eyelid_left"].byte_from_angle(base_eyelid_left_angle)
    base_right = CHANNELS["eyelid_right"].byte_from_angle(base_eyelid_right_angle)

    # Keep identical closed targets as separate phases: close first, optional hold second.
    keyframes: list[tuple[int, ...]] = [(closed_left, closed_right, close_ticks)]
    if hold_ticks:
        keyframes.append((closed_left, closed_right, hold_ticks))
    keyframes.append((base_left, base_right, open_ticks))

    samples = [
        BlinkSample(close_ms, closed_angle, closed_angle, closed_left, closed_right),
    ]
    if hold_ms > 0:
        samples.append(BlinkSample(close_ms + hold_ms, closed_angle, closed_angle, closed_left, closed_right))
    samples.append(
        BlinkSample(
            close_ms + hold_ms + open_ms,
            base_eyelid_left_angle,
            base_eyelid_right_angle,
            base_left,
            base_right,
        )
    )

    return (
        RenderedAnimation(name, channels_mask(BLINK_CHANNELS), BLINK_CHANNELS, tuple(keyframes)),
        tuple(samples),
    )


def blink_event_weight(t_ms: float, event: BlinkEvent) -> float:
    if event.start_ms < 0:
        raise ValueError("blink event start_ms must be >= 0")
    if event.close_ms <= 0 or event.open_ms <= 0:
        raise ValueError("blink close/open duration must be > 0")
    if event.hold_ms < 0:
        raise ValueError("blink hold duration must be >= 0")

    close_end = event.start_ms + event.close_ms
    hold_end = close_end + event.hold_ms
    open_end = hold_end + event.open_ms
    if t_ms < event.start_ms or t_ms > open_end:
        return 0.0
    if t_ms <= close_end:
        return normalized_time(t_ms, event.start_ms, close_end)
    if t_ms <= hold_end:
        return 1.0
    return 1.0 - normalized_time(t_ms, hold_end, open_end)


def blink_weight(t_ms: float, events: Sequence[BlinkEvent]) -> float:
    if not events:
        return 0.0
    return clamp(max(blink_event_weight(t_ms, event) for event in events), 0.0, 1.0)


def parse_blink_times(value: str) -> tuple[float, ...]:
    try:
        times = tuple(float(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("blink times must be comma-separated milliseconds") from exc
    if not times:
        raise argparse.ArgumentTypeError("blink time list must not be empty")
    if any(time_ms < 0 for time_ms in times):
        raise argparse.ArgumentTypeError("blink times must be >= 0")
    return times


def demo_gaze_yaw_curve(parts: Sequence[int] = DEMO_GAZE_YAW_ALL_PARTS) -> YawTargetCurve:
    validate_demo_gaze_yaw_parts(parts)
    parts = tuple(parts)
    if parts == DEMO_GAZE_YAW_ALL_PARTS:
        return YawTargetCurve(
            (
                HoldYaw(0.0, 500.0, 0.0),
                HoldYaw(500.0, 1600.0, 25.0),
                HoldYaw(1600.0, 2600.0, -20.0),
                LinearYaw(2600.0, 4100.0, -20.0, 20.0),
                HoldYaw(4100.0, 4200.0, 20.0),
            )
        )

    segments: list[YawSegment] = []
    cursor_ms = 0.0
    for part in parts:
        if part == 1:
            segments.extend(
                (
                    HoldYaw(cursor_ms, cursor_ms + 500.0, 0.0),
                    HoldYaw(cursor_ms + 500.0, cursor_ms + 1600.0, 25.0),
                )
            )
            cursor_ms += 1600.0
        elif part == 2:
            segments.extend(
                (
                    HoldYaw(cursor_ms, cursor_ms + 500.0, 25.0),
                    HoldYaw(cursor_ms + 500.0, cursor_ms + 1500.0, -20.0),
                )
            )
            cursor_ms += 1500.0
        elif part == 3:
            segments.extend(
                (
                    HoldYaw(cursor_ms, cursor_ms + 500.0, -20.0),
                    LinearYaw(cursor_ms + 500.0, cursor_ms + 2000.0, -20.0, 20.0),
                    HoldYaw(cursor_ms + 2000.0, cursor_ms + 2100.0, 20.0),
                )
            )
            cursor_ms += 2100.0
    return YawTargetCurve(tuple(segments))


def gaze_to_curves(
    target_yaw: float,
    target_pitch: float,
    dwell_ms: float,
) -> tuple[YawTargetCurve, YawTargetCurve]:
    if dwell_ms <= 0:
        raise ValueError("gaze_to dwell duration must be > 0")
    yaw_min = CHANNELS["neck_rotation"].min_angle + CHANNELS["eye_leftright"].min_angle
    yaw_max = CHANNELS["neck_rotation"].max_angle + CHANNELS["eye_leftright"].max_angle
    pitch_min = CHANNELS["neck_elevation"].min_angle + CHANNELS["eye_updown"].min_angle
    pitch_max = CHANNELS["neck_elevation"].max_angle + CHANNELS["eye_updown"].max_angle
    target_yaw = clamp_reachable_angle(target_yaw, yaw_min, yaw_max, "gaze_to yaw")
    target_pitch = clamp_reachable_angle(target_pitch, pitch_min, pitch_max, "gaze_to pitch")
    return (
        YawTargetCurve((HoldYaw(0.0, dwell_ms, target_yaw),)),
        YawTargetCurve((HoldYaw(0.0, dwell_ms, target_pitch),)),
    )


def clamp_reachable_angle(value: float, minimum: float, maximum: float, name: str) -> float:
    if minimum <= value <= maximum:
        return value
    if minimum - REACHABLE_RANGE_EPS_DEG <= value < minimum:
        return minimum
    if maximum < value <= maximum + REACHABLE_RANGE_EPS_DEG:
        return maximum
    raise ValueError(
        f"{name} {value:g} deg outside reachable range "
        f"{minimum:.1f}..{maximum:.1f}"
    )


def demo_gaze_corners_curves(
    *,
    yaw_deg: float = 20.0,
    pitch_deg: float = 10.0,
    settle_ms: float = 200.0,
    hold_ms: float = 450.0,
    return_ms: float = 600.0,
) -> tuple[YawTargetCurve, YawTargetCurve]:
    if yaw_deg <= 0 or pitch_deg <= 0:
        raise ValueError("corner yaw/pitch amplitudes must be > 0")
    if settle_ms <= 0:
        raise ValueError("corner settle duration must be > 0")
    if hold_ms <= 0:
        raise ValueError("corner hold duration must be > 0")
    if return_ms <= 0:
        raise ValueError("corner return duration must be > 0")

    points = (
        (-yaw_deg, pitch_deg),
        (yaw_deg, pitch_deg),
        (yaw_deg, -pitch_deg),
        (-yaw_deg, -pitch_deg),
        (0.0, 0.0),
    )
    yaw_segments: list[YawSegment] = [HoldYaw(0.0, settle_ms, 0.0)]
    pitch_segments: list[YawSegment] = [HoldYaw(0.0, settle_ms, 0.0)]
    cursor_ms = settle_ms
    for index, (yaw, pitch) in enumerate(points):
        dwell_ms = return_ms if index == len(points) - 1 else hold_ms
        yaw_segments.append(HoldYaw(cursor_ms, cursor_ms + dwell_ms, yaw))
        pitch_segments.append(HoldYaw(cursor_ms, cursor_ms + dwell_ms, pitch))
        cursor_ms += dwell_ms

    return YawTargetCurve(tuple(yaw_segments)), YawTargetCurve(tuple(pitch_segments))


def render_gaze_corners_curves(
    yaw_curve: YawTargetCurve,
    pitch_curve: YawTargetCurve,
    *,
    config: GazeCornersConfig | None = None,
    name: str = "gaze_corners",
    initial_state: GazeControllerState | None = None,
    include_eyelids: bool = False,
    eyelid_offset: float = -2.0,
    blink_events: Sequence[BlinkEvent] = (),
) -> tuple[RenderedAnimation, tuple[GazeCornerSample, ...]]:
    config = config or GazeCornersConfig()
    render_channels = GAZE_TO_CHANNELS if include_eyelids else CORNER_GAZE_CHANNELS
    if config.sample_ms <= 0:
        raise ValueError("sample_ms must be > 0")
    if yaw_curve.start_ms != pitch_curve.start_ms or yaw_curve.end_ms != pitch_curve.end_ms:
        raise ValueError("yaw and pitch curves must share start and end times")
    if (
        config.eye_tau_ms <= 0
        or config.eyelid_tau_ms <= 0
        or config.neck_yaw_tau_ms <= 0
        or config.neck_pitch_tau_ms <= 0
    ):
        raise ValueError("gaze controller tau values must be > 0")
    if (
        config.eye_yaw_max_speed_dps <= 0
        or config.eye_pitch_max_speed_dps <= 0
        or config.eyelid_max_speed_dps <= 0
        or config.neck_yaw_max_speed_dps <= 0
        or config.neck_pitch_max_speed_dps <= 0
    ):
        raise ValueError("gaze controller max speeds must be > 0")
    if config.eye_yaw_limit_deg <= 0 or config.eye_pitch_limit_deg <= 0:
        raise ValueError("eye limit values must be > 0")
    for event in blink_events:
        blink_event_weight(event.start_ms, event)
        validate_channel_angle("eyelid_left", event.closed_angle, "blink closed angle")
        validate_channel_angle("eyelid_right", event.closed_angle, "blink closed angle")

    if initial_state is None:
        eye_yaw = 0.0
        eye_pitch = 0.0
        neck_yaw = clamp(
            yaw_curve.sample(yaw_curve.start_ms),
            CHANNELS["neck_rotation"].min_angle,
            CHANNELS["neck_rotation"].max_angle,
        )
        neck_pitch = clamp(
            pitch_curve.sample(pitch_curve.start_ms),
            CHANNELS["neck_elevation"].min_angle,
            CHANNELS["neck_elevation"].max_angle,
        )
        eyelid_left = eyelid_offset - eye_pitch
        eyelid_right = eyelid_offset - eye_pitch
    else:
        eye_yaw = clamp(
            initial_state.eye_yaw,
            CHANNELS["eye_leftright"].min_angle,
            CHANNELS["eye_leftright"].max_angle,
        )
        eye_pitch = clamp(
            initial_state.eye_pitch,
            CHANNELS["eye_updown"].min_angle,
            CHANNELS["eye_updown"].max_angle,
        )
        neck_yaw = clamp(
            initial_state.neck_yaw,
            CHANNELS["neck_rotation"].min_angle,
            CHANNELS["neck_rotation"].max_angle,
        )
        neck_pitch = clamp(
            initial_state.neck_pitch,
            CHANNELS["neck_elevation"].min_angle,
            CHANNELS["neck_elevation"].max_angle,
        )
        default_eyelid = eyelid_offset - eye_pitch
        eyelid_left = clamp(
            initial_state.eyelid_left if initial_state.eyelid_left is not None else default_eyelid,
            CHANNELS["eyelid_left"].min_angle,
            CHANNELS["eyelid_left"].max_angle,
        )
        eyelid_right = clamp(
            initial_state.eyelid_right if initial_state.eyelid_right is not None else default_eyelid,
            CHANNELS["eyelid_right"].min_angle,
            CHANNELS["eyelid_right"].max_angle,
        )
    samples: list[GazeCornerSample] = []
    keyframes: list[tuple[int, ...]] = []

    t_ms = yaw_curve.start_ms
    while t_ms < yaw_curve.end_ms:
        interval_ms = min(config.sample_ms, yaw_curve.end_ms - t_ms)
        target_yaw = yaw_curve.sample(t_ms)
        target_pitch = pitch_curve.sample(t_ms)

        neck_yaw_target = clamp(
            target_yaw,
            CHANNELS["neck_rotation"].min_angle,
            CHANNELS["neck_rotation"].max_angle,
        )
        neck_yaw = step_first_order(
            current=neck_yaw,
            target=neck_yaw_target,
            tau_ms=config.neck_yaw_tau_ms,
            dt_ms=interval_ms,
            max_speed_dps=config.neck_yaw_max_speed_dps,
        )
        neck_pitch_target = clamp(
            target_pitch,
            CHANNELS["neck_elevation"].min_angle,
            CHANNELS["neck_elevation"].max_angle,
        )
        neck_pitch = step_first_order(
            current=neck_pitch,
            target=neck_pitch_target,
            tau_ms=config.neck_pitch_tau_ms,
            dt_ms=interval_ms,
            max_speed_dps=config.neck_pitch_max_speed_dps,
        )

        eye_yaw_min = max(CHANNELS["eye_leftright"].min_angle, -config.eye_yaw_limit_deg)
        eye_yaw_max = min(CHANNELS["eye_leftright"].max_angle, config.eye_yaw_limit_deg)
        desired_eye_yaw = clamp(target_yaw - neck_yaw, eye_yaw_min, eye_yaw_max)
        eye_yaw = step_first_order(
            current=eye_yaw,
            target=desired_eye_yaw,
            tau_ms=config.eye_tau_ms,
            dt_ms=interval_ms,
            max_speed_dps=config.eye_yaw_max_speed_dps,
        )
        eye_pitch_min = max(CHANNELS["eye_updown"].min_angle, -config.eye_pitch_limit_deg)
        eye_pitch_max = min(CHANNELS["eye_updown"].max_angle, config.eye_pitch_limit_deg)
        desired_eye_pitch = clamp(target_pitch - neck_pitch, eye_pitch_min, eye_pitch_max)
        eye_pitch = step_first_order(
            current=eye_pitch,
            target=desired_eye_pitch,
            tau_ms=config.eye_tau_ms,
            dt_ms=interval_ms,
            max_speed_dps=config.eye_pitch_max_speed_dps,
        )

        base_eyelid = eyelid_offset - eye_pitch
        blink_amount = blink_weight(t_ms + interval_ms, blink_events)
        blink_closed_angle = (
            min(event.closed_angle for event in blink_events)
            if blink_events
            else BLINK_DEFAULT_CLOSED_ANGLE
        )
        desired_eyelid = base_eyelid + (blink_closed_angle - base_eyelid) * blink_amount
        eyelid_left = step_first_order(
            current=eyelid_left,
            target=desired_eyelid,
            tau_ms=config.eyelid_tau_ms,
            dt_ms=interval_ms,
            max_speed_dps=config.eyelid_max_speed_dps,
        )
        eyelid_right = step_first_order(
            current=eyelid_right,
            target=desired_eyelid,
            tau_ms=config.eyelid_tau_ms,
            dt_ms=interval_ms,
            max_speed_dps=config.eyelid_max_speed_dps,
        )

        eyelid_left_byte = CHANNELS["eyelid_left"].byte_from_angle(eyelid_left)
        eyelid_right_byte = CHANNELS["eyelid_right"].byte_from_angle(eyelid_right)
        eye_yaw_byte = CHANNELS["eye_leftright"].byte_from_angle(eye_yaw)
        eye_pitch_byte = CHANNELS["eye_updown"].byte_from_angle(eye_pitch)
        neck_yaw_byte = CHANNELS["neck_rotation"].byte_from_angle(neck_yaw)
        neck_pitch_byte = CHANNELS["neck_elevation"].byte_from_angle(neck_pitch)
        duration_ticks = duration_ms_to_ticks(interval_ms)
        target_by_channel = {
            "eyelid_left": eyelid_left_byte,
            "eyelid_right": eyelid_right_byte,
            "eye_leftright": eye_yaw_byte,
            "eye_updown": eye_pitch_byte,
            "neck_elevation": neck_pitch_byte,
            "neck_rotation": neck_yaw_byte,
        }
        targets = tuple(target_by_channel[channel] for channel in render_channels)
        append_keyframe(keyframes, targets, duration_ticks)
        samples.append(
            GazeCornerSample(
                time_ms=t_ms + interval_ms,
                target_yaw=target_yaw,
                target_pitch=target_pitch,
                eye_yaw=eye_yaw,
                eye_pitch=eye_pitch,
                neck_yaw=neck_yaw,
                neck_pitch=neck_pitch,
                eye_yaw_byte=eye_yaw_byte,
                eye_pitch_byte=eye_pitch_byte,
                eyelid_left_byte=eyelid_left_byte,
                eyelid_right_byte=eyelid_right_byte,
                neck_yaw_byte=neck_yaw_byte,
                neck_pitch_byte=neck_pitch_byte,
            )
        )
        t_ms += interval_ms

    max_frames = (255 - 3) // (len(render_channels) + 1)
    if len(keyframes) > max_frames:
        raise ValueError(
            f"rendered {name} uses {len(keyframes)} frames for {len(render_channels)} channels; "
            f"max is {max_frames}. "
            "Increase sample_ms or shorten the curve."
        )

    return (
        RenderedAnimation(
            name,
            channels_mask(render_channels),
            render_channels,
            tuple(keyframes),
        ),
        tuple(samples),
    )


def gaze_start_pose_command(eyelid_offset: float) -> Command:
    targets = look_targets(0.0, 0.0, eyelid_offset)
    base = pose_command(tuple(targets[name] for name in CHANNEL_ORDER))
    return Command(
        name=f"gaze_start_pose_eyelid_offset={eyelid_offset:g}",
        payload=base.payload,
        delay_after=base.delay_after,
        show_rx=base.show_rx,
    )


def render_gaze_yaw_curve(
    curve: YawTargetCurve,
    *,
    config: GazeYawConfig | None = None,
    name: str = "gaze_yaw",
) -> tuple[RenderedAnimation, tuple[GazeYawSample, ...]]:
    config = config or GazeYawConfig()
    if config.sample_ms <= 0:
        raise ValueError("sample_ms must be > 0")
    if config.eye_tau_ms <= 0 or config.neck_tau_ms <= 0:
        raise ValueError("gaze controller tau values must be > 0")
    if config.eye_max_speed_dps <= 0 or config.neck_max_speed_dps <= 0:
        raise ValueError("gaze controller max speeds must be > 0")
    if config.eye_limit_deg <= 0:
        raise ValueError("eye_limit_deg must be > 0")

    target_yaw = curve.sample(curve.start_ms)
    neck_yaw = clamp(target_yaw, CHANNELS["neck_rotation"].min_angle, CHANNELS["neck_rotation"].max_angle)
    eye_yaw = 0.0
    samples: list[GazeYawSample] = []
    keyframes: list[tuple[int, ...]] = []

    t_ms = curve.start_ms
    while t_ms < curve.end_ms:
        interval_ms = min(config.sample_ms, curve.end_ms - t_ms)
        target_yaw = curve.sample(t_ms)
        neck_target_yaw = clamp(
            target_yaw,
            CHANNELS["neck_rotation"].min_angle,
            CHANNELS["neck_rotation"].max_angle,
        )
        neck_yaw = step_first_order(
            current=neck_yaw,
            target=neck_target_yaw,
            tau_ms=config.neck_tau_ms,
            dt_ms=interval_ms,
            max_speed_dps=config.neck_max_speed_dps,
        )

        eye_min_yaw = max(CHANNELS["eye_leftright"].min_angle, -config.eye_limit_deg)
        eye_max_yaw = min(CHANNELS["eye_leftright"].max_angle, config.eye_limit_deg)
        desired_eye_yaw = clamp(target_yaw - neck_yaw, eye_min_yaw, eye_max_yaw)
        eye_yaw = step_first_order(
            current=eye_yaw,
            target=desired_eye_yaw,
            tau_ms=config.eye_tau_ms,
            dt_ms=interval_ms,
            max_speed_dps=config.eye_max_speed_dps,
        )

        eye_byte = CHANNELS["eye_leftright"].byte_from_angle(eye_yaw)
        neck_byte = CHANNELS["neck_rotation"].byte_from_angle(neck_yaw)
        duration_ticks = duration_ms_to_ticks(interval_ms)
        append_keyframe(keyframes, (eye_byte, neck_byte), duration_ticks)
        samples.append(
            GazeYawSample(
                time_ms=t_ms + interval_ms,
                target_yaw=target_yaw,
                eye_yaw=eye_yaw,
                neck_yaw=neck_yaw,
                eye_byte=eye_byte,
                neck_byte=neck_byte,
            )
        )
        t_ms += interval_ms

    max_frames = (255 - 3) // (len(YAW_GAZE_CHANNELS) + 1)
    if len(keyframes) > max_frames:
        raise ValueError(
            f"rendered yaw gaze uses {len(keyframes)} frames; max is {max_frames}. "
            "Increase sample_ms or shorten the curve."
        )

    return (
        RenderedAnimation(name, channels_mask(YAW_GAZE_CHANNELS), YAW_GAZE_CHANNELS, tuple(keyframes)),
        tuple(samples),
    )


def neck_speed_test_commands(from_angle: float, to_angle: float, settle_s: float, listen_s: float) -> tuple[Command, ...]:
    if settle_s <= 0 or listen_s <= 0:
        raise ValueError("neck speed settle/listen durations must be > 0")
    from_value = CHANNELS["neck_rotation"].byte_from_angle(from_angle)
    to_value = CHANNELS["neck_rotation"].byte_from_angle(to_angle)
    from_command = position_command(NECK_ROTATION_MASK, from_value)
    to_command = position_command(NECK_ROTATION_MASK, to_value)
    return (
        *STARTUP_COMMANDS,
        Command(
            name=f"neck_speed_from_{from_angle:g}deg_value={from_value}",
            payload=from_command.payload,
            delay_after=settle_s,
            show_rx=True,
        ),
        Command(
            name=f"neck_speed_to_{to_angle:g}deg_value={to_value}",
            payload=to_command.payload,
            delay_after=listen_s,
            show_rx=True,
        ),
    )


def servo_speed_test_commands(
    channels: Sequence[str],
    from_angle: float,
    to_angle: float,
    settle_s: float,
    listen_s: float,
) -> tuple[Command, ...]:
    validate_channels(channels)
    validate_angles_for_channels(channels, from_angle, "servo speed from angle")
    validate_angles_for_channels(channels, to_angle, "servo speed to angle")
    if from_angle == to_angle:
        raise ValueError("servo speed from/to angles must differ")
    if settle_s <= 0 or listen_s <= 0:
        raise ValueError("servo speed settle/listen durations must be > 0")

    commands = list(STARTUP_COMMANDS)
    for channel in channels:
        from_value = CHANNELS[channel].byte_from_angle(from_angle)
        to_value = CHANNELS[channel].byte_from_angle(to_angle)
        from_command = position_command(CHANNELS[channel].mask, from_value)
        to_command = position_command(CHANNELS[channel].mask, to_value)
        commands.extend(
            (
                Command(
                    name=f"servo_speed_neutral:{channel}",
                    payload=STARTUP_COMMANDS[1].payload,
                    delay_after=0.35,
                    show_rx=True,
                ),
                Command(
                    name=f"servo_speed_from:{channel}:angle={from_angle:g}deg",
                    payload=from_command.payload,
                    delay_after=settle_s,
                    show_rx=True,
                ),
                Command(
                    name=f"servo_speed_to:{channel}:angle={to_angle:g}deg",
                    payload=to_command.payload,
                    delay_after=listen_s,
                    show_rx=True,
                ),
            )
        )
    return tuple(commands)


def extract_feedback_samples(data: bytes, time_s: float) -> tuple[NeckSpeedSample, ...]:
    samples: list[NeckSpeedSample] = []
    offset = 0
    while offset + 5 <= len(data):
        sync = data.find(b"\xFA\x00", offset)
        if sync < 0:
            break
        if sync + 5 > len(data):
            break
        payload_len = data[sync + 2]
        packet_len = payload_len + 5
        end = sync + packet_len
        if end > len(data):
            break
        raw = data[sync:end]
        if tuple(raw[-2:]) == fletcher16(raw[:-2]):
            payload = raw[3:-2]
            if len(payload) == 9 and payload[0] == 0x01 and payload[1] == 0x00:
                value = payload[2 + CHANNEL_ORDER.index("neck_rotation")]
                samples.append(NeckSpeedSample(time_s, value, CHANNELS["neck_rotation"].angle_from_byte(value)))
            offset = end
        else:
            offset = sync + 1
    return tuple(samples)


class NeckFeedbackDecoder:
    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes, time_s: float) -> tuple[NeckSpeedSample, ...]:
        self._buffer.extend(data)
        samples: list[NeckSpeedSample] = []

        while len(self._buffer) >= 5:
            sync = self._buffer.find(b"\xFA\x00")
            if sync < 0:
                self._buffer.clear()
                break
            if sync > 0:
                del self._buffer[:sync]
            if len(self._buffer) < 5:
                break

            payload_len = self._buffer[2]
            packet_len = payload_len + 5
            if len(self._buffer) < packet_len:
                break

            raw = bytes(self._buffer[:packet_len])
            if tuple(raw[-2:]) != fletcher16(raw[:-2]):
                del self._buffer[0]
                continue

            payload = raw[3:-2]
            if len(payload) == 9 and payload[0] == 0x01 and payload[1] == 0x00:
                value = payload[2 + CHANNEL_ORDER.index("neck_rotation")]
                samples.append(NeckSpeedSample(time_s, value, CHANNELS["neck_rotation"].angle_from_byte(value)))
            del self._buffer[:packet_len]

        return tuple(samples)


class FeedbackDecoder:
    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes, time_s: float) -> tuple[FeedbackFrame, ...]:
        self._buffer.extend(data)
        frames: list[FeedbackFrame] = []

        while len(self._buffer) >= 5:
            sync = self._buffer.find(b"\xFA\x00")
            if sync < 0:
                self._buffer.clear()
                break
            if sync > 0:
                del self._buffer[:sync]
            if len(self._buffer) < 5:
                break

            payload_len = self._buffer[2]
            packet_len = payload_len + 5
            if len(self._buffer) < packet_len:
                break

            raw = bytes(self._buffer[:packet_len])
            if tuple(raw[-2:]) != fletcher16(raw[:-2]):
                del self._buffer[0]
                continue

            payload = raw[3:-2]
            if len(payload) == 9 and payload[0] == 0x01 and payload[1] == 0x00:
                frames.append(FeedbackFrame(time_s, tuple(payload[2:9])))
            del self._buffer[:packet_len]

        return tuple(frames)


def summarize_neck_speed(samples: Sequence[NeckSpeedSample], tx_time_s: float, target_angle: float) -> str:
    motion_samples = [sample for sample in samples if sample.time_s >= tx_time_s]
    if len(motion_samples) < 2:
        return "neck speed: not enough feedback samples after target command"

    initial_angle = motion_samples[0].angle
    total_delta = target_angle - initial_angle
    if abs(total_delta) < 1.0:
        return f"neck speed: target is too close to initial angle ({initial_angle:.1f} deg)"

    direction = 1.0 if total_delta > 0 else -1.0
    threshold_10 = initial_angle + total_delta * 0.10
    threshold_90 = initial_angle + total_delta * 0.90

    def first_crossing(threshold: float) -> NeckSpeedSample | None:
        for sample in motion_samples:
            if direction * (sample.angle - threshold) >= 0:
                return sample
        return None

    sample_10 = first_crossing(threshold_10)
    sample_90 = first_crossing(threshold_90)
    near_sample = next((sample for sample in motion_samples if abs(sample.angle - target_angle) <= 2.0), None)

    peak_speed = 0.0
    for previous, current in zip(motion_samples, motion_samples[1:]):
        dt = current.time_s - previous.time_s
        if dt > 0:
            peak_speed = max(peak_speed, abs((current.angle - previous.angle) / dt))

    lines = [
        f"neck speed target: {initial_angle:.1f} deg -> {target_angle:.1f} deg",
        f"peak observed speed: {peak_speed:.1f} deg/s",
    ]
    if sample_10 is not None and sample_90 is not None and sample_90.time_s > sample_10.time_s:
        dt_10_90 = sample_90.time_s - sample_10.time_s
        avg_speed_10_90 = abs((sample_90.angle - sample_10.angle) / dt_10_90)
        lines.append(f"10-90% time: {dt_10_90:.3f} s ({avg_speed_10_90:.1f} deg/s avg)")
    else:
        lines.append("10-90% time: not reached during listen window")

    if near_sample is not None:
        lines.append(f"time to within 2 deg: {near_sample.time_s - tx_time_s:.3f} s")
    else:
        lines.append("time to within 2 deg: not reached during listen window")

    return "\n".join(lines)


def summarize_channel_speed(
    channel: str,
    samples: Sequence[ChannelSpeedSample],
    target_angle: float,
) -> ChannelSpeedSummary:
    validate_channel_name(channel)
    validate_channel_angle(channel, target_angle, "servo speed target angle")
    target_value = CHANNELS[channel].byte_from_angle(target_angle)
    target_angle = CHANNELS[channel].angle_from_byte(target_value)

    if len(samples) < 2:
        return ChannelSpeedSummary(
            channel=channel,
            sample_count=len(samples),
            initial_value=samples[0].value if samples else None,
            target_value=target_value,
            initial_angle=samples[0].angle if samples else None,
            target_angle=target_angle,
            peak_byte_s=None,
            peak_deg_s=None,
            t_10_90_s=None,
            avg_byte_s_10_90=None,
            avg_deg_s_10_90=None,
            t_to_3_bytes_s=None,
            t_to_2_deg_s=None,
        )

    initial_value = samples[0].value
    initial_angle = samples[0].angle
    total_delta_angle = target_angle - initial_angle
    direction = 1.0 if total_delta_angle >= 0 else -1.0

    def first_angle_crossing(threshold: float) -> ChannelSpeedSample | None:
        for sample in samples:
            if direction * (sample.angle - threshold) >= 0:
                return sample
        return None

    if abs(total_delta_angle) >= 0.1:
        threshold_10 = initial_angle + total_delta_angle * 0.10
        threshold_90 = initial_angle + total_delta_angle * 0.90
        sample_10 = first_angle_crossing(threshold_10)
        sample_90 = first_angle_crossing(threshold_90)
    else:
        sample_10 = None
        sample_90 = None

    peak_byte_s = 0.0
    peak_deg_s = 0.0
    for previous, current in zip(samples, samples[1:]):
        dt = current.time_s - previous.time_s
        if dt > 0:
            peak_byte_s = max(peak_byte_s, abs((current.value - previous.value) / dt))
            peak_deg_s = max(peak_deg_s, abs((current.angle - previous.angle) / dt))

    t_10_90_s = None
    avg_byte_s_10_90 = None
    avg_deg_s_10_90 = None
    if sample_10 is not None and sample_90 is not None and sample_90.time_s > sample_10.time_s:
        t_10_90_s = sample_90.time_s - sample_10.time_s
        avg_byte_s_10_90 = abs((sample_90.value - sample_10.value) / t_10_90_s)
        avg_deg_s_10_90 = abs((sample_90.angle - sample_10.angle) / t_10_90_s)

    start_time = samples[0].time_s
    near_byte_sample = next((sample for sample in samples if abs(sample.value - target_value) <= 3), None)
    near_angle_sample = next((sample for sample in samples if abs(sample.angle - target_angle) <= 2.0), None)

    return ChannelSpeedSummary(
        channel=channel,
        sample_count=len(samples),
        initial_value=initial_value,
        target_value=target_value,
        initial_angle=initial_angle,
        target_angle=target_angle,
        peak_byte_s=peak_byte_s,
        peak_deg_s=peak_deg_s,
        t_10_90_s=t_10_90_s,
        avg_byte_s_10_90=avg_byte_s_10_90,
        avg_deg_s_10_90=avg_deg_s_10_90,
        t_to_3_bytes_s=(near_byte_sample.time_s - start_time) if near_byte_sample is not None else None,
        t_to_2_deg_s=(near_angle_sample.time_s - start_time) if near_angle_sample is not None else None,
    )


def format_servo_speed_summaries(summaries: Sequence[ChannelSpeedSummary]) -> str:
    def fmt_float(value: float | None, digits: int = 2) -> str:
        return "-" if value is None else f"{value:.{digits}f}"

    def fmt_int(value: int | None) -> str:
        return "-" if value is None else str(value)

    lines = [
        "servo speed summary:",
        (
            f"{'channel':<16} {'byte':>9} {'angle deg':>17} {'peak B/s':>9} "
            f"{'peak deg/s':>10} {'10-90 s':>8} {'avg deg/s':>9} {'to +/-3B':>9} "
            f"{'to +/-2deg':>11} {'samples':>7}"
        ),
    ]
    for summary in summaries:
        initial_angle = "-" if summary.initial_angle is None else f"{summary.initial_angle:.1f}"
        lines.append(
            f"{summary.channel:<16} "
            f"{fmt_int(summary.initial_value):>3}->{summary.target_value:<3} "
            f"{initial_angle:>7}->{summary.target_angle:<7.1f} "
            f"{fmt_float(summary.peak_byte_s, 1):>9} "
            f"{fmt_float(summary.peak_deg_s, 1):>10} "
            f"{fmt_float(summary.t_10_90_s):>8} "
            f"{fmt_float(summary.avg_deg_s_10_90, 1):>9} "
            f"{fmt_float(summary.t_to_3_bytes_s):>9} "
            f"{fmt_float(summary.t_to_2_deg_s):>11} "
            f"{summary.sample_count:>7}"
        )
    return "\n".join(lines)


def channels_from_frames(frames: Sequence[AnimationFrame]) -> tuple[str, ...]:
    used = set()
    for frame in frames:
        used.update(frame.targets)
    return tuple(name for name in CHANNEL_ORDER if name in used)


def validate_channel_name(name: str) -> None:
    if name not in CHANNEL_ORDER:
        raise ValueError(f"unknown channel {name!r}")


def validate_channels(channels: Sequence[str]) -> None:
    if not channels:
        raise ValueError("render channel list must not be empty")
    seen = set()
    for name in channels:
        validate_channel_name(name)
        if name in seen:
            raise ValueError(f"duplicate render channel {name!r}")
        seen.add(name)
    mask = channels_mask(channels)
    if mask & ~KNOWN_CHANNEL_MASK:
        raise ValueError(f"channel mask contains unknown bits: 0x{mask & ~KNOWN_CHANNEL_MASK:02X}")


def validate_channel_angle(channel: str, angle: float, name: str = "angle") -> None:
    validate_channel_name(channel)
    if not math.isfinite(angle):
        raise ValueError(f"{name} must be finite")
    min_angle = CHANNELS[channel].min_angle
    max_angle = CHANNELS[channel].max_angle
    if not min_angle <= angle <= max_angle:
        raise ValueError(
            f"{name} {angle:g} deg is outside {channel} range "
            f"{min_angle:.1f}..{max_angle:.1f} deg"
        )


def validate_angles_for_channels(channels: Sequence[str], angle: float, name: str) -> None:
    for channel in channels:
        validate_channel_angle(channel, angle, f"{name} for {channel}")


def channel_max_speed_dps(channel: str) -> float:
    validate_channel_name(channel)
    return MEASURED_CHANNEL_MAX_SPEED_DPS[channel]


def min_duration_ms_for_angle_step(channel: str, from_angle: float, to_angle: float) -> float:
    validate_channel_angle(channel, from_angle, "from angle")
    validate_channel_angle(channel, to_angle, "to angle")
    return abs(to_angle - from_angle) / channel_max_speed_dps(channel) * 1000.0


def validate_angle_step_duration(channel: str, from_angle: float, to_angle: float, duration_ms: float) -> None:
    if duration_ms <= 0:
        raise ValueError("duration_ms must be > 0")
    min_duration_ms = min_duration_ms_for_angle_step(channel, from_angle, to_angle)
    if duration_ms < min_duration_ms:
        raise ValueError(
            f"{channel} step {from_angle:g}->{to_angle:g} deg over {duration_ms:g} ms is too fast; "
            f"needs at least {min_duration_ms:.0f} ms at measured {channel_max_speed_dps(channel):.0f} deg/s"
        )


def channels_mask(channels: Sequence[str]) -> int:
    mask = 0
    for name in channels:
        bit_index = CHANNEL_ORDER.index(name)
        mask |= 0x40 >> bit_index
    return mask


def duration_ms_to_tick_chunks(duration_ms: float) -> tuple[int, ...]:
    total_ticks = duration_ms_to_ticks(duration_ms)
    chunks: list[int] = []
    while total_ticks > 255:
        chunks.append(255)
        total_ticks -= 255
    chunks.append(total_ticks)
    return tuple(chunks)


def duration_ms_to_ticks(duration_ms: float) -> int:
    return max(1, math.ceil(duration_ms / (SCRIPT_TICK_SECONDS * 1000.0)))


def append_keyframe(keyframes: list[tuple[int, ...]], targets: tuple[int, ...], duration_ticks: int) -> None:
    if duration_ticks <= 0:
        raise ValueError("duration_ticks must be > 0")
    if keyframes and keyframes[-1][:-1] == targets and keyframes[-1][-1] + duration_ticks <= 255:
        keyframes[-1] = (*targets, keyframes[-1][-1] + duration_ticks)
    else:
        keyframes.append((*targets, duration_ticks))


def normalized_time(t_ms: float, start_ms: float, end_ms: float) -> float:
    if end_ms <= start_ms:
        return 1.0
    return clamp((t_ms - start_ms) / (end_ms - start_ms), 0.0, 1.0)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def step_first_order(
    *,
    current: float,
    target: float,
    tau_ms: float,
    dt_ms: float,
    max_speed_dps: float,
) -> float:
    alpha = 1.0 - math.exp(-dt_ms / tau_ms)
    requested_delta = (target - current) * alpha
    max_delta = max_speed_dps * (dt_ms / 1000.0)
    delta = clamp(requested_delta, -max_delta, max_delta)
    return current + delta


def parse_gaze_points(value: str) -> tuple[GazePoint, ...]:
    points: list[GazePoint] = []
    for raw_point in value.split(";"):
        raw_point = raw_point.strip()
        if not raw_point:
            continue
        parts = [part.strip() for part in raw_point.split(",")]
        if len(parts) == 2:
            yaw_text, duration_text = parts
            pitch_text = "0"
        elif len(parts) == 3:
            yaw_text, pitch_text, duration_text = parts
        else:
            raise argparse.ArgumentTypeError(
                "gaze points must be YAW,DURATION_MS or YAW,PITCH,DURATION_MS separated by semicolon"
            )
        try:
            points.append(GazePoint(float(yaw_text), float(pitch_text), float(duration_text)))
        except ValueError as exc:
            raise argparse.ArgumentTypeError("gaze points must contain numeric values") from exc

    if not points:
        raise argparse.ArgumentTypeError("gaze sequence must contain at least one point")
    return tuple(points)


def parse_gaze_to(value: str) -> tuple[float, float]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) == 1:
        try:
            return (float(parts[0]), 0.0)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("--gaze-to YAW must be numeric") from exc
    if len(parts) == 2:
        try:
            return (float(parts[0]), float(parts[1]))
        except ValueError as exc:
            raise argparse.ArgumentTypeError("--gaze-to YAW,PITCH must be numeric") from exc
    raise argparse.ArgumentTypeError("--gaze-to expects YAW or YAW,PITCH")


def parse_servo_speed_channels(value: str) -> tuple[str, ...]:
    text = value.strip()
    if text.lower() == "all":
        return CHANNEL_ORDER
    channels = tuple(part.strip() for part in text.split(",") if part.strip())
    if not channels:
        raise argparse.ArgumentTypeError("servo speed channels must be 'all' or a comma-separated channel list")
    try:
        validate_channels(channels)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return channels


def validate_demo_gaze_yaw_parts(parts: Sequence[int]) -> None:
    if not parts:
        raise ValueError("demo gaze yaw parts must not be empty")
    previous = 0
    seen = set()
    for part in parts:
        if part not in DEMO_GAZE_YAW_ALL_PARTS:
            raise ValueError("demo gaze yaw parts must be 1, 2, and/or 3")
        if part in seen:
            raise ValueError(f"duplicate demo gaze yaw part {part}")
        if part <= previous:
            raise ValueError("demo gaze yaw parts must be in ascending order")
        seen.add(part)
        previous = part


def parse_demo_gaze_yaw_parts(value: str) -> tuple[int, ...]:
    raw_parts = [part.strip() for part in value.split(",") if part.strip()]
    if not raw_parts:
        raise argparse.ArgumentTypeError("demo gaze yaw parts must be 1, 2, and/or 3")
    try:
        parts = tuple(int(part) for part in raw_parts)
        validate_demo_gaze_yaw_parts(parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return parts


NEGATIVE_VALUE_OPTIONS = {
    "--gaze",
    "--gaze-to",
    "--eyelid-offset",
    "--neck-speed-from",
    "--neck-speed-to",
    "--servo-speed-from",
    "--servo-speed-to",
    "--gaze-corners-yaw",
    "--gaze-corners-pitch",
    "--blink-closed-angle",
}


def normalize_negative_option_values(argv: list[str]) -> list[str]:
    normalized: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item in NEGATIVE_VALUE_OPTIONS and index + 1 < len(argv):
            value = argv[index + 1]
            if value.startswith("-") and len(value) > 1 and not value.startswith("--"):
                normalized.append(f"{item}={value}")
                index += 2
                continue
        normalized.append(item)
        index += 1
    return normalized


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render higher-level robot animations to robot_motion.py commands.")
    parser.add_argument(
        "--gaze",
        type=parse_gaze_points,
        metavar="YAW[,PITCH],MS;...",
        help="Build an animation from gaze points. Example: --gaze '-30,0,800;20,0,800;0,0,500'.",
    )
    parser.add_argument(
        "--demo-gaze-yaw",
        nargs="?",
        const="1,2,3",
        default=None,
        type=parse_demo_gaze_yaw_parts,
        metavar="PARTS",
        help=(
            "Render yaw-only demo parts. PARTS is 1, 2, 3, or comma-separated, "
            "where 1 is 0 -> +25, 2 is +25 -> -20, and 3 is -20 -> +20. "
            "Default when omitted: 1,2,3."
        ),
    )
    parser.add_argument(
        "--demo-gaze-corners",
        action="store_true",
        help="Render a pitch/yaw gaze demo that visits upper/lower left/right corners and returns to center.",
    )
    parser.add_argument(
        "--blink",
        action="store_true",
        help="Blink eyelids using the current eye pitch as the non-blink baseline.",
    )
    parser.add_argument(
        "--gaze-to",
        type=parse_gaze_to,
        metavar="YAW[,PITCH]",
        default=None,
        help=(
            "Move to YAW[,PITCH] degrees using eye-leads-neck behavior. "
            "Captures current servo pose from feedback as the controller starting state, "
            "so consecutive --gaze-to calls compose naturally."
        ),
    )
    parser.add_argument(
        "--gaze-to-dwell-ms",
        type=float,
        default=1500.0,
        help="Total script duration for --gaze-to. Default: 1500 ms.",
    )
    parser.add_argument(
        "--gaze-to-sample-ms",
        type=float,
        default=50.0,
        help="Sampling interval for --gaze-to. Default: 50 ms.",
    )
    parser.add_argument(
        "--gaze-to-listen-ms",
        type=float,
        default=200.0,
        help="Listen window after power-on used to capture current pose. Default: 200 ms.",
    )
    parser.add_argument(
        "--gaze-corners-yaw",
        type=float,
        default=20.0,
        metavar="DEG",
        help="Yaw amplitude for --demo-gaze-corners. Default: 20.",
    )
    parser.add_argument(
        "--gaze-corners-pitch",
        type=float,
        default=10.0,
        metavar="DEG",
        help="Pitch amplitude for --demo-gaze-corners. Default: 10.",
    )
    parser.add_argument(
        "--gaze-corners-settle-ms",
        type=float,
        default=200.0,
        help="Initial dwell at center before --demo-gaze-corners begins. Default: 200 ms.",
    )
    parser.add_argument(
        "--gaze-corners-hold-ms",
        type=float,
        default=450.0,
        help="Target dwell at each corner for --demo-gaze-corners. Default: 450 ms.",
    )
    parser.add_argument(
        "--gaze-corners-return-ms",
        type=float,
        default=600.0,
        help="Target dwell at center after the last corner for --demo-gaze-corners. Default: 600 ms.",
    )
    parser.add_argument(
        "--gaze-corners-sample-ms",
        type=float,
        default=80.0,
        help="Sampling interval for --demo-gaze-corners. Default: 80 ms.",
    )
    parser.add_argument(
        "--gaze-yaw-sample-ms",
        type=float,
        default=50.0,
        help="Sampling interval for --demo-gaze-yaw. Default: 50 ms.",
    )
    parser.add_argument(
        "--gaze-yaw-eye-max-speed",
        type=float,
        default=GAZE_YAW_DEFAULT_EYE_MAX_SPEED_DPS,
        metavar="DEG_PER_S",
        help=f"Eye yaw speed limit for --demo-gaze-yaw. Default: {GAZE_YAW_DEFAULT_EYE_MAX_SPEED_DPS:g} deg/s.",
    )
    parser.add_argument(
        "--gaze-yaw-neck-max-speed",
        type=float,
        default=GAZE_YAW_DEFAULT_NECK_MAX_SPEED_DPS,
        metavar="DEG_PER_S",
        help=f"Neck yaw speed limit for --demo-gaze-yaw. Default: {GAZE_YAW_DEFAULT_NECK_MAX_SPEED_DPS:g} deg/s.",
    )
    parser.add_argument(
        "--eyelid-offset",
        type=float,
        default=-2.0,
        help="Eyelid offset in degrees applied to gaze start/gaze points. Default: -2.",
    )
    parser.add_argument(
        "--blink-at-ms",
        type=parse_blink_times,
        default=(),
        metavar="MS[,MS...]",
        help="Bake blink events into --gaze-to or --demo-gaze-corners at these times.",
    )
    parser.add_argument(
        "--blink-close-ms",
        type=float,
        default=BLINK_DEFAULT_CLOSE_MS,
        help=f"Blink close duration. Default: {BLINK_DEFAULT_CLOSE_MS:g} ms.",
    )
    parser.add_argument(
        "--blink-hold-ms",
        type=float,
        default=BLINK_DEFAULT_HOLD_MS,
        help=f"Blink closed hold duration. Default: {BLINK_DEFAULT_HOLD_MS:g} ms.",
    )
    parser.add_argument(
        "--blink-open-ms",
        type=float,
        default=BLINK_DEFAULT_OPEN_MS,
        help=f"Blink open duration. Default: {BLINK_DEFAULT_OPEN_MS:g} ms.",
    )
    parser.add_argument(
        "--blink-closed-angle",
        type=float,
        default=BLINK_DEFAULT_CLOSED_ANGLE,
        metavar="DEG",
        help=f"Closed eyelid angle in degrees. Default: {BLINK_DEFAULT_CLOSED_ANGLE:g}.",
    )
    parser.add_argument(
        "--test-neck-speed",
        action="store_true",
        help="Measure neck rotation step response using feedback from the motorboard.",
    )
    parser.add_argument(
        "--neck-speed-from",
        type=float,
        default=-30.0,
        help="Start angle in degrees for --test-neck-speed. Default: -30.",
    )
    parser.add_argument(
        "--neck-speed-to",
        type=float,
        default=30.0,
        help="Target angle in degrees for --test-neck-speed. Default: 30.",
    )
    parser.add_argument(
        "--neck-speed-settle",
        type=float,
        default=1.5,
        help="Seconds to wait at the start angle before measuring. Default: 1.5.",
    )
    parser.add_argument(
        "--neck-speed-listen",
        type=float,
        default=3.0,
        help="Seconds to listen after the target step. Default: 3.0.",
    )
    parser.add_argument(
        "--test-servo-speeds",
        action="store_true",
        help="Measure one angle step on all servos, one channel at a time, using feedback.",
    )
    parser.add_argument(
        "--servo-speed-channels",
        type=parse_servo_speed_channels,
        default=CHANNEL_ORDER,
        metavar="all|CH1,CH2",
        help="Channels to test with --test-servo-speeds. Default: all.",
    )
    parser.add_argument(
        "--servo-speed-from",
        type=float,
        default=-8.0,
        metavar="DEG",
        help="Start angle in degrees for --test-servo-speeds. Default: -8.",
    )
    parser.add_argument(
        "--servo-speed-to",
        type=float,
        default=8.0,
        metavar="DEG",
        help="Target angle in degrees for --test-servo-speeds. Default: 8.",
    )
    parser.add_argument(
        "--servo-speed-settle",
        type=float,
        default=1.0,
        help="Seconds to wait at the start angle before measuring each servo. Default: 1.0.",
    )
    parser.add_argument(
        "--servo-speed-listen",
        type=float,
        default=3.0,
        help="Seconds to listen after each servo target step. Default: 3.0.",
    )
    parser.add_argument("--port", default="/dev/ttyAMA0")
    parser.add_argument("--baudrate", type=int, default=BAUDRATE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--print-keyframes", action="store_true", help="Print rendered mask and raw keyframes.")
    parser.add_argument("--print-samples", action="store_true", help="Print sampled animation/controller values as CSV.")
    parser.add_argument("--sample-log", help="Write decoded feedback sample rows to this CSV file.")
    parser.add_argument("--power-off", action="store_true", help="Send 4F 00 after the animation.")
    parser.add_argument("--listen-before", type=float, default=0.10)
    parser.add_argument("--listen-after", type=float, default=0.50)
    return parser.parse_args(normalize_negative_option_values(sys.argv[1:] if argv is None else argv))


def _print_gaze_corners_outputs(
    args: argparse.Namespace,
    rendered: RenderedAnimation,
    samples: Sequence[GazeCornerSample],
) -> None:
    if args.print_keyframes:
        print(f"mask=0x{rendered.mask:02X}")
        print(f"channels={','.join(rendered.channels)}")
        print(rendered.raw_keyframes_text())
    if args.print_samples and samples:
        include_eyelids = "eyelid_left" in rendered.channels or "eyelid_right" in rendered.channels
        header = (
            "time_ms,target_yaw,target_pitch,eye_yaw,eye_pitch,neck_yaw,neck_pitch,"
            "eye_yaw_byte,eye_pitch_byte"
        )
        if include_eyelids:
            header += ",eyelid_left_byte,eyelid_right_byte"
        header += ",neck_yaw_byte,neck_pitch_byte"
        print(header)
        for sample in samples:
            row = (
                f"{sample.time_ms:.0f},"
                f"{sample.target_yaw:.2f},"
                f"{sample.target_pitch:.2f},"
                f"{sample.eye_yaw:.2f},"
                f"{sample.eye_pitch:.2f},"
                f"{sample.neck_yaw:.2f},"
                f"{sample.neck_pitch:.2f},"
                f"{sample.eye_yaw_byte},"
                f"{sample.eye_pitch_byte}"
            )
            if include_eyelids:
                row += f",{sample.eyelid_left_byte},{sample.eyelid_right_byte}"
            row += f",{sample.neck_yaw_byte},{sample.neck_pitch_byte}"
            print(row)


def blink_events_from_args(args: argparse.Namespace) -> tuple[BlinkEvent, ...]:
    return tuple(
        BlinkEvent(
            start_ms=start_ms,
            close_ms=args.blink_close_ms,
            hold_ms=args.blink_hold_ms,
            open_ms=args.blink_open_ms,
            closed_angle=args.blink_closed_angle,
        )
        for start_ms in args.blink_at_ms
    )


def _print_blink_outputs(
    args: argparse.Namespace,
    rendered: RenderedAnimation,
    samples: Sequence[BlinkSample],
) -> None:
    if args.print_keyframes:
        print(f"mask=0x{rendered.mask:02X}")
        print(f"channels={','.join(rendered.channels)}")
        print(rendered.raw_keyframes_text())
    if args.print_samples:
        print("time_ms,eyelid_left_angle,eyelid_right_angle,eyelid_left_byte,eyelid_right_byte")
        for sample in samples:
            print(
                f"{sample.time_ms:.0f},"
                f"{sample.eyelid_left_angle:.2f},"
                f"{sample.eyelid_right_angle:.2f},"
                f"{sample.eyelid_left_byte},"
                f"{sample.eyelid_right_byte}"
            )


def run_blink(args: argparse.Namespace) -> int:
    name = "blink"

    if args.dry_run:
        base_eyelid = blink_base_eyelid_angle(0.0, args.eyelid_offset)
        try:
            rendered, samples = render_blink(
                base_eyelid,
                base_eyelid,
                closed_angle=args.blink_closed_angle,
                close_ms=args.blink_close_ms,
                hold_ms=args.blink_hold_ms,
                open_ms=args.blink_open_ms,
                name=name,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        _print_blink_outputs(args, rendered, samples)
        commands: list[Command] = [STARTUP_COMMANDS[0], rendered.command()]
        if args.power_off:
            commands.append(POWER_OFF)
        for item in commands:
            print(f"{item.name}: {format_hex(packet(item.payload))}")
        return 0

    started_at = time.monotonic()
    feedback_decoder = FeedbackDecoder()
    latest_frame: list[FeedbackFrame | None] = [None]
    listen_s = max(args.gaze_to_listen_ms / 1000.0, 0.05)

    def on_rx(data: bytes) -> None:
        if args.verbose:
            print_rx(data, started_at)
        for frame in feedback_decoder.feed(data, time.monotonic() - started_at):
            latest_frame[0] = frame

    with RobotMotion(args.port, args.baudrate) as robot:
        port = robot.serial_port
        for command in (STARTUP_COMMANDS[0], read_command("read_vr_values", 0x40)):
            tx = packet(command.payload)
            if args.verbose:
                print(
                    f"TX +{time.monotonic() - started_at:0.3f}s {command.name}: {format_hex(tx)}",
                    flush=True,
                )
            port.write(tx)
            port.flush()

        deadline = time.monotonic() + listen_s
        while time.monotonic() < deadline:
            read_available(port, 0.025, on_rx)

        if latest_frame[0] is None:
            print(
                f"could not capture pose feedback within {args.gaze_to_listen_ms:.0f} ms; "
                "is the motorboard powered and connected?",
                file=sys.stderr,
            )
            return 2

        eye_pitch = latest_frame[0].angle("eye_updown")
        base_eyelid = blink_base_eyelid_angle(eye_pitch, args.eyelid_offset)
        if args.verbose:
            print(
                f"blink baseline: eye_pitch={eye_pitch:.2f} eyelid={base_eyelid:.2f}",
                flush=True,
            )

        try:
            rendered, samples = render_blink(
                base_eyelid,
                base_eyelid,
                closed_angle=args.blink_closed_angle,
                close_ms=args.blink_close_ms,
                hold_ms=args.blink_hold_ms,
                open_ms=args.blink_open_ms,
                name=name,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        _print_blink_outputs(args, rendered, samples)

        commands = [rendered.command()]
        if args.power_off:
            commands.append(POWER_OFF)

        def handle_tx(item: Command, tx_bytes: bytes) -> None:
            if args.verbose:
                print(
                    f"TX +{time.monotonic() - started_at:0.3f}s {item.name}: {format_hex(tx_bytes)}",
                    flush=True,
                )

        robot.run_commands(
            commands,
            listen_before=0.0,
            listen_after=args.listen_after,
            tx_callback=handle_tx if args.verbose else None,
            rx_callback=on_rx if args.verbose else None,
        )

    return 0


def run_gaze_to(args: argparse.Namespace) -> int:
    target_yaw, target_pitch = args.gaze_to
    try:
        yaw_curve, pitch_curve = gaze_to_curves(target_yaw, target_pitch, args.gaze_to_dwell_ms)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    config = GazeCornersConfig(sample_ms=args.gaze_to_sample_ms)
    name = f"gaze_to_yaw={target_yaw:g}_pitch={target_pitch:g}"
    blink_events = blink_events_from_args(args)

    if args.dry_run:
        try:
            rendered, samples = render_gaze_corners_curves(
                yaw_curve,
                pitch_curve,
                config=config,
                name=name,
                initial_state=GazeControllerState(0.0, 0.0, 0.0, 0.0),
                include_eyelids=True,
                eyelid_offset=args.eyelid_offset,
                blink_events=blink_events,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        _print_gaze_corners_outputs(args, rendered, samples)
        commands: list[Command] = [STARTUP_COMMANDS[0], rendered.command()]
        if args.power_off:
            commands.append(POWER_OFF)
        for item in commands:
            print(f"{item.name}: {format_hex(packet(item.payload))}")
        return 0

    started_at = time.monotonic()
    feedback_decoder = FeedbackDecoder()
    latest_frame: list[FeedbackFrame | None] = [None]
    listen_s = max(args.gaze_to_listen_ms / 1000.0, 0.05)

    def on_rx(data: bytes) -> None:
        if args.verbose:
            print_rx(data, started_at)
        for frame in feedback_decoder.feed(data, time.monotonic() - started_at):
            latest_frame[0] = frame

    with RobotMotion(args.port, args.baudrate) as robot:
        port = robot.serial_port
        for command in (STARTUP_COMMANDS[0], read_command("read_vr_values", 0x40)):
            tx = packet(command.payload)
            if args.verbose:
                print(
                    f"TX +{time.monotonic() - started_at:0.3f}s {command.name}: {format_hex(tx)}",
                    flush=True,
                )
            port.write(tx)
            port.flush()

        deadline = time.monotonic() + listen_s
        while time.monotonic() < deadline:
            read_available(port, 0.025, on_rx)

        if latest_frame[0] is None:
            print(
                f"could not capture pose feedback within {args.gaze_to_listen_ms:.0f} ms; "
                "is the motorboard powered and connected?",
                file=sys.stderr,
            )
            return 2

        frame = latest_frame[0]
        initial_state = GazeControllerState(
            eye_yaw=frame.angle("eye_leftright"),
            eye_pitch=frame.angle("eye_updown"),
            neck_yaw=frame.angle("neck_rotation"),
            neck_pitch=frame.angle("neck_elevation"),
            eyelid_left=frame.angle("eyelid_left"),
            eyelid_right=frame.angle("eyelid_right"),
        )
        if args.verbose:
            print(
                f"current pose: eye_yaw={initial_state.eye_yaw:.2f} "
                f"eye_pitch={initial_state.eye_pitch:.2f} "
                f"neck_yaw={initial_state.neck_yaw:.2f} "
                f"neck_pitch={initial_state.neck_pitch:.2f}",
                flush=True,
            )

        try:
            rendered, samples = render_gaze_corners_curves(
                yaw_curve,
                pitch_curve,
                config=config,
                name=name,
                initial_state=initial_state,
                include_eyelids=True,
                eyelid_offset=args.eyelid_offset,
                blink_events=blink_events,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        _print_gaze_corners_outputs(args, rendered, samples)

        commands = [rendered.command()]
        if args.power_off:
            commands.append(POWER_OFF)

        def handle_tx(item: Command, tx_bytes: bytes) -> None:
            if args.verbose:
                print(
                    f"TX +{time.monotonic() - started_at:0.3f}s {item.name}: {format_hex(tx_bytes)}",
                    flush=True,
                )

        robot.run_commands(
            commands,
            listen_before=0.0,
            listen_after=args.listen_after,
            tx_callback=handle_tx if args.verbose else None,
            rx_callback=on_rx if args.verbose else None,
        )

    return 0


def main() -> int:
    args = parse_args()
    modes = [
        args.gaze is not None,
        args.gaze_to is not None,
        args.blink,
        args.demo_gaze_yaw is not None,
        args.demo_gaze_corners,
        args.test_neck_speed,
        args.test_servo_speeds,
    ]
    if sum(modes) != 1:
        print(
            "choose one mode, for example --gaze-to, --demo-gaze-yaw, "
            "--demo-gaze-corners, --test-neck-speed, --test-servo-speeds, "
            "--blink, or --gaze '-30,0,800;20,0,800'",
            file=sys.stderr,
        )
        return 2
    if args.blink_at_ms and not (args.gaze_to is not None or args.demo_gaze_corners):
        print("--blink-at-ms can only be used with --gaze-to or --demo-gaze-corners", file=sys.stderr)
        return 2

    if args.gaze_to is not None:
        return run_gaze_to(args)
    if args.blink:
        return run_blink(args)

    try:
        if args.test_neck_speed:
            commands = list(
                neck_speed_test_commands(
                    args.neck_speed_from,
                    args.neck_speed_to,
                    args.neck_speed_settle,
                    args.neck_speed_listen,
                )
            )
            rendered = None
            samples = ()
        elif args.test_servo_speeds:
            commands = list(
                servo_speed_test_commands(
                    args.servo_speed_channels,
                    args.servo_speed_from,
                    args.servo_speed_to,
                    args.servo_speed_settle,
                    args.servo_speed_listen,
                )
            )
            rendered = None
            samples = ()
        elif args.demo_gaze_corners:
            yaw_curve, pitch_curve = demo_gaze_corners_curves(
                yaw_deg=args.gaze_corners_yaw,
                pitch_deg=args.gaze_corners_pitch,
                settle_ms=args.gaze_corners_settle_ms,
                hold_ms=args.gaze_corners_hold_ms,
                return_ms=args.gaze_corners_return_ms,
            )
            rendered, samples = render_gaze_corners_curves(
                yaw_curve,
                pitch_curve,
                config=GazeCornersConfig(sample_ms=args.gaze_corners_sample_ms),
                name="demo_gaze_corners",
                include_eyelids=True,
                eyelid_offset=args.eyelid_offset,
                blink_events=blink_events_from_args(args),
            )
            command = rendered.command()
            commands = [*STARTUP_COMMANDS, gaze_start_pose_command(args.eyelid_offset), command]
        elif args.demo_gaze_yaw:
            demo_parts = tuple(args.demo_gaze_yaw)
            rendered, samples = render_gaze_yaw_curve(
                demo_gaze_yaw_curve(demo_parts),
                config=GazeYawConfig(
                    sample_ms=args.gaze_yaw_sample_ms,
                    eye_max_speed_dps=args.gaze_yaw_eye_max_speed,
                    neck_max_speed_dps=args.gaze_yaw_neck_max_speed,
                ),
                name="demo_gaze_yaw_parts_" + "_".join(str(part) for part in demo_parts),
            )
            command = rendered.command()
            commands = [*STARTUP_COMMANDS, gaze_start_pose_command(args.eyelid_offset), command]
        else:
            points = tuple(
                GazePoint(point.yaw, point.pitch, point.duration_ms, args.eyelid_offset)
                for point in args.gaze
            )
            animation = gaze_animation(points)
            rendered = animation.render(LOOK_CHANNELS)
            samples = ()
            command = rendered.command()
            commands = [*STARTUP_COMMANDS, command]
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.power_off:
        commands.append(POWER_OFF)

    if args.print_keyframes and rendered is not None:
        print(f"mask=0x{rendered.mask:02X}")
        print(f"channels={','.join(rendered.channels)}")
        print(rendered.raw_keyframes_text())

    if args.print_samples and samples:
        if isinstance(samples[0], GazeYawSample):
            print("time_ms,target_yaw,eye_yaw,neck_yaw,eye_byte,neck_byte")
            for sample in samples:
                print(
                    f"{sample.time_ms:.0f},"
                    f"{sample.target_yaw:.2f},"
                    f"{sample.eye_yaw:.2f},"
                    f"{sample.neck_yaw:.2f},"
                    f"{sample.eye_byte},"
                    f"{sample.neck_byte}"
                )
        elif isinstance(samples[0], GazeCornerSample):
            include_eyelids = (
                rendered is not None
                and ("eyelid_left" in rendered.channels or "eyelid_right" in rendered.channels)
            )
            header = (
                "time_ms,target_yaw,target_pitch,eye_yaw,eye_pitch,neck_yaw,neck_pitch,"
                "eye_yaw_byte,eye_pitch_byte"
            )
            if include_eyelids:
                header += ",eyelid_left_byte,eyelid_right_byte"
            header += ",neck_yaw_byte,neck_pitch_byte"
            print(header)
            for sample in samples:
                row = (
                    f"{sample.time_ms:.0f},"
                    f"{sample.target_yaw:.2f},"
                    f"{sample.target_pitch:.2f},"
                    f"{sample.eye_yaw:.2f},"
                    f"{sample.eye_pitch:.2f},"
                    f"{sample.neck_yaw:.2f},"
                    f"{sample.neck_pitch:.2f},"
                    f"{sample.eye_yaw_byte},"
                    f"{sample.eye_pitch_byte}"
                )
                if include_eyelids:
                    row += f",{sample.eyelid_left_byte},{sample.eyelid_right_byte}"
                row += f",{sample.neck_yaw_byte},{sample.neck_pitch_byte}"
                print(row)

    if args.dry_run:
        for item in commands:
            print(f"{item.name}: {format_hex(packet(item.payload))}")
        return 0

    started_at = time.monotonic()
    neck_decoder = NeckFeedbackDecoder()
    feedback_decoder = FeedbackDecoder()
    neck_samples: list[NeckSpeedSample] = []
    neck_state = {"to_tx_time": None}
    servo_channels = tuple(args.servo_speed_channels) if args.test_servo_speeds else ()
    servo_samples: dict[str, list[ChannelSpeedSample]] = {channel: [] for channel in servo_channels}
    servo_state = {"active_channel": None}
    show_tx = args.verbose or args.test_neck_speed or args.test_servo_speeds

    def handle_tx(item: Command, tx: bytes) -> None:
        if item.name.startswith("neck_speed_to_"):
            neck_state["to_tx_time"] = time.monotonic() - started_at
        if args.test_servo_speeds:
            if item.name.startswith("servo_speed_to:"):
                parts = item.name.split(":", 2)
                servo_state["active_channel"] = parts[1] if len(parts) >= 2 else None
            elif item.name.startswith("servo_speed_"):
                servo_state["active_channel"] = None
        if show_tx:
            print(
                f"TX +{time.monotonic() - started_at:0.3f}s {item.name}: {format_hex(tx)}",
                flush=True,
            )

    def handle_rx(data: bytes) -> None:
        now_s = time.monotonic() - started_at
        if args.verbose:
            print_rx(data, started_at)
        if args.test_neck_speed:
            decoded = neck_decoder.feed(data, now_s)
            neck_samples.extend(decoded)
            for sample in decoded:
                row = f"{sample.time_s:.3f},{sample.value},{sample.angle:.2f}"
                if args.print_samples:
                    print(row, flush=True)
                if sample_log is not None:
                    print(row, file=sample_log, flush=True)
        if args.test_servo_speeds:
            frames = feedback_decoder.feed(data, now_s)
            active_channel = servo_state["active_channel"]
            if active_channel is not None:
                for frame in frames:
                    sample = ChannelSpeedSample(
                        time_s=frame.time_s,
                        value=frame.value(active_channel),
                        angle=frame.angle(active_channel),
                    )
                    servo_samples[active_channel].append(sample)
                    row = f"{sample.time_s:.3f},{active_channel},{sample.value},{sample.angle:.2f}"
                    if args.print_samples:
                        print(row, flush=True)
                    if sample_log is not None:
                        print(row, file=sample_log, flush=True)

    tx_callback = handle_tx if show_tx else None
    rx_callback = handle_rx if args.verbose or args.test_neck_speed or args.test_servo_speeds else None

    sample_log = None
    sample_header = None
    if args.test_neck_speed:
        sample_header = "time_s,neck_byte,neck_angle"
    if args.test_servo_speeds:
        sample_header = "time_s,channel,byte,angle"

    if args.test_neck_speed and args.print_samples:
        print(sample_header)
    if args.test_servo_speeds and args.print_samples:
        print(sample_header)

    if args.sample_log is not None:
        try:
            sample_log = open(args.sample_log, "w", encoding="utf-8")
        except OSError as exc:
            print(f"could not open sample log {args.sample_log!r}: {exc}", file=sys.stderr)
            return 2
        if sample_header is not None:
            print(sample_header, file=sample_log, flush=True)

    try:
        with RobotMotion(args.port, args.baudrate) as robot:
            robot.run_commands(
                commands,
                listen_before=args.listen_before,
                listen_after=args.listen_after,
                tx_callback=tx_callback,
                rx_callback=rx_callback,
            )
    finally:
        if sample_log is not None:
            sample_log.close()

    if args.test_neck_speed:
        tx_time = neck_state["to_tx_time"]
        if tx_time is None:
            print("neck speed: target command timestamp was not recorded")
        else:
            print(summarize_neck_speed(neck_samples, tx_time, args.neck_speed_to))
    if args.test_servo_speeds:
        summaries = [
            summarize_channel_speed(channel, servo_samples[channel], args.servo_speed_to)
            for channel in servo_channels
        ]
        print(format_servo_speed_summaries(summaries))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
