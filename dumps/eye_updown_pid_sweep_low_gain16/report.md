# Eye Up/Down PID Sweep

Captured: `2026-05-05T20:45:28.741198+00:00`

Lower score is better: `0.20 * mean_residual_rms + 0.45 * mean_hold_tail_residual_p2p + 0.35 * mean_hold_residual_rms`.

| Rank | Variant | P | I | D | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg | Max hold tail p2p deg | Score |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | p1p3_i0p0002_d8 | 1.3 | 0.0002 | 8 | 2.871 | 2.318 | 6.676 | 11.117 | 4.390 |
| 2 | p1p1_i0p0001_d6 | 1.1 | 0.0001 | 6 | 3.165 | 2.783 | 7.323 | 13.588 | 4.902 |
| 3 | p0p9_i0_d4 | 0.9 | 0 | 4 | 3.724 | 3.541 | 7.529 | 12.647 | 5.372 |
| 4 | p0p9_i0p0001_d4 | 0.9 | 0.0001 | 4 | 3.709 | 3.338 | 7.705 | 14.294 | 5.377 |
| 5 | p1p1_i0_d4 | 1.1 | 0 | 4 | 3.469 | 3.195 | 8.000 | 13.941 | 5.412 |
| 6 | p0p9_i0_d2 | 0.9 | 0 | 2 | 3.940 | 3.570 | 7.647 | 13.470 | 5.478 |
| 7 | p1p3_i0_d6 | 1.3 | 0 | 6 | 3.334 | 2.981 | 8.411 | 17.353 | 5.495 |
| 8 | p0p7_i0_d2 | 0.7 | 0 | 2 | 4.594 | 4.582 | 9.235 | 14.764 | 6.678 |
| 9 | p0p7_i0p00005_d4 | 0.7 | 5e-05 | 4 | 4.374 | 4.681 | 9.264 | 13.823 | 6.682 |
| 10 | p0p7_i0_d4 | 0.7 | 0 | 4 | 4.368 | 4.546 | 9.441 | 15.117 | 6.713 |
| 11 | p0p5_i0_d4 | 0.5 | 0 | 4 | 5.635 | 6.581 | 13.441 | 19.588 | 9.479 |
| 12 | p0p5_i0_d2 | 0.5 | 0 | 2 | 5.767 | 6.564 | 13.617 | 20.294 | 9.579 |
| 13 | p0p5_i0_d1 | 0.5 | 0 | 1 | 5.785 | 6.615 | 13.588 | 20.647 | 9.587 |
| 14 | p0p3_i0_d2 | 0.3 | 0 | 2 | 8.153 | 10.343 | 21.176 | 25.824 | 14.780 |
| 15 | p0p3_i0_d1 | 0.3 | 0 | 1 | 8.255 | 10.436 | 21.647 | 25.824 | 15.045 |
| 16 | p0p3_i0_d0p5 | 0.3 | 0 | 0.5 | 8.547 | 10.735 | 22.059 | 29.000 | 15.393 |

## Per Speed

### p0p3_i0_d0p5  P=0.3 I=0 D=0.5

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 10.948 | 13.881 | 29.000 |
| 10 | 100 | 9.561 | 12.190 | 25.000 |
| 18 | 180 | 7.721 | 9.712 | 20.176 |
| 32 | 320 | 5.957 | 7.156 | 14.058 |

### p0p3_i0_d1  P=0.3 I=0 D=1

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 9.895 | 12.498 | 25.824 |
| 10 | 100 | 9.319 | 11.811 | 24.765 |
| 18 | 180 | 7.803 | 9.880 | 20.882 |
| 32 | 320 | 6.000 | 7.556 | 15.117 |

### p0p3_i0_d2  P=0.3 I=0 D=2

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 10.084 | 12.659 | 25.824 |
| 10 | 100 | 9.065 | 11.646 | 24.412 |
| 18 | 180 | 7.564 | 9.627 | 19.706 |
| 32 | 320 | 5.899 | 7.443 | 14.764 |

### p0p5_i0_d1  P=0.5 I=0 D=1

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 8.634 | 9.920 | 20.647 |
| 10 | 100 | 6.644 | 7.092 | 14.529 |
| 18 | 180 | 4.420 | 5.057 | 10.176 |
| 32 | 320 | 3.441 | 4.390 | 9.000 |

### p0p5_i0_d2  P=0.5 I=0 D=2

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 8.498 | 9.739 | 20.294 |
| 10 | 100 | 6.275 | 6.906 | 15.000 |
| 18 | 180 | 4.825 | 5.087 | 10.176 |
| 32 | 320 | 3.471 | 4.523 | 9.000 |

### p0p5_i0_d4  P=0.5 I=0 D=4

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 8.375 | 9.528 | 19.588 |
| 10 | 100 | 6.244 | 7.054 | 14.411 |
| 18 | 180 | 4.502 | 5.237 | 10.764 |
| 32 | 320 | 3.419 | 4.506 | 9.000 |

### p0p7_i0_d2  P=0.7 I=0 D=2

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 7.069 | 7.137 | 14.764 |
| 10 | 100 | 5.022 | 4.418 | 8.647 |
| 18 | 180 | 3.434 | 3.659 | 7.235 |
| 32 | 320 | 2.849 | 3.112 | 6.294 |

### p0p7_i0_d4  P=0.7 I=0 D=4

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 6.665 | 6.758 | 15.117 |
| 10 | 100 | 4.766 | 4.657 | 9.235 |
| 18 | 180 | 3.226 | 3.585 | 6.999 |
| 32 | 320 | 2.814 | 3.185 | 6.411 |

### p0p7_i0p00005_d4  P=0.7 I=5e-05 D=4

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 6.623 | 7.024 | 13.823 |
| 10 | 100 | 4.824 | 4.762 | 9.117 |
| 18 | 180 | 3.303 | 3.759 | 7.705 |
| 32 | 320 | 2.747 | 3.178 | 6.411 |

### p0p9_i0_d2  P=0.9 I=0 D=2

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 6.319 | 5.642 | 13.470 |
| 10 | 100 | 4.282 | 3.258 | 6.176 |
| 18 | 180 | 3.074 | 2.864 | 5.823 |
| 32 | 320 | 2.086 | 2.514 | 5.117 |

### p0p9_i0_d4  P=0.9 I=0 D=4

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 6.109 | 5.388 | 12.647 |
| 10 | 100 | 3.988 | 3.282 | 6.294 |
| 18 | 180 | 2.786 | 2.940 | 5.941 |
| 32 | 320 | 2.010 | 2.553 | 5.235 |

### p0p9_i0p0001_d4  P=0.9 I=0.0001 D=4

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 6.112 | 5.020 | 14.294 |
| 10 | 100 | 4.099 | 3.234 | 6.294 |
| 18 | 180 | 2.755 | 2.598 | 5.117 |
| 32 | 320 | 1.868 | 2.499 | 5.117 |

### p1p1_i0_d4  P=1.1 I=0 D=4

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 5.948 | 4.762 | 13.941 |
| 10 | 100 | 3.676 | 3.259 | 8.058 |
| 18 | 180 | 2.477 | 2.459 | 5.235 |
| 32 | 320 | 1.776 | 2.300 | 4.764 |

### p1p1_i0p0001_d6  P=1.1 I=0.0001 D=6

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 5.413 | 4.173 | 13.588 |
| 10 | 100 | 3.524 | 2.561 | 5.235 |
| 18 | 180 | 2.111 | 2.135 | 5.823 |
| 32 | 320 | 1.610 | 2.264 | 4.646 |

### p1p3_i0_d6  P=1.3 I=0 D=6

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 6.092 | 5.161 | 17.353 |
| 10 | 100 | 3.307 | 2.736 | 5.941 |
| 18 | 180 | 2.325 | 1.805 | 3.588 |
| 32 | 320 | 1.613 | 2.223 | 6.764 |

### p1p3_i0p0002_d8  P=1.3 I=0.0002 D=8

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 4.930 | 3.343 | 11.117 |
| 10 | 100 | 2.968 | 1.903 | 4.646 |
| 18 | 180 | 1.932 | 1.806 | 4.294 |
| 32 | 320 | 1.653 | 2.220 | 6.647 |

## Recommendation

Best measured candidate: `p1p3_i0p0002_d8` with `P=1.3, I=0.0002, D=8`.

Use this as the next visual test point, then tune around it with smaller steps.
