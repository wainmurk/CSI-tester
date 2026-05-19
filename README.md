# AV-CSI Tester for ADV7282-M on Raspberry Pi 4

Tester for an AV-to-CSI adapter based on ADV7282-M with output to the LCD wiki 3.5" RPi Display.

Display guide: https://www.lcdwiki.com/3.5inch_RPi_Display

## One Command Install

Run this on the Raspberry Pi:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash
```

The installer downloads everything it needs and installs it automatically:

- `python3`, `python3-opencv`, `python3-numpy`, `v4l-utils`, `i2c-tools`, `git`, `curl`;
- `/opt/avcsi/av_csi_tester.py`;
- `/etc/systemd/system/avcsi.service`;
- `/etc/default/avcsi`;
- LCD-show driver for the 3.5" RPi Display.

Important: `LCD35-show` can change boot config and reboot the Raspberry Pi. After reboot, `avcsi.service` starts automatically.

## Options

Skip LCD driver install:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --no-lcd-driver
```

Use a specific V4L2 device:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --device /dev/video0
```

Rotate image:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --rotate 90
```

Use a specific framebuffer:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --fb /dev/fb1
```

Force PAL or NTSC if auto-detection does not open the capture device:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --standard PAL
```

Force ADV7282-M I2C address if the overlay probes the wrong address:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash -s -- --no-lcd-driver --addr 0x20 --standard PAL
```

## Behavior

- If the ADV7282-M is not visible on I2C and no usable capture device is found, the display shows `NO ADAPTER`.
- If the adapter is visible on I2C but V4L2 is not ready or frames are not readable, the display shows `NO SIGNAL`.
- If video is present, the image is shown fullscreen.
- OSD shows FPS, video device, input resolution, pixel format, V4L2 status, approximate signal quality, brightness, contrast, sharpness, saturation, and motion.

## Service

```bash
sudo systemctl start avcsi.service
sudo systemctl status avcsi.service
sudo journalctl -u avcsi.service -f
```

Manual run:

```bash
sudo python3 /opt/avcsi/av_csi_tester.py --width 480 --height 320 --fb /dev/fb1 --device auto
```

Runtime config:

```bash
sudo nano /etc/default/avcsi
sudo systemctl restart avcsi.service
```

## Diagnostics

If the display says `NO SIGNAL ... cannot open` or goes back to `NO ADAPTER`, reboot first. The installer adds this boot overlay:

```text
dtoverlay=adv7282m,addr=0x21
```

Some boards use `addr=0x20`, while the Raspberry Pi overlay default is `0x21`. The installer tries to detect this, but you can force it with `--addr 0x20` or `--addr 0x21`.

The overlay only takes effect after reboot. If it still fails, run:

```bash
grep -n "adv728" /boot/firmware/config.txt /boot/config.txt 2>/dev/null || true
v4l2-ctl --list-devices
v4l2-ctl -d /dev/video0 -D
v4l2-ctl -d /dev/video0 --get-detected-standard
v4l2-ctl -d /dev/video0 --set-standard PAL
v4l2-ctl -d /dev/video0 --stream-mmap --stream-count=5 --stream-to=/tmp/adv.raw
i2cdetect -l
for b in /dev/i2c-*; do sudo i2cdetect -y "${b##*-}"; done
journalctl -u avcsi.service -n 80 --no-pager
```

## OSD Data

Without a custom low-level ADV7282-M register reader, the reliable data comes from V4L2 and frame analysis:

- capture device presence;
- frame read status;
- FPS;
- input resolution and pixel format from `v4l2-ctl --all`;
- V4L2 input status if the driver exposes it;
- ADV728x presence on I2C sysfs or common ADV728x I2C addresses (`0x20`, `0x21`);
- brightness, contrast, saturation, sharpness, and motion estimated from the frame;
- approximate signal quality percentage.
