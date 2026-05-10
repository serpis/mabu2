#!/usr/bin/env python3
"""Bake all quiz speech variants to WAV files on the host machine."""
from __future__ import annotations

import argparse
from itertools import combinations
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any

from dialog_state import (
    QuizConfig,
    QuizQuestion,
    format_name_list,
    load_quiz_config,
    player_subset_speech_key,
    question_speech_key,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_TTS_DIR = Path("/Users/serp/code/20260508-tts")
DEFAULT_REF_TEXT = (
    "Dagens fråga. En tidning har gjort en rundfråga. "
    "Så här svarade några på de här frågorna."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiz-file", default=str(ROOT / "quiz" / "robot_quiz.yaml"))
    parser.add_argument("--quiz-runtime-file", default=str(ROOT / "quiz" / "robot_quiz_runtime.json"))
    parser.add_argument("--quiz-teams-file", default=str(ROOT / "quiz" / "robot_quiz_teams.json"))
    parser.add_argument(
        "--speech-file",
        default=str(ROOT / "quiz" / "robot_quiz_baked_speech.json"),
    )
    parser.add_argument("--sound-dir", default=str(ROOT / "sound"))
    parser.add_argument("--tts-dir", default=str(DEFAULT_TTS_DIR))
    parser.add_argument("--tts-python", default=None)
    parser.add_argument("--ref-audio", default=str(DEFAULT_TTS_DIR / "dagens_fraga.wav"))
    parser.add_argument("--ref-text", default=DEFAULT_REF_TEXT)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--dtype", default="float16")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def non_empty_subsets(values: tuple[str, ...]) -> list[tuple[str, ...]]:
    return [
        tuple(item)
        for size in range(1, len(values) + 1)
        for item in combinations(values, size)
    ]


def clip_name(prefix: str, *parts: str) -> str:
    suffix = "_".join(slug(part) for part in parts if part)
    if suffix:
        return f"{prefix}_{suffix}.wav"
    return f"{prefix}.wav"


def slug(value: str) -> str:
    value = value.lower().replace("å", "a").replace("ä", "a").replace("ö", "o")
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value or "clip"


def question_prompt_text(index: int, question: QuizQuestion) -> str:
    parts = [f"Fråga {index + 1}.", question.text]
    if question.choices:
        choice_texts = [
            f"Svar {key}: {value}."
            for key, value in sorted(question.choices.items())
        ]
        parts.extend(choice_texts)
    return " ".join(part for part in parts if part)


def question_result_text(question: QuizQuestion) -> str:
    answer = question.correct
    label = question.choices.get(answer)
    if label:
        return f"Rätt svar var {answer}: {label}."
    return f"Rätt svar var {answer}."


def registration_text(config: QuizConfig, player_ids: tuple[str, ...]) -> str:
    names = [config.player_name(player_id) for player_id in player_ids]
    return f"Anmälda lag är {format_name_list(names)}. Då kör vi."


def nudge_text(config: QuizConfig, player_ids: tuple[str, ...]) -> str:
    names = [config.player_name(player_id) for player_id in player_ids]
    if len(names) == 1:
        return f"Aha, men {names[0]} då, vad svarar ni?"
    return f"Aha, men {format_name_list(names)} då, vad svarar ni?"


def final_text(config: QuizConfig, player_ids: tuple[str, ...]) -> str:
    names = [config.player_name(player_id) for player_id in player_ids]
    if len(names) == 1:
        return f"Quizzet är slut. Vinnare är {names[0]}."
    return f"Quizzet är slut. Det blev oavgjort mellan {format_name_list(names)}."


def add_item(
    items: list[dict[str, str]],
    speech_clips: dict[str, str],
    *,
    key: str,
    clip: str,
    text: str,
) -> None:
    speech_clips[key] = clip
    items.append({"key": key, "clip": clip, "text": text})


def build_bake_items(config: QuizConfig) -> tuple[list[dict[str, str]], dict[str, str]]:
    items: list[dict[str, str]] = []
    speech_clips: dict[str, str] = {}

    add_item(
        items,
        speech_clips,
        key="registration_prompt",
        clip="quiz_registration_prompt.wav",
        text="Nu börjar quizzet. Håll upp en av era svarsskyltar för att anmäla laget.",
    )
    add_item(
        items,
        speech_clips,
        key="registration_waiting",
        clip="quiz_registration_waiting.wav",
        text="Jag väntar på minst ett lag. Håll upp en svarsskylt för att vara med.",
    )

    player_ids = config.player_ids
    for subset in non_empty_subsets(player_ids):
        add_item(
            items,
            speech_clips,
            key=player_subset_speech_key("registered", subset),
            clip=clip_name("quiz_registered", *subset),
            text=registration_text(config, subset),
        )
        add_item(
            items,
            speech_clips,
            key=player_subset_speech_key("nudge", subset),
            clip=clip_name("quiz_nudge", *subset),
            text=nudge_text(config, subset),
        )
        add_item(
            items,
            speech_clips,
            key=player_subset_speech_key("final", subset),
            clip=clip_name("quiz_final", *subset),
            text=final_text(config, subset),
        )

    for index, question in enumerate(config.questions):
        prompt_clip = question.prompt_clip or clip_name("quiz_question", f"{index + 1:02d}")
        add_item(
            items,
            speech_clips,
            key=question_speech_key(index),
            clip=prompt_clip,
            text=question_prompt_text(index, question),
        )
        result_clip = question.result_clip or clip_name("quiz_question", f"{index + 1:02d}", "result")
        add_item(
            items,
            speech_clips,
            key=question_speech_key(index, "result"),
            clip=result_clip,
            text=question_result_text(question),
        )

    return items, speech_clips


def write_speech_file(
    path: Path,
    *,
    config: QuizConfig,
    sound_dir: Path,
    items: list[dict[str, str]],
    speech_clips: dict[str, str],
) -> None:
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "quiz": config.name,
        "sound_dir": str(sound_dir),
        "speech_clips": speech_clips,
        "items": items,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def run_tts_batch(
    *,
    tts_python: Path,
    tts_dir: Path,
    jobs: list[dict[str, str]],
    ref_audio: Path,
    ref_text: str,
    device: str,
    dtype: str,
) -> None:
    code = r'''
import json
from pathlib import Path
import sys

from omnivoice import OmniVoice
import soundfile as sf
import torch

jobs_path = Path(sys.argv[1])
ref_audio = sys.argv[2]
ref_text = sys.argv[3]
device = sys.argv[4]
dtype_name = sys.argv[5]

dtype = getattr(torch, dtype_name)
model = OmniVoice.from_pretrained(
    "k2-fsa/OmniVoice",
    device_map=device,
    dtype=dtype,
)

jobs = json.loads(jobs_path.read_text())
for index, job in enumerate(jobs, start=1):
    out_path = Path(job["out"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[{index}/{len(jobs)}] {job['clip']}", flush=True)
    audio = model.generate(
        text=job["text"],
        ref_audio=ref_audio,
        ref_text=ref_text,
    )
    sf.write(str(out_path), audio[0], 24000)
'''
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(jobs, handle, ensure_ascii=False)
        jobs_path = Path(handle.name)
    try:
        subprocess.run(
            [str(tts_python), "-c", code, str(jobs_path), str(ref_audio), ref_text, device, dtype],
            cwd=str(tts_dir),
            check=True,
        )
    finally:
        jobs_path.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    sound_dir = Path(args.sound_dir)
    tts_dir = Path(args.tts_dir)
    tts_python = Path(args.tts_python) if args.tts_python else tts_dir / ".venv" / "bin" / "python"

    config = load_quiz_config(
        args.quiz_file,
        runtime_path=args.quiz_runtime_file,
        teams_path=args.quiz_teams_file,
    )
    items, speech_clips = build_bake_items(config)
    speech_file = Path(args.speech_file)
    write_speech_file(
        speech_file,
        config=config,
        sound_dir=sound_dir,
        items=items,
        speech_clips=speech_clips,
    )

    jobs: list[dict[str, str]] = []
    for item in items:
        out_path = sound_dir / item["clip"]
        if out_path.exists() and not args.force:
            continue
        jobs.append({**item, "out": str(out_path)})

    print(f"wrote {speech_file}")
    print(f"{len(items)} speech variants, {len(jobs)} wav files to generate")
    if args.dry_run or not jobs:
        return 0
    if not tts_python.exists():
        raise SystemExit(f"TTS python not found: {tts_python}")
    run_tts_batch(
        tts_python=tts_python,
        tts_dir=tts_dir,
        jobs=jobs,
        ref_audio=Path(args.ref_audio),
        ref_text=args.ref_text,
        device=args.device,
        dtype=args.dtype,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
