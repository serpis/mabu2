#!/bin/sh
set -eu

CONFIG=/boot/firmware/config.txt
CMDLINE=/boot/firmware/cmdline.txt

if ! grep -Eq '^[[:space:]]*dtoverlay=uart0-pi5([,[:space:]]|$)' "$CONFIG"; then
    printf '\n# Enable UART0 on GPIO14/GPIO15, physical pins 8/10.\ndtoverlay=uart0-pi5\n' | sudo tee -a "$CONFIG" >/dev/null
    echo "Added dtoverlay=uart0-pi5 to $CONFIG"
else
    echo "dtoverlay=uart0-pi5 is already present in $CONFIG"
fi

if grep -Eq '(^|[[:space:]])console=serial' "$CMDLINE"; then
    sudo cp "$CMDLINE" "$CMDLINE.codex-bak"
    sudo sed -i 's/[[:space:]]*console=serial[^[:space:]]*//g' "$CMDLINE"
    echo "Removed serial console from $CMDLINE"
else
    echo "No serial console entry found in $CMDLINE"
fi

sudo systemctl disable --now serial-getty@serial0.service serial-getty@ttyAMA0.service 2>/dev/null || true

echo "Reboot required. After reboot, check:"
echo "  ls -l /dev/ttyAMA0"
echo "  pinctrl get 14,15"
