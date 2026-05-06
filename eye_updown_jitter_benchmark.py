#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from robot_motion import (
    BAUDRATE,
    CHANNELS,
    CHANNEL_ORDER,
    POWER_OFF,
    SCRIPT_TICK_SECONDS,
    STARTUP_COMMANDS,
    Command,
    FeedbackDecoder,
    RobotMotion,
    format_hex,
    packet,
    pose_command,
    position_command,
    raw_keyframes_command,
)


CHANNEL = "eye_updown"
TESTABLE_CHANNELS = ("eye_updown", "eye_leftright")
NEUTRAL_TARGETS = tuple(STARTUP_COMMANDS[1].payload[3:])
NEUTRAL_BY_CHANNEL = dict(zip(CHANNEL_ORDER, NEUTRAL_TARGETS))


@dataclass(frozen=True)
class Segment:
    start_s: float
    end_s: float
    from_angle: float
    to_angle: float
    kind: str


@dataclass(frozen=True)
class Case:
    name: str
    move_ticks: int
    hold_ticks: int
    cycles: int
    start_angle: float
    high_angle: float
    low_angle: float
    neutral_angle: float
    command: Command
    segments: tuple[Segment, ...]

    @property
    def duration_s(self) -> float:
        return self.segments[-1].end_s if self.segments else 0.0


@dataclass(frozen=True)
class FeedbackSample:
    case: str
    repeat: int
    time_s: float
    values: tuple[int, ...]

    @property
    def channel_byte(self) -> int:
        return self.values[CHANNEL_ORDER.index(CHANNEL)]

    @property
    def channel_angle(self) -> float:
        return CHANNELS[CHANNEL].angle_from_byte(self.channel_byte)


@dataclass(frozen=True)
class TxEvent:
    case: str
    repeat: int
    role: str
    time_s: float
    command_name: str
    tx_hex: str


def parse_int_list(value: str) -> tuple[int, ...]:
    try:
        values = tuple(int(part.strip(), 0) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from exc
    if not values:
        raise argparse.ArgumentTypeError("list must not be empty")
    return values


def clamp_angle(channel: str, angle: float) -> float:
    channel_map = CHANNELS[channel]
    return max(channel_map.min_angle, min(channel_map.max_angle, angle))


def value_for_angle(angle: float) -> int:
    return CHANNELS[CHANNEL].byte_from_angle(angle)


def build_case(
    *,
    move_ticks: int,
    hold_ticks: int,
    cycles: int,
    low_angle: float,
    high_angle: float,
    neutral_angle: float,
) -> Case:
    if move_ticks < 1:
        raise ValueError("move_ticks must be >= 1")
    if hold_ticks < 0:
        raise ValueError("hold_ticks must be >= 0")
    if cycles < 1:
        raise ValueError("cycles must be >= 1")

    low_angle = clamp_angle(CHANNEL, low_angle)
    high_angle = clamp_angle(CHANNEL, high_angle)
    neutral_angle = clamp_angle(CHANNEL, neutral_angle)
    frames: list[tuple[int, int]] = []
    segments: list[Segment] = []

    cursor_s = 0.0
    current_angle = low_angle

    def append_target(target_angle: float, ticks: int, kind: str) -> None:
        nonlocal cursor_s, current_angle
        if ticks <= 0:
            return
        start_s = cursor_s
        end_s = start_s + ticks * SCRIPT_TICK_SECONDS
        frames.append((value_for_angle(target_angle), ticks))
        segments.append(
            Segment(
                start_s=start_s,
                end_s=end_s,
                from_angle=current_angle,
                to_angle=target_angle,
                kind=kind,
            )
        )
        cursor_s = end_s
        current_angle = target_angle

    for _ in range(cycles):
        append_target(high_angle, move_ticks, "move_up")
        append_target(high_angle, hold_ticks, "hold_high")
        append_target(low_angle, move_ticks, "move_down")
        append_target(low_angle, hold_ticks, "hold_low")
    append_target(neutral_angle, max(move_ticks, 10), "return_neutral")

    command = raw_keyframes_command(CHANNELS[CHANNEL].mask, tuple((*frame,) for frame in frames))
    name = f"{CHANNEL}_move{move_ticks}_hold{hold_ticks}_cycles{cycles}"
    return Case(
        name=name,
        move_ticks=move_ticks,
        hold_ticks=hold_ticks,
        cycles=cycles,
        start_angle=low_angle,
        high_angle=high_angle,
        low_angle=low_angle,
        neutral_angle=neutral_angle,
        command=Command(name, command.payload, command.delay_after),
        segments=tuple(segments),
    )


def expected_angle(case: Case, time_s: float) -> tuple[float | None, str | None]:
    if time_s < 0:
        return None, None
    for segment in case.segments:
        if segment.start_s <= time_s <= segment.end_s:
            duration = segment.end_s - segment.start_s
            if duration <= 0:
                return segment.to_angle, segment.kind
            u = (time_s - segment.start_s) / duration
            angle = segment.from_angle + (segment.to_angle - segment.from_angle) * u
            return angle, segment.kind
    if case.segments:
        return case.segments[-1].to_angle, "after"
    return None, None


def expected_angle_phase_progress(case: Case, time_s: float) -> tuple[float | None, str | None, float | None]:
    if time_s < 0:
        return None, None, None
    for segment in case.segments:
        if segment.start_s <= time_s <= segment.end_s:
            duration = segment.end_s - segment.start_s
            if duration <= 0:
                return segment.to_angle, segment.kind, 1.0
            u = (time_s - segment.start_s) / duration
            angle = segment.from_angle + (segment.to_angle - segment.from_angle) * u
            return angle, segment.kind, u
    if case.segments:
        return case.segments[-1].to_angle, "after", 1.0
    return None, None, None


def rms(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return math.sqrt(sum(value * value for value in values) / len(values))


def summarize_case(case: Case, repeat: int, samples: Sequence[FeedbackSample]) -> dict:
    residuals: list[float] = []
    hold_residuals: list[float] = []
    hold_angles: list[float] = []
    hold_tail_residuals: list[float] = []
    hold_tail_angles: list[float] = []
    move_residuals: list[float] = []
    velocities: list[float] = []
    last_sample: FeedbackSample | None = None

    for sample in samples:
        target, phase, progress = expected_angle_phase_progress(case, sample.time_s)
        if target is None:
            continue
        residual = sample.channel_angle - target
        residuals.append(residual)
        if phase and phase.startswith("hold"):
            hold_residuals.append(residual)
            hold_angles.append(sample.channel_angle)
            if progress is not None and progress >= 0.5:
                hold_tail_residuals.append(residual)
                hold_tail_angles.append(sample.channel_angle)
        elif phase and phase.startswith("move"):
            move_residuals.append(residual)
        if last_sample is not None:
            dt = sample.time_s - last_sample.time_s
            if dt > 0:
                velocities.append((sample.channel_angle - last_sample.channel_angle) / dt)
        last_sample = sample

    hold_peak_to_peak = None
    if hold_angles:
        hold_peak_to_peak = max(hold_angles) - min(hold_angles)
    hold_tail_peak_to_peak = None
    if hold_tail_angles:
        hold_tail_peak_to_peak = max(hold_tail_angles) - min(hold_tail_angles)
    hold_tail_residual_peak_to_peak = None
    if hold_tail_residuals:
        hold_tail_residual_peak_to_peak = max(hold_tail_residuals) - min(hold_tail_residuals)

    velocity_rms = rms(velocities)
    return {
        "case": case.name,
        "repeat": repeat,
        "move_ticks": case.move_ticks,
        "move_ms": case.move_ticks * SCRIPT_TICK_SECONDS * 1000.0,
        "hold_ticks": case.hold_ticks,
        "hold_ms": case.hold_ticks * SCRIPT_TICK_SECONDS * 1000.0,
        "cycles": case.cycles,
        "sample_count": len(samples),
        "duration_s": case.duration_s,
        "residual_rms_deg": rms(residuals),
        "residual_peak_to_peak_deg": (max(residuals) - min(residuals)) if residuals else None,
        "move_residual_rms_deg": rms(move_residuals),
        "hold_residual_rms_deg": rms(hold_residuals),
        "hold_angle_peak_to_peak_deg": hold_peak_to_peak,
        "hold_tail_residual_rms_deg": rms(hold_tail_residuals),
        "hold_tail_angle_peak_to_peak_deg": hold_tail_peak_to_peak,
        "hold_tail_residual_peak_to_peak_deg": hold_tail_residual_peak_to_peak,
        "velocity_rms_deg_per_s": velocity_rms,
    }


class BenchmarkRunner:
    def __init__(self, port: str, baudrate: int, verbose: bool) -> None:
        self.robot = RobotMotion(port, baudrate)
        self.decoder = FeedbackDecoder()
        self.verbose = verbose
        self.started_at = 0.0
        self.case_started_at = 0.0
        self.samples: list[FeedbackSample] = []
        self.tx_events: list[TxEvent] = []
        self.current_case = ""
        self.current_repeat = 0

    @property
    def port(self):
        return self.robot.serial_port

    def now_s(self) -> float:
        return time.monotonic() - self.started_at

    def open(self) -> None:
        self.robot.open()
        self.started_at = time.monotonic()

    def close(self) -> None:
        self.robot.close()

    def read_available(self) -> None:
        waiting = self.port.in_waiting
        if not waiting:
            return
        data = self.port.read(waiting)
        now_s = self.now_s()
        rel_s = now_s - self.case_started_at
        for frame in self.decoder.feed(data, rel_s):
            self.samples.append(
                FeedbackSample(
                    case=self.current_case,
                    repeat=self.current_repeat,
                    time_s=frame.time_s,
                    values=frame.values,
                )
            )

    def pump_for(self, duration_s: float) -> None:
        deadline = self.now_s() + max(0.0, duration_s)
        while self.now_s() < deadline:
            self.read_available()
            time.sleep(0.004)
        self.read_available()

    def send(self, command: Command, role: str) -> float:
        self.read_available()
        tx = packet(command.payload)
        tx_time_s = self.now_s()
        self.port.write(tx)
        self.port.flush()
        rel_s = tx_time_s - self.case_started_at
        self.tx_events.append(
            TxEvent(
                case=self.current_case,
                repeat=self.current_repeat,
                role=role,
                time_s=rel_s,
                command_name=command.name,
                tx_hex=format_hex(tx),
            )
        )
        if self.verbose:
            print(
                f"TX {self.current_case}#{self.current_repeat} {role} "
                f"+{rel_s:.3f}s {command.name}: {format_hex(tx)}",
                flush=True,
            )
        return tx_time_s

    def reset_to_start(self, start_angle: float, settle_s: float) -> None:
        self.current_case = "reset"
        self.current_repeat = 0
        self.case_started_at = self.now_s()
        self.decoder = FeedbackDecoder()
        self.send(STARTUP_COMMANDS[0], "power_on")
        self.pump_for(STARTUP_COMMANDS[0].delay_after)
        self.send(pose_command(NEUTRAL_TARGETS), "neutral_pose")
        self.pump_for(0.50)
        self.send(position_command(CHANNELS[CHANNEL].mask, value_for_angle(start_angle)), "start_angle")
        self.pump_for(settle_s)

    def run_case(self, case: Case, repeat: int, settle_s: float, listen_after_s: float) -> list[FeedbackSample]:
        self.reset_to_start(case.start_angle, settle_s)
        self.current_case = case.name
        self.current_repeat = repeat
        self.case_started_at = self.now_s()
        self.decoder = FeedbackDecoder()
        start_index = len(self.samples)
        print(
            f"case {case.name} repeat {repeat}: "
            f"move={case.move_ticks} ticks ({case.move_ticks * SCRIPT_TICK_SECONDS * 1000:.0f} ms)",
            flush=True,
        )
        self.send(case.command, "script")
        self.pump_for(case.duration_s + listen_after_s)
        return self.samples[start_index:]

    def power_off(self) -> None:
        self.current_case = "power_off"
        self.current_repeat = 0
        self.case_started_at = self.now_s()
        self.send(POWER_OFF, "power_off")
        self.pump_for(POWER_OFF.delay_after)


def write_csv(path: Path, cases: dict[str, Case], samples: Sequence[FeedbackSample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "case",
                "repeat",
                "time_s",
                "phase",
                "target_angle",
                "channel_byte",
                "channel_angle",
                "residual_deg",
                *CHANNEL_ORDER,
            ]
        )
        for sample in samples:
            case = cases.get(sample.case)
            target = None
            phase = None
            residual = None
            if case is not None:
                target, phase = expected_angle(case, sample.time_s)
                if target is not None:
                    residual = sample.channel_angle - target
            writer.writerow(
                [
                    sample.case,
                    sample.repeat,
                    f"{sample.time_s:.6f}",
                    phase or "",
                    "" if target is None else f"{target:.6f}",
                    sample.channel_byte,
                    f"{sample.channel_angle:.6f}",
                    "" if residual is None else f"{residual:.6f}",
                    *sample.values,
                ]
            )


def write_json(
    path: Path,
    *,
    args: argparse.Namespace,
    cases: Sequence[Case],
    summaries: Sequence[dict],
    tx_events: Sequence[TxEvent],
    csv_path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "kind": "eye_jitter_benchmark",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "channel": CHANNEL,
        "channel_limits_deg": {
            "min": CHANNELS[CHANNEL].min_angle,
            "max": CHANNELS[CHANNEL].max_angle,
        },
        "parameters": {
            "channel": args.channel,
            "port": args.port,
            "baudrate": args.baudrate,
            "move_ticks": list(args.move_ticks),
            "hold_ticks": args.hold_ticks,
            "cycles": args.cycles,
            "repeats": args.repeats,
            "low_angle": args.low_angle,
            "high_angle": args.high_angle,
            "neutral_angle": args.neutral_angle,
            "settle_s": args.settle,
            "listen_after_s": args.listen_after,
        },
        "csv": str(csv_path),
        "cases": [
            {
                "name": case.name,
                "move_ticks": case.move_ticks,
                "hold_ticks": case.hold_ticks,
                "cycles": case.cycles,
                "start_angle": case.start_angle,
                "low_angle": case.low_angle,
                "high_angle": case.high_angle,
                "neutral_angle": case.neutral_angle,
                "duration_s": case.duration_s,
                "command_payload_hex": format_hex(bytes(case.command.payload)),
                "segments": [segment.__dict__ for segment in case.segments],
            }
            for case in cases
        ],
        "tx_events": [event.__dict__ for event in tx_events],
        "summaries": list(summaries),
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark eye shake by running large scripted movements at different speeds."
    )
    parser.add_argument("--channel", choices=TESTABLE_CHANNELS, default=CHANNEL)
    parser.add_argument("--port", default="/dev/ttyAMA0")
    parser.add_argument("--baudrate", type=int, default=BAUDRATE)
    parser.add_argument("--move-ticks", type=parse_int_list, default=(8, 12, 20, 35, 60, 100))
    parser.add_argument("--hold-ticks", type=int, default=30)
    parser.add_argument("--cycles", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--low-angle", type=float, default=-13.5)
    parser.add_argument("--high-angle", type=float, default=13.5)
    parser.add_argument("--neutral-angle", type=float, default=0.0)
    parser.add_argument("--settle", type=float, default=0.8)
    parser.add_argument("--listen-after", type=float, default=0.8)
    parser.add_argument("--output", type=Path, default=Path("dumps/eye_updown_jitter_benchmark.json"))
    parser.add_argument("--csv", type=Path, default=Path("dumps/eye_updown_jitter_benchmark_samples.csv"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--power-off", action="store_true")
    return parser.parse_args()


def main() -> int:
    global CHANNEL
    args = parse_args()
    CHANNEL = args.channel
    cases = tuple(
        build_case(
            move_ticks=move_ticks,
            hold_ticks=args.hold_ticks,
            cycles=args.cycles,
            low_angle=args.low_angle,
            high_angle=args.high_angle,
            neutral_angle=args.neutral_angle,
        )
        for move_ticks in args.move_ticks
    )

    if args.dry_run:
        for case in cases:
            print(f"{case.name}: duration={case.duration_s:.3f}s")
            print(f"  mask=0x{CHANNELS[CHANNEL].mask:02X}")
            print(f"  payload={format_hex(bytes(case.command.payload))}")
        return 0

    runner = BenchmarkRunner(args.port, args.baudrate, args.verbose)
    summaries: list[dict] = []
    all_case_samples: list[FeedbackSample] = []
    try:
        runner.open()
        for case in cases:
            for repeat in range(1, args.repeats + 1):
                samples = runner.run_case(case, repeat, args.settle, args.listen_after)
                all_case_samples.extend(samples)
                summary = summarize_case(case, repeat, samples)
                summaries.append(summary)
                hold_p2p = summary["hold_angle_peak_to_peak_deg"]
                hold_tail_p2p = summary["hold_tail_residual_peak_to_peak_deg"]
                hold_rms = summary["hold_residual_rms_deg"]
                residual_rms = summary["residual_rms_deg"]
                print(
                    f"  hold_p2p={hold_p2p if hold_p2p is not None else float('nan'):.3f} deg "
                    f"hold_tail_residual_p2p={hold_tail_p2p if hold_tail_p2p is not None else float('nan'):.3f} deg "
                    f"hold_rms={hold_rms if hold_rms is not None else float('nan'):.3f} deg "
                    f"residual_rms={residual_rms if residual_rms is not None else float('nan'):.3f} deg",
                    flush=True,
                )
        if args.power_off:
            runner.power_off()
    finally:
        runner.close()

    case_by_name = {case.name: case for case in cases}
    write_csv(args.csv, case_by_name, all_case_samples)
    write_json(
        args.output,
        args=args,
        cases=cases,
        summaries=summaries,
        tx_events=runner.tx_events,
        csv_path=args.csv,
    )
    print(f"wrote {args.output}")
    print(f"wrote {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
