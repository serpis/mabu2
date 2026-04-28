# Animation Test Failed

Det här är en separat utbrytning av [dumps/animation_test_failed.json](dumps/animation_test_failed.json).

Viktigt:

- animationsnamnen finns inte i UART-dumpen
- namnen nedan är infererade från ordningen i fabriksappens lista och från vilka maskar som faktiskt används
- själva scriptformatet går nu att avkoda strukturellt för 2-kanals-animationerna

## Initial pose

Före de namngivna animationerna skickas ett preset-paket:

```text
0.077917s RX pose_preset
eyelid_left=124
eyelid_right=124
eye_leftright=127
eye_updown=127
neck_elevation=127
neck_rotation=127
neck_tilt=127
```

Det ser ut som en startpose innan testsekvensen körs.

## Trolig mappning

## 1. Neck_Roll

- tid: `0.281816s`
- mask: `0x05` = `neck_elevation + neck_tilt`
- keyframes:

```text
(217,217,20)
(255,127,20)
(217,37,20)
(127,0,20)
(37,37,20)
(0,127,20)
(37,217,20)
(127,255,20)
(217,217,20)
(127,127,20)
```

## 2. Half_Close

- tid: `1.270960s`
- mask: `0x60` = `eyelid_left + eyelid_right`
- keyframes:

```text
(31,31,30)
(124,124,50)
```

- loggen rapporterar att denna animation failade
- loggens `Points within tolerance: 31` och `Points out of tolerance: 45` finns inte som egna UART-fält
- under detta tidsfönster finns `78` stycken `position_feedback`-paket, så appen verkar räkna toleranspunkter från feedbackströmmen snarare än från ett separat resultatpaket

## 3. Eye_Roll_Slow_CCW

- tid: `1.657156s`
- mask: `0x18` = `eye_leftright + eye_updown`
- keyframes:

```text
(211,43,20)
(246,127,20)
(211,211,20)
(127,246,20)
(43,211,20)
(8,127,20)
(43,43,20)
(127,8,20)
(211,43,20)
(127,127,20)
```

## 4. Neck_Elevation_Stretch

- tid: `2.637849s`
- mask: `0x06` = `neck_elevation + neck_rotation`
- keyframes:

```text
(45,127,50)
(245,127,50)
(245,150,25)
(245,104,50)
(245,127,35)
(127,127,25)
```

Namnet i appen nämner bara `Neck_Elevation`, men paketet driver även `neck_rotation`. Det tyder på att appens namn beskriver en högre nivå-animation, inte nödvändigtvis en enda rå DOF.

## 5. Alternate_Winks

- tid: `3.807777s`
- mask: `0x60` = `eyelid_left + eyelid_right`
- keyframes:

```text
(31,217,100)
(217,31,100)
(31,217,60)
(217,31,30)
(124,124,50)
```

- detta matchar namnet mycket väl: vänster/höger ögonlock alternerar
- loggen rapporterar att denna animation failade
- loggens `Points within tolerance: 281` och `Points out of tolerance: 59` finns inte som egna UART-fält
- under detta tidsfönster finns `346` stycken `position_feedback`-paket, så även här ser appen ut att räkna toleranspunkter från feedbackströmmen

## 6. Eye_Roll_Fast_CW

- tid: `5.513296s`
- mask: `0x18` = `eye_leftright + eye_updown`
- keyframes:

```text
(43,43,10)
(8,127,10)
(43,211,10)
(127,246,10)
(211,211,10)
(246,127,10)
(211,43,10)
(127,8,10)
(43,43,10)
(127,127,10)
```

Detta ser ut som samma typ av ögonrullning som ovan, men med kortare durations (`10` i stället för `20`), alltså konsistent med `Fast`.

## 7. Neck_Tilt_Stretch

- tid: `5.997356s`
- mask: `0x03` = `neck_rotation + neck_tilt`
- keyframes:

```text
(226,18,50)
(170,72,70)
(226,18,70)
(28,236,150)
(85,182,70)
(28,236,70)
(127,127,50)
```

Även här nämner appnamnet bara `Neck_Tilt`, medan paketet faktiskt driver både `neck_rotation` och `neck_tilt`.
