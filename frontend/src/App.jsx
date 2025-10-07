import { useEffect, useState } from "react";

export default function App() {
  const [apiUrl, setApiUrl] = useState(import.meta.env.VITE_API_URL || "");
  const [isPlaying, setIsPlaying] = useState(true);

  const streamUrl = isPlaying ? `${apiUrl}/video_feed` : "";

  useEffect(() => {
    if (!apiUrl) {
      console.warn("VITE_API_URL not set. Create .env with VITE_API_URL=http://<ip>:5000");
    }
  }, [apiUrl]);

  return (
    <div style={{
      minHeight: "100vh",
      display: "grid",
      placeItems: "center",
      background: "#0f172a",
      color: "white",
      padding: 24
    }}>
      <div style={{ maxWidth: 960, width: "100%" }}>
        <h1 style={{ marginBottom: 12, fontSize: 28 }}>Webcam Live Stream</h1>

        <div style={{
          display: "flex",
          gap: 12,
          alignItems: "center",
          marginBottom: 12,
          flexWrap: "wrap"
        }}>
          <input
            value={apiUrl}
            onChange={(e) => setApiUrl(e.target.value)}
            placeholder="http://192.168.x.x:5000"
            style={{
              flex: 1,
              padding: "10px 12px",
              borderRadius: 10,
              border: "1px solid #334155",
              background: "#0b1220",
              color: "white"
            }}
          />
          <button
            onClick={() => setIsPlaying((v) => !v)}
            style={{
              padding: "10px 16px",
              borderRadius: 10,
              border: "1px solid #334155",
              background: isPlaying ? "#1e293b" : "#14532d",
              color: "white",
              cursor: "pointer"
            }}
          >
            {isPlaying ? "Pause" : "Play"}
          </button>
        </div>

        <div style={{
          width: "100%",
          background: "#0b1220",
          border: "1px solid #334155",
          borderRadius: 16,
          padding: 12
        }}>
          {streamUrl ? (
            <img
              src={streamUrl}
              alt="Live Stream"
              style={{ width: "100%", height: "auto", borderRadius: 12 }}
            />
          ) : (
            <div style={{ padding: 24, textAlign: "center", opacity: 0.8 }}>
              Stream paused
            </div>
          )}
        </div>

        <p style={{ marginTop: 12, opacity: 0.8 }}>
          Using MJPEG over HTTP. Ensure the Flask backend is running and reachable.
        </p>
      </div>
    </div>
  );
}
