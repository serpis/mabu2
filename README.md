# UART dump med FX2LA på macOS

Det här upplägget använder `sigrok-cli` för själva fångsten från en FX2LA och `record.py` som Python-wrapper för att:

- spela in `RX` på kanal `0`
- spela in `TX` på kanal `1`
- dekoda UART offline
- spara resultatet som JSON
- spara varje RX/TX-byte med timestamp och värde

## Varför detta upplägg

`sigrok` har redan stöd för FX2-baserade logikanalysatorer via `fx2lafw`, och `sigrok-cli` kan exportera UART-dekodning som JSON-trace. Det här scriptet bygger vidare på det i stället för att uppfinna ett eget råformat.

Scriptet fångar först en rå `.sr`-session och dekodar sedan offline. Det är robustare än att försöka både sampla och dekoda live i samma steg.

## Installera

```bash
brew install sigrok-cli
```

Verifiera gärna att din FX2LA hittas:

```bash
sigrok-cli --scan
```

## Användning

```bash
python3 record.py 115200 8N1 8 1 dump.json
```

Argument:

- `115200`: baudrate
- `8N1`: UART-läge
- `8`: ny sekvens om bussen varit tyst i minst 8 teckentider
- `1`: kasta sekvenser kortare än 1 byte
- `dump.json`: slutlig JSON-utfil

Default:

- `RX` fångas på kanal `0`
- `TX` fångas på kanal `1`
- samplerate väljs automatiskt till närmaste rimliga FX2-hastighet, minst cirka `32x` baudraten
- en rå `dump.sr` sparas bredvid JSON-filen
- JSON-filen innehåller separata listor `rx_bytes` och `tx_bytes`

Stoppa inspelningen med `Ctrl-C`.

## Vanliga flaggor

```bash
python3 record.py 115200 8N1 8 1 dump.json --capture-time 10s
python3 record.py 115200 8N1 8 1 dump.json --samplerate 8m
python3 record.py 115200 8N1 8 1 dump.json --invert-rx
python3 record.py 115200 8N1 8 1 dump.json --group-sequences
python3 record.py 115200 8N1 8 1 dump.json --group-sequences --prompt-names
python3 record.py 115200 8N1 8 1 dump.json --no-keep-sr
```

## Gissa UART-inställningar från `.sr`

Om du först har spelat in en rå capture kan du låta `guess_uart.py` ranka vanliga baudrate/mode-kombinationer:

```bash
python3 guess_uart.py raw.sr
```

Vanliga exempel:

```bash
python3 guess_uart.py raw.sr --top 8
python3 guess_uart.py raw.sr --channels RX,TX
python3 guess_uart.py raw.sr --min-baud 9600 --max-baud 230400
python3 guess_uart.py raw.sr --exhaustive
```

Det här är en heuristisk gissning, inte en matematisk garanti, men den fungerar bra för att snabbt hitta rimliga kandidater som `57600 8N1`.

## JSON-format

JSON-filen innehåller:

- `rx_bytes`: alla mottagna bytes i ordning
- `tx_bytes`: alla sända bytes i ordning
- `byte_count`: antal bytes per riktning

Varje bytepost innehåller:

- `timestamp_sample`
- `timestamp_seconds`
- `value`
- `hex`
- `ascii`

Exempel:

```json
{
  "index": 1,
  "timestamp_sample": 304,
  "timestamp_seconds": 0.000304,
  "end_sample": 1138,
  "end_seconds": 0.001138,
  "value": 65,
  "hex": "41",
  "ascii": "A"
}
```

Om du vill ha grupperade sekvenser ovanpå byte-listorna kan du lägga till `--group-sequences`. Då används `idle_gap_chars` och `min_bytes`, och du kan även lägga till `--prompt-names`.

## Praktiska råd

- Kontrollera jord mellan logikanalysatorn och målsystemet.
- Kontrollera att din FX2LA verkligen tål den logiknivå du mäter på.
- Spara gärna `.sr`-filen även om du främst jobbar i JSON. Då kan du senare dekoda om samma capture med annan baudrate, invertering eller annan protokolltolkning.

## Robot- och PID-verktyg

Robotens UART-protokoll och rörelseverktyg dokumenteras i `ROBOT_MOTION.md`,
`ROBOT_ANIMATION.md` och `ROBOT_ENGINE.md`.

Eye-servo-benchmark och senaste `eye_updown` PID-tuning finns i
`EYE_PID_TUNING.md`.

## Face follow vid boot

`face_follow.py` kan köras som en `systemd`-service på Raspberry Pi:n. Det startar
både face-follow-loopen och debug-dashboarden på port `8080`.

Installera eller uppdatera servicefilen på Pi:n:

```bash
rsync -av deploy/ pi@192.168.1.147:/home/pi/deploy/
ssh -t pi@192.168.1.147 /home/pi/deploy/install_face_follow_service.sh
```

Service-defaults ligger i `/home/pi/face-follow.env`. Dashboardens runtime-val
sparas separat i `/home/pi/face_follow_settings.json`, och kalibreringspunkterna
ligger i `/home/pi/face_follow_calibration.json`.

Vanliga kommandon på Pi:n:

```bash
sudo systemctl status face-follow.service
sudo systemctl restart face-follow.service
sudo systemctl stop face-follow.service
journalctl -u face-follow.service -n 100 --no-pager
```

Efter kodändringar från utvecklingsmaskinen:

```bash
./deploy/update_face_follow_pi.sh
```
