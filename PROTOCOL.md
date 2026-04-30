# Robot UART Protocol Notes

Det här dokumentet sammanfattar vad som hittills är avkodat från UART-trafiken mellan huvudenhet och motor/styrenhet.

Fokus här är:

- sådant som är verifierat direkt från captures
- sådant som är starkt indicerat men fortfarande är tolkning
- sådant som fortfarande är oklart

## Kort sammanfattning

- framing: `FA 00`, `payload_length` i byte `2`, total längd `payload_length + 5`, checksumma `Fletcher-16` lagrad som `[sum2, sum1]`
- riktning: `RX` ser ut att vara huvudenhet -> motorbord, `TX` motorbord -> huvudenhet
- positionskommando: `01 <mask> 01 <value>`
- positionsfeedback: `01 00 <7 kanaler>`
- kanalordning: `0x40 eyelid_left`, `0x20 eyelid_right`, `0x10 eye_leftright`, `0x08 eye_updown`, `0x04 neck_elevation`, `0x02 neck_rotation`, `0x01 neck_tilt`
- power/enable: `4F <mask>`
- kalibreringsstart: `43 <mask>`
- kalibreringsresultat: `43 00` = success, `43 <failed_mask> <status>` = fail
- kända kalibreringsfel: `0x73 = range_too_small`, `0x62 = center_voltage_too_high`
- kalibreringsvärden: `42` läser tillbaka `7 x (min, max, range)` som little-endian `uint16` i en `0..4095`-liknande sensor/ADC-domän
- VR-läsning: `40` returnerar samma normaliserade 7x8-bitars feedback som löpande `position_feedback`
- UI-vinklarna är linjära app-side konverteringar från feedback/read-VR-byte för alla sju kanaler
- blockerad-rörelse-test stärker att feedback är faktisk uppmätt position, inte bara senaste setpoint
- versionsläsning: `56`
- PID: `47` read, `50` write, `52` reset; payloaden är `21` big-endian `float32` = `7 x (P, I, D)`
- rörelsescript: längre `0x01`-paket används för animationer och testcykler
- ingen observerad write-opcode för kalibreringsvärden; min/max ser ut att sättas av själva kalibreringen och ligger kvar efter omstart

Underlag som använts:

- [dumps/dump.json](dumps/dump.json)
- [dumps/animation_test_failed.json](dumps/animation_test_failed.json)
- [dumps/eyelid_left.json](dumps/eyelid_left.json)
- [dumps/eyelid_right.json](dumps/eyelid_right.json)
- [dumps/eye_leftright.json](dumps/eye_leftright.json)
- [dumps/eye_updown.json](dumps/eye_updown.json)
- [dumps/neck_elevation.json](dumps/neck_elevation.json)
- [dumps/neck_rotation.json](dumps/neck_rotation.json)
- [dumps/neck_tilt.json](dumps/neck_tilt.json)
- [dumps/pid.json](dumps/pid.json)
- [dumps/calibrate.json](dumps/calibrate.json)
- [dumps/power_on.json](dumps/power_on.json)
- [dumps/power_off.json](dumps/power_off.json)
- [dumps/start.json](dumps/start.json)
- [dumps/start_to_first_movement.json](dumps/start_to_first_movement.json)
- [dumps/version_id.json](dumps/version_id.json)
- [dumps/read_pid_values.json](dumps/read_pid_values.json)
- [dumps/read_vr_value.json](dumps/read_vr_value.json)
- [dumps/read_vr_value_annotated.json](dumps/read_vr_value_annotated.json)
- [dumps/read_vr_value_min.json](dumps/read_vr_value_min.json)
- [dumps/read_vr_value_max.json](dumps/read_vr_value_max.json)
- [dumps/anglemap_notes.md](dumps/anglemap_notes.md)
- [dumps/anglemap_ldl.json](dumps/anglemap_ldl.json)
- [dumps/anglemap_ldr.json](dumps/anglemap_ldr.json)
- [dumps/anglemap_ldr2.json](dumps/anglemap_ldr2.json)
- [dumps/anglemap_elr.json](dumps/anglemap_elr.json)
- [dumps/anglemap_eud.json](dumps/anglemap_eud.json)
- [dumps/anglemap_ne.json](dumps/anglemap_ne.json)
- [dumps/anglemap_nr.json](dumps/anglemap_nr.json)
- [dumps/anglemap_nt.json](dumps/anglemap_nt.json)
- [dumps/reset_pid_values.json](dumps/reset_pid_values.json)
- [dumps/calibration_values.json](dumps/calibration_values.json)
- [dumps/calibration_values_annotated.json](dumps/calibration_values_annotated.json)
- [dumps/anglemap_calibration.json](dumps/anglemap_calibration.json)
- [dumps/calibration_sequence.json](dumps/calibration_sequence.json)
- [dumps/calibration_persistence.json](dumps/calibration_persistence.json)
- [dumps/cal_ldl.json](dumps/cal_ldl.json)
- [dumps/cal_ldr.json](dumps/cal_ldr.json)
- [dumps/cal_ldr_fail.json](dumps/cal_ldr_fail.json)
- [dumps/cal_elr.json](dumps/cal_elr.json)
- [dumps/cal_eud.json](dumps/cal_eud.json)
- [dumps/cal_ne.json](dumps/cal_ne.json)
- [dumps/cal_nr.json](dumps/cal_nr.json)
- [dumps/cal_nt.json](dumps/cal_nt.json)
- [dumps/single_cycle_test.json](dumps/single_cycle_test.json)
- [dumps/double_cycle_test.json](dumps/double_cycle_test.json)
- [dumps/five_cycle_test.json](dumps/five_cycle_test.json)
- [dumps/eyes_eyelid_test_fail.json](dumps/eyes_eyelid_test_fail.json)
- [dumps/neck_rotation_test.json](dumps/neck_rotation_test.json)
- [dumps/neck_rotation_test_fail.json](dumps/neck_rotation_test_fail.json)
- [dumps/neck_tilt_test.json](dumps/neck_tilt_test.json)
- [dumps/slow_blink.json](dumps/slow_blink.json)
- [dumps/position_hold_or_obstructed_soft.json](dumps/position_hold_or_obstructed_soft.json)
- [dumps/position_hold_or_obstructed_soft2.json](dumps/position_hold_or_obstructed_soft2.json)

Praktisk decoder:

- [decode_dump.py](decode_dump.py)
- [ANIMATION_TEST_FAILED.md](ANIMATION_TEST_FAILED.md)

## Verifierad framing

Alla hittills observerade paket använder samma framing:

- sync/header börjar med `FA 00`
- byte `2` är payloadlängd
- total paketlängd är `payload_length + 5`
- sista två byten är checksumma

Checksumman är verifierad som `Fletcher-16` över hela paketet utom de sista två bytesen, lagrad som:

- `sum2`
- `sum1`

Exempel:

```text
FA 00 04 01 40 01 34 EB 75
```

- sync = `FA 00`
- payload length = `04`
- payload = `01 40 01 34`
- checksum = `EB 75`

## Observerade pakettyper

## 1. Positionskommando

Format:

```text
FA 00 04 01 <mask> 01 <value> <cksum_hi> <cksum_lo>
```

Verifierat:

- payloadlängd är alltid `4`
- payload byte `0` är alltid `0x01`
- payload byte `1` fungerar som kanal/mask
- payload byte `2` är hittills alltid `0x01`
- payload byte `3` varierar och följer det kommenderade läget

Tolkning:

- detta är ett kommando från huvudenheten till motorbordet för att sätta målposition
- `mask` väljer servo/axel
- `value` är önskat positionsvärde

Det som stöder tolkningen:

- i varje enkel-axel-capture är `mask` konstant för just den axeln
- när ett visst servo rörs varierar motsvarande TX-fält efteråt mot samma nivå

## 2. Positionsfeedback

Format:

```text
FA 00 09 01 00 <ch0> <ch1> <ch2> <ch3> <ch4> <ch5> <ch6> <cksum_hi> <cksum_lo>
```

Verifierat:

- payloadlängd är alltid `9`
- payload byte `0` är alltid `0x01`
- payload byte `1` är alltid `0x00`
- därefter följer `7` variabla bytes

Stark tolkning:

- de sju sista payload-bytena är återrapporterade positionsvärden för sju kanaler
- motorbordet skickar denna feedback snabbare än nya målkommandon kommer in
- när ett servo rör sig glider motsvarande feedbackvärde successivt mot RX-värdet

Exempel från vänster ögonlock:

```text
RX: FA 00 04 01 40 01 34 EB 75
TX: FA 00 09 01 00 3E E6 B3 9E 94 77 79 67 02
```

För `mask = 0x40` korrelerar RX-värdet starkast mot TX `payload[2]`.

I `dumps/dump.json`:

- RX packet rate: cirka `47.97 Hz`
- TX packet rate: cirka `192.49 Hz`

Det är alltså ungefär `4.01x` fler feedbackpaket än målkommandon, vilket stämmer bra med att TX är återrapportering under pågående rörelse.

### Blockerad rörelse

[dumps/position_hold_or_obstructed_soft.json](dumps/position_hold_or_obstructed_soft.json)
och [dumps/position_hold_or_obstructed_soft2.json](dumps/position_hold_or_obstructed_soft2.json)
är viktiga eftersom vänster ögonlock hölls fast medan appen skickade nya
setpoints.

I första capturet hölls ögonlocket först fast och släpptes sedan:

| Kommando | Feedback under blockering | Feedback efter släpp | Tolkning |
| --- | ---: | ---: | --- |
| `01 40 01 F5` (`target=245`) | stannar runt `68` i ungefär `2 s` | går till `244` | feedback följer fysisk position |
| `01 40 01 50` (`target=80`) | stannar runt `206` i ungefär `2 s` | går till `80` | feedback följer fysisk position |

I andra capturet hölls ögonlocket tills motorn gav upp:

```text
2.373500 RX 01 40 01 F1   target=241
2.440561 RX 01 40 01 F1   target=241
2.376565..4.795415 TX feedback ch0=91..145, last=142
4.706731 TX 02 6E 40
```

Feedbacken når alltså aldrig setpoint `241`, utan ligger kvar runt
`139..145` medan mekaniken är blockerad. Det är stark evidens för att
feedbacken är uppmätt position, eller åtminstone en signal som påverkas
av faktisk mekanisk position, inte bara senaste accepterade målposition.

## 3. Aktiv mask-echo

Format:

```text
FA 00 03 02 6E <mask> <cksum_hi> <cksum_lo>
```

Verifierat:

- förekommer i några captures men inte alla
- payload byte `0` är `0x02`
- payload byte `1` är `0x6E`
- payload byte `2` matchar den mask som rör sig i capturet

Tolkning:

- troligen ett kort status- eller bekräftelsepaket som anger vilken kanal som är aktiv eller senast adresserad
- i [dumps/position_hold_or_obstructed_soft2.json](dumps/position_hold_or_obstructed_soft2.json) kommer `02 6E 40` efter att `eyelid_left` varit blockerad i drygt `2 s` och aldrig nått target

Det som fortfarande är oklart:

- exakt när detta paket skickas
- om det är en ack, statusindikering, kanalval eller en timeout/fault-indikering efter blockerad rörelse

## 4. PID write

Format:

```text
FA 00 55 50 <84 byte data> <cksum_hi> <cksum_lo>
```

Verifierat:

- payloadlängd `0x55 = 85`
- första payloadbyten är `0x50`
- återstående `84` bytes = `21` stycken `float32`
- floatsen är big-endian

Tolkning:

- RX med opcode `0x50` är ett skrivkommando för PID-parametrar
- TX med opcode `0x50` är ett echo/bekräftelsepaket från motorbordet

## 5. PID read request

Format:

```text
FA 00 01 47 35 43
```

Verifierat:

- payloadlängd `1`
- enda payloadbyte är `0x47`

Tolkning:

- begär att motorbordet ska rapportera aktuella PID-parametrar

## 6. PID report

Format:

```text
FA 00 55 47 <84 byte data> <cksum_hi> <cksum_lo>
```

Verifierat:

- payloadlängd `85`
- första payloadbyten är `0x47`
- resterande `84` bytes dekodar till samma `21` stycken big-endian `float32`

Tolkning:

- detta är motorbordets svar på `get_pid_request`

## 7. Power mask set

Format:

```text
FA 00 02 4F <mask> <cksum_hi> <cksum_lo>
```

Observerat i:

- `power_on.json`: `FA 00 02 4F 7F ...`
- `power_off.json`: `FA 00 02 4F 00 ...`
- `calibrate.json`: `FA 00 02 4F 7F ...`

Verifierat:

- payloadlängd `2`
- opcode `0x4F`
- argumentet beter sig som en mask
- motorbordet ekar tillbaka exakt samma paket direkt

Tolkning:

- detta sätter sannolikt aktiv eller påslagen kanalmask
- `0x7F` betyder sannolikt att alla kanaler aktiveras
- `0x00` betyder sannolikt att alla kanaler stängs av

Det som är oklart:

- exakt semantik för opcode `0x4F`
- om detta påverkar matning, drivsteg-enable eller någon intern aktiv mask

## Observerad startsekvens

`start.json` innehåller bara en byte `00` på RX och inga TX-bytes. Den
går inte att packetisera och råcapturet har bara några få flanker, så den
ser ut som power-up/line-glitch eller idle-nivå snarare än giltigt
protokoll.

`start_to_first_movement.json` innehåller däremot en ren sekvens utan
garbage-bytes:

```text
18.375358s RX  FA 00 02 4F 7F 0B CB
18.375984s TX  FA 00 02 4F 7F 0B CB
18.376092s RX  FA 00 01 47 35 43
18.376649s TX  FA 00 55 47 ... CE 9B
19.722028s RX  FA 00 04 01 40 01 49 01 8A
19.782221s RX  FA 00 04 01 40 01 49 01 8A
```

Tolkning:

- appen eller huvudenheten aktiverar först alla kanaler med `4F 7F`
- motorbordet ekar `4F 7F` direkt
- appen läser PID med `47`, och motorbordet svarar med `21` floatar
- cirka `1.35 s` senare skickas första positionskommandot
- första rörelsen är `01 40 01 49`, alltså `eyelid_left` till värde `73`
- exakt samma positionskommando skickas två gånger med cirka `60 ms` mellanrum
- efter första positionskommandot skickar motorbordet `32` feedbackpaket där `eyelid_left` går från `0` till `72`

Det här ser inte ut som en separat dold boot-handshake. Det är samma
pakettyper som redan observerats: enable, PID-läsning, positionskommando
och positionsfeedback. I den här capturet syns ingen versionsläsning,
ingen `read_calibration_values` och ingen kalibrering vid start.

## 8. Calibration request

Format:

```text
FA 00 02 43 <mask> <cksum_hi> <cksum_lo>
```

Observerat i `calibrate.json`:

```text
RX: FA 00 02 43 7F F2 BF
```

Verifierat:

- payloadlängd `2`
- opcode `0x43`
- argumentet är `0x7F`

Stark tolkning:

- detta startar själva kalibreringen
- `0x7F` betyder "alla kanaler"

Det som stöder det:

- `0x7F` är exakt OR-summan av alla kända kanalbitar: `0x40|0x20|0x10|0x08|0x04|0x02|0x01`
- efter detta paket följer cirka `19.6` sekunders tystnad innan ett resultat kommer tillbaka

## 9. Calibration result

Observerade format:

```text
FA 00 03 43 <mask> <status> <cksum_hi> <cksum_lo>
FA 00 02 43 00 <cksum_hi> <cksum_lo>
```

Observerat i `calibrate.json`:

```text
TX: FA 00 03 43 20 73 6B D4
```

Observerat i `neck_rotation_test.json` och `neck_tilt_test.json`:

```text
TX: FA 00 02 43 00 73 40
```

Verifierat:

- opcode `0x43`
- det finns minst två svarsformat
- `43 <mask> <status>` används vid fel
- `43 00` används i subset-tester som lyckas

Stark tolkning:

- `0x20` är felande kanal, alltså `eyelid_right`
- `0x73` är en status- eller felkod
- den här koden motsvarar sannolikt appens feltext `EYELID_RIGHT Motor range is smaller than expected`
- `0x62` motsvarar i `neck_rotation_test_fail.json` appfelet `NECK_ROTATION Motor center voltage is too high`
- `43 00` betyder sannolikt att kalibreringen avslutades utan fel för den begärda masken

Det som stöder tolkningen:

- `0x20` är redan etablerad mask för `eyelid_right`
- resultatpaketet kommer först när kalibreringen är klar
- inga andra nya datapaket syns under tiden
- i både `neck_rotation_test.json` och `neck_tilt_test.json` kommer `43 00` precis innan appen läser ut kalibreringsvärden

## Kalibreringssekvens

Hela den observerade sekvensen i `calibrate.json` är:

```text
0.149897s RX  FA 00 02 4F 7F 0B CB
0.150523s TX  FA 00 02 4F 7F 0B CB
0.188117s RX  FA 00 02 43 7F F2 BF
19.796340s TX FA 00 03 43 20 73 6B D4
```

Tolkning:

- huvudenheten aktiverar först alla kanaler via `0x4F 0x7F`
- motorbordet bekräftar direkt
- huvudenheten startar kalibrering för alla kanaler
- motorbordet arbetar sedan autonomt i cirka `19.6` sekunder
- motorbordet returnerar ett resultat som pekar ut `eyelid_right`

## Hur motor range sannolikt detekteras

Det går inte att se själva range-beräkningen på UART-nivå i `calibrate.json`.

Det som är verifierat:

- ingen löpande positionsfeedback skickas under kalibreringen
- inga vanliga `position_feedback`-paket förekommer i capturet
- enda synliga resultatet är slutpaketet `43 20 73`

Den starkaste tolkningen är därför:

- motorbordets firmware kör kalibreringen lokalt
- den använder intern positionsåterkoppling eller motsvarande lokalt uppmätt lägesvärde
- den beräknar ett uppnått rörelseomfång, rimligen `max_position - min_position`
- den jämför detta omfång mot en intern minimitröskel
- om omfånget är för litet returneras fel för den kanal som misslyckades

Det som talar för detta:

- vi har redan en separat 7-kanals positionsfeedback i normal drift
- samma typer av lägesvärden kan mycket väl användas internt under kalibrering
- eftersom inga mellanresultat skickas över UART sker bedömningen sannolikt helt inne i motorbordet

Det som fortfarande är oklart:

- exakt vilken sensor eller återkopplingskälla som används
- om range mäts som absolut encoder-/potvärde, regulatorns interna positionsestimat eller något mekaniskt stoppkriterium
- vilken tröskel som används för att avgöra att range är "smaller than expected"
- om `0x62` är en generisk kod för "center too high" eller specifik för just denna nackkanal

## Kalibrering av delmängd

De nya `_test`-dumparna visar att opcode `0x43` inte bara används för "kalibrera allt", utan också för en vald delmängd av kanaler.

Observerat:

- `eyes_eyelid_test_fail.json`: `43 78`
- `neck_rotation_test.json`: `43 02`
- `neck_tilt_test.json`: `43 05`
- `cal_ldl.json`: `43 40`
- `cal_ldr.json`: `43 20`
- `cal_elr.json`: `43 10`
- `cal_eud.json`: `43 08`
- `cal_ne.json`: `43 04`
- `cal_nr.json`: `43 02`
- `cal_nt.json`: `43 01`

Masktolkning:

- `0x78 = 0x40|0x20|0x10|0x08` = båda ögonlocken + båda ögonaxlarna
- `0x02` = `neck_rotation`
- `0x05 = 0x04|0x01` = `neck_elevation` + `neck_tilt`

De separata `cal_*.json`-dumparna visar dessutom att fabriksappen kan
starta kalibrering av exakt en kanal åt gången utan något synligt `0x4F`
i samma capture:

| Dump | RX | TX-resultat | Tolkning | Tid till svar |
| --- | --- | --- | --- | --- |
| [cal_ldl.json](dumps/cal_ldl.json) | `43 40` | `43 00` | `eyelid_left` OK | `1.84 s` |
| [cal_ldr.json](dumps/cal_ldr.json) | `43 20` | `43 00` | `eyelid_right` OK | `2.89 s` |
| [cal_ldr_fail.json](dumps/cal_ldr_fail.json) | `43 20` | `43 20 73` | `eyelid_right`, range för liten | `3.95 s` |
| [cal_elr.json](dumps/cal_elr.json) | `43 10` | `43 00` | `eyes_left_right` OK | `2.61 s` |
| [cal_eud.json](dumps/cal_eud.json) | `43 08` | `43 00` | `eyes_up_down` OK | `1.62 s` |
| [cal_ne.json](dumps/cal_ne.json) | `43 04` | `43 00` | `neck_elevation` OK | `2.84 s` |
| [cal_nr.json](dumps/cal_nr.json) | `43 02` | `43 02 62` | `neck_rotation`, center voltage för hög | `2.70 s` |
| [cal_nt.json](dumps/cal_nt.json) | `43 01` | `43 00` | `neck_tilt` OK | `1.91 s` |

Sekvensen ser ut så här:

1. appen skickar `43 <mask>` för den grupp som ska testas
2. motorbordet kalibrerar lokalt
3. motorbordet svarar med antingen `43 00` eller `43 <failed_mask> <status>`
4. appen skickar sedan `42` för att läsa tillbaka kalibreringsvärden

Det ger två starka slutsatser:

- range-bedömningen görs i motorbordet, inte i appen
- `read_calibration_values` används efter kalibrering för att hämta resultatparametrar, inte för att appen själv ska räkna ut range i efterhand

## 10. Read VR values

Format:

```text
RX: FA 00 01 40 ...
TX: FA 00 09 01 00 <7 byte> ...
```

Observerat i [dumps/read_vr_value.json](dumps/read_vr_value.json).

Verifierat:

- ett enbytekommando med opcode `0x40`
- svaret är exakt samma packet-typ som den normala 7-kanals feedbacken

Stark tolkning:

- den vanliga `position_feedback`-packettypen representerar VR- eller positionsåterkoppling, eller ett mycket närliggande derivat
- servicekommandot `0x40` triggar en enskild snapshot av samma data

`read_vr_value_min.json` och `read_vr_value_max.json` är tagna när alla
signaler i fabriksappen ställts på min respektive max. De ska därför
tolkas som appens kommenderade ändlägen, inte som råa `0x42`
kalibreringsvärden.

| Kanal | App-min byte | Annoterad byte | App-max byte | Annoterad app-position | Konsollogg |
| --- | ---: | ---: | ---: | ---: | ---: |
| `eyelid_left` | `1` | `126` | `253` | `49.6%` | `-30.52` |
| `eyelid_right` | `28` | `255` | `255` | `100.0%` | `-72.00` |
| `eyes_left_right` | `10` | `128` | `236` | `52.2%` | `-0.06` |
| `eyes_up_down` | `2` | `100` | `191` | `51.9%` | `-3.24` |
| `neck_elevation` | `2` | `129` | `236` | `54.3%` | `-0.16` |
| `neck_rotation` | `0` | `119` | `254` | `46.9%` | `-3.00` |
| `neck_tilt` | `12` | `126` | `245` | `48.9%` | `0.16` |

Den här mappningen bekräftar kanalordningen i `position_feedback`.
Konsolloggarnas gradvärden syns däremot inte som egna UART-fält; de ser
ut att räknas i appen från dessa normaliserade 8-bitarsvärden och en
separat DOF-modell. De centrala neck/eye-värdena ligger nära mitten av
app-min/max-intervallet. `eyelid_right` låg i den annoterade dumpen exakt
på app-max och gav `-72.00`, medan `eyelid_left` nära mitten gav
`-30.52`, vilket tyder på att ögonlockens gradskala inte är en enkel
symmetrisk center-skala.

`anglemap_*.json` och [dumps/anglemap_notes.md](dumps/anglemap_notes.md)
ger en starkare mappning mellan UI-vinkel och feedback/read-VR-byte. Varje
capture innehåller flera manuellt satta UI-lägen. Extra smådragningar i
UI:t har grupperats genom att ta sista stabila `position_feedback`-platån
före nästa rörelsekluster.

Formeln nedan är `ui_angle_degrees ~= a * feedback_byte + b`. Eftersom
`read_vr_value` returnerar samma sjubytesformat som löpande
`position_feedback` bör samma formler gälla för `0x40`-snapshoten.

| Kanal | Formel från stabil feedbackbyte | RMSE | Kommentar |
| --- | --- | ---: | --- |
| `eyelid_left` | `angle ~= -0.3210 * byte + 9.996` | `0.12 deg` | verifierad över fem platåer |
| `eyelid_right` | `angle ~= -0.3216 * byte + 10.001` | `0.00 deg` | verifierad i `anglemap_ldr2.json` |
| `eyes_left_right` | `angle ~= -0.1178 * byte + 15.023` | `0.07 deg` | ungefär `+15..-15 deg` |
| `eyes_up_down` | `angle ~= 0.11765 * byte - 15.002` | `0.00 deg` | ungefär `-15..+15 deg` |
| `neck_elevation` | `angle ~= -0.10978 * byte + 13.996` | `0.00 deg` | ungefär `+14..-14 deg` |
| `neck_rotation` | `angle ~= 0.35185 * byte - 44.932` | `0.10 deg` | ungefär `-45..+45 deg` |
| `neck_tilt` | `angle ~= -0.10977 * byte + 13.997` | `0.00 deg` | ungefär `+14..-14 deg` |

Det här gör UI-mappningen betydligt mindre oklar för alla sju kanaler:
UI-vinkeln är i praktiken en linjär app-side konvertering från den
normaliserade 8-bitars feedbacken. `eyelid_left` och `eyelid_right`
använder i praktiken samma skala: cirka `+10 deg` vid byte `0` och cirka
`-72 deg` vid byte `255`.

Ögonlockens UI-range är alltså cirka `82 deg`:

- `eyelid_left`: ungefär `+10.0 deg` till `-71.9 deg`
- `eyelid_right`: ungefär `+10.0 deg` till `-72.0 deg`

## 11. Read calibration values

Format:

```text
RX: FA 00 01 42 ...
TX: FA 00 2B 42 <42 data-byte> ...
```

Observerat i [dumps/calibration_values.json](dumps/calibration_values.json).

Verifierat:

- RX är ett enbytekommando med opcode `0x42`
- TX innehåller `43` bytes payload: opcode `0x42` plus `42` databytes
- `42` databytes är exakt `21` stycken 16-bitarsvärden

Stark tolkning:

- detta är `7` stycken tripplar i little-endian
- tripplarna är `min`, `max`, `range`
- tredje värdet är i de observerade testerna exakt `max - min`

Det här stöds nu direkt av fabriksappens loggtexter:

- `neck_rotation_test.json` visar `NECK_ROTATION min: 33 max: 3831`
- samma dump innehåller `neck_rotation=[33,3831,3798]`, alltså `3798 = 3831 - 33`
- `neck_tilt_test.json` visar:
  - `NECK_ELEVATION min: 2692 max: 3344`
  - `NECK_TILT min: 2692 max: 3357`
- samma dump innehåller:
  - `neck_elevation=[2692,3344,652]`
  - `neck_tilt=[2692,3357,665]`
- `eyes_eyelid_test_fail.json` visar `EYELID_LEFT min: 1326 max: 3243`
- samma dump innehåller `eyelid_left=[1326,3243,1917]`

Little-endian tripplar:

```text
[1325,3243,1918]
[2594,2964,370]
[1902,2275,373]
[886,1232,346]
[2687,3349,662]
[35,3831,3796]
[2691,3355,664]
```

I [dumps/calibration_values_annotated.json](dumps/calibration_values_annotated.json)
är samma format:

| Kanal | Min | Max | Range |
| --- | ---: | ---: | ---: |
| `eyelid_left` | `1325` | `3246` | `1921` |
| `eyelid_right` | `2558` | `2970` | `412` |
| `eyes_left_right` | `1901` | `2275` | `374` |
| `eyes_up_down` | `887` | `1231` | `344` |
| `neck_elevation` | `2671` | `3327` | `656` |
| `neck_rotation` | `31` | `3831` | `3800` |
| `neck_tilt` | `2693` | `3357` | `664` |

[dumps/calibration_sequence.json](dumps/calibration_sequence.json) visar
vad som händer när appen läser `0x42`, kalibrerar en kanal med
`43 <mask>`, och sedan läser `0x42` igen. Bara den kalibrerade kanalens
triplet ändras efter respektive lyckad kalibrering:

| Steg | Resultat | Ändrad kanal | Nytt triplet |
| --- | --- | --- | --- |
| initial read | - | - | se [dumps/anglemap_calibration.json](dumps/anglemap_calibration.json) |
| `43 40` | `43 00` | `eyelid_left` | `[1326, 3237, 1911]` |
| `43 20` | `43 00` | `eyelid_right` | `[1241, 3187, 1946]` |
| `43 10` | `43 00` | `eyes_left_right` | `[1959, 2209, 250]` |
| `43 08` | `43 00` | `eyes_up_down` | `[886, 1223, 337]` |
| `43 04` | `43 00` | `neck_elevation` | `[2701, 3339, 638]` |
| `43 02` | `43 02 62` | `neck_rotation` | `[25, 3831, 3806]` |
| `43 01` | `43 00` | `neck_tilt` | `[2693, 3360, 667]` |

[dumps/calibration_persistence.json](dumps/calibration_persistence.json) visar
att värdena ligger kvar efter omstart i det inspelade flödet:

| Tid | Händelse | `eyelid_left` triplet |
| ---: | --- | --- |
| `0.406579` | initial `42` | `[1326, 3237, 1911]` |
| `2.756969` | `43 40` startar kalibrering | - |
| `4.708824` | `43 00` success | - |
| `5.493029` | `42` efter kalibrering | `[1325, 3243, 1918]` |
| `29.134898` | `42` efter omstart/ny initsekvens | `[1325, 3243, 1918]` |

Det här höjer konfidensen för att `0x42` returnerar motorbordets lagrade
per-kanals kalibreringsresultat, inte en app-side beräkning. Värdena
ligger i `0..4095`-domänen och ser därför ut som rå eller nästan rå
12-bitars ADC/VR-spänning. De är inte samma skala som `0x40`/feedbackens
normaliserade `0..255`-värden.

Notering om fel:

- `neck_rotation` returnerade `43 02 62`, alltså center-voltage-fel
- efter felet ändrades ändå `neck_rotation`-triplet från `[27,3831,3804]` till `[25,3831,3806]`
- ett misslyckat kalibreringssteg kan alltså fortfarande skriva eller åtminstone uppdatera delvärden

Tolkning:

- `triplet[0] = min`
- `triplet[1] = max`
- `triplet[2] = measured_range`
- värdena är lagrade per kanal och uppdateras av `43 <mask>`

Det som fortfarande är oklart:

- i vilken fysisk enhet min/max mäts
- exakt hur motorbordet mappar dessa `0..4095`-värden till normaliserad `0..255` feedback

Notering:

- samma capture innehåller fler TX-bytes efter det första giltiga packetet
- de extra bytena gick inte att packetisera med samma framing och ser ut som en avklippt eller sammanblandad servicepayload

## Mönster för read/write-paket

De observerade serviceopcodes följer ett ganska konsekvent mönster:

| Funktion | RX | TX | Mönster |
| --- | --- | --- | --- |
| read VR values | `40` | `01 00 <7 byte>` | läsning som återanvänder normal feedbacktyp |
| read calibration values | `42` | `42 <42 byte>` | read request, samma opcode i svar |
| start calibration | `43 <mask>` | `43 00` eller `43 <mask> <status>` | action request med resultatkod |
| read PID | `47` | `47 <84 byte>` | read request, samma opcode i svar |
| set power mask | `4F <mask>` | `4F <mask>` | skrivning/set med exakt echo |
| write PID | `50 <84 byte>` | `50 <84 byte>` | skrivning med exakt echo |
| reset PID | `52` | `52 <84 byte>` | action request som returnerar ny tabell |
| read version | `56` | `56 <4 byte>` | read request, samma opcode i svar |

Det ger två praktiska regler:

- rena läsningar är oftast ett enbytekommando där svaret börjar med samma opcode
- skrivningar eller set-kommandon har argument/data i RX och motorbordet ekar antingen samma payload eller skickar ett kort resultat

`0x40` är undantaget: den är en läsning, men svaret är inte `40 ...` utan
samma `position_feedback`-format som normal drift.

### Möjligt `set calibration values`

Ingen dump hittills innehåller ett observerat skrivkommando för
kalibreringsvärden.

Det som talar emot att det används i fabriksappens flöden:

- alla observerade RX-opcodes är `01`, `40`, `42`, `43`, `47`, `4F`, `50`, `52`, `56`
- inga okända RX-serviceopcodes syns i dumpmängden
- inga RX-paket med payloadlängd `43` syns; en full write av
  `7 x (min,max,range)` borde sannolikt vara `opcode + 42 data-byte`
- inga okända per-kanals write-paket med `mask + 3 x uint16` syns heller
- `calibration_sequence.json` visar att `43 <mask>` följt av lokal
  kalibrering uppdaterar den berörda kanalens `0x42`-triplet

Om ett sådant kommando ändå finns är den mest sannolika formen, baserat
på PID-write, något i stil med:

```text
RX: FA 00 2B <write_cal_opcode> <42 calibration data bytes> <cksum>
TX: FA 00 2B <write_cal_opcode> <42 calibration data bytes> <cksum>
```

En annan möjlig form är ett per-kanalskommando:

```text
RX: <opcode> <mask> <min:u16le> <max:u16le> <range:u16le>
```

Men inget av dessa format har observerats. Den starkaste aktuella
tolkningen är därför att min/max inte skrivs direkt av appen i de här
flödena, utan att motorbordet mäter och lagrar dem själv när `43 <mask>`
körs.

## 12. Reset PID values

Format:

```text
RX: FA 00 01 52 ...
TX: FA 00 55 52 <84 byte data> ...
```

Observerat i [dumps/reset_pid_values.json](dumps/reset_pid_values.json).

Verifierat:

- RX opcode `0x52`
- TX svarar med samma `21` float32-värden som i PID-tabellen, men med opcode `0x52`

Tolkning:

- detta återställer sannolikt PID-värden till default och returnerar sedan default-tabellen

## 13. Read version

Format:

```text
RX: FA 00 01 56 ...
TX: FA 00 05 56 <4 byte> ...
```

Observerat i [dumps/version_id.json](dumps/version_id.json).

Verifierat:

- ett enbytekommando med opcode `0x56`
- svaret innehåller `4` bytes efter opcode
- de fyra bytena dekodar snyggt som big-endian float `2.39`

Tolkning:

- detta är sannolikt ett versions-id eller firmwareversionsnummer

## 14. Motion scripts och animationer

Fabriksappen verkar inte skicka ett separat animation-id. Den skickar i
stället råa keyframe-script med opcode `0x01`. Samma opcode används för
enkla positionskommandon, men scriptvarianten har payload längre än fyra
bytes.

Observerat i:

- [dumps/animation_test_failed.json](dumps/animation_test_failed.json)
- [dumps/single_cycle_test.json](dumps/single_cycle_test.json)
- [dumps/double_cycle_test.json](dumps/double_cycle_test.json)
- [dumps/five_cycle_test.json](dumps/five_cycle_test.json)
- [dumps/slow_blink.json](dumps/slow_blink.json)

Verifierade scriptformat:

```text
1-kanal:
01 <mask> <byte_count> (<target> <arg>)*

2-kanal:
01 <mask> (0x80 | point_count) (<target_a> <target_b> <duration_ticks>)*

pose-preset för alla kanaler:
01 7F 01 <7 target bytes>
```

Tolkning:

- `mask` väljer kanal eller kanaler med samma bitmask som övriga kommandon
- kanalordningen i ett 2-kanals-script följer maskordningen: `0x40`, `0x20`, `0x10`, `0x08`, `0x04`, `0x02`, `0x01`
- `target` är samma 8-bitars positionsskala som positionskommandon och feedback använder
- tredje värdet i 2-kanals-tupeln är mycket starkt indicerat som duration i tickar om cirka `5 ms`
- motorbordet kör scriptet lokalt efter att hela paketet skickats
- under körning kommer bara vanliga `position_feedback`-paket tillbaka
- appens `Points within/out of tolerance` verkar räknas app-side från feedbackströmmen, inte komma från motorbordet som egna resultatpaket

Exempel: för `mask 0x60 = eyelid_left + eyelid_right` betyder tupeln
`(31,217,100)` sannolikt `eyelid_left` target `31`,
`eyelid_right` target `217`, och duration cirka `100 * 5 ms = 500 ms`.

Det här stöds av både RX-tider och feedback i
[dumps/animation_test_failed.json](dumps/animation_test_failed.json):

| Animation | Summa duration | Tid till nästa script | Implikation | Feedbackpaket i fönstret |
| --- | ---: | ---: | ---: | ---: |
| `Neck_Roll` | `200` | `989 ms` | `4.95 ms/tick` | `200` |
| `Half_Close` | `80` | `386 ms` | `4.83 ms/tick` | `78` |
| `Eye_Roll_Slow_CCW` | `200` | `981 ms` | `4.90 ms/tick` | `199` |
| `Neck_Elevation_Stretch` | `235` | `1170 ms` | `4.98 ms/tick` | `238` |
| `Alternate_Winks` | `340` | `1706 ms` | `5.02 ms/tick` | `346` |
| `Eye_Roll_Fast_CW` | `100` | `484 ms` | `4.84 ms/tick` | `98` |

Feedbackströmmen i samma capture ligger på median `4.927 ms` per paket,
alltså cirka `203 Hz`. Det gör att duration-tickarna i praktiken ligger
på samma klocka som feedbackperioden.

För `Alternate_Winks` syns keyframe-gränserna också i feedbacken:
vänster ögonlock har vändpunkter vid cirka `100`, `200`, `260` och `290`
duration-tickar. Dessa ger ungefär `4.96-5.02 ms/tick` när de jämförs
mot `position_feedback`, med några tiotals millisekunders mekanisk/regleringslagg.

### Cycle-test scripts

Cycle-testerna använder 1-kanalsformatet på `eye_updown` (`mask 0x08`):

```text
single_cycle_test: 01 08 04 E5 7F 19 7F
double_cycle_test: 01 08 08 E5 7F 19 7F E5 7F 19 7F
five_cycle_test:   01 08 14 E5 7F 19 7F ... upprepat 5 gånger
```

Tolkning:

- varje par är säkert `<target, arg>`
- `E5 7F` betyder target `229` med argument `127`
- `19 7F` betyder target `25` med argument `127`
- det är ännu inte bevisat att `arg` i 1-kanalsformatet är samma duration som tredje värdet i 2-kanalsformatet
- i de separata cycle-captures stämmer inte `127` lika rent mot `5 ms` som 2-kanals-animationerna gör, så `arg` kan vara hastighet, ramp-parameter eller duration i ett annat delformat

Korrelation mot feedback för cycle-testerna:

| Capture | Scriptpar | Feedback-vändpunkter efter RX | Tolkning |
| --- | ---: | --- | --- |
| `single_cycle_test` | `2` | cirka `35 ms`, `129 ms` | en upp- och nedrörelse |
| `double_cycle_test` | `4` | cirka `34 ms`, `122 ms`, `206 ms`, `300 ms` | två cykler |
| `five_cycle_test` | `10` | cirka `36 ms`, `119 ms`, `213 ms`, `302 ms`, `381 ms`, `464 ms`, `548 ms`, `642 ms`, `721 ms`, `809 ms` | fem cykler |

Vändpunkterna ligger alltså ungefär `80-95 ms` isär trots att `arg` är
`127`. Om `arg` vore samma `5 ms`-duration som i 2-kanalsformatet borde
ett segment ta cirka `635 ms`. Därför ska 1-kanals `arg` inte tolkas som
samma tidsfält utan mer sannolikt som hastighet, ramp eller ett separat
kompakt delformat.

### Namngivna animationer i fabriksappen

Animationsnamnen finns inte i UART-paketen. Mappningen nedan är infererad
från ordningen i fabriksappens lista och från vilka maskar som används i
[dumps/animation_test_failed.json](dumps/animation_test_failed.json).

Före listan skickas en startpose:

```text
01 7F 01 7C 7C 7F 7F 7F 7F 7F
eyelid_left=124, eyelid_right=124, övriga=127
```

`Neck_Roll`, mask `0x05 = neck_elevation + neck_tilt`:

```text
(217,217,20) (255,127,20) (217,37,20) (127,0,20) (37,37,20)
(0,127,20) (37,217,20) (127,255,20) (217,217,20) (127,127,20)
```

`Half_Close`, mask `0x60 = eyelid_left + eyelid_right`:

```text
(31,31,30) (124,124,50)
```

`Eye_Roll_Slow_CCW`, mask `0x18 = eyes_left_right + eyes_up_down`:

```text
(211,43,20) (246,127,20) (211,211,20) (127,246,20) (43,211,20)
(8,127,20) (43,43,20) (127,8,20) (211,43,20) (127,127,20)
```

`Neck_Elevation_Stretch`, mask `0x06 = neck_elevation + neck_rotation`:

```text
(45,127,50) (245,127,50) (245,150,25)
(245,104,50) (245,127,35) (127,127,25)
```

`Alternate_Winks`, mask `0x60 = eyelid_left + eyelid_right`:

```text
(31,217,100) (217,31,100) (31,217,60) (217,31,30) (124,124,50)
```

`Eye_Roll_Fast_CW`, mask `0x18 = eyes_left_right + eyes_up_down`:

```text
(43,43,10) (8,127,10) (43,211,10) (127,246,10) (211,211,10)
(246,127,10) (211,43,10) (127,8,10) (43,43,10) (127,127,10)
```

`Neck_Tilt_Stretch`, mask `0x03 = neck_rotation + neck_tilt`:

```text
(226,18,50) (170,72,70) (226,18,70) (28,236,150)
(85,182,70) (28,236,70) (127,127,50)
```

`slow_blink.json` använder samma 2-kanalsformat på ögonlocken:

```text
mask 0x60 = eyelid_left + eyelid_right
(22,22,20) (252,252,60) (11,11,60) (22,22,10)
```

Detaljerad utbrytning av fabriksappens animationstest finns i
[ANIMATION_TEST_FAILED.md](ANIMATION_TEST_FAILED.md).

## Kanalmappning

Kanalmappningen är infererad genom att jämföra flera separata captures där bara en axel rördes åt gången.

Masker i positionskommandot:

| Mask | Tolkad kanal |
| --- | --- |
| `0x40` | `eyelid_left` |
| `0x20` | `eyelid_right` |
| `0x10` | `eye_leftright` |
| `0x08` | `eye_updown` |
| `0x04` | `neck_elevation` |
| `0x02` | `neck_rotation` |
| `0x01` | `neck_tilt` |

Feedbackkanaler i `position_feedback`:

| TX payload-index | Kanal |
| --- | --- |
| `2` | `eyelid_left` |
| `3` | `eyelid_right` |
| `4` | `eye_leftright` |
| `5` | `eye_updown` |
| `6` | `neck_elevation` |
| `7` | `neck_rotation` |
| `8` | `neck_tilt` |

Notera:

- RX-maskerna och TX-kanalindexen är inte samma tal, men mappningen mellan dem är stabil i de captures som analyserats

## PID-layout

`pid.json` visar `7` grupper av `P, I, D`, alltså totalt `21` stycken `float32`.

Avkodade värden:

| Kanal | P | I | D |
| --- | ---: | ---: | ---: |
| `eyelid_left` | `0.8` | `1.25e-05` | `8` |
| `eyelid_right` | `0.8` | `1.25e-05` | `8` |
| `eye_leftright` | `2.1` | `0.0023` | `16` |
| `eye_updown` | `2.1` | `0.0023` | `16` |
| `neck_elevation` | `1.0` | `0.00125` | `0.008` |
| `neck_rotation` | `0.3` | `0.001` | `0.08` |
| `neck_tilt` | `0.5` | `0.001` | `0.08` |

Starkt indicerat:

- ordningen i PID-payloaden följer samma kanalordning som positionsfeedbacken

## Sådant som är verifierat

- sync är `FA 00`
- byte `2` är payloadlängd
- total paketlängd är `payload_length + 5`
- checksumma är `Fletcher-16`
- det finns ett positionskommando med 1 mask + 1 värde
- det finns ett 7-kanals feedbackpaket
- det finns ett kort mask-echo-paket
- det finns PID write, PID read request och PID report
- det finns en separat kalibreringssekvens med opcode `0x4F` och `0x43`
- kalibrering kan köras både för alla kanaler och för delmängder via mask i `0x43`
- kalibreringsresultat kan returneras både som `43 00` och `43 <failed_mask> <status>`
- det finns servicekommandon för VR-läsning, kalibreringsvärden, versionsläsning och PID-reset
- `read_vr_value` returnerar samma 7-kanals feedbackformat som normal drift använder
- `version_id` ser ut att returnera float-version `2.39`
- `calibration_values` returnerar `7 x (min, max, range)` som little-endian `uint16`
- det finns en scriptad rörelsekommando-familj under opcode `0x01`
- 2-kanals-animationsscript använder keyframeformatet `0x80 | point_count` följt av `(value_a, value_b, duration)`-tripplar
- PID-data är `big-endian float32`
- kanalordningen är stabil mellan flera captures

## Sådant som fortfarande är oklart

## Betydelsen av `payload[2] = 0x01` i positionskommandot

Det är konstant i alla observerade positionspaket.

Möjliga tolkningar:

- register-id
- subkommando
- mode/sida/bank

Det är ännu inte verifierat.

## Exakt semantik för positionsvärdet

Det är tydligt att `value` påverkar servoets läge, men oklart om skalan är:

- rå servoenhet
- vinkel efter intern skalning
- setpoint i någon 8-bitars intern domän

Det är också oklart om värdet är linjärt mot verklig mekanisk vinkel.

## Exakt semantik för feedbackvärdena

Feedbacken beter sig som återrapporterad position och de blockerade
rörelse-capturesen gör det osannolikt att värdet bara är senaste setpoint.
När `eyelid_left` hölls fast stannade feedbacken långt från target och
fortsatte först till slutposition när mekaniken släpptes.

Det som fortfarande inte är helt avgjort är om värdet är:

- faktisk uppmätt position
- intern regulatorposition
- filtrerat estimat

Det som talar för riktig återrapportering är att värdet rör sig gradvis
mellan flera TX-paket medan servot går mot målet, och att det avviker från
setpoint under mekanisk blockering.

## Exakt betydelse av mask-echo-paketet

Paketet `FA 00 03 02 6E <mask> ...` är ännu inte fullt förstått.

Oklart:

- om `0x6E` är ett kommando-id, status-id, register-id eller fel/statusnotis
- om paketet triggas av kanalval, rörelse, intern status eller timeout efter blockerad rörelse

## Riktningarnas semantik i fysisk mening

I dokumentet används `RX` och `TX` enligt capturefilen.

Den logiska tolkningen är:

- `RX`: huvudenhet skickar kommandon till motorbordet
- `TX`: motorbordet skickar status/feedback tillbaka

Detta är starkt indicerat av datamönstret, men den slutliga namngivningen beror på hur logikanalysatorn kopplades.

## Om det finns fler packet-typer

Hittills är bara ovanstående typer observerade i de captures som analyserats.

Det kan fortfarande finnas:

- initpaket
- fel/statuspaket
- kalibreringspaket
- andra parametrar än PID

## Nästa steg för att minska osäkerheten

- spela in en capture där samma kanal får flera olika hopp mellan extrema lägen
- spela in en capture där ingen mekanik får röra sig men PID läses flera gånger
- spela in en capture där bara PID för en kanal ändras
- spela in en capture direkt efter uppstart
- spela in en capture medan ett mekaniskt stopp eller fel uppstår

Det borde göra det möjligt att avgöra:

- om feedback är verklig position eller bara intern setpoint
- om `payload[2]` i positionskommandot är register-id
- om mask-echo-paketet är ack eller status
- om det finns fler administrativa packet-typer
