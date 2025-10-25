import { useEffect, useRef, useState } from "react";
import { Room, createLocalTracks, RoomEvent } from "livekit-client";

const LIVEKIT_URL = import.meta.env.VITE_LIVEKIT_URL;
const TOKEN_ENDPOINT = import.meta.env.VITE_TOKEN_ENDPOINT;
const API_BASE = import.meta.env.VITE_API_URL || (TOKEN_ENDPOINT ? new URL(TOKEN_ENDPOINT).origin : "");
const ROOM_NAME = "playground-01"; // use one per site/camera

export default function Live() {
  const [room, setRoom] = useState(null);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");
  const [devices, setDevices] = useState([]);
  const [deviceId, setDeviceId] = useState("");
  const [authToken, setAuthToken] = useState(() => localStorage.getItem('authToken') || "");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
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
      headers: {
        "Content-Type": "application/json",
        ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
      },
      body: JSON.stringify({ room: ROOM_NAME, identity, publish }),
    });
    const { token } = await res.json();
    return token;
  }

  async function doLogin(e) {
    e?.preventDefault?.();
    setError("");
    try {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      });
      if (!res.ok) {
        const msg = await res.json().catch(() => ({}));
        throw new Error(msg.error || `Login failed (${res.status})`);
      }
      const data = await res.json();
      localStorage.setItem('authToken', data.token);
      setAuthToken(data.token);
      setUsername(""); setPassword("");
    } catch (e) {
      setError(e.message || String(e));
    }
  }

  async function doRegister(e) {
    e?.preventDefault?.();
    setError("");
    try {
      const res = await fetch(`${API_BASE}/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      });
      if (!res.ok) {
        const msg = await res.json().catch(() => ({}));
        throw new Error(msg.error || `Register failed (${res.status})`);
      }
      const data = await res.json();
      localStorage.setItem('authToken', data.token);
      setAuthToken(data.token);
      setUsername(""); setPassword("");
    } catch (e) {
      setError(e.message || String(e));
    }
  }

  function logout() {
    localStorage.removeItem('authToken');
    setAuthToken("");
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
    <div style={{ minHeight: "100vh", background: "#0f172a", color: "white", width: "100%" }}>
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: 24 }}>
        <h1 style={{ marginTop: 0, marginBottom: 12 }}>Live</h1>
        <p style={{ marginTop: 0 }}>Status: {status}</p>
      {!authToken ? (
        <form onSubmit={doLogin} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
          <input placeholder="username" value={username} onChange={(e) => setUsername(e.target.value)} />
          <input placeholder="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          <button type="submit">Login</button>
          <button type="button" onClick={doRegister}>Register</button>
        </form>
      ) : (
        <div style={{ marginBottom: 12 }}>
          <span>Logged in</span>
          <button style={{ marginLeft: 8 }} onClick={logout}>Logout</button>
        </div>
      )}
      <div style={{ display: "flex", gap: 16, marginBottom: 16, flexWrap: "wrap" }}>
        <button onClick={() => connectRoom(true)} disabled={status !== "idle" || !authToken}>Go Live (Publish)</button>
        <button onClick={() => connectRoom(false)} disabled={status !== "idle" || !authToken}>View Live</button>
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

      {/* Videos grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 24 }}>
        <div>
          <h3 style={{ marginTop: 0 }}>Local (publisher preview)</h3>
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            style={{ width: "100%", display: "block", background: "#111", borderRadius: 8, aspectRatio: "16 / 9" }}
          />
        </div>
        <div>
          <h3 style={{ marginTop: 0 }}>Remote (viewer)</h3>
          <video
            ref={remoteVideoRef}
            autoPlay
            playsInline
            style={{ width: "100%", display: "block", background: "#111", borderRadius: 8, aspectRatio: "16 / 9" }}
          />
        </div>
      </div>
      </div>
    </div>
  );
}
