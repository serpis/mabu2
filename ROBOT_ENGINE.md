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
gaze -20 0 1200
idle off
status
quit
```

Om flera `gaze` skickas medan motorboardet kör sparas bara den senaste som
pending gaze. Det undviker att engine bygger en lång intern kö.

## Idle-detektion

Engine räknar själv ut när ett skickat script borde vara klart:

```text
expected_done_at = tx_time + sum(duration_ticks) * 10 ms
```

Boarden betraktas som idle när tiden har passerat och feedback varit tyst en
kort stund. Om feedback beter sig konstigt finns en fallback-timeout så engine
inte fastnar i `settling`.

## Blink

Idle-blinkar injiceras ungefär var `--blink-interval` sekund. Om engine ändå
ska rendera ett gaze-script och en blink ligger inom scriptets tidsfönster
bakas blink in i samma 6-kanals keyframe-script. Om inget gaze körs skickas
blink som ett kort eyelid-only script när boarden är idle.

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
