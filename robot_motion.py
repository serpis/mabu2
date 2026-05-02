#!/usr/bin/env python3

from __future__ import annotations

import argparse
import struct
import sys
import time
from dataclasses import dataclass
from typing import Sequence

try:
    import serial
except ImportError:  # pragma: no cover - helpful on the Raspberry Pi
    serial = None


BAUDRATE = 57_600
KNOWN_CHANNEL_MASK = 0x7F
SCRIPT_TICK_SECONDS = 0.010
SCRIPT_SETTLE_SECONDS = 0.25


@dataclass(frozen=True)
class Command:
    name: str
    payload: tuple[int, ...]
    delay_after: float = 0.05
    show_rx: bool = False


@dataclass(frozen=True)
class CommandResult:
    command: Command
    tx: bytes
    rx: bytes


@dataclass(frozen=True)
class RunResult:
    command_results: tuple[CommandResult, ...]
    pre_rx: bytes = b""
    post_rx: bytes = b""

    @property
    def tx(self) -> bytes:
        return b"".join(result.tx for result in self.command_results)

    @property
    def rx(self) -> bytes:
        return self.pre_rx + b"".join(result.rx for result in self.command_results) + self.post_rx


@dataclass(frozen=True)
class FeedbackFrame:
    time_s: float
    values: tuple[int, ...]

    def value(self, channel: str) -> int:
        if channel not in CHANNEL_ORDER:
            raise ValueError(f"unknown channel {channel!r}")
        return self.values[CHANNEL_ORDER.index(channel)]


@dataclass(frozen=True)
class ScriptTickTrial:
    hold_ticks: int
    repeat: int
    tx_time_s: float
    samples: tuple[tuple[float, int], ...]
    baseline: float | None
    onset_s: float | None


@dataclass(frozen=True)
class Animation:
    number: int
    name: str
    mask: int
    keyframes: tuple[tuple[int, int, int], ...]

    @property
    def delay_after(self) -> float:
        return sum(duration for _a, _b, duration in self.keyframes) * SCRIPT_TICK_SECONDS + SCRIPT_SETTLE_SECONDS


@dataclass(frozen=True)
class ChannelMap:
    name: str
    mask: int
    slope: float
    intercept: float

    @property
    def min_angle(self) -> float:
        return min(self.angle_from_byte(0), self.angle_from_byte(255))

    @property
    def max_angle(self) -> float:
        return max(self.angle_from_byte(0), self.angle_from_byte(255))

    def angle_from_byte(self, value: int) -> float:
        return self.slope * value + self.intercept

    def byte_from_angle(self, angle: float) -> int:
        clamped = clamp(angle, self.min_angle, self.max_angle)
        value = round((clamped - self.intercept) / self.slope)
        return int(clamp(value, 0, 255))


def fletcher16(data: bytes) -> tuple[int, int]:
    sum1 = 0
    sum2 = 0
    for value in data:
        sum1 = (sum1 + value) % 255
        sum2 = (sum2 + sum1) % 255
    return sum2, sum1


def packet(payload: tuple[int, ...]) -> bytes:
    if len(payload) > 255:
        raise ValueError("Payload is too large for this protocol")
    raw = bytes((0xFA, 0x00, len(payload), *payload))
    return raw + bytes(fletcher16(raw))


def two_channel_script_payload(animation: Animation) -> tuple[int, ...]:
    validate_channel_mask(animation.mask)
    if animation.mask.bit_count() != 2:
        raise ValueError(f"{animation.name} mask must contain exactly two channel bits")
    if len(animation.keyframes) > 0x7F:
        raise ValueError(f"{animation.name} has too many keyframes")

    payload = [0x01, animation.mask, 0x80 | len(animation.keyframes)]
    for index, (value_a, value_b, duration) in enumerate(animation.keyframes, start=1):
        validate_byte_value(value_a, f"{animation.name} keyframe {index} target_a")
        validate_byte_value(value_b, f"{animation.name} keyframe {index} target_b")
        validate_byte_value(duration, f"{animation.name} keyframe {index} duration")
        payload.extend((value_a, value_b, duration))
    return tuple(payload)


def raw_keyframes_payload(mask: int, keyframes: tuple[tuple[int, ...], ...]) -> tuple[int, ...]:
    if not keyframes:
        raise ValueError("Raw animation must contain at least one keyframe")
    if len(keyframes) > 0x7F:
        raise ValueError("Raw animation has too many keyframes; max is 127")
    if mask == 0:
        raise ValueError("Raw animation mask must not be zero")
    if mask & ~KNOWN_CHANNEL_MASK:
        raise ValueError(f"Raw animation mask contains unknown channel bits: 0x{mask & ~KNOWN_CHANNEL_MASK:02X}")

    target_count = mask.bit_count()

    payload = [0x01, mask, 0x80 | len(keyframes)]
    for index, frame in enumerate(keyframes, start=1):
        expected_length = target_count + 1
        if len(frame) != expected_length:
            raise ValueError(
                f"Raw keyframe {index} has {len(frame) - 1} targets, "
                f"but mask 0x{mask:02X} has {target_count} channel bits set"
            )
        payload.extend(validate_byte_value(value, f"raw keyframe {index} byte") for value in frame)
    return tuple(payload)


ANIMATIONS = {
    1: Animation(
        number=1,
        name="Neck_Roll",
        mask=0x05,
        keyframes=(
            (217, 217, 20),
            (255, 127, 20),
            (217, 37, 20),
            (127, 0, 20),
            (37, 37, 20),
            (0, 127, 20),
            (37, 217, 20),
            (127, 255, 20),
            (217, 217, 20),
            (127, 127, 20),
        ),
    ),
    2: Animation(
        number=2,
        name="Half_Close",
        mask=0x60,
        keyframes=((31, 31, 30), (124, 124, 50)),
    ),
    3: Animation(
        number=3,
        name="Eye_Roll_Slow_CCW",
        mask=0x18,
        keyframes=(
            (211, 43, 20),
            (246, 127, 20),
            (211, 211, 20),
            (127, 246, 20),
            (43, 211, 20),
            (8, 127, 20),
            (43, 43, 20),
            (127, 8, 20),
            (211, 43, 20),
            (127, 127, 20),
        ),
    ),
    4: Animation(
        number=4,
        name="Neck_Elevation_Stretch",
        mask=0x06,
        keyframes=((45, 127, 50), (245, 127, 50), (245, 150, 25), (245, 104, 50), (245, 127, 35), (127, 127, 25)),
    ),
    5: Animation(
        number=5,
        name="Alternate_Winks",
        mask=0x60,
        keyframes=((31, 217, 100), (217, 31, 100), (31, 217, 60), (217, 31, 30), (124, 124, 50)),
    ),
    6: Animation(
        number=6,
        name="Eye_Roll_Fast_CW",
        mask=0x18,
        keyframes=(
            (43, 43, 10),
            (8, 127, 10),
            (43, 211, 10),
            (127, 246, 10),
            (211, 211, 10),
            (246, 127, 10),
            (211, 43, 10),
            (127, 8, 10),
            (43, 43, 10),
            (127, 127, 10),
        ),
    ),
    7: Animation(
        number=7,
        name="Neck_Tilt_Stretch",
        mask=0x03,
        keyframes=((226, 18, 50), (170, 72, 70), (226, 18, 70), (28, 236, 150), (85, 182, 70), (28, 236, 70), (127, 127, 50)),
    ),
    8: Animation(
        number=8,
        name="Slow_Blink",
        mask=0x60,
        keyframes=((22, 22, 20), (252, 252, 60), (11, 11, 60), (22, 22, 10)),
    ),
}


CHANNELS = {
    "eyelid_left": ChannelMap("eyelid_left", 0x40, -0.3210, 9.996),
    "eyelid_right": ChannelMap("eyelid_right", 0x20, -0.3216, 10.001),
    "eye_leftright": ChannelMap("eye_leftright", 0x10, -0.1178, 15.023),
    "eye_updown": ChannelMap("eye_updown", 0x08, 0.11765, -15.002),
    "neck_elevation": ChannelMap("neck_elevation", 0x04, -0.10978, 13.996),
    "neck_rotation": ChannelMap("neck_rotation", 0x02, 0.35185, -44.932),
    "neck_tilt": ChannelMap("neck_tilt", 0x01, -0.10977, 13.997),
}

CHANNEL_ORDER = (
    "eyelid_left",
    "eyelid_right",
    "eye_leftright",
    "eye_updown",
    "neck_elevation",
    "neck_rotation",
    "neck_tilt",
)


STARTUP_COMMANDS = (
    Command(
        name="power_on_all_channels",
        payload=(0x4F, 0x7F),
        delay_after=0.20,
    ),
    Command(
        name="neutral_start_pose",
        payload=(0x01, 0x7F, 0x01, 0x7C, 0x7C, 0x7F, 0x7F, 0x7F, 0x7F, 0x7F),
        delay_after=0.30,
    ),
)

POWER_OFF = Command("power_off_all_channels", (0x4F, 0x00), delay_after=0.05)
OVERLAP_TEST_MASK = 0x60
OVERLAP_TEST_FIRST_KEYFRAMES = ((31, 217, 200), (31, 217, 200), (124, 124, 80))
OVERLAP_TEST_SECOND_KEYFRAMES = ((217, 31, 80), (124, 124, 80))
SCRIPT_LENGTH_TEST_MASK = 0x60
SCRIPT_TICK_TEST_MASK = 0x18
SCRIPT_TICK_TEST_CHANNEL = "eye_leftright"
SCRIPT_TICK_TEST_HOLD_CHANNEL = "eye_updown"
SCRIPT_TICK_TEST_START_ANGLE = 10.0
SCRIPT_TICK_TEST_TARGET_ANGLE = -10.0
SCRIPT_TICK_TEST_HOLD_ANGLE = 0.0
SCRIPT_TICK_TEST_TARGET_HOLD_TICKS = 80


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def validate_byte_value(value: int, name: str = "value") -> int:
    if not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not 0 <= value <= 255:
        raise ValueError(f"{name} is outside byte range 0..255")
    return value


def animation_command(animation: Animation) -> Command:
    return Command(
        name=animation.name,
        payload=two_channel_script_payload(animation),
        delay_after=animation.delay_after,
    )


def list_animations() -> str:
    lines = []
    for number in sorted(ANIMATIONS):
        animation = ANIMATIONS[number]
        ticks = sum(duration for _a, _b, duration in animation.keyframes)
        lines.append(f"{number}: {animation.name} mask=0x{animation.mask:02X} ticks={ticks}")
    return "\n".join(lines)


def validate_channel_mask(mask: int, *, allow_zero: bool = False, exactly_one: bool = False) -> None:
    if mask == 0 and not allow_zero:
        raise ValueError("mask must not be zero")
    if mask & ~KNOWN_CHANNEL_MASK:
        raise ValueError(f"mask contains unknown channel bits: 0x{mask & ~KNOWN_CHANNEL_MASK:02X}")
    if exactly_one and mask.bit_count() != 1:
        raise ValueError("mask must contain exactly one channel bit")


def parse_mask(value: str) -> int:
    mask = parse_byte(value)
    try:
        validate_channel_mask(mask)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return mask


def parse_power_mask(value: str) -> int:
    mask = parse_byte(value)
    try:
        validate_channel_mask(mask, allow_zero=True)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return mask


def parse_position_target(value: str) -> tuple[int, int]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("use MASK,VALUE, for example: --position 0x40,73")
    mask = parse_byte(parts[0])
    try:
        validate_channel_mask(mask, exactly_one=True)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return mask, parse_byte(parts[1])


def parse_pose_targets(value: str) -> tuple[int, ...]:
    targets = tuple(parse_byte(part.strip()) for part in value.split(",") if part.strip())
    if len(targets) != len(CHANNEL_ORDER):
        raise argparse.ArgumentTypeError(
            f"pose must contain {len(CHANNEL_ORDER)} target bytes in channel order: {', '.join(CHANNEL_ORDER)}"
        )
    return targets


def parse_pid_values(value: str) -> tuple[float, ...]:
    parts = [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    try:
        values = tuple(float(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("PID values must be numeric") from exc
    if len(values) != 21:
        raise argparse.ArgumentTypeError("PID write requires exactly 21 floats: 7 x (P,I,D)")
    return values


def parse_look_target(value: str) -> tuple[float, float]:
    parts = [part.strip() for part in value.split(",")]
    if not 1 <= len(parts) <= 2 or any(part == "" for part in parts):
        raise argparse.ArgumentTypeError("use YAW or YAW,PITCH, for example: --look 30 or --look 30,-5")

    try:
        yaw = float(parts[0])
        pitch = float(parts[1]) if len(parts) == 2 else 0.0
    except ValueError as exc:
        raise argparse.ArgumentTypeError("look target must use numeric degrees") from exc

    return yaw, pitch


def parse_byte(value: str) -> int:
    try:
        parsed = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not a valid byte") from exc
    if not 0 <= parsed <= 255:
        raise argparse.ArgumentTypeError(f"{value!r} is outside byte range 0..255")
    return parsed


def parse_raw_keyframes(value: str) -> tuple[tuple[int, ...], ...]:
    frames: list[tuple[int, ...]] = []
    for raw_frame in value.replace(";", " ").split():
        parts = [part.strip() for part in raw_frame.split(",")]
        if len(parts) < 2:
            raise argparse.ArgumentTypeError(
                "raw keyframes must be target_1,...,target_N,duration frames separated by semicolon"
            )
        frames.append(tuple(parse_byte(part) for part in parts))

    if not frames:
        raise argparse.ArgumentTypeError("raw keyframes must contain at least one frame")
    if len(frames) > 0x7F:
        raise argparse.ArgumentTypeError("raw keyframes supports at most 127 frames")
    return tuple(frames)


def split_angle(total: float, base: ChannelMap, fine: ChannelMap, base_share: float = 0.65) -> tuple[float, float]:
    total = clamp(total, base.min_angle + fine.min_angle, base.max_angle + fine.max_angle)
    base_angle = clamp(total * base_share, base.min_angle, base.max_angle)
    fine_angle = total - base_angle

    if fine_angle > fine.max_angle:
        overflow = fine_angle - fine.max_angle
        fine_angle = fine.max_angle
        base_angle = clamp(base_angle + overflow, base.min_angle, base.max_angle)
    elif fine_angle < fine.min_angle:
        overflow = fine_angle - fine.min_angle
        fine_angle = fine.min_angle
        base_angle = clamp(base_angle + overflow, base.min_angle, base.max_angle)

    return base_angle, fine_angle


def look_targets(yaw: float, pitch: float, eyelid_offset: float = 0.0) -> dict[str, int]:
    neck_rotation, eye_leftright = split_angle(yaw, CHANNELS["neck_rotation"], CHANNELS["eye_leftright"])
    neck_elevation, eye_updown = split_angle(pitch, CHANNELS["neck_elevation"], CHANNELS["eye_updown"])
    eyelid_angle = eyelid_offset - eye_updown

    return {
        "eyelid_left": CHANNELS["eyelid_left"].byte_from_angle(eyelid_angle),
        "eyelid_right": CHANNELS["eyelid_right"].byte_from_angle(eyelid_angle),
        "eye_leftright": CHANNELS["eye_leftright"].byte_from_angle(eye_leftright),
        "eye_updown": CHANNELS["eye_updown"].byte_from_angle(eye_updown),
        "neck_elevation": CHANNELS["neck_elevation"].byte_from_angle(neck_elevation),
        "neck_rotation": CHANNELS["neck_rotation"].byte_from_angle(neck_rotation),
        "neck_tilt": CHANNELS["neck_tilt"].byte_from_angle(0.0),
    }


def look_pose_command(yaw: float, pitch: float, eyelid_offset: float) -> Command:
    neck_rotation, eye_leftright = split_angle(yaw, CHANNELS["neck_rotation"], CHANNELS["eye_leftright"])
    neck_elevation, eye_updown = split_angle(pitch, CHANNELS["neck_elevation"], CHANNELS["eye_updown"])
    eyelid_angle = eyelid_offset - eye_updown
    targets = look_targets(yaw, pitch, eyelid_offset)
    payload = (
        0x01,
        0x7F,
        0x01,
        targets["eyelid_left"],
        targets["eyelid_right"],
        targets["eye_leftright"],
        targets["eye_updown"],
        targets["neck_elevation"],
        targets["neck_rotation"],
        targets["neck_tilt"],
    )
    name = (
        f"look_yaw={yaw:g}_pitch={pitch:g}"
        f"_eye_lr={eye_leftright:0.1f}_neck_rot={neck_rotation:0.1f}"
        f"_eye_ud={eye_updown:0.1f}_neck_el={neck_elevation:0.1f}"
        f"_eyelid={eyelid_angle:0.1f}"
    )
    return Command(name=name, payload=payload, delay_after=0.80)


def raw_keyframes_command(mask: int, keyframes: tuple[tuple[int, ...], ...]) -> Command:
    payload = raw_keyframes_payload(mask, keyframes)
    ticks = sum(frame[-1] for frame in keyframes)
    return Command(
        name=f"raw_keyframes_mask=0x{mask:02X}_frames={len(keyframes)}_ticks={ticks}",
        payload=payload,
        delay_after=ticks * SCRIPT_TICK_SECONDS + SCRIPT_SETTLE_SECONDS,
    )


def overlap_test_commands(overlap_delay: float = 0.50, listen_after_second: float = 3.00) -> list[Command]:
    first = raw_keyframes_command(OVERLAP_TEST_MASK, OVERLAP_TEST_FIRST_KEYFRAMES)
    second = raw_keyframes_command(OVERLAP_TEST_MASK, OVERLAP_TEST_SECOND_KEYFRAMES)
    return [
        *STARTUP_COMMANDS,
        Command(
            name=f"overlap_test_first_wait={overlap_delay:g}s",
            payload=first.payload,
            delay_after=overlap_delay,
            show_rx=True,
        ),
        Command(
            name="overlap_test_second_opposite",
            payload=second.payload,
            delay_after=listen_after_second,
            show_rx=True,
        ),
    ]


def script_length_test_command(frame_count: int, duration_ticks: int = 10, mask: int = SCRIPT_LENGTH_TEST_MASK) -> Command:
    if frame_count < 1:
        raise ValueError("frame_count must be at least 1")
    if frame_count > 0x7F:
        raise ValueError("frame_count must be <= 127")
    validate_byte_value(duration_ticks, "duration_ticks")
    if duration_ticks == 0:
        raise ValueError("duration_ticks must be > 0")
    validate_channel_mask(mask)

    target_count = mask.bit_count()
    if target_count != 2:
        raise ValueError("script length test currently uses a 2-channel mask")

    keyframes = []
    for index in range(frame_count):
        if index % 2 == 0:
            keyframes.append((31, 217, duration_ticks))
        else:
            keyframes.append((217, 31, duration_ticks))

    payload = raw_keyframes_payload(mask, tuple(keyframes))
    if len(payload) > 255:
        raise ValueError(f"generated payload is {len(payload)} bytes, max is 255")
    return Command(
        name=f"script_length_test_mask=0x{mask:02X}_frames={frame_count}_ticks={frame_count * duration_ticks}",
        payload=payload,
        delay_after=frame_count * duration_ticks * SCRIPT_TICK_SECONDS + 0.50,
        show_rx=True,
    )


def parse_tick_durations(value: str) -> tuple[int, ...]:
    try:
        durations = tuple(int(part.strip(), 0) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("tick durations must be comma-separated integers") from exc
    if not durations:
        raise argparse.ArgumentTypeError("tick duration list must not be empty")
    for duration in durations:
        if not 1 <= duration <= 255:
            raise argparse.ArgumentTypeError("tick durations must be in range 1..255")
    return durations


def script_tick_test_commands(hold_ticks: Sequence[int], repeats: int) -> tuple[Command, ...]:
    if repeats < 1:
        raise ValueError("script tick repeats must be at least 1")

    start_value = CHANNELS[SCRIPT_TICK_TEST_CHANNEL].byte_from_angle(SCRIPT_TICK_TEST_START_ANGLE)
    target_value = CHANNELS[SCRIPT_TICK_TEST_CHANNEL].byte_from_angle(SCRIPT_TICK_TEST_TARGET_ANGLE)
    hold_value = CHANNELS[SCRIPT_TICK_TEST_HOLD_CHANNEL].byte_from_angle(SCRIPT_TICK_TEST_HOLD_ANGLE)

    commands: list[Command] = [*STARTUP_COMMANDS]
    for repeat in range(1, repeats + 1):
        for ticks in hold_ticks:
            validate_byte_value(ticks, "script tick hold duration")
            keyframes = (
                (start_value, hold_value, ticks),
                (target_value, hold_value, SCRIPT_TICK_TEST_TARGET_HOLD_TICKS),
            )
            payload = raw_keyframes_payload(SCRIPT_TICK_TEST_MASK, keyframes)
            settle = position_command(CHANNELS[SCRIPT_TICK_TEST_CHANNEL].mask, start_value)
            commands.extend(
                (
                    Command(
                        name=f"script_tick_start_repeat={repeat}_hold={ticks}",
                        payload=settle.payload,
                        delay_after=0.90,
                    ),
                    Command(
                        name=f"script_tick_trial_repeat={repeat}_hold={ticks}",
                        payload=payload,
                        # Give the board enough time even if the real tick is slower than our current assumption.
                        delay_after=1.00 + ticks * 0.014,
                    ),
                )
            )
    return tuple(commands)


class FeedbackDecoder:
    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes, time_s: float) -> tuple[FeedbackFrame, ...]:
        self._buffer.extend(data)
        frames: list[FeedbackFrame] = []

        while True:
            try:
                start = self._buffer.index(0xFA)
            except ValueError:
                self._buffer.clear()
                break
            if start:
                del self._buffer[:start]
            if len(self._buffer) < 5:
                break

            payload_len = self._buffer[2]
            packet_len = 3 + payload_len + 2
            if len(self._buffer) < packet_len:
                break

            raw = bytes(self._buffer[:packet_len])
            del self._buffer[:packet_len]
            if raw[1] != 0x00:
                continue
            if tuple(raw[-2:]) != fletcher16(raw[:-2]):
                continue

            payload = raw[3:-2]
            if payload_len == 9 and payload[:2] == b"\x01\x00" and len(payload[2:]) == len(CHANNEL_ORDER):
                frames.append(FeedbackFrame(time_s, tuple(payload[2:])))

        return tuple(frames)


def median(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("median requires at least one value")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def analyze_script_tick_trial(
    hold_ticks: int,
    repeat: int,
    tx_time_s: float,
    samples: Sequence[tuple[float, int]],
) -> ScriptTickTrial:
    if not samples:
        return ScriptTickTrial(hold_ticks, repeat, tx_time_s, tuple(), None, None)

    rel_samples = tuple((time_s - tx_time_s, value) for time_s, value in samples if time_s >= tx_time_s)
    baseline_values = [value for rel_time, value in rel_samples if 0.0 <= rel_time <= 0.08]
    if len(baseline_values) < 2:
        baseline_values = [value for _rel_time, value in rel_samples[:5]]
    if not baseline_values:
        return ScriptTickTrial(hold_ticks, repeat, tx_time_s, tuple(samples), None, None)

    baseline = median(tuple(float(value) for value in baseline_values))
    target_value = CHANNELS[SCRIPT_TICK_TEST_CHANNEL].byte_from_angle(SCRIPT_TICK_TEST_TARGET_ANGLE)
    delta = target_value - baseline
    if abs(delta) < 1:
        return ScriptTickTrial(hold_ticks, repeat, tx_time_s, tuple(samples), baseline, None)

    direction = 1 if delta > 0 else -1
    threshold = baseline + direction * max(12.0, abs(delta) * 0.25)
    onset_s = None
    for rel_time, value in rel_samples:
        if rel_time < 0.10:
            continue
        if direction > 0 and value >= threshold:
            onset_s = rel_time
            break
        if direction < 0 and value <= threshold:
            onset_s = rel_time
            break

    return ScriptTickTrial(hold_ticks, repeat, tx_time_s, tuple(samples), baseline, onset_s)


def summarize_script_tick_trials(trials: Sequence[ScriptTickTrial]) -> str:
    usable = [trial for trial in trials if trial.onset_s is not None]
    lines = [
        "script tick test:",
        f"channel: {SCRIPT_TICK_TEST_CHANNEL}",
        f"start angle: {SCRIPT_TICK_TEST_START_ANGLE:g} deg, target angle: {SCRIPT_TICK_TEST_TARGET_ANGLE:g} deg",
        "hold_ticks,repeat,baseline_byte,onset_s",
    ]
    for trial in trials:
        baseline = "" if trial.baseline is None else f"{trial.baseline:.1f}"
        onset = "" if trial.onset_s is None else f"{trial.onset_s:.4f}"
        lines.append(f"{trial.hold_ticks},{trial.repeat},{baseline},{onset}")

    if len(usable) < 2:
        lines.append("fit: not enough detected onsets")
        return "\n".join(lines)

    xs = [float(trial.hold_ticks) for trial in usable]
    ys = [float(trial.onset_s) for trial in usable if trial.onset_s is not None]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    variance_x = sum((x - mean_x) ** 2 for x in xs)
    if variance_x == 0:
        lines.append("fit: tick durations did not vary")
        return "\n".join(lines)

    slope_s_per_tick = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / variance_x
    intercept_s = mean_y - slope_s_per_tick * mean_x
    residuals = [y - (intercept_s + slope_s_per_tick * x) for x, y in zip(xs, ys)]
    max_abs_residual_ms = max(abs(value) for value in residuals) * 1000.0
    lines.extend(
        (
            f"fit intercept: {intercept_s * 1000.0:.1f} ms",
            f"fit tick: {slope_s_per_tick * 1000.0:.3f} ms/tick",
            f"max residual: {max_abs_residual_ms:.1f} ms",
        )
    )
    return "\n".join(lines)


def power_command(mask: int) -> Command:
    validate_channel_mask(mask, allow_zero=True)
    return Command(f"set_power_mask=0x{mask:02X}", (0x4F, mask), delay_after=0.20, show_rx=True)


def position_command(mask: int, value: int) -> Command:
    validate_channel_mask(mask, exactly_one=True)
    validate_byte_value(value, "position value")
    return Command(f"position_mask=0x{mask:02X}_value={value}", (0x01, mask, 0x01, value), delay_after=0.30)


def pose_command(targets: tuple[int, ...]) -> Command:
    if len(targets) != len(CHANNEL_ORDER):
        raise ValueError(f"pose requires {len(CHANNEL_ORDER)} target bytes")
    for index, value in enumerate(targets):
        validate_byte_value(value, f"pose target {index}")
    return Command("pose_all_channels", (0x01, 0x7F, 0x01, *targets), delay_after=0.50)


def calibrate_command(mask: int, timeout: float) -> Command:
    validate_channel_mask(mask)
    return Command(f"calibrate_mask=0x{mask:02X}", (0x43, mask), delay_after=timeout, show_rx=True)


def read_command(name: str, opcode: int, timeout: float = 0.40) -> Command:
    validate_byte_value(opcode, "opcode")
    return Command(name, (opcode,), delay_after=timeout, show_rx=True)


def write_pid_command(values: tuple[float, ...]) -> Command:
    if len(values) != 21:
        raise ValueError("PID write requires exactly 21 floats")
    packed = struct.pack(">21f", *values)
    return Command("write_pid", (0x50, *packed), delay_after=0.60, show_rx=True)


def format_hex(data: bytes) -> str:
    return " ".join(f"{value:02X}" for value in data)


def print_rx(data: bytes, started_at: float) -> None:
    print(f"RX +{time.monotonic() - started_at:0.3f}s: {format_hex(data)}", flush=True)


def read_available(port: "serial.Serial", duration: float, rx_callback=None) -> bytes:
    deadline = time.monotonic() + duration
    chunks: list[bytes] = []
    while time.monotonic() < deadline:
        waiting = port.in_waiting
        if waiting:
            data = port.read(waiting)
            chunks.append(data)
            if rx_callback is not None:
                rx_callback(data)
        time.sleep(0.005)

    waiting = port.in_waiting
    if waiting:
        data = port.read(waiting)
        chunks.append(data)
        if rx_callback is not None:
            rx_callback(data)

    return b"".join(chunks)


def pump_rx(port: "serial.Serial", duration: float, started_at: float, verbose: bool) -> None:
    callback = (lambda data: print_rx(data, started_at)) if verbose else None
    read_available(port, duration, callback)


class RobotMotion:
    def __init__(self, port: str = "/dev/ttyAMA0", baudrate: int = BAUDRATE):
        self.port = port
        self.baudrate = baudrate
        self._serial = None

    def open(self) -> "RobotMotion":
        if self._serial is not None:
            return self
        if serial is None:
            raise RuntimeError("pyserial is missing. Install with: sudo apt install python3-serial")
        self._serial = serial.Serial(self.port, self.baudrate, bytesize=8, parity="N", stopbits=1, timeout=0)
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        return self

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def __enter__(self) -> "RobotMotion":
        return self.open()

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    @property
    def serial_port(self):
        if self._serial is None:
            self.open()
        return self._serial

    def run_commands(
        self,
        commands: list[Command] | tuple[Command, ...],
        *,
        listen_before: float = 0.10,
        listen_after: float = 0.50,
        tx_callback=None,
        rx_callback=None,
    ) -> RunResult:
        port = self.serial_port
        pre_rx = read_available(port, listen_before, rx_callback)
        results: list[CommandResult] = []

        for command in commands:
            tx = packet(command.payload)
            if tx_callback is not None:
                tx_callback(command, tx)
            port.write(tx)
            port.flush()
            rx = read_available(port, command.delay_after, rx_callback)
            results.append(CommandResult(command=command, tx=tx, rx=rx))

        post_rx = read_available(port, listen_after, rx_callback)
        return RunResult(command_results=tuple(results), pre_rx=pre_rx, post_rx=post_rx)

    def power_mask(self, mask: int, **kwargs) -> RunResult:
        return self.run_commands([power_command(mask)], **kwargs)

    def position(self, mask: int, value: int, *, power_on: bool = True, **kwargs) -> RunResult:
        commands = ([STARTUP_COMMANDS[0]] if power_on else []) + [position_command(mask, value)]
        return self.run_commands(commands, **kwargs)

    def pose(self, targets: tuple[int, ...], *, power_on: bool = True, **kwargs) -> RunResult:
        commands = ([STARTUP_COMMANDS[0]] if power_on else []) + [pose_command(targets)]
        return self.run_commands(commands, **kwargs)

    def animation(self, number: int = 5, *, power_off: bool = False, **kwargs) -> RunResult:
        commands = [*STARTUP_COMMANDS, animation_command(ANIMATIONS[number])]
        if power_off:
            commands.append(POWER_OFF)
        return self.run_commands(commands, **kwargs)

    def look(self, yaw: float, pitch: float = 0.0, *, eyelid_offset: float = 0.0, power_off: bool = False, **kwargs) -> RunResult:
        commands = [STARTUP_COMMANDS[0], look_pose_command(yaw, pitch, eyelid_offset)]
        if power_off:
            commands.append(POWER_OFF)
        return self.run_commands(commands, **kwargs)

    def raw_keyframes(self, mask: int, keyframes: tuple[tuple[int, ...], ...], *, power_off: bool = False, **kwargs) -> RunResult:
        commands = [*STARTUP_COMMANDS, raw_keyframes_command(mask, keyframes)]
        if power_off:
            commands.append(POWER_OFF)
        return self.run_commands(commands, **kwargs)

    def overlap_test(self, *, overlap_delay: float = 0.50, listen_after_second: float = 3.00, power_off: bool = False, **kwargs) -> RunResult:
        commands = overlap_test_commands(overlap_delay, listen_after_second)
        if power_off:
            commands.append(POWER_OFF)
        return self.run_commands(commands, **kwargs)

    def script_length_test(self, frame_count: int, *, duration_ticks: int = 10, power_off: bool = False, **kwargs) -> RunResult:
        commands = [*STARTUP_COMMANDS, script_length_test_command(frame_count, duration_ticks)]
        if power_off:
            commands.append(POWER_OFF)
        return self.run_commands(commands, **kwargs)

    def calibrate(self, mask: int, *, timeout: float = 25.0, **kwargs) -> RunResult:
        return self.run_commands([STARTUP_COMMANDS[0], calibrate_command(mask, timeout)], **kwargs)

    def read_vr(self, **kwargs) -> RunResult:
        return self.run_commands([read_command("read_vr_values", 0x40)], **kwargs)

    def read_calibration(self, **kwargs) -> RunResult:
        return self.run_commands([read_command("read_calibration_values", 0x42)], **kwargs)

    def read_pid(self, **kwargs) -> RunResult:
        return self.run_commands([read_command("read_pid", 0x47)], **kwargs)

    def reset_pid(self, **kwargs) -> RunResult:
        return self.run_commands([read_command("reset_pid", 0x52, timeout=0.60)], **kwargs)

    def read_version(self, **kwargs) -> RunResult:
        return self.run_commands([read_command("read_version", 0x56)], **kwargs)

    def write_pid(self, values: tuple[float, ...], **kwargs) -> RunResult:
        return self.run_commands([write_pid_command(values)], **kwargs)


NEGATIVE_VALUE_OPTIONS = {"--look", "--write-pid"}


def normalize_negative_option_values(argv: list[str]) -> list[str]:
    normalized: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item in NEGATIVE_VALUE_OPTIONS and index + 1 < len(argv):
            value = argv[index + 1]
            if value.startswith("-") and len(value) > 1 and not value.startswith("--") and (value[1].isdigit() or value[1] == "."):
                normalized.append(f"{item}={value}")
                index += 2
                continue

        normalized.append(item)
        index += 1

    return normalized


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send known UART protocol commands to the motor board."
    )
    parser.add_argument(
        "--animation",
        type=int,
        choices=sorted(ANIMATIONS),
        help="Animation number to run. If no command mode is selected, defaults to 5 (Alternate_Winks).",
    )
    parser.add_argument(
        "--look",
        type=parse_look_target,
        metavar="YAW[,PITCH]",
        help="Look at a target angle in degrees by combining eyes and neck. Example: --look 30 or --look 30,-5.",
    )
    parser.add_argument(
        "--position",
        type=parse_position_target,
        metavar="MASK,VALUE",
        help="Send one raw position target: 01 <mask> 01 <value>. Mask must contain exactly one known channel bit.",
    )
    parser.add_argument(
        "--pose",
        type=parse_pose_targets,
        metavar="V0,V1,V2,V3,V4,V5,V6",
        help="Send all seven raw targets in feedback/channel order.",
    )
    parser.add_argument(
        "--raw-mask",
        type=parse_byte,
        metavar="MASK",
        help="Channel mask for --raw-keyframes, for example 0x60 for eyelids or 0x78 for eyelids+eyes.",
    )
    parser.add_argument(
        "--raw-keyframes",
        type=parse_raw_keyframes,
        metavar="A[,B...],T;...",
        help="Raw keyframes as target_1,...,target_N,duration_ticks frames. N must match the number of bits in --raw-mask.",
    )
    parser.add_argument(
        "--eyelid-offset",
        type=float,
        default=0.0,
        help="For --look, set both eyelids this many degrees above the inverted eye_updown relation. Default: 0.",
    )
    parser.add_argument("--power-mask", type=parse_power_mask, metavar="MASK", help="Send power/enable mask 4F <mask>. Use 0 to disable all.")
    parser.add_argument("--calibrate", type=parse_mask, metavar="MASK", help="Start calibration with 43 <mask>.")
    parser.add_argument("--read-vr", action="store_true", help="Read current VR/position values with opcode 0x40.")
    parser.add_argument("--read-calibration", action="store_true", help="Read calibration values with opcode 0x42.")
    parser.add_argument("--read-pid", action="store_true", help="Read PID table with opcode 0x47.")
    parser.add_argument("--reset-pid", action="store_true", help="Reset PID table with opcode 0x52.")
    parser.add_argument("--read-version", action="store_true", help="Read firmware/version id with opcode 0x56.")
    parser.add_argument(
        "--write-pid",
        type=parse_pid_values,
        metavar="F1,...,F21",
        help="Write PID table with opcode 0x50. Values are 21 big-endian float32 values: 7 x (P,I,D).",
    )
    parser.add_argument(
        "--test-overlap",
        action="store_true",
        help="Send a long motion script, then another opposite script before the first should be done.",
    )
    parser.add_argument(
        "--overlap-delay",
        type=float,
        default=0.50,
        help="Seconds to wait after the first overlap-test script before sending the second. Default: 0.50.",
    )
    parser.add_argument(
        "--overlap-listen",
        type=float,
        default=3.00,
        help="Seconds to listen after the second overlap-test script. Default: 3.00.",
    )
    parser.add_argument(
        "--test-script-length",
        type=int,
        metavar="FRAMES",
        help="Send a generated 2-channel script with this many frames to test accepted script length.",
    )
    parser.add_argument(
        "--length-test-duration",
        type=int,
        default=10,
        help="Duration ticks per frame for --test-script-length. Default: 10.",
    )
    parser.add_argument(
        "--test-script-tick",
        action="store_true",
        help="Estimate raw motion-script tick duration by fitting feedback onset against hold ticks.",
    )
    parser.add_argument(
        "--script-tick-durations",
        type=parse_tick_durations,
        default=(40, 80, 120, 160, 200),
        metavar="T1,T2,...",
        help="Hold durations in ticks for --test-script-tick. Default: 40,80,120,160,200.",
    )
    parser.add_argument(
        "--script-tick-repeats",
        type=int,
        default=2,
        help="Repeats per hold duration for --test-script-tick. Default: 2.",
    )
    parser.add_argument("--list-animations", action="store_true", help="List available animation numbers and exit.")
    parser.add_argument(
        "--port",
        default="/dev/ttyAMA0",
        help="Serial port to use. On Raspberry Pi 5 GPIO14/15 this is normally /dev/ttyAMA0 after dtoverlay=uart0-pi5.",
    )
    parser.add_argument("--baudrate", type=int, default=BAUDRATE)
    parser.add_argument("--dry-run", action="store_true", help="Print packets without opening the serial port.")
    parser.add_argument("--verbose", action="store_true", help="Print TX/RX bytes while running.")
    parser.add_argument(
        "--power-off",
        action="store_true",
        help="Send 4F 00 after the selected command. Default leaves motors enabled.",
    )
    parser.add_argument(
        "--listen-before",
        type=float,
        default=0.10,
        help="Seconds to listen before the first TX.",
    )
    parser.add_argument(
        "--listen-after",
        type=float,
        default=0.50,
        help="Extra seconds to listen after the last command.",
    )
    parser.add_argument(
        "--calibrate-timeout",
        type=float,
        default=25.0,
        help="Seconds to listen for calibration result after --calibrate. Default: 25.",
    )
    return parser.parse_args(normalize_negative_option_values(sys.argv[1:] if argv is None else argv))


def main() -> int:
    args = parse_args()
    if args.list_animations:
        print(list_animations())
        return 0

    modes = [
        args.animation is not None,
        args.look is not None,
        args.position is not None,
        args.pose is not None,
        args.raw_keyframes is not None or args.raw_mask is not None,
        args.power_mask is not None,
        args.calibrate is not None,
        args.read_vr,
        args.read_calibration,
        args.read_pid,
        args.reset_pid,
        args.read_version,
        args.write_pid is not None,
        args.test_overlap,
        args.test_script_length is not None,
        args.test_script_tick,
    ]
    if sum(modes) > 1:
        print("choose only one command mode at a time", file=sys.stderr)
        return 2
    if args.raw_keyframes is not None and args.raw_mask is None:
        print("--raw-keyframes requires --raw-mask", file=sys.stderr)
        return 2
    if args.raw_mask is not None and args.raw_keyframes is None:
        print("--raw-mask requires --raw-keyframes", file=sys.stderr)
        return 2
    if args.raw_mask is not None and args.raw_mask == 0:
        print("--raw-mask must not be zero", file=sys.stderr)
        return 2
    if args.raw_mask is not None and args.raw_mask & ~KNOWN_CHANNEL_MASK:
        print(
            f"--raw-mask contains unknown channel bits: 0x{args.raw_mask & ~KNOWN_CHANNEL_MASK:02X}; "
            f"valid mask bits are 0x{KNOWN_CHANNEL_MASK:02X}",
            file=sys.stderr,
        )
        return 2

    if args.look is not None:
        yaw, pitch = args.look
        commands = [STARTUP_COMMANDS[0], look_pose_command(yaw, pitch, args.eyelid_offset)]
    elif args.position is not None:
        mask, value = args.position
        commands = [STARTUP_COMMANDS[0], position_command(mask, value)]
    elif args.pose is not None:
        commands = [STARTUP_COMMANDS[0], pose_command(args.pose)]
    elif args.raw_keyframes is not None:
        try:
            commands = [*STARTUP_COMMANDS, raw_keyframes_command(args.raw_mask, args.raw_keyframes)]
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    elif args.power_mask is not None:
        commands = [power_command(args.power_mask)]
    elif args.calibrate is not None:
        commands = [STARTUP_COMMANDS[0], calibrate_command(args.calibrate, args.calibrate_timeout)]
    elif args.read_vr:
        commands = [read_command("read_vr_values", 0x40)]
    elif args.read_calibration:
        commands = [read_command("read_calibration_values", 0x42)]
    elif args.read_pid:
        commands = [read_command("read_pid", 0x47)]
    elif args.reset_pid:
        commands = [read_command("reset_pid", 0x52, timeout=0.60)]
    elif args.read_version:
        commands = [read_command("read_version", 0x56)]
    elif args.write_pid is not None:
        commands = [write_pid_command(args.write_pid)]
    elif args.test_overlap:
        commands = overlap_test_commands(args.overlap_delay, args.overlap_listen)
    elif args.test_script_length is not None:
        try:
            commands = [*STARTUP_COMMANDS, script_length_test_command(args.test_script_length, args.length_test_duration)]
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    elif args.test_script_tick:
        try:
            commands = list(script_tick_test_commands(args.script_tick_durations, args.script_tick_repeats))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    else:
        animation = ANIMATIONS[args.animation if args.animation is not None else 5]
        commands = [*STARTUP_COMMANDS, animation_command(animation)]
    if args.power_off:
        commands.append(POWER_OFF)

    if args.dry_run:
        for command in commands:
            print(f"{command.name}: {format_hex(packet(command.payload))}")
        return 0

    started_at = time.monotonic()
    show_rx = args.verbose or any(command.show_rx for command in commands)
    show_tx = args.verbose or args.test_overlap or args.test_script_length is not None or args.test_script_tick

    script_tick_decoder = FeedbackDecoder()
    script_tick_tx_times: dict[tuple[int, int], float] = {}
    script_tick_samples: dict[tuple[int, int], list[tuple[float, int]]] = {}
    script_tick_active = {"key": None}

    def handle_tx(command: Command, tx: bytes) -> None:
        if args.test_script_tick:
            if command.name.startswith("script_tick_trial_repeat="):
                rest = command.name.removeprefix("script_tick_trial_repeat=")
                repeat_text, hold_text = rest.split("_hold=", 1)
                key = (int(hold_text), int(repeat_text))
                script_tick_active["key"] = key
                script_tick_tx_times[key] = time.monotonic() - started_at
                script_tick_samples[key] = []
            elif command.name.startswith("script_tick_"):
                script_tick_active["key"] = None

        if show_tx:
            print(
                f"TX +{time.monotonic() - started_at:0.3f}s {command.name}: {format_hex(tx)}",
                flush=True,
            )

    def handle_rx(data: bytes) -> None:
        now_s = time.monotonic() - started_at
        if show_rx:
            print_rx(data, started_at)
        if args.test_script_tick:
            key = script_tick_active["key"]
            if key is not None:
                for frame in script_tick_decoder.feed(data, now_s):
                    script_tick_samples.setdefault(key, []).append(
                        (frame.time_s, frame.value(SCRIPT_TICK_TEST_CHANNEL))
                    )

    tx_callback = handle_tx if show_tx or args.test_script_tick else None
    rx_callback = handle_rx if show_rx or args.test_script_tick else None

    try:
        with RobotMotion(args.port, args.baudrate) as robot:
            robot.run_commands(
                commands,
                listen_before=args.listen_before,
                listen_after=args.listen_after,
                tx_callback=tx_callback,
                rx_callback=rx_callback,
            )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.test_script_tick:
        trials: list[ScriptTickTrial] = []
        for repeat in range(1, args.script_tick_repeats + 1):
            for hold_ticks in args.script_tick_durations:
                key = (hold_ticks, repeat)
                tx_time = script_tick_tx_times.get(key)
                if tx_time is None:
                    trials.append(ScriptTickTrial(hold_ticks, repeat, 0.0, tuple(), None, None))
                    continue
                trials.append(
                    analyze_script_tick_trial(
                        hold_ticks,
                        repeat,
                        tx_time,
                        script_tick_samples.get(key, ()),
                    )
                )
        print(summarize_script_tick_trials(trials))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
