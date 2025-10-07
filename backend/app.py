from flask import Flask, Response
from flask_cors import CORS
import cv2

app = Flask(__name__)
CORS(app)

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
    app.run(host='0.0.0.0', port=5000, debug=False)
