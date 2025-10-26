// frontend/src/App.jsx
import { useMemo, useState } from "react";
import Live from "./Live.jsx";
import Events from "./Events.jsx";

const TOKEN_ENDPOINT = import.meta.env.VITE_TOKEN_ENDPOINT;
const API_BASE = import.meta.env.VITE_API_URL || (TOKEN_ENDPOINT ? new URL(TOKEN_ENDPOINT).origin : "");

export default function App() {
  const [view, setView] = useState("home"); // 'home' | 'live' | 'events'
  const [authToken, setAuthToken] = useState(() => localStorage.getItem('authToken') || "");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

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

  if (view === "live") {
    return <Live onBack={() => setView("home")} />;
  }
  if (view === "events") {
    return <Events onBack={() => setView("home")} />;
  }

  return (
    <div style={{ minHeight: "100vh", background: "#0f172a", color: "white", width: "100%" }}>
      <div style={{ maxWidth: 900, margin: "0 auto", padding: 24 }}>
        <h1 style={{ marginTop: 0, marginBottom: 12 }}>Welcome</h1>
        <p style={{ marginTop: 0, opacity: 0.9 }}>Choose an option below. Login is required to publish or view.</p>

        {!authToken ? (
          <form onSubmit={doLogin} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
            <input placeholder="username" value={username} onChange={(e) => setUsername(e.target.value)} />
            <input placeholder="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            <button type="submit">Login</button>
            <button type="button" onClick={doRegister}>Register</button>
          </form>
        ) : (
          <div style={{ marginBottom: 16 }}>
            <span>Logged in</span>
            <button style={{ marginLeft: 8 }} onClick={logout}>Logout</button>
          </div>
        )}
        {error && (
          <div style={{ color: '#fca5a5', marginBottom: 12 }}>
            Error: {error}
          </div>
        )}

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16 }}>
          <button onClick={() => setView("live")} style={{ height: 120, fontSize: 18 }}>Live Streaming</button>
          <button onClick={() => setView("events")} style={{ height: 120, fontSize: 18 }}>Events</button>
        </div>
      </div>
    </div>
  );
}
