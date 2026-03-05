# -*- coding: utf-8 -*-
"""
Created on Wed Mar  4 18:56:49 2026

@author: ethan
"""

"""
Pi 4: YOLOv8 Human Detection + MJPEG HTTP Stream + Clip Capture + H.264 Convert + Backend Upload

- Streams annotated frames at: http://<pi-ip>:8080/video_feed
- Simple page at: http://<pi-ip>:8080/
- Records clip ONLY when human detected above threshold
- Converts clip to H.264 with ffmpeg before upload
"""

import os
import time
import platform
import subprocess
import threading
from datetime import datetime
from collections import deque

import cv2
import requests
from flask import Flask, Response, render_template_string
from ultralytics import YOLO

# ----------------------- Config -----------------------
# Model
MODEL = "yolov8n.pt"
HUMAN_CLASS_ID = 0  # COCO person
IMGSZ = 416
BASE_CONF = 0.40
HUMAN_CONF_TRIGGER = 0.80

# Camera
SOURCE = 0
REQ_W = 640
REQ_H = 480
REQ_FPS = 24

# Streaming server
HOST = "0.0.0.0"
PORT = 8080
STREAM_FPS_LIMIT = 12  # MJPEG send rate cap

# Clip / upload
CLIP_DURATION_SEC = 3.0
COOLDOWN_SEC = 60.0
EVENTS_DIR = "/home/pi/events_human"  # change if you want

# Backend
BACKEND_BASE_URL = "https://webcam-stream-ea5w.onrender.com"
EVENT_UPLOAD_URL = f"{BACKEND_BASE_URL}/events/upload"
EVENT_TYPE = "human-present"
DEVICE_ID = "pi-01"
AUTH_EMAIL = "ethanmlee@msn.com"
AUTH_PASSWORD = "EL000244"

# Performance knobs
DETECT_EVERY_N_FRAMES = 2  # 1 = every frame
PRINT_METRICS_EVERY_SEC = 5.0
METRICS_WINDOW = 120
# ------------------------------------------------------


# ----------------------- Streaming State -----------------------
app = Flask(__name__)
latest_jpeg = None
latest_jpeg_lock = threading.Lock()
stop_event = threading.Event()


INDEX_HTML = """
<!doctype html>
<html>
  <head>
    <title>Pi Human Stream</title>
    <style>
      body { font-family: Arial, sans-serif; background:#111; color:#eee; text-align:center; }
      .wrap { max-width: 900px; margin: 24px auto; }
      img { width: 100%; border-radius: 12px; border: 1px solid #333; }
      .meta { margin-top: 10px; color: #bbb; font-size: 14px; }
    </style>
  </head>
  <body>
    <div class="wrap">
      <h2>Pi Human Detection Stream</h2>
      <img src="/video_feed" />
      <div class="meta">MJPEG stream • refresh page if it freezes</div>
    </div>
  </body>
</html>
"""


@app.get("/")
def index():
    return render_template_string(INDEX_HTML)


def mjpeg_generator():
    """Yield the latest JPEG frame as an MJPEG multipart response."""
    min_dt = 1.0 / max(STREAM_FPS_LIMIT, 1)
    last_send = 0.0
    while not stop_event.is_set():
        now = time.time()
        if (now - last_send) < min_dt:
            time.sleep(0.005)
            continue
        last_send = now

        with latest_jpeg_lock:
            frame = latest_jpeg

        if frame is None:
            time.sleep(0.02)
            continue

        yield (b"--frame\r\n"
               b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")


@app.get("/video_feed")
def video_feed():
    return Response(mjpeg_generator(), mimetype="multipart/x-mixed-replace; boundary=frame")


# ----------------------- Helpers -----------------------
def ensure_events_dir():
    os.makedirs(EVENTS_DIR, exist_ok=True)


def ensure_ffmpeg_available():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except Exception as e:
        raise RuntimeError(
            "ffmpeg not found. Install with: sudo apt-get update && sudo apt-get install -y ffmpeg"
        ) from e


def open_source(source):
    sysname = platform.system()
    if sysname == "Windows":
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_FFMPEG]
    elif sysname == "Darwin":
        backends = [cv2.CAP_AVFOUNDATION, cv2.CAP_FFMPEG]
    else:
        backends = [cv2.CAP_V4L2, cv2.CAP_FFMPEG, cv2.CAP_GSTREAMER]

    for backend in backends:
        cap = cv2.VideoCapture(source, backend)
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
    """Convert to H.264 MP4. Returns converted path or original on failure."""
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
        print("[WARN] ffmpeg conversion failed; uploading original.")
        return src_path
    return out_path


def best_conf_from_det(det) -> float:
    if not det:
        return 0.0
    try:
        return max((box.conf.item() for box in det[0].boxes), default=0.0)
    except Exception:
        return 0.0


def annotate_for_stream(frame, best_conf, recording):
    """Overlay status text on frame for the MJPEG stream."""
    out = frame.copy()
    txt1 = f"human_best={best_conf:.2f} trig>={HUMAN_CONF_TRIGGER:.2f}"
    txt2 = "RECORDING..." if recording else "idle"
    cv2.putText(out, txt1, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 3)
    cv2.putText(out, txt1, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)
    cv2.putText(out, txt2, (10, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255) if recording else (255, 255, 255), 2)
    return out


def update_stream_jpeg(bgr_frame):
    """Encode BGR frame -> JPEG and store as latest for MJPEG."""
    global latest_jpeg
    ok, jpg = cv2.imencode(".jpg", bgr_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ok:
        return
    with latest_jpeg_lock:
        latest_jpeg = jpg.tobytes()


# ----------------------- Core Loop -----------------------
def camera_ml_loop():
    ensure_events_dir()
    ensure_ffmpeg_available()

    model = YOLO(MODEL)

    cap = open_source(SOURCE)
    if cap is None:
        raise RuntimeError("Cannot open camera source")

    actual_fps = cap.get(cv2.CAP_PROP_FPS) or REQ_FPS or 24
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or REQ_W
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or REQ_H
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # we'll convert after

    jwt_token = fetch_jwt()

    recording = False
    recording_end_ts = 0.0
    cooldown_until = 0.0
    writer = None
    current_filepath = None

    # metrics
    last_metrics_print = time.time()
    loop_dt_hist = deque(maxlen=METRICS_WINDOW)
    infer_dt_hist = deque(maxlen=METRICS_WINDOW)
    detect_rate_hist = deque(maxlen=METRICS_WINDOW)

    frame_count = 0
    last_det = None
    last_best_conf = 0.0

    print(f"[INFO] Streaming at http://<pi-ip>:{PORT}/  (video at /video_feed)")
    print("[INFO] Starting camera + ML loop... Ctrl+C to stop")

    try:
        while not stop_event.is_set():
            t0 = time.perf_counter()
            ok, frame = cap.read()
            if not ok:
                print("[WARN] Camera read failed.")
                time.sleep(0.2)
                continue

            frame_count += 1
            now = time.time()
            run_detect = (frame_count % DETECT_EVERY_N_FRAMES == 0)

            det = None
            infer_dt = 0.0
            if run_detect:
                ti0 = time.perf_counter()
                det = model.predict(
                    source=frame,
                    imgsz=IMGSZ,
                    conf=BASE_CONF,
                    classes=[HUMAN_CLASS_ID],
                    verbose=False,
                )
                infer_dt = time.perf_counter() - ti0
                last_det = det
                last_best_conf = best_conf_from_det(det)
            else:
                det = last_det

            # Trigger recording when human present
            if (last_best_conf >= HUMAN_CONF_TRIGGER) and (not recording) and (now >= cooldown_until):
                recording = True
                recording_end_ts = now + CLIP_DURATION_SEC
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"human_clip_{ts}.mp4"
                current_filepath = os.path.join(EVENTS_DIR, filename)
                writer = cv2.VideoWriter(current_filepath, fourcc, actual_fps, (frame_w, frame_h))

                if not writer.isOpened():
                    try:
                        writer.release()
                    except Exception:
                        pass
                    writer = None
                    recording = False
                    print(f"[{ts}] Failed to open VideoWriter")
                else:
                    print(f"[{ts}] Human detected (conf={last_best_conf:.2f}). Recording -> {current_filepath}")

            # Write frames if recording
            if recording and writer:
                writer.write(frame)
                if now >= recording_end_ts:
                    writer.release()
                    writer = None
                    recording = False

                    cooldown_until = now + COOLDOWN_SEC
                    recorded_path = current_filepath
                    current_filepath = None

                    try:
                        h264_path = convert_to_h264_ffmpeg(recorded_path)
                        upload_clip(h264_path, jwt_token, CLIP_DURATION_SEC)
                        print(f"[INFO] Uploaded {h264_path}")

                        # cleanup converted file if different
                        if h264_path != recorded_path and os.path.exists(h264_path):
                            os.remove(h264_path)

                    except requests.HTTPError as e:
                        if e.response is not None and e.response.status_code == 401:
                            print("[WARN] JWT expired. Refreshing and retrying upload...")
                            jwt_token = fetch_jwt()
                            h264_path = convert_to_h264_ffmpeg(recorded_path)
                            upload_clip(h264_path, jwt_token, CLIP_DURATION_SEC)
                            print(f"[INFO] Re-uploaded {h264_path}")
                            if h264_path != recorded_path and os.path.exists(h264_path):
                                os.remove(h264_path)
                        else:
                            print(f"[ERROR] Upload failed: {e}")
                    except Exception as e:
                        print(f"[ERROR] Upload error: {e}")

            # Build annotated frame for stream
            annotated = frame
            try:
                if det:
                    annotated = det[0].plot()
            except Exception:
                annotated = frame

            annotated = annotate_for_stream(annotated, last_best_conf, recording)
            update_stream_jpeg(annotated)

            # metrics
            loop_dt = time.perf_counter() - t0
            loop_dt_hist.append(loop_dt)
            infer_dt_hist.append(infer_dt if run_detect else 0.0)
            detect_rate_hist.append(1 if run_detect else 0)

            if (time.time() - last_metrics_print) >= PRINT_METRICS_EVERY_SEC:
                last_metrics_print = time.time()
                avg_loop_dt = sum(loop_dt_hist) / max(len(loop_dt_hist), 1)
                avg_fps = (1.0 / avg_loop_dt) if avg_loop_dt > 0 else 0.0
                infer_samples = [x for x in infer_dt_hist if x > 0]
                avg_infer = (sum(infer_samples) / len(infer_samples)) if infer_samples else 0.0
                detect_rate = (sum(detect_rate_hist) / max(len(detect_rate_hist), 1)) * 100.0

                print(
                    f"[METRICS] fps={avg_fps:.1f} | infer={avg_infer*1000:.1f}ms | "
                    f"detect_frames={detect_rate:.0f}% | best_conf={last_best_conf:.2f} | "
                    f"recording={'YES' if recording else 'no'}"
                )

    finally:
        cap.release()
        if writer:
            writer.release()


def run_server():
    # Flask built-in server is fine for LAN MJPEG; for production you can use gunicorn.
    app.run(host=HOST, port=PORT, debug=False, threaded=True)


def main():
    # Start ML loop in a background thread
    t = threading.Thread(target=camera_ml_loop, daemon=True)
    t.start()

    try:
        run_server()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        t.join(timeout=2.0)


if __name__ == "__main__":
    main()