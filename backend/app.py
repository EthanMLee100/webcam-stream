from flask import Flask, Response, request, jsonify
from flask_cors import CORS
import cv2
import os

from livekit.api import AccessToken, VideoGrants  # <-- server SDK import

app = Flask(__name__)

# ... your other imports


# Replace with your actual Vercel domain
ALLOWED_ORIGINS = ["webcam-stream-omega.vercel.app"]

CORS(
    app,
    resources={r"/*": {"origins": ALLOWED_ORIGINS}},
    supports_credentials=False,
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


# Try camera index 0 first; if you have other cameras, try 1, 2, etc.
cap = cv2.VideoCapture(0)

# Optional: force MJPG for smoother USB2.0 webcams like C270
# Comment out if your driver doesn't like it
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))

# Set a safe resolution & fps (C270 can do 1280x720 MJPG, but 640x480 is universal)
cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
cap.set(cv2.CAP_PROP_FPS, 30)

def mjpeg_generator():
    if not cap.isOpened():
        raise RuntimeError("Could not open video source")

    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 80]  # 60–85 is typical
    while True:
        ok, frame = cap.read()
        if not ok:
            break

        # (Optional) mirror the image like a selfie
        # frame = cv2.flip(frame, 1)

        ok, jpg = cv2.imencode('.jpg', frame, encode_params)
        if not ok:
            continue
        bytes_frame = jpg.tobytes()

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n'
               b'Content-Length: ' + str(len(bytes_frame)).encode() + b'\r\n\r\n' +
               bytes_frame + b'\r\n')


@app.route('/webrtc/token', methods=['POST'])
def get_livekit_token():
    data = request.get_json() or {}
    room = data.get("room", "playground-01")
    identity = data.get("identity", "anonymous")
    publish = bool(data.get("publish", False))

    # Build grants (permissions)
    grants = VideoGrants(
        room_join=True,
        room=room,
        can_publish=publish,
        can_subscribe=True,
        can_publish_data=True,
    )

    token = (
        AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(identity)
        .with_grants(grants)
        .to_jwt()
    )
    return jsonify({"token": token})




@app.route('/video_feed')
def video_feed():
    return Response(mjpeg_generator(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/')
def index():
    # Quick test page
    return '''
    <html>
      <body style="margin:0;background:#111;display:flex;justify-content:center;align-items:center;height:100vh;">
        <img src="/video_feed" style="max-width:100%;height:auto;border:4px solid #333;border-radius:12px"/>
      </body>
    </html>
    '''

if __name__ == '__main__':
    # Bind on LAN so phones/other PCs can view
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
