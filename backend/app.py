from flask import Flask, Response, request, jsonify
from flask_cors import CORS
import cv2
import os
import sqlite3
from datetime import datetime, timedelta, timezone
import jwt
from werkzeug.security import generate_password_hash, check_password_hash

from livekit.api import AccessToken, VideoGrants  # <-- server SDK import

app = Flask(__name__)

# ... your other imports

app.url_map.strict_slashes = False

# Replace with your actual Vercel domain


# TEMP: open CORS wide for debugging. Tighten later.
CORS(
    app,
    resources={
        r"/*": {
            "origins": [
                r".*vercel\.app$",           # deployed Vercel domains
                "http://localhost:5173",     # local Vite dev
                "http://127.0.0.1:5173",     # local Vite dev
            ]
        }
    },
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



DB_PATH = os.environ.get("AUTH_DB_PATH", os.path.join(os.path.dirname(__file__), "auth.db"))
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_EXPIRE_DAYS = int(os.environ.get("JWT_EXPIRE_DAYS", "7"))


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def create_jwt(username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=JWT_EXPIRE_DAYS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def verify_jwt(auth_header: str):
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload
    except jwt.PyJWTError:
        return None


@app.route('/auth/register', methods=['POST'])
def register():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400
    if len(username) < 3 or len(password) < 6:
        return jsonify({"error": "username >= 3 chars and password >= 6 chars"}), 400

    pw_hash = generate_password_hash(password)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, pw_hash, datetime.utcnow().isoformat()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "username already exists"}), 409
    finally:
        conn.close()

    token = create_jwt(username)
    return jsonify({"token": token, "username": username})


@app.route('/auth/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400

    conn = get_db()
    try:
        row = conn.execute("SELECT username, password_hash FROM users WHERE username = ?", (username,)).fetchone()
    finally:
        conn.close()
    if not row or not check_password_hash(row["password_hash"], password):
        return jsonify({"error": "invalid credentials"}), 401

    token = create_jwt(username)
    return jsonify({"token": token, "username": username})


@app.route('/auth/me', methods=['GET'])
def me():
    payload = verify_jwt(request.headers.get('Authorization', ''))
    if not payload:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"username": payload.get("sub")})


@app.route('/webrtc/token', methods=['POST', 'OPTIONS'])
def webrtc_token_unused():
    if request.method == 'OPTIONS':
        return ('', 204)
    # Require valid auth for both publish and view
    payload = verify_jwt(request.headers.get('Authorization', ''))
    if not payload:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json() or {}
    room = data.get("room", "playground-01")
    # Default identity to username from JWT when not provided
    identity = data.get("identity") or payload.get("sub") or "anonymous"
    publish = bool(data.get("publish", False))

    # Build grants (permissions)
    grants = VideoGrants(
        room_join=True,
        room=room,
        can_publish=publish,
        can_subscribe=True,
        can_publish_data=True,
    )
    try:
        api_key = os.environ["LIVEKIT_API_KEY"]
        api_secret = os.environ["LIVEKIT_API_SECRET"]
    except KeyError as e:
        return jsonify({"error": f"Missing environment variable: {e.args[0]}"}), 500

    token = (
        AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_grants(grants)
        .to_jwt()
    )
    return jsonify({"token": token})
def webrtc_token():
    if request.method == 'OPTIONS':
        # Preflight — return empty 204 with CORS headers (added by flask-cors)
        return ('', 204)
    
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
@app.after_request
def add_cors_headers(resp):
    # Safety net: make sure these are present even on errors
    resp.headers.setdefault('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
    resp.headers.setdefault('Vary', 'Origin')
    resp.headers.setdefault('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
    resp.headers.setdefault('Access-Control-Allow-Headers', 'Content-Type, Authorization')
    return resp

if __name__ == '__main__':
    # Bind on LAN so phones/other PCs can view
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
