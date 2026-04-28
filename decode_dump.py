#!/usr/bin/env python3

from __future__ import annotations

import argparse
import bisect
import json
import math
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SYNC_PREFIX_LEN = 2
SYNC_SEARCH_TOP = 32
GENERIC_REFERENCE_NAMES = {"dump"}


@dataclass(frozen=True)
class Packet:
    direction: str
    index: int
    offset: int
    start_sample: int
    start_seconds: float
    end_sample: int
    end_seconds: float
    raw: tuple[int, ...]
    payload: tuple[int, ...]
    checksum_ok: bool

    @property
    def length_field(self) -> int:
        return self.raw[2]

    @property
    def sync(self) -> tuple[int, int]:
        return self.raw[0], self.raw[1]

    @property
    def checksum(self) -> tuple[int, int]:
        return self.raw[-2], self.raw[-1]


@dataclass(frozen=True)
class ScanResult:
    sync: tuple[int, int]
    packets: list[Packet]
    consumed_bytes: int
    garbage_bytes: int


@dataclass(frozen=True)
class MessageFamily:
    direction: str
    key: tuple[int, ...]
    count: int
    payload_length: int
    field_stats: list[dict[str, Any]]
    label: str


@dataclass(frozen=True)
class ReferenceMapping:
    name: str
    source_file: str
    rx_mask: int
    tx_payload_index: int
    tx_channel_index: int
    tx_range: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Decode robot UART dump.json into verified packets, identify packet "
            "families, and infer which fields map to servo movement."
        )
    )
    parser.add_argument(
        "input_json",
        nargs="?",
        type=Path,
        help="Input dump JSON. Default: dump.json or dumps/dump.json if present.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the decoded result as JSON.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the decoded result as JSON instead of a text summary.",
    )
    parser.add_argument(
        "--show-packets",
        type=int,
        default=8,
        help="How many decoded packets to show in the text summary (default: 8).",
    )
    parser.add_argument(
        "--calibration-dir",
        type=Path,
        help=(
            "Optional directory with sibling JSON dumps used to infer stable names "
            "for masks and TX channel indices."
        ),
    )
    return parser.parse_args()


def resolve_input_path(path: Path | None) -> Path:
    if path is not None:
        return path

    candidates = [
        Path("dump.json"),
        Path("dumps/dump.json"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "Hittade ingen default-input. Ange sökvägen till dump.json explicit."
    )


def load_dump(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def fletcher16(data: list[int] | tuple[int, ...]) -> tuple[int, int]:
    sum1 = 0
    sum2 = 0
    for value in data:
        sum1 = (sum1 + value) % 255
        sum2 = (sum2 + sum1) % 255
    return sum2, sum1


def scan_packets(direction: str, entries: list[dict[str, Any]], sync: tuple[int, int]) -> ScanResult:
    values = [entry["value"] for entry in entries]
    packets: list[Packet] = []
    i = 0
    garbage_bytes = 0

    while i < len(values):
        if i + 5 > len(values):
            garbage_bytes += len(values) - i
            break

        if values[i] != sync[0] or values[i + 1] != sync[1]:
            garbage_bytes += 1
            i += 1
            continue

        payload_length = values[i + 2]
        total_length = payload_length + 5
        if i + total_length > len(values):
            garbage_bytes += len(values) - i
            break

        raw = tuple(values[i : i + total_length])
        checksum_ok = fletcher16(raw[:-2]) == raw[-2:]
        if not checksum_ok:
            garbage_bytes += 1
            i += 1
            continue

        start_entry = entries[i]
        end_entry = entries[i + total_length - 1]
        packets.append(
            Packet(
                direction=direction,
                index=len(packets),
                offset=i,
                start_sample=start_entry["timestamp_sample"],
                start_seconds=start_entry["timestamp_seconds"],
                end_sample=end_entry["end_sample"],
                end_seconds=end_entry["end_seconds"],
                raw=raw,
                payload=raw[3:-2],
                checksum_ok=True,
            )
        )
        i += total_length

    consumed_bytes = sum(len(packet.raw) for packet in packets)
    return ScanResult(
        sync=sync,
        packets=packets,
        consumed_bytes=consumed_bytes,
        garbage_bytes=garbage_bytes,
    )


def infer_sync(direction: str, entries: list[dict[str, Any]]) -> ScanResult:
    values = [entry["value"] for entry in entries]
    pair_counts = Counter(
        tuple(values[i : i + SYNC_PREFIX_LEN])
        for i in range(max(0, len(values) - 1))
    )

    best: ScanResult | None = None
    for sync, _count in pair_counts.most_common(SYNC_SEARCH_TOP):
        result = scan_packets(direction, entries, sync)
        if best is None:
            best = result
            continue
        if len(result.packets) > len(best.packets):
            best = result
            continue
        if len(result.packets) == len(best.packets) and result.consumed_bytes > best.consumed_bytes:
            best = result

    if best is None or not best.packets:
        raise ValueError(f"Kunde inte hitta någon rimlig packetisering för {direction}.")
    return best


def message_family_key(packet: Packet) -> tuple[int, ...]:
    payload = packet.payload
    if len(payload) >= 2:
        return (len(payload), payload[0], payload[1])
    if len(payload) == 1:
        return (1, payload[0])
    return (0,)


def compute_field_stats(values: list[tuple[int, ...]]) -> list[dict[str, Any]]:
    if not values:
        return []

    columns = list(zip(*values))
    stats: list[dict[str, Any]] = []
    for index, column in enumerate(columns):
        minimum = min(column)
        maximum = max(column)
        mean = sum(column) / len(column)
        variance = sum((item - mean) ** 2 for item in column) / len(column)
        changes = sum(a != b for a, b in zip(column, column[1:]))
        stats.append(
            {
                "index": index,
                "min": minimum,
                "max": maximum,
                "range": maximum - minimum,
                "unique_values": len(set(column)),
                "mean": mean,
                "stddev": math.sqrt(variance),
                "changes": changes,
            }
        )
    return stats


def family_label(direction: str, key: tuple[int, ...]) -> str:
    if direction == "rx" and len(key) == 3 and key[0] == 4 and key[1] == 0x01:
        return "set_position_target"
    if direction == "rx" and len(key) >= 2 and key[0] == 2 and key[1] == 0x4F:
        return "set_power_mask"
    if direction == "rx" and len(key) >= 2 and key[0] == 2 and key[1] == 0x43:
        return "calibration_request"
    if direction == "rx" and key == (1, 0x40):
        return "read_vr_values_request"
    if direction == "rx" and key == (1, 0x42):
        return "read_calibration_values_request"
    if direction == "rx" and key == (1, 0x47):
        return "get_pid_request"
    if direction == "rx" and key == (1, 0x52):
        return "reset_pid_values_request"
    if direction == "rx" and key == (1, 0x56):
        return "read_version_request"
    if direction == "rx" and len(key) >= 2 and key[0] == 85 and key[1] == 0x50:
        return "set_pid_gains"
    if direction == "rx" and len(key) >= 3 and key[0] > 4 and key[1] != 0x00 and key[2] != 0x01:
        return "motion_script"
    if direction == "tx" and len(key) >= 2 and key[0] == 2 and key[1] == 0x4F:
        return "set_power_mask_echo"
    if direction == "tx" and len(key) >= 2 and key[0] in (2, 3) and key[1] == 0x43:
        return "calibration_result"
    if direction == "tx" and len(key) >= 2 and key[0] == 43 and key[1] == 0x42:
        return "calibration_values_report"
    if direction == "tx" and key == (5, 0x56, 0x40):
        return "version_id_report"
    if direction == "tx" and key == (9, 0x01, 0x00):
        return "position_feedback"
    if direction == "tx" and key == (3, 0x02, 0x6E):
        return "active_mask_echo"
    if direction == "tx" and len(key) >= 2 and key[0] == 85 and key[1] == 0x47:
        return "pid_report"
    if direction == "tx" and len(key) >= 2 and key[0] == 85 and key[1] == 0x52:
        return "reset_pid_values_report"
    if direction == "tx" and len(key) >= 2 and key[0] == 85 and key[1] == 0x50:
        return "pid_write_echo"
    return "generic"


def build_families(direction: str, packets: list[Packet]) -> list[MessageFamily]:
    grouped: dict[tuple[int, ...], list[Packet]] = defaultdict(list)
    for packet in packets:
        grouped[message_family_key(packet)].append(packet)

    families: list[MessageFamily] = []
    for key, family_packets in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        payloads = [packet.payload for packet in family_packets]
        families.append(
            MessageFamily(
                direction=direction,
                key=key,
                count=len(family_packets),
                payload_length=len(family_packets[0].payload),
                field_stats=compute_field_stats(payloads),
                label=family_label(direction, key),
            )
        )
    return families


def dominant_tx_field(snapshot_packets: list[Packet]) -> tuple[int, int] | None:
    if not snapshot_packets:
        return None

    payloads = [packet.payload for packet in snapshot_packets]
    stats = compute_field_stats(payloads)
    candidates = [item for item in stats if item["index"] >= 2]
    if not candidates:
        return None

    best = max(candidates, key=lambda item: (item["range"], item["stddev"], item["changes"]))
    return best["index"], best["range"]


def rx_mask_value(rx_packets: list[Packet]) -> int | None:
    if not rx_packets:
        return None

    first_payload = rx_packets[0].payload
    if len(first_payload) == 4 and first_payload[0] == 0x01:
        return first_payload[1]
    return None


def normalize_name(text: str) -> str:
    return text.replace("-", "_")


def reference_preference(reference: ReferenceMapping) -> tuple[int, int, int]:
    descriptive = 0 if reference.name in GENERIC_REFERENCE_NAMES else 1
    return descriptive, len(reference.name), reference.tx_range


def build_reference_mappings(directory: Path) -> list[ReferenceMapping]:
    mappings_by_mask: dict[int, ReferenceMapping] = {}

    for path in sorted(directory.glob("*.json")):
        data = load_dump(path)
        rx_result = infer_sync("rx", data["rx_bytes"])
        tx_result = infer_sync("tx", data["tx_bytes"])
        mask = rx_mask_value(rx_result.packets)
        if mask is None:
            continue

        snapshot_packets = [
            packet
            for packet in tx_result.packets
            if message_family_key(packet) == (9, 0x01, 0x00)
        ]
        dominant = dominant_tx_field(snapshot_packets)
        if dominant is None:
            continue

        tx_payload_index, tx_range = dominant
        candidate = ReferenceMapping(
            name=normalize_name(path.stem),
            source_file=path.name,
            rx_mask=mask,
            tx_payload_index=tx_payload_index,
            tx_channel_index=tx_payload_index - 2,
            tx_range=tx_range,
        )
        existing = mappings_by_mask.get(mask)
        if existing is None or reference_preference(candidate) > reference_preference(existing):
            mappings_by_mask[mask] = candidate

    return sorted(mappings_by_mask.values(), key=lambda item: item.rx_mask, reverse=True)


def decode_packet(
    packet: Packet,
    mask_names: dict[int, str],
    tx_channel_names: dict[int, str],
) -> dict[str, Any]:
    payload = list(packet.payload)

    if packet.direction == "rx" and len(payload) == 4 and payload[0] == 0x01:
        mask = payload[1]
        return {
            "kind": "set_position_target",
            "mask": mask,
            "mask_hex": f"0x{mask:02X}",
            "name": mask_names.get(mask),
            "register": payload[2],
            "value": payload[3],
        }

    if packet.direction == "tx" and len(payload) == 9 and payload[:2] == [0x01, 0x00]:
        channels = []
        for payload_index in range(2, len(payload)):
            channel_index = payload_index - 2
            channels.append(
                {
                    "channel_index": channel_index,
                    "payload_index": payload_index,
                    "name": tx_channel_names.get(payload_index),
                    "value": payload[payload_index],
                }
            )
        return {
            "kind": "position_feedback",
            "channels": channels,
        }

    if packet.direction == "tx" and len(payload) == 3 and payload[:2] == [0x02, 0x6E]:
        mask = payload[2]
        return {
            "kind": "active_mask_echo",
            "mask": mask,
            "mask_hex": f"0x{mask:02X}",
            "name": mask_names.get(mask),
        }

    if len(payload) == 2 and payload[0] == 0x4F:
        mask_info = decode_mask(mask=payload[1], mask_names=mask_names)
        return {
            "kind": "set_power_mask" if packet.direction == "rx" else "set_power_mask_echo",
            "opcode": payload[0],
            **mask_info,
        }

    if packet.direction == "rx" and len(payload) == 2 and payload[0] == 0x43:
        mask_info = decode_mask(mask=payload[1], mask_names=mask_names)
        return {
            "kind": "calibration_request",
            "opcode": payload[0],
            **mask_info,
        }

    if packet.direction == "tx" and len(payload) == 2 and payload[0] == 0x43:
        return {
            "kind": "calibration_result",
            "opcode": payload[0],
            "success": payload[1] == 0x00,
            "result_code": payload[1],
            "result_code_hex": f"0x{payload[1]:02X}",
            "result_name": "success" if payload[1] == 0x00 else None,
        }

    if packet.direction == "tx" and len(payload) == 3 and payload[0] == 0x43:
        mask_info = decode_mask(mask=payload[1], mask_names=mask_names)
        return {
            "kind": "calibration_result",
            "opcode": payload[0],
            "success": False,
            **mask_info,
            "status": payload[2],
            "status_hex": f"0x{payload[2]:02X}",
            "status_name": calibration_status_name(payload[2]),
        }

    if packet.direction == "rx" and payload == [0x40]:
        return {
            "kind": "read_vr_values_request",
        }

    if packet.direction == "rx" and payload == [0x42]:
        return {
            "kind": "read_calibration_values_request",
        }

    if packet.direction == "rx" and payload == [0x47]:
        return {
            "kind": "get_pid_request",
        }

    if packet.direction == "rx" and payload == [0x52]:
        return {
            "kind": "reset_pid_values_request",
        }

    if packet.direction == "rx" and payload == [0x56]:
        return {
            "kind": "read_version_request",
        }

    if packet.direction == "rx" and len(payload) > 4 and payload[0] == 0x01:
        return decode_motion_script_payload(payload, mask_names, tx_channel_names)

    if packet.direction == "tx" and len(payload) == 43 and payload[0] == 0x42:
        return {
            "kind": "calibration_values_report",
            "opcode": payload[0],
            "triplets": decode_calibration_triplets(payload[1:], tx_channel_names),
        }

    if packet.direction == "tx" and len(payload) == 5 and payload[0] == 0x56:
        version_value = struct.unpack(">f", bytes(payload[1:5]))[0]
        return {
            "kind": "version_id_report",
            "opcode": payload[0],
            "version_float": version_value,
        }

    if len(payload) == 85 and payload[0] in (0x47, 0x50):
        gains = decode_pid_payload(payload, tx_channel_names)
        if packet.direction == "rx" and payload[0] == 0x50:
            kind = "set_pid_gains"
        elif packet.direction == "tx" and payload[0] == 0x47:
            kind = "pid_report"
        elif packet.direction == "tx" and payload[0] == 0x50:
            kind = "pid_write_echo"
        else:
            kind = "pid_blob"
        return {
            "kind": kind,
            "opcode": payload[0],
            "gains": gains,
        }

    if packet.direction == "tx" and len(payload) == 85 and payload[0] == 0x52:
        return {
            "kind": "reset_pid_values_report",
            "opcode": payload[0],
            "gains": decode_pid_payload(payload, tx_channel_names),
        }

    return {
        "kind": "generic",
        "payload": payload,
    }


def decode_mask(mask: int, mask_names: dict[int, str]) -> dict[str, Any]:
    members: list[dict[str, Any]] = []
    remaining = mask
    for bit, name in sorted(mask_names.items(), reverse=True):
        if mask & bit:
            members.append(
                {
                    "bit": bit,
                    "bit_hex": f"0x{bit:02X}",
                    "name": name,
                }
            )
            remaining &= ~bit

    exact_name = mask_names.get(mask)
    if exact_name is not None:
        label = exact_name
    elif members and remaining == 0 and len(members) == len(mask_names):
        label = "all_channels"
    else:
        label = None

    return {
        "mask": mask,
        "mask_hex": f"0x{mask:02X}",
        "name": label,
        "members": members,
        "unknown_mask_bits": remaining,
        "unknown_mask_bits_hex": f"0x{remaining:02X}",
    }


def calibration_status_name(status: int) -> str | None:
    return {
        0x62: "center_voltage_too_high",
        0x73: "range_too_small",
    }.get(status)


def decode_pid_payload(
    payload: list[int],
    tx_channel_names: dict[int, str],
) -> list[dict[str, Any]]:
    body = bytes(payload[1:])
    if len(body) % 12 != 0:
        return [{"channel_index": -1, "name": None, "raw_hex": body.hex()}]

    gains: list[dict[str, Any]] = []
    channel_count = len(body) // 12
    for channel_index in range(channel_count):
        offset = channel_index * 12
        p_gain, i_gain, d_gain = struct.unpack(">fff", body[offset : offset + 12])
        payload_index = channel_index + 2
        gains.append(
            {
                "channel_index": channel_index,
                "payload_index": payload_index,
                "name": tx_channel_names.get(payload_index),
                "p": p_gain,
                "i": i_gain,
                "d": d_gain,
            }
        )
    return gains


def decode_calibration_triplets(
    payload: list[int],
    tx_channel_names: dict[int, str],
) -> list[dict[str, Any]]:
    if len(payload) % 6 != 0:
        return [{"channel_index": -1, "raw_hex": bytes(payload).hex()}]

    triplets: list[dict[str, Any]] = []
    channel_count = len(payload) // 6
    for channel_index in range(channel_count):
        offset = channel_index * 6
        a = payload[offset] | (payload[offset + 1] << 8)
        b = payload[offset + 2] | (payload[offset + 3] << 8)
        c = payload[offset + 4] | (payload[offset + 5] << 8)
        payload_index = channel_index + 2
        triplets.append(
            {
                "channel_index": channel_index,
                "payload_index": payload_index,
                "name": tx_channel_names.get(payload_index),
                "minimum": a,
                "maximum": b,
                "measured_range": c,
                "range_from_minmax": b - a,
                "range_matches_minmax": (b - a) == c,
                "value_a": a,
                "value_b": b,
                "value_c": c,
            }
        )
    return triplets


def decode_motion_script_payload(
    payload: list[int],
    mask_names: dict[int, str],
    tx_channel_names: dict[int, str],
) -> dict[str, Any]:
    mask_info = decode_mask(mask=payload[1], mask_names=mask_names)
    body = payload[2:]
    explicit_length = body[0] if body else None

    if mask_info.get("name") == "all_channels" and body[:1] == [0x01] and len(body) == 8:
        values = []
        for channel_index, value in enumerate(body[1:]):
            payload_index = channel_index + 2
            values.append(
                {
                    "channel_index": channel_index,
                    "payload_index": payload_index,
                    "name": tx_channel_names.get(payload_index),
                    "value": value,
                }
            )
        return {
            "kind": "pose_preset",
            "opcode": payload[0],
            **mask_info,
            "mode": body[0],
            "values": values,
        }

    if explicit_length is not None and explicit_length == len(body) - 1 and explicit_length % 2 == 0:
        pairs = []
        script_bytes = body[1:]
        for index in range(0, len(script_bytes), 2):
            pairs.append(
                {
                    "step_index": index // 2,
                    "value": script_bytes[index],
                    "arg": script_bytes[index + 1],
                }
            )
        return {
            "kind": "motion_script",
            "opcode": payload[0],
            **mask_info,
            "script_length": explicit_length,
            "steps": pairs,
        }

    if len(mask_info["members"]) == 2 and explicit_length is not None and explicit_length & 0x80:
        point_count = explicit_length & 0x7F
        script_bytes = body[1:]
        if len(script_bytes) == point_count * 3:
            channel_a, channel_b = mask_info["members"]
            points = []
            for index in range(point_count):
                offset = index * 3
                value_a = script_bytes[offset]
                value_b = script_bytes[offset + 1]
                duration = script_bytes[offset + 2]
                points.append(
                    {
                        "point_index": index,
                        "channel_a_name": channel_a["name"],
                        "channel_b_name": channel_b["name"],
                        "channel_a_value": value_a,
                        "channel_b_value": value_b,
                        "duration": duration,
                    }
                )
            return {
                "kind": "motion_script_2ch",
                "opcode": payload[0],
                **mask_info,
                "point_count": point_count,
                "points": points,
            }

    return {
        "kind": "motion_script_raw",
        "opcode": payload[0],
        **mask_info,
        "data_bytes": body,
    }


def packet_to_dict(
    packet: Packet,
    mask_names: dict[int, str],
    tx_channel_names: dict[int, str],
) -> dict[str, Any]:
    return {
        "direction": packet.direction,
        "index": packet.index,
        "offset": packet.offset,
        "start_sample": packet.start_sample,
        "start_seconds": packet.start_seconds,
        "end_sample": packet.end_sample,
        "end_seconds": packet.end_seconds,
        "sync": [f"0x{packet.sync[0]:02X}", f"0x{packet.sync[1]:02X}"],
        "length_field": packet.length_field,
        "raw_hex": " ".join(f"{value:02X}" for value in packet.raw),
        "payload_hex": " ".join(f"{value:02X}" for value in packet.payload),
        "checksum_hex": " ".join(f"{value:02X}" for value in packet.checksum),
        "checksum_ok": packet.checksum_ok,
        "decoded": decode_packet(packet, mask_names, tx_channel_names),
    }


def correlate_active_fields(rx_packets: list[Packet], tx_packets: list[Packet]) -> dict[str, Any] | None:
    rx_candidates = [
        item
        for item in compute_field_stats([packet.payload for packet in rx_packets])
        if item["range"] > 0
    ]
    tx_snapshot_packets = [
        packet for packet in tx_packets if message_family_key(packet) == (9, 0x01, 0x00)
    ]
    tx_candidates = [
        item
        for item in compute_field_stats([packet.payload for packet in tx_snapshot_packets])
        if item["index"] >= 2 and item["range"] > 0
    ]
    if not rx_candidates or not tx_candidates or not tx_snapshot_packets:
        return None

    tx_times = [packet.start_seconds for packet in tx_snapshot_packets]
    best: dict[str, Any] | None = None
    for rx_field in rx_candidates:
        for tx_field in tx_candidates:
            rx_values: list[int] = []
            tx_values: list[int] = []
            offsets: list[float] = []
            for rx_packet in rx_packets:
                probe = rx_packet.start_seconds
                position = bisect.bisect_left(tx_times, probe)
                candidates = []
                for index in (position - 1, position, position + 1):
                    if 0 <= index < len(tx_snapshot_packets):
                        offset = tx_snapshot_packets[index].start_seconds - probe
                        candidates.append((abs(offset), offset, index))
                if not candidates:
                    continue
                _distance, offset, index = min(candidates)
                rx_values.append(rx_packet.payload[rx_field["index"]])
                tx_values.append(tx_snapshot_packets[index].payload[tx_field["index"]])
                offsets.append(offset)

            if len(rx_values) < 2:
                continue

            rx_mean = sum(rx_values) / len(rx_values)
            tx_mean = sum(tx_values) / len(tx_values)
            covariance = sum(
                (rx_value - rx_mean) * (tx_value - tx_mean)
                for rx_value, tx_value in zip(rx_values, tx_values)
            )
            rx_energy = sum((value - rx_mean) ** 2 for value in rx_values)
            tx_energy = sum((value - tx_mean) ** 2 for value in tx_values)
            if rx_energy == 0 or tx_energy == 0:
                continue

            correlation = covariance / math.sqrt(rx_energy * tx_energy)
            candidate = {
                "rx_payload_index": rx_field["index"],
                "tx_payload_index": tx_field["index"],
                "tx_channel_index": tx_field["index"] - 2,
                "correlation": correlation,
                "mean_time_offset_ms": (sum(offsets) / len(offsets)) * 1000,
                "tx_field_range": tx_field["range"],
                "rx_field_range": rx_field["range"],
            }
            if best is None or abs(candidate["correlation"]) > abs(best["correlation"]):
                best = candidate

    return best


def packet_rate_hz(packets: list[Packet]) -> float | None:
    if len(packets) < 2:
        return None
    gaps = [
        second.start_seconds - first.start_seconds
        for first, second in zip(packets, packets[1:])
    ]
    mean_gap = sum(gaps) / len(gaps)
    if mean_gap <= 0:
        return None
    return 1 / mean_gap


def build_result(input_path: Path, calibration_dir: Path | None) -> dict[str, Any]:
    data = load_dump(input_path)
    rx_result = infer_sync("rx", data["rx_bytes"])
    tx_result = infer_sync("tx", data["tx_bytes"])

    references = build_reference_mappings(calibration_dir) if calibration_dir else []
    mask_names = {reference.rx_mask: reference.name for reference in references}
    tx_channel_names = {
        reference.tx_payload_index: reference.name for reference in references
    }

    rx_families = build_families("rx", rx_result.packets)
    tx_families = build_families("tx", tx_result.packets)
    correlation = correlate_active_fields(rx_result.packets, tx_result.packets)

    timeline = sorted(
        [
            packet_to_dict(packet, mask_names, tx_channel_names)
            for packet in [*rx_result.packets, *tx_result.packets]
        ],
        key=lambda item: (item["start_seconds"], item["direction"], item["index"]),
    )

    return {
        "input_file": str(input_path),
        "protocol": {
            "sync": [f"0x{rx_result.sync[0]:02X}", f"0x{rx_result.sync[1]:02X}"],
            "length_rule": "total_bytes = payload_length + 5",
            "checksum": "fletcher16(header_plus_payload) stored as [sum2, sum1]",
        },
        "directions": {
            "rx": {
                "packet_count": len(rx_result.packets),
                "consumed_bytes": rx_result.consumed_bytes,
                "garbage_bytes": rx_result.garbage_bytes,
                "rate_hz": packet_rate_hz(rx_result.packets),
                "families": [
                    {
                        "label": family.label,
                        "key": [f"0x{value:02X}" for value in family.key],
                        "count": family.count,
                        "payload_length": family.payload_length,
                        "field_stats": family.field_stats,
                    }
                    for family in rx_families
                ],
            },
            "tx": {
                "packet_count": len(tx_result.packets),
                "consumed_bytes": tx_result.consumed_bytes,
                "garbage_bytes": tx_result.garbage_bytes,
                "rate_hz": packet_rate_hz(tx_result.packets),
                "families": [
                    {
                        "label": family.label,
                        "key": [f"0x{value:02X}" for value in family.key],
                        "count": family.count,
                        "payload_length": family.payload_length,
                        "field_stats": family.field_stats,
                    }
                    for family in tx_families
                ],
            },
        },
        "references": [
            {
                "name": reference.name,
                "source_file": reference.source_file,
                "rx_mask": f"0x{reference.rx_mask:02X}",
                "tx_payload_index": reference.tx_payload_index,
                "tx_channel_index": reference.tx_channel_index,
                "tx_range": reference.tx_range,
            }
            for reference in references
        ],
        "active_field_correlation": correlation,
        "packets": timeline,
    }


def fmt_rate(rate: float | None) -> str:
    if rate is None:
        return "okänd"
    return f"{rate:.2f} Hz"


def format_family(family: dict[str, Any], tx_channel_names: dict[int, str]) -> str:
    lines = [
        f"- {family['label']}: {family['count']} paket, payload_len={family['payload_length']}",
    ]
    for field in family["field_stats"]:
        field_name = ""
        if family["label"] == "position_feedback" and field["index"] >= 2:
            name = tx_channel_names.get(field["index"])
            if name:
                field_name = f" ({name})"
        lines.append(
            "  "
            f"payload[{field['index']}]"
            f"{field_name}: range={field['range']} min={field['min']} max={field['max']} "
            f"unique={field['unique_values']}"
        )
    return "\n".join(lines)


def format_packet(packet: dict[str, Any]) -> str:
    decoded = packet["decoded"]
    prefix = f"{packet['start_seconds']:.6f}s {packet['direction'].upper()} {decoded['kind']}"

    if decoded["kind"] == "set_position_target":
        name = decoded.get("name") or "unknown"
        return (
            f"{prefix} mask={decoded['mask_hex']} ({name}) "
            f"register=0x{decoded['register']:02X} value={decoded['value']}"
        )

    if decoded["kind"] == "position_feedback":
        parts = []
        for channel in decoded["channels"]:
            name = channel["name"] or f"ch{channel['channel_index']}"
            parts.append(f"{name}={channel['value']}")
        return f"{prefix} " + ", ".join(parts)

    if decoded["kind"] == "active_mask_echo":
        name = decoded.get("name") or "unknown"
        return f"{prefix} mask={decoded['mask_hex']} ({name})"

    if decoded["kind"] in {"set_power_mask", "set_power_mask_echo", "calibration_request"}:
        return f"{prefix} mask={format_mask(decoded)}"

    if decoded["kind"] == "calibration_result":
        if decoded.get("success"):
            return f"{prefix} success"
        if "status_hex" not in decoded:
            return f"{prefix} result={decoded['result_code_hex']}"
        status_note = decoded.get("status_name")
        if status_note:
            return (
                f"{prefix} failed_mask={format_mask(decoded)} "
                f"status={decoded['status_hex']} ({status_note})"
            )
        return (
            f"{prefix} failed_mask={format_mask(decoded)} "
            f"status={decoded['status_hex']}"
        )

    if decoded["kind"] in {
        "read_vr_values_request",
        "read_calibration_values_request",
        "get_pid_request",
        "reset_pid_values_request",
        "read_version_request",
    }:
        return prefix

    if decoded["kind"] in {
        "set_pid_gains",
        "pid_report",
        "pid_write_echo",
        "pid_blob",
        "reset_pid_values_report",
    }:
        parts = []
        for gain in decoded.get("gains", []):
            name = gain.get("name") or f"ch{gain['channel_index']}"
            if "p" in gain:
                parts.append(
                    f"{name}(P={gain['p']:.6g}, I={gain['i']:.6g}, D={gain['d']:.6g})"
                )
            else:
                parts.append(name)
        if parts:
            return f"{prefix} " + ", ".join(parts)
        return prefix

    if decoded["kind"] == "version_id_report":
        return f"{prefix} version={decoded['version_float']:.3f}"

    if decoded["kind"] == "calibration_values_report":
        parts = []
        for item in decoded.get("triplets", []):
            name = item.get("name") or f"ch{item['channel_index']}"
            if "minimum" in item:
                parts.append(
                    f"{name}(min={item['minimum']}, max={item['maximum']}, range={item['measured_range']})"
                )
        if parts:
            return f"{prefix} " + ", ".join(parts)
        return prefix

    if decoded["kind"] == "pose_preset":
        parts = []
        for item in decoded.get("values", []):
            name = item.get("name") or f"ch{item['channel_index']}"
            parts.append(f"{name}={item['value']}")
        if parts:
            return f"{prefix} " + ", ".join(parts)
        return prefix

    if decoded["kind"] == "motion_script":
        steps = ", ".join(
            f"{step['step_index']}:{step['value']}/{step['arg']}"
            for step in decoded.get("steps", [])
        )
        return f"{prefix} mask={format_mask(decoded)} steps=[{steps}]"

    if decoded["kind"] == "motion_script_2ch":
        points = ", ".join(
            f"{point['point_index']}:{point['channel_a_value']}/{point['channel_b_value']}@{point['duration']}"
            for point in decoded.get("points", [])
        )
        return f"{prefix} mask={format_mask(decoded)} points=[{points}]"

    if decoded["kind"] == "motion_script_raw":
        data = " ".join(f"{value:02X}" for value in decoded.get("data_bytes", []))
        return f"{prefix} mask={format_mask(decoded)} data={data}"

    return f"{prefix} payload={packet['payload_hex']}"


def format_mask(decoded: dict[str, Any]) -> str:
    mask_hex = decoded["mask_hex"]
    name = decoded.get("name")
    members = decoded.get("members", [])
    if name is not None:
        return f"{mask_hex} ({name})"
    if members:
        member_names = ",".join(member["name"] for member in members)
        return f"{mask_hex} ({member_names})"
    return mask_hex


def print_text_summary(result: dict[str, Any], show_packets: int) -> None:
    references = result["references"]
    tx_channel_names = {item["tx_payload_index"]: item["name"] for item in references}
    rx_direction = result["directions"]["rx"]
    tx_direction = result["directions"]["tx"]

    print("Framing")
    print(f"- sync: {' '.join(result['protocol']['sync'])}")
    print(f"- length: {result['protocol']['length_rule']}")
    print(f"- checksum: {result['protocol']['checksum']}")
    print()

    print("Directions")
    print(
        f"- RX: {rx_direction['packet_count']} paket, {rx_direction['consumed_bytes']} bytes, "
        f"{fmt_rate(rx_direction['rate_hz'])}"
    )
    for family in rx_direction["families"]:
        print(format_family(family, tx_channel_names))
    print(
        f"- TX: {tx_direction['packet_count']} paket, {tx_direction['consumed_bytes']} bytes, "
        f"{fmt_rate(tx_direction['rate_hz'])}"
    )
    for family in tx_direction["families"]:
        print(format_family(family, tx_channel_names))
    print()

    if references:
        print("Reference Map")
        for reference in references:
            print(
                f"- mask {reference['rx_mask']} -> {reference['name']} -> "
                f"TX payload[{reference['tx_payload_index']}] / channel {reference['tx_channel_index']}"
            )
        print()

    correlation = result["active_field_correlation"]
    if correlation is not None:
        tx_name = tx_channel_names.get(correlation["tx_payload_index"])
        suffix = f" ({tx_name})" if tx_name else ""
        print("Likely Active Link")
        print(
            f"- RX payload[{correlation['rx_payload_index']}] tracks "
            f"TX payload[{correlation['tx_payload_index']}] / channel {correlation['tx_channel_index']}"
            f"{suffix}"
        )
        print(
            f"- correlation={correlation['correlation']:.3f}, "
            f"mean time offset={correlation['mean_time_offset_ms']:.3f} ms"
        )
        print()

    rx_rate = rx_direction["rate_hz"]
    tx_rate = tx_direction["rate_hz"]
    if correlation is not None and rx_rate and tx_rate and tx_rate > rx_rate:
        print("Interpretation")
        print("- RX ser ut som kommenderat målvärde per mask/adress.")
        print(
            f"- TX ser ut som återrapporterad position: {tx_rate / rx_rate:.2f}x högre uppdateringsfrekvens "
            "och värdet glider mot RX-värdet i mellanliggande snapshots."
        )
        print()

    print("Sample Timeline")
    for packet in result["packets"][:show_packets]:
        print(f"- {format_packet(packet)}")


def main() -> None:
    args = parse_args()
    input_path = resolve_input_path(args.input_json).resolve()
    calibration_dir = args.calibration_dir.resolve() if args.calibration_dir else input_path.parent
    result = build_result(input_path, calibration_dir)

    if args.output:
        with args.output.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")

    if args.json:
        print(json.dumps(result, indent=2))
        return

    print_text_summary(result, args.show_packets)


if __name__ == "__main__":
    main()
