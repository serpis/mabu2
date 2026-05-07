#!/bin/sh
set -eu

SERVICE_NAME=face-follow.service
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

sudo install -m 0644 "$SCRIPT_DIR/$SERVICE_NAME" "/etc/systemd/system/$SERVICE_NAME"

if [ ! -f /home/pi/face-follow.env ]; then
  install -m 0644 "$SCRIPT_DIR/face-follow.env" /home/pi/face-follow.env
else
  echo "Keeping existing /home/pi/face-follow.env"
fi

sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
pkill -TERM -f "^python3 /home/pi/face_follow.py" 2>/dev/null || true
pkill -TERM -f "^rpicam-vid .*--codec mjpeg" 2>/dev/null || true
sleep 1

sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"
sudo systemctl status --no-pager -l "$SERVICE_NAME"
