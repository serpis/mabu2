# `robot_engine.py`

`robot_engine.py` är en enkel foreground-runtime ovanpå `robot_animation.py`.
Den äger UART:en och läser kommandon från stdin. Poängen är att inget annat
ska skicka scripts direkt till motorboardet medan engine kör.

Start:

```bash
python3 /home/pi/robot_engine.py
```

Utan automatiska idle-blinkar:

```bash
python3 /home/pi/robot_engine.py --no-idle-blink
```

## Kommandon på stdin

```text
gaze YAW[,PITCH] [DURATION_MS]
gaze YAW PITCH [DURATION_MS]
blink
stretch
idle on
idle off
interval SECONDS
status
quit
```

Exempel:

```text
gaze 25,5 1500
blink
stretch
gaze -20 0 1200
idle off
status
quit
```

## Timeline

Engine skiljer på schemaläggning och rendering. Kommandon lägger
högnivåevents på en tidslinje:

```text
TimelineGaze(start, yaw, pitch, duration)
TimelineBlink(start, reason)
TimelineNeckStretch(start, amplitudes, duration)
```

När motorboardet är idle och det finns ett event vid `t=0` renderar engine
hela tidslinjen fram till nästa idle-punkt. Det betyder att gaze, blink och
andra overlays samplas tillsammans och skickas som ett komplett script. Gaze
och blink använder sex kanaler; neck stretch använder sju eftersom `neck_tilt`
behövs. Nya `gaze`-kommandon läggs vid `t=0` om boarden är idle, annars efter
det script eller den explicita gaze som redan ligger först i kön.

Idle-blinkar är inte permanenta köposter. De genereras från `--blink-interval`
när roboten faktiskt är idle, och framtida idle-blinkar kan flyttas om ett
explicit gaze-kommando kommer in.

## Idle-detektion

Engine räknar själv ut när ett skickat script borde vara klart:

```text
expected_done_at = tx_time + sum(duration_ticks) * 10 ms
```

Boarden betraktas som idle när tiden har passerat och feedback varit tyst en
kort stund. Om feedback beter sig konstigt finns en fallback-timeout så engine
inte fastnar i `settling`.

## Blink

Idle-blinkar schemaläggs ungefär var `--blink-interval` sekund när roboten är
idle. Blink renderas med samma timeline-renderare som gaze: om inget gaze är
aktivt blir det ett 6-kanals script där ögon/nacke hålls vid aktuell pose och
ögonlocken blinkar.

När en blink bakas in nära slutet av ett gaze-script förlängs renderfönstret
tills blinkens öppningsfas är klar. Det merge:ade resultatet ska alltid
innehålla hela gaze-rörelsen och hela blinkrörelsen.

Defaultblinket är servoanpassat till `360 ms` stängning, `0 ms` hold och
`360 ms` öppning.

Stora gaze-hopp blinkar också automatiskt. Engine mäter vinkelavståndet från
senast kända feedbackpose till ny target, och om hoppet är större än
`--gaze-blink-threshold` grader, default `15`, bakas en blink in vid start av
gaze-scriptet. Automatiska blinkar spärras av `--gaze-blink-refractory`
sekunder, default `3`, så en ny gaze-blink eller idle-blink hoppas över om
senaste blink var för nära i tid.

Blinklagret går mot underliggande basposition:

```text
base_eyelid = eyelid_offset - eye_pitch
final_eyelid = mix(base_eyelid, closed_eyelid, blink_weight)
```

Det betyder att gaze inte behöver känna till blink och blink inte behöver veta
vilken gaze som är aktiv.

## Neck Stretch

`stretch` och `neck-stretch` schemalägger en nackstretch efter redan köade
explicita events. Om engine är idle körs den direkt.

Stretchen är gaze-preserving. Den utgår från aktuell blickriktning:

```text
target_yaw = eye_leftright + neck_rotation
target_pitch = eye_updown + neck_elevation
```

Sedan läggs mjuka offsets på nackens elevation/rotation/tilt, medan ögonen
kompenserar yaw/pitch så att samma target behålls under rörelsen. Vid mekaniska
gränser reduceras nack-offseten hellre än att target tappas.

Stretchen använder större amplituder och snabbare stretch-respons än vanlig
gaze, så den ska läsa mer som fabriksappens neck-stretch utan att lämna roboten
i neutral blick efteråt.
