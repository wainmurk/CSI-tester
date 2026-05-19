#!/usr/bin/env bash
set -euo pipefail

REPO_RAW="${REPO_RAW:-https://raw.githubusercontent.com/wainmurk/CSI-tester/main}"
INSTALL_DIR="/opt/avcsi"
WIDTH="480"
HEIGHT="320"
FB="auto"
DEVICE="auto"
ROTATE="0"
STANDARD="auto"
ADDR="auto"
INSTALL_LCD_DRIVER="1"
ENABLE_SERVICE="1"
START_SERVICE="1"

usage() {
  cat <<'EOF'
Usage:
  curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash

Options:
  --install-dir DIR       Install directory (default: /opt/avcsi)
  --width N               Display width (default: 480)
  --height N              Display height (default: 320)
  --fb PATH|auto          Framebuffer (default: auto; prefers /dev/fb1)
  --device PATH|auto      V4L2 device (default: auto)
  --rotate 0|90|180|270   Rotate captured image in the app (default: 0)
  --standard auto|PAL|NTSC Analog video standard (default: auto)
  --addr auto|0x20|0x21 ADV7282-M I2C address for dtoverlay (default: auto)
  --no-lcd-driver         Do not run LCD-show/LCD35-show
  --no-enable             Install files without enabling the service
  --no-start              Do not start service after install
  -h, --help              Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --width) WIDTH="$2"; shift 2 ;;
    --height) HEIGHT="$2"; shift 2 ;;
    --fb) FB="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --rotate) ROTATE="$2"; shift 2 ;;
    --standard) STANDARD="$2"; shift 2 ;;
    --addr) ADDR="$2"; shift 2 ;;
    --no-lcd-driver) INSTALL_LCD_DRIVER="0"; shift ;;
    --no-enable) ENABLE_SERVICE="0"; shift ;;
    --no-start) START_SERVICE="0"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root. Example: curl -fsSL ${REPO_RAW}/installer.sh | sudo bash" >&2
  exit 1
fi

if [[ "$ROTATE" != "0" && "$ROTATE" != "90" && "$ROTATE" != "180" && "$ROTATE" != "270" ]]; then
  echo "--rotate must be 0, 90, 180, or 270" >&2
  exit 2
fi

if [[ "$STANDARD" != "auto" && "$STANDARD" != "PAL" && "$STANDARD" != "NTSC" && "$STANDARD" != "SECAM" && "$STANDARD" != "pal" && "$STANDARD" != "ntsc" && "$STANDARD" != "secam" ]]; then
  echo "--standard must be auto, PAL, NTSC, or SECAM" >&2
  exit 2
fi

if [[ "$ADDR" != "auto" && "$ADDR" != "0x20" && "$ADDR" != "0x21" && "$ADDR" != "20" && "$ADDR" != "21" ]]; then
  echo "--addr must be auto, 0x20, or 0x21" >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive

echo "[1/7] Installing packages"
apt-get update
apt-get install -y \
  ca-certificates \
  curl \
  git \
  i2c-tools \
  python3 \
  python3-numpy \
  python3-opencv \
  v4l-utils

echo "[2/7] Downloading AV-CSI tester files"
install -d -m 0755 "$INSTALL_DIR"
curl -fsSL "${REPO_RAW}/av_csi_tester.py" -o "${INSTALL_DIR}/av_csi_tester.py"
curl -fsSL "${REPO_RAW}/README.md" -o "${INSTALL_DIR}/README.md"
chmod 0755 "${INSTALL_DIR}/av_csi_tester.py"
chmod 0644 "${INSTALL_DIR}/README.md"

echo "[3/7] Writing runtime config"
cat >/etc/default/avcsi <<EOF
AVCSI_WIDTH=${WIDTH}
AVCSI_HEIGHT=${HEIGHT}
AVCSI_FB=${FB}
AVCSI_DEVICE=${DEVICE}
AVCSI_ROTATE=${ROTATE}
AVCSI_STANDARD=${STANDARD}
EOF

echo "[4/7] Installing systemd service"
curl -fsSL "${REPO_RAW}/avcsi.service" -o /etc/systemd/system/avcsi.service
chmod 0644 /etc/systemd/system/avcsi.service
systemctl daemon-reload
if [[ "$ENABLE_SERVICE" == "1" ]]; then
  systemctl enable avcsi.service
fi

detect_adv_addr() {
  if [[ "$ADDR" == "20" || "$ADDR" == "0x20" ]]; then echo "0x20"; return; fi
  if [[ "$ADDR" == "21" || "$ADDR" == "0x21" ]]; then echo "0x21"; return; fi
  local bus table
  for dev in /dev/i2c-*; do
    [[ -e "$dev" ]] || continue
    bus="${dev##*-}"
    table="$(i2cdetect -y "$bus" 2>/dev/null || true)"
    if printf '%s\n' "$table" | awk '/^20:/ {if ($2=="20" || $2=="UU") found=1} END {exit !found}'; then
      echo "0x20"; return
    fi
    if printf '%s\n' "$table" | awk '/^20:/ {if ($3=="21" || $3=="UU") found=1} END {exit !found}'; then
      echo "0x21"; return
    fi
  done
  echo "0x21"
}

echo "[4/7] Ensuring ADV7282-M overlay"
ADV_ADDR="$(detect_adv_addr)"
BOOT_CONFIG=""
if [[ -f /boot/firmware/config.txt ]]; then
  BOOT_CONFIG="/boot/firmware/config.txt"
elif [[ -f /boot/config.txt ]]; then
  BOOT_CONFIG="/boot/config.txt"
fi
if [[ -n "$BOOT_CONFIG" ]]; then
  OVERLAY_LINE="dtoverlay=adv7282m,addr=${ADV_ADDR}"
  cp "$BOOT_CONFIG" "${BOOT_CONFIG}.avcsi.bak"
  if grep -Eq '^[[:space:]]*dtoverlay=adv7282m' "$BOOT_CONFIG"; then
    sed -i -E "s#^[[:space:]]*dtoverlay=adv7282m.*#${OVERLAY_LINE}#" "$BOOT_CONFIG"
    echo "Updated ADV7282-M overlay in $BOOT_CONFIG to ${OVERLAY_LINE}. Reboot is required."
  else
    printf '\n# AV-CSI ADV7282-M\n%s\n' "$OVERLAY_LINE" >>"$BOOT_CONFIG"
    echo "Added ${OVERLAY_LINE} to $BOOT_CONFIG. Reboot is required."
  fi
else
  echo "Boot config not found; cannot add dtoverlay=adv7282m automatically."
fi

echo "[5/7] Checking devices"
v4l2-ctl --list-devices || true
i2cdetect -l || true
if [[ -e /dev/fb1 ]]; then
  echo "Framebuffer /dev/fb1 found."
else
  echo "Framebuffer /dev/fb1 is not present yet. LCD-show usually creates it after reboot."
fi

echo "[6/7] LCD driver"
if [[ "$INSTALL_LCD_DRIVER" == "1" ]]; then
  cd /opt
  rm -rf LCD-show
  git clone https://github.com/goodtft/LCD-show.git
  chmod -R 755 LCD-show
  cd LCD-show
  echo "Running LCD35-show. It may reboot the Raspberry Pi."
  ./LCD35-show
else
  echo "Skipped LCD-show. Install later with: cd /opt/LCD-show && sudo ./LCD35-show"
fi

echo "[7/7] Starting service"
if [[ "$START_SERVICE" == "1" ]]; then
  systemctl restart avcsi.service || true
fi

echo "Done."
echo "Status: sudo systemctl status avcsi.service"
