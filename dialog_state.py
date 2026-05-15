#!/usr/bin/env python3
"""Small dialog/quiz state model for camera-driven robot interactions."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Protocol


APP_MODE_ACTIVE = "active"
APP_MODE_IDLE = "idle"
APP_MODE_QUIZ = "quiz"
APP_MODES = {APP_MODE_ACTIVE, APP_MODE_IDLE, APP_MODE_QUIZ}


@dataclass(frozen=True)
class PlayerConfig:
    player_id: str
    name: str
    answers: dict[str, int]


@dataclass(frozen=True)
class QuizQuestion:
    text: str
    correct: str
    choices: dict[str, str] = field(default_factory=dict)
    prompt_clip: str | None = None
    result_clip: str | None = None


@dataclass(frozen=True)
class QuizConfig:
    name: str
    start_marker_id: int | None
    players: tuple[PlayerConfig, ...]
    questions: tuple[QuizQuestion, ...]
    stable_start_s: float = 0.8
    stable_registration_s: float = 0.8
    registration_timeout_s: float = 5.0
    answer_memory_s: float = 3.0
    stable_answer_s: float = 1.0
    initial_timeout_s: float = 5.0
    nudge_timeout_s: float = 4.0
    after_prompt_delay_s: float = 1.0
    next_question_delay_s: float = 1.0
    default_next_mode: str = APP_MODE_IDLE
    speech_clips: dict[str, str] = field(default_factory=dict)

    @property
    def player_ids(self) -> tuple[str, ...]:
        return tuple(player.player_id for player in self.players)

    def player_name(self, player_id: str) -> str:
        for player in self.players:
            if player.player_id == player_id:
                return player.name
        return player_id


@dataclass(frozen=True)
class MarkerObservation:
    marker_id: int
    track_id: int | None
    center: tuple[float, float] | None
    center_norm: tuple[float, float] | None
    first_seen_at: float
    last_seen_at: float
    stable_since: float

    def stable_for_s(self, now: float) -> float:
        return max(0.0, now - self.stable_since)


@dataclass(frozen=True)
class WorldState:
    updated_at: float
    markers: dict[int, MarkerObservation]
    decoded_answers: dict[str, str]
    start_marker_id: int | None = None

    def marker_stable(self, marker_id: int | None, *, stable_for_s: float, now: float | None = None) -> bool:
        if marker_id is None:
            return False
        marker = self.markers.get(marker_id)
        if marker is None:
            return False
        sample_time = self.updated_at if now is None else now
        return marker.stable_for_s(sample_time) >= stable_for_s

    def visible_marker_ids(self) -> list[int]:
        return sorted(self.markers)

    def stable_marker_ids(self, *, stable_for_s: float, now: float | None = None) -> list[int]:
        sample_time = self.updated_at if now is None else now
        return [
            marker_id
            for marker_id, marker in sorted(self.markers.items())
            if marker.stable_for_s(sample_time) >= stable_for_s
        ]

    def as_debug(self, *, stable_for_s: float) -> dict[str, Any]:
        return {
            "visible_marker_ids": self.visible_marker_ids(),
            "stable_marker_ids": self.stable_marker_ids(stable_for_s=stable_for_s),
            "decoded_answers": dict(self.decoded_answers),
            "start_marker_id": self.start_marker_id,
            "start_marker_stable": self.marker_stable(
                self.start_marker_id,
                stable_for_s=stable_for_s,
            ),
        }


class RobotDialogPort(Protocol):
    def speak_to_group(self, clip: str | None) -> dict[str, Any]:
        ...

    def is_speaking(self) -> bool:
        ...


class ObservationDecoder:
    def __init__(self, config: QuizConfig) -> None:
        self.config = config
        self.answer_by_marker_id: dict[int, tuple[str, str]] = {}
        for player in config.players:
            for answer, marker_id in player.answers.items():
                self.answer_by_marker_id[int(marker_id)] = (player.player_id, answer)

    def decode_answers(self, markers: dict[int, MarkerObservation]) -> dict[str, str]:
        answers = {}
        for marker_id in sorted(markers):
            player_answer = self.answer_by_marker_id.get(marker_id)
            if player_answer is None:
                continue
            player_id, answer = player_answer
            answers[player_id] = answer
        return answers


class WorldStateBuilder:
    def __init__(self, decoder: ObservationDecoder) -> None:
        self.decoder = decoder
        self.previous_markers: dict[int, MarkerObservation] = {}

    def update_from_marker_dicts(self, marker_dicts: list[dict[str, Any]], now: float) -> WorldState:
        markers: dict[int, MarkerObservation] = {}
        for item in marker_dicts:
            if not item.get("visible", True):
                continue
            marker_id = item.get("marker_id")
            if not isinstance(marker_id, int):
                continue
            track_id = item.get("id")
            if not isinstance(track_id, int):
                track_id = None
            center = _point_tuple(item.get("center"))
            center_norm = _point_tuple(item.get("center_norm"))
            previous = self.previous_markers.get(marker_id)
            if previous is not None and previous.track_id == track_id:
                first_seen_at = previous.first_seen_at
                stable_since = previous.stable_since
            else:
                first_seen_at = now
                stable_since = now
            markers[marker_id] = MarkerObservation(
                marker_id=marker_id,
                track_id=track_id,
                center=center,
                center_norm=center_norm,
                first_seen_at=first_seen_at,
                last_seen_at=now,
                stable_since=stable_since,
            )
        self.previous_markers = markers
        return WorldState(
            updated_at=now,
            markers=markers,
            decoded_answers=self.decoder.decode_answers(markers),
            start_marker_id=self.decoder.config.start_marker_id,
        )


class StableAnswerTracker:
    def __init__(
        self,
        player_ids: tuple[str, ...],
        *,
        stable_for_s: float,
        answer_memory_s: float,
    ) -> None:
        self.player_ids = player_ids
        self.stable_for_s = stable_for_s
        self.answer_memory_s = answer_memory_s
        self.last_seen: dict[str, dict[str, float]] = {}
        self.first_seen: dict[str, tuple[str, float]] = {}
        self.locked: dict[str, str | None] = {}

    def update(self, observations: dict[str, str], now: float) -> None:
        for player_id, answer in observations.items():
            if player_id not in self.player_ids or player_id in self.locked:
                continue
            self.last_seen.setdefault(player_id, {})[answer] = now

        for player_id in self.player_ids:
            if player_id in self.locked:
                continue
            answer = self.current_answer(player_id, now)
            if answer is None:
                self.first_seen.pop(player_id, None)
                continue
            previous = self.first_seen.get(player_id)
            if previous is None or previous[0] != answer:
                self.first_seen[player_id] = (answer, now)
                continue
            if now - previous[1] >= self.stable_for_s:
                self.locked[player_id] = answer

    def current_answer(self, player_id: str, now: float) -> str | None:
        seen = self.last_seen.get(player_id)
        if not seen:
            return None
        cutoff = now - self.answer_memory_s
        recent = {
            answer: last_seen_at
            for answer, last_seen_at in seen.items()
            if last_seen_at >= cutoff
        }
        if recent != seen:
            if recent:
                self.last_seen[player_id] = recent
            else:
                self.last_seen.pop(player_id, None)
        if not recent:
            return None
        latest_seen_at = max(recent.values())
        latest_answers = [
            answer
            for answer, last_seen_at in recent.items()
            if last_seen_at == latest_seen_at
        ]
        if len(latest_answers) != 1:
            return None
        return latest_answers[0]

    def current_answers(self, now: float) -> dict[str, str]:
        return {
            player_id: answer
            for player_id in self.player_ids
            if (answer := self.current_answer(player_id, now)) is not None
        }

    def missing_players(self) -> list[str]:
        return [player_id for player_id in self.player_ids if player_id not in self.locked]

    def all_locked(self) -> bool:
        return not self.missing_players()

    def lock_missing_as_none(self) -> None:
        for player_id in self.missing_players():
            self.locked[player_id] = None

    def locked_answers(self) -> dict[str, str | None]:
        return {player_id: self.locked.get(player_id) for player_id in self.player_ids}


class StablePresenceTracker:
    def __init__(self, player_ids: tuple[str, ...], *, stable_for_s: float) -> None:
        self.player_ids = player_ids
        self.stable_for_s = stable_for_s
        self.first_seen: dict[str, float] = {}
        self.locked: set[str] = set()

    def update(self, observations: dict[str, str], now: float) -> None:
        for player_id in self.player_ids:
            if player_id in self.locked:
                continue
            if player_id not in observations:
                self.first_seen.pop(player_id, None)
                continue
            first_seen = self.first_seen.get(player_id)
            if first_seen is None:
                self.first_seen[player_id] = now
                continue
            if now - first_seen >= self.stable_for_s:
                self.locked.add(player_id)

    def registered_player_ids(self) -> tuple[str, ...]:
        return tuple(player_id for player_id in self.player_ids if player_id in self.locked)


def speech_key(prefix: str, *parts: str) -> str:
    return "__".join([prefix, *parts])


def player_subset_speech_key(prefix: str, player_ids: tuple[str, ...] | list[str]) -> str:
    return speech_key(prefix, *player_ids)


def question_speech_key(question_index: int, suffix: str | None = None) -> str:
    key = f"question_{question_index + 1:02d}"
    if suffix:
        return speech_key(key, suffix)
    return key


class QuizSession:
    def __init__(self, config: QuizConfig) -> None:
        self.config = config
        self.running = False
        self.phase = "stopped"
        self.phase_started_at = 0.0
        self.question_index = -1
        self.registered_player_ids: tuple[str, ...] = ()
        self.registration_first_player_at: float | None = None
        self.scores: dict[str, int] = {}
        self.registration_tracker: StablePresenceTracker | None = None
        self.registration_prompt_played = False
        self.registration_waiting_played = False
        self.pending_final_after_result = False
        self.answer_tracker: StableAnswerTracker | None = None
        self.locked_answers: dict[str, str | None] = {}
        self.missing_players: list[str] = []
        self.last_event: str | None = None
        self.last_error: str | None = None
        self.nudge_text: str | None = None

    def start(self, now: float) -> None:
        self.running = True
        self.phase = "register_players"
        self.phase_started_at = now
        self.question_index = -1
        self.registered_player_ids = ()
        self.registration_first_player_at = None
        self.scores = {}
        self.registration_tracker = StablePresenceTracker(
            self.config.player_ids,
            stable_for_s=self.config.stable_registration_s,
        )
        self.registration_prompt_played = False
        self.registration_waiting_played = False
        self.pending_final_after_result = False
        self.answer_tracker = None
        self.locked_answers = {}
        self.missing_players = []
        self.last_event = "started"
        self.last_error = None
        self.nudge_text = None

    def stop(self, now: float, *, phase: str = "stopped") -> None:
        self.running = False
        self.phase = phase
        self.phase_started_at = now
        self.registration_first_player_at = None
        self.registration_tracker = None
        self.pending_final_after_result = False
        self.answer_tracker = None
        self.last_event = phase

    def reset(self, now: float) -> None:
        self.stop(now, phase="stopped")
        self.question_index = -1
        self.registered_player_ids = ()
        self.registration_first_player_at = None
        self.scores = {}
        self.locked_answers = {}
        self.missing_players = []
        self.last_error = None
        self.nudge_text = None

    def update(self, world: WorldState, robot: RobotDialogPort, now: float) -> None:
        if not self.running:
            return
        if not self.config.questions:
            self.stop(now, phase="complete")
            self.last_error = "quiz has no questions"
            return

        if self.phase == "register_players":
            if not self.registration_prompt_played:
                self._speak_key(robot, "registration_prompt")
                self.registration_prompt_played = True
            if self.registration_tracker is None:
                self.registration_tracker = StablePresenceTracker(
                    self.config.player_ids,
                    stable_for_s=self.config.stable_registration_s,
                )
            self.registration_tracker.update(world.decoded_answers, now)
            self.registered_player_ids = self.registration_tracker.registered_player_ids()
            if self.registered_player_ids:
                if self.registration_first_player_at is None:
                    self.registration_first_player_at = now
                self.scores = {player_id: 0 for player_id in self.registered_player_ids}
            self.missing_players = [
                player_id
                for player_id in self.config.player_ids
                if player_id not in self.registered_player_ids
            ]
            if (
                self.registered_player_ids
                and self.registration_first_player_at is not None
                and now - self.registration_first_player_at >= self.config.registration_timeout_s
            ):
                self.registration_tracker = None
                self.locked_answers = {}
                self.missing_players = []
                self.nudge_text = None
                if self._speak_key(
                    robot,
                    player_subset_speech_key("registered", self.registered_player_ids),
                ):
                    self._set_phase("speaking_registration_complete", now)
                else:
                    self._set_phase("start_question", now)
            elif not self.registered_player_ids:
                self.nudge_text = "Vantar pa minst ett lag"
                if (
                    not self.registration_waiting_played
                    and now - self.phase_started_at >= self.config.registration_timeout_s
                    and not robot.is_speaking()
                ):
                    self._speak_key(robot, "registration_waiting")
                    self.registration_waiting_played = True
            return

        if self.phase == "speaking_registration_complete":
            if not robot.is_speaking():
                self._set_phase("start_question", now)
            return

        if self.phase == "start_question":
            if not self.registered_player_ids:
                self.registration_tracker = StablePresenceTracker(
                    self.config.player_ids,
                    stable_for_s=self.config.stable_registration_s,
                )
                self._set_phase("register_players", now)
                return
            self.question_index += 1
            if self.question_index >= len(self.config.questions):
                self._finish(now)
                return
            self.locked_answers = {}
            self.missing_players = []
            self.nudge_text = None
            self.answer_tracker = StableAnswerTracker(
                self.registered_player_ids,
                stable_for_s=self.config.stable_answer_s,
                answer_memory_s=self.config.answer_memory_s,
            )
            self._speak_question(robot)
            self._set_phase("speaking_question", now)
            return

        if self.phase == "speaking_question":
            if not robot.is_speaking():
                self._set_phase("settle_after_speech", now)
            return

        if self.phase == "settle_after_speech":
            if now - self.phase_started_at >= self.config.after_prompt_delay_s:
                self._set_phase("accept_answers", now)
            return

        if self.phase == "accept_answers":
            if self.answer_tracker is None:
                self._set_phase("start_question", now)
                return
            self.answer_tracker.update(world.decoded_answers, now)
            self.locked_answers = self.answer_tracker.locked_answers()
            self.missing_players = self.answer_tracker.missing_players()
            if self.answer_tracker.all_locked():
                self._score_current_question(now, robot)
                return
            if now - self.phase_started_at >= self.config.initial_timeout_s:
                self.nudge_text = self._missing_prompt(self.missing_players)
                self._speak_key(robot, player_subset_speech_key("nudge", self.missing_players))
                self._set_phase("nudge_missing", now)
            return

        if self.phase == "nudge_missing":
            if self.answer_tracker is not None:
                self.answer_tracker.update(world.decoded_answers, now)
                self.locked_answers = self.answer_tracker.locked_answers()
                self.missing_players = self.answer_tracker.missing_players()
                if self.answer_tracker.all_locked():
                    self._score_current_question(now, robot)
                    return
            if now - self.phase_started_at >= self.config.nudge_timeout_s:
                if self.answer_tracker is not None:
                    self.answer_tracker.lock_missing_as_none()
                    self.locked_answers = self.answer_tracker.locked_answers()
                    self.missing_players = []
                self._score_current_question(now, robot)
            return

        if self.phase == "speaking_result":
            if robot.is_speaking():
                return
            if self.pending_final_after_result:
                self._speak_final(robot, now)
            else:
                self._set_phase("between_questions", now)
            return

        if self.phase == "speaking_final":
            if not robot.is_speaking():
                self._finish(now)
            return

        if self.phase == "between_questions":
            if now - self.phase_started_at >= self.config.next_question_delay_s:
                self._set_phase("start_question", now)
            return

    def _speak_question(self, robot: RobotDialogPort) -> None:
        question = self.current_question()
        if question is None:
            return
        clip = question.prompt_clip or self._speech_clip(question_speech_key(self.question_index))
        if not clip:
            return
        result = robot.speak_to_group(clip)
        if result.get("ok") is False:
            self.last_error = str(result.get("error") or result.get("status") or "sound failed")

    def _score_current_question(self, now: float, robot: RobotDialogPort) -> None:
        question = self.current_question()
        if question is None:
            self._finish(now)
            return
        for player_id, answer in self.locked_answers.items():
            if answer == question.correct:
                self.scores[player_id] = self.scores.get(player_id, 0) + 1
        self.last_event = "scored_question"
        self.pending_final_after_result = self.question_index >= len(self.config.questions) - 1
        clip = question.result_clip or self._speech_clip(
            question_speech_key(self.question_index, "result")
        )
        if clip:
            result = robot.speak_to_group(clip)
            if result.get("ok") is False:
                self.last_error = str(result.get("error") or result.get("status") or "sound failed")
            self._set_phase("speaking_result", now)
            return
        if self.pending_final_after_result:
            self._speak_final(robot, now)
        else:
            self._set_phase("between_questions", now)

    def _speak_final(self, robot: RobotDialogPort, now: float) -> None:
        self.pending_final_after_result = False
        clip = self._speech_clip(player_subset_speech_key("final", tuple(self.winner_ids())))
        if not clip:
            self._finish(now)
            return
        result = robot.speak_to_group(clip)
        if result.get("ok") is False:
            self.last_error = str(result.get("error") or result.get("status") or "sound failed")
        self._set_phase("speaking_final", now)

    def _finish(self, now: float) -> None:
        self.running = False
        self.phase = "complete"
        self.phase_started_at = now
        self.last_event = "complete"

    def _set_phase(self, phase: str, now: float) -> None:
        self.phase = phase
        self.phase_started_at = now

    def current_question(self) -> QuizQuestion | None:
        if 0 <= self.question_index < len(self.config.questions):
            return self.config.questions[self.question_index]
        return None

    def _missing_prompt(self, player_ids: list[str]) -> str | None:
        if not player_ids:
            return None
        names = [self.config.player_name(player_id) for player_id in player_ids]
        if len(names) == 1:
            return f"Aha, men {names[0]} då, vad svarar ni?"
        return f"Aha, men {format_name_list(names)} då, vad svarar ni?"

    def _speech_clip(self, key: str) -> str | None:
        clip = self.config.speech_clips.get(key)
        return clip or None

    def _speak_key(self, robot: RobotDialogPort, key: str) -> bool:
        clip = self._speech_clip(key)
        if not clip:
            return False
        result = robot.speak_to_group(clip)
        if result.get("ok") is False:
            self.last_error = str(result.get("error") or result.get("status") or "sound failed")
        return True

    def as_debug(self) -> dict[str, Any]:
        question = self.current_question()
        winner_ids = self.winner_ids() if self.phase == "complete" else []
        registered_names = [
            self.config.player_name(player_id) for player_id in self.registered_player_ids
        ]
        return {
            "running": self.running,
            "name": self.config.name,
            "phase": self.phase,
            "registration_open": self.phase == "register_players",
            "registered_player_ids": list(self.registered_player_ids),
            "registered_player_names": registered_names,
            "registered_count": len(self.registered_player_ids),
            "player_count": len(self.config.player_ids),
            "registration_first_player_at": self.registration_first_player_at,
            "question_index": self.question_index if question is not None else None,
            "question_count": len(self.config.questions),
            "question_text": question.text if question is not None else None,
            "correct": question.correct if question is not None else None,
            "accepting_answers": self.phase in {"accept_answers", "nudge_missing"},
            "locked_answers": self.locked_answers,
            "missing_players": self.missing_players,
            "nudge_text": self.nudge_text,
            "scores": dict(self.scores),
            "winner_ids": winner_ids,
            "winner_names": [self.config.player_name(player_id) for player_id in winner_ids],
            "last_event": self.last_event,
            "last_error": self.last_error,
        }

    def winner_ids(self) -> list[str]:
        if not self.scores:
            return []
        best_score = max(self.scores.values())
        return [player_id for player_id, score in self.scores.items() if score == best_score]


class IdleBehavior:
    def __init__(self, config: QuizConfig) -> None:
        self.config = config
        self.phase = "watching_start_marker"

    def update(self, world: WorldState, now: float) -> str | None:
        if world.marker_stable(
            self.config.start_marker_id,
            stable_for_s=self.config.stable_start_s,
            now=now,
        ):
            return "start_quiz"
        return None


class AppController:
    def __init__(
        self,
        config: QuizConfig,
        *,
        mode: str = APP_MODE_ACTIVE,
        auto_start_quiz: bool = True,
    ) -> None:
        if mode not in APP_MODES:
            mode = APP_MODE_ACTIVE
        self.config = config
        self.mode = mode
        self.auto_start_quiz = auto_start_quiz
        self.idle = IdleBehavior(config)
        self.quiz = QuizSession(config)
        self.last_event: str | None = None

    def set_mode(self, mode: str, now: float) -> None:
        if mode not in APP_MODES:
            raise ValueError(f"app mode must be one of: {', '.join(sorted(APP_MODES))}")
        self.mode = mode
        self.last_event = f"mode:{mode}"
        if mode != APP_MODE_QUIZ and self.quiz.running:
            self.quiz.stop(now)
        if mode == APP_MODE_QUIZ and not self.quiz.running:
            self.quiz.start(now)

    def start_quiz(self, now: float) -> None:
        self.mode = APP_MODE_QUIZ
        self.quiz.start(now)
        self.last_event = "start_quiz"

    def stop_quiz(self, now: float, *, next_mode: str | None = None) -> None:
        self.quiz.stop(now)
        self.mode = next_mode or self.config.default_next_mode
        if self.mode not in APP_MODES:
            self.mode = APP_MODE_IDLE
        self.last_event = "stop_quiz"

    def reset_quiz(self, now: float) -> None:
        self.quiz.reset(now)
        self.last_event = "reset_quiz"

    def update(self, world: WorldState, robot: RobotDialogPort, now: float) -> None:
        if self.mode == APP_MODE_IDLE and self.auto_start_quiz:
            event = self.idle.update(world, now)
            if event == "start_quiz":
                self.start_quiz(now)
        if self.mode == APP_MODE_QUIZ:
            self.quiz.update(world, robot, now)
            if not self.quiz.running and self.quiz.phase == "complete":
                self.last_event = "quiz_complete"

    def should_run_active_follow(self) -> bool:
        return self.mode == APP_MODE_ACTIVE

    def marker_detection_required(self, target_mode: str) -> bool:
        return target_mode == "markers" or self.mode in {APP_MODE_IDLE, APP_MODE_QUIZ} or self.auto_start_quiz

    def as_debug(self) -> dict[str, Any]:
        phase = "tracking"
        if self.mode == APP_MODE_IDLE:
            phase = self.idle.phase
        elif self.mode == APP_MODE_QUIZ:
            phase = self.quiz.phase
        return {
            "mode": self.mode,
            "phase": phase,
            "auto_start_quiz": self.auto_start_quiz,
            "last_event": self.last_event,
        }


def load_quiz_config(
    path: str | Path,
    *,
    runtime_path: str | Path | None = None,
    teams_path: str | Path | None = None,
    speech_path: str | Path | None = None,
) -> QuizConfig:
    quiz_data = _load_mapping_file(Path(path), "quiz")
    if runtime_path is None and teams_path is None and speech_path is None:
        return quiz_config_from_dict(quiz_data)

    merged: dict[str, Any] = {}
    if runtime_path is not None:
        merged.update(_load_mapping_file(Path(runtime_path), "quiz runtime"))

    merged["name"] = quiz_data.get("name", quiz_data.get("title", "Robotquiz"))
    if "questions" in quiz_data:
        merged["questions"] = quiz_data["questions"]

    if teams_path is not None:
        merged["players"] = _load_players_file(Path(teams_path))
    elif "players" in quiz_data:
        merged["players"] = quiz_data["players"]

    if speech_path is not None:
        path_obj = Path(speech_path)
        if path_obj.exists():
            speech_data = _load_mapping_file(path_obj, "quiz speech")
            speech_clips = speech_data.get("speech_clips", speech_data.get("clips", {}))
            if isinstance(speech_clips, dict):
                merged["speech_clips"] = speech_clips

    return quiz_config_from_dict(merged)


def quiz_config_from_dict(data: dict[str, Any]) -> QuizConfig:
    players = []
    for item in data.get("players", []):
        if not isinstance(item, dict):
            continue
        player_id = str(item.get("id") or item.get("player_id") or "")
        if not player_id:
            continue
        answers = {
            str(answer): int(marker_id)
            for answer, marker_id in dict(item.get("answers") or {}).items()
        }
        players.append(
            PlayerConfig(
                player_id=player_id,
                name=str(item.get("name") or player_id),
                answers=answers,
            )
        )

    questions = []
    for item in data.get("questions", []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("prompt") or "")
        correct = str(item.get("correct") or "")
        if not correct:
            continue
        questions.append(
            QuizQuestion(
                text=text,
                choices={str(k): str(v) for k, v in dict(item.get("choices") or {}).items()},
                correct=correct,
                prompt_clip=_optional_str(item.get("prompt_clip")),
                result_clip=_optional_str(item.get("result_clip")),
            )
        )

    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    speech_clips = data.get("speech_clips", data.get("clips", {}))
    if not isinstance(speech_clips, dict):
        speech_clips = {}
    return QuizConfig(
        name=str(data.get("name") or data.get("title") or "Robotquiz"),
        start_marker_id=_optional_int(data.get("start_marker_id")),
        players=tuple(players),
        questions=tuple(questions),
        stable_start_s=float(settings.get("stable_start_s", data.get("stable_start_s", 0.8))),
        stable_registration_s=float(
            settings.get("stable_registration_s", data.get("stable_registration_s", 0.8))
        ),
        registration_timeout_s=float(
            settings.get("registration_timeout_s", data.get("registration_timeout_s", 5.0))
        ),
        answer_memory_s=float(settings.get("answer_memory_s", data.get("answer_memory_s", 3.0))),
        stable_answer_s=float(settings.get("stable_answer_s", data.get("stable_answer_s", 1.0))),
        initial_timeout_s=float(settings.get("initial_timeout_s", data.get("initial_timeout_s", 5.0))),
        nudge_timeout_s=float(settings.get("nudge_timeout_s", data.get("nudge_timeout_s", 4.0))),
        after_prompt_delay_s=float(
            settings.get("after_prompt_delay_s", data.get("after_prompt_delay_s", 1.0))
        ),
        next_question_delay_s=float(
            settings.get("next_question_delay_s", data.get("next_question_delay_s", 1.0))
        ),
        default_next_mode=str(data.get("default_next_mode") or APP_MODE_IDLE),
        speech_clips={str(key): str(value) for key, value in speech_clips.items()},
    )


def format_name_list(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} och {names[1]}"
    return ", ".join(names[:-1]) + f" och {names[-1]}"


def _point_tuple(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    x, y = value
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    return float(x), float(y)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _load_yaml(path: Path) -> Any:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        try:
            return _load_simple_yaml(path)
        except ValueError:
            raise ValueError(
                f"YAML quiz file {path} needs PyYAML or the simple key/list format used by the examples"
            ) from exc
    return yaml.safe_load(path.read_text())


def _load_mapping_file(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"{label} file not found: {path}")
    data = _load_data_file(path)
    if not isinstance(data, dict):
        raise ValueError(f"{label} file {path} must contain an object")
    return data


def _load_players_file(path: Path) -> list[dict[str, Any]]:
    data = _load_data_file(path)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("players"), list):
        return [item for item in data["players"] if isinstance(item, dict)]
    raise ValueError(f"quiz teams file {path} must contain a players list")


def _load_data_file(path: Path) -> Any:
    try:
        if path.suffix.lower() == ".json":
            return json.loads(path.read_text())
        if path.suffix.lower() in {".yaml", ".yml"}:
            return _load_yaml(path)
    except OSError as exc:
        raise ValueError(f"could not load quiz file {path}: {exc}") from exc
    raise ValueError(f"unsupported quiz file type: {path.suffix}")


def _load_simple_yaml(path: Path) -> Any:
    lines = []
    for raw in path.read_text().splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, raw.strip()))
    if not lines:
        return {}
    parsed, index = _parse_simple_yaml_block(lines, 0, lines[0][0])
    if index != len(lines):
        raise ValueError(f"could not parse simple YAML file {path}")
    return parsed


def _parse_simple_yaml_block(
    lines: list[tuple[int, str]],
    index: int,
    indent: int,
) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    current_indent, content = lines[index]
    if current_indent != indent:
        raise ValueError("unexpected YAML indentation")
    if content.startswith("- "):
        return _parse_simple_yaml_list(lines, index, indent)
    return _parse_simple_yaml_map(lines, index, indent)


def _parse_simple_yaml_map(
    lines: list[tuple[int, str]],
    index: int,
    indent: int,
) -> tuple[dict[str, Any], int]:
    mapping: dict[str, Any] = {}
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent or content.startswith("- "):
            break
        key, value = _split_simple_yaml_key_value(content)
        index += 1
        if value == "":
            if index < len(lines) and lines[index][0] > current_indent:
                child, index = _parse_simple_yaml_block(lines, index, lines[index][0])
                mapping[key] = child
            else:
                mapping[key] = {}
        else:
            mapping[key] = _parse_simple_yaml_scalar(value)
    return mapping, index


def _parse_simple_yaml_list(
    lines: list[tuple[int, str]],
    index: int,
    indent: int,
) -> tuple[list[Any], int]:
    items: list[Any] = []
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent or not content.startswith("- "):
            break
        value = content[2:].strip()
        index += 1
        if value == "":
            if index < len(lines) and lines[index][0] > current_indent:
                child, index = _parse_simple_yaml_block(lines, index, lines[index][0])
            else:
                child = None
            items.append(child)
            continue
        if ":" in value:
            key, item_value = _split_simple_yaml_key_value(value)
            item: dict[str, Any] = {}
            if item_value == "":
                if index < len(lines) and lines[index][0] > current_indent:
                    child, index = _parse_simple_yaml_block(lines, index, lines[index][0])
                    item[key] = child
                else:
                    item[key] = {}
            else:
                item[key] = _parse_simple_yaml_scalar(item_value)
            if index < len(lines) and lines[index][0] > current_indent:
                child, index = _parse_simple_yaml_block(lines, index, lines[index][0])
                if not isinstance(child, dict):
                    raise ValueError("list item children must be mappings")
                item.update(child)
            items.append(item)
        else:
            items.append(_parse_simple_yaml_scalar(value))
    return items, index


def _split_simple_yaml_key_value(content: str) -> tuple[str, str]:
    if ":" not in content:
        raise ValueError("expected YAML key/value pair")
    key, value = content.split(":", 1)
    key = key.strip()
    if not key:
        raise ValueError("empty YAML key")
    return key, value.strip()


def _parse_simple_yaml_scalar(value: str) -> Any:
    if not value:
        return ""
    if value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    lowered = value.lower()
    if lowered in {"null", "none", "~"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value
