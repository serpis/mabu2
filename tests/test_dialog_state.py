import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import json

from dialog_state import (
    APP_MODE_ACTIVE,
    APP_MODE_IDLE,
    AppController,
    ObservationDecoder,
    StableAnswerTracker,
    WorldStateBuilder,
    load_quiz_config,
    load_quiz_selector_configs,
    quiz_config_from_dict,
)


def test_config():
    return quiz_config_from_dict(
        {
            "name": "Testquiz",
            "start_marker_id": 42,
            "settings": {
                "stable_start_s": 0.8,
                "stable_registration_s": 0.5,
                "registration_timeout_s": 0.0,
                "answer_memory_s": 3.0,
                "stable_answer_s": 1.0,
                "initial_timeout_s": 2.0,
                "nudge_timeout_s": 1.0,
                "after_prompt_delay_s": 0.0,
                "next_question_delay_s": 0.0,
            },
            "players": [
                {"id": "team_1", "name": "Lag ett", "answers": {"A": 101, "B": 102}},
                {"id": "team_2", "name": "Lag tva", "answers": {"A": 111, "B": 112}},
            ],
            "questions": [
                {"text": "Fraga", "correct": "B"},
            ],
        }
    )


class FakeRobot:
    def __init__(self):
        self.spoken = []
        self.speaking = False

    def speak_to_group(self, clip):
        self.spoken.append(clip)
        return {"ok": True, "status": "no_clip"}

    def is_speaking(self):
        return self.speaking


class DialogStateTest(unittest.TestCase):
    def advance_to_accept_answers(self, app, builder, robot, now, registration_markers):
        app.start_quiz(now)
        app.update(builder.update_from_marker_dicts(registration_markers, now), robot, now)
        app.update(builder.update_from_marker_dicts(registration_markers, now + 0.6), robot, now + 0.6)
        self.assertEqual(app.quiz.phase, "start_question")

        empty_world = builder.update_from_marker_dicts([], now + 0.7)
        app.update(empty_world, robot, now + 0.7)
        self.assertEqual(app.quiz.phase, "speaking_question")

        empty_world = builder.update_from_marker_dicts([], now + 0.8)
        app.update(empty_world, robot, now + 0.8)
        self.assertEqual(app.quiz.phase, "settle_after_speech")

        empty_world = builder.update_from_marker_dicts([], now + 0.9)
        app.update(empty_world, robot, now + 0.9)
        self.assertEqual(app.quiz.phase, "accept_answers")
        return now + 0.9

    def test_start_marker_requires_stability(self):
        config = test_config()
        builder = WorldStateBuilder(ObservationDecoder(config))
        marker = {"id": 7, "visible": True, "marker_id": 42, "center": [10, 20]}

        early = builder.update_from_marker_dicts([marker], 10.0)
        self.assertFalse(early.marker_stable(42, stable_for_s=0.8, now=10.7))

        stable = builder.update_from_marker_dicts([marker], 10.9)
        self.assertTrue(stable.marker_stable(42, stable_for_s=0.8, now=10.9))

    def test_idle_starts_quiz_from_stable_start_marker(self):
        config = test_config()
        app = AppController(config, mode=APP_MODE_IDLE, auto_start_quiz=True)
        robot = FakeRobot()
        builder = WorldStateBuilder(ObservationDecoder(config))
        marker = {"id": 7, "visible": True, "marker_id": 42, "center": [10, 20]}

        app.update(builder.update_from_marker_dicts([marker], 20.0), robot, 20.0)
        self.assertEqual(app.mode, APP_MODE_IDLE)

        app.update(builder.update_from_marker_dicts([marker], 20.9), robot, 20.9)
        self.assertEqual(app.mode, "quiz")
        self.assertTrue(app.quiz.running)
        self.assertEqual(app.quiz.phase, "register_players")

    def test_idle_starts_selected_quiz_from_stable_selector_marker(self):
        default_config = test_config()
        horse_config = quiz_config_from_dict(
            {
                "name": "Horse quiz",
                "players": [
                    {"id": "team_1", "name": "Lag ett", "answers": {"A": 101, "B": 102}},
                ],
                "questions": [{"text": "Hastfraga", "correct": "A"}],
            }
        )
        app = AppController(
            default_config,
            mode=APP_MODE_IDLE,
            auto_start_quiz=True,
            selector_configs={9: horse_config},
        )
        robot = FakeRobot()
        builder = WorldStateBuilder(ObservationDecoder(default_config))
        marker = {"id": 7, "visible": True, "marker_id": 9, "center": [10, 20]}

        app.update(builder.update_from_marker_dicts([marker], 22.0), robot, 22.0)
        self.assertEqual(app.mode, APP_MODE_IDLE)

        app.update(builder.update_from_marker_dicts([marker], 22.9), robot, 22.9)
        self.assertEqual(app.mode, "quiz")
        self.assertEqual(app.config.name, "Horse quiz")
        self.assertEqual(app.selected_marker_id, 9)

    def test_active_start_mode_starts_quiz_from_selector_marker(self):
        config = test_config()
        app = AppController(config, mode=APP_MODE_ACTIVE, auto_start_quiz=True)
        robot = FakeRobot()
        builder = WorldStateBuilder(ObservationDecoder(config))
        marker = {"id": 7, "visible": True, "marker_id": 42, "center": [10, 20]}

        app.update(builder.update_from_marker_dicts([marker], 24.0), robot, 24.0)
        self.assertEqual(app.mode, APP_MODE_ACTIVE)

        app.update(builder.update_from_marker_dicts([marker], 24.9), robot, 24.9)
        self.assertEqual(app.mode, "quiz")
        self.assertTrue(app.quiz.running)

    def test_quiz_waits_for_at_least_one_registered_team(self):
        config = test_config()
        app = AppController(config, mode="quiz", auto_start_quiz=False)
        robot = FakeRobot()
        builder = WorldStateBuilder(ObservationDecoder(config))
        app.start_quiz(25.0)

        world = builder.update_from_marker_dicts([], 25.0)
        app.update(world, robot, 25.0)
        app.update(world, robot, 30.0)

        self.assertEqual(app.quiz.phase, "register_players")
        self.assertEqual(app.quiz.question_index, -1)
        self.assertEqual(app.quiz.registered_player_ids, ())
        self.assertEqual(app.quiz.scores, {})

    def test_quiz_scores_stable_answers(self):
        config = test_config()
        app = AppController(config, mode="quiz", auto_start_quiz=False)
        robot = FakeRobot()
        builder = WorldStateBuilder(ObservationDecoder(config))
        registration_markers = [
            {"id": 1, "visible": True, "marker_id": 101, "center": [10, 20]},
            {"id": 2, "visible": True, "marker_id": 111, "center": [30, 40]},
        ]
        self.advance_to_accept_answers(app, builder, robot, 30.0, registration_markers)

        markers = [
            {"id": 1, "visible": True, "marker_id": 102, "center": [10, 20]},
            {"id": 2, "visible": True, "marker_id": 111, "center": [30, 40]},
        ]
        app.update(builder.update_from_marker_dicts(markers, 31.0), robot, 31.0)
        app.update(builder.update_from_marker_dicts(markers, 32.1), robot, 32.1)

        self.assertEqual(app.quiz.scores["team_1"], 1)
        self.assertEqual(app.quiz.scores["team_2"], 0)

    def test_quiz_uses_only_registered_teams(self):
        config = test_config()
        app = AppController(config, mode="quiz", auto_start_quiz=False)
        robot = FakeRobot()
        builder = WorldStateBuilder(ObservationDecoder(config))
        registration_markers = [
            {"id": 1, "visible": True, "marker_id": 101, "center": [10, 20]},
        ]

        self.advance_to_accept_answers(app, builder, robot, 35.0, registration_markers)

        self.assertEqual(app.quiz.registered_player_ids, ("team_1",))
        self.assertEqual(app.quiz.scores, {"team_1": 0})

    def test_completed_quiz_returns_to_idle(self):
        config = test_config()
        app = AppController(config, mode="quiz", auto_start_quiz=False)
        robot = FakeRobot()
        builder = WorldStateBuilder(ObservationDecoder(config))
        app.quiz.running = False
        app.quiz.phase = "complete"

        app.update(builder.update_from_marker_dicts([], 39.0), robot, 39.0)

        self.assertEqual(app.mode, APP_MODE_IDLE)
        self.assertEqual(app.last_event, "quiz_complete")

    def test_quiz_prefers_faces_while_speaking_and_markers_while_collecting_answers(self):
        config = test_config()
        app = AppController(config, mode=APP_MODE_IDLE, auto_start_quiz=False)

        self.assertEqual(app.preferred_target_mode(), "faces")

        app.start_quiz(39.0)
        self.assertEqual(app.preferred_target_mode(), "markers")

        app.quiz.phase = "speaking_question"
        self.assertEqual(app.preferred_target_mode(), "faces")

        app.quiz.phase = "accept_answers"
        self.assertEqual(app.preferred_target_mode(), "markers")

    def test_missing_answer_times_out_as_none(self):
        config = test_config()
        app = AppController(config, mode="quiz", auto_start_quiz=False)
        robot = FakeRobot()
        builder = WorldStateBuilder(ObservationDecoder(config))
        registration_markers = [
            {"id": 1, "visible": True, "marker_id": 101, "center": [10, 20]},
            {"id": 2, "visible": True, "marker_id": 111, "center": [30, 40]},
        ]
        self.advance_to_accept_answers(app, builder, robot, 40.0, registration_markers)

        world = builder.update_from_marker_dicts([], 41.0)
        app.update(world, robot, 41.0)
        app.update(world, robot, 43.1)
        app.update(world, robot, 44.2)

        self.assertEqual(app.quiz.locked_answers["team_1"], None)
        self.assertEqual(app.quiz.locked_answers["team_2"], None)
        self.assertEqual(app.quiz.scores, {"team_1": 0, "team_2": 0})

    def test_result_speech_names_teams_that_answered_correctly(self):
        config = quiz_config_from_dict(
            {
                "name": "Testquiz",
                "players": [
                    {"id": "team_1", "name": "Lag ett", "answers": {"A": 101, "B": 102}},
                    {"id": "team_2", "name": "Lag tva", "answers": {"A": 111, "B": 112}},
                ],
                "questions": [
                    {"text": "Fraga", "correct": "A"},
                    {"text": "Fraga tva", "correct": "B"},
                ],
                "speech_clips": {
                    "question_01__result": "question_result.wav",
                    "correct__team_1": "correct_team_1.wav",
                },
            }
        )
        quiz = AppController(config, mode="quiz", auto_start_quiz=False).quiz
        robot = FakeRobot()
        quiz.running = True
        quiz.phase = "accept_answers"
        quiz.question_index = 0
        quiz.registered_player_ids = ("team_1", "team_2")
        quiz.scores = {"team_1": 0, "team_2": 0}
        quiz.locked_answers = {"team_1": "A", "team_2": "B"}

        quiz._score_current_question(50.0, robot)
        self.assertEqual(robot.spoken, ["question_result.wav"])

        quiz.update(
            WorldStateBuilder(ObservationDecoder(config)).update_from_marker_dicts([], 50.1),
            robot,
            50.1,
        )
        self.assertEqual(robot.spoken, ["question_result.wav", "correct_team_1.wav"])

    def test_final_standings_are_lowest_score_first(self):
        quiz = AppController(test_config(), mode="quiz", auto_start_quiz=False).quiz
        quiz.registered_player_ids = ("team_1", "team_2")
        quiz.scores = {"team_1": 2, "team_2": 0}

        self.assertEqual(
            quiz.final_standings(),
            [("team_2", 0, 2), ("team_1", 2, 1)],
        )

    def test_recent_answer_survives_short_detection_gap(self):
        tracker = StableAnswerTracker(
            ("team_1",),
            stable_for_s=1.0,
            answer_memory_s=3.0,
        )

        tracker.update({"team_1": "A"}, 10.0)
        tracker.update({}, 10.8)
        self.assertEqual(tracker.current_answers(10.8), {"team_1": "A"})

        tracker.update({}, 11.1)
        self.assertEqual(tracker.locked_answers(), {"team_1": "A"})

    def test_more_recent_opposite_answer_wins(self):
        tracker = StableAnswerTracker(
            ("team_1",),
            stable_for_s=1.0,
            answer_memory_s=3.0,
        )

        tracker.update({"team_1": "A"}, 20.0)
        tracker.update({"team_1": "B"}, 20.5)
        self.assertEqual(tracker.current_answers(20.5), {"team_1": "B"})

        tracker.update({}, 21.4)
        self.assertEqual(tracker.locked_answers(), {"team_1": None})

        tracker.update({}, 21.6)
        self.assertEqual(tracker.locked_answers(), {"team_1": "B"})

    def test_recent_answer_expires_after_memory_window(self):
        tracker = StableAnswerTracker(
            ("team_1",),
            stable_for_s=10.0,
            answer_memory_s=3.0,
        )

        tracker.update({"team_1": "A"}, 30.0)
        tracker.update({}, 33.1)

        self.assertEqual(tracker.current_answers(33.1), {})
        self.assertEqual(tracker.locked_answers(), {"team_1": None})

    def test_load_separate_quiz_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runtime.json").write_text(
                json.dumps(
                    {
                        "start_marker_id": 7,
                        "settings": {"answer_memory_s": 2.5, "stable_answer_s": 1.25},
                    }
                )
            )
            (root / "teams.json").write_text(
                json.dumps(
                    {
                        "players": [
                            {"id": "team_1", "name": "Lag ett", "answers": {"A": 0, "B": 1}}
                        ]
                    }
                )
            )
            (root / "quiz.yaml").write_text(
                "\n".join(
                    [
                        "name: Split quiz",
                        "questions:",
                        "  - text: Fraga",
                        "    choices:",
                        "      A: Svar A",
                        "      B: Svar B",
                        "    correct: A",
                        "",
                    ]
                )
            )
            (root / "speech.json").write_text(
                json.dumps(
                    {
                        "speech_clips": {
                            "registration_prompt": "quiz_registration_prompt.wav",
                            "question_01__result": "quiz_question_01_result.wav",
                        }
                    }
                )
            )

            config = load_quiz_config(
                root / "quiz.yaml",
                runtime_path=root / "runtime.json",
                teams_path=root / "teams.json",
                speech_path=root / "speech.json",
            )

        self.assertEqual(config.name, "Split quiz")
        self.assertEqual(config.start_marker_id, 7)
        self.assertEqual(config.answer_memory_s, 2.5)
        self.assertEqual(config.stable_answer_s, 1.25)
        self.assertEqual(config.players[0].answers, {"A": 0, "B": 1})
        self.assertEqual(config.questions[0].correct, "A")
        self.assertEqual(config.speech_clips["question_01__result"], "quiz_question_01_result.wav")

    def test_load_selector_quiz_configs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runtime.json").write_text(
                json.dumps(
                    {
                        "quiz_selectors": {
                            "8": {
                                "quiz_file": "minecraft.yaml",
                                "speech_file": "minecraft_speech.json",
                            },
                            "9": {"quiz_file": "horse.yaml"},
                        }
                    }
                )
            )
            (root / "teams.json").write_text(
                json.dumps(
                    {
                        "players": [
                            {"id": "team_1", "name": "Lag ett", "answers": {"A": 0, "B": 1}}
                        ]
                    }
                )
            )
            (root / "minecraft.yaml").write_text(
                "name: Minecraft medel\nquestions:\n  - text: Fraga\n    correct: A\n"
            )
            (root / "horse.yaml").write_text(
                "name: Hast medel\nquestions:\n  - text: Fraga\n    correct: B\n"
            )
            (root / "minecraft_speech.json").write_text(
                json.dumps({"speech_clips": {"question_01": "minecraft.wav"}})
            )

            configs = load_quiz_selector_configs(
                root / "runtime.json",
                teams_path=root / "teams.json",
            )

        self.assertEqual(sorted(configs), [8, 9])
        self.assertEqual(configs[8].name, "Minecraft medel")
        self.assertEqual(configs[8].speech_clips["question_01"], "minecraft.wav")
        self.assertEqual(configs[9].name, "Hast medel")

    def test_runtime_team_ids_cover_four_teams(self):
        config = load_quiz_config(
            Path("quiz/minecraft_medel.yaml"),
            runtime_path=Path("quiz/robot_quiz_runtime.json"),
            teams_path=Path("quiz/robot_quiz_teams.json"),
        )

        self.assertEqual(
            {player.player_id: player.answers for player in config.players},
            {
                "team_1": {"A": 0, "B": 1},
                "team_2": {"A": 2, "B": 3},
                "team_3": {"A": 4, "B": 5},
                "team_4": {"A": 6, "B": 7},
            },
        )


if __name__ == "__main__":
    unittest.main()
