// frontend/src/App.jsx
import { useMemo, useState } from "react";
import Live from "./Live.jsx";
import Events from "./Events.jsx";

const TOKEN_ENDPOINT = import.meta.env.VITE_TOKEN_ENDPOINT;
// Prefer origin of TOKEN_ENDPOINT to avoid mismatched API URLs
const API_BASE = TOKEN_ENDPOINT ? new URL(TOKEN_ENDPOINT).origin : (import.meta.env.VITE_API_URL || "");

export default function App() {
  const [view, setView] = useState("home"); // 'home' | 'live' | 'events'
  const [authToken, setAuthToken] = useState(() => localStorage.getItem('authToken') || "");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [resetToken, setResetToken] = useState(() => new URLSearchParams(window.location.search).get('reset_token') || "");

  async function doLogin(e) {
    e?.preventDefault?.();
    setError("");
    setInfo("");
    try {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
      });
      if (!res.ok) {
        const msg = await res.json().catch(() => ({}));
        throw new Error(msg.error || `Login failed (${res.status})`);
      }
      const data = await res.json();
      localStorage.setItem('authToken', data.token);
      setAuthToken(data.token);
      setEmail(""); setPassword("");
    } catch (e) {
      setError((e && e.message) ? `${e.message} [API: ${API_BASE}]` : `Request failed [API: ${API_BASE}]`);
    }
  }

  async function doRegister(e) {
    e?.preventDefault?.();
    setError("");
    setInfo("");
    try {
      const res = await fetch(`${API_BASE}/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
      });
      if (!res.ok) {
        const msg = await res.json().catch(() => ({}));
        throw new Error(msg.error || `Register failed (${res.status})`);
      }
      const data = await res.json();
      localStorage.setItem('authToken', data.token);
      setAuthToken(data.token);
      setEmail(""); setPassword("");
    } catch (e) {
      setError((e && e.message) ? `${e.message} [API: ${API_BASE}]` : `Request failed [API: ${API_BASE}]`);
    }
  }

  async function doForgot(e) {
    e?.preventDefault?.();
    setError(""); setInfo("");
    try {
      const res = await fetch(`${API_BASE}/auth/forgot`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email })
      });
      // Always 200; show generic info
      setInfo('If that email exists, a reset link was sent.');
    } catch (e) {
      setError('Unable to request reset right now.');
    }
  }

  async function doReset(e) {
    e?.preventDefault?.();
    setError(""); setInfo("");
    try {
      const res = await fetch(`${API_BASE}/auth/reset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: resetToken, password })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        throw new Error(data.error || 'Reset failed');
      }
      setInfo('Password reset. You can now log in.');
      setPassword("");
      // Clear token from URL
      const url = new URL(window.location.href);
      url.searchParams.delete('reset_token');
      window.history.replaceState({}, '', url);
      setResetToken('');
    } catch (e) {
      setError(e.message || 'Reset failed');
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
            <input placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            <input placeholder="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            <button type="submit">Login</button>
            <button type="button" onClick={doRegister}>Register</button>
            <button type="button" onClick={doForgot}>Forgot password</button>
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
        {info && (
          <div style={{ color: '#a7f3d0', marginBottom: 12 }}>
            {info}
          </div>
        )}

        {resetToken && (
          <form onSubmit={doReset} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
            <input placeholder="new password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            <button type="submit">Set new password</button>
          </form>
        )}

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16 }}>
          <button onClick={() => setView("live")} style={{ height: 120, fontSize: 18 }}>Live Streaming</button>
          <button onClick={() => setView("events")} style={{ height: 120, fontSize: 18 }}>Events</button>
        </div>
      </div>
    </div>
  );
}
