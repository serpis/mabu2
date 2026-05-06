# Eye Up/Down Low-Gain PID Analysis

Date: 2026-05-05

This analysis follows the low-gain tuning pass for the new board's shaky
`eye_updown` motion. The goal was to include the very aggressive 60 ms case,
not only the more practical 100 ms and slower cases.

## Test Setup

- Channel: `eye_updown`
- Move timings: 60 ms, 100 ms, 180 ms, 320 ms
- Coarse sweep: 16 variants, including lower gains down to `P=0.3`
- Fine sweep: 15 variants around the best coarse region
- Score: `0.20 * mean_residual_rms + 0.45 * mean_hold_tail_p2p + 0.35 * mean_hold_rms`

The score intentionally weights hold tail peak-to-peak strongly because that
captures the visible shake after the scripted move should have settled.

## Coarse Sweep Result

Top candidates from `dumps/eye_updown_pid_sweep_low_gain16/report.md`:

| Rank | PID | Score | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---|---:|---:|---:|---:|
| 1 | `P=1.3 I=0.0002 D=8` | 4.390 | 2.871 | 2.318 | 6.676 |
| 2 | `P=1.1 I=0.0001 D=6` | 4.902 | 3.165 | 2.783 | 7.323 |
| 3 | `P=0.9 I=0 D=4` | 5.372 | 3.724 | 3.541 | 7.529 |
| 4 | `P=0.9 I=0.0001 D=4` | 5.377 | 3.709 | 3.338 | 7.705 |

Very low gain values such as `P=0.3` and `P=0.5` were stable-looking in the
sense that they avoided aggressive correction, but they were too slow and left
large residual errors. They are not useful for gaze.

## Fine Sweep Result

Top candidates from `dumps/eye_updown_pid_sweep_low_gain_fine/report.md`:

| Rank | PID | Score | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---|---:|---:|---:|---:|
| 1 | `P=1.3 I=0.0003 D=8` | 3.595 | 2.690 | 2.232 | 5.058 |
| 2 | `P=1.2 I=0.0002 D=8` | 3.687 | 2.811 | 2.425 | 5.058 |
| 3 | `P=1.4 I=0.0002 D=9` | 4.384 | 2.960 | 2.365 | 6.588 |
| 4 | `P=1.2 I=0.0003 D=9` | 4.592 | 2.973 | 2.423 | 6.999 |
| 9 | current control `P=1.9 I=0.0008 D=8` | 5.402 | 3.071 | 2.450 | 8.735 |

## 60 ms Comparison

The 60 ms case is the clearest reason to move lower than the previous tuned
value:

| PID | Residual RMS deg | Hold RMS deg | Hold tail p2p deg |
|---|---:|---:|---:|
| current `P=1.9 I=0.0008 D=8` | 6.268 | 4.739 | 20.882 |
| recommended `P=1.3 I=0.0003 D=8` | 4.173 | 2.844 | 7.235 |
| practical-best `P=1.4 I=0.0002 D=9` | 5.417 | 3.849 | 13.823 |

## Practical >=100 ms Comparison

If the renderer avoids 60 ms jumps, the ranking is tighter:

| Rank | PID | Score >=100 ms | Residual RMS deg | Hold RMS deg | Hold tail p2p deg |
|---:|---|---:|---:|---:|---:|
| 1 | `P=1.4 I=0.0002 D=9` | 2.962 | 2.141 | 1.871 | 4.176 |
| 2 | `P=1.3 I=0.0003 D=8` | 3.099 | 2.196 | 2.028 | 4.333 |
| 3 | current `P=1.9 I=0.0008 D=8` | 3.100 | 2.005 | 1.687 | 4.686 |
| 4 | coarse best `P=1.3 I=0.0002 D=8` | 3.152 | 2.204 | 2.023 | 4.450 |

The old `P=1.9 I=0.0008 D=8` is still competitive at 100 ms and slower, but it
is much worse at 60 ms. Since this pass explicitly included 60 ms, the more
robust choice is `P=1.3 I=0.0003 D=8`.

## Recommendation

Use:

`eye_updown: P=1.3, I=0.0003, D=8`

This has been written to the board and verified by `read-pid`; the raw
write/read log is in
`dumps/eye_updown_pid_sweep_low_gain_fine/recommended_write_and_read_pid.log`.

Next visual check: run gaze and corner demos with this setting. If it looks too
soft at normal speeds, the next alternative is `P=1.4 I=0.0002 D=9`, but that
setting is clearly less stable at 60 ms.
