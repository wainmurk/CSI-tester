# AV-CSI Tester for ADV7282-M on Raspberry Pi Zero 2 W

Tester for an AV-to-CSI adapter based on ADV7282-M with final video output on HDMI 0.

This profile is tuned for Raspberry Pi Zero 2 W:

- HDMI output through `/dev/fb0`;
- SDL/KMSDRM HDMI output first, direct framebuffer fallback second;
- no 3.5" GPIO/SPI display and no LCD-show install;
- lower default render size, `720x576`, to keep CPU/framebuffer bandwidth reasonable on Zero 2 W;
- ADV7282-M via the Zero 2 W CSI camera connector.

## One Command Install

PAL:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --addr 0x21 --standard PAL
```

PAL with forced FullHD HDMI output:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --addr 0x21 --standard PAL --force-fullhd
```

NTSC:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --addr 0x21 --standard NTSC --height 480
```

After install, reboot:

```bash
sudo reboot
```

## Hardware Notes for Zero 2 W

Raspberry Pi Zero 2 W uses the smaller 22-pin camera connector. Use the correct Zero camera ribbon/adapter for the ADV7282-M CSI cable. A full-size 15-pin camera cable from Raspberry Pi 4 will not plug in directly without an adapter.

The stock `adv7282m` overlay uses the camera connector I2C bus and Unicam. It does not use GPIO2/GPIO3 `i2c-1`.

Expected good state after reboot:

```bash
ls /dev/i2c-*
v4l2-ctl --list-devices
dmesg | grep -Ei "adv|728|unicam|csi" | tail -80
```

You should see `adv7180 ... chip id ... found` and `unicam ... /dev/video0`.

## Options

Force address `0x20`:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --addr 0x20 --standard PAL
```

Use a specific framebuffer/output size:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --fb /dev/fb0 --width 720 --height 576
```

Force direct framebuffer output instead of SDL/KMSDRM:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --output fb
```

Rotate image:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --rotate 180
```

## Behavior

- `NO ADAPTER`: ADV7282-M is not bound by the kernel or `/dev/video0` is not available.
- `NO SIGNAL`: adapter is present but frames are not readable.
- Video present: fullscreen HDMI output with OSD.
- Hot swap: the service keeps running and repeatedly re-detects `/dev/video*`; signal loss/reconnect should recover without restarting the service once the kernel exposes the capture device again.
- `--force-fullhd`: requests HDMI 0 as `1920x1080@60`, disables console blanking, and starts the app on `/dev/tty1`.

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

The overlay must be under `[all]`, not under `[cm4]`, `[cm5]`, or another board-specific section.

## Diagnostics

```bash
grep -n "adv728" /boot/firmware/config.txt /boot/config.txt 2>/dev/null || true
ls /dev/i2c-*
i2cdetect -l
for b in /dev/i2c-*; do sudo i2cdetect -y "${b##*-}"; done
v4l2-ctl --list-devices
v4l2-ctl -d /dev/video0 -D
journalctl -u avcsi.service -n 80 --no-pager
```

If HDMI stays black on a FullHD monitor, reinstall with `--force-fullhd`, reboot, and check:

```bash
cat /sys/class/graphics/fb0/virtual_size
cat /sys/class/graphics/fb0/bits_per_pixel
sudo systemctl status avcsi.service
sudo journalctl -u avcsi.service -n 120 --no-pager
```

If the service is running and logs say `Using framebuffer /dev/fb0` but HDMI is still black, use the default SDL/KMSDRM backend:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --addr 0x21 --standard PAL --force-fullhd --output auto
sudo reboot
```
