import { useEffect, useState } from 'react'

const TOKEN_ENDPOINT = import.meta.env.VITE_TOKEN_ENDPOINT;
const API_BASE = TOKEN_ENDPOINT ? new URL(TOKEN_ENDPOINT).origin : (import.meta.env.VITE_API_URL || "");

export default function Events({ onBack }) {
  const [items, setItems] = useState([])
  const [error, setError] = useState("")
  const authToken = localStorage.getItem('authToken') || ""

  async function load() {
    setError("")
    try {
      const res = await fetch(`${API_BASE}/events`, {
        headers: { Authorization: `Bearer ${authToken}` }
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || `Failed (${res.status})`)
      setItems(data.items || [])
    } catch (e) {
      setError(e.message || 'Failed to load events')
    }
  }

  useEffect(() => { load() }, [])

  return (
    <div style={{ minHeight: '100vh', background: '#0f172a', color: 'white', width: '100%' }}>
      <div style={{ maxWidth: 1000, margin: '0 auto', padding: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <h1 style={{ marginTop: 0, marginBottom: 12 }}>Events</h1>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button onClick={load}>Refresh</button>
            {typeof onBack === 'function' && (
              <button onClick={onBack}>Back</button>
            )}
          </div>
        </div>
        {error && <div style={{ color: '#fca5a5', marginBottom: 12 }}>Error: {error}</div>}
        {items.length === 0 && <div style={{ opacity: 0.9 }}>No events yet.</div>}

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
          {items.map((it) => (
            <div key={it.id} style={{ background: '#111', padding: 12, borderRadius: 8 }}>
              <div style={{ marginBottom: 8, fontSize: 14, opacity: 0.9 }}>
                <strong>{it.event_type || 'event'}</strong> · {it.created_at?.replace('T', ' ').replace('Z','')}
              </div>
              {it.url ? (
                <video controls style={{ width: '100%', borderRadius: 6 }} src={it.url} />
              ) : (
                <div style={{ padding: 24, textAlign: 'center' }}>No preview</div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
