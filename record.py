#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COMMON_FX2_SAMPLE_RATES = [
    20_000,
    25_000,
    50_000,
    100_000,
    200_000,
    250_000,
    500_000,
    1_000_000,
    2_000_000,
    3_000_000,
    4_000_000,
    6_000_000,
    8_000_000,
    12_000_000,
    16_000_000,
    24_000_000,
]

PARITY_MAP = {
    "N": "none",
    "E": "even",
    "O": "odd",
    "M": "one",
    "S": "zero",
}


@dataclass(frozen=True)
class UartMode:
    data_bits: int
    parity_letter: str
    parity_name: str
    stop_bits_text: str
    stop_bits_value: float

    @property
    def bits_per_symbol(self) -> float:
        parity_bits = 0 if self.parity_name == "none" else 1
        return 1 + self.data_bits + parity_bits + self.stop_bits_value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture UART RX/TX on an FX2LA with sigrok-cli and export "
            "sigrok JSON trace plus sequence grouping."
        )
    )
    parser.add_argument("baudrate", type=int, help="UART baudrate, e.g. 115200")
    parser.add_argument("mode", help="UART mode, e.g. 8N1, 7E1, 8N2")
    parser.add_argument(
        "idle_gap_chars",
        type=float,
        help="Start a new sequence after this much silence, measured in character times",
    )
    parser.add_argument(
        "min_bytes",
        type=int,
        help="Discard sequences smaller than this many decoded bytes",
    )
    parser.add_argument("output_json", type=Path, help="Output JSON file")
    parser.add_argument(
        "--driver",
        default="fx2lafw",
        help="sigrok driver to use for capture (default: fx2lafw)",
    )
    parser.add_argument(
        "--samplerate",
        help="Capture samplerate for sigrok-cli, e.g. 4m, 8m, 12000000. Default: auto",
    )
    parser.add_argument(
        "--capture-time",
        help="Optional capture duration understood by sigrok-cli, e.g. 5s or 2500",
    )
    parser.add_argument(
        "--rx-channel",
        default="0",
        help="Capture RX on this logic channel (default: 0)",
    )
    parser.add_argument(
        "--tx-channel",
        default="1",
        help="Capture TX on this logic channel (default: 1)",
    )
    parser.add_argument(
        "--decode-rx-name",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--decode-tx-name",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--input-format",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--invert-rx",
        action="store_true",
        help="Invert RX before UART decode",
    )
    parser.add_argument(
        "--invert-tx",
        action="store_true",
        help="Invert TX before UART decode",
    )
    parser.add_argument(
        "--keep-sr",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep the raw sigrok session sidecar (.sr). Default: yes",
    )
    parser.add_argument(
        "--group-sequences",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Include grouped RX/TX sequences in JSON. Default: no",
    )
    parser.add_argument(
        "--prompt-names",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Prompt for a friendly name per sequence after capture. Requires --group-sequences. Default: no",
    )
    parser.add_argument(
        "--include-trace-events",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Include raw sigrok traceEvents in JSON. Default: no",
    )
    return parser.parse_args()


def parse_uart_mode(text: str) -> UartMode:
    text = text.strip().upper()
    if len(text) < 3:
        raise ValueError(f"Ogiltigt UART-läge: {text!r}")

    data_bits_text = text[0]
    parity_letter = text[1]
    stop_bits_text = text[2:]

    if not data_bits_text.isdigit():
        raise ValueError(f"Ogiltigt antal databitar i UART-läge: {text!r}")
    if parity_letter not in PARITY_MAP:
        raise ValueError(
            f"Ogiltig paritet i UART-läge: {text!r}. Använd N, E, O, M eller S."
        )

    try:
        data_bits = int(data_bits_text)
    except ValueError as exc:
        raise ValueError(f"Ogiltigt UART-läge: {text!r}") from exc

    if data_bits < 5 or data_bits > 9:
        raise ValueError(
            f"Ogiltigt antal databitar i UART-läge: {text!r}. Stöd: 5-9."
        )

    try:
        stop_bits_value = float(stop_bits_text)
    except ValueError as exc:
        raise ValueError(
            f"Ogiltigt antal stoppbitar i UART-läge: {text!r}"
        ) from exc

    return UartMode(
        data_bits=data_bits,
        parity_letter=parity_letter,
        parity_name=PARITY_MAP[parity_letter],
        stop_bits_text=stop_bits_text,
        stop_bits_value=stop_bits_value,
    )


def samplerate_to_hz(text: str) -> int:
    cleaned = text.strip().lower()
    suffixes = {"k": 1_000, "m": 1_000_000, "g": 1_000_000_000}
    if cleaned[-1] in suffixes:
        return int(float(cleaned[:-1]) * suffixes[cleaned[-1]])
    return int(cleaned)


def hz_to_sigrok_rate(hz: int) -> str:
    for suffix, scale in (("g", 1_000_000_000), ("m", 1_000_000), ("k", 1_000)):
        if hz % scale == 0 and hz >= scale:
            return f"{hz // scale}{suffix}"
    return str(hz)


def capture_time_to_ms(text: str) -> str:
    cleaned = text.strip().lower()
    if cleaned.endswith("ms"):
        return str(int(float(cleaned[:-2])))
    if cleaned.endswith("s"):
        return str(int(float(cleaned[:-1]) * 1000))
    return str(int(float(cleaned)))


def choose_default_samplerate(baudrate: int) -> int:
    target = baudrate * 32
    for rate in COMMON_FX2_SAMPLE_RATES:
        if rate >= target:
            return rate
    return COMMON_FX2_SAMPLE_RATES[-1]


def ascii_preview(values: list[int]) -> str:
    parts: list[str] = []
    for value in values:
        if value == 0x0A:
            parts.append("\\n")
        elif value == 0x0D:
            parts.append("\\r")
        elif value == 0x09:
            parts.append("\\t")
        elif 32 <= value <= 126 and value not in (0x5C,):
            parts.append(chr(value))
        elif value == 0x5C:
            parts.append("\\\\")
        else:
            parts.append(f"\\x{value:02X}")
    return "".join(parts)


def hex_preview(values: list[int]) -> str:
    return " ".join(f"{value:02X}" for value in values)


def run_command(cmd: list[str], capture_stdout: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture_stdout else None,
        stderr=subprocess.PIPE,
    )


def ensure_sigrok_cli() -> None:
    if shutil.which("sigrok-cli") is None:
        raise SystemExit(
            "sigrok-cli hittades inte i PATH. Installera med: brew install sigrok-cli"
        )


def capture_to_sr(
    args: argparse.Namespace,
    sr_path: Path,
    samplerate_text: str,
) -> tuple[int, str]:
    channels = f"{args.rx_channel}=RX,{args.tx_channel}=TX"
    cmd = [
        "sigrok-cli",
        "--driver",
        args.driver,
        "--config",
        f"samplerate={samplerate_text}",
        "--channels",
        channels,
        "--output-file",
        str(sr_path),
    ]

    if args.capture_time:
        cmd.extend(["--time", capture_time_to_ms(args.capture_time)])
    else:
        cmd.append("--continuous")

    print(f"Samplar till {sr_path} med {samplerate_text}...", file=sys.stderr)
    if not args.capture_time:
        print("Tryck Ctrl-C när du vill stoppa inspelningen.", file=sys.stderr)

    proc = subprocess.Popen(
        cmd,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    interrupted = False
    try:
        returncode = proc.wait()
    except KeyboardInterrupt:
        interrupted = True
        proc.send_signal(signal.SIGINT)
        returncode = proc.wait()

    stderr_text = proc.stderr.read() if proc.stderr else ""
    if returncode not in (0, 130):
        if interrupted and sr_path.exists() and sr_path.stat().st_size > 0:
            return returncode, stderr_text
        raise RuntimeError(
            f"sigrok capture misslyckades med kod {returncode}.\n{stderr_text.strip()}"
        )

    if not sr_path.exists() or sr_path.stat().st_size == 0:
        raise RuntimeError("Ingen capture skapades. Kontrollera att logikanalysatorn hittas.")

    return returncode, stderr_text


def decode_trace(
    input_file: Path,
    decode_rx_name: str,
    decode_tx_name: str,
    baudrate: int,
    mode: UartMode,
    invert_rx: bool,
    invert_tx: bool,
    input_format: str | None = None,
) -> dict[str, Any]:
    rx_decoder = (
        f"uart:rx={decode_rx_name}:baudrate={baudrate}:data_bits={mode.data_bits}:"
        f"parity={mode.parity_name}:stop_bits={mode.stop_bits_value}:format=hex:"
        f"invert_rx={'yes' if invert_rx else 'no'}"
    )
    tx_decoder = (
        f"uart:tx={decode_tx_name}:baudrate={baudrate}:data_bits={mode.data_bits}:"
        f"parity={mode.parity_name}:stop_bits={mode.stop_bits_value}:format=hex:"
        f"invert_tx={'yes' if invert_tx else 'no'}"
    )
    cmd = [
        "sigrok-cli",
        "--input-file",
        str(input_file),
    ]
    if input_format:
        cmd.extend(["--input-format", input_format])
    cmd.extend(
        [
            "--protocol-decoders",
            rx_decoder,
            "--protocol-decoders",
            tx_decoder,
            "--protocol-decoder-annotations",
            "uart=rx-data:tx-data",
            "--protocol-decoder-jsontrace",
        ]
    )
    result = run_command(cmd, capture_stdout=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"sigrok decode misslyckades med kod {result.returncode}.\n"
            f"{result.stderr.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("sigrok returnerade ogiltig JSON vid decode.") from exc


def load_trace_from_import(
    args: argparse.Namespace,
    mode: UartMode,
) -> tuple[dict[str, Any], str, int, Path]:
    if args.input_file is None:
        raise RuntimeError("Intern fel: load_trace_from_import anropades utan input-file.")

    if not args.decode_rx_name or not args.decode_tx_name:
        raise RuntimeError(
            "--decode-rx-name och --decode-tx-name krävs när --input-file används."
        )

    samplerate_hz = samplerate_to_hz(args.samplerate) if args.samplerate else 0
    if samplerate_hz <= 0:
        raise RuntimeError(
            "När --input-file används måste --samplerate anges så att tider kan räknas ut."
        )

    trace = decode_trace(
        input_file=args.input_file,
        decode_rx_name=args.decode_rx_name,
        decode_tx_name=args.decode_tx_name,
        baudrate=args.baudrate,
        mode=mode,
        invert_rx=args.invert_rx,
        invert_tx=args.invert_tx,
        input_format=args.input_format,
    )
    return trace, hz_to_sigrok_rate(samplerate_hz), samplerate_hz, args.input_file


def pair_trace_events(trace: dict[str, Any], samplerate_hz: int) -> list[dict[str, Any]]:
    pending: dict[tuple[str, str], list[dict[str, Any]]] = {}
    byte_events: list[dict[str, Any]] = []

    for event in trace.get("traceEvents", []):
        pid = str(event.get("pid", ""))
        tid = str(event.get("tid", ""))
        phase = str(event.get("ph", ""))
        name = str(event.get("name", ""))
        key = (pid, tid)

        if phase == "B":
            pending.setdefault(key, []).append(event)
            continue

        if phase != "E":
            continue

        stack = pending.get(key)
        if not stack:
            continue

        begin = stack.pop()
        start_sample = int(round(float(begin["ts"])))
        end_sample = int(round(float(event["ts"])))
        direction = "rx" if tid.upper().startswith("RX") else "tx"

        try:
            value = int(name, 16)
        except ValueError:
            continue

        byte_events.append(
            {
                "direction": direction,
                "pid": pid,
                "tid": tid,
                "value": value,
                "hex": f"{value:02X}",
                "ascii": ascii_preview([value]),
                "start_sample": start_sample,
                "end_sample": end_sample,
                "start_seconds": start_sample / samplerate_hz,
                "end_seconds": end_sample / samplerate_hz,
            }
        )

    byte_events.sort(key=lambda item: (item["start_sample"], item["direction"]))
    return byte_events


def split_byte_events(
    byte_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rx_bytes: list[dict[str, Any]] = []
    tx_bytes: list[dict[str, Any]] = []
    rx_index = 0
    tx_index = 0

    for event in byte_events:
        item = {
            "index": 0,
            "timestamp_sample": event["start_sample"],
            "timestamp_seconds": event["start_seconds"],
            "end_sample": event["end_sample"],
            "end_seconds": event["end_seconds"],
            "value": event["value"],
            "hex": event["hex"],
            "ascii": event["ascii"],
        }
        if event["direction"] == "rx":
            rx_index += 1
            item["index"] = rx_index
            rx_bytes.append(item)
        else:
            tx_index += 1
            item["index"] = tx_index
            tx_bytes.append(item)

    return rx_bytes, tx_bytes


def build_segments(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []

    for event in events:
        if not segments or segments[-1]["direction"] != event["direction"]:
            segments.append(
                {
                    "direction": event["direction"],
                    "start_sample": event["start_sample"],
                    "end_sample": event["end_sample"],
                    "start_seconds": event["start_seconds"],
                    "end_seconds": event["end_seconds"],
                    "bytes": [event["value"]],
                }
            )
            continue

        segment = segments[-1]
        segment["bytes"].append(event["value"])
        segment["end_sample"] = event["end_sample"]
        segment["end_seconds"] = event["end_seconds"]

    for segment in segments:
        values = segment["bytes"]
        segment["length"] = len(values)
        segment["hex"] = hex_preview(values)
        segment["ascii"] = ascii_preview(values)

    return segments


def build_sequences(
    byte_events: list[dict[str, Any]],
    samplerate_hz: int,
    baudrate: int,
    mode: UartMode,
    idle_gap_chars: float,
    min_bytes: int,
) -> tuple[list[dict[str, Any]], int]:
    if not byte_events:
        return [], 0

    gap_samples = math.ceil(
        idle_gap_chars * mode.bits_per_symbol * samplerate_hz / baudrate
    )
    sequences: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_end: int | None = None

    for event in byte_events:
        if current and previous_end is not None:
            silence = event["start_sample"] - previous_end
            if silence > gap_samples:
                sequences.append(current)
                current = []
        current.append(event)
        previous_end = event["end_sample"]

    if current:
        sequences.append(current)

    output: list[dict[str, Any]] = []
    kept_index = 0
    for events in sequences:
        if len(events) < min_bytes:
            continue
        kept_index += 1
        start_sample = events[0]["start_sample"]
        end_sample = events[-1]["end_sample"]
        segments = build_segments(events)
        output.append(
            {
                "index": kept_index,
                "name": f"seq_{kept_index:04d}",
                "start_sample": start_sample,
                "end_sample": end_sample,
                "start_seconds": start_sample / samplerate_hz,
                "end_seconds": end_sample / samplerate_hz,
                "duration_seconds": (end_sample - start_sample) / samplerate_hz,
                "byte_count": len(events),
                "segments": segments,
            }
        )

    return output, gap_samples


def summarize_sequence(sequence: dict[str, Any]) -> str:
    pieces = []
    for segment in sequence["segments"][:4]:
        pieces.append(
            f"{segment['direction'].upper()} {segment['ascii']} [{segment['hex']}]"
        )
    summary = " | ".join(pieces)
    if len(sequence["segments"]) > 4:
        summary += " | ..."
    return summary


def maybe_prompt_for_names(
    sequences: list[dict[str, Any]],
    enabled: bool,
) -> None:
    if not enabled or not sequences or not sys.stdin.isatty():
        return

    print("\nNamnge sekvenserna. Tryck Enter för att behålla standardnamnet.\n")
    for sequence in sequences:
        default_name = sequence["name"]
        summary = summarize_sequence(sequence)
        prompt = f"{default_name} ({sequence['byte_count']} bytes) {summary}\nNamn: "
        try:
            name = input(prompt).strip()
        except EOFError:
            return
        if name:
            sequence["name"] = name


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> int:
    args = parse_args()
    mode = parse_uart_mode(args.mode)
    ensure_sigrok_cli()

    output_json = args.output_json.expanduser().resolve()
    default_sr_path = output_json.with_suffix(".sr")
    trace: dict[str, Any]
    capture_source: Path | None
    sr_sidecar_path: Path | None

    if args.input_file is not None:
        trace, samplerate_text, samplerate_hz, capture_source = load_trace_from_import(
            args, mode
        )
        sr_sidecar_path: Path | None = args.input_file.resolve()
    else:
        samplerate_hz = (
            samplerate_to_hz(args.samplerate)
            if args.samplerate
            else choose_default_samplerate(args.baudrate)
        )
        samplerate_text = hz_to_sigrok_rate(samplerate_hz)
        decode_rx_name = args.decode_rx_name or "RX"
        decode_tx_name = args.decode_tx_name or "TX"

        if args.keep_sr:
            sr_path = default_sr_path
            sr_path.parent.mkdir(parents=True, exist_ok=True)
            sr_sidecar_path = sr_path
            capture_to_sr(args, sr_path, samplerate_text)
            capture_source = sr_path
            trace = decode_trace(
                input_file=sr_path,
                decode_rx_name=decode_rx_name,
                decode_tx_name=decode_tx_name,
                baudrate=args.baudrate,
                mode=mode,
                invert_rx=args.invert_rx,
                invert_tx=args.invert_tx,
            )
        else:
            with tempfile.TemporaryDirectory(prefix="dump-uart-") as temp_dir:
                sr_path = Path(temp_dir) / "capture.sr"
                capture_to_sr(args, sr_path, samplerate_text)
                trace = decode_trace(
                    input_file=sr_path,
                    decode_rx_name=decode_rx_name,
                    decode_tx_name=decode_tx_name,
                    baudrate=args.baudrate,
                    mode=mode,
                    invert_rx=args.invert_rx,
                    invert_tx=args.invert_tx,
                )
                sr_sidecar_path = None
            capture_source = None

    byte_events = pair_trace_events(trace, samplerate_hz)
    rx_bytes, tx_bytes = split_byte_events(byte_events)

    sequences: list[dict[str, Any]] = []
    gap_samples = 0
    if args.group_sequences:
        sequences, gap_samples = build_sequences(
            byte_events=byte_events,
            samplerate_hz=samplerate_hz,
            baudrate=args.baudrate,
            mode=mode,
            idle_gap_chars=args.idle_gap_chars,
            min_bytes=args.min_bytes,
        )
        maybe_prompt_for_names(sequences, args.prompt_names)

    payload = {
        "format": "sigrok-uart-bytes/v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "capture": {
            "driver": args.driver,
            "source": str(capture_source) if capture_source else None,
            "baudrate": args.baudrate,
            "mode": args.mode.upper(),
            "data_bits": mode.data_bits,
            "parity": mode.parity_name,
            "stop_bits": mode.stop_bits_value,
            "samplerate_hz": samplerate_hz,
            "samplerate_sigrok": samplerate_text,
            "rx_channel": args.rx_channel,
            "tx_channel": args.tx_channel,
            "invert_rx": args.invert_rx,
            "invert_tx": args.invert_tx,
            "group_sequences": args.group_sequences,
        },
        "files": {
            "json": str(output_json),
            "sigrok_session": str(sr_sidecar_path) if sr_sidecar_path else None,
        },
        "rx_bytes": rx_bytes,
        "tx_bytes": tx_bytes,
        "byte_count": {
            "rx": len(rx_bytes),
            "tx": len(tx_bytes),
            "total": len(byte_events),
        },
    }
    if args.include_trace_events:
        payload["traceEvents"] = trace.get("traceEvents", [])
    if args.group_sequences:
        payload["sequence_config"] = {
            "idle_gap_chars": args.idle_gap_chars,
            "idle_gap_samples": gap_samples,
            "min_bytes": args.min_bytes,
        }
        payload["sequences"] = sequences
    write_json(output_json, payload)

    print(
        f"Skrev RX={len(rx_bytes)} och TX={len(tx_bytes)} bytes till {output_json}",
        file=sys.stderr,
    )
    if sr_sidecar_path is not None:
        print(f"Rå sigrok-capture sparad i {sr_sidecar_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Fel: {exc}", file=sys.stderr)
        raise SystemExit(1)
