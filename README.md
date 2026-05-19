# AV-CSI Tester for ADV7282-M on Raspberry Pi 4

Tester for an AV-to-CSI adapter based on ADV7282-M with output to the LCD wiki 3.5" RPi Display.

Display guide: https://www.lcdwiki.com/3.5inch_RPi_Display

## One Command Install

Run this on the Raspberry Pi:

```bash
curl -fsSL https://raw.githubusercontent.com/wainmurk/CSI-tester/main/installer.sh | sudo bash
```

The installer downloads everything it needs and installs it automatically:

- `python3`, `python3-opencv`, `python3-numpy`, `v4l-utils`, `git`, `curl`;
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

## Behavior

- If the ADV7282-M / V4L2 capture device is not found, the display shows `NO ADAPTER`.
- If the adapter is found but no frames are readable, the display shows `NO SIGNAL`.
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

## OSD Data

Without a custom low-level ADV7282-M register reader, the reliable data comes from V4L2 and frame analysis:

- capture device presence;
- frame read status;
- FPS;
- input resolution and pixel format from `v4l2-ctl --all`;
- V4L2 input status if the driver exposes it;
- brightness, contrast, saturation, sharpness, and motion estimated from the frame;
- approximate signal quality percentage.
