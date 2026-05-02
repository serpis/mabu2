# `robot_motion.py`

`robot_motion.py` skickar kända UART-kommandon till motorbrädan på `57600 8N1`.
Filen fungerar både som CLI-verktyg och som importerbart Python-bibliotek.

För högre nivå-animationer finns `robot_animation.py`, som bygger semantiska
animationer och renderar dem till `robot_motion.py`-kommandon.

Default-porten är `/dev/ttyAMA0`, vilket är UART0 på Raspberry Pi 5 GPIO14/15
när `dtoverlay=uart0-pi5` är aktivt.

## Snabbstart CLI

Lista inbyggda animationer:

```bash
python3 /home/pi/robot_motion.py --list-animations
```

Kör default-animationen `Alternate_Winks`:

```bash
python3 /home/pi/robot_motion.py
```

Kör vald animation:

```bash
python3 /home/pi/robot_motion.py --animation 5
```

Visa TX/RX-bytes:

```bash
python3 /home/pi/robot_motion.py --animation 5 --verbose
```

Visa paket utan att öppna serialporten:

```bash
python3 /home/pi/robot_motion.py --animation 5 --dry-run
```

## Look-läge

`--look` tar vinklar i grader, inte råa servovärden:

```bash
python3 /home/pi/robot_motion.py --look 30,-5
python3 /home/pi/robot_motion.py --look -40,0 --eyelid-offset -2
```

Format:

```text
--look YAW[,PITCH]
```

Scriptet delar yaw mellan `neck_rotation` och `eye_leftright`, och pitch
mellan `neck_elevation` och `eye_updown`. Ögonlocken följer den inverterade
vertikala ögonriktningen:

```text
eyelid_angle = eyelid_offset - eye_updown
```

Default för `--eyelid-offset` är `0`.

## Rå position och pose

Sätt en enskild kanal med rått 8-bitars target:

```bash
python3 /home/pi/robot_motion.py --position 0x40,73
```

Skicka all-channel pose:

```bash
python3 /home/pi/robot_motion.py --pose 124,124,127,127,127,127,127
```

Pose-ordningen är:

```text
eyelid_left, eyelid_right, eye_leftright, eye_updown,
neck_elevation, neck_rotation, neck_tilt
```

## Rå keyframe-animation

`--raw-keyframes` bygger motion-script med opcode `0x01`.

```bash
python3 /home/pi/robot_motion.py \
  --raw-mask 0x66 \
  --raw-keyframes '78,171,45,127,50;31,217,245,127,50'
```

Format per frame:

```text
target_1,target_2,...,target_N,duration_ticks
```

`N` måste matcha antal satta bitar i `--raw-mask`. Duration verkar på den
testade motorbrädan vara ungefär `10 ms` per tick. Maskens kanalordning är
högsta bit först:

```text
0x40 eyelid_left
0x20 eyelid_right
0x10 eye_leftright
0x08 eye_updown
0x04 neck_elevation
0x02 neck_rotation
0x01 neck_tilt
```

Exempel: `0x66` betyder `0x40 + 0x20 + 0x04 + 0x02`, så varje frame har:

```text
eyelid_left,eyelid_right,neck_elevation,neck_rotation,duration_ticks
```

## Servicekommandon

```bash
python3 /home/pi/robot_motion.py --power-mask 0x7f
python3 /home/pi/robot_motion.py --power-mask 0
python3 /home/pi/robot_motion.py --read-vr
python3 /home/pi/robot_motion.py --read-calibration
python3 /home/pi/robot_motion.py --calibrate 0x40
python3 /home/pi/robot_motion.py --read-pid
python3 /home/pi/robot_motion.py --reset-pid
python3 /home/pi/robot_motion.py --read-version
```

Läskommandon skriver ut RX även utan `--verbose`.

PID-write kräver exakt `21` floats, alltså `7 x (P,I,D)`:

```bash
python3 /home/pi/robot_motion.py --write-pid '0.8,0.0000125,8,...'
```

## Overlap-test

För att testa vad motorboardet gör när ett nytt motion-script skickas innan
föregående script borde vara klart:

```bash
python3 /home/pi/robot_motion.py --test-overlap
```

Testet gör detta:

```text
1. power on
2. neutral pose
3. skicka ett långt eyelid-script på mask 0x60
4. vänta 0.5 s
5. skicka ett motsatt eyelid-script innan det första borde vara klart
```

Justera timing:

```bash
python3 /home/pi/robot_motion.py --test-overlap --overlap-delay 0.25
python3 /home/pi/robot_motion.py --test-overlap --overlap-delay 0.75 --overlap-listen 4
```

Testet skriver TX-tider och RX även utan `--verbose`, så det går att jämföra
feedback/rörelse mot när andra scriptet skickades.

Tolkning:

- om rörelsen byter riktning direkt när andra scriptet skickas: motorboardet avbryter pågående script
- om andra rörelsen kommer först efter första scriptet: motorboardet köar script
- om andra rörelsen aldrig syns: motorboardet ignorerar nytt script medan ett script kör

## Script-längdtest

För att testa hur många keyframes motorboardet accepterar i ett script:

```bash
python3 /home/pi/robot_motion.py --test-script-length 10
python3 /home/pi/robot_motion.py --test-script-length 20
python3 /home/pi/robot_motion.py --test-script-length 40
python3 /home/pi/robot_motion.py --test-script-length 80
```

Testet genererar ett 2-kanals eyelid-script på mask `0x60` med alternerande
targets och skriver TX/RX även utan `--verbose`.

Varje frame är:

```text
target_left,target_right,duration_ticks
```

Default är `10` ticks per frame, alltså ungefär `100 ms` på den testade
motorbrädan:

```bash
python3 /home/pi/robot_motion.py --test-script-length 40 --length-test-duration 10
```

Protokollmässigt begränsar payloadlängden en 2-kanals scriptpayload till:

```text
3 headerbytes i payload + 3 bytes per frame <= 255
max 84 frames
```

För 7-kanals raw scripts är motsvarande gräns:

```text
3 headerbytes i payload + 8 bytes per frame <= 255
max 31 frames
```

## Script-ticktest

För att mäta vilken ticklängd motorboardet faktiskt använder för raw
motion-scripts:

```bash
python3 /home/pi/robot_motion.py --test-script-tick
```

Testet kör flera raw scripts på `eye_leftright`. Varje trial sätter ögat i ett
startläge, skickar ett script som håller startläget i `N` ticks och går sedan
till ett targetläge. Feedbacken används för att hitta när rörelsen tydligt
börjar, och scriptet fit:ar:

```text
onset_time = fixed_latency + N * tick_seconds
```

Default mäter `40,80,120,160,200` ticks med två repetitioner. På den testade
uppsättningen gav det cirka `9.7 ms/tick`, vilket stödjer att lokalt
genererade scripts ska räkna med ungefär `10 ms` per tick.

För att välja egna hållängder:

```bash
python3 /home/pi/robot_motion.py \
  --test-script-tick \
  --script-tick-durations 40,80,160,240 \
  --script-tick-repeats 3
```

## Biblioteksanvändning

Import kör ingen argument-parsning och öppnar ingen serialport. Serialporten
öppnas först när `RobotMotion.open()`, context managern, eller en metod används.

```python
from robot_motion import RobotMotion

with RobotMotion("/dev/ttyAMA0") as robot:
    result = robot.look(30, -5, eyelid_offset=-2)
    print(result.tx.hex(" "))
    print(result.rx.hex(" "))
```

Alla huvudlägen finns som metoder:

```python
robot.animation(5)
robot.look(30, -5, eyelid_offset=-2)
robot.raw_keyframes(0x66, ((78, 171, 45, 127, 50), (31, 217, 245, 127, 50)))
robot.position(0x40, 73)
robot.pose((124, 124, 127, 127, 127, 127, 127))
robot.power_mask(0x7F)
robot.read_vr()
robot.read_calibration()
robot.read_pid()
robot.reset_pid()
robot.read_version()
robot.write_pid((0.0,) * 21)
robot.calibrate(0x40)
robot.overlap_test()
robot.script_length_test(40)
```

Resultatet är ett `RunResult`:

```python
result.tx               # alla skickade paket som bytes
result.rx               # all mottagen RX som bytes
result.command_results  # ett CommandResult per kommando
```

Vill du bygga paket utan att öppna serialporten:

```python
from robot_motion import look_pose_command, packet

command = look_pose_command(-40, 0, -2)
raw_packet = packet(command.payload)
print(raw_packet.hex(" "))
```

## Validering

Scriptet validerar bland annat:

- maskar får bara använda kända kanalbitar inom `0x7F`
- `--position` kräver exakt en kanalbit
- `--raw-keyframes` kräver att antal targets per frame matchar antal satta maskbitar
- bytevärden måste ligga i `0..255`
- `--write-pid` kräver exakt `21` floats

## Raspberry Pi 5 UART

För GPIO14/GPIO15 på Raspberry Pi 5 ska `/boot/firmware/config.txt` innehålla:

```text
dtoverlay=uart0-pi5
```

Seriell konsol bör inte ligga på samma UART. Kontrollera efter reboot:

```bash
ls -l /dev/ttyAMA0
pinctrl get 14,15
```

Förväntat är att GPIO14 är `TXD0` och GPIO15 är `RXD0`.
