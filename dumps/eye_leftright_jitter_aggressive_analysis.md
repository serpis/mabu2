# Eye Left/Right Aggressive Jitter Test

This test used the same aggressive movement profile for `eye_leftright` and
`eye_updown`:

- amplitude: about `-13.5 deg` to `+13.5 deg`
- move times: `40 ms`, `60 ms`, `80 ms`, `100 ms`, `180 ms`, `320 ms`
- four cycles per speed
- `10` tick endpoint holds

The `eye_updown` data was captured after the current tuned PID
`P=1.9, I=0.0008, D=8`. The `eye_leftright` PID was left unchanged.

## Per Speed

| Channel | Move ms | Residual RMS deg | Hold RMS deg | Hold tail residual p2p deg |
|---|---:|---:|---:|---:|
| eye_leftright | 40 | 13.454 | 16.128 | 35.717 |
| eye_leftright | 60 | 8.530 | 8.596 | 18.990 |
| eye_leftright | 80 | 5.166 | 3.307 | 9.330 |
| eye_leftright | 100 | 2.718 | 1.081 | 1.791 |
| eye_leftright | 180 | 2.107 | 0.993 | 2.262 |
| eye_leftright | 320 | 1.741 | 0.938 | 2.380 |
| eye_updown | 40 | 11.705 | 13.237 | 36.883 |
| eye_updown | 60 | 6.138 | 5.561 | 19.823 |
| eye_updown | 80 | 3.877 | 2.307 | 9.823 |
| eye_updown | 100 | 3.805 | 3.302 | 14.411 |
| eye_updown | 180 | 1.925 | 1.563 | 4.058 |
| eye_updown | 320 | 2.083 | 1.699 | 5.588 |

## Summary

For extremely aggressive 40-60 ms moves, both channels show large error and
large endpoint residual spread. This looks like a motion-profile/mechanical
limit rather than a problem unique to one PID axis.

For practical fast moves from 100 ms and up, `eye_leftright` is much cleaner:

| Channel | Mean residual RMS >=100 ms | Mean hold RMS >=100 ms | Mean hold tail p2p >=100 ms |
|---|---:|---:|---:|
| eye_leftright | 2.189 | 1.004 | 2.144 |
| eye_updown | 2.604 | 2.188 | 8.019 |

At exactly 100 ms the difference is especially clear:

- `eye_leftright`: hold tail residual p2p `1.791 deg`
- `eye_updown`: hold tail residual p2p `14.411 deg`

## Conclusion

I do not see the same practical shake in `eye_leftright`. It can be forced to
misbehave with 40-60 ms large-amplitude jumps, but at 100 ms and above it is
substantially more stable than `eye_updown`.

Recommendation: leave `eye_leftright` PID alone for now. The next improvement
should be in the animation renderer: enforce a higher minimum duration for
large `eye_updown` moves than for `eye_leftright`.
