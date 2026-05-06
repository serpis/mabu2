# Eye Up/Down PID Sweep

Captured: `2026-05-05T20:49:11.775359+00:00`

Lower score is better: `0.20 * mean_residual_rms + 0.45 * mean_hold_tail_residual_p2p + 0.35 * mean_hold_residual_rms`.

| Rank | Variant | P | I | D | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg | Max hold tail p2p deg | Score |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | p1p3_i0p0003_d8 | 1.3 | 0.0003 | 8 | 2.690 | 2.232 | 5.058 | 7.235 | 3.595 |
| 2 | p1p2_i0p0002_d8 | 1.2 | 0.0002 | 8 | 2.811 | 2.425 | 5.058 | 6.529 | 3.687 |
| 3 | p1p4_i0p0002_d9 | 1.4 | 0.0002 | 9 | 2.960 | 2.365 | 6.588 | 13.823 | 4.384 |
| 4 | p1p2_i0p0003_d9 | 1.2 | 0.0003 | 9 | 2.973 | 2.423 | 6.999 | 12.529 | 4.592 |
| 5 | coarse_best_p1p3_i0p0002_d8 | 1.3 | 0.0002 | 8 | 3.052 | 2.652 | 7.029 | 14.764 | 4.702 |
| 6 | p1p5_i0p0003_d10 | 1.5 | 0.0003 | 10 | 3.217 | 2.695 | 7.176 | 14.764 | 4.816 |
| 7 | p1p2_i0p0001_d7 | 1.2 | 0.0001 | 7 | 3.130 | 2.845 | 7.176 | 14.411 | 4.851 |
| 8 | p1p1_i0p0001_d6 | 1.1 | 0.0001 | 6 | 3.315 | 2.992 | 8.088 | 15.823 | 5.350 |
| 9 | control_current_p1p9_i0p0008_d8 | 1.9 | 0.0008 | 8 | 3.071 | 2.450 | 8.735 | 20.882 | 5.402 |
| 10 | p1p4_i0p0001_d8 | 1.4 | 0.0001 | 8 | 3.221 | 2.981 | 8.382 | 15.000 | 5.459 |
| 11 | p1p3_i0p0001_d7 | 1.3 | 0.0001 | 7 | 3.233 | 3.104 | 8.382 | 17.000 | 5.505 |
| 12 | p1p5_i0p0001_d8 | 1.5 | 0.0001 | 8 | 3.159 | 2.710 | 8.735 | 18.647 | 5.511 |
| 13 | p1p3_i0p0002_d9 | 1.3 | 0.0002 | 9 | 3.177 | 2.809 | 8.882 | 17.117 | 5.616 |
| 14 | p1p4_i0p0003_d10 | 1.4 | 0.0003 | 10 | 3.285 | 2.849 | 9.000 | 16.176 | 5.704 |
| 15 | p1p5_i0p0002_d9 | 1.5 | 0.0002 | 9 | 3.369 | 3.293 | 10.441 | 21.588 | 6.525 |

## Per Speed

### control_current_p1p9_i0p0008_d8  P=1.9 I=0.0008 D=8

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 6.268 | 4.739 | 20.882 |
| 10 | 100 | 3.283 | 2.497 | 8.294 |
| 18 | 180 | 1.566 | 1.505 | 3.235 |
| 32 | 320 | 1.166 | 1.058 | 2.529 |

### coarse_best_p1p3_i0p0002_d8  P=1.3 I=0.0002 D=8

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 5.595 | 4.538 | 14.764 |
| 10 | 100 | 3.150 | 2.333 | 5.235 |
| 18 | 180 | 2.006 | 1.703 | 3.588 |
| 32 | 320 | 1.457 | 2.034 | 4.529 |

### p1p1_i0p0001_d6  P=1.1 I=0.0001 D=6

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 5.700 | 4.403 | 15.823 |
| 10 | 100 | 3.735 | 2.977 | 6.882 |
| 18 | 180 | 2.236 | 2.303 | 4.882 |
| 32 | 320 | 1.589 | 2.287 | 4.764 |

### p1p2_i0p0001_d7  P=1.2 I=0.0001 D=7

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 5.624 | 4.654 | 14.411 |
| 10 | 100 | 3.296 | 2.397 | 4.999 |
| 18 | 180 | 2.068 | 2.106 | 4.764 |
| 32 | 320 | 1.531 | 2.222 | 4.529 |

### p1p2_i0p0002_d8  P=1.2 I=0.0002 D=8

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 4.706 | 3.209 | 6.529 |
| 10 | 100 | 3.149 | 2.343 | 4.882 |
| 18 | 180 | 2.021 | 2.151 | 4.294 |
| 32 | 320 | 1.369 | 1.998 | 4.529 |

### p1p2_i0p0003_d9  P=1.2 I=0.0003 D=9

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 5.175 | 3.333 | 12.529 |
| 10 | 100 | 3.215 | 2.375 | 7.352 |
| 18 | 180 | 1.974 | 1.900 | 3.705 |
| 32 | 320 | 1.527 | 2.082 | 4.411 |

### p1p3_i0p0001_d7  P=1.3 I=0.0001 D=7

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 5.901 | 5.145 | 17.000 |
| 10 | 100 | 3.391 | 2.904 | 7.352 |
| 18 | 180 | 2.049 | 2.255 | 4.529 |
| 32 | 320 | 1.593 | 2.111 | 4.646 |

### p1p3_i0p0002_d9  P=1.3 I=0.0002 D=9

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 5.782 | 4.768 | 17.117 |
| 10 | 100 | 3.620 | 2.788 | 10.411 |
| 18 | 180 | 1.844 | 1.672 | 3.588 |
| 32 | 320 | 1.463 | 2.010 | 4.411 |

### p1p3_i0p0003_d8  P=1.3 I=0.0003 D=8

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 4.173 | 2.844 | 7.235 |
| 10 | 100 | 3.129 | 2.208 | 4.882 |
| 18 | 180 | 1.989 | 1.847 | 3.823 |
| 32 | 320 | 1.469 | 2.030 | 4.294 |

### p1p4_i0p0001_d8  P=1.4 I=0.0001 D=8

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 5.925 | 4.812 | 15.000 |
| 10 | 100 | 3.452 | 3.235 | 10.176 |
| 18 | 180 | 1.974 | 1.990 | 4.058 |
| 32 | 320 | 1.534 | 1.885 | 4.294 |

### p1p4_i0p0002_d9  P=1.4 I=0.0002 D=9

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 5.417 | 3.849 | 13.823 |
| 10 | 100 | 3.009 | 2.173 | 4.999 |
| 18 | 180 | 2.011 | 1.475 | 3.470 |
| 32 | 320 | 1.402 | 1.964 | 4.058 |

### p1p4_i0p0003_d10  P=1.4 I=0.0003 D=10

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 5.704 | 4.502 | 16.176 |
| 10 | 100 | 3.689 | 2.679 | 10.176 |
| 18 | 180 | 2.267 | 2.189 | 4.882 |
| 32 | 320 | 1.482 | 2.025 | 4.764 |

### p1p5_i0p0001_d8  P=1.5 I=0.0001 D=8

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 6.213 | 4.910 | 18.647 |
| 10 | 100 | 3.116 | 2.418 | 8.411 |
| 18 | 180 | 1.737 | 1.621 | 3.588 |
| 32 | 320 | 1.570 | 1.893 | 4.294 |

### p1p5_i0p0002_d9  P=1.5 I=0.0002 D=9

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 6.116 | 5.675 | 21.588 |
| 10 | 100 | 3.958 | 3.871 | 12.411 |
| 18 | 180 | 1.906 | 1.804 | 3.588 |
| 32 | 320 | 1.495 | 1.823 | 4.176 |

### p1p5_i0p0003_d10  P=1.5 I=0.0003 D=10

| Move ticks | Move ms | Mean residual RMS deg | Mean hold RMS deg | Mean hold tail p2p deg |
|---:|---:|---:|---:|---:|
| 6 | 60 | 5.684 | 4.307 | 14.764 |
| 10 | 100 | 3.926 | 3.026 | 6.058 |
| 18 | 180 | 1.826 | 1.584 | 3.588 |
| 32 | 320 | 1.434 | 1.865 | 4.294 |

## Recommendation

Best measured candidate: `p1p3_i0p0003_d8` with `P=1.3, I=0.0003, D=8`.

Use this as the next visual test point, then tune around it with smaller steps.
