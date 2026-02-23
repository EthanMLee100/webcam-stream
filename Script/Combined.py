
"""
Mode A (Person Gate):
- Look for person.
- If person detected -> switch to Mode B indefinitely.
- If no person detected for NO_PERSON_TIMEOUT seconds -> exit program.

Mode B (Fall Detection):
- Run fall model continuously.
- Only record/upload when fall triggers.
"""

import os
import time
import platform
import subprocess
from datetime import datetime

import cv2
import requests
from ultralytics import YOLO

# ----------------------- Config -----------------------
# Person gate
PERSON_MODEL = "yolov8n.pt"
PERSON_CLASS_ID = 0
PERSON_IMGSZ = 416
PERSON_CONF = 0.40
PERSON_CONF_TRIGGER = 0.80

# How long to wait for a person before exiting
NO_PERSON_TIMEOUT = 20.0  # seconds

# Fall model
FALL_MODEL = r"C:\Users\ethan\Downloads\best (1).pt"
FALL_CLASS_ID = 0
FALL_IMGSZ = 416
FALL_CONF = 0.20
FALL_CONF_TRIGGER = 0.70

# Optional: require N consecutive fall frames before triggering
REQUIRE_FALL_CONSEC_FRAMES = 3

# Camera
SOURCE = "0"
REQ_W = 640
REQ_H = 480
REQ_FPS = 24

# Clip settings (only used in fall mode)
CLIP_DURATION = 5.0
COOLDOWN = 180.0

WINDOW_TITLE = "Gate -> Fall Detection"

# Backend
AUTH_EMAIL = "ethanmlee@msn.com"
AUTH_PASSWORD = "EL000244"
BACKEND_BASE_URL = "https://webcam-stream-ea5w.onrender.com"
EVENT_UPLOAD_URL = f"{BACKEND_BASE_URL}/events/upload"
EVENT_TYPE = "person-fall"
DEVICE_ID = "pi-01"

FFMPEG_PATH = r"C:\Users\ethan\Downloads\ffmpeg-2025-11-17-git-e94439e49b-full_build\ffmpeg-2025-11-17-git-e94439e49b-full_build\bin\ffmpeg.exe"
# ------------------------------------------------------


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
        print("ffmpeg failed; uploading original file.")
        return src_path
    return h264_path


def best_conf_from_det(det):
    if not det or len(det) == 0:
        return 0.0
    try:
        return max((box.conf.item() for box in det[0].boxes), default=0.0)
    except Exception:
        return 0.0


def person_gate_loop(cap, person_model):
    """
    Returns True if person detected (triggered), False if timed out.
    """
    start = time.time()
    while True:
        ok, frame = cap.read()
        if not ok:
            return False

        det = person_model.predict(
            source=frame,
            imgsz=PERSON_IMGSZ,
            conf=PERSON_CONF,
            classes=[PERSON_CLASS_ID],
            verbose=False,
        )
        best_person = best_conf_from_det(det)

        elapsed = time.time() - start
        remaining = max(0.0, NO_PERSON_TIMEOUT - elapsed)

        # UI
        display = frame.copy()
        cv2.putText(display, f"MODE: PERSON GATE  (timeout in {remaining:.1f}s)",
                    (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 3)
        cv2.putText(display, f"MODE: PERSON GATE  (timeout in {remaining:.1f}s)",
                    (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)

        cv2.putText(display, f"person_best={best_person:.2f}  trigger>={PERSON_CONF_TRIGGER:.2f}",
                    (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 3)
        cv2.putText(display, f"person_best={best_person:.2f}  trigger>={PERSON_CONF_TRIGGER:.2f}",
                    (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)

        cv2.imshow(WINDOW_TITLE, display)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            return False

        # Trigger condition
        if best_person >= PERSON_CONF_TRIGGER:
            print(f"[INFO] Person detected (conf={best_person:.2f}). Switching to fall detection indefinitely.")
            return True

        # Timeout condition (no person)
        if elapsed >= NO_PERSON_TIMEOUT:
            print("[INFO] No person detected during timeout window. Exiting (turning off).")
            return False


def fall_detection_loop(cap, fall_model, jwt_token, actual_fps, frame_w, frame_h):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")

    recording = False
    recording_end_ts = 0.0
    cooldown_until = 0.0
    writer = None
    current_filename = None
    fall_consec = 0

    print("[INFO] Fall detection running indefinitely. Press 'q' to quit.")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            det = fall_model.predict(
                source=frame,
                imgsz=FALL_IMGSZ,
                conf=FALL_CONF,
                classes=[FALL_CLASS_ID],
                verbose=False,
            )
            best_fall = best_conf_from_det(det)
            now = time.time()

            if best_fall >= FALL_CONF_TRIGGER:
                fall_consec += 1
            else:
                fall_consec = 0

            fall_confirmed = (fall_consec >= REQUIRE_FALL_CONSEC_FRAMES)

            # Trigger clip on confirmed fall
            if fall_confirmed and (not recording) and (now >= cooldown_until):
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
                    print(f"[{ts}] FALL trigger (fall={best_fall:.2f}, consec={fall_consec}) -> recording {current_filename}")

            # Write frames if recording
            if recording and writer:
                writer.write(frame)
                if now >= recording_end_ts:
                    writer.release()
                    writer = None
                    recording = False
                    duration = now - (recording_end_ts - CLIP_DURATION)
                    cooldown_until = now + COOLDOWN
                    fall_consec = 0  # reset after an event

                    try:
                        size_bytes = os.path.getsize(current_filename)
                        print(f"Saved {current_filename} ({size_bytes} bytes). Converting/uploading…")
                        upload_path = convert_to_h264(current_filename)
                        upload_clip(upload_path, jwt_token, duration)
                        print(f"Uploaded {upload_path}")
                        if upload_path != current_filename and os.path.exists(upload_path):
                            os.remove(upload_path)
                    except requests.HTTPError as e:
                        if e.response is not None and e.response.status_code == 401:
                            jwt_token = fetch_jwt()
                            upload_clip(upload_path, jwt_token, duration)
                            print("[INFO] Re-uploaded after refreshing JWT")
                        else:
                            print(f"Upload failed: {e}")
                    except Exception as e:
                        print(f"Upload error: {e}")
                    finally:
                        current_filename = None

            # UI
            display = frame.copy()
            cv2.putText(display, "MODE: FALL DETECTION (indefinite)",
                        (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 3)
            cv2.putText(display, "MODE: FALL DETECTION (indefinite)",
                        (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)

            cv2.putText(display, f"fall_best={best_fall:.2f}  trig>={FALL_CONF_TRIGGER:.2f}  consec={fall_consec}/{REQUIRE_FALL_CONSEC_FRAMES}",
                        (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 3)
            cv2.putText(display, f"fall_best={best_fall:.2f}  trig>={FALL_CONF_TRIGGER:.2f}  consec={fall_consec}/{REQUIRE_FALL_CONSEC_FRAMES}",
                        (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)

            if recording:
                cv2.putText(display, "RECORDING...",
                            (10, 85), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

            cv2.imshow(WINDOW_TITLE, display)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    finally:
        if writer:
            writer.release()


def main():
    print("[INFO] Loading models...")
    person_model = YOLO(PERSON_MODEL)
    fall_model = YOLO(FALL_MODEL)

    cap = open_source(SOURCE)
    if cap is None:
        raise RuntimeError(f"Unable to open SOURCE={SOURCE}. Check camera/virtual device.")

    actual_fps = cap.get(cv2.CAP_PROP_FPS) or REQ_FPS or 24
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or REQ_W
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or REQ_H

    jwt_token = fetch_jwt()

    try:
        # MODE A: Gate
        triggered = person_gate_loop(cap, person_model)
        if not triggered:
            return  # exit program

        # MODE B: Fall detection indefinitely
        fall_detection_loop(cap, fall_model, jwt_token, actual_fps, frame_w, frame_h)

    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()