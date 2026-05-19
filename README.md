# AV-CSI Tester for ADV7282-M on Raspberry Pi 4

Tester for an AV-to-CSI adapter based on ADV7282-M with final video output on HDMI 0.

## One Command Install

Run this on the Raspberry Pi:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --addr 0x21 --standard PAL
```

This HDMI build does not install LCD-show and does not use the 3.5" GPIO/SPI display. Output goes to the HDMI framebuffer, normally `/dev/fb0`.

## Options

Force NTSC:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --addr 0x21 --standard NTSC
```

Use a specific V4L2 device:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --device /dev/video0
```

Use a specific HDMI framebuffer or output size:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --fb /dev/fb0 --width 1920 --height 1080
```

Force ADV7282-M I2C address if needed:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --addr 0x20 --standard PAL
```

## Behavior

- `NO ADAPTER`: ADV7282-M is not bound by the kernel or `/dev/video0` is not available.
- `NO SIGNAL`: adapter is present but frames are not readable.
- Video present: fullscreen HDMI output with OSD.
- Hot swap: the service keeps running and repeatedly re-detects `/dev/video*`; unplug/replug or signal loss should recover without restarting the service once the kernel exposes the capture device again.

## Service

```bash
sudo systemctl restart avcsi.service
sudo systemctl status avcsi.service
sudo journalctl -u avcsi.service -f
```

Runtime config:

```bash
sudo nano /etc/default/avcsi
sudo systemctl restart avcsi.service
```

## Required Kernel Overlay

The installer places the ADV7282-M overlay under `[all]`:

```text
[all]
dtoverlay=adv7282m,addr=0x21
```

The overlay must not be under `[cm4]`, `[cm5]`, or another board-specific section for Raspberry Pi 4.

## Diagnostics

Expected good state:

```bash
dmesg | grep -Ei "adv|728|unicam|csi" | tail -80
v4l2-ctl --list-devices
```

You should see `adv7180 ... chip id ... found` and `unicam ... /dev/video0`.

If not:

```bash
grep -n "adv728" /boot/firmware/config.txt /boot/config.txt 2>/dev/null || true
ls /dev/i2c-*
i2cdetect -l
for b in /dev/i2c-*; do sudo i2cdetect -y "${b##*-}"; done
journalctl -u avcsi.service -n 80 --no-pager
```
