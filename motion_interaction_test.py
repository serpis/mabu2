#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

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


NEUTRAL_TARGETS = tuple(STARTUP_COMMANDS[1].payload[3:])
NEUTRAL_BY_CHANNEL = dict(zip(CHANNEL_ORDER, NEUTRAL_TARGETS))


@dataclass(frozen=True)
class TargetFrame:
    targets: dict[str, int]
    duration_ticks: int

    @property
    def duration_s(self) -> float:
        return self.duration_ticks * SCRIPT_TICK_SECONDS


@dataclass(frozen=True)
class MotionPlan:
    name: str
    kind: str
    command: Command
    channels: tuple[str, ...]
    frames: tuple[TargetFrame, ...]
    script_duration_s: float

    @property
    def first_targets(self) -> dict[str, int]:
        if self.frames:
            return self.frames[0].targets
        return {}

    def target_events(self) -> tuple[tuple[float, dict[str, int]], ...]:
        events: list[tuple[float, dict[str, int]]] = []
        cursor_s = 0.0
        for frame in self.frames:
            events.append((cursor_s, frame.targets))
            cursor_s += frame.duration_s
        return tuple(events)


@dataclass(frozen=True)
class CasePlan:
    name: str
    category: str
    first: MotionPlan
    second: MotionPlan
    offset_s: float
    observe_s: float
    repeats: int


@dataclass(frozen=True)
class TxEvent:
    case: str
    repeat: int
    role: str
    name: str
    time_s: float
    payload_hex: str


@dataclass(frozen=True)
class Sample:
    case: str
    repeat: int
    time_s: float
    channel: str
    value: int
    angle: float


@dataclass(frozen=True)
class ChannelSummary:
    channel: str
    second_target: int | None
    value_at_second: int | None
    second_effect_s: float | None
    first_future_effect_s: float | None


@dataclass(frozen=True)
class CaseSummary:
    case: str
    repeat: int
    category: str
    first: str
    second: str
    offset_s: float
    first_expected_end_s: float
    classification: str
    notes: str
    channels: tuple[ChannelSummary, ...]


def channels_for_mask(mask: int) -> tuple[str, ...]:
    return tuple(channel for channel in CHANNEL_ORDER if CHANNELS[channel].mask & mask)


def mask_for_channels(channels: Iterable[str]) -> int:
    mask = 0
    for channel in channels:
        mask |= CHANNELS[channel].mask
    return mask


def byte_for_angle(channel: str, angle: float) -> int:
    return CHANNELS[channel].byte_from_angle(angle)


def ordered_frame_values(channels: tuple[str, ...], targets: dict[str, int], duration_ticks: int) -> tuple[int, ...]:
    return (*[targets[channel] for channel in channels], duration_ticks)


def make_script_plan(name: str, frames: tuple[TargetFrame, ...], channels: tuple[str, ...] | None = None) -> MotionPlan:
    if channels is None:
        mask = 0
        for frame in frames:
            mask |= mask_for_channels(frame.targets)
        channels = channels_for_mask(mask)
    else:
        channels = tuple(channels)
        mask = mask_for_channels(channels)

    raw_frames = tuple(ordered_frame_values(channels, frame.targets, frame.duration_ticks) for frame in frames)
    command = raw_keyframes_command(mask, raw_frames)
    duration_s = sum(frame.duration_s for frame in frames)
    return MotionPlan(name=name, kind="script", command=Command(name, command.payload, command.delay_after), channels=channels, frames=frames, script_duration_s=duration_s)


def make_position_plan(name: str, channel: str, value: int) -> MotionPlan:
    command = position_command(CHANNELS[channel].mask, value)
    frame = TargetFrame({channel: value}, 0)
    return MotionPlan(name=name, kind="position", command=Command(name, command.payload, command.delay_after), channels=(channel,), frames=(frame,), script_duration_s=0.0)


def make_pose_plan(name: str, targets: dict[str, int]) -> MotionPlan:
    merged = dict(NEUTRAL_BY_CHANNEL)
    merged.update(targets)
    target_tuple = tuple(merged[channel] for channel in CHANNEL_ORDER)
    command = pose_command(target_tuple)
    frame = TargetFrame(merged, 0)
    return MotionPlan(name=name, kind="pose", command=Command(name, command.payload, command.delay_after), channels=CHANNEL_ORDER, frames=(frame,), script_duration_s=0.0)


def build_plans() -> dict[str, MotionPlan]:
    eye_lr_neutral = NEUTRAL_BY_CHANNEL["eye_leftright"]
    eye_ud_neutral = NEUTRAL_BY_CHANNEL["eye_updown"]
    lid_left_neutral = NEUTRAL_BY_CHANNEL["eyelid_left"]
    lid_right_neutral = NEUTRAL_BY_CHANNEL["eyelid_right"]

    eye_lr_pos = byte_for_angle("eye_leftright", 10.0)
    eye_lr_neg = byte_for_angle("eye_leftright", -10.0)
    eye_ud_pos = byte_for_angle("eye_updown", 8.0)
    eye_ud_neg = byte_for_angle("eye_updown", -8.0)
    lid_left_a = 80
    lid_right_a = 80
    lid_left_b = 170
    lid_right_b = 170

    plans: dict[str, MotionPlan] = {}
    plans["eyes_long_hold_pos"] = make_script_plan(
        "eyes_long_hold_pos",
        (
            TargetFrame({"eye_leftright": eye_lr_pos, "eye_updown": eye_ud_neutral}, 120),
            TargetFrame({"eye_leftright": eye_lr_pos, "eye_updown": eye_ud_neutral}, 120),
            TargetFrame({"eye_leftright": eye_lr_neutral, "eye_updown": eye_ud_neutral}, 50),
        ),
        ("eye_leftright", "eye_updown"),
    )
    plans["eyes_short_hold_pos"] = make_script_plan(
        "eyes_short_hold_pos",
        (
            TargetFrame({"eye_leftright": eye_lr_pos, "eye_updown": eye_ud_neutral}, 20),
            TargetFrame({"eye_leftright": eye_lr_pos, "eye_updown": eye_ud_neutral}, 20),
            TargetFrame({"eye_leftright": eye_lr_neutral, "eye_updown": eye_ud_neutral}, 20),
        ),
        ("eye_leftright", "eye_updown"),
    )
    plans["eyes_script_neg"] = make_script_plan(
        "eyes_script_neg",
        (
            TargetFrame({"eye_leftright": eye_lr_neg, "eye_updown": eye_ud_neutral}, 70),
            TargetFrame({"eye_leftright": eye_lr_neutral, "eye_updown": eye_ud_neutral}, 40),
        ),
        ("eye_leftright", "eye_updown"),
    )
    plans["lids_script_a"] = make_script_plan(
        "lids_script_a",
        (
            TargetFrame({"eyelid_left": lid_left_a, "eyelid_right": lid_right_a}, 70),
            TargetFrame({"eyelid_left": lid_left_b, "eyelid_right": lid_right_b}, 70),
            TargetFrame({"eyelid_left": lid_left_neutral, "eyelid_right": lid_right_neutral}, 40),
        ),
        ("eyelid_left", "eyelid_right"),
    )
    plans["lids_short_a"] = make_script_plan(
        "lids_short_a",
        (
            TargetFrame({"eyelid_left": lid_left_a, "eyelid_right": lid_right_a}, 20),
            TargetFrame({"eyelid_left": lid_left_b, "eyelid_right": lid_right_b}, 20),
            TargetFrame({"eyelid_left": lid_left_neutral, "eyelid_right": lid_right_neutral}, 20),
        ),
        ("eyelid_left", "eyelid_right"),
    )
    plans["one_lr_long_pos"] = make_script_plan(
        "one_lr_long_pos",
        (
            TargetFrame({"eye_leftright": eye_lr_pos}, 120),
            TargetFrame({"eye_leftright": eye_lr_pos}, 120),
            TargetFrame({"eye_leftright": eye_lr_neutral}, 50),
        ),
        ("eye_leftright",),
    )
    plans["one_lr_script_neg"] = make_script_plan(
        "one_lr_script_neg",
        (
            TargetFrame({"eye_leftright": eye_lr_neg}, 70),
            TargetFrame({"eye_leftright": eye_lr_neutral}, 40),
        ),
        ("eye_leftright",),
    )
    plans["one_ud_script_pos"] = make_script_plan(
        "one_ud_script_pos",
        (
            TargetFrame({"eye_updown": eye_ud_pos}, 70),
            TargetFrame({"eye_updown": eye_ud_neutral}, 40),
        ),
        ("eye_updown",),
    )
    plans["pos_lr_neg"] = make_position_plan("pos_lr_neg", "eye_leftright", eye_lr_neg)
    plans["pos_ud_pos"] = make_position_plan("pos_ud_pos", "eye_updown", eye_ud_pos)
    plans["pos_lid_left_a"] = make_position_plan("pos_lid_left_a", "eyelid_left", lid_left_a)
    plans["pose_eye_neg_ud_pos"] = make_pose_plan(
        "pose_eye_neg_ud_pos",
        {
            "eye_leftright": eye_lr_neg,
            "eye_updown": eye_ud_pos,
            "eyelid_left": lid_left_neutral,
            "eyelid_right": lid_right_neutral,
        },
    )
    plans["neutral_pose"] = make_pose_plan("neutral_pose", dict(NEUTRAL_BY_CHANNEL))
    return plans


def build_cases(repeats: int, suite: str) -> list[CasePlan]:
    plans = build_plans()
    base_cases = [
        CasePlan(
            "same_2ch_long_script_early",
            "script_script_same_channels",
            plans["eyes_long_hold_pos"],
            plans["eyes_script_neg"],
            0.45,
            4.40,
            repeats,
        ),
        CasePlan(
            "same_2ch_long_script_late",
            "script_script_same_channels",
            plans["eyes_long_hold_pos"],
            plans["eyes_script_neg"],
            2.70,
            4.80,
            repeats,
        ),
        CasePlan(
            "same_2ch_short_script_early",
            "script_script_same_channels",
            plans["eyes_short_hold_pos"],
            plans["eyes_script_neg"],
            0.12,
            2.20,
            repeats,
        ),
        CasePlan(
            "disjoint_long_eyes_then_lids",
            "script_script_disjoint_channels",
            plans["eyes_long_hold_pos"],
            plans["lids_script_a"],
            0.45,
            4.40,
            repeats,
        ),
        CasePlan(
            "disjoint_short_eyes_then_lids",
            "script_script_disjoint_channels",
            plans["eyes_short_hold_pos"],
            plans["lids_short_a"],
            0.12,
            2.20,
            repeats,
        ),
        CasePlan(
            "one_channel_same_script",
            "single_channel_script_script_same",
            plans["one_lr_long_pos"],
            plans["one_lr_script_neg"],
            0.45,
            4.40,
            repeats,
        ),
        CasePlan(
            "one_channel_disjoint_script",
            "single_channel_script_script_disjoint",
            plans["one_lr_long_pos"],
            plans["one_ud_script_pos"],
            0.45,
            4.40,
            repeats,
        ),
        CasePlan(
            "script_then_position_same_long",
            "script_position_same_channel",
            plans["eyes_long_hold_pos"],
            plans["pos_lr_neg"],
            0.45,
            4.20,
            repeats,
        ),
        CasePlan(
            "script_then_position_same_short",
            "script_position_same_channel",
            plans["eyes_short_hold_pos"],
            plans["pos_lr_neg"],
            0.12,
            2.20,
            repeats,
        ),
        CasePlan(
            "script_then_position_same_script_other_channel",
            "script_position_same_script_other_channel",
            plans["eyes_long_hold_pos"],
            plans["pos_ud_pos"],
            0.45,
            4.20,
            repeats,
        ),
        CasePlan(
            "script_then_position_disjoint_lid",
            "script_position_disjoint_channel",
            plans["eyes_long_hold_pos"],
            plans["pos_lid_left_a"],
            0.45,
            4.20,
            repeats,
        ),
        CasePlan(
            "script_then_all_pose",
            "script_pose_all_channels",
            plans["eyes_long_hold_pos"],
            plans["pose_eye_neg_ud_pos"],
            0.45,
            4.20,
            repeats,
        ),
    ]
    if suite == "quick":
        return [base_cases[index] for index in (0, 2, 3, 7, 11)]
    return base_cases


def value_at_or_before(samples: list[tuple[float, int]], at_s: float) -> int | None:
    previous: int | None = None
    for time_s, value in samples:
        if time_s > at_s:
            break
        previous = value
    return previous


def detect_effect_time(
    samples: list[tuple[float, int]],
    *,
    target: int,
    search_start_s: float,
    baseline_time_s: float,
    min_delta: int = 8,
    sustain_samples: int = 3,
    sustain_window_s: float = 0.06,
) -> tuple[float | None, int | None]:
    baseline = value_at_or_before(samples, baseline_time_s)
    if baseline is None:
        baseline = value_at_or_before(samples, search_start_s)
    if baseline is None:
        return None, None

    delta = target - baseline
    if abs(delta) < min_delta:
        return None, baseline

    direction = 1 if delta > 0 else -1
    threshold = baseline + direction * max(min_delta, abs(delta) * 0.25)
    for index, (time_s, value) in enumerate(samples):
        if time_s < search_start_s:
            continue

        crossed = value >= threshold if direction > 0 else value <= threshold
        if not crossed:
            continue

        sustained = 0
        for check_time_s, check_value in samples[index:]:
            if check_time_s > time_s + sustain_window_s:
                break
            if direction > 0 and check_value >= threshold:
                sustained += 1
            elif direction < 0 and check_value <= threshold:
                sustained += 1
            if sustained >= sustain_samples:
                return time_s, baseline
    return None, baseline


def future_first_effect_time(
    first: MotionPlan,
    samples_by_channel: dict[str, list[tuple[float, int]]],
    *,
    channel: str,
    offset_s: float,
    second_effect_s: float | None,
) -> float | None:
    if channel not in first.channels:
        return None
    search_floor = offset_s
    if second_effect_s is not None:
        search_floor = max(search_floor, second_effect_s + 0.05)

    hits: list[float] = []
    for event_s, targets in first.target_events():
        if event_s <= search_floor + 0.05 or channel not in targets:
            continue
        samples = samples_by_channel.get(channel, [])
        effect_s, _baseline = detect_effect_time(
            samples,
            target=targets[channel],
            search_start_s=event_s + 0.02,
            baseline_time_s=event_s,
        )
        if effect_s is not None:
            hits.append(effect_s)
    return min(hits) if hits else None


def classify_case(case: CasePlan, channel_summaries: tuple[ChannelSummary, ...]) -> tuple[str, str]:
    first_end_s = case.first.script_duration_s
    second_effects = [item.second_effect_s for item in channel_summaries if item.second_effect_s is not None]
    future_hits = [item.first_future_effect_s for item in channel_summaries if item.first_future_effect_s is not None]
    early_effects = [effect for effect in second_effects if effect < first_end_s - 0.08]

    if not second_effects:
        return "no_visible_second_effect", "No watched channel crossed toward the second command target."

    first_effect_s = min(second_effects)
    if first_effect_s >= first_end_s - 0.08:
        return "after_first_expected_end", "The second command effect begins at or after the first script expected end."

    if "disjoint" in case.category:
        if future_hits:
            return "parallel_or_channel_merge", "The second command affects its channel before first end, while a later first-script target is still visible."
        return "early_second_effect_first_continuation_unclear", "The second command affects its channel before first end; first-script continuation was not detected."

    if future_hits:
        return "transient_override_first_continues", "The second command affects the channel early, then a later first-script target is visible."

    if early_effects:
        return "replace_abort_or_channel_override", "The second command affects the channel before first end and no later first-script target was detected."

    return "unclassified", "The effects did not match a simple queue/replace/ignore pattern."


class InteractionRunner:
    def __init__(self, port_name: str, baudrate: int, output_dir: Path, verbose: bool = False) -> None:
        self.robot = RobotMotion(port_name, baudrate)
        self.output_dir = output_dir
        self.verbose = verbose
        self.decoder = FeedbackDecoder()
        self.started_at = 0.0
        self.current_case = ""
        self.current_repeat = 0
        self.case_start_s = 0.0
        self.tx_events: list[TxEvent] = []
        self.samples: list[Sample] = []
        self.summaries: list[CaseSummary] = []

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
        for frame in self.decoder.feed(data, now_s):
            rel_s = now_s - self.case_start_s
            for channel, value in zip(CHANNEL_ORDER, frame.values):
                self.samples.append(
                    Sample(
                        case=self.current_case,
                        repeat=self.current_repeat,
                        time_s=rel_s,
                        channel=channel,
                        value=value,
                        angle=CHANNELS[channel].angle_from_byte(value),
                    )
                )

    def pump_until(self, deadline_abs_s: float) -> None:
        while self.now_s() < deadline_abs_s:
            self.read_available()
            time.sleep(0.004)
        self.read_available()

    def pump_for(self, duration_s: float) -> None:
        self.pump_until(self.now_s() + max(0.0, duration_s))

    def send(self, command: Command, *, case: str, repeat: int, role: str) -> float:
        self.read_available()
        tx = packet(command.payload)
        tx_time_s = self.now_s()
        self.port.write(tx)
        self.port.flush()
        self.tx_events.append(TxEvent(case, repeat, role, command.name, tx_time_s - self.case_start_s, format_hex(tx)))
        if self.verbose:
            print(f"TX {case}#{repeat} {role} +{tx_time_s - self.case_start_s:.3f}s {command.name}: {format_hex(tx)}", flush=True)
        return tx_time_s

    def reset_to_neutral(self, settle_s: float) -> None:
        self.current_case = "reset"
        self.current_repeat = 0
        self.case_start_s = self.now_s()
        self.send(STARTUP_COMMANDS[0], case="reset", repeat=0, role="power_on")
        self.pump_for(STARTUP_COMMANDS[0].delay_after)
        self.send(pose_command(NEUTRAL_TARGETS), case="reset", repeat=0, role="neutral")
        self.pump_for(settle_s)

    def run_case(self, case: CasePlan, repeat: int, settle_s: float) -> None:
        self.reset_to_neutral(settle_s)
        self.current_case = case.name
        self.current_repeat = repeat
        self.case_start_s = self.now_s()
        self.decoder = FeedbackDecoder()

        print(f"case {case.name} repeat {repeat}/{case.repeats}", flush=True)
        self.send(case.first.command, case=case.name, repeat=repeat, role="first")
        self.pump_until(self.case_start_s + case.offset_s)
        second_tx_abs_s = self.send(case.second.command, case=case.name, repeat=repeat, role="second")
        self.pump_until(self.case_start_s + case.observe_s)
        self.summaries.append(self.analyze_case(case, repeat, second_tx_abs_s - self.case_start_s))

    def run_baseline(self, plan: MotionPlan, settle_s: float, observe_s: float) -> None:
        self.reset_to_neutral(settle_s)
        self.current_case = f"baseline_{plan.name}"
        self.current_repeat = 1
        self.case_start_s = self.now_s()
        self.decoder = FeedbackDecoder()
        print(f"baseline {plan.name}", flush=True)
        self.send(plan.command, case=self.current_case, repeat=1, role="baseline")
        self.pump_until(self.case_start_s + observe_s)

    def samples_by_channel(self, case_name: str, repeat: int) -> dict[str, list[tuple[float, int]]]:
        result: dict[str, list[tuple[float, int]]] = {channel: [] for channel in CHANNEL_ORDER}
        for sample in self.samples:
            if sample.case == case_name and sample.repeat == repeat:
                result[sample.channel].append((sample.time_s, sample.value))
        return result

    def analyze_case(self, case: CasePlan, repeat: int, second_tx_s: float) -> CaseSummary:
        samples_by_channel = self.samples_by_channel(case.name, repeat)
        channels = tuple(dict.fromkeys((*case.first.channels, *case.second.channels)))
        channel_summaries: list[ChannelSummary] = []

        for channel in channels:
            samples = samples_by_channel.get(channel, [])
            second_target = case.second.first_targets.get(channel)
            second_effect_s: float | None = None
            value_at_second: int | None = None
            if second_target is not None:
                second_effect_s, value_at_second = detect_effect_time(
                    samples,
                    target=second_target,
                    search_start_s=second_tx_s + 0.02,
                    baseline_time_s=second_tx_s,
                )
            first_future_s = future_first_effect_time(
                case.first,
                samples_by_channel,
                channel=channel,
                offset_s=case.offset_s,
                second_effect_s=second_effect_s,
            )
            channel_summaries.append(
                ChannelSummary(
                    channel=channel,
                    second_target=second_target,
                    value_at_second=value_at_second,
                    second_effect_s=second_effect_s,
                    first_future_effect_s=first_future_s,
                )
            )

        summary_channels = tuple(channel_summaries)
        classification, notes = classify_case(case, summary_channels)
        return CaseSummary(
            case=case.name,
            repeat=repeat,
            category=case.category,
            first=case.first.name,
            second=case.second.name,
            offset_s=case.offset_s,
            first_expected_end_s=case.first.script_duration_s,
            classification=classification,
            notes=notes,
            channels=summary_channels,
        )

    def write_outputs(self, cases: list[CasePlan], args: argparse.Namespace) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with (self.output_dir / "tx_events.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("case", "repeat", "role", "name", "time_s", "payload_hex"))
            for event in self.tx_events:
                writer.writerow((event.case, event.repeat, event.role, event.name, f"{event.time_s:.6f}", event.payload_hex))

        with (self.output_dir / "samples.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("case", "repeat", "time_s", "channel", "value", "angle"))
            for sample in self.samples:
                writer.writerow((sample.case, sample.repeat, f"{sample.time_s:.6f}", sample.channel, sample.value, f"{sample.angle:.3f}"))

        with (self.output_dir / "summary.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                (
                    "case",
                    "repeat",
                    "category",
                    "first",
                    "second",
                    "offset_s",
                    "first_expected_end_s",
                    "classification",
                    "notes",
                    "channel",
                    "second_target",
                    "value_at_second",
                    "second_effect_s",
                    "first_future_effect_s",
                )
            )
            for summary in self.summaries:
                for channel in summary.channels:
                    writer.writerow(
                        (
                            summary.case,
                            summary.repeat,
                            summary.category,
                            summary.first,
                            summary.second,
                            f"{summary.offset_s:.6f}",
                            f"{summary.first_expected_end_s:.6f}",
                            summary.classification,
                            summary.notes,
                            channel.channel,
                            "" if channel.second_target is None else channel.second_target,
                            "" if channel.value_at_second is None else channel.value_at_second,
                            "" if channel.second_effect_s is None else f"{channel.second_effect_s:.6f}",
                            "" if channel.first_future_effect_s is None else f"{channel.first_future_effect_s:.6f}",
                        )
                    )

        payload = {
            "args": vars(args),
            "neutral_targets": dict(NEUTRAL_BY_CHANNEL),
            "cases": [
                {
                    "name": case.name,
                    "category": case.category,
                    "first": case.first.name,
                    "second": case.second.name,
                    "offset_s": case.offset_s,
                    "observe_s": case.observe_s,
                    "repeats": case.repeats,
                }
                for case in cases
            ],
            "summaries": [
                {
                    "case": summary.case,
                    "repeat": summary.repeat,
                    "category": summary.category,
                    "classification": summary.classification,
                    "notes": summary.notes,
                    "channels": [channel.__dict__ for channel in summary.channels],
                }
                for summary in self.summaries
            ],
        }
        with (self.output_dir / "results.json").open("w") as handle:
            json.dump(payload, handle, indent=2)

    def print_summary(self) -> None:
        grouped: dict[str, list[CaseSummary]] = {}
        for summary in self.summaries:
            grouped.setdefault(summary.case, []).append(summary)
        print("\nsummary:", flush=True)
        for case_name, summaries in grouped.items():
            classes = sorted({summary.classification for summary in summaries})
            print(f"{case_name}: {', '.join(classes)}", flush=True)


def unique_plans(cases: list[CasePlan]) -> list[MotionPlan]:
    seen: set[str] = set()
    plans: list[MotionPlan] = []
    for case in cases:
        for plan in (case.first, case.second):
            if plan.name in seen:
                continue
            seen.add(plan.name)
            plans.append(plan)
    return plans


def print_dry_run(cases: list[CasePlan]) -> None:
    for case in cases:
        print(
            f"{case.name}: {case.first.name} -> {case.second.name} "
            f"offset={case.offset_s:.3f}s observe={case.observe_s:.3f}s repeats={case.repeats}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe motorboard behavior when a second motion command is sent while a first script is active."
    )
    parser.add_argument("--port", default="/dev/ttyAMA0")
    parser.add_argument("--baudrate", type=int, default=BAUDRATE)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--suite", choices=("quick", "full"), default="full")
    parser.add_argument("--case-filter", default=None, help="Only run cases whose name or category contains this text.")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--settle-s", type=float, default=0.80)
    parser.add_argument("--skip-baselines", action="store_true")
    parser.add_argument("--power-off", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.repeats < 1:
        print("--repeats must be >= 1")
        return 2
    if args.settle_s < 0:
        print("--settle-s must be >= 0")
        return 2

    cases = build_cases(args.repeats, args.suite)
    if args.case_filter is not None:
        cases = [
            case for case in cases
            if args.case_filter in case.name or args.case_filter in case.category
        ]
        if not cases:
            print(f"--case-filter {args.case_filter!r} matched no cases")
            return 2
    if args.dry_run:
        print_dry_run(cases)
        return 0

    output_dir = Path(args.output_dir) if args.output_dir else Path("motion_interaction_results") / time.strftime("%Y%m%d-%H%M%S")
    runner = InteractionRunner(args.port, args.baudrate, output_dir, verbose=args.verbose)
    runner.open()
    try:
        if not args.skip_baselines:
            for plan in unique_plans(cases):
                observe_s = max(1.20, plan.script_duration_s + 0.80)
                runner.run_baseline(plan, args.settle_s, observe_s)
        for case in cases:
            for repeat in range(1, case.repeats + 1):
                runner.run_case(case, repeat, args.settle_s)
        if args.power_off:
            runner.current_case = "power_off"
            runner.current_repeat = 0
            runner.case_start_s = runner.now_s()
            runner.send(POWER_OFF, case="power_off", repeat=0, role="power_off")
            runner.pump_for(POWER_OFF.delay_after)
    finally:
        runner.write_outputs(cases, args)
        runner.print_summary()
        runner.close()

    print(f"wrote {output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
