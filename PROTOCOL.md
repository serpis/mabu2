# Robot UART Protocol Notes

Det här dokumentet sammanfattar vad som hittills är avkodat från UART-trafiken mellan huvudenhet och motor/styrenhet.

Fokus här är:

- sådant som är verifierat direkt från captures
- sådant som är starkt indicerat men fortfarande är tolkning
- sådant som fortfarande är oklart

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
- [dumps/version_id.json](dumps/version_id.json)
- [dumps/read_pid_values.json](dumps/read_pid_values.json)
- [dumps/read_vr_value.json](dumps/read_vr_value.json)
- [dumps/reset_pid_values.json](dumps/reset_pid_values.json)
- [dumps/calibration_values.json](dumps/calibration_values.json)
- [dumps/single_cycle_test.json](dumps/single_cycle_test.json)
- [dumps/double_cycle_test.json](dumps/double_cycle_test.json)
- [dumps/five_cycle_test.json](dumps/five_cycle_test.json)
- [dumps/eyes_eyelid_test.json](dumps/eyes_eyelid_test.json)
- [dumps/eyes_eyelid_test_fail.json](dumps/eyes_eyelid_test_fail.json)
- [dumps/neck_rotation_test.json](dumps/neck_rotation_test.json)
- [dumps/neck_rotation_test_fail.json](dumps/neck_rotation_test_fail.json)
- [dumps/neck_tilt_test.json](dumps/neck_tilt_test.json)
- [dumps/slow_blink.json](dumps/slow_blink.json)

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

Det som fortfarande är oklart:

- exakt när detta paket skickas
- om det är en ack, statusindikering eller någon form av kanalval

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

- `eyes_eyelid_test.json`: `43 78`
- `neck_rotation_test.json`: `43 02`
- `neck_tilt_test.json`: `43 05`

Masktolkning:

- `0x78 = 0x40|0x20|0x10|0x08` = båda ögonlocken + båda ögonaxlarna
- `0x02` = `neck_rotation`
- `0x05 = 0x04|0x01` = `neck_elevation` + `neck_tilt`

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

Tolkning:

- `triplet[0] = min`
- `triplet[1] = max`
- `triplet[2] = measured_range`

Det som fortfarande är oklart:

- i vilken fysisk enhet min/max mäts
- om min/max är råa ADC/VR-värden eller redan internskalade positionsenheter

Notering:

- samma capture innehåller fler TX-bytes efter det första giltiga packetet
- de extra bytena gick inte att packetisera med samma framing och ser ut som en avklippt eller sammanblandad servicepayload

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

## 14. Motion script command

I flera nya captures används ett längre RX-paket som börjar med `0x01`, men som inte är det enkla positionskommandot.

Observerat i:

- [dumps/animation_test_failed.json](dumps/animation_test_failed.json)
- [dumps/single_cycle_test.json](dumps/single_cycle_test.json)
- [dumps/double_cycle_test.json](dumps/double_cycle_test.json)
- [dumps/five_cycle_test.json](dumps/five_cycle_test.json)
- [dumps/slow_blink.json](dumps/slow_blink.json)

Exempel:

```text
single_cycle_test:
FA 00 07 01 08 04 E5 7F 19 7F 19 0D

double_cycle_test:
FA 00 0B 01 08 08 E5 7F 19 7F E5 7F 19 7F 69 13
```

Verifierat:

- opcode är `0x01`
- byte `1` beter sig som kanalmask
- i cycle-test-captures är byte `2` lika med antalet återstående scriptbytes
- resten av payloaden består där av upprepade bytepar

Stark tolkning:

- detta är en scriptad rörelse- eller pattern-sekvens
- i cycle-test-filerna ser varje bytepar ut att vara något i stil med `target, arg`
- för `single_cycle_test`, `double_cycle_test` och `five_cycle_test` är samma två par upprepade:
- `E5 7F`
- `19 7F`
- masken är `0x08`, vilket stämmer med att `eye_updown` är den kanal som faktiskt rör sig i TX-feedbacken

Nyare fynd från `animation_test_failed.json`:

- för 2-kanals-script har första scriptbyten hög bit satt, alltså `0x80 | point_count`
- resten av scriptet består då av upprepade tripplar:
  - `value_for_channel_A`
  - `value_for_channel_B`
  - `duration`
- exempel:

```text
FA 00 09 01 60 82 1F 1F 1E 7C 7C 32 ...
```

tolkas som:

- mask `0x60` = `eyelid_left + eyelid_right`
- `0x82` = `2` keyframes
- keyframes:
  - `(31,31,30)`
  - `(124,124,50)`

Detta matchar mycket väl loggnamnet `Half_Close`.

Ytterligare exempel:

- `Alternate_Winks` använder också mask `0x60`, men med `5` keyframes
- `Eye_Roll_Slow_CCW` och `Eye_Roll_Fast_CW` använder mask `0x18`
- de skiljer sig framför allt i durations: `20` för slow och `10` för fast

För `slow_blink.json` gäller sannolikt samma kommandofamilj, men med ett ännu inte fullt förstått delformat.

`animation_test_failed.json` använder samma familj mer extensivt:

- den börjar med `set_power_mask 0x7F`
- den skickar först ett separat pose-preset för alla kanaler
- därefter följer sju 2-kanals keyframe-script
- de sju scripten kan sannolikt mappas 1:1 mot fabriksappens namngivna animationslista

De två räknarna i appens felruta:

- `Points within tolerance`
- `Points out of tolerance`

syns inte som egna UART-fält. De ser i stället ut att vara beräknade i appen från `position_feedback` under respektive animations tidsfönster.

Det tyder på att fabriksappens animerings- eller funktionsprov återanvänder samma scriptmekanism snarare än nya separata opcodes.

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

Feedbacken beter sig som återrapporterad position, men det är inte bevisat om värdet är:

- faktisk uppmätt position
- intern regulatorposition
- filtrerat estimat
- senaste accepterade setpoint med slewrate

Det som talar för riktig återrapportering är att värdet rör sig gradvis mellan flera TX-paket medan servot går mot målet.

## Exakt betydelse av mask-echo-paketet

Paketet `FA 00 03 02 6E <mask> ...` är ännu inte fullt förstått.

Oklart:

- om `0x6E` är ett kommando-id, status-id eller register-id
- om paketet triggas av kanalval, rörelse eller intern status

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
