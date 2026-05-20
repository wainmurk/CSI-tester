#!/usr/bin/env bash
set -euo pipefail

REPO_RAW="${REPO_RAW:-https://raw.githubusercontent.com/wainmurk/CSI-tester/main}"
INSTALL_DIR="/opt/avcsi"
WIDTH="720"
HEIGHT="576"
FB="/dev/fb0"
OUTPUT="auto"
DEVICE="auto"
ROTATE="0"
STANDARD="auto"
ADDR="auto"
ENABLE_SERVICE="1"
START_SERVICE="1"
FORCE_FULLHD="0"
KIOSK="0"
TARGET_USER="${SUDO_USER:-${USER:-pi}}"
SKIP_APT="${SKIP_APT:-0}"

usage() {
  cat <<'EOF'
Usage:
  curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash

Options:
  --install-dir DIR       Install directory (default: /opt/avcsi)
  --width N               HDMI output width (default: 720)
  --height N              HDMI output height (default: 576)
  --fb PATH|auto          Framebuffer (default: /dev/fb0 for HDMI)
  --output auto|desktop|sdl|fb HDMI output backend (default: desktop unless --kiosk)
  --device PATH|auto      V4L2 device (default: auto)
  --rotate 0|90|180|270   Rotate captured image in the app (default: 0)
  --standard auto|PAL|NTSC Analog video standard (default: auto)
  --addr auto|0x20|0x21 ADV7282-M I2C address for dtoverlay (default: auto)
  --force-fullhd         Force HDMI 0 to 1920x1080 and disable console blanking
  --kiosk                Disable desktop and run as system HDMI/KMS service
  --keep-desktop         Keep Raspberry Pi OS desktop/display manager enabled (default)
  --user USER            Desktop user for fullscreen autostart (default: sudo user)
  --skip-apt             Do not run apt-get package installation
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
    --output) OUTPUT="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --rotate) ROTATE="$2"; shift 2 ;;
    --standard) STANDARD="$2"; shift 2 ;;
    --addr) ADDR="$2"; shift 2 ;;
    --force-fullhd) FORCE_FULLHD="1"; WIDTH="1920"; HEIGHT="1080"; shift ;;
    --kiosk) KIOSK="1"; [[ "$OUTPUT" == "auto" ]] && OUTPUT="auto"; shift ;;
    --keep-desktop) KIOSK="0"; shift ;;
    --user) TARGET_USER="$2"; shift 2 ;;
    --skip-apt) SKIP_APT="1"; shift ;;
    --no-lcd-driver) shift ;;
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

if [[ "$OUTPUT" != "auto" && "$OUTPUT" != "desktop" && "$OUTPUT" != "sdl" && "$OUTPUT" != "fb" ]]; then
  echo "--output must be auto, desktop, sdl, or fb" >&2
  exit 2
fi

if [[ "$KIOSK" == "0" && "$OUTPUT" == "auto" ]]; then
  OUTPUT="desktop"
fi

TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6 || true)"
if [[ -z "$TARGET_HOME" ]]; then
  TARGET_HOME="/home/${TARGET_USER}"
fi

if [[ "$ADDR" != "auto" && "$ADDR" != "0x20" && "$ADDR" != "0x21" && "$ADDR" != "20" && "$ADDR" != "21" ]]; then
  echo "--addr must be auto, 0x20, or 0x21" >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive

append_once_to_cmdline() {
  local file="$1"
  local token="$2"
  [[ -f "$file" ]] || return 0
  if ! grep -qw -- "$token" "$file"; then
    cp "$file" "${file}.avcsi.bak"
    sed -i "1s/$/ ${token}/" "$file"
  fi
}

append_boot_config_once() {
  local file="$1"
  local line="$2"
  [[ -f "$file" ]] || return 0
  if ! grep -qxF "$line" "$file"; then
    printf '%s\n' "$line" >>"$file"
  fi
}

echo "[1/7] Installing packages"
if [[ "$SKIP_APT" == "1" ]]; then
  echo "Skipping apt package installation."
else
  apt-get update || true
  if command -v timeout >/dev/null 2>&1; then
    timeout 180 apt-get install -y \
      ca-certificates \
      curl \
      git \
      i2c-tools \
      python3 \
      python3-numpy \
      python3-opencv \
      python3-pygame \
      wmctrl \
      v4l-utils || echo "WARNING: apt install failed or timed out; continuing with existing packages."
  else
    apt-get install -y \
      ca-certificates \
      curl \
      git \
      i2c-tools \
      python3 \
      python3-numpy \
      python3-opencv \
      python3-pygame \
      wmctrl \
      v4l-utils || echo "WARNING: apt install failed; continuing with existing packages."
  fi
fi

echo "[2/7] Downloading AV-CSI tester files"
install -d -m 0755 "$INSTALL_DIR"
curl -fsSL "${REPO_RAW}/av_csi_tester.py" -o "${INSTALL_DIR}/av_csi_tester.py"
curl -fsSL "${REPO_RAW}/README.md" -o "${INSTALL_DIR}/README.md"
chmod 0755 "${INSTALL_DIR}/av_csi_tester.py"
chmod 0644 "${INSTALL_DIR}/README.md"

MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
echo "Detected board: ${MODEL:-unknown}"
if ! printf '%s' "$MODEL" | grep -qi 'Zero 2'; then
  echo "NOTE: this installer profile is tuned for Raspberry Pi Zero 2 W, but will continue."
fi

echo "[3/7] Writing runtime config"
cat >/etc/default/avcsi <<EOF
AVCSI_WIDTH=${WIDTH}
AVCSI_HEIGHT=${HEIGHT}
AVCSI_OUTPUT=${OUTPUT}
AVCSI_FB=${FB}
AVCSI_DEVICE=${DEVICE}
AVCSI_ROTATE=${ROTATE}
AVCSI_STANDARD=${STANDARD}
EOF

echo "[4/7] Installing systemd service"
curl -fsSL "${REPO_RAW}/avcsi.service" -o /etc/systemd/system/avcsi.service
chmod 0644 /etc/systemd/system/avcsi.service
systemctl daemon-reload
if [[ "$ENABLE_SERVICE" == "1" && "$KIOSK" == "1" ]]; then
  systemctl enable avcsi.service
else
  systemctl disable --now avcsi.service >/dev/null 2>&1 || true
fi

if [[ "$KIOSK" == "1" ]]; then
  echo "Configuring appliance boot: disabling desktop display manager."
  systemctl set-default multi-user.target || true
  for svc in display-manager.service lightdm.service gdm.service sddm.service wayfire.service labwc.service; do
    if systemctl list-unit-files "$svc" >/dev/null 2>&1; then
      systemctl disable --now "$svc" >/dev/null 2>&1 || true
    fi
  done
else
  echo "Configuring desktop autostart output."
  systemctl set-default graphical.target || true
  systemctl enable display-manager.service >/dev/null 2>&1 || true
  systemctl enable lightdm.service >/dev/null 2>&1 || true
  if command -v raspi-config >/dev/null 2>&1; then
    raspi-config nonint do_boot_behaviour B4 >/dev/null 2>&1 || true
  fi
  install -d -m 0755 /etc/xdg/autostart
  install -d -m 0755 "${TARGET_HOME}/.config/autostart"
  install -d -m 0755 "${TARGET_HOME}/.config/lxsession/LXDE-pi"
  install -d -m 0755 "${TARGET_HOME}/.config/lxsession/LXDE"
  install -d -m 0755 "${TARGET_HOME}/.config/labwc"
  install -d -m 0755 "${TARGET_HOME}/.config/systemd/user"
  cat >/usr/local/bin/avcsi-desktop-launcher <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec 9>/tmp/avcsi-desktop.lock
flock -n 9 || exit 0
pkill -u "$(id -un)" -f '/opt/avcsi/av_csi_tester.py' >/dev/null 2>&1 || true
sleep 1
for _ in $(seq 1 60); do
  if [[ -n "${DISPLAY:-}" ]] || [[ -S /tmp/.X11-unix/X0 ]]; then
    break
  fi
  sleep 1
done
setterm -blank 0 -powerdown 0 -powersave off >/dev/null 2>&1 || true
source /etc/default/avcsi 2>/dev/null || true
export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
rm -f /tmp/avcsi-desktop.log
/usr/bin/python3 /opt/avcsi/av_csi_tester.py \
  --width "${AVCSI_WIDTH:-1920}" \
  --height "${AVCSI_HEIGHT:-1080}" \
  --output "${AVCSI_OUTPUT:-desktop}" \
  --fb "${AVCSI_FB:-/dev/fb0}" \
  --device "${AVCSI_DEVICE:-auto}" \
  --rotate "${AVCSI_ROTATE:-0}" \
  --standard "${AVCSI_STANDARD:-auto}" >>/tmp/avcsi-desktop.log 2>&1 &
app_pid="$!"
for _ in $(seq 1 120); do
  wmctrl -r "AV-CSI Tester" -b add,fullscreen,above >/dev/null 2>&1 || true
  wmctrl -a "AV-CSI Tester" >/dev/null 2>&1 || true
  sleep 1
  kill -0 "$app_pid" 2>/dev/null || exit 0
done
wait "$app_pid"
EOF
  chmod 0755 /usr/local/bin/avcsi-desktop-launcher
  cat >/etc/systemd/system/avcsi-desktop.service <<EOF
[Unit]
Description=AV-CSI Tester desktop fullscreen launcher
After=graphical.target display-manager.service
Wants=graphical.target

[Service]
Type=simple
User=${TARGET_USER}
Group=${TARGET_USER}
Environment=HOME=${TARGET_HOME}
Environment=DISPLAY=:0
Environment=XAUTHORITY=${TARGET_HOME}/.Xauthority
ExecStart=/usr/local/bin/avcsi-desktop-launcher
Restart=always
RestartSec=3

[Install]
WantedBy=graphical.target
EOF
  systemctl daemon-reload
  systemctl disable --now avcsi-desktop.service >/dev/null 2>&1 || true
  cat >/etc/xdg/autostart/avcsi.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=AV-CSI Tester
Exec=sh -c 'sleep 8; pgrep -f av_csi_tester.py >/dev/null || /usr/local/bin/avcsi-desktop-launcher'
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
  cp /etc/xdg/autostart/avcsi.desktop "${TARGET_HOME}/.config/autostart/avcsi.desktop"
  cat >/etc/xdg/autostart/avcsi-raise.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=AV-CSI Tester Raise
Exec=sh -c 'sleep 8; for i in $(seq 1 20); do wmctrl -r "AV-CSI Tester" -b add,fullscreen,above; wmctrl -a "AV-CSI Tester"; sleep 2; done'
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
  cp /etc/xdg/autostart/avcsi-raise.desktop "${TARGET_HOME}/.config/autostart/avcsi-raise.desktop"
  cat >"${TARGET_HOME}/.config/lxsession/LXDE-pi/autostart" <<'EOF'
@/usr/local/bin/avcsi-desktop-launcher
EOF
  cat >"${TARGET_HOME}/.config/lxsession/LXDE/autostart" <<'EOF'
@/usr/local/bin/avcsi-desktop-launcher
EOF
  cat >"${TARGET_HOME}/.config/labwc/autostart" <<'EOF'
/usr/local/bin/avcsi-desktop-launcher &
EOF
  cat >"${TARGET_HOME}/.config/systemd/user/avcsi-desktop-user.service" <<'EOF'
[Unit]
Description=AV-CSI Tester user desktop launcher

[Service]
Type=simple
Environment=DISPLAY=:0
ExecStart=/usr/local/bin/avcsi-desktop-launcher
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
  chown -R "${TARGET_USER}:${TARGET_USER}" "${TARGET_HOME}/.config"
  sudo -u "$TARGET_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$TARGET_USER")" systemctl --user daemon-reload >/dev/null 2>&1 || true
  sudo -u "$TARGET_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$TARGET_USER")" systemctl --user enable avcsi-desktop-user.service >/dev/null 2>&1 || true
  rm -f /etc/xdg/autostart/avcsi.desktop.bak 2>/dev/null || true
fi

echo "[4/7] Ensuring ADV7282-M overlay"
detect_adv_addr() {
  if [[ "$ADDR" == "20" || "$ADDR" == "0x20" ]]; then
    echo "0x20"
    return
  fi
  if [[ "$ADDR" == "21" || "$ADDR" == "0x21" ]]; then
    echo "0x21"
    return
  fi
  local bus table
  for dev in /dev/i2c-*; do
    [[ -e "$dev" ]] || continue
    bus="${dev##*-}"
    table="$(i2cdetect -y "$bus" 2>/dev/null || true)"
    if printf '%s\n' "$table" | awk '/^20:/ {if ($2=="20" || $2=="UU") found=1} END {exit !found}'; then
      echo "0x20"
      return
    fi
    if printf '%s\n' "$table" | awk '/^20:/ {if ($3=="21" || $3=="UU") found=1} END {exit !found}'; then
      echo "0x21"
      return
    fi
  done
  echo "0x21"
}

ADV_ADDR="$(detect_adv_addr)"
BOOT_CONFIG=""
if [[ -f /boot/firmware/config.txt ]]; then
  BOOT_CONFIG="/boot/firmware/config.txt"
elif [[ -f /boot/config.txt ]]; then
  BOOT_CONFIG="/boot/config.txt"
fi
if [[ -n "$BOOT_CONFIG" ]]; then
  OVERLAY_LINE="dtoverlay=adv7282m,addr=${ADV_ADDR}"
  if grep -Eq '^[[:space:]]*dtoverlay=adv7282m' "$BOOT_CONFIG"; then
    cp "$BOOT_CONFIG" "${BOOT_CONFIG}.avcsi.bak"
    sed -i -E '/^[[:space:]]*dtoverlay=adv7282m/d' "$BOOT_CONFIG"
    printf '\n[all]\n# AV-CSI ADV7282-M\n%s\n' "$OVERLAY_LINE" >>"$BOOT_CONFIG"
    echo "Moved ADV7282-M overlay to [all] in $BOOT_CONFIG as ${OVERLAY_LINE}. Reboot is required."
  else
    cp "$BOOT_CONFIG" "${BOOT_CONFIG}.avcsi.bak"
    printf '\n[all]\n# AV-CSI ADV7282-M\n%s\n' "$OVERLAY_LINE" >>"$BOOT_CONFIG"
    echo "Added ${OVERLAY_LINE} to $BOOT_CONFIG. Reboot is required."
  fi
  append_boot_config_once "$BOOT_CONFIG" "disable_splash=1"
  if [[ "$FORCE_FULLHD" == "1" ]]; then
    append_boot_config_once "$BOOT_CONFIG" "hdmi_force_hotplug=1"
    append_boot_config_once "$BOOT_CONFIG" "hdmi_group=2"
    append_boot_config_once "$BOOT_CONFIG" "hdmi_mode=82"
    echo "FullHD HDMI firmware mode requested in $BOOT_CONFIG."
  fi
else
  echo "Boot config not found; cannot add dtoverlay=adv7282m automatically."
fi

CMDLINE_CONFIG=""
if [[ -f /boot/firmware/cmdline.txt ]]; then
  CMDLINE_CONFIG="/boot/firmware/cmdline.txt"
elif [[ -f /boot/cmdline.txt ]]; then
  CMDLINE_CONFIG="/boot/cmdline.txt"
fi
if [[ -n "$CMDLINE_CONFIG" ]]; then
  append_once_to_cmdline "$CMDLINE_CONFIG" "consoleblank=0"
  if [[ "$FORCE_FULLHD" == "1" ]]; then
    append_once_to_cmdline "$CMDLINE_CONFIG" "video=HDMI-A-1:1920x1080M@60D"
    echo "FullHD DRM/KMS mode requested in $CMDLINE_CONFIG."
  fi
fi

echo "[5/7] Checking devices"
v4l2-ctl --list-devices || true
i2cdetect -l || true
if ! i2cdetect -l | grep -Eq 'i2c-(10|0|22)[[:space:]]'; then
  echo "WARNING: camera-connector I2C bus is not visible."
  echo "The adv7282m overlay uses the camera connector I2C pins, not GPIO2/GPIO3 i2c-1."
fi
if [[ -e "$FB" && "$FB" != "auto" ]]; then
  echo "Framebuffer $FB found."
elif [[ -e /dev/fb0 ]]; then
  echo "Framebuffer /dev/fb0 found."
else
  echo "WARNING: HDMI framebuffer /dev/fb0 is not present yet."
fi

echo "[6/7] HDMI output selected"
echo "Built-in 3.5 inch LCD/LCD-show is not installed by this HDMI build."

echo "[7/7] Starting service"
if [[ "$START_SERVICE" == "1" && "$KIOSK" == "1" ]]; then
  systemctl restart avcsi.service || true
elif [[ "$START_SERVICE" == "1" ]]; then
  echo "Desktop autostart is installed. Reboot or log out/in to start fullscreen output inside the real desktop session."
fi

echo "Done."
echo "Status: sudo systemctl status avcsi.service"
