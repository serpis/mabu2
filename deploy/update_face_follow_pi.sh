#!/bin/sh
set -eu

PI_HOST=${PI_HOST:-pi@192.168.1.147}
PI_DIR=${PI_DIR:-/home/pi}

rsync -av \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  face_follow.py \
  dialog_state.py \
  robot_engine.py \
  robot_motion.py \
  robot_animation.py \
  camera \
  "$PI_HOST:$PI_DIR/"

rsync -av --delete sound/ "$PI_HOST:$PI_DIR/sound/"
rsync -av --delete quiz/ "$PI_HOST:$PI_DIR/quiz/"

if ssh "$PI_HOST" "sudo -n systemctl restart face-follow.service"; then
  ssh "$PI_HOST" "systemctl status --no-pager -l face-follow.service"
else
  echo "sudo restart failed; killing face_follow.py so systemd treats it as failed and restarts it"
  ssh "$PI_HOST" "pkill -KILL -f '^/usr/bin/python3 /home/pi/face_follow.py' || pkill -KILL -f '^python3 /home/pi/face_follow.py' || true"
  sleep 2
  if ssh "$PI_HOST" "systemctl is-active --quiet face-follow.service"; then
    ssh "$PI_HOST" "systemctl status --no-pager -l face-follow.service"
  else
    echo "systemd service is not active; starting face_follow.py manually as pi"
    ssh "$PI_HOST" "cd '$PI_DIR' && setsid -f /bin/sh -c '. /home/pi/face-follow.env 2>/dev/null || true; exec /usr/bin/python3 /home/pi/face_follow.py \${FACE_FOLLOW_ARGS:-}' </dev/null > /home/pi/face_follow_manual.log 2>&1"
    sleep 2
    ssh "$PI_HOST" "pgrep -af 'face_follow.py|rpicam' || true"
  fi
fi
