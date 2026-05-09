#!/usr/bin/env python3

from __future__ import annotations

import argparse
import configparser
import math
import statistics
import zipfile
from dataclasses import dataclass
from pathlib import Path


COMMON_BAUDRATES = [
    300,
    600,
    1200,
    1800,
    2400,
    4800,
    7200,
    9600,
    14400,
    19200,
    28800,
    31250,
    38400,
    56000,
    57600,
    74880,
    76800,
    115200,
    128000,
    153600,
    230400,
    250000,
    256000,
    460800,
    500000,
    576000,
    921600,
    1000000,
    1500000,
    2000000,
    3000000,
]

COMMON_MODES = [
    (7, "none", 1.0),
    (7, "even", 1.0),
    (7, "odd", 1.0),
    (8, "none", 1.0),
    (8, "even", 1.0),
    (8, "odd", 1.0),
    (8, "none", 2.0),
    (8, "even", 2.0),
    (8, "odd", 2.0),
    (9, "none", 1.0),
]

FULL_MODES = [
    (data_bits, parity, stop_bits)
    for data_bits in range(5, 10)
    for parity in ("none", "even", "odd")
    for stop_bits in (1.0, 2.0)
]

PARITY_LETTER = {"none": "N", "even": "E", "odd": "O"}


@dataclass(frozen=True)
class ChannelInfo:
    index: int
    probe_number: int
    label: str
    bitmask: int

    @property
    def short_name(self) -> str:
        return f"D{self.index}"

    @property
    def display_name(self) -> str:
        if self.label == self.short_name:
            return f"{self.short_name} (probe {self.probe_number})"
        return f"{self.label} / {self.short_name} (probe {self.probe_number})"


@dataclass
class ChannelTrace:
    info: ChannelInfo
    transitions: list[int]
    levels_after: list[int]
    run_lengths: list[int]
    high_samples: int
    total_samples: int

    @property
    def edge_count(self) -> int:
        return len(self.transitions)

    @property
    def low_samples(self) -> int:
        return self.total_samples - self.high_samples


@dataclass
class DecodeGuess:
    channel: ChannelInfo
    baudrate: int
    data_bits: int
    parity: str
    stop_bits: float
    inverted: bool
    good_frames: int
    frame_errors: int
    parity_errors: int
    printable_ratio: float
    stability: float
    edge_density: float
    score: float
    sample_bytes: list[int]

    @property
    def mode_text(self) -> str:
        stop_text = str(int(self.stop_bits)) if self.stop_bits.is_integer() else str(self.stop_bits)
        return f"{self.data_bits}{PARITY_LETTER[self.parity]}{stop_text}"

    @property
    def invert_text(self) -> str:
        return "inverted" if self.inverted else "normal"

    @property
    def preview_ascii(self) -> str:
        parts: list[str] = []
        for value in self.sample_bytes[:24]:
            if value == 0x0A:
                parts.append("\\n")
            elif value == 0x0D:
                parts.append("\\r")
            elif value == 0x09:
                parts.append("\\t")
            elif 32 <= value <= 126 and value != 0x5C:
                parts.append(chr(value))
            elif value == 0x5C:
                parts.append("\\\\")
            else:
                parts.append(".")
        return "".join(parts)

    @property
    def preview_hex(self) -> str:
        return " ".join(f"{value:02X}" for value in self.sample_bytes[:16])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Guess UART baudrate and mode from a sigrok .sr capture."
    )
    parser.add_argument("input_sr", type=Path, help="Input .sr file")
    parser.add_argument(
        "--channels",
        help=(
            "Comma-separated channel labels or D-names to inspect, e.g. RX,TX or D5,D7. "
            "Default: auto-pick the most relevant channels."
        ),
    )
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="How many guesses per channel to print (default: 5)",
    )
    parser.add_argument(
        "--max-starts",
        type=int,
        default=300,
        help="Max start-bit candidates to evaluate per guess (default: 300)",
    )
    parser.add_argument(
        "--max-good-frames",
        type=int,
        default=160,
        help="Stop scoring a guess after this many valid frames (default: 160)",
    )
    parser.add_argument(
        "--min-baud",
        type=int,
        default=1200,
        help="Lowest baudrate to try (default: 1200)",
    )
    parser.add_argument(
        "--max-baud",
        type=int,
        default=1000000,
        help="Highest baudrate to try (default: 1000000)",
    )
    parser.add_argument(
        "--exhaustive",
        action="store_true",
        help="Try more UART modes than the common default grid",
    )
    return parser.parse_args()


def parse_samplerate(text: str) -> int:
    cleaned = text.strip().lower()
    parts = cleaned.split()
    if len(parts) == 2:
        value = float(parts[0])
        unit = parts[1]
    else:
        value = float(parts[0])
        unit = "hz"

    if unit in {"hz", "sps"}:
        scale = 1
    elif unit in {"khz", "ksps"}:
        scale = 1_000
    elif unit in {"mhz", "msps"}:
        scale = 1_000_000
    elif unit in {"ghz", "gsps"}:
        scale = 1_000_000_000
    else:
        raise ValueError(f"Unknown samplerate unit: {text!r}")
    return int(value * scale)


def load_capture(path: Path) -> tuple[int, list[ChannelInfo], bytes]:
    parser = configparser.ConfigParser()
    parser.optionxform = str

    with zipfile.ZipFile(path) as archive:
        metadata = archive.read("metadata").decode("utf-8", "replace")
        parser.read_string(metadata)
        device = parser["device 1"]
        samplerate_hz = parse_samplerate(device["samplerate"])
        total_probes = int(device["total probes"])
        unitsize = int(device.get("unitsize", "1"))
        if unitsize != 1:
            raise ValueError(f"Only unitsize=1 is supported, got {unitsize}.")

        channels: list[ChannelInfo] = []
        for probe_number in range(1, total_probes + 1):
            label = device.get(f"probe{probe_number}", f"D{probe_number - 1}")
            index = probe_number - 1
            channels.append(
                ChannelInfo(
                    index=index,
                    probe_number=probe_number,
                    label=label,
                    bitmask=1 << index,
                )
            )

        logic_chunks = sorted(
            (
                name
                for name in archive.namelist()
                if name.startswith("logic-1-")
            ),
            key=lambda name: int(name.rsplit("-", 1)[1]),
        )
        raw = b"".join(archive.read(name) for name in logic_chunks)

    return samplerate_hz, channels, raw


def build_channel_traces(raw: bytes, channels: list[ChannelInfo]) -> list[ChannelTrace]:
    if not raw:
        raise ValueError("Capture file contains no samples.")

    total_samples = len(raw)
    transitions: list[list[int]] = [[] for _ in channels]
    levels_after: list[list[int]] = [[] for _ in channels]
    run_lengths: list[list[int]] = [[] for _ in channels]
    high_counts = [0 for _ in channels]

    first = raw[0]
    last_edge = [0 for _ in channels]
    prev_levels = [1 if first & channel.bitmask else 0 for channel in channels]
    for idx, level in enumerate(prev_levels):
        high_counts[idx] += level

    for sample_index, value in enumerate(raw[1:], start=1):
        changed = value ^ raw[sample_index - 1]
        for channel_index, channel in enumerate(channels):
            level = 1 if value & channel.bitmask else 0
            high_counts[channel_index] += level
            if changed & channel.bitmask:
                transitions[channel_index].append(sample_index)
                levels_after[channel_index].append(level)
                run_lengths[channel_index].append(sample_index - last_edge[channel_index])
                last_edge[channel_index] = sample_index
                prev_levels[channel_index] = level

    for channel_index, _ in enumerate(channels):
        run_lengths[channel_index].append(total_samples - last_edge[channel_index])

    return [
        ChannelTrace(
            info=channel,
            transitions=transitions[idx],
            levels_after=levels_after[idx],
            run_lengths=run_lengths[idx],
            high_samples=high_counts[idx],
            total_samples=total_samples,
        )
        for idx, channel in enumerate(channels)
    ]


def resolve_channels(
    traces: list[ChannelTrace],
    selectors: str | None,
) -> list[ChannelTrace]:
    if selectors:
        wanted = {item.strip().lower() for item in selectors.split(",") if item.strip()}
        selected = [
            trace
            for trace in traces
            if trace.info.label.lower() in wanted or trace.info.short_name.lower() in wanted
        ]
        if not selected:
            raise ValueError(f"No matching channels found for {selectors!r}.")
        return selected

    rx_like = [
        trace for trace in traces if trace.info.label.lower() in {"rx", "tx"}
    ]
    if rx_like:
        return sorted(rx_like, key=lambda trace: trace.info.label)

    active = [trace for trace in traces if trace.edge_count > 8]
    active.sort(key=lambda trace: trace.edge_count, reverse=True)
    return active[:2] if active else traces[:2]


def sample_channel(raw: bytes, bitmask: int, sample_index: int, inverted: bool) -> int:
    physical = 1 if raw[sample_index] & bitmask else 0
    return physical ^ int(inverted)


def parity_ok(parity: str, data: int, parity_bit: int) -> bool:
    ones = bin(data).count("1")
    if parity == "even":
        return ((ones + parity_bit) % 2) == 0
    if parity == "odd":
        return ((ones + parity_bit) % 2) == 1
    return True


def format_stop_bits(stop_bits: float) -> str:
    return str(int(stop_bits)) if stop_bits.is_integer() else str(stop_bits)


def run_fit_score(trace: ChannelTrace, bit_width: float) -> float:
    if bit_width <= 0:
        return 0.0

    tolerance = 0.35
    considered = 0.0
    matched = 0.0

    for run_length in trace.run_lengths:
        if run_length < 2 or run_length > int(bit_width * 20):
            continue
        multiple = max(1, round(run_length / bit_width))
        error = abs(run_length - multiple * bit_width) / bit_width
        weight = 1.0 / (1.0 + 0.20 * (multiple - 1))
        considered += weight
        matched += weight * max(0.0, 1.0 - (error / tolerance))

    if considered == 0.0:
        return 0.0
    return matched / considered


def mode_prior_score(data_bits: int, parity: str, stop_bits: float) -> float:
    if data_bits == 8 and parity == "none" and stop_bits == 1.0:
        return 8.0
    if data_bits == 7 and parity in {"even", "odd"} and stop_bits == 1.0:
        return 4.0
    if data_bits == 8 and parity in {"even", "odd"} and stop_bits == 1.0:
        return 3.0
    if data_bits == 8 and parity == "none" and stop_bits == 2.0:
        return 2.0
    if data_bits == 7 and parity == "none" and stop_bits == 1.0:
        return 1.0
    return 0.0


def score_guess(
    raw: bytes,
    samplerate_hz: int,
    trace: ChannelTrace,
    baudrate: int,
    data_bits: int,
    parity: str,
    stop_bits: float,
    inverted: bool,
    max_starts: int,
    max_good_frames: int,
) -> DecodeGuess | None:
    bit_width = samplerate_hz / baudrate
    if bit_width < 2.0:
        return None

    physical_idle = 1 ^ int(inverted)
    physical_start = 0 ^ int(inverted)

    good_frames = 0
    frame_errors = 0
    parity_errors = 0
    printable_hits = 0
    stability_values: list[float] = []
    sample_bytes: list[int] = []
    first_good_sample: int | None = None
    last_good_sample: int | None = None

    prev_level = 1 if raw[0] & trace.info.bitmask else 0
    next_allowed_start = 0
    starts_considered = 0
    parity_bits = 0 if parity == "none" else 1
    guard_samples = int(max(1.0, (1 + data_bits + parity_bits + stop_bits - 0.2) * bit_width))
    neighborhood = max(1, int(bit_width * 0.2))

    for edge_index, new_level in zip(trace.transitions, trace.levels_after):
        old_level = prev_level
        prev_level = new_level

        if starts_considered >= max_starts or good_frames >= max_good_frames:
            break
        if edge_index < next_allowed_start:
            continue
        if old_level != physical_idle or new_level != physical_start:
            continue

        starts_considered += 1

        def logical_bit(offset_bits: float) -> int | None:
            sample_pos = edge_index + offset_bits * bit_width
            sample_index = int(round(sample_pos))
            if sample_index < 0 or sample_index >= len(raw):
                return None
            return sample_channel(raw, trace.info.bitmask, sample_index, inverted)

        def bit_stability(offset_bits: float, expected: int) -> float:
            sample_pos = edge_index + offset_bits * bit_width
            center = int(round(sample_pos))
            if center <= 0 or center >= len(raw) - 1:
                return 0.0
            positions = [
                max(0, min(len(raw) - 1, center - neighborhood)),
                center,
                max(0, min(len(raw) - 1, center + neighborhood)),
            ]
            matches = sum(
                1
                for pos in positions
                if sample_channel(raw, trace.info.bitmask, pos, inverted) == expected
            )
            return matches / len(positions)

        start_bit = logical_bit(0.5)
        if start_bit != 0:
            frame_errors += 1
            next_allowed_start = edge_index + max(1, int(bit_width * 0.8))
            continue

        value = 0
        data_stability = []
        failed = False
        for bit_index in range(data_bits):
            bit_value = logical_bit(1.5 + bit_index)
            if bit_value is None:
                failed = True
                break
            value |= bit_value << bit_index
            data_stability.append(bit_stability(1.5 + bit_index, bit_value))

        if failed:
            break

        if parity_bits:
            parity_bit = logical_bit(1.5 + data_bits)
            if parity_bit is None:
                break
            if not parity_ok(parity, value, parity_bit):
                parity_errors += 1
                next_allowed_start = edge_index + guard_samples
                continue

        stop_center = 1.5 + data_bits + parity_bits
        stop_bit = logical_bit(stop_center)
        if stop_bit != 1:
            frame_errors += 1
            next_allowed_start = edge_index + guard_samples
            continue

        if stop_bits >= 2.0:
            second_stop = logical_bit(stop_center + 1.0)
            if second_stop != 1:
                frame_errors += 1
                next_allowed_start = edge_index + guard_samples
                continue

        good_frames += 1
        sample_bytes.append(value)
        if first_good_sample is None:
            first_good_sample = edge_index
        last_good_sample = edge_index
        if value in (0x09, 0x0A, 0x0D) or 32 <= value <= 126:
            printable_hits += 1

        frame_stability = [bit_stability(0.5, 0), *data_stability, bit_stability(stop_center, 1)]
        stability_values.append(sum(frame_stability) / len(frame_stability))
        next_allowed_start = edge_index + guard_samples

    if starts_considered == 0:
        return None

    stability = statistics.fmean(stability_values) if stability_values else 0.0
    printable_ratio = printable_hits / good_frames if good_frames else 0.0
    attempts = good_frames + frame_errors + parity_errors
    success_ratio = good_frames / attempts if attempts else 0.0
    edge_density = trace.edge_count / trace.total_samples if trace.total_samples else 0.0
    fit_score = run_fit_score(trace, bit_width)
    if first_good_sample is not None and last_good_sample is not None and last_good_sample > first_good_sample:
        sample_span = last_good_sample - first_good_sample
        frame_density = good_frames / sample_span * samplerate_hz
    else:
        frame_density = 0.0
    density_bonus = min(frame_density / max(baudrate / 12.0, 1.0), 1.0)

    score = (
        good_frames * 4.0
        + success_ratio * 50.0
        + stability * 25.0
        + fit_score * 80.0
        + density_bonus * 20.0
        + printable_ratio * 5.0
        + mode_prior_score(data_bits, parity, stop_bits)
        - frame_errors * 3.5
        - parity_errors * 2.0
    )

    return DecodeGuess(
        channel=trace.info,
        baudrate=baudrate,
        data_bits=data_bits,
        parity=parity,
        stop_bits=stop_bits,
        inverted=inverted,
        good_frames=good_frames,
        frame_errors=frame_errors,
        parity_errors=parity_errors,
        printable_ratio=printable_ratio,
        stability=stability,
        edge_density=edge_density,
        score=score,
        sample_bytes=sample_bytes,
    )


def rank_channel_guesses(
    raw: bytes,
    samplerate_hz: int,
    trace: ChannelTrace,
    baudrates: list[int],
    modes: list[tuple[int, str, float]],
    max_starts: int,
    max_good_frames: int,
) -> list[DecodeGuess]:
    guesses: list[DecodeGuess] = []
    for baudrate in baudrates:
        for data_bits, parity, stop_bits in modes:
            for inverted in (False, True):
                guess = score_guess(
                    raw=raw,
                    samplerate_hz=samplerate_hz,
                    trace=trace,
                    baudrate=baudrate,
                    data_bits=data_bits,
                    parity=parity,
                    stop_bits=stop_bits,
                    inverted=inverted,
                    max_starts=max_starts,
                    max_good_frames=max_good_frames,
                )
                if guess and guess.good_frames > 0:
                    guesses.append(guess)

    guesses.sort(
        key=lambda guess: (
            guess.score,
            guess.good_frames,
            -guess.frame_errors,
            -guess.parity_errors,
            guess.stability,
        ),
        reverse=True,
    )
    return guesses


def print_capture_summary(path: Path, samplerate_hz: int, traces: list[ChannelTrace]) -> None:
    print(f"Fil: {path}")
    print(f"Samplerate: {samplerate_hz} Hz")
    print("Channels:")
    for trace in traces:
        high_ratio = trace.high_samples / trace.total_samples if trace.total_samples else 0.0
        print(
            f"  - {trace.info.display_name}: "
            f"{trace.edge_count} edges, idle~{'high' if high_ratio >= 0.5 else 'low'}"
        )


def print_ranked_guesses(trace: ChannelTrace, guesses: list[DecodeGuess], top: int) -> None:
    print()
    print(f"Channel {trace.info.display_name}")
    if not guesses:
        print("  No plausible UART candidates found.")
        return

    best = guesses[0]
    print(
        f"  Best guess: {best.baudrate} {best.mode_text} "
        f"({best.invert_text}), score={best.score:.1f}"
    )

    for idx, guess in enumerate(guesses[:top], start=1):
        print(
            f"  {idx}. {guess.baudrate:>7} {guess.mode_text:<4} {guess.invert_text:<8} "
            f"good={guess.good_frames:<4} frame_err={guess.frame_errors:<4} "
            f"parity_err={guess.parity_errors:<4} stability={guess.stability:.2f} "
            f"ascii={guess.preview_ascii!r} hex={guess.preview_hex}"
        )


def main() -> int:
    args = parse_args()
    input_sr = args.input_sr.expanduser().resolve()
    samplerate_hz, channels, raw = load_capture(input_sr)
    traces = build_channel_traces(raw, channels)
    selected = resolve_channels(traces, args.channels)

    baudrates = [
        baudrate
        for baudrate in COMMON_BAUDRATES
        if args.min_baud <= baudrate <= args.max_baud and samplerate_hz / baudrate >= 2.0
    ]
    if not baudrates:
        raise ValueError("No baud rates remain after the selected min/max range.")
    modes = FULL_MODES if args.exhaustive else COMMON_MODES

    print_capture_summary(input_sr, samplerate_hz, selected)

    for trace in selected:
        guesses = rank_channel_guesses(
            raw=raw,
            samplerate_hz=samplerate_hz,
            trace=trace,
            baudrates=baudrates,
            modes=modes,
            max_starts=args.max_starts,
            max_good_frames=args.max_good_frames,
        )
        print_ranked_guesses(trace, guesses, args.top)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
