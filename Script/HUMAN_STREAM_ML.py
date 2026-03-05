"""
Pi 4: YOLOv8 Human Detection + RTMPS Livestream (ffmpeg) + Clip Capture + H.264 Convert + Backend Upload

Architecture (like combine_new.py):
- Thread 1: Capture + Stream
  * Reads camera frames continuously
  * Streams EVERY frame to RTMPS via ffmpeg stdin (smooth stream for FlutterFlow)
  * Updates a shared "latest frame" for ML (no backlog)

- Thread 2: ML (Human detection)
  * Reads latest frame snapshot
  * Runs YOLO person detection (optionally downscaled + throttled)
  * On trigger: records a short clip and uploads (after H.264 conversion)

Notes:
- Uses system ffmpeg on Pi (install: sudo apt-get install -y ffmpeg)
- Saves events to: /home/gceja/Desktop/SolarPlaygroundPi/events_human
"""

import os
import time
import platform
import threading
import subprocess
from datetime import datetime

import cv2
import requests
from ultralytics import YOLO

# ----------------------- Config -----------------------
# Model (COCO person)
MODEL = "yolov8n.pt"
HUMAN_CLASS_ID = 0
IMGSZ = 416
BASE_CONF = 0.40
HUMAN_CONF_TRIGGER = 0.80  # trigger clip when best person conf >= this

# Camera (Pi)
SOURCE = 0
REQ_W = 640
REQ_H = 480
REQ_FPS = 24

# Stream settings (to backend / LiveKit ingest)
# IMPORTANT: set this to the RTMPS ingest URL your FlutterFlow stream uses
RTMPS_URL = "rtmps://webcam-stream-d4cttylu.rtmp.livekit.cloud/x/NthuXzhPNuiN"

# ML downscale (saves CPU; streaming stays full-res)
INFER_W = 640
INFER_H = 360

# ML throttle (limits how often YOLO runs; streaming unaffected)
ML_MAX_FPS = 4.0  # set 0 for "as fast as possible"

# Clip / upload settings
CLIP_DURATION_SEC = 3.0
COOLDOWN_SEC = 60.0
EVENTS_DIR = "/home/gceja/Desktop/SolarPlaygroundPi/events_human"

# Backend (clip upload)
BACKEND_BASE_URL = "https://webcam-stream-ea5w.onrender.com"
EVENT_UPLOAD_URL = f"{BACKEND_BASE_URL}/events/upload"
EVENT_TYPE = "human-present"
DEVICE_ID = "pi-01"

AUTH_EMAIL = "ethanmlee@msn.com"
AUTH_PASSWORD = "EL000244"
# ------------------------------------------------------


def ensure_events_dir():
    os.makedirs(EVENTS_DIR, exist_ok=True)


def ensure_ffmpeg_available():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except Exception as e:
        raise RuntimeError(
            "ffmpeg not found. Install on Pi with:\n"
            "  sudo apt-get update && sudo apt-get install -y ffmpeg"
        ) from e


def open_source(source):
    sysname = platform.system()
    if sysname == "Windows":
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_FFMPEG]
    elif sysname == "Darwin":
        backends = [cv2.CAP_AVFOUNDATION, cv2.CAP_FFMPEG]
    else:
        backends = [cv2.CAP_V4L2, cv2.CAP_FFMPEG, cv2.CAP_GSTREAMER]

    src = int(source) if isinstance(source, str) and source.isdigit() else source

    for backend in backends:
        cap = cv2.VideoCapture(src, backend)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, REQ_W)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, REQ_H)
            cap.set(cv2.CAP_PROP_FPS, REQ_FPS)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return cap
        cap.release()
    return None


def fetch_jwt():
    resp = requests.post(
        f"{BACKEND_BASE_URL}/auth/login",
        json={"email": AUTH_EMAIL, "password": AUTH_PASSWORD},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["token"]


def upload_clip(filepath, jwt_token, duration_sec):
    with open(filepath, "rb") as f:
        resp = requests.post(
            EVENT_UPLOAD_URL,
            headers={"Authorization": f"Bearer {jwt_token}"},
            files={"file": (os.path.basename(filepath), f, "video/mp4")},
            data={
                "event_type": EVENT_TYPE,
                "device_id": DEVICE_ID,
                "duration_seconds": duration_sec,
            },
            timeout=60,
        )
    resp.raise_for_status()


def convert_to_h264_ffmpeg(src_path: str) -> str:
    """Convert clip to H.264 MP4 using ffmpeg. Returns final path (or original on failure)."""
    base, _ = os.path.splitext(src_path)
    out_path = f"{base}_h264.mp4"

    cmd = [
        "ffmpeg",
        "-y",
        "-i", src_path,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or not os.path.exists(out_path):
        print("[WARN] ffmpeg conversion failed; uploading original file.")
        return src_path
    return out_path


def best_conf_from_det(det) -> float:
    if not det or len(det) == 0:
        return 0.0
    try:
        return max((box.conf.item() for box in det[0].boxes), default=0.0)
    except Exception:
        return 0.0


def start_ffmpeg_stream(w, h, fps, rtmps_url):
    """
    Start ffmpeg that reads raw BGR frames from stdin and streams RTMPS.
    """
    cmd = [
        "ffmpeg",
        "-loglevel", "error",

        # raw frames from Python:
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{w}x{h}",
        "-r", str(int(fps)),
        "-i", "-",

        # no audio
        "-an",

        # low-latency encode
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-bf", "0",
        "-pix_fmt", "yuv420p",
        "-profile:v", "baseline",
        "-g", "60",

        # bitrate tuning (adjust if needed)
        "-b:v", "1500k",
        "-maxrate", "1500k",
        "-bufsize", "3000k",

        "-f", "flv",
        rtmps_url,
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


class SharedFrame:
    """Thread-safe 'latest frame' holder (no backlog)."""
    def __init__(self):
        self._lock = threading.Lock()
        self._frame = None
        self._ts = 0.0

    def set(self, frame, ts):
        with self._lock:
            self._frame = frame
            self._ts = ts

    def get(self):
        with self._lock:
            if self._frame is None:
                return None, 0.0
            return self._frame.copy(), self._ts


def capture_and_stream_loop(cap, ffmpeg_proc, shared: SharedFrame, stop_event: threading.Event):
    """
    Continuously capture frames, stream them, update shared latest frame.
    """
    while not stop_event.is_set():
        ok, frame = cap.read()
        if not ok:
            print("[WARN] Camera read failed.")
            stop_event.set()
            break

        ts = time.time()

        # Stream every frame
        try:
            ffmpeg_proc.stdin.write(frame.tobytes())
        except BrokenPipeError:
            print("[ERROR] ffmpeg pipe broke (stream ended?).")
            stop_event.set()
            break
        except Exception as e:
            print(f"[ERROR] ffmpeg write error: {e}")
            stop_event.set()
            break

        # Update latest for ML
        shared.set(frame, ts)


def ml_loop(shared: SharedFrame, stop_event: threading.Event):
    """
    Human detection only.
    Records/uploads clips on human trigger.
    """
    print("[INFO] Loading YOLO model (ML thread)...")
    model = YOLO(MODEL)

    print("[INFO] Fetching JWT for uploads...")
    jwt_token = fetch_jwt()

    ensure_events_dir()

    # recording state
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # we'll convert after
    recording = False
    recording_end_ts = 0.0
    cooldown_until = 0.0
    writer = None
    current_path = None

    # throttle
    min_dt = (1.0 / ML_MAX_FPS) if ML_MAX_FPS and ML_MAX_FPS > 0 else 0.0
    last_ml_ts = 0.0

    last_best = 0.0

    print("[INFO] ML thread running (human detection).")
    try:
        while not stop_event.is_set():
            frame, _ = shared.get()
            if frame is None:
                time.sleep(0.01)
                continue

            now = time.time()

            if min_dt > 0 and (now - last_ml_ts) < min_dt:
                time.sleep(0.005)
                continue
            last_ml_ts = now

            # downscale for ML
            small = cv2.resize(frame, (INFER_W, INFER_H), interpolation=cv2.INTER_AREA)

            det = model.predict(
                source=small,
                imgsz=IMGSZ,
                conf=BASE_CONF,
                classes=[HUMAN_CLASS_ID],
                verbose=False,
            )
            last_best = best_conf_from_det(det)

            # trigger clip
            if (last_best >= HUMAN_CONF_TRIGGER) and (not recording) and (now >= cooldown_until):
                recording = True
                recording_end_ts = now + CLIP_DURATION_SEC
                ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"human_clip_{ts_str}.mp4"
                current_path = os.path.join(EVENTS_DIR, filename)

                writer = cv2.VideoWriter(current_path, fourcc, float(REQ_FPS), (frame.shape[1], frame.shape[0]))
                if not writer.isOpened():
                    try:
                        writer.release()
                    except Exception:
                        pass
                    writer = None
                    recording = False
                    print(f"[{ts_str}] Failed to open VideoWriter")
                else:
                    print(f"[{ts_str}] Human trigger (conf={last_best:.2f}) -> recording {current_path}")

            # write frames while recording (uses the latest full-res frames)
            if recording and writer:
                writer.write(frame)
                if now >= recording_end_ts:
                    writer.release()
                    writer = None
                    recording = False

                    cooldown_until = now + COOLDOWN_SEC

                    try:
                        print(f"[INFO] Saved clip: {current_path}. Converting to H.264...")
                        h264_path = convert_to_h264_ffmpeg(current_path)
                        print(f"[INFO] Uploading: {h264_path}")
                        upload_clip(h264_path, jwt_token, CLIP_DURATION_SEC)
                        print(f"[INFO] Uploaded {h264_path}")

                        # cleanup converted file (optional)
                        if h264_path != current_path and os.path.exists(h264_path):
                            os.remove(h264_path)

                    except requests.HTTPError as e:
                        if e.response is not None and e.response.status_code == 401:
                            print("[WARN] JWT expired. Refreshing and retrying upload...")
                            jwt_token = fetch_jwt()
                            h264_path = convert_to_h264_ffmpeg(current_path)
                            upload_clip(h264_path, jwt_token, CLIP_DURATION_SEC)
                            print(f"[INFO] Re-uploaded {h264_path}")
                            if h264_path != current_path and os.path.exists(h264_path):
                                os.remove(h264_path)
                        else:
                            print(f"[ERROR] Upload failed: {e}")
                    except Exception as e:
                        print(f"[ERROR] Upload error: {e}")
                    finally:
                        current_path = None

    finally:
        if writer:
            writer.release()


def main():
    if platform.system() == "Windows":
        print("[WARN] This is intended for Raspberry Pi / Linux.")

    ensure_ffmpeg_available()

    print("[INFO] Opening camera...")
    cap = open_source(SOURCE)
    if cap is None:
        raise RuntimeError("Unable to open camera source.")

    actual_fps = cap.get(cv2.CAP_PROP_FPS) or REQ_FPS or 24
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or REQ_W
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or REQ_H

    print(f"[INFO] Camera: {frame_w}x{frame_h} @ {actual_fps:.1f} fps")
    print("[INFO] Starting ffmpeg livestream to backend...")
    ffmpeg_proc = start_ffmpeg_stream(frame_w, frame_h, actual_fps, RTMPS_URL)

    shared = SharedFrame()
    stop_event = threading.Event()

    t_stream = threading.Thread(
        target=capture_and_stream_loop,
        args=(cap, ffmpeg_proc, shared, stop_event),
        daemon=True,
    )
    t_ml = threading.Thread(
        target=ml_loop,
        args=(shared, stop_event),
        daemon=True,
    )

    print("[INFO] Starting threads...")
    t_stream.start()
    t_ml.start()

    try:
        while not stop_event.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        print("[INFO] Shutting down...")
        stop_event.set()

        try:
            cap.release()
        except Exception:
            pass

        try:
            if ffmpeg_proc and ffmpeg_proc.stdin:
                ffmpeg_proc.stdin.close()
        except Exception:
            pass

        try:
            if ffmpeg_proc:
                ffmpeg_proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    main()