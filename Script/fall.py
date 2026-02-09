import os
import time
import platform
import subprocess
from datetime import datetime

import cv2
import requests
from ultralytics import YOLO

# ----------------------- Model / detection config -----------------------
MODEL = r"C:\Users\ethan\Downloads\best (1).pt"  # your custom fall model
SOURCE = "0"                 # or "OBS Virtual Camera" / index as needed
IMGSZ = 416
CONF = 0.20                  # detection confidence threshold for YOLO
FALL_CLASS_ID = 0            # class ID for “Fall” in your dataset
WINDOW_TITLE = "Fall Detection + Clip Uploader"

REQ_W = 640
REQ_H = 480
REQ_FPS = 24
# ------------------------------------------------------------------------

# Clip / upload settings
CLIP_DURATION = 5.0
COOLDOWN = 180.0             # seconds before re-triggering
CONF_TRIGGER = 0.70          # only clip when detection ≥ 0.80

AUTH_EMAIL = "ethanmlee@msn.com"
AUTH_PASSWORD = "EL000244"
BACKEND_BASE_URL = "https://webcam-stream-ea5w.onrender.com"
EVENT_UPLOAD_URL = f"{BACKEND_BASE_URL}/events/upload"
EVENT_TYPE = "person-fall"
DEVICE_ID = "pi-01"

FFMPEG_PATH = r"C:\Users\ethan\Downloads\ffmpeg-2025-11-17-git-e94439e49b-full_build\ffmpeg-2025-11-17-git-e94439e49b-full_build\bin\ffmpeg.exe"

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

def upload_clip(filepath, jwt_token, duration):
    with open(filepath, "rb") as f:
        resp = requests.post(
            EVENT_UPLOAD_URL,
            headers={"Authorization": f"Bearer {jwt_token}"},
            files={"file": (os.path.basename(filepath), f, "video/mp4")},
            data={
                "event_type": EVENT_TYPE,
                "device_id": DEVICE_ID,
                "duration_seconds": duration,
            },
            timeout=60,
        )
    resp.raise_for_status()

def convert_to_h264(src_path):
    """Convert clip to H.264 MP4 using ffmpeg. Returns final path."""
    h264_path = src_path.replace(".mp4", "_h264.mp4")
    cmd = [
        FFMPEG_PATH,
        "-y",
        "-i", src_path,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        h264_path,
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        print("ffmpeg conversion failed; uploading original file.")
        return src_path
    return h264_path

def main():
    print(f"[INFO] Loading fall model from: {MODEL}")
    model = YOLO(MODEL)
    try:
        print("[INFO] Model classes:", model.model.names)
    except Exception:
        pass

    cap = open_source(SOURCE)
    if cap is None:
        raise RuntimeError(f"Unable to open SOURCE={SOURCE}. Check camera/virtual device.")

    actual_fps = cap.get(cv2.CAP_PROP_FPS) or REQ_FPS or 24
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or REQ_W
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or REQ_H
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")

    jwt_token = fetch_jwt()
    recording = False
    recording_end_ts = 0.0
    cooldown_until = 0.0
    writer = None
    current_filename = None

    print("Press 'q' to quit")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            det = model.predict(
                source=frame,
                imgsz=IMGSZ,
                conf=CONF,
                classes=[FALL_CLASS_ID],
                verbose=False,
            )
            best_conf = max((box.conf.item() for box in det[0].boxes), default=0.0)
            now = time.time()

            # Trigger clip
            if best_conf >= CONF_TRIGGER and not recording and now >= cooldown_until:
                recording = True
                recording_end_ts = now + CLIP_DURATION
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                current_filename = f"clip_{ts}.mp4"
                writer = cv2.VideoWriter(current_filename, fourcc, actual_fps, (frame_w, frame_h))
                if not writer.isOpened():
                    writer.release()
                    writer = None
                    recording = False
                    print(f"[{ts}] Failed to open VideoWriter")
                else:
                    print(f"[{ts}] Triggered recording → {current_filename}")

            # Record frames if active
            if recording and writer:
                writer.write(frame)
                if now >= recording_end_ts:
                    writer.release()
                    writer = None
                    recording = False
                    duration = now - (recording_end_ts - CLIP_DURATION)
                    cooldown_until = now + COOLDOWN
                    try:
                        size_bytes = os.path.getsize(current_filename)
                        print(f"Saved {current_filename} ({size_bytes} bytes). Converting/uploading…")
                        upload_path = convert_to_h264(current_filename)
                        upload_clip(upload_path, jwt_token, duration)
                        print(f"Uploaded {upload_path}")
                        if upload_path != current_filename and os.path.exists(upload_path):
                            os.remove(upload_path)
                    except requests.HTTPError as e:
                        if e.response.status_code == 401:
                            jwt_token = fetch_jwt()
                            upload_clip(upload_path, jwt_token, duration)
                        else:
                            print(f"Upload failed: {e}")
                    except Exception as e:
                        print(f"Upload error: {e}")
                    finally:
                        current_filename = None

            annotated = frame
            cv2.imshow(WINDOW_TITLE, annotated)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    finally:
        cap.release()
        if writer:
            writer.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    main()