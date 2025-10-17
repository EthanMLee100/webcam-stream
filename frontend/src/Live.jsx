import { useEffect, useRef, useState } from "react";
import { Room, RemoteParticipant, createLocalTracks } from "livekit-client";

const LIVEKIT_URL = import.meta.env.VITE_LIVEKIT_URL;
const TOKEN_ENDPOINT = import.meta.env.VITE_TOKEN_ENDPOINT;
const ROOM_NAME = "playground-01"; // use one per site/camera

export default function Live() {
  const [room, setRoom] = useState(null);
  const [status, setStatus] = useState("idle");
  const videoRef = useRef(null);
  const remoteVideoRef = useRef(null);

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

    // Identity can be anything unique for the session
    const identity = publish ? `pub-${Date.now()}` : `sub-${Date.now()}`;
    const token = await getToken({ publish, identity });

    const r = await Room.connect(LIVEKIT_URL, token, {
      // auto-subscribe to tracks on join
      autoSubscribe: !publish,
    });

    setRoom(r);

    // Handle remote tracks (viewer side)
    r.on("trackSubscribed", (track, publication, participant) => {
      if (track.kind === "video" && remoteVideoRef.current) {
        track.attach(remoteVideoRef.current);
      }
    });

    // Publisher: create and publish local video
    if (publish) {
      const tracks = await createLocalTracks({ video: true, audio: false });
      for (const t of tracks) {
        await r.localParticipant.publishTrack(t);
        if (t.kind === "video" && videoRef.current) {
          // show local preview
          t.attach(videoRef.current);
        }
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
