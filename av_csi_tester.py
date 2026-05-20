#!/usr/bin/env python3
import argparse
import glob
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Tuple

import cv2
import numpy as np


DEFAULT_W = 720
DEFAULT_H = 576
DEVICE_RECHECK_SEC = 2.0
V4L2_INFO_RECHECK_SEC = 5.0
FB_OPEN_RETRY_SEC = 30.0
ADV_I2C_ADDRS = {"20", "21"}
STANDARDS = ("PAL", "NTSC", "SECAM")
READ_FAIL_LIMIT = 8
READ_WAIT_NOTICE_SEC = 1.0

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


class Display(Protocol):
    width: int
    height: int

    def show(self, image: np.ndarray):
        ...

    def close(self):
        ...


class FramebufferDisplay:
    def __init__(self, path: str, width: int, height: int):
        self.fb, self.fb_file = open_framebuffer(path, width, height)
        self.width = width
        self.height = height

    def show(self, image: np.ndarray):
        self.fb_file.seek(0)
        self.fb_file.write(bgr_to_fb_bytes(image, self.fb))

    def close(self):
        self.fb_file.close()


class SdlDisplay:
    def __init__(self, width: int, height: int, driver: str = "kmsdrm"):
        if driver:
            os.environ.setdefault("SDL_VIDEODRIVER", driver)
        os.environ.setdefault("SDL_NOMOUSE", "1")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        import pygame

        self.pygame = pygame
        pygame.init()
        pygame.mouse.set_visible(False)
        pygame.display.set_caption("AV-CSI Tester")
        info = pygame.display.Info()
        self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        self.width = self.screen.get_width() or info.current_w or width
        self.height = self.screen.get_height() or info.current_h or height
        self.raise_window()
        print(f"Using SDL display: {self.width}x{self.height}, driver {pygame.display.get_driver()}", flush=True)

    def raise_window(self):
        for _ in range(10):
            run_text(["wmctrl", "-r", "AV-CSI Tester", "-b", "add,fullscreen,above"], timeout=0.5)
            run_text(["wmctrl", "-a", "AV-CSI Tester"], timeout=0.5)
            time.sleep(0.2)

    def show(self, image: np.ndarray):
        if image.shape[1] != self.width or image.shape[0] != self.height:
            image = cv2.resize(image, (self.width, self.height), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        surface = self.pygame.image.frombuffer(rgb.tobytes(), (self.width, self.height), "RGB")
        self.screen.blit(surface, (0, 0))
        self.pygame.display.flip()
        self.pygame.event.pump()

    def close(self):
        self.pygame.quit()


def stop(_signum, _frame):
    global RUNNING
    RUNNING = False


def read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def run_text(args: List[str], timeout: float = 1.5) -> str:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""


def list_video_devices() -> List[VideoDevice]:
    devices = []
    for path in sorted(glob.glob("/dev/video*")):
        base = os.path.basename(path)
        name = read_text(f"/sys/class/video4linux/{base}/name")
        devices.append(VideoDevice(path=path, name=name or base))
    return devices


def device_score(device: VideoDevice) -> int:
    name = device.name.lower()
    score = 0
    for token in ("adv728", "adv718", "adv", "unicam", "csi", "capture"):
        if token in name:
            score += 20
    for token in ("codec", "isp", "h264", "jpeg", "stateless", "mem2mem"):
        if token in name:
            score -= 50
    return score


def device_caps(path: str) -> str:
    return run_text(["v4l2-ctl", "-d", path, "-D"], timeout=1.5)


def is_capture_device(path: str) -> bool:
    caps = device_caps(path).lower()
    if not caps:
        return True
    if "video capture" not in caps:
        return False
    if "meta capture" in caps and "video capture" not in caps.replace("meta capture", ""):
        return False
    return True


def can_open(path: str) -> bool:
    cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
    opened = cap.isOpened()
    cap.release()
    return opened


def adv_i2c_matches() -> List[str]:
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
            row = parts[0].strip(":")
            for cell_index, cell in enumerate(parts[1:]):
                if cell in ("--",):
                    continue
                try:
                    addr = f"{int(row, 16) + cell_index:02x}"
                except ValueError:
                    continue
                if addr in ADV_I2C_ADDRS and (cell == "uu" or cell == addr):
                    matches.append(f"i2c-{bus}:0x{addr}")

    return matches


def i2c_has_adv7282() -> bool:
    return bool(adv_i2c_matches())


def select_device(requested: str) -> Optional[VideoDevice]:
    if requested != "auto":
        return VideoDevice(path=requested, name=read_text(f"/sys/class/video4linux/{os.path.basename(requested)}/name") or requested)

    devices = list_video_devices()
    devices.sort(key=device_score, reverse=True)
    for device in devices:
        if device_score(device) < -10:
            continue
        if not is_capture_device(device.path):
            continue
        return device
    return None


def detected_standard(device: Optional[VideoDevice]) -> str:
    if device is None:
        return ""
    text = run_text(["v4l2-ctl", "-d", device.path, "--get-detected-standard"], timeout=1.5)
    for standard in STANDARDS:
        if standard.lower() in text.lower():
            return standard
    return ""


def configure_standard(device: Optional[VideoDevice], requested: str) -> str:
    if device is None:
        return ""

    candidates = []
    if requested != "auto":
        candidates.append(requested.upper())
    detected = detected_standard(device)
    if detected:
        candidates.append(detected)
    candidates.extend(STANDARDS)

    tried = set()
    for standard in candidates:
        if standard in tried:
            continue
        tried.add(standard)
        run_text(["v4l2-ctl", "-d", device.path, "--set-standard", standard], timeout=1.5)
        if standard == "PAL":
            run_text(["v4l2-ctl", "-d", device.path, "--set-fmt-video=width=720,height=576,pixelformat=UYVY"], timeout=1.5)
        elif standard == "NTSC":
            run_text(["v4l2-ctl", "-d", device.path, "--set-fmt-video=width=720,height=480,pixelformat=UYVY"], timeout=1.5)
        if can_open(device.path):
            return standard
    return ""


def parse_v4l2_info(device: Optional[VideoDevice]) -> Dict[str, str]:
    if device is None:
        return {}

    text = run_text(["v4l2-ctl", "-d", device.path, "--all"], timeout=2.0)
    info: Dict[str, str] = {
        "device": f"{device.path} {device.name}".strip(),
    }

    width = re.search(r"Width/Height\s*:\s*(\d+)/(\d+)", text)
    pixfmt = re.search(r"Pixel Format\s*:\s*'([^']+)'", text)
    field = re.search(r"Field\s*:\s*([^\n]+)", text)
    standard = re.search(r"Video Standard\s*=\s*([^\n]+)", text)
    input_status = re.search(r"Input Status\s*:\s*([^\n]+)", text)

    if width:
        info["input"] = f"{width.group(1)}x{width.group(2)}"
    if pixfmt:
        info["fmt"] = pixfmt.group(1)
    if field:
        info["field"] = field.group(1).strip()
    if standard:
        info["std"] = standard.group(1).strip()
    if input_status:
        info["status"] = input_status.group(1).strip()

    return info


def choose_framebuffer(requested: str) -> str:
    if requested != "auto":
        return requested
    return "/dev/fb0"


def fb_sys_name(path: str) -> str:
    return os.path.basename(path)


def parse_pair(text: str, default_a: int, default_b: int) -> Tuple[int, int]:
    parts = [p.strip() for p in text.split(",")]
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return int(parts[0]), int(parts[1])
    return default_a, default_b


def open_framebuffer(path: str, width: int, height: int) -> Tuple[Framebuffer, object]:
    deadline = time.time() + FB_OPEN_RETRY_SEC
    last_error = ""
    while time.time() < deadline:
        try:
            return open_framebuffer_once(path, width, height)
        except OSError as exc:
            last_error = str(exc)
            time.sleep(0.5)
    raise RuntimeError(f"Cannot open framebuffer {path}: {last_error}")


def open_framebuffer_once(path: str, width: int, height: int) -> Tuple[Framebuffer, object]:
    name = fb_sys_name(path)
    sys_dir = f"/sys/class/graphics/{name}"
    fb_width, fb_height = parse_pair(read_text(f"{sys_dir}/virtual_size"), width, height)
    bpp_text = read_text(f"{sys_dir}/bits_per_pixel")
    stride_text = read_text(f"{sys_dir}/stride")
    bpp = int(bpp_text) if bpp_text.isdigit() else 16
    stride = int(stride_text) if stride_text.isdigit() else fb_width * max(1, bpp // 8)
    fh = open(path, "r+b", buffering=0)
    print(f"Using framebuffer {path}: {fb_width}x{fb_height}, {bpp} bpp, stride {stride}", flush=True)
    return Framebuffer(path=path, width=fb_width, height=fb_height, bpp=bpp, stride=stride), fh


def text_size(text: str, scale: float, thickness: int) -> Tuple[int, int]:
    size, baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    return size[0], size[1] + baseline


def draw_center(width: int, height: int, title: str, subtitle: str, color: Tuple[int, int, int]) -> np.ndarray:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    scale = 1.7 if width >= 460 else 1.15
    thickness = 3
    tw, th = text_size(title, scale, thickness)
    x = max(4, (width - tw) // 2)
    y = max(th + 4, (height // 2) - 8)
    cv2.putText(image, title, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)
    if subtitle:
        sw, _sh = text_size(subtitle, 0.48, 1)
        sx = max(4, (width - sw) // 2)
        cv2.putText(image, subtitle, (sx, height - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (205, 205, 205), 1, cv2.LINE_AA)
    return image


def quality_metrics(prev_gray: Optional[np.ndarray], rgb: np.ndarray, fps: float) -> Tuple[Dict[str, float], Optional[np.ndarray]]:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    mean = float(np.mean(gray))
    contrast = float(np.std(gray))
    saturation = float(np.mean(hsv[:, :, 1]))
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    motion = 0.0
    if prev_gray is not None:
        motion = float(np.mean(np.abs(gray.astype(np.float32) - prev_gray.astype(np.float32))))

    exposure_score = max(0.0, 100.0 - abs(mean - 118.0) * 1.2)
    contrast_score = min(100.0, contrast * 3.0)
    sharp_score = min(100.0, sharpness / 8.0)
    fps_score = min(100.0, fps * 4.0)
    quality = 0.35 * contrast_score + 0.25 * sharp_score + 0.25 * exposure_score + 0.15 * fps_score
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


def draw_osd(image: np.ndarray, lines: List[str]):
    width = image.shape[1]
    line_h = 18
    osd_h = min(image.shape[0], 9 + line_h * len(lines))
    overlay = image.copy()
    cv2.rectangle(overlay, (0, 0), (width, osd_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.58, image, 0.42, 0, image)
    for i, line in enumerate(lines):
        cv2.putText(image, line[:68], (8, 16 + i * line_h), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (245, 245, 245), 1, cv2.LINE_AA)


def resize_to_fb(image: np.ndarray, fb: Framebuffer) -> np.ndarray:
    if image.shape[1] == fb.width and image.shape[0] == fb.height:
        return image
    return cv2.resize(image, (fb.width, fb.height), interpolation=cv2.INTER_AREA)


def bgr_to_fb_bytes(image: np.ndarray, fb: Framebuffer) -> bytes:
    image = resize_to_fb(image, fb)
    if fb.bpp == 16:
        b = image[:, :, 0].astype(np.uint16)
        g = image[:, :, 1].astype(np.uint16)
        r = image[:, :, 2].astype(np.uint16)
        rgb565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        row_bytes = rgb565.astype("<u2").tobytes()
        expected_stride = fb.width * 2
    elif fb.bpp == 24:
        row_bytes = image.tobytes()
        expected_stride = fb.width * 3
    elif fb.bpp == 32:
        alpha = np.full((fb.height, fb.width, 1), 255, dtype=np.uint8)
        bgra = np.concatenate((image, alpha), axis=2)
        row_bytes = bgra.tobytes()
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


def show_frame(fb: Framebuffer, fb_file, image: np.ndarray):
    fb_file.seek(0)
    fb_file.write(bgr_to_fb_bytes(image, fb))


def open_display(output: str, fb_path: str, width: int, height: int) -> Display:
    if output == "desktop":
        return SdlDisplay(width, height, driver="")
    if output in ("auto", "sdl"):
        try:
            return SdlDisplay(width, height)
        except Exception as exc:
            if output == "sdl":
                raise
            print(f"SDL display unavailable, falling back to framebuffer: {exc}", flush=True)
    return FramebufferDisplay(fb_path, width, height)


def main():
    parser = argparse.ArgumentParser(description="ADV7282-M AV-CSI tester for Raspberry Pi display")
    parser.add_argument("--width", type=int, default=DEFAULT_W)
    parser.add_argument("--height", type=int, default=DEFAULT_H)
    parser.add_argument("--output", choices=("auto", "sdl", "desktop", "fb"), default="auto", help="HDMI output backend")
    parser.add_argument("--fb", default="auto", help="Framebuffer path, HDMI console is usually /dev/fb0")
    parser.add_argument("--device", default="auto", help="V4L2 device path or auto")
    parser.add_argument("--rotate", choices=("0", "90", "180", "270"), default="0")
    parser.add_argument("--standard", choices=("auto", "PAL", "NTSC", "SECAM", "pal", "ntsc", "secam"), default="auto")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    fb = choose_framebuffer(args.fb)
    display = open_display(args.output, fb, args.width, args.height)
    width = display.width
    height = display.height
    display.show(draw_center(width, height, "STARTING", "Initializing HDMI output and CSI capture", (245, 245, 245)))

    cap = None
    device: Optional[VideoDevice] = None
    prev_gray: Optional[np.ndarray] = None
    v4l2_info: Dict[str, str] = {}
    last_device_check = 0.0
    last_info_check = 0.0
    last_frame_time = time.time()
    fps = 0.0
    read_failures = 0
    last_wait_notice = 0.0

    while RUNNING:
        now = time.time()
        if cap is not None and device is not None and now - last_device_check >= DEVICE_RECHECK_SEC:
            last_device_check = now
            if not os.path.exists(device.path):
                cap.release()
                cap = None
                device = None
                prev_gray = None
                v4l2_info = {}

        if cap is None and now - last_device_check >= DEVICE_RECHECK_SEC:
            last_device_check = now
            device = select_device(args.device)
            active_standard = configure_standard(device, args.standard)
            if device is not None and active_standard:
                cap = cv2.VideoCapture(device.path, cv2.CAP_V4L2)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                prev_gray = None
                read_failures = 0
                v4l2_info = parse_v4l2_info(device)
                v4l2_info["std"] = active_standard

        if cap is None:
            i2c_matches = adv_i2c_matches()
            if i2c_matches:
                detail = f"I2C {', '.join(i2c_matches[:2])}, no V4L2 frames"
                image = draw_center(width, height, "NO SIGNAL", detail, (30, 180, 245))
            elif device is not None:
                detail = f"{device.path} {device.name}, cannot open"
                image = draw_center(width, height, "NO SIGNAL", detail, (30, 180, 245))
            else:
                detail = "ADV7282-M not found on V4L2/I2C"
                image = draw_center(width, height, "NO ADAPTER", detail, (35, 35, 235))
            display.show(image)
            time.sleep(0.2)
            continue

        if now - last_info_check >= V4L2_INFO_RECHECK_SEC:
            last_info_check = now
            v4l2_info = parse_v4l2_info(device)

        if now - last_wait_notice >= READ_WAIT_NOTICE_SEC:
            last_wait_notice = now
            image = draw_center(width, height, "WAITING FRAME", f"{device.path if device else 'auto'} read pending", (30, 180, 245))
            draw_osd(image, [
                f"ADV7282-M tester | {device.path if device else 'auto'}",
                f"input {v4l2_info.get('input', 'n/a')} | fmt {v4l2_info.get('fmt', 'n/a')} | std {v4l2_info.get('std', 'n/a')}",
                "If this stays here, V4L2 is blocking while waiting for analog video.",
            ])
            display.show(image)

        ok, frame = cap.read()
        if not ok or frame is None:
            read_failures += 1
            image = draw_center(width, height, "NO SIGNAL", "Adapter is present, waiting for video", (30, 180, 245))
            display.show(image)
            if read_failures >= READ_FAIL_LIMIT:
                cap.release()
                cap = None
                prev_gray = None
                v4l2_info = {}
            time.sleep(0.2)
            continue
        read_failures = 0

        current_time = time.time()
        dt = max(0.001, current_time - last_frame_time)
        instant_fps = 1.0 / dt
        fps = instant_fps if fps <= 0.1 else (0.85 * fps + 0.15 * instant_fps)
        last_frame_time = current_time

        image = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        if args.rotate != "0":
            rotations = {
                "90": cv2.ROTATE_90_CLOCKWISE,
                "180": cv2.ROTATE_180,
                "270": cv2.ROTATE_90_COUNTERCLOCKWISE,
            }
            image = cv2.rotate(image, rotations[args.rotate])
            image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)

        rgb_for_metrics = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        metrics, prev_gray = quality_metrics(prev_gray, rgb_for_metrics, fps)

        status = v4l2_info.get("status", "OK")
        input_res = v4l2_info.get("input", f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}")
        osd_lines = [
            f"ADV7282-M tester | {device.path if device else 'auto'} | {status}",
            f"FPS {fps:4.1f} | input {input_res} | fmt {v4l2_info.get('fmt', 'n/a')} | output {args.output}",
            f"quality {metrics['quality']:3.0f}% | bright {metrics['brightness']:3.0f} | contrast {metrics['contrast']:3.0f} | sharp {metrics['sharpness']:5.0f}",
            f"motion {metrics['motion']:4.1f} | saturation {metrics['saturation']:3.0f} | std {v4l2_info.get('std', 'n/a')}",
        ]
        draw_osd(image, osd_lines)
        display.show(image)

    if cap is not None:
        cap.release()
    display.close()


if __name__ == "__main__":
    main()
