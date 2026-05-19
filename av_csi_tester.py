#!/usr/bin/env python3
import argparse
import glob
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass

import cv2
import numpy as np

DEFAULT_W = 480
DEFAULT_H = 320
DEVICE_RECHECK_SEC = 2.0
V4L2_INFO_RECHECK_SEC = 5.0
ADV_I2C_ADDRS = {"20", "21"}
RUNNING = True


@dataclass
class VideoDevice:
    path: str
    name: str


@dataclass
class Framebuffer:
    path: str
    width: int
    height: int
    bpp: int
    stride: int


def stop(_signum, _frame):
    global RUNNING
    RUNNING = False


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def run_text(args, timeout=1.5):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""


def list_video_devices():
    devices = []
    for path in sorted(glob.glob("/dev/video*")):
        base = os.path.basename(path)
        name = read_text(f"/sys/class/video4linux/{base}/name") or base
        devices.append(VideoDevice(path, name))
    return devices


def device_score(device):
    name = device.name.lower()
    score = 0
    for token in ("adv728", "adv718", "adv", "unicam", "csi", "capture"):
        if token in name:
            score += 20
    for token in ("codec", "isp", "h264", "jpeg", "stateless", "mem2mem"):
        if token in name:
            score -= 50
    return score


def can_open(path):
    cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
    opened = cap.isOpened()
    cap.release()
    return opened


def adv_i2c_matches():
    matches = []
    for path in glob.glob("/sys/bus/i2c/devices/*/name"):
        name = read_text(path).lower()
        if "adv728" in name or "adv718" in name or "adv" in name:
            matches.append(f"{os.path.basename(os.path.dirname(path))}:{name}")
    if matches:
        return matches

    for bus_path in sorted(glob.glob("/dev/i2c-*")):
        bus = os.path.basename(bus_path).split("-")[-1]
        if not bus.isdigit():
            continue
        table = run_text(["i2cdetect", "-y", bus], timeout=2.0).lower()
        for line in table.splitlines():
            parts = line.split()
            if not parts or not parts[0].endswith(":"):
                continue
            try:
                row = int(parts[0].strip(":"), 16)
            except ValueError:
                continue
            for cell_index, cell in enumerate(parts[1:]):
                if cell == "--":
                    continue
                addr = f"{row + cell_index:02x}"
                if addr in ADV_I2C_ADDRS and (cell == "uu" or cell == addr):
                    matches.append(f"i2c-{bus}:0x{addr}")
    return matches


def select_device(requested):
    if requested != "auto":
        base = os.path.basename(requested)
        return VideoDevice(requested, read_text(f"/sys/class/video4linux/{base}/name") or requested)
    devices = list_video_devices()
    devices.sort(key=device_score, reverse=True)
    for device in devices:
        if device_score(device) >= -10:
            return device
    return None


def parse_v4l2_info(device):
    if device is None:
        return {}
    text = run_text(["v4l2-ctl", "-d", device.path, "--all"], timeout=2.0)
    info = {"device": f"{device.path} {device.name}".strip()}
    patterns = {
        "input": r"Width/Height\s*:\s*(\d+)/(\d+)",
        "fmt": r"Pixel Format\s*:\s*'([^']+)'",
        "field": r"Field\s*:\s*([^\n]+)",
        "std": r"Video Standard\s*=\s*([^\n]+)",
        "status": r"Input Status\s*:\s*([^\n]+)",
    }
    width = re.search(patterns["input"], text)
    if width:
        info["input"] = f"{width.group(1)}x{width.group(2)}"
    for key in ("fmt", "field", "std", "status"):
        match = re.search(patterns[key], text)
        if match:
            info[key] = match.group(1).strip()
    return info


def choose_framebuffer(requested):
    if requested != "auto":
        return requested
    return "/dev/fb1" if os.path.exists("/dev/fb1") else "/dev/fb0"


def parse_pair(text, default_a, default_b):
    parts = [p.strip() for p in text.split(",")]
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return int(parts[0]), int(parts[1])
    return default_a, default_b


def open_framebuffer(path, width, height):
    name = os.path.basename(path)
    sys_dir = f"/sys/class/graphics/{name}"
    fb_width, fb_height = parse_pair(read_text(f"{sys_dir}/virtual_size"), width, height)
    bpp_text = read_text(f"{sys_dir}/bits_per_pixel")
    stride_text = read_text(f"{sys_dir}/stride")
    bpp = int(bpp_text) if bpp_text.isdigit() else 16
    stride = int(stride_text) if stride_text.isdigit() else fb_width * max(1, bpp // 8)
    return Framebuffer(path, fb_width, fb_height, bpp, stride), open(path, "r+b", buffering=0)


def draw_center(width, height, title, subtitle, color):
    image = np.zeros((height, width, 3), dtype=np.uint8)
    scale = 1.7 if width >= 460 else 1.15
    thickness = 3
    size, baseline = cv2.getTextSize(title, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    x = max(4, (width - size[0]) // 2)
    y = max(size[1] + baseline + 4, (height // 2) - 8)
    cv2.putText(image, title, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)
    if subtitle:
        sub_size, _ = cv2.getTextSize(subtitle, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)
        sx = max(4, (width - sub_size[0]) // 2)
        cv2.putText(image, subtitle[:76], (sx, height - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (205, 205, 205), 1, cv2.LINE_AA)
    return image


def quality_metrics(prev_gray, bgr, fps):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    mean = float(np.mean(gray))
    contrast = float(np.std(gray))
    saturation = float(np.mean(hsv[:, :, 1]))
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    motion = 0.0 if prev_gray is None else float(np.mean(np.abs(gray.astype(np.float32) - prev_gray.astype(np.float32))))
    exposure_score = max(0.0, 100.0 - abs(mean - 118.0) * 1.2)
    quality = 0.35 * min(100.0, contrast * 3.0) + 0.25 * min(100.0, sharpness / 8.0) + 0.25 * exposure_score + 0.15 * min(100.0, fps * 4.0)
    if mean < 8.0 or mean > 247.0 or contrast < 3.0:
        quality = min(quality, 20.0)
    return {
        "quality": max(0.0, min(100.0, quality)),
        "brightness": mean,
        "contrast": contrast,
        "sharpness": sharpness,
        "saturation": saturation,
        "motion": motion,
    }, gray


def draw_osd(image, lines):
    width = image.shape[1]
    line_h = 18
    osd_h = min(image.shape[0], 9 + line_h * len(lines))
    overlay = image.copy()
    cv2.rectangle(overlay, (0, 0), (width, osd_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.58, image, 0.42, 0, image)
    for i, line in enumerate(lines):
        cv2.putText(image, line[:68], (8, 16 + i * line_h), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (245, 245, 245), 1, cv2.LINE_AA)


def resize_to_fb(image, fb):
    if image.shape[1] == fb.width and image.shape[0] == fb.height:
        return image
    return cv2.resize(image, (fb.width, fb.height), interpolation=cv2.INTER_AREA)


def bgr_to_fb_bytes(image, fb):
    image = resize_to_fb(image, fb)
    if fb.bpp == 16:
        b = image[:, :, 0].astype(np.uint16)
        g = image[:, :, 1].astype(np.uint16)
        r = image[:, :, 2].astype(np.uint16)
        row_bytes = (((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)).astype("<u2").tobytes()
        expected_stride = fb.width * 2
    elif fb.bpp == 24:
        row_bytes = image.tobytes()
        expected_stride = fb.width * 3
    elif fb.bpp == 32:
        alpha = np.full((fb.height, fb.width, 1), 255, dtype=np.uint8)
        row_bytes = np.concatenate((image, alpha), axis=2).tobytes()
        expected_stride = fb.width * 4
    else:
        raise RuntimeError(f"Unsupported framebuffer depth: {fb.bpp} bpp")
    if fb.stride == expected_stride:
        return row_bytes
    padded = bytearray(fb.stride * fb.height)
    for y in range(fb.height):
        src_start = y * expected_stride
        src_end = src_start + min(expected_stride, fb.stride)
        dst_start = y * fb.stride
        padded[dst_start:dst_start + (src_end - src_start)] = row_bytes[src_start:src_end]
    return bytes(padded)


def show_frame(fb, fb_file, image):
    fb_file.seek(0)
    fb_file.write(bgr_to_fb_bytes(image, fb))


def main():
    parser = argparse.ArgumentParser(description="ADV7282-M AV-CSI tester for Raspberry Pi display")
    parser.add_argument("--width", type=int, default=DEFAULT_W)
    parser.add_argument("--height", type=int, default=DEFAULT_H)
    parser.add_argument("--fb", default="auto")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--rotate", choices=("0", "90", "180", "270"), default="0")
    args = parser.parse_args()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    fb_path = choose_framebuffer(args.fb)
    fb_info, fb_file = open_framebuffer(fb_path, args.width, args.height)
    width, height = args.width, args.height
    cap = None
    device = None
    prev_gray = None
    v4l2_info = {}
    last_device_check = 0.0
    last_info_check = 0.0
    last_frame_time = time.time()
    fps = 0.0

    while RUNNING:
        now = time.time()
        if cap is None and now - last_device_check >= DEVICE_RECHECK_SEC:
            last_device_check = now
            device = select_device(args.device)
            if device is not None and can_open(device.path):
                cap = cv2.VideoCapture(device.path, cv2.CAP_V4L2)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                prev_gray = None
                v4l2_info = parse_v4l2_info(device)

        if cap is None:
            i2c_matches = adv_i2c_matches()
            if i2c_matches:
                image = draw_center(width, height, "NO SIGNAL", f"I2C {', '.join(i2c_matches[:2])}, no V4L2 frames", (30, 180, 245))
            elif device is not None:
                image = draw_center(width, height, "NO SIGNAL", f"{device.path} {device.name}, cannot open", (30, 180, 245))
            else:
                image = draw_center(width, height, "NO ADAPTER", "ADV7282-M not found on V4L2/I2C", (35, 35, 235))
            show_frame(fb_info, fb_file, image)
            time.sleep(0.2)
            continue

        if now - last_info_check >= V4L2_INFO_RECHECK_SEC:
            last_info_check = now
            v4l2_info = parse_v4l2_info(device)

        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            cap = None
            image = draw_center(width, height, "NO SIGNAL", "Adapter is present, waiting for video", (30, 180, 245))
            show_frame(fb_info, fb_file, image)
            time.sleep(0.2)
            continue

        current_time = time.time()
        dt = max(0.001, current_time - last_frame_time)
        instant_fps = 1.0 / dt
        fps = instant_fps if fps <= 0.1 else (0.85 * fps + 0.15 * instant_fps)
        last_frame_time = current_time

        image = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        if args.rotate != "0":
            rotations = {"90": cv2.ROTATE_90_CLOCKWISE, "180": cv2.ROTATE_180, "270": cv2.ROTATE_90_COUNTERCLOCKWISE}
            image = cv2.rotate(image, rotations[args.rotate])
            image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)

        metrics, prev_gray = quality_metrics(prev_gray, image, fps)
        input_res = v4l2_info.get("input", f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}")
        draw_osd(image, [
            f"ADV7282-M tester | {device.path if device else 'auto'} | {v4l2_info.get('status', 'OK')}",
            f"FPS {fps:4.1f} | input {input_res} | fmt {v4l2_info.get('fmt', 'n/a')} | fb {fb_info.path}",
            f"quality {metrics['quality']:3.0f}% | bright {metrics['brightness']:3.0f} | contrast {metrics['contrast']:3.0f} | sharp {metrics['sharpness']:5.0f}",
            f"motion {metrics['motion']:4.1f} | saturation {metrics['saturation']:3.0f} | std {v4l2_info.get('std', 'n/a')}",
        ])
        show_frame(fb_info, fb_file, image)

    if cap is not None:
        cap.release()
    fb_file.close()


if __name__ == "__main__":
    main()
