#!/bin/bash
# Cheap, persistent system watchdog.
# Appends a timestamped line every 2s with throttle status, voltage,
# temperature, load, free RAM. Output goes to ~/camera/watchdog.log
# (survives reboots, unlike /tmp). Run with: nohup ./watchdog.sh &disown
#
# Throttle bits (output of `vcgencmd get_throttled`):
#   0x1   undervoltage NOW
#   0x2   ARM frequency capped NOW
#   0x4   currently throttled
#   0x8   soft temp limit NOW
#   0x10000  undervoltage occurred since boot
#   0x20000  ARM freq capped since boot
#   0x40000  throttling occurred since boot
#   0x80000  soft temp limit since boot

LOG="$HOME/camera/watchdog.log"
mkdir -p "$(dirname "$LOG")"

echo "=== boot $(date -Is) uptime=$(awk '{print $1}' /proc/uptime)s ===" >> "$LOG"

while true; do
  ts=$(date -Is)
  thr=$(vcgencmd get_throttled 2>/dev/null)
  vol=$(vcgencmd measure_volts 2>/dev/null)
  tmp=$(vcgencmd measure_temp 2>/dev/null)
  load=$(awk '{print $1,$2,$3}' /proc/loadavg)
  mem=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
  echo "$ts $thr $vol $tmp load=$load memAvailKB=$mem" >> "$LOG"
  sync "$LOG"  # flush to disk so we don't lose lines on hard reset
  sleep 2
done
