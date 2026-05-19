#!/usr/bin/env bash
set -euo pipefail

REPO_RAW="${REPO_RAW:-https://raw.githubusercontent.com/wainmurk/CSI-tester/main}"
INSTALL_DIR="/opt/avcsi"
WIDTH="480"
HEIGHT="320"
FB="auto"
DEVICE="auto"
ROTATE="0"
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

export DEBIAN_FRONTEND=noninteractive

echo "[1/7] Installing packages"
apt-get update
apt-get install -y \
  ca-certificates \
  curl \
  git \
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
EOF

echo "[4/7] Installing systemd service"
curl -fsSL "${REPO_RAW}/avcsi.service" -o /etc/systemd/system/avcsi.service
chmod 0644 /etc/systemd/system/avcsi.service
systemctl daemon-reload
if [[ "$ENABLE_SERVICE" == "1" ]]; then
  systemctl enable avcsi.service
fi

echo "[5/7] Checking devices"
v4l2-ctl --list-devices || true
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
