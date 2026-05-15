# Robotdialog, idle och quiz i dashboarden

Designen applicerar iden fran den delade ChatGPT-traden "Robotdialog med
Aruco-koder" pa den har kodbasen:

https://chatgpt.com/share/69ff80ba-68f4-838e-99a4-19c43ac1d1a4

Malet ar en minimal forsta implementation dar dashboarden visar och styr om
roboten ar i `active`, `idle` eller `quiz`, och dar `idle` kan starta ett quiz
nar en start-ArUco syns stabilt.

## Nulage i koden

Den relevanta runtime-koden ligger i `face_follow.py`.

- `SharedCameraMjpegServer` ager dashboard-HTML, `/debug` och runtime-endpoints.
- `RpicamFaceTracker` ger senaste `TrackedFaceFrame` med faces och ArUco-markers.
- `target_mode` styr idag om roboten tittar pa faces eller markers.
- `gaze_mode` styr om blicken skickas via animation engine eller direkt pose.
- `RobotEngine` ager UART, board-state, gaze timeline, blink, speech motion och
  nack-overlays.
- Ljud finns redan som `sound/*.wav` och spelas via `/sound`.

Viktig begransning: marker-detection ar idag kopplad till `target_mode ==
"markers"`. For idle-trigger och quiz maste marker-detection kunna vara aktiv
aven nar roboten inte just nu tittar pa markers.

## Minimal appmodell

Lagg ett nytt app-lager ovanpa dagens face-follow-loop:

```text
AppController
  mode: active | idle | quiz
  world: WorldState
  idle: IdleBehavior
  quiz: QuizSession | None
  robot: RobotAdapter
```

`active` ar dagens beteende: valj face/marker enligt dashboardens `target_mode`
och skicka gaze enligt `gaze_mode`.

`idle` ar passivt: roboten far anvanda idle blink och eventuellt en enkel
vilopose, men skickar inte normal face-follow gaze. Kameran fortsatter titta
efter markers. Om startmarkern syns stabilt byter appen till `quiz`.

`quiz` tar over robotens semantiska beteende. Normal face-follow gaze pausas,
men `RobotEngine` fortsatter agera transport och timeline-renderare for gaze,
blink och speech motion.

## WorldState

Bygg `WorldState` en gang per kamera-frame fran `TrackedFaceFrame`:

```python
@dataclass
class MarkerObservation:
    marker_id: int
    track_id: int
    center: tuple[float, float]
    center_norm: tuple[float, float]
    first_seen_at: float
    last_seen_at: float
    stable_since: float | None

@dataclass
class WorldState:
    faces: list[dict]
    markers: dict[int, MarkerObservation]
    decoded: dict
```

`decoded` ar inte bilddata. Det byggs fran tre separata filer:
runtime-regler, lag/ArUco och quizinnehall.

```json
{
  "players": [
    {
      "id": "team_1",
      "name": "Lag ett",
      "answers": {
        "A": 0,
        "B": 1
      }
    },
    {
      "id": "team_2",
      "name": "Lag tva",
      "answers": {
        "A": 2,
        "B": 3
      }
    }
  ]
}
```

Runtime-regler ligger i en egen fil:

```json
{
  "start_marker_id": 7,
  "default_next_mode": "idle",
  "settings": {
    "stable_start_s": 0.8,
    "stable_registration_s": 0.8,
    "registration_timeout_s": 5.0,
    "answer_memory_s": 3.0,
    "stable_answer_s": 1.0,
    "initial_timeout_s": 5.0,
    "nudge_timeout_s": 4.0
  }
}
```

Quiznamn och fragor ligger separat, till exempel i YAML:

```yaml
name: Robotquiz
questions:
  - text: Dagens fraga
    choices:
      A: Svar A
      B: Svar B
    correct: B
```

Exempel pa decoder-resultat:

```python
{
    "start_quiz_visible": True,
    "answers": {
        "team_1": "B",
        "team_2": "A",
    },
}
```

Karnan fran iden i traden ar att dialogen inte ska vanta pa en raw ArUco-kod,
utan pa ett villkor, till exempel "alla aktiva lag har haft samma svar stabilt
i minst 1.0 s".

## IdleBehavior

Idle ar en liten stateful komponent:

```python
class IdleBehavior:
    def update(self, world, robot, now) -> AppEvent | None:
        if world.marker_stable(config.start_marker_id, stable_for=0.8):
            return AppEvent("start_quiz")
        robot.ensure_idle(now)
        return None
```

Forsta versionen behover bara:

- halla marker-detection aktiv
- visa `watching_start_marker` i dashboarden
- trigga `start_quiz` nar startmarkern ar stabil
- inte skicka vanlig face-follow gaze

## QuizSession

Forsta quizmotorn kan vara en enkel sekvens av states, inte en stor generell
dialogmotor:

```text
register_players
ask_question
settle_after_speech
accept_answers
nudge_missing
score_question
next_question | final_scores
complete
```

Senare kan varje steg bli `Action`-objekt, men det behovs inte for forsta
implementationen.

`register_players` samlar lag som visar nagon av sina egna ArUco-taggar
stabilt. Nar forsta laget ar anmalt startar `registration_timeout_s` som
kort anmalningsfonster for fler lag. Nar fonstret har gatt och minst ett lag
ar anmalt gar quizzet vidare. Om inget lag ar anmalt stannar sessionen i
registrering och dashboarden visar att den vantar.

Svar samlas med en `StableAnswerTracker`:

```python
class StableAnswerTracker:
    stable_for_s = 1.0
    initial_timeout_s = 5.0
    nudge_timeout_s = 4.0

    def update(self, observations, now): ...
    def locked_answers(self) -> dict[str, str | None]: ...
    def missing_players(self) -> list[str]: ...
```

För svarskort används `answer_memory_s` som ett kort per-lag-minne. Om varken `A`
eller `B` har synts inom fönstret finns inget aktuellt svar. Om båda har synts
är den senast sedda sidan lagets aktuella svar. `stable_answer_s` räknas ovanpå
detta aktuella svar, så ett kort kan klara korta detektionsglapp men ett byte till
andra sidan tar över direkt.

Regler:

- ignorera svar medan roboten laser fragan
- starta insamling efter `settle_after_speech`
- las ett svar nar samma lag visar samma svar stabilt i `stable_for_s`
- efter `initial_timeout_s`, fraga saknade lag: "Aha, men lag tva da, vad
  svarar ni?"
- efter `nudge_timeout_s`, ga vidare med saknade svar som `None`
- `None` ger noll poang

Quizet avslutas med totalpoang och vinnare:

```text
Quizet ar slut.
Lag ett fick 2 poang.
Lag tva fick 4 poang.
Vinnare ar lag tva.
```

## RobotAdapter

Quizmotorn ska inte skriva direkt till `RobotEngine`. Den ska prata med en tunn
adapter som oversatter semantiska kommandon till dagens mekanismer:

```python
class RobotAdapter:
    def speak_to_group(self, clip_or_line_id: str): ...
    def speak_at_marker(self, marker_id: int, clip_or_line_id: str): ...
    def gaze_at_group(self): ...
    def gaze_at_marker(self, marker_id: int): ...
    def idle(self): ...
```

Forsta versionen anvander befintliga ljudklipp i `sound/` i stallet for fri
TTS. Om text saknar forrenderat ljud ska dashboarden visa en tydlig
`missing_audio`-status i quizpanelen.

Quizljud bakas pa host-datorn innan deploy:

```bash
python3 quiz_bake.py
```

Bake-laget laser quiz-, runtime- och lagfilerna, skapar alla fraser som
runtime kan spela och skriver:

- `sound/*.wav`
- `quiz/robot_quiz_baked_speech.json`

Speech-filen ar en generated lookup-tabell fran dialognyckel till WAV-fil. Den
ar separat fran quizfragor, runtime-regler och lag/ArUco-config.

Nar ljud spelas kan befintlig `speech_motion` ateranvandas:

- `SharedCameraMjpegServer.is_sound_running()`
- `schedule_speech_motion_if_ready(engine)`
- `cancel_pending_speech_motion(engine)`

Gaze mot marker kan ateranvanda dagens bildpunkt-till-gaze-kod genom att mata
marker-center till `calibrated_frame_point_to_gaze()`.

## Stodandringar i ljud och animation

Ja, dialoglagret blir enklare om ljud och animation far lite tydligare runtime-
metadata. Den viktigaste forsta andringen ar att ljudklipp exponerar langd.

`/sound` och `/debug.sound` bor innehalla:

```json
{
  "status": "playing",
  "clip": "dagens_fraga.wav",
  "duration_s": 2.34,
  "started_at": 1770000000.0,
  "elapsed_s": 0.82,
  "remaining_s": 1.52,
  "expected_end_at": 1770000002.34
}
```

Varfor det behovs:

- `QuizSession` kan implementera `WaitUntilSpeechFinished` utan att gissa.
- Dashboarden kan visa om roboten faktiskt pratar eller bara ar i en dialogfas.
- `speaking`-overlay kan startas och stoppas deterministiskt.
- Fragesvar kan ignoreras tills ljudet ar klart plus en kort settle-delay.
- Saknade ljud kan rapporteras som `missing_audio` innan quizet startar.

Implementation:

- `.wav`-langd lases med Pythons `wave`-modul.
- Andra format kan anvanda `ffprobe` om det finns installerat.
- Langd cachas per fil baserat pa `mtime` och filstorlek.
- Om langd saknas fungerar spelning anda; dialogen faller tillbaka pa
  process-status fran ljudspelaren.

Animation bor hallas pa tva nivaer:

```text
intent/base:
  idle
  listening
  thinking
  counting
  celebrating

overlay:
  speaking
  blink
  speech_motion
  neck_stretch
```

Forsta implementationen behover inte rendera alla base-intents som egna
animationer. `RobotAdapter` kan mappa dem konservativt:

- `idle`: ingen face-follow gaze, idle blink pa
- `listening`: gaze mot grupp eller marker, idle blink pa
- `thinking`: kort blink eller liten neck-stretch om motorboardet ar idle
- `counting`: gaze-at-marker i ordning, speech motion vid ljud
- `celebrating`: glad ljudfras plus befintlig blink/stretch om det ar sakert

Viktigt: behall dagens chunkade `speech_motion`. Aven om ljudlangden ar kand
ska vi inte rendera ett langt motorboard-script for hela ljudet. Langden ska
styra dialogfasen; motorrorelsen kan fortsatt schemalaggas i korta chunks medan
ljudprocessen kor.

## Dashboard

Lagg en ny panel hogt upp i `SharedCameraMjpegServer._index_html()`:

```text
App
  mode        active | idle | quiz
  phase       tracking | watching_start_marker | question_2_accepting_answers
  controls    Active  Idle  Start quiz  Stop quiz
  auto start  [x] Start quiz from marker
```

Lagg en quizpanel under `App`:

```text
Quiz
  quiz        Robotquiz
  question    2 / 5
  accepting   yes | no
  answers     2 / 3 locked
  missing     Lag tre
  scores      Lag ett 1, Lag tva 2, Lag tre 0
```

Visa ocksa en kompakt marker-rad:

```text
markers  42 stable, 101 seen, 112 stable
```

Det gor att man kan felsoka om idle inte startar quizet utan att lasa raw JSON.

## Endpoints

Utoka `/debug` med:

```json
{
  "app": {
    "mode": "idle",
    "phase": "watching_start_marker",
    "auto_start_quiz": true
  },
  "quiz": {
    "running": false,
    "name": "Robotquiz",
    "question_index": null,
    "accepting_answers": false,
    "locked_answers": {},
    "scores": {}
  },
  "world": {
    "visible_marker_ids": [42],
    "stable_marker_ids": [42]
  }
}
```

Nya styr-endpoints:

```text
/app?action=set_mode&mode=active
/app?action=set_mode&mode=idle
/app?action=set_auto_start&enabled=1
/quiz?action=start
/quiz?action=stop
/quiz?action=reset
```

`Start quiz` fran dashboarden ska fungera aven utan startmarker. Startmarker ar
bara idle-autostart.

## Run-loop integration

Nuvarande loop i `run()` bor fa en tydlig grind:

```python
world = world_state.update_from_frame(frame, now)
app_event = app_controller.update(world, robot_adapter, now)

if app_controller.mode == "active":
    run_existing_face_follow_gaze()
else:
    skip_existing_face_follow_gaze()
```

All motorboard-uppdatering som redan maste ske varje varv ligger kvar:

```python
engine.read_serial()
engine.update_board_state()
engine.maybe_start_next()
apply_blink_settings(...)
apply_eyelid_offset(...)
apply_speech_motion_amplitude(...)
```

Marker-detection ska vara aktiv nar nagon av dessa ar sann:

```python
target_mode == "markers"
or app_mode in {"idle", "quiz"}
or auto_start_quiz
```

Det ar en separat fraga fran vem roboten tittar pa.

## Runtime-settings

Utoka `face_follow_settings.json` med:

```json
{
  "app_mode": "active",
  "auto_start_quiz": true,
  "quiz_file": "quiz/robot_quiz.yaml",
  "quiz_runtime_file": "quiz/robot_quiz_runtime.json",
  "quiz_teams_file": "quiz/robot_quiz_teams.json",
  "quiz_speech_file": "quiz/robot_quiz_baked_speech.json"
}
```

Bevara dagens default genom att starta i `active` om setting saknas.

## Foreslagen filstruktur

Forsta implementation:

```text
dialog_state.py
  WorldState
  ObservationDecoder
  StableAnswerTracker
  IdleBehavior
  QuizSession
  AppController

face_follow.py
  endpoints, dashboard och run-loop integration

quiz/robot_quiz.yaml
  quiznamn + fragor
quiz/robot_quiz_runtime.json
  startmarkor, stable-tider och timeout/policy
quiz/robot_quiz_teams.json
  lagens namn och ArUco-ID for svarskorten
quiz/robot_quiz_baked_speech.json
  generated lookup-tabell till bakade ljudfiler
quiz_bake.py
  host-only bake-lage for WAV-generering
```

Det finns ingen manifestfil; `face_follow.py` laddar de tre filerna explicit.

`dialog_state.py` ska inte importera OpenCV eller serialkod. Den ska vara enkel
att enhetstesta med syntetiska marker-observationer.

## Testplan

Enhetstester:

- startmarker maste vara stabil i minst `0.8 s` innan idle startar quiz
- quiz startar med lagregistrering och gar inte till fragor utan minst ett lag
- endast registrerade lag far `locked_answers`, `missing_players` och poang
- samma svar maste vara stabilt i minst `1.0 s` innan det lases
- svar som fladdrar mellan A/B far inte lasas
- timeout skapar saknade svar som `None`
- missade svar ger noll poang
- final score valjer ensam vinnare och hanterar oavgjort

Manuell Pi-test:

1. Starta `face_follow.py` och oppna dashboarden.
2. Satt `mode=idle`.
3. Visa start-ArUco tills dashboarden visar `stable`.
4. Verifiera att `mode=quiz` och normal face-follow gaze ar pausad.
5. Visa svarskoder och se att `locked_answers` och `scores` uppdateras.
6. Stoppa quiz via dashboarden och verifiera att roboten gar tillbaka till
   `idle` eller `active` enligt vald policy.

## Icke-mal for forsta versionen

- fri text-TTS under livekorningsloopen
- generell mission/state-machine utover quiz
- flera samtidiga quiz
- avancerad animation blending utover befintlig gaze, blink och speech motion
- persistent historik over tidigare quiz

Den har specen lamnar plats for en senare `StateMachineSession`, men forsta
steget ar medvetet smalt: dashboard-styrt app-lage, idle-start via ArUco och
ett quiz som laser stabila svar och visar exakt vad som hander.
