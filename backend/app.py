from flask import Flask, Response, request, jsonify
from flask_cors import CORS
import cv2
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import jwt
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
from psycopg2 import errors as pg_errors
import secrets
import hashlib
import re
import time
import requests
import json

# Firebase Admin (Storage)
try:
    import firebase_admin
    from firebase_admin import credentials as fb_credentials, storage as fb_storage
except Exception:
    firebase_admin = None

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



DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    # Fallback to provided Neon DSN if env not set (for local/dev). Prefer setting env in Render.
    "postgresql://neondb_owner:npg_ek5PG9mRDJxW@ep-long-dream-aemp6jrl-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require",
)
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_EXPIRE_DAYS = int(os.environ.get("JWT_EXPIRE_DAYS", "7"))

# Email + reset configuration (use env in production)
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "")
FROM_NAME = os.environ.get("FROM_NAME", "")
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "")  # e.g., https://your-app.vercel.app
RESET_TOKEN_TTL_MIN = int(os.environ.get("RESET_TOKEN_TTL_MIN", "60"))
FORGOT_RATE_PER_HOUR = int(os.environ.get("FORGOT_RATE_PER_HOUR", "3"))

# Firebase envs
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "")
FIREBASE_STORAGE_BUCKET = os.environ.get("FIREBASE_STORAGE_BUCKET", "")
FIREBASE_SERVICE_ACCOUNT_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
FIREBASE_SERVICE_ACCOUNT_BASE64 = os.environ.get("FIREBASE_SERVICE_ACCOUNT_BASE64", "")


def get_db():
    # Note: psycopg2 returns tuples by default; we'll use RealDictCursor where needed.
    conn = psycopg2.connect(DATABASE_URL)
    return conn


def init_db():
    # Email-first schema. Create if missing and ensure case-insensitive unique email index.
    ddl_users = (
        "CREATE TABLE IF NOT EXISTS users ("
        " id BIGSERIAL PRIMARY KEY,"
        " email TEXT NOT NULL,"
        " password_hash TEXT NOT NULL,"
        " email_verified BOOLEAN NOT NULL DEFAULT false,"
        " created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ");"
    )
    idx_email = (
        "CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_idx ON users ((lower(email)));"
    )
    alter_users_last_login = (
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;"
    )
    ddl_prt = (
        "CREATE TABLE IF NOT EXISTS password_reset_tokens ("
        " id BIGSERIAL PRIMARY KEY,"
        " user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
        " token_hash TEXT NOT NULL,"
        " expires_at TIMESTAMPTZ NOT NULL,"
        " used_at TIMESTAMPTZ,"
        " created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ");"
    )
    idx_prt = (
        "CREATE UNIQUE INDEX IF NOT EXISTS prt_token_hash_idx ON password_reset_tokens (token_hash);"
    )
    ddl_events = (
        "CREATE TABLE IF NOT EXISTS events ("
        " id BIGSERIAL PRIMARY KEY,"
        " operator_email TEXT NOT NULL,"
        " device_id TEXT,"
        " event_type TEXT,"
        " storage_path TEXT NOT NULL,"
        " duration_seconds NUMERIC,"
        " created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ");"
    )
    idx_events_email = (
        "CREATE INDEX IF NOT EXISTS events_operator_email_lower_idx ON events ((lower(operator_email)));"
    )
    conn = get_db()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(ddl_users)
            cur.execute(idx_email)
            cur.execute(alter_users_last_login)
            cur.execute(ddl_prt)
            cur.execute(idx_prt)
            cur.execute(ddl_events)
            cur.execute(idx_events_email)
    finally:
        conn.close()


def init_firebase():
    if firebase_admin is None:
        return False
    if firebase_admin._apps:
        return True
    try:
        if FIREBASE_SERVICE_ACCOUNT_JSON:
            info = json.loads(FIREBASE_SERVICE_ACCOUNT_JSON)
        elif FIREBASE_SERVICE_ACCOUNT_BASE64:
            info = json.loads(
                base64.b64decode(FIREBASE_SERVICE_ACCOUNT_BASE64).decode("utf-8")
            )
        else:
            return False
        cred = fb_credentials.Certificate(info)
        firebase_admin.initialize_app(cred, {
            'projectId': FIREBASE_PROJECT_ID or info.get('project_id', ''),
            'storageBucket': FIREBASE_STORAGE_BUCKET,
        })
        return True
    except Exception:
        return False


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


EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.I)


@app.route('/auth/register', methods=['POST'])
def register():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    if not email or not password:
        return jsonify({"error": "email and password required"}), 400
    if not EMAIL_RE.fullmatch(email):
        return jsonify({"error": "invalid email"}), 400
    if len(password) < 6:
        return jsonify({"error": "password must be at least 6 characters"}), 400

    pw_hash = generate_password_hash(password)
    conn = get_db()
    try:
        with conn:
            with conn.cursor() as cur:
                # users table (email-first) or legacy users with username-only schema
                try:
                    cur.execute(
                        "INSERT INTO users (email, password_hash) VALUES (%s, %s)",
                        (email, pw_hash),
                    )
                except pg_errors.UndefinedColumn:
                    # Fallback for legacy schema (not expected after migration)
                    cur.execute(
                        "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                        (email, pw_hash),
                    )
    except (psycopg2.IntegrityError, pg_errors.UniqueViolation):
        return jsonify({"error": "account already exists"}), 409
    finally:
        conn.close()

    token = create_jwt(email)
    return jsonify({"token": token, "email": email})


@app.route('/auth/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    if not email or not password:
        return jsonify({"error": "email and password required"}), 400

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Case-insensitive match
            # Prefer email, but support legacy username if email column missing
            try:
                cur.execute("SELECT email, password_hash FROM users WHERE lower(email) = lower(%s)", (email,))
            except pg_errors.UndefinedColumn:
                cur.execute("SELECT username AS email, password_hash FROM users WHERE lower(username) = lower(%s)", (email,))
            row = cur.fetchone()
        # On successful lookup, update last_login_at
        if row:
            with conn:
                with conn.cursor() as cur2:
                    try:
                        cur2.execute(
                            "UPDATE users SET last_login_at = now() WHERE lower(email) = lower(%s)",
                            (row.get("email") or email,)
                        )
                    except Exception:
                        pass
    finally:
        conn.close()
    if not row or not check_password_hash(row["password_hash"], password):
        return jsonify({"error": "invalid credentials"}), 401

    token = create_jwt(row.get("email") or email)
    return jsonify({"token": token, "email": row.get("email") or email})


@app.route('/auth/me', methods=['GET'])
def me():
    payload = verify_jwt(request.headers.get('Authorization', ''))
    if not payload:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"email": payload.get("sub")})


# In-memory rate limiter for /auth/forgot (email+ip)
_forgot_hits = {}
_FORGOT_WINDOW_SEC = 3600


def _rate_ok(email_lower: str, ip: str) -> bool:
    now = time.time()
    key = (email_lower, ip or "")
    arr = _forgot_hits.get(key, [])
    # prune
    arr = [t for t in arr if now - t < _FORGOT_WINDOW_SEC]
    if len(arr) >= FORGOT_RATE_PER_HOUR:
        _forgot_hits[key] = arr
        return False
    arr.append(now)
    _forgot_hits[key] = arr
    return True


def _send_reset_email(to_email: str, reset_link: str):
    if not (SENDGRID_API_KEY and FROM_EMAIL):
        # Log-only mode if not configured
        return
    payload = {
        "personalizations": [{
            "to": [{"email": to_email}],
            "subject": "Password reset"
        }],
        "from": {"email": FROM_EMAIL, "name": FROM_NAME or ""},
        "content": [{
            "type": "text/plain",
            "value": f"We received a password reset request.\n\nClick to reset: {reset_link}\n\nIf you didn't request this, ignore this email."
        }]
    }
    try:
        requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
    except Exception:
        pass


def _send_email(to_email: str, subject: str, text: str):
    if not (SENDGRID_API_KEY and FROM_EMAIL and to_email):
        return
    payload = {
        "personalizations": [{
            "to": [{"email": to_email}],
            "subject": subject
        }],
        "from": {"email": FROM_EMAIL, "name": FROM_NAME or ""},
        "content": [{
            "type": "text/plain",
            "value": text
        }]
    }
    try:
        requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
    except Exception:
        pass


@app.route('/auth/forgot', methods=['POST'])
def auth_forgot():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip()
    # Always 200; do not reveal whether the account exists
    status = 200
    try:
        if not email or not EMAIL_RE.fullmatch(email):
            return ('', status)
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if not _rate_ok(email.lower(), ip or ""):
            return ('', status)
        # Lookup user id
        conn = get_db()
        try:
            with conn.cursor() as cur:
                try:
                    cur.execute("SELECT id FROM users WHERE lower(email) = lower(%s)", (email,))
                except pg_errors.UndefinedColumn:
                    cur.execute("SELECT id FROM users WHERE lower(username) = lower(%s)", (email,))
                row = cur.fetchone()
            if not row:
                return ('', status)
            user_id = row[0]
            # Create token
            token = secrets.token_urlsafe(48)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_TTL_MIN)
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS password_reset_tokens (
                            id BIGSERIAL PRIMARY KEY,
                            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            token_hash TEXT NOT NULL,
                            expires_at TIMESTAMPTZ NOT NULL,
                            used_at TIMESTAMPTZ,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                        )
                        """
                    )
                    cur.execute(
                        "CREATE UNIQUE INDEX IF NOT EXISTS prt_token_hash_idx ON password_reset_tokens (token_hash)"
                    )
                    cur.execute(
                        "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
                        (user_id, token_hash, expires_at),
                    )
            # Build link using query param on home: /?reset_token=
            if FRONTEND_BASE_URL:
                link = f"{FRONTEND_BASE_URL.rstrip('/')}/?reset_token={token}"
                _send_reset_email(email, link)
        finally:
            conn.close()
    except Exception:
        # Do not leak errors
        return ('', status)
    return ('', status)


@app.route('/auth/reset', methods=['POST'])
def auth_reset():
    data = request.get_json() or {}
    token = (data.get("token") or "").strip()
    new_password = data.get("password") or ""
    if not token or len(new_password) < 6:
        return jsonify({"error": "invalid token or password"}), 400
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, user_id, expires_at, used_at FROM password_reset_tokens WHERE token_hash = %s",
                (token_hash,),
            )
            row = cur.fetchone()
        if not row or row.get('used_at') or row.get('expires_at') < now:
            return jsonify({"error": "invalid or expired token"}), 400
        pw_hash = generate_password_hash(new_password)
        with conn:
            with conn.cursor() as cur:
                # Update password
                try:
                    cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (pw_hash, row['user_id']))
                except pg_errors.UndefinedColumn:
                    cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (pw_hash, row['user_id']))
                # Mark token used
                cur.execute("UPDATE password_reset_tokens SET used_at = %s WHERE id = %s", (now, row['id']))
        return jsonify({"ok": True})
    finally:
        conn.close()


# Events: upload (device/operator) and list (operator)
@app.route('/events/upload', methods=['POST'])
def events_upload():
    payload = verify_jwt(request.headers.get('Authorization', ''))
    if not payload:
        return jsonify({"error": "unauthorized"}), 401
    operator_email = (payload.get('sub') or '').strip()
    if not operator_email:
        return jsonify({"error": "unauthorized"}), 401

    if not init_firebase():
        return jsonify({"error": "storage not configured"}), 500

    if 'file' not in request.files:
        return jsonify({"error": "file missing"}), 400
    f = request.files['file']
    if not f or f.filename == '':
        return jsonify({"error": "invalid file"}), 400

    event_type = (request.form.get('event_type') or '').strip() or None
    device_id = (request.form.get('device_id') or '').strip() or None
    try:
        duration_seconds = float(request.form.get('duration_seconds')) if request.form.get('duration_seconds') else None
    except Exception:
        duration_seconds = None

    # Build storage path: events/<email>/<timestamp>_<filename>
    safe_email = operator_email.replace('/', '_').lower()
    ts = int(time.time())
    base_name = f.filename.rsplit('/', 1)[-1]
    storage_path = f"events/{safe_email}/{ts}_{base_name}"

    try:
        bucket = fb_storage.bucket(FIREBASE_STORAGE_BUCKET) if FIREBASE_STORAGE_BUCKET else fb_storage.bucket()
        blob = bucket.blob(storage_path)
        blob.upload_from_file(f.stream, content_type=f.mimetype or 'video/mp4')
    except Exception as e:
        return jsonify({"error": "upload failed"}), 500

    # Save DB record
    conn = get_db()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO events (operator_email, device_id, event_type, storage_path, duration_seconds)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (operator_email, device_id, event_type, storage_path, duration_seconds),
                )
                row = cur.fetchone()
                ev_id = row[0] if row else None
    finally:
        conn.close()

    # Email alert to most recent logged-in operator
    recent_email = None
    conn2 = get_db()
    try:
        with conn2.cursor() as cur:
            try:
                cur.execute("SELECT email FROM users WHERE last_login_at IS NOT NULL ORDER BY last_login_at DESC LIMIT 1")
                r = cur.fetchone()
                if r:
                    recent_email = r[0]
            except Exception:
                recent_email = None
    finally:
        conn2.close()

    if recent_email and FRONTEND_BASE_URL:
        tz_chicago = ZoneInfo('America/Chicago')
        ts = datetime.now(tz_chicago).strftime('%Y-%m-%d %H:%M:%S %Z')
        subj = f"New event: {event_type or 'event'}"
        body = (
            f"A new event was uploaded.\n\n"
            f"Type: {event_type or 'event'}\n"
            f"Device: {device_id or 'unknown'}\n"
            f"Time: {ts}\n\n"
            f"View it on the Events page: {FRONTEND_BASE_URL}"
        )
        _send_email(recent_email, subj, body)

    return jsonify({"ok": True, "id": ev_id, "path": storage_path})


@app.route('/events', methods=['GET'])
def events_list():
    payload = verify_jwt(request.headers.get('Authorization', ''))
    if not payload:
        return jsonify({"error": "unauthorized"}), 401
    operator_email = (payload.get('sub') or '').strip()
    if not operator_email:
        return jsonify({"error": "unauthorized"}), 401

    if not init_firebase():
        return jsonify({"error": "storage not configured"}), 500

    limit = 50
    try:
        if request.args.get('limit'):
            limit = min(100, max(1, int(request.args['limit'])))
    except Exception:
        pass

    conn = get_db()
    rows = []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, operator_email, device_id, event_type, storage_path, duration_seconds, created_at
                FROM events
                WHERE lower(operator_email) = lower(%s)
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (operator_email, limit),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()

    bucket = fb_storage.bucket(FIREBASE_STORAGE_BUCKET) if FIREBASE_STORAGE_BUCKET else fb_storage.bucket()
    tz_chicago = ZoneInfo('America/Chicago')
    out = []
    for r in rows:
        blob = bucket.blob(r['storage_path'])
        try:
            url = blob.generate_signed_url(expiration=timedelta(hours=1))
        except Exception:
            url = None
        created = r.get('created_at')
        created_iso = created.astimezone(timezone.utc).isoformat() if created else None
        created_local = created.astimezone(tz_chicago).isoformat() if created else None
        out.append({
            "id": r['id'],
            "event_type": r.get('event_type'),
            "created_at": created_iso,
            "created_local": created_local,
            "duration_seconds": float(r['duration_seconds']) if r.get('duration_seconds') is not None else None,
            "device_id": r.get('device_id'),
            "url": url,
        })

    return jsonify({"items": out})


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
