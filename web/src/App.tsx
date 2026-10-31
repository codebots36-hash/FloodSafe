import { lazy, Suspense, useEffect, useState } from 'react'
import './App.css'
import { sendSos } from './api'
import FloodGuidance from './components/FloodGuidance'
import OfficialWarnings from './components/OfficialWarnings'
import ResponderDashboard from './components/ResponderDashboard'
import NotificationSettings from './components/NotificationSettings'
import ReplayMode from './components/ReplayMode'
import PreviewBadge from './components/PreviewBadge'

const RiskMap = lazy(() => import('./components/RiskMap'))

// No status feed is connected yet; listing the metrics without values keeps
// the layout without any figure that could be read as a reading.
const statusMetrics = [
  'Road network nodes covered',
  'Shelters & hospitals mapped',
  'Active hazard reports right now',
  'Rainfall — currently wettest station',
  'Rainfall — currently driest station',
]

const SOS_STORAGE_KEY = 'floodsafe-sos-contact'

function loadSosContact() {
  try {
    const saved = JSON.parse(localStorage.getItem(SOS_STORAGE_KEY) ?? '{}')
    return { name: String(saved.name ?? ''), numbers: String(saved.numbers ?? '') }
  } catch {
    return { name: '', numbers: '' }
  }
}

function getPosition() {
  return new Promise<GeolocationPosition>((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error('This browser cannot share your location.'))
      return
    }
    navigator.geolocation.getCurrentPosition(resolve, () =>
      reject(new Error('Could not get your location. Allow location access and try again.')),
      { enableHighAccuracy: true, timeout: 15000 },
    )
  })
}

function openSms(numbers: string[], body: string) {
  window.location.href = `sms:${numbers.join(',')}?body=${encodeURIComponent(body)}`
}

const MAP_HASH = '#/map'

function MapPage() {
  return (
    <div className="map-page">
      <header className="map-page-bar">
        <a href="#" onClick={(event) => { event.preventDefault(); window.location.hash = '' }}>
          ← FloodSafe
        </a>
        <strong>Flood-aware map tool</strong>
        <span className="map-tag">Surveyed data</span>
      </header>
      <Suspense fallback={<p role="status">Loading map…</p>}>
        <RiskMap />
      </Suspense>
    </div>
  )
}

function App() {
  const [onMapPage, setOnMapPage] = useState(() => window.location.hash === MAP_HASH)

  useEffect(() => {
    const sync = () => setOnMapPage(window.location.hash === MAP_HASH)
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [])

  useEffect(() => {
    document.title = onMapPage ? 'FloodSafe — Map tool' : 'FloodSafe'
  }, [onMapPage])

  return onMapPage ? <MapPage /> : <Portal />
}

function Portal() {
  const [sosOpen, setSosOpen] = useState(false)
  const [sosName, setSosName] = useState(() => loadSosContact().name)
  const [sosNumbers, setSosNumbers] = useState(() => loadSosContact().numbers)
  const [sosBusy, setSosBusy] = useState(false)
  const [sosMessage, setSosMessage] = useState<string | null>(null)

  const handleSos = async (event: React.FormEvent) => {
    event.preventDefault()
    const numbers = sosNumbers
      .split(/[,\n]/)
      .map((value) => value.trim().replace(/[\s-]/g, ''))
      .filter(Boolean)
    const name = sosName.trim()

    if (!name || numbers.length === 0) {
      setSosMessage('Enter your name and at least one phone number with country code, e.g. +91…')
      return
    }
    if (numbers.some((value) => !/^\+\d{8,15}$/.test(value))) {
      setSosMessage('Phone numbers need the country code, for example +919876543210.')
      return
    }

    try {
      localStorage.setItem(SOS_STORAGE_KEY, JSON.stringify({ name, numbers: sosNumbers }))
    } catch {
      // Remembering the contact is optional.
    }

    setSosBusy(true)
    setSosMessage(null)

    let position: GeolocationPosition
    try {
      position = await getPosition()
    } catch (err) {
      setSosMessage(err instanceof Error ? err.message : 'Could not get your location.')
      setSosBusy(false)
      return
    }

    const lat = Number(position.coords.latitude.toFixed(5))
    const lon = Number(position.coords.longitude.toFixed(5))

    try {
      const result = await sendSos({ name, lat, lon, numbers })
      if (result.status === 'sent') {
        setSosMessage('SOS sent. Your contacts have been texted your location.')
      } else {
        setSosMessage('Opening your SMS app with the message ready to send.')
        openSms(numbers, result.sms_body)
      }
    } catch {
      // The API is unreachable: still let the phone send the text itself.
      const link = `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=16/${lat}/${lon}`
      setSosMessage('Could not reach FloodSafe. Opening your SMS app instead.')
      openSms(numbers, `SOS from ${name} via FloodSafe. I need help. My location: ${link}`)
    } finally {
      setSosBusy(false)
    }
  }

  return (
    <main className="dashboard">
      <div className="utility-bar">
        <span>FloodSafe Portal — Flood-Aware Road Advisory Service</span>
        <div>
          <button type="button">English</button>
          <span>|</span>
          <button type="button">हिंदी</button>
          <span>Text size:</span>
          <button type="button">A-</button>
          <button type="button">A</button>
          <button type="button">A+</button>
        </div>
      </div>

      <header className="portal-header">
        <a className="portal-brand" href="#">
          <span className="portal-brand-icon">⚑</span>
          <span>
            <strong>FloodSafe</strong>
            <small>Flood-Aware Road Advisory — Uttarakhand</small>
          </span>
        </a>

        <nav className="portal-nav">
          <a href="#live-status">System Status</a>
          <a href="#guidance">Flood Guidance</a>
          <a href="#ffgs">FFGS</a>
          <a href="#services">Services</a>
          <a href="#hazard-reports">Hazard Reports</a>
          <a className="nav-cta" href={MAP_HASH} target="_blank" rel="noopener">Open Map Tool</a>
        </nav>
      </header>

      <div className="portal-content">
        <div className="advisory-banner">
          <strong>⚠ Public Advisory:</strong> Road conditions can change rapidly during monsoon season.
          Always verify local conditions before travel.
        </div>

        <section className="hero">
          <div className="hero-copy">
            <h2>Fastest isn't always <span>safe.</span></h2>
            <p>
              FloodSafe advises road routes across Uttarakhand using a
              georeferenced flash-flood hazard classification, live rainfall
              data, and citizen-reported hazards — instead of distance alone.
            </p>
            <div className="hero-actions">
              <a className="primary-button" href={MAP_HASH} target="_blank" rel="noopener">Open the Map Tool →</a>
              <a className="secondary-button" href="#live-status">View System Status</a>
            </div>
            <button
              className="sos-button"
              type="button"
              aria-expanded={sosOpen}
              onClick={() => setSosOpen((open) => !open)}
            >
              <span className="sos-circle">SOS</span>
              <strong>In danger? Text your family your location</strong>
            </button>
            {sosOpen && (
              <form className="sos-form" onSubmit={handleSos}>
                <label>
                  Your name
                  <input
                    value={sosName}
                    onChange={(event) => setSosName(event.target.value)}
                    autoComplete="name"
                  />
                </label>
                <label>
                  Phone numbers to text (comma separated, with country code)
                  <input
                    value={sosNumbers}
                    onChange={(event) => setSosNumbers(event.target.value)}
                    inputMode="tel"
                    placeholder="+919876543210, +911234567890"
                  />
                </label>
                <button type="submit" disabled={sosBusy}>
                  {sosBusy ? 'Getting your location…' : 'Send my location now'}
                </button>
                {sosMessage && <p role="status">{sosMessage}</p>}
              </form>
            )}
          </div>

          <div className="route-illustration">
            <p>Illustrative example — hover or tap a route below</p>
            <svg viewBox="0 0 460 340" role="img" aria-label="Illustration comparing fastest and safest routes">
              <path className="route-safe" d="M42 278 C100 250 110 265 150 280 S220 300 260 220 S220 130 280 90 S370 35 420 35" />
              <path className="route-fast" d="M42 278 C100 205 145 195 190 215 S270 220 310 165 S385 90 420 35" />
              <circle cx="42" cy="278" r="7" />
              <circle cx="420" cy="35" r="7" />
            </svg>
            <div className="route-legend">
              <span><i className="legend-safe" /> Safest route</span>
              <span><i className="legend-fast" /> Fastest route</span>
            </div>
            <small>Hover or tap either route to trace it live</small>
          </div>
        </section>

        <section id="live-status" className="portal-section">
          <h3>SYSTEM STATUS</h3>
          <PreviewBadge />
          <div className="status-table">
            <div className="table-head"><span>Metric</span><span>Value</span></div>
            {statusMetrics.map((metric) => (
              <div key={metric}>
                <span>{metric}</span>
                <strong className="preview-value">Not connected</strong>
              </div>
            ))}
          </div>
          <div className="hazard-legend">
            <span><i className="hazard-low" /> LOW hazard</span>
            <span><i className="hazard-moderate" /> MODERATE hazard</span>
            <span><i className="hazard-significant" /> SIGNIFICANT hazard</span>
            <span><i className="hazard-extreme" /> EXTREME hazard</span>
          </div>
          <p className="data-note">Layout preview — no status feed is connected, so no figures are shown.</p>
        </section>

        <section id="guidance" className="portal-section">
          <FloodGuidance />
        </section>

        <section id="map-tool" className="portal-section">
          <div className="section-heading">
            <div>
              <h3>Flood-aware map tool</h3>
              <p>Road and area hazard overview</p>
            </div>
            <span className="map-tag">Surveyed data</span>
          </div>
          <p>
            Route planning, hazard layers, shelters, hospitals and community
            reports open in a full-screen map in a new tab.
          </p>
          <a className="primary-button" href={MAP_HASH} target="_blank" rel="noopener">
            Open the full-screen map →
          </a>
        </section>

        <section id="ffgs" className="portal-section">
          <h2>Flash Flood Guidance</h2>
          <p>Check rainfall conditions and local risk before travelling.</p>
        </section>

        <section id="services" className="portal-section">
          <ResponderDashboard />
          <NotificationSettings />
          <ReplayMode />
        </section>

        <section id="hazard-reports" className="portal-section">
          <OfficialWarnings />
        </section>

        <footer className="portal-footer">
          <span>FloodSafe — Flood-Aware Road Advisory Service for Uttarakhand.</span>
          <span>Use official guidance during emergencies.</span>
        </footer>
      </div>
    </main>
  )
}

export default App

