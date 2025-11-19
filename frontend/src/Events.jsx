import { useEffect, useMemo, useState } from 'react'

const TOKEN_ENDPOINT = import.meta.env.VITE_TOKEN_ENDPOINT;
const API_BASE = TOKEN_ENDPOINT ? new URL(TOKEN_ENDPOINT).origin : (import.meta.env.VITE_API_URL || "");

export default function Events({ onBack }) {
  const [items, setItems] = useState([])
  const [error, setError] = useState("")
  const authToken = localStorage.getItem('authToken') || ""
  const formatter = useMemo(() => new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Chicago',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit'
  }), [])

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
                {(() => {
                  const iso = it.created_local || it.created_at
                  const formatted = iso ? formatter.format(new Date(iso)) : '—'
                  return (
                    <>
                      <strong>{it.event_type || 'event'}</strong> · {formatted}
                    </>
                  )
                })()}
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
