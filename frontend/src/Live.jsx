import { useEffect, useRef, useState } from "react";
import { Room, createLocalTracks, RoomEvent } from "livekit-client";

const LIVEKIT_URL = import.meta.env.VITE_LIVEKIT_URL;
const TOKEN_ENDPOINT = import.meta.env.VITE_TOKEN_ENDPOINT;
const ROOM_NAME = "playground-01"; // use one per site/camera

export default function Live() {
  const [room, setRoom] = useState(null);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");
  const [devices, setDevices] = useState([]);
  const [deviceId, setDeviceId] = useState("");
  const videoRef = useRef(null);
  const remoteVideoRef = useRef(null);

  async function refreshDevices() {
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      setDevices(all.filter((d) => d.kind === "videoinput"));
    } catch (e) {
      // labels may be empty until permission is granted
    }
  }

  useEffect(() => {
    if (navigator.mediaDevices?.enumerateDevices) {
      refreshDevices();
    }
  }, []);

  async function getToken({ publish, identity }) {
    const res = await fetch(TOKEN_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ room: ROOM_NAME, identity, publish }),
    });
    const { token } = await res.json();
    return token;
  }

  async function connectRoom(publish) {
    setStatus("connecting");
    setError("");

    // Identity can be anything unique for the session
    const identity = publish ? `pub-${Date.now()}` : `sub-${Date.now()}`;
    const token = await getToken({ publish, identity });

    const r = new Room({ autoSubscribe: !publish });
    await r.connect(LIVEKIT_URL, token);

    setRoom(r);

    // Handle remote tracks (viewer side)
    r.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
      if (track.kind === "video" && remoteVideoRef.current) {
        track.attach(remoteVideoRef.current);
      }
    });

    // Publisher: create and publish local video
    if (publish) {
      try {
        const tracks = await createLocalTracks({
          video: {
            deviceId: deviceId || undefined,
            facingMode: "user",
            // Use modest defaults that most cams support
            resolution: { width: 1280, height: 720 },
          },
          audio: false,
        });
        for (const t of tracks) {
          await r.localParticipant.publishTrack(t);
          if (t.kind === "video" && videoRef.current) {
            // show local preview
            t.attach(videoRef.current);
          }
        }
      } catch (e) {
        // Surface helpful guidance for NotReadableError / Overconstrained
        const msg = (e && e.name === 'NotReadableError')
          ? 'Camera is busy or unavailable. Close other apps/tabs using the camera and try again.'
          : (e && e.name === 'OverconstrainedError')
            ? 'Camera does not support requested resolution. Try a different camera or lower resolution.'
            : (e && e.message) || String(e);
        setError(msg);
        setStatus("idle");
        // refresh device list in case labels became available after permission
        refreshDevices();
        return;
      }
    }

    setStatus(publish ? "live (publishing)" : "live (viewing)");
  }

  async function disconnectRoom() {
    if (room) {
      room.disconnect();
      setRoom(null);
    }
    setStatus("idle");
    setError("");
    // Clean up video elements
    if (videoRef.current) videoRef.current.srcObject = null;
    if (remoteVideoRef.current) remoteVideoRef.current.srcObject = null;
  }

  return (
    <div style={{ padding: 16, color: "white", background: "#0f172a", minHeight: "100vh" }}>
      <h1>Live</h1>
      <p>Status: {status}</p>
      <div style={{ display: "flex", gap: 16, marginBottom: 12, flexWrap: "wrap" }}>
        <button onClick={() => connectRoom(true)} disabled={status !== "idle"}>Go Live (Publish)</button>
        <button onClick={() => connectRoom(false)} disabled={status !== "idle"}>View Live</button>
        <button onClick={disconnectRoom} disabled={status === "idle"}>Stop</button>
      </div>

      {/* Camera selection and errors */}
      <div style={{ marginBottom: 12 }}>
        <label style={{ marginRight: 8 }}>Camera:</label>
        <select value={deviceId} onChange={(e) => setDeviceId(e.target.value)}>
          <option value="">Default</option>
          {devices.map((d, i) => (
            <option key={d.deviceId || i} value={d.deviceId}>
              {d.label || `Camera ${i + 1}`}
            </option>
          ))}
        </select>
        <button style={{ marginLeft: 8 }} onClick={refreshDevices}>Refresh Cameras</button>
      </div>

      {error && (
        <div style={{ color: '#fca5a5', marginBottom: 12 }}>
          Error: {error}
        </div>
      )}

      {/* Local preview for publisher */}
      <div style={{ marginBottom: 16 }}>
        <h3>Local (publisher preview)</h3>
        <video ref={videoRef} autoPlay playsInline muted style={{ width: 480, background: "#111", borderRadius: 8 }} />
      </div>

      {/* Remote for viewer */}
      <div>
        <h3>Remote (viewer)</h3>
        <video ref={remoteVideoRef} autoPlay playsInline style={{ width: 480, background: "#111", borderRadius: 8 }} />
      </div>
    </div>
  );
}
