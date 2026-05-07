#!/bin/sh
set -eu

PI_HOST=${PI_HOST:-pi@192.168.1.147}
PI_DIR=${PI_DIR:-/home/pi}

rsync -av \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  face_follow.py \
  robot_engine.py \
  robot_motion.py \
  robot_animation.py \
  camera \
  "$PI_HOST:$PI_DIR/"

ssh -t "$PI_HOST" "sudo systemctl restart face-follow.service && systemctl status --no-pager -l face-follow.service"
