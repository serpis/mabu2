# Robot UART Protocol Notes

Det här dokumentet sammanfattar vad som hittills är avkodat från UART-trafiken mellan huvudenhet och motor/styrenhet.

Fokus här är:

- sådant som är verifierat direkt från captures
- sådant som är starkt indicerat men fortfarande är tolkning
- sådant som fortfarande är oklart

Underlag som använts:

- [dumps/dump.json](dumps/dump.json)
- [dumps/left_eyelid.json](dumps/left_eyelid.json)
- [dumps/eyelid_right.json](dumps/eyelid_right.json)
- [dumps/eye_leftright.json](dumps/eye_leftright.json)
- [dumps/eye_updown.json](dumps/eye_updown.json)
- [dumps/neck_elevate.json](dumps/neck_elevate.json)
- [dumps/neck_right.json](dumps/neck_right.json)
- [dumps/neck_tilt.json](dumps/neck_tilt.json)
- [dumps/pid.json](dumps/pid.json)

Praktisk decoder:

- [decode_dump.py](decode_dump.py)

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

## Kanalmappning

Kanalmappningen är infererad genom att jämföra flera separata captures där bara en axel rördes åt gången.

Masker i positionskommandot:

| Mask | Tolkad kanal |
| --- | --- |
| `0x40` | `left_eyelid` |
| `0x20` | `eyelid_right` |
| `0x10` | `eye_leftright` |
| `0x08` | `eye_updown` |
| `0x04` | `neck_elevation` |
| `0x02` | `neck_rotation` |
| `0x01` | `neck_tilt` |

Feedbackkanaler i `position_feedback`:

| TX payload-index | Kanal |
| --- | --- |
| `2` | `left_eyelid` |
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
| `left_eyelid` | `0.8` | `1.25e-05` | `8` |
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
