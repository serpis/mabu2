# `robot_animation.py`

`robot_animation.py` är ett högre lager ovanpå `robot_motion.py`.
Det resonerar i semantiska animationer och renderar sedan till ett enda
raw keyframe-script som motorboardet kan köra.

Det här lagret ska äga beteenden som gaze, blink, idle och uttryck.
`gaze` är alltså inte ett eget transportlager, utan en animation-primitive.

## Gaze-animation från CLI

Exempel:

```bash
python3 /home/pi/robot_animation.py \
  --gaze '-30,0,800;20,0,800;0,0,500'
```

Format:

```text
--gaze 'YAW,PITCH,DURATION_MS;YAW,PITCH,DURATION_MS;...'
```

Pitch kan utelämnas:

```bash
python3 /home/pi/robot_animation.py --gaze '-30,800;20,800;0,500'
```

Visa vad som renderas utan att öppna serialporten:

```bash
python3 /home/pi/robot_animation.py \
  --gaze '-30,0,800;20,0,800;0,0,500' \
  --print-keyframes \
  --dry-run
```

## Vad gaze renderar till

Gaze använder samma yaw/pitch-mappning som `robot_motion.py --look`.
Default-kanalerna är:

```text
eyelid_left, eyelid_right,
eye_leftright, eye_updown,
neck_elevation, neck_rotation
```

`neck_tilt` lämnas utanför default-renderingen för gaze och står kvar i
neutral startpose.

Varje gaze-punkt blir en keyframe med råa `0..255`-targets och en duration i
ticks. På den testade motorbrädan är en tick ungefär `10 ms`.

Default för `--eyelid-offset` är `-2`. För yaw-demo skickas först en startpose
med samma eyelid-offset, och sedan körs yaw-scriptet bara på ögon/nackrotation.

## Begränsningar

Renderingen skapar ett enda motorboard-script. Den försöker inte skicka nästa
script medan det första kör, eftersom overlap-testen visade att motorboardet
inte hanterar det som en pålitlig kö.

För gaze med sex kanaler ryms maximalt:

```text
3 headerbytes + 7 bytes per frame <= 255
max 36 frames
```

Långa durationer över `255` ticks delas upp i flera identiska keyframes, men
hela animationen måste fortfarande rymmas i ett motorboard-script.

Animationlagret ska resonera i grader och fysiska begränsningar. Bytes är ett
renderingsformat mot `robot_motion.py`, inte beteendemodellen.

Uppmätta hastighetsgränser används som konservativa defaults:

```text
eyelid_left      220 deg/s
eyelid_right     220 deg/s
eye_leftright    350 deg/s
eye_updown       450 deg/s
neck_elevation    75 deg/s
neck_rotation     60 deg/s
neck_tilt         55 deg/s
```

Biblioteksfunktionerna `min_duration_ms_for_angle_step()` och
`validate_angle_step_duration()` finns för att framtida animationer ska kunna
kontrollera om ett tänkt steg faktiskt hinner genomföras.

## Neck stretch

Neck stretch är inte en rå kopia av fabriksanimationen
`Neck_Elevation_Stretch`, eftersom den skulle flytta blickriktningen.
I animationslagret är stretch i stället en overlay runt aktuell gaze-bas:

```bash
python3 /home/pi/robot_animation.py --neck-stretch --verbose
```

Renderingen skickar sju kanaler:

```text
eyelid_left, eyelid_right,
eye_leftright, eye_updown,
neck_elevation, neck_rotation, neck_tilt
```

Nackens `neck_elevation`, `neck_rotation` och `neck_tilt` får mjuka relativa
offsets. Samtidigt räknas ögonens yaw/pitch om som:

```text
eye = target_gaze - actual_neck
```

Det gör att roboten behåller samma gaze under och efter stretchen så långt
ögonens mätta rörelseutrymme räcker. Om blicken redan ligger nära en mekanisk
gräns clampas nack-offseten hellre ned än att gaze tappar target.

Default är närmare uttrycket i fabriksanimationen `Neck_Elevation_Stretch`,
men fortfarande gaze-preserving:

```text
pitch amplitude 13 deg
yaw amplitude    9 deg
tilt amplitude   7 deg
active duration  3200 ms
settle time       500 ms
```

Stretch-offseten har en egen snabbare nackrespons än vanlig gaze. Vanlig
gaze får alltså behålla sin långsamma nackföljning, medan stretch faktiskt når
fram till tydliga nackvändningar innan den går tillbaka.

## Speech motion

Speech motion använder samma gaze-preserving nack-overlay-lager som stretch,
men med små, korta och kontinuerligt chunkade offsets för prat:

```text
neck_rotation  +/-3.0 deg
neck_elevation +/-2.2 deg
neck_tilt      +/-2.0 deg
```

Ljud-dashboarden startar inte någon egen motorstyrning. Den markerar bara att
ljud spelas; `RobotEngine` bakar sedan in korta speech-motion chunks i nästa
timeline-render. Renderingen använder fortsatt:

```text
eye = target_gaze - actual_neck
```

Det betyder att samma blickmål behålls även när pratlagret flyttar nacken.
Eftersom rörelsen chunkas kan nytt ljud avbryta gammalt ljud utan att roboten
fastnar i en lång, redan renderad huvudrörelse.

## Yaw-gaze med ögon först

För naturlig blick i yaw-led beskriver lagret först en targetkurva:

```text
target_yaw(t) -> grader
```

Kurvan byggs av enkla segment:

```python
HoldYaw(start_ms, end_ms, yaw)
LinearYaw(start_ms, end_ms, start_yaw, end_yaw)
EaseYaw(start_ms, end_ms, start_yaw, end_yaw)
```

Själva gaze-beteendet ligger sedan i en controller:

```text
neck_yaw följer target långsamt, max 60 deg/s default
eye_yaw följer target - neck_yaw snabbt, max 350 deg/s default
eye_yaw clampas till ungefär +/-15 grader
```

Det gör att targethopp ger snabb ögonrörelse först, medan nacken följer efter
och ögonen gradvis återgår närmare centrum.

Demo-sekvensen är:

```text
1. target hoppar 0° -> +25°
2. target hoppar +25° -> -20°
3. target går långsamt -20° -> +20°
```

Kör alla delsekvenser:

```bash
python3 /home/pi/robot_animation.py --demo-gaze-yaw --verbose
```

Kör en eller flera delsekvenser:

```bash
python3 /home/pi/robot_animation.py --demo-gaze-yaw 1 --verbose
python3 /home/pi/robot_animation.py --demo-gaze-yaw 2,3 --verbose
```

Om del 2 eller 3 körs fristående innehåller scriptet en kort prep-fas till
delens startvinkel innan själva delsekvensen börjar.

Inspektera kurvan och raw keyframes:

```bash
python3 /home/pi/robot_animation.py \
  --demo-gaze-yaw 2 \
  --print-samples \
  --print-keyframes \
  --dry-run
```

Yaw-demo renderas till två kanaler:

```text
mask=0x12
channels=eye_leftright,neck_rotation
```

Default-sampling är `50 ms`, alltså `5` duration ticks per frame. Om en kurva
blir för lång för ett motorboard-script kan `--gaze-yaw-sample-ms` höjas.
Hastighetsgränserna kan justeras för experiment:

```bash
python3 /home/pi/robot_animation.py \
  --demo-gaze-yaw \
  --gaze-yaw-neck-max-speed 55 \
  --gaze-yaw-eye-max-speed 350
```

## Sätt blick mot enskild punkt

`--gaze-to YAW[,PITCH]` flyttar blicken till en angiven vinkel med samma
"ögon först, nacke ikapp"-controller som hörndemoen. Den läser robotens
nuvarande servoposition från position-feedback och använder den som
controllerns starttillstånd, så två anrop i följd bygger på varandra
istället för att stega tillbaka till `(0, 0)`.

`gaze-to` renderas till sex kanaler, så ögonlocken följer pitch-rörelsen:

```text
eyelid_left, eyelid_right,
eye_leftright, eye_updown,
neck_elevation, neck_rotation
```

Ögonlockens target är samma relation som `robot_motion.py --look` använder:
`eyelid_offset - eye_pitch`. Default `--eyelid-offset` är `-2`.

```bash
python3 /home/pi/robot_animation.py --gaze-to 25,5 --verbose
python3 /home/pi/robot_animation.py --gaze-to -20,-8 --verbose
```

För att läsa pose pingar scriptet motorboardet med opcode `0x40`
(`read_vr_values`) direkt efter power-on, eftersom motorboardet annars
inte garanterat strömmar feedback i idle.

Defaults:

```text
--gaze-to-dwell-ms     1500   total scriptlängd för rörelsen
--gaze-to-sample-ms      50   samplingsintervall för controllern
--gaze-to-listen-ms     200   väntefönster för pose-snapshot
```

För större hopp (>30°) kan default-dwell vara för kort för att nacken
ska hinna fram. Höj `--gaze-to-dwell-ms` då. Eftersom `gaze-to` nu använder
sex kanaler ryms maximalt 36 frames i ett motorboard-script; om du går över
cirka `1800 ms` med default `50 ms` sampling behöver du även höja
`--gaze-to-sample-ms`.

Blink kan bakas in i samma gaze-script:

```bash
python3 /home/pi/robot_animation.py \
  --gaze-to 25,5 \
  --blink-at-ms 500
```

`--dry-run` antar startpose `(0, 0)` eftersom ingen feedback läses.

## Blink

Fristående blink:

```bash
python3 /home/pi/robot_animation.py --blink --verbose
```

`--blink` skickar bara ögonlockskanalerna (`mask=0x60`). Den läser aktuell
`eye_updown` från feedback och räknar ut den underliggande icke-blinkande
ögonlockspositionen med samma relation som gaze:

```text
base_eyelid = eyelid_offset - eye_pitch
```

Sedan renderas ett kort raw script som interpolerar från aktuell motorposition
till stängt ögonlock och tillbaka till `base_eyelid`. Blink återställer alltså
inte till den uppmätta ögonlockspositionen om den råkar avvika; den går tillbaka
till vad ögonlocket borde vara utan blink.

Defaults:

```text
--blink-close-ms      360
--blink-hold-ms         0
--blink-open-ms       360
--blink-closed-angle  -65
```

De här tiderna är valda efter uppmätt ögonlockshastighet. Blinket har ingen
hold-fas; stängning och öppning är långa nog för att servot ska hinna nära
`--blink-closed-angle`.

Viktigt: skicka inte `--blink` som ett separat kommando medan ett annat
motion-script kör. Motorboardet är inte en pålitlig scriptkö. För blink under
gaze ska blink bakas in i samma renderade script med `--blink-at-ms`, t.ex.:

```bash
python3 /home/pi/robot_animation.py \
  --demo-gaze-corners \
  --blink-at-ms 700,1500
```

När blink bakas in i ett gaze-script renderas hela unionen av båda
animationerna. Om en blink börjar nära slutet av gaze-fönstret förlängs alltså
det merge:ade scriptet tills blinkens öppningsfas är klar, så ögonlocken inte
stannar halvvägs.

## Pitch/yaw-hörn

Hörndemoen använder samma "ögon först, nacke ikapp"-controller som
yaw-demoen, fast på två axlar. Den definierar en yaw-targetkurva och en
pitch-targetkurva som gör stegvisa hopp mellan hörnen, och simulerar två
parallella first-order controllers vid sampling. Renderingen blir ett
6-kanals raw script, så ögonlocken följer pitch-rörelsen:

```text
eyelid_left, eyelid_right,
eye_leftright, eye_updown,
neck_elevation, neck_rotation
```

Sekvensen går till övre vänster, övre höger, nedre höger, nedre vänster
och tillbaka till centrum:

```bash
python3 /home/pi/robot_animation.py --demo-gaze-corners --verbose
```

Default är `yaw=20°`, `pitch=10°`, `200 ms` settle innan första hörnet,
`450 ms` målhållning per hörn och `600 ms` målhållning på retur till
centrum. Eftersom ögonen klampas till ±15° yaw / ±10° pitch leder de
varje hopp och nacken får komma efter. Ögonlockens target följer
`eyelid_offset - eye_pitch`, med default `--eyelid-offset -2`.

Justera amplitud och tempo:

```bash
python3 /home/pi/robot_animation.py \
  --demo-gaze-corners \
  --gaze-corners-yaw 18 \
  --gaze-corners-pitch 8 \
  --gaze-corners-hold-ms 500 \
  --gaze-corners-return-ms 700
```

Inspektera trajektorian och keyframes:

```bash
python3 /home/pi/robot_animation.py \
  --demo-gaze-corners \
  --print-samples \
  --print-keyframes \
  --dry-run
```

`--print-samples` ger en CSV med `time_ms`, mål-yaw/pitch, simulerad
öga/nacke per axel, ögonlocksbytes och bytes som skickas till motorboardet.
Default sample-intervallet är `80 ms`, så defaultsekvensen ryms i ett enda
6-kanals script. Om kurvan blir för lång för ett enda script (max 36 frames
för 6 kanaler) kan `--gaze-corners-sample-ms` höjas eller hålltiderna kortas.

## Mäta nackens hastighet

Om en renderad gaze-animation ser ut som att ögonen sitter fast relativt
huvudet är den troliga orsaken att den simulerade nackkurvan går snabbare än
den fysiska nacken. Default för yaw-gaze använder nu uppmätt nackhastighet,
men nya mätningar kan fortfarande användas för att stämma modellen.

Mät med ett positionssteg på nackrotationen:

```bash
python3 /home/pi/robot_animation.py --test-neck-speed
```

Default är `-30° -> +30°`. Ett mildare test:

```bash
python3 /home/pi/robot_animation.py \
  --test-neck-speed \
  --neck-speed-from -20 \
  --neck-speed-to 20
```

Testet använder vanliga position-kommandon, inte motion-script. Det skriver
TX-rader och sammanfattar feedback:

```text
neck speed target: ...
peak observed speed: ...
10-90% time: ...
time to within 2 deg: ...
```

För feedbackrader som CSV:

```bash
python3 /home/pi/robot_animation.py \
  --test-neck-speed \
  --print-samples
```

## Mäta alla servons hastighet

För att mäta alla sju kanaler körs ett positionssteg på ett servo i taget.
Animationlagret tar emot vinklar i grader och konverterar till råa bytes först
när kommandot renderas mot `robot_motion.py`.

Default är `-8° -> +8°`, med neutral pose mellan varje kanal:

```bash
python3 /home/pi/robot_animation.py --test-servo-speeds
```

Testet använder vanliga position-kommandon, inte motion-script. Det samlar
feedbackpaket från motorboardet och skriver en sammanfattning:

```text
channel
byte start -> target
angle start -> target
peak B/s
peak deg/s
10-90% time
avg deg/s under 10-90%
time to within +/-3 feedback bytes
time to within +/-2 degrees
```

Välj kanaler eller byt vinkelsteg:

```bash
python3 /home/pi/robot_animation.py \
  --test-servo-speeds \
  --servo-speed-channels neck_rotation,eye_leftright \
  --servo-speed-from -12 \
  --servo-speed-to 12
```

Vinklar valideras mot respektive kanals uppmätta min/max. Om du testar alla
kanaler måste intervallet fungera för alla valda kanaler.

För sample-rader i terminalen:

```bash
python3 /home/pi/robot_animation.py \
  --test-servo-speeds \
  --print-samples
```

För en ren CSV-fil med feedbacksamples:

```bash
python3 /home/pi/robot_animation.py \
  --test-servo-speeds \
  --sample-log /home/pi/servo_speed_samples.csv
```

## Biblioteksanvändning

```python
from robot_animation import GazePoint, LOOK_CHANNELS, gaze_animation
from robot_motion import RobotMotion, STARTUP_COMMANDS

animation = gaze_animation(
    (
        GazePoint(-30, 0, 800),
        GazePoint(20, 0, 800),
        GazePoint(0, 0, 500),
    )
)
rendered = animation.render(LOOK_CHANNELS)

with RobotMotion("/dev/ttyAMA0") as robot:
    robot.run_commands([*STARTUP_COMMANDS, rendered.command()])
```

För att kombinera animationer:

```python
left = gaze_animation((GazePoint(-25, 0, 700),), name="look_left")
right = gaze_animation((GazePoint(25, 0, 700),), name="look_right")
combined = left.then(right, name="scan")
```

Yaw-only demo från bibliotek:

```python
from robot_animation import GazeYawConfig, demo_gaze_yaw_curve, render_gaze_yaw_curve
from robot_motion import RobotMotion, STARTUP_COMMANDS

rendered, samples = render_gaze_yaw_curve(
    demo_gaze_yaw_curve(),
    config=GazeYawConfig(sample_ms=50),
)

with RobotMotion("/dev/ttyAMA0") as robot:
    robot.run_commands([*STARTUP_COMMANDS, rendered.command()])
```
