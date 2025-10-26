export default function Events({ onBack }) {
  return (
    <div style={{ minHeight: '100vh', background: '#0f172a', color: 'white', width: '100%' }}>
      <div style={{ maxWidth: 900, margin: '0 auto', padding: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <h1 style={{ marginTop: 0, marginBottom: 12 }}>Events</h1>
          {typeof onBack === 'function' && (
            <button onClick={onBack}>Back</button>
          )}
        </div>
        <p style={{ opacity: 0.9 }}>Coming soon.</p>
      </div>
    </div>
  );
}

