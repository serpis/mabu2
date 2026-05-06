#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean


BASELINE_PID = (
    0.8, 0.0000125, 8.0,
    0.8, 0.0000125, 8.0,
    2.1, 0.0023, 16.0,
    2.1, 0.0023, 16.0,
    1.0, 0.00125, 0.008,
    0.3, 0.001, 0.08,
    0.5, 0.001, 0.08,
)


@dataclass(frozen=True)
class Variant:
    label: str
    eye_updown_pid: tuple[float, float, float]
    rationale: str


PRESS_VARIANTS = (
    Variant("best_previous", (1.5, 0.0008, 8.0), "previous best short-sweep value"),
    Variant("p1p5_i0p0005_d10", (1.5, 0.0005, 10.0), "lower I with more D"),
    Variant("p1p7_i0p0005_d8", (1.7, 0.0005, 8.0), "higher P, lower I"),
    Variant("p1p7_i0p0008_d6", (1.7, 0.0008, 6.0), "higher P, lower D"),
    Variant("p1p7_i0p0008_d8", (1.7, 0.0008, 8.0), "higher P at previous I/D"),
    Variant("p1p7_i0p0008_d10", (1.7, 0.0008, 10.0), "higher P/D"),
    Variant("p1p7_i0p0012_d8", (1.7, 0.0012, 8.0), "higher I"),
    Variant("p1p9_i0p0005_d6", (1.9, 0.0005, 6.0), "aggressive P with lower I/D"),
    Variant("p1p9_i0p0008_d8", (1.9, 0.0008, 8.0), "aggressive P at previous I/D"),
    Variant("p2p0_i0p0008_d10", (2.0, 0.0008, 10.0), "upper aggressive candidate"),
)


LOW_GAIN16_VARIANTS = (
    Variant("p0p3_i0_d0p5", (0.3, 0.0, 0.5), "very low P/D baseline"),
    Variant("p0p3_i0_d1", (0.3, 0.0, 1.0), "very low P with more D"),
    Variant("p0p3_i0_d2", (0.3, 0.0, 2.0), "very low P upper D"),
    Variant("p0p5_i0_d1", (0.5, 0.0, 1.0), "low P/D"),
    Variant("p0p5_i0_d2", (0.5, 0.0, 2.0), "low P with more D"),
    Variant("p0p5_i0_d4", (0.5, 0.0, 4.0), "low P upper D"),
    Variant("p0p7_i0_d2", (0.7, 0.0, 2.0), "moderate-low P/D"),
    Variant("p0p7_i0_d4", (0.7, 0.0, 4.0), "moderate-low P upper D"),
    Variant("p0p7_i0p00005_d4", (0.7, 0.00005, 4.0), "moderate-low P with tiny I"),
    Variant("p0p9_i0_d2", (0.9, 0.0, 2.0), "near useful lower P"),
    Variant("p0p9_i0_d4", (0.9, 0.0, 4.0), "near useful lower P with more D"),
    Variant("p0p9_i0p0001_d4", (0.9, 0.0001, 4.0), "near useful lower P with I"),
    Variant("p1p1_i0_d4", (1.1, 0.0, 4.0), "low useful P without I"),
    Variant("p1p1_i0p0001_d6", (1.1, 0.0001, 6.0), "low useful P with I/D"),
    Variant("p1p3_i0_d6", (1.3, 0.0, 6.0), "coarse candidate without I"),
    Variant("p1p3_i0p0002_d8", (1.3, 0.0002, 8.0), "coarse candidate with I/D"),
)


LOW_GAIN_FINE_VARIANTS = (
    Variant("control_current_p1p9_i0p0008_d8", (1.9, 0.0008, 8.0), "current tuned value; control"),
    Variant("coarse_best_p1p3_i0p0002_d8", (1.3, 0.0002, 8.0), "best coarse low-gain candidate"),
    Variant("p1p1_i0p0001_d6", (1.1, 0.0001, 6.0), "second coarse candidate"),
    Variant("p1p2_i0p0001_d7", (1.2, 0.0001, 7.0), "fine lower P/D around coarse best"),
    Variant("p1p2_i0p0002_d8", (1.2, 0.0002, 8.0), "fine lower P at coarse I/D"),
    Variant("p1p2_i0p0003_d9", (1.2, 0.0003, 9.0), "fine lower P with more I/D"),
    Variant("p1p3_i0p0001_d7", (1.3, 0.0001, 7.0), "fine coarse P with less I/D"),
    Variant("p1p3_i0p0002_d9", (1.3, 0.0002, 9.0), "fine coarse P with more D"),
    Variant("p1p3_i0p0003_d8", (1.3, 0.0003, 8.0), "fine coarse P with more I"),
    Variant("p1p4_i0p0001_d8", (1.4, 0.0001, 8.0), "fine higher P with low I"),
    Variant("p1p4_i0p0002_d9", (1.4, 0.0002, 9.0), "fine higher P with coarse I and more D"),
    Variant("p1p4_i0p0003_d10", (1.4, 0.0003, 10.0), "fine higher P with more I/D"),
    Variant("p1p5_i0p0001_d8", (1.5, 0.0001, 8.0), "upper fine P with low I"),
    Variant("p1p5_i0p0002_d9", (1.5, 0.0002, 9.0), "upper fine P with coarse I and more D"),
    Variant("p1p5_i0p0003_d10", (1.5, 0.0003, 10.0), "upper fine P with more I/D"),
)


VARIANT_PRESETS = {
    "press": PRESS_VARIANTS,
    "low-gain16": LOW_GAIN16_VARIANTS,
    "low-gain-fine": LOW_GAIN_FINE_VARIANTS,
}


def pid_values_for(variant: Variant) -> tuple[float, ...]:
    values = list(BASELINE_PID)
    start = 3 * 3
    values[start:start + 3] = list(variant.eye_updown_pid)
    return tuple(values)


def pid_argument(values: tuple[float, ...]) -> str:
    return ",".join(f"{value:.9g}" for value in values)


def run_command(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=True)


def numeric_values(summaries: list[dict], key: str) -> list[float]:
    return [float(item[key]) for item in summaries if item.get(key) is not None]


def summarize_variant(label: str, output_json: Path, variant: Variant) -> dict:
    data = json.loads(output_json.read_text())
    summaries = data["summaries"]
    residual_rms = numeric_values(summaries, "residual_rms_deg")
    hold_rms = numeric_values(summaries, "hold_residual_rms_deg")
    hold_tail_p2p = numeric_values(summaries, "hold_tail_residual_peak_to_peak_deg")
    velocity_rms = numeric_values(summaries, "velocity_rms_deg_per_s")

    by_move_ticks: dict[str, dict] = {}
    for item in summaries:
        key = str(item["move_ticks"])
        by_move_ticks.setdefault(
            key,
            {
                "move_ticks": item["move_ticks"],
                "move_ms": item["move_ms"],
                "residual_rms_deg": [],
                "hold_residual_rms_deg": [],
                "hold_tail_residual_peak_to_peak_deg": [],
            },
        )
        for metric in (
            "residual_rms_deg",
            "hold_residual_rms_deg",
            "hold_tail_residual_peak_to_peak_deg",
        ):
            value = item.get(metric)
            if value is not None:
                by_move_ticks[key][metric].append(float(value))

    for bucket in by_move_ticks.values():
        for metric in (
            "residual_rms_deg",
            "hold_residual_rms_deg",
            "hold_tail_residual_peak_to_peak_deg",
        ):
            values = bucket[metric]
            bucket[f"mean_{metric}"] = mean(values) if values else None
            del bucket[metric]

    mean_residual = mean(residual_rms) if residual_rms else None
    mean_tail = mean(hold_tail_p2p) if hold_tail_p2p else None
    mean_hold = mean(hold_rms) if hold_rms else None
    max_tail = max(hold_tail_p2p) if hold_tail_p2p else None
    score = None
    if mean_residual is not None and mean_tail is not None and mean_hold is not None:
        # Lower is better. Low-gain pass prioritizes stability; tracking error is secondary.
        score = 0.20 * mean_residual + 0.45 * mean_tail + 0.35 * mean_hold

    return {
        "label": label,
        "eye_updown_pid": {
            "p": variant.eye_updown_pid[0],
            "i": variant.eye_updown_pid[1],
            "d": variant.eye_updown_pid[2],
        },
        "rationale": variant.rationale,
        "output_json": str(output_json),
        "output_csv": str(output_json.with_name(f"{label}_samples.csv")),
        "mean_residual_rms_deg": mean_residual,
        "mean_hold_residual_rms_deg": mean_hold,
        "mean_hold_tail_residual_p2p_deg": mean_tail,
        "max_hold_tail_residual_p2p_deg": max_tail,
        "mean_velocity_rms_deg_per_s": mean(velocity_rms) if velocity_rms else None,
        "score": score,
        "by_move_ticks": list(sorted(by_move_ticks.values(), key=lambda item: item["move_ticks"])),
    }


def write_markdown_report(path: Path, summary: dict) -> None:
    ranked = sorted(summary["variants"], key=lambda item: item["score"] if item["score"] is not None else float("inf"))
    lines = [
        "# Eye Up/Down PID Sweep",
        "",
        f"Captured: `{summary['captured_at_utc']}`",
        "",
        "Lower score is better: `0.20 * mean_residual_rms + 0.45 * mean_hold_tail_residual_p2p + 0.35 * mean_hold_residual_rms`.",
        "",
        "| Rank | Variant | P | I | D | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg | Max hold tail p2p deg | Score |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, item in enumerate(ranked, start=1):
        pid = item["eye_updown_pid"]
        lines.append(
            "| "
            f"{rank} | {item['label']} | {pid['p']:.6g} | {pid['i']:.6g} | {pid['d']:.6g} | "
            f"{item['mean_residual_rms_deg']:.3f} | {item['mean_hold_residual_rms_deg']:.3f} | "
            f"{item['mean_hold_tail_residual_p2p_deg']:.3f} | {item['max_hold_tail_residual_p2p_deg']:.3f} | "
            f"{item['score']:.3f} |"
        )

    lines.extend(["", "## Per Speed", ""])
    for item in summary["variants"]:
        pid = item["eye_updown_pid"]
        lines.extend(
            [
                f"### {item['label']}  P={pid['p']:.6g} I={pid['i']:.6g} D={pid['d']:.6g}",
                "",
                "| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |",
                "|---:|---:|---:|---:|---:|",
            ]
        )
        for bucket in item["by_move_ticks"]:
            lines.append(
                "| "
                f"{bucket['move_ticks']} | {bucket['move_ms']:.0f} | "
                f"{bucket['mean_residual_rms_deg']:.3f} | "
                f"{bucket['mean_hold_residual_rms_deg']:.3f} | "
                f"{bucket['mean_hold_tail_residual_peak_to_peak_deg']:.3f} |"
            )
        lines.append("")

    best = ranked[0]
    pid = best["eye_updown_pid"]
    lines.extend(
        [
            "## Recommendation",
            "",
            f"Best measured candidate: `{best['label']}` with `P={pid['p']:.6g}, I={pid['i']:.6g}, D={pid['d']:.6g}`.",
            "",
            "Use this as the next visual test point, then tune around it with smaller steps.",
            "",
        ]
    )
    path.write_text("\n".join(lines))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run eye_updown PID variants through the jitter benchmark.")
    parser.add_argument("--robot-motion", default="/home/pi/robot_motion.py")
    parser.add_argument("--benchmark", default="/home/pi/eye_updown_jitter_benchmark.py")
    parser.add_argument("--output-dir", type=Path, default=Path("/home/pi/dumps/eye_updown_pid_sweep"))
    parser.add_argument("--move-ticks", default="6,10,18,32")
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--hold-ticks", type=int, default=10)
    parser.add_argument("--settle", type=float, default=0.1)
    parser.add_argument("--listen-after", type=float, default=0.1)
    parser.add_argument(
        "--preset",
        choices=sorted(VARIANT_PRESETS),
        default="low-gain-fine",
        help="PID variant preset to sweep.",
    )
    parser.add_argument("--read-back", action="store_true", help="Read PID after each write. Slower, but verifies board state.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    variants = VARIANT_PRESETS[args.preset]
    variants_summary: list[dict] = []
    for index, variant in enumerate(variants, start=1):
        print(f"[{index}/{len(variants)}] write PID {variant.label}: {variant.eye_updown_pid}", flush=True)
        values = pid_values_for(variant)
        write_result = run_command(["python3", args.robot_motion, "--write-pid", pid_argument(values), "--verbose"])
        (args.output_dir / f"{variant.label}_write_pid.log").write_text(write_result.stdout + write_result.stderr)

        if args.read_back:
            time.sleep(0.2)
            read_result = run_command(["python3", args.robot_motion, "--read-pid", "--verbose"])
            (args.output_dir / f"{variant.label}_read_pid.log").write_text(read_result.stdout + read_result.stderr)

        output_json = args.output_dir / f"{variant.label}.json"
        output_csv = args.output_dir / f"{variant.label}_samples.csv"
        print(f"[{index}/{len(variants)}] benchmark {variant.label}", flush=True)
        benchmark_cmd = [
            "python3",
            args.benchmark,
            "--move-ticks", args.move_ticks,
            "--cycles", str(args.cycles),
            "--repeats", str(args.repeats),
            "--hold-ticks", str(args.hold_ticks),
            "--settle", str(args.settle),
            "--listen-after", str(args.listen_after),
            "--output", str(output_json),
            "--csv", str(output_csv),
        ]
        benchmark = run_command(benchmark_cmd)
        (args.output_dir / f"{variant.label}_benchmark.log").write_text(benchmark.stdout + benchmark.stderr)
        print(benchmark.stdout, end="", flush=True)

        variants_summary.append(summarize_variant(variant.label, output_json, variant))

    summary = {
        "kind": "eye_updown_pid_sweep",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark_parameters": {
            "preset": args.preset,
            "move_ticks": args.move_ticks,
            "cycles": args.cycles,
            "repeats": args.repeats,
            "hold_ticks": args.hold_ticks,
            "settle": args.settle,
            "listen_after": args.listen_after,
            "read_back": args.read_back,
        },
        "variants": variants_summary,
    }
    summary_json = args.output_dir / "summary.json"
    summary_md = args.output_dir / "report.md"
    summary_json.write_text(json.dumps(summary, indent=2, sort_keys=False) + "\n")
    write_markdown_report(summary_md, summary)
    print(f"wrote {summary_json}")
    print(f"wrote {summary_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
