# Mätstrategi: nya kommandon medan motion-script kör

Det här dokumentet är inte en policy för hur engine ska bete sig. Det är en
plan för att ta reda på vad motorbrädan faktiskt gör när vi skickar ett nytt
`0x01`-kommando medan ett tidigare motion-script fortfarande bör vara aktivt.

Syftet är att skilja observerat brädbeteende från app-side antaganden.

## Frågor vi behöver besvara

1. Köar brädan ett nytt script, avbryter den aktivt script, ignorerar den det,
   eller hamnar den i ett blandläge?
2. Är beteendet samma för nytt keyframe-script och direkt positionskommando
   `01 <mask> 01 <target>`?
3. Spelar det roll om andra kommandot rör samma kanal, annan kanal eller alla
   kanaler?
4. Fortsätter första scriptets interna tidslinje efter ett direktkommando, eller
   försvinner den helt?
5. Finns det någon observerbar signal för "script klart", eller är feedback och
   tid enda rimliga indikatorerna?
6. Är beteendet stabilt över flera repetitioner och olika offset-tider?

## Hypoteser att falsifiera

Vi ska klassificera varje testkörning mot en av dessa hypoteser:

| Hypotes | Signatur i feedback |
| --- | --- |
| Intern kö | Andra rörelsen börjar först efter första scriptets förväntade slut. |
| Replace/abort | Rörelsen byter riktning kort efter andra TX och första scriptets senare keyframes syns inte. |
| Ignore/drop | Andra TX syns på bussen men ingen rörelse mot andra target syns. |
| Parallell kanalmerge | Andra kommandot påverkar sin kanal medan första scriptet fortsätter på andra kanaler. |
| Transient override | Direkt target påverkar servot kort, men första scriptets senare keyframes tar över igen. |
| Odefinierat/farligt | Feedback tappar paket, servo rycker oförklarligt, eller resultatet varierar mellan repetitioner. |

Om vi inte kan klassificera ett test med feedback, är testet för otydligt och
ska göras om med större separation mellan targets, längre duration eller en
annan kanal.

## Mätupplägg

Primärt ska testet köras med [motion_interaction_test.py](motion_interaction_test.py)
på Pi:n. Det scriptet äger UART:en, skickar båda kommandona med kontrollerad
offset, läser feedback och skriver resultatfiler. Det behövs alltså ingen
separat terminal som också försöker prata med motorbrädan.

```bash
python3 /home/pi/motion_interaction_test.py --suite full --repeats 3 --power-off
```

Om vi vill ha oberoende bevis på de fysiska TX/RX-tiderna kan en
logikanalysator spela in passivt bredvid, men den ska inte ersätta
single-process-testet:

```bash
python3 record.py 57600 8N1 8 1 dumps/overlap_case.json --capture-time 6s --group-sequences
```

Spara alltid:

- exakt kommando
- offset mellan första och andra TX
- förväntad duration för första scriptet
- `tx_events.csv`, `samples.csv`, `summary.csv` och `results.json`
- rå `.sr` och dekodad `.json` om passiv logikanalysator används
- `run.log` eller verbose-logg om den finns

Alla tester bör köras minst tre gånger per offset. Börja med små amplituder på
ögonkanaler, inte nacke, tills beteendet är begripligt.

`robot_motion.py --test-overlap` kan användas som första smoke-test, men det
räcker inte som bevis: det använder en specifik kanalgrupp och en specifik
offset. Matrisen nedan behövs för att förstå om beteendet beror på kommandoform,
kanalöverlapp eller timing.

## Baslinjer före overlap

Innan andra kommandot blandas in behöver varje primitiv ha en egen referens:

- kör första scriptet ensamt och mät faktisk duration, feedbacktrend och settle
- kör andra scriptet ensamt och verifiera att targeten är tydligt synlig
- kör direkt positionskommando ensamt och mät latens från TX till feedbacktrend
- upprepa baslinjen efter power-cycle om resultatet verkar variera

Overlap-testet ska sedan jämföras mot dessa baslinjer, inte bara mot förväntad
tickduration i koden.

## Experimentmatris

### A. Script följt av script på samma kanaler

Fråga: köar, avbryter eller ignorerar brädan ett nytt script som använder samma
kanaler?

Förslag:

- första script: `eye_leftright + eye_updown`, lång hold/ramp mot target A
- andra script: samma mask, tydligt motsatt target B
- offsets: `50 ms`, `250 ms`, `750 ms`, `förväntat_slut - 100 ms`,
  `förväntat_slut + 100 ms`

Tolkning:

- Om andra target börjar före första scriptets slut: brädan kan preempta eller
  replace:a.
- Om andra target börjar först efter slut: intern kö eller brädan ignorerar
  TX tills idle och kommandot ligger i egen buffert.
- Om andra target aldrig syns: drop/ignore.

### B. Script följt av script på disjunkta kanaler

Fråga: kan brädan köra två kanalgrupper parallellt, eller är scriptmotorn
global?

Förslag:

- första script: ögonkanaler `0x18`
- andra script: ögonlock `0x60`
- samma offsets som A

Tolkning:

- Om ögonlock börjar röra sig medan ögonscriptet fortsätter oförändrat finns
  kanalvis parallellism eller kanalmerge.
- Om ögonscriptet avbryts när ögonlock startar är scriptmotorn global.
- Om ögonlock väntar till slut finns intern kö.

### C. Script följt av direkt positionskommando på samma kanal

Fråga: betyder direkt position "ställ target nu" även när ett script kör?

Förslag:

- första script: lång rörelse/hold på `eye_leftright`
- andra kommando: `01 10 01 <motsatt target>`
- offset: `250 ms` och `förväntat_slut - 100 ms`

Tolkning:

- Direkt rörelse och sedan återgång till första scriptets senare target:
  transient override.
- Direkt rörelse och ingen återgång: direct command abortar/replacer scriptet
  för den kanalen eller hela scriptmotorn.
- Ingen rörelse före scriptslut: kö/drop/ignore.

### D. Script följt av direkt positionskommando på annan kanal

Fråga: är direkt position kanal-lokal även när scriptmotorn är upptagen?

Förslag:

- första script: `eye_leftright`
- andra kommando: ögonlock eller `eye_updown`

Tolkning:

- Annan kanal rör sig direkt medan första fortsätter: direktkommando är
  kanal-lokalt.
- Första avbryts: global replace.
- Annan kanal väntar: global kö eller busy-ignore.

### E. Script följt av all-kanals pose

Fråga: vad händer med `01 7F 01 <7 bytes>` under aktivt script?

Det här är närmast "ställ in denna vinkel" i praktiken, men också störst
ingrepp. Kör först med små targetskillnader.

Tolkning:

- Om pose tar över direkt men första scriptets senare keyframes syns efteråt
  ska engine aldrig använda raw pose som "avbrott"; det blir bara temporärt.
- Om pose tar över och scriptet dör kan raw pose vara ett faktiskt replace,
  men det behöver repeteras på flera offsets innan vi litar på det.

### F. Sent kommando efter förväntat slut men före mekanisk settle

Fråga: behöver vi vänta på mekanisk settle/feedback-tystnad, eller räcker
scriptduration?

Skicka andra kommandot precis efter beräknat scriptslut men medan servot
fortfarande kan röra sig. Om resultatet är stabilt kan engine vänta mindre. Om
det varierar behöver engine även använda feedbackbaserad idle-detektion.

## Analysmetod

För varje capture:

1. Dela upp TX i hela paket och notera timestamp för första och andra kommando.
2. Dekoda feedbackramar och plocka ut berörda kanalers bytevärden.
3. Markera förväntade keyframe-gränser från första scriptet.
4. Leta efter första tydliga trend mot andra target.
5. Jämför trendstart mot andra TX och första scriptets förväntade slut.
6. Klassificera enligt hypotes-tabellen.

En enkel CSV per test räcker:

```text
case,repeat,offset_s,first_expected_end_s,second_tx_s,
channel,second_effect_s,class,notes
```

Det är bättre att ha få tydliga testfall med bra klassning än många captures
som kräver tolkning.

## Beslut efter mätning

När matrisen är körd kan engine-policyn väljas utifrån resultatet:

- Om brädan köar stabilt kan engine använda intern kö mer aggressivt, men bara
  för verifierade kommandotyper.
- Om brädan replace:ar stabilt kan vi skapa ett explicit `interrupt/set`
  kommando, men det måste fortfarande dokumentera vilka format som fungerar.
- Om beteendet skiljer mellan script och direkt position ska engine exponera
  två olika semantiker.
- Om beteendet varierar eller är oklart ska engine fortsätta äga all köning och
  bara skicka nytt motionkommando när brädan är idle.

Innan testerna nedan kördes skulle alla påståenden om kö, replace och ignore
märkas som hypoteser, inte fakta. Efter körningen är den nuvarande modellen
sammanfattad i resultatavsnittet.

## Resultat 2026-05-03

Testscriptet [motion_interaction_test.py](motion_interaction_test.py) kördes på
Pi:n med samma process som både skickade TX och läste feedback från UART:

```bash
python3 /home/pi/motion_interaction_test.py \
  --suite full \
  --repeats 3 \
  --output-dir /home/pi/motion_interaction_results/20260503-141230-full-r3 \
  --power-off \
  --verbose
```

En kompletterande körning för positionskommando på helt disjunkt kanal:

```bash
python3 /home/pi/motion_interaction_test.py \
  --case-filter disjoint_lid \
  --repeats 3 \
  --skip-baselines \
  --output-dir /home/pi/motion_interaction_results/20260503-141841-disjoint-pos-r3 \
  --power-off \
  --verbose
```

Resultatfilerna hämtades också till:

```text
pi_results/20260503-141230-full-r3/
pi_results/20260503-141841-disjoint-pos-r3/
```

I den lokala fullkörningskatalogen finns även `summary_sustained.csv`, som är
samma rådata omräknad med den skärpta effekt-detektionen i nuvarande
`motion_interaction_test.py` (minst tre konsekutiva feedbackprov över
tröskeln).

### Sammanfattad beteendemodell

Motorbrädan beter sig inte som en enkel global FIFO-kö. Den verkar snarare ha
kanalvis motion-state:

- nytt script på samma aktiva kanal börjar inte direkt vid TX
- samma-kanals kommandot börjar synas först runt nästa keyframe-/segmentgräns
  plus mekanisk/regleringslatens
- när samma-kanals kommandot tar över syns inte återstående keyframes från det
  första scriptet för den kanalen
- nytt script eller positionskommando på en kanal som inte ingår i det aktiva
  scriptet kan börja medan första scriptet fortsätter på sina kanaler
- positionskommando på en annan kanal som ändå ingår i första scriptets mask
  väntar också till den kanalens pågående segmentgräns
- all-kanals pose beter sig som ett positionskommando för de kanaler den
  skriver; aktiva ögonkanaler togs över vid segmentgräns

Det betyder att "interrupt" finns, men den är varken omedelbar eller global.
Den säkrare app-side-regeln är fortfarande att engine inte ska anta global
köning. Om vi vill utnyttja detta bör vi modellera det som kanalvis takeover
vid segmentgräns.

### Viktiga mätpunkter

| Fall | Andra kommando | Effekt i feedback |
| --- | --- | --- |
| lång 2-kanals ögonanimation, samma kanal, andra TX vid `0.45s` | nytt ögonscript | `eye_leftright` började gå mot andra target vid median `1.415s`, alltså efter första framegränsen `1.2s`; återstående första-script-keyframes syntes inte |
| lång 2-kanals ögonanimation, samma kanal, andra TX vid `2.70s` | nytt ögonscript | effekt vid median `3.08s`, efter första scriptets förväntade slut `2.9s` |
| kort 2-kanals ögonanimation, samma kanal, andra TX vid `0.12s` | nytt ögonscript | effekt vid cirka `0.43s`; återstående första-script-keyframes syntes inte |
| ögonscript följt av ögonlocksscript | disjunkta kanaler | ögonlock började `0.09-0.23s` efter andra TX medan ögonscriptets senare neutral-keyframe fortfarande syntes |
| enkansals `0x80`-script, samma kanal | nytt enkansals script | samma takeover-mönster som 2-kanals script |
| enkansals `0x80`-script, annan kanal | nytt enkansals script | andra kanalen började röra sig medan första kanalen fortsatte |
| ögonscript följt av position på `eye_leftright` | samma kanal | position tog över vid segmentgräns; återstående ögon-keyframes syntes inte |
| ögonscript följt av position på `eye_updown` | kanal i samma scriptmask, men inte aktivt rörlig | position tog över `eye_updown` vid segmentgräns medan `eye_leftright` fortsatte |
| ögonscript följt av position på `eyelid_left` | helt disjunkt kanal | ögonlocket började `0.05-0.06s` efter TX medan ögonscriptet fortsatte |
| ögonscript följt av all-kanals pose | inkluderar aktiva ögonkanaler | aktiva ögonkanaler togs över vid segmentgräns; återstående ögon-keyframes syntes inte |

### Konsekvens för engine

Om engine ska vara enkel och robust: fortsätt serialisera rörelsekommandon och
skicka bara nytt script när egna state-maskinen bedömer berörda kanaler idle.

Om engine ska bli mer responsiv: den kan tillåta kanalvis takeover, men då
behöver varje aktiv kanal ha egen `busy_until` baserad på aktuell keyframe, inte
bara ett globalt `expected_done_at`.
