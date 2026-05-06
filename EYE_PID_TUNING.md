# Eye PID Tuning

This note documents the eye servo jitter benchmark and the `eye_updown` PID
tuning pass for board 2.

## Scripts

- `eye_updown_jitter_benchmark.py` runs repeated raw-keyframe eye movements and
  records feedback from the motor board.
- `eye_updown_pid_sweep.py` writes a set of PID variants, runs the benchmark for
  each variant, and writes `summary.json` plus `report.md`.

Both scripts are intended to run on the Raspberry Pi, next to
`robot_motion.py`, using `/dev/ttyAMA0` by default.

## Benchmark

The benchmark drives a single eye channel between two angles and compares motor
feedback against the ideal scripted interpolation. It records:

- residual RMS in degrees
- hold residual RMS in degrees
- endpoint hold-tail peak-to-peak residual in degrees
- per-sample CSV feedback for later inspection

Example:

```bash
python3 /home/pi/eye_updown_jitter_benchmark.py \
  --channel eye_updown \
  --move-ticks 6,10,18,32 \
  --cycles 2 \
  --repeats 1 \
  --hold-ticks 10 \
  --settle 0.1 \
  --listen-after 0.1 \
  --output /home/pi/dumps/eye_updown_jitter.json \
  --csv /home/pi/dumps/eye_updown_jitter_samples.csv
```

With the current 10 ms motor-board tick assumption, the move timings above are
60 ms, 100 ms, 180 ms, and 320 ms.

## Sweep Presets

`eye_updown_pid_sweep.py` currently has these presets:

- `press`: earlier aggressive 10-candidate sweep around faster values.
- `low-gain16`: coarse low-gain pass with 16 variants, including `P=0.3`.
- `low-gain-fine`: fine pass around the best low-gain region.

Example coarse pass:

```bash
python3 /home/pi/eye_updown_pid_sweep.py \
  --preset low-gain16 \
  --output-dir /home/pi/dumps/eye_updown_pid_sweep_low_gain16 \
  --move-ticks 6,10,18,32 \
  --cycles 2 \
  --repeats 1 \
  --hold-ticks 10 \
  --settle 0.1 \
  --listen-after 0.1
```

Example fine pass:

```bash
python3 /home/pi/eye_updown_pid_sweep.py \
  --preset low-gain-fine \
  --output-dir /home/pi/dumps/eye_updown_pid_sweep_low_gain_fine \
  --move-ticks 6,10,18,32 \
  --cycles 2 \
  --repeats 1 \
  --hold-ticks 10 \
  --settle 0.1 \
  --listen-after 0.1
```

## Scoring

The low-gain sweep uses this score:

```text
0.20 * mean_residual_rms
+ 0.45 * mean_hold_tail_residual_p2p
+ 0.35 * mean_hold_residual_rms
```

Lower is better. The score intentionally weights the hold-tail peak-to-peak
metric heavily because that captures visible endpoint shake after the movement
should have settled.

## Result

The coarse pass found `P=1.3 I=0.0002 D=8` as the best low-gain region. The
fine pass found:

```text
eye_updown: P=1.3, I=0.0003, D=8
```

That value was written to the board and verified with `read-pid`.

Key comparison at the aggressive 60 ms movement:

| PID | Residual RMS deg | Hold RMS deg | Hold tail p2p deg |
|---|---:|---:|---:|
| previous `P=1.9 I=0.0008 D=8` | 6.268 | 4.739 | 20.882 |
| selected `P=1.3 I=0.0003 D=8` | 4.173 | 2.844 | 7.235 |

For movements of 100 ms and slower, `P=1.4 I=0.0002 D=9` was marginally better
in the numeric score, but it was much worse at 60 ms. Since the latest tuning
pass explicitly included 60 ms, `P=1.3 I=0.0003 D=8` is the selected robust
setting.

## Captured Reports

- `dumps/eye_leftright_jitter_aggressive_analysis.md`
- `dumps/eye_updown_pid_low_gain_analysis.md`
- `dumps/eye_updown_pid_sweep_low_gain16/report.md`
- `dumps/eye_updown_pid_sweep_low_gain_fine/report.md`
- `dumps/eye_updown_pid_sweep_low_gain_fine/recommended_write_and_read_pid.log`
