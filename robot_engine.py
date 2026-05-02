#!/usr/bin/env python3

from __future__ import annotations

import argparse
import math
import select
import sys
import time
from dataclasses import dataclass
from enum import Enum

from robot_animation import (
    BLINK_DEFAULT_CLOSED_ANGLE,
    BLINK_DEFAULT_CLOSE_MS,
    BLINK_DEFAULT_HOLD_MS,
    BLINK_DEFAULT_OPEN_MS,
    BlinkEvent,
    FeedbackDecoder,
    FeedbackFrame,
    GAZE_TO_CHANNELS,
    GazeControllerState,
    GazeCornersConfig,
    blink_base_eyelid_angle,
    gaze_to_curves,
    render_blink,
    render_gaze_corners_curves,
)
from robot_motion import (
    BAUDRATE,
    Command,
    POWER_OFF,
    SCRIPT_TICK_SECONDS,
    STARTUP_COMMANDS,
    RobotMotion,
    format_hex,
    packet,
    print_rx,
    read_command,
)


DEFAULT_GAZE_MS = 1500.0
DEFAULT_GAZE_SAMPLE_MS = 50.0
DEFAULT_IDLE_BLINK_INTERVAL_S = 4.0
DEFAULT_GAZE_BLINK_THRESHOLD_DEG = 15.0
DEFAULT_GAZE_BLINK_REFRACTORY_S = 3.0
DEFAULT_FEEDBACK_SILENCE_S = 0.20
DEFAULT_DONE_FALLBACK_S = 0.60


class BoardState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    SETTLING = "settling"
    UNKNOWN = "unknown"


@dataclass
class PendingGaze:
    yaw: float
    pitch: float
    dwell_ms: float


@dataclass(frozen=True)
class EngineConfig:
    port: str
    baudrate: int
    gaze_sample_ms: float
    eyelid_offset: float
    idle_blink: bool
    blink_interval_s: float
    gaze_blink_threshold_deg: float
    gaze_blink_refractory_s: float
    blink_close_ms: float
    blink_hold_ms: float
    blink_open_ms: float
    blink_closed_angle: float
    feedback_silence_s: float
    done_fallback_s: float
    verbose: bool


def rendered_duration_s(keyframes: tuple[tuple[int, ...], ...]) -> float:
    return sum(frame[-1] for frame in keyframes) * SCRIPT_TICK_SECONDS


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Foreground animation engine that owns the motorboard UART and reads commands from stdin."
    )
    parser.add_argument("--port", default="/dev/ttyAMA0")
    parser.add_argument("--baudrate", type=int, default=BAUDRATE)
    parser.add_argument("--gaze-sample-ms", type=float, default=DEFAULT_GAZE_SAMPLE_MS)
    parser.add_argument("--eyelid-offset", type=float, default=-2.0)
    parser.add_argument("--idle-blink", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--blink-interval", type=float, default=DEFAULT_IDLE_BLINK_INTERVAL_S)
    parser.add_argument("--gaze-blink-threshold", type=float, default=DEFAULT_GAZE_BLINK_THRESHOLD_DEG)
    parser.add_argument("--gaze-blink-refractory", type=float, default=DEFAULT_GAZE_BLINK_REFRACTORY_S)
    parser.add_argument("--blink-close-ms", type=float, default=BLINK_DEFAULT_CLOSE_MS)
    parser.add_argument("--blink-hold-ms", type=float, default=BLINK_DEFAULT_HOLD_MS)
    parser.add_argument("--blink-open-ms", type=float, default=BLINK_DEFAULT_OPEN_MS)
    parser.add_argument("--blink-closed-angle", type=float, default=BLINK_DEFAULT_CLOSED_ANGLE)
    parser.add_argument("--feedback-silence", type=float, default=DEFAULT_FEEDBACK_SILENCE_S)
    parser.add_argument("--done-fallback", type=float, default=DEFAULT_DONE_FALLBACK_S)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def parse_gaze_line(parts: list[str]) -> PendingGaze:
    if len(parts) < 2:
        raise ValueError("usage: gaze YAW[,PITCH] [DURATION_MS] or gaze YAW PITCH [DURATION_MS]")

    if "," in parts[1]:
        angle_parts = [part.strip() for part in parts[1].split(",")]
        if len(angle_parts) == 1:
            yaw = float(angle_parts[0])
            pitch = 0.0
        elif len(angle_parts) == 2:
            yaw = float(angle_parts[0])
            pitch = float(angle_parts[1])
        else:
            raise ValueError("gaze angle must be YAW or YAW,PITCH")
        dwell_ms = float(parts[2]) if len(parts) >= 3 else DEFAULT_GAZE_MS
    else:
        yaw = float(parts[1])
        if len(parts) >= 3:
            pitch = float(parts[2])
            dwell_ms = float(parts[3]) if len(parts) >= 4 else DEFAULT_GAZE_MS
        else:
            pitch = 0.0
            dwell_ms = DEFAULT_GAZE_MS

    if dwell_ms <= 0:
        raise ValueError("gaze duration must be > 0")
    return PendingGaze(yaw=yaw, pitch=pitch, dwell_ms=dwell_ms)


class RobotEngine:
    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self.robot = RobotMotion(config.port, config.baudrate)
        self.feedback_decoder = FeedbackDecoder()
        self.started_at = time.monotonic()
        self.state = BoardState.UNKNOWN
        self.expected_done_at = 0.0
        self.last_tx_at: float | None = None
        self.last_feedback_at: float | None = None
        self.latest_frame: FeedbackFrame | None = None
        self.pending_gaze: PendingGaze | None = None
        self.pending_manual_blink = False
        self.idle_blink_enabled = config.idle_blink
        self.next_idle_blink_at = time.monotonic() + config.blink_interval_s
        self.last_blink_at: float | None = None
        self.running = True

    def log(self, message: str) -> None:
        print(message, flush=True)

    def debug(self, message: str) -> None:
        if self.config.verbose:
            self.log(message)

    def open(self) -> None:
        self.robot.open()
        self.send_wire_command(STARTUP_COMMANDS[0], expected_duration_s=STARTUP_COMMANDS[0].delay_after)
        self.pump_for(STARTUP_COMMANDS[0].delay_after)
        self.refresh_pose()
        self.state = BoardState.IDLE
        self.log("engine ready. Type 'help' for commands.")

    def close(self) -> None:
        self.robot.close()

    @property
    def port(self):
        return self.robot.serial_port

    def send_wire_command(self, command: Command, *, expected_duration_s: float) -> None:
        now = time.monotonic()
        tx = packet(command.payload)
        if self.config.verbose:
            self.log(f"TX +{now - self.started_at:0.3f}s {command.name}: {format_hex(tx)}")
        self.port.write(tx)
        self.port.flush()
        self.last_tx_at = now
        self.expected_done_at = now + expected_duration_s
        self.state = BoardState.RUNNING

    def send_rendered_command(self, command: Command, *, script_duration_s: float) -> None:
        self.send_wire_command(command, expected_duration_s=script_duration_s)

    def read_serial(self) -> None:
        waiting = self.port.in_waiting
        if not waiting:
            return
        data = self.port.read(waiting)
        now = time.monotonic()
        if self.config.verbose:
            print_rx(data, self.started_at)
        for frame in self.feedback_decoder.feed(data, now - self.started_at):
            self.latest_frame = frame
            self.last_feedback_at = now

    def pump_for(self, duration_s: float) -> None:
        deadline = time.monotonic() + max(duration_s, 0.0)
        while time.monotonic() < deadline:
            self.read_serial()
            time.sleep(0.01)
        self.read_serial()

    def refresh_pose(self) -> None:
        command = read_command("engine_read_vr_values", 0x40, timeout=0.25)
        self.send_wire_command(command, expected_duration_s=command.delay_after)
        self.pump_for(command.delay_after)

    def update_board_state(self) -> None:
        now = time.monotonic()
        if self.state == BoardState.IDLE:
            return
        if now < self.expected_done_at:
            self.state = BoardState.RUNNING
            return

        feedback_quiet = self.last_feedback_at is None or now - self.last_feedback_at >= self.config.feedback_silence_s
        fallback_elapsed = now >= self.expected_done_at + self.config.done_fallback_s
        if feedback_quiet or fallback_elapsed:
            self.state = BoardState.IDLE
        else:
            self.state = BoardState.SETTLING

    def current_controller_state(self) -> GazeControllerState:
        frame = self.latest_frame
        if frame is None:
            return GazeControllerState(0.0, 0.0, 0.0, 0.0)
        return GazeControllerState(
            eye_yaw=frame.angle("eye_leftright"),
            eye_pitch=frame.angle("eye_updown"),
            neck_yaw=frame.angle("neck_rotation"),
            neck_pitch=frame.angle("neck_elevation"),
            eyelid_left=frame.angle("eyelid_left"),
            eyelid_right=frame.angle("eyelid_right"),
        )

    def current_base_eyelid_angle(self) -> float:
        eye_pitch = self.latest_frame.angle("eye_updown") if self.latest_frame is not None else 0.0
        return blink_base_eyelid_angle(eye_pitch, self.config.eyelid_offset)

    def estimated_gaze(self) -> tuple[float, float]:
        state = self.current_controller_state()
        return state.eye_yaw + state.neck_yaw, state.eye_pitch + state.neck_pitch

    def gaze_jump_degrees(self, gaze: PendingGaze) -> float:
        current_yaw, current_pitch = self.estimated_gaze()
        return math.hypot(gaze.yaw - current_yaw, gaze.pitch - current_pitch)

    def make_blink_event(self, start_ms: float = 0.0) -> BlinkEvent:
        return BlinkEvent(
            start_ms=start_ms,
            close_ms=self.config.blink_close_ms,
            hold_ms=self.config.blink_hold_ms,
            open_ms=self.config.blink_open_ms,
            closed_angle=self.config.blink_closed_angle,
        )

    def can_auto_blink_at(self, at_s: float, previous_blink_at: float | None) -> bool:
        return (
            previous_blink_at is None
            or at_s - previous_blink_at >= self.config.gaze_blink_refractory_s
        )

    def blink_events_for_window(
        self,
        duration_ms: float,
        *,
        gaze: PendingGaze | None = None,
        gaze_jump_deg: float = 0.0,
    ) -> tuple[BlinkEvent, ...]:
        now = time.monotonic()
        events: list[BlinkEvent] = []
        event_times: list[float] = []
        latest_scheduled_blink_at = self.last_blink_at

        def add_event(start_ms: float, *, force: bool = False) -> bool:
            nonlocal latest_scheduled_blink_at
            at_s = now + start_ms / 1000.0
            if latest_scheduled_blink_at is not None and abs(at_s - latest_scheduled_blink_at) < 0.001:
                return False
            if not force and not self.can_auto_blink_at(at_s, latest_scheduled_blink_at):
                return False
            events.append(self.make_blink_event(start_ms))
            event_times.append(at_s)
            latest_scheduled_blink_at = at_s
            return True

        if self.pending_manual_blink:
            add_event(0.0, force=True)
            self.pending_manual_blink = False

        if (
            gaze is not None
            and gaze_jump_deg > self.config.gaze_blink_threshold_deg
        ):
            add_event(0.0)

        if self.idle_blink_enabled:
            window_end = now + duration_ms / 1000.0
            while self.next_idle_blink_at <= window_end:
                start_ms = max(0.0, (self.next_idle_blink_at - now) * 1000.0)
                if add_event(start_ms):
                    self.next_idle_blink_at += self.config.blink_interval_s
                elif latest_scheduled_blink_at is not None:
                    min_next = latest_scheduled_blink_at + max(
                        self.config.blink_interval_s,
                        self.config.gaze_blink_refractory_s,
                    )
                    self.next_idle_blink_at = max(
                        self.next_idle_blink_at + self.config.blink_interval_s,
                        min_next,
                    )
                else:
                    self.next_idle_blink_at += self.config.blink_interval_s

        if event_times:
            latest_event_at = max(event_times)
            self.last_blink_at = latest_event_at
            self.next_idle_blink_at = max(
                self.next_idle_blink_at,
                latest_event_at + self.config.blink_interval_s,
            )

        return tuple(events)

    def run_blink(self) -> None:
        base = self.current_base_eyelid_angle()
        rendered, _samples = render_blink(
            base,
            base,
            closed_angle=self.config.blink_closed_angle,
            close_ms=self.config.blink_close_ms,
            hold_ms=self.config.blink_hold_ms,
            open_ms=self.config.blink_open_ms,
            name="engine_blink",
        )
        self.log(f"blink baseline={base:.2f}deg frames={len(rendered.keyframes)}")
        self.send_rendered_command(rendered.command(), script_duration_s=rendered_duration_s(rendered.keyframes))
        now = time.monotonic()
        self.last_blink_at = now
        self.next_idle_blink_at = now + self.config.blink_interval_s

    def run_gaze(self, gaze: PendingGaze) -> None:
        yaw_curve, pitch_curve = gaze_to_curves(gaze.yaw, gaze.pitch, gaze.dwell_ms)
        gaze_jump_deg = self.gaze_jump_degrees(gaze)
        blink_events = self.blink_events_for_window(
            gaze.dwell_ms,
            gaze=gaze,
            gaze_jump_deg=gaze_jump_deg,
        )
        max_frames = (255 - 3) // (len(GAZE_TO_CHANNELS) + 1)
        sample_ms = max(self.config.gaze_sample_ms, math.ceil(gaze.dwell_ms / max_frames))
        rendered, _samples = render_gaze_corners_curves(
            yaw_curve,
            pitch_curve,
            config=GazeCornersConfig(sample_ms=sample_ms),
            name=f"engine_gaze_yaw={gaze.yaw:g}_pitch={gaze.pitch:g}",
            initial_state=self.current_controller_state(),
            include_eyelids=True,
            eyelid_offset=self.config.eyelid_offset,
            blink_events=blink_events,
        )
        self.log(
            f"gaze yaw={gaze.yaw:g} pitch={gaze.pitch:g} ms={gaze.dwell_ms:g} "
            f"frames={len(rendered.keyframes)} blinks={len(blink_events)} "
            f"jump={gaze_jump_deg:.1f}deg"
        )
        self.send_rendered_command(rendered.command(), script_duration_s=rendered_duration_s(rendered.keyframes))

    def maybe_start_next(self) -> None:
        if self.state != BoardState.IDLE:
            return

        if self.pending_gaze is not None:
            gaze = self.pending_gaze
            self.pending_gaze = None
            try:
                self.run_gaze(gaze)
            except ValueError as exc:
                self.log(f"gaze error: {exc}")
            return

        now = time.monotonic()
        if self.pending_manual_blink:
            self.pending_manual_blink = False
            try:
                self.run_blink()
            except ValueError as exc:
                self.log(f"blink error: {exc}")
            return

        if self.idle_blink_enabled and now >= self.next_idle_blink_at:
            try:
                self.run_blink()
            except ValueError as exc:
                self.log(f"idle blink error: {exc}")
                self.next_idle_blink_at = now + self.config.blink_interval_s

    def print_status(self) -> None:
        feedback_age = "none"
        if self.last_feedback_at is not None:
            feedback_age = f"{time.monotonic() - self.last_feedback_at:.2f}s"
        pending = "none" if self.pending_gaze is None else f"gaze {self.pending_gaze.yaw:g},{self.pending_gaze.pitch:g}"
        self.log(
            f"state={self.state.value} feedback_age={feedback_age} "
            f"pending={pending} manual_blink={self.pending_manual_blink} "
            f"idle_blink={self.idle_blink_enabled}"
        )

    def process_line(self, line: str) -> None:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return

        parts = stripped.split()
        command = parts[0].lower()
        try:
            if command in {"quit", "exit"}:
                self.running = False
            elif command == "help":
                self.log(
                    "commands: gaze YAW[,PITCH] [MS] | gaze YAW PITCH [MS] | "
                    "blink | idle on|off | interval SEC | status | quit"
                )
            elif command == "status":
                self.print_status()
            elif command == "blink":
                self.pending_manual_blink = True
                self.log("queued blink")
            elif command in {"gaze", "gaze-to", "look"}:
                self.pending_gaze = parse_gaze_line(parts)
                self.log(
                    f"queued gaze yaw={self.pending_gaze.yaw:g} "
                    f"pitch={self.pending_gaze.pitch:g} ms={self.pending_gaze.dwell_ms:g}"
                )
            elif command == "idle":
                if len(parts) != 2 or parts[1].lower() not in {"on", "off"}:
                    raise ValueError("usage: idle on|off")
                self.idle_blink_enabled = parts[1].lower() == "on"
                self.next_idle_blink_at = time.monotonic() + self.config.blink_interval_s
                self.log(f"idle blink {'on' if self.idle_blink_enabled else 'off'}")
            elif command == "interval":
                if len(parts) != 2:
                    raise ValueError("usage: interval SECONDS")
                interval = float(parts[1])
                if interval <= 0:
                    raise ValueError("interval must be > 0")
                self.config = EngineConfig(
                    **{**self.config.__dict__, "blink_interval_s": interval}
                )
                self.next_idle_blink_at = time.monotonic() + interval
                self.log(f"blink interval {interval:g}s")
            elif command == "poweroff":
                self.send_wire_command(POWER_OFF, expected_duration_s=POWER_OFF.delay_after)
            else:
                self.log(f"unknown command: {command}")
        except ValueError as exc:
            self.log(f"error: {exc}")

    def read_stdin(self) -> None:
        while True:
            readable, _, _ = select.select([sys.stdin], [], [], 0)
            if not readable:
                return
            line = sys.stdin.readline()
            if line == "":
                self.running = False
                return
            self.process_line(line)

    def run(self) -> None:
        self.open()
        try:
            while self.running:
                self.read_serial()
                self.update_board_state()
                self.read_stdin()
                self.maybe_start_next()
                time.sleep(0.01)
        finally:
            self.close()


def main() -> int:
    args = parse_args()
    if args.blink_interval <= 0:
        print("--blink-interval must be > 0", file=sys.stderr)
        return 2
    if args.gaze_blink_threshold < 0:
        print("--gaze-blink-threshold must be >= 0", file=sys.stderr)
        return 2
    if args.gaze_blink_refractory < 0:
        print("--gaze-blink-refractory must be >= 0", file=sys.stderr)
        return 2
    config = EngineConfig(
        port=args.port,
        baudrate=args.baudrate,
        gaze_sample_ms=args.gaze_sample_ms,
        eyelid_offset=args.eyelid_offset,
        idle_blink=args.idle_blink,
        blink_interval_s=args.blink_interval,
        gaze_blink_threshold_deg=args.gaze_blink_threshold,
        gaze_blink_refractory_s=args.gaze_blink_refractory,
        blink_close_ms=args.blink_close_ms,
        blink_hold_ms=args.blink_hold_ms,
        blink_open_ms=args.blink_open_ms,
        blink_closed_angle=args.blink_closed_angle,
        feedback_silence_s=args.feedback_silence,
        done_fallback_s=args.done_fallback,
        verbose=args.verbose,
    )
    try:
        RobotEngine(config).run()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
