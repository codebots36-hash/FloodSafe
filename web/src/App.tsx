import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import './App.css'
import { getHealth, getHospitals, getReports, getShelters, getZones, sendSos } from './api'
import FloodGuidance from './components/FloodGuidance'
import OfficialWarnings from './components/OfficialWarnings'
import NotificationSettings from './components/NotificationSettings'
import ReplayMode from './components/ReplayMode'
import { setSetting, TEXT_ZOOM, useSettings, useT, type TextSize } from './settings'

const RiskMap = lazy(() => import('./components/RiskMap'))
// Both pull in MapLibre (~1 MB), so they load only when shown.
const ResponderDashboard = lazy(() => import('./components/ResponderDashboard'))

const HOLD_MS = 2000

type StatusRow = [label: string, value: string | null]

/** Live figures for the status table; a figure that cannot be read stays "—". */
function useSystemStatus(replay: string | null): StatusRow[] {
  const [rows, setRows] = useState<Record<string, string>>({})
  useEffect(() => {
    let active = true
    const put = (label: string) => (value: string) => {
      if (active) setRows((current) => ({ ...current, [label]: value }))
    }
    getHealth().then((h) => put('Road network nodes covered')(h.nodes.toLocaleString())).catch(() => undefined)
    Promise.all([getShelters(), getHospitals()])
      .then(([s, h]) => put('Shelters & hospitals mapped')(`${s.length} / ${h.length}`))
      .catch(() => undefined)
    getReports().then((r) => put('Active hazard reports right now')(String(r.items.length))).catch(() => undefined)
    getZones(replay)
      .then((z) => {
        const wettest = [...z.zones].sort((a, b) => (b.rain_mm.h24 ?? 0) - (a.rain_mm.h24 ?? 0))[0]
        if (wettest) put('Wettest zone, last 24 h')(`${wettest.name} — ${wettest.rain_mm.h24 ?? 0} mm`)
        put('Zones at WATCH or CRITICAL')(String(z.zones.filter((zone) => zone.status !== 'SAFE').length))
      })
      .catch(() => undefined)
    return () => {
      active = false
    }
  }, [replay])
  return [
    'Road network nodes covered',
    'Shelters & hospitals mapped',
    'Active hazard reports right now',
    'Wettest zone, last 24 h',
    'Zones at WATCH or CRITICAL',
  ].map((label) => [label, rows[label] ?? null])
}

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

function ReplayBanner() {
  const t = useT()
  const { replay } = useSettings()
  if (!replay) return null
  return (
    <div className="replay-banner" role="status">
      {t('Replay of')} {replay} — {t('rain that actually fell that day, not live.')}{' '}
      <button type="button" onClick={() => setSetting('replay', null)}>
        {t('Back to live')}
      </button>
    </div>
  )
}

function App() {
  const { lang } = useSettings()
  const [onMapPage, setOnMapPage] = useState(() => window.location.hash === MAP_HASH)

  useEffect(() => {
    document.documentElement.lang = lang
  }, [lang])

  useEffect(() => {
    const sync = () => setOnMapPage(window.location.hash === MAP_HASH)
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [])

  useEffect(() => {
    document.title = onMapPage ? 'FloodSafe — Map tool' : 'FloodSafe'
  }, [onMapPage])

  return (
    <>
      <ReplayBanner />
      {onMapPage ? <MapPage /> : <Portal />}
    </>
  )
}

function Portal() {
  const t = useT()
  const { lang, textSize, replay } = useSettings()
  const status = useSystemStatus(replay)
  const [sosShare, setSosShare] = useState(false)
  const holdTimer = useRef<number | null>(null)
  const [holding, setHolding] = useState(false)
  const [sosOpen, setSosOpen] = useState(false)
  const [sosName, setSosName] = useState(() => loadSosContact().name)
  const [sosNumbers, setSosNumbers] = useState(() => loadSosContact().numbers)
  const [sosBusy, setSosBusy] = useState(false)
  const [sosMessage, setSosMessage] = useState<string | null>(null)

  const handleSos = async () => {
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
      const result = await sendSos({ name, lat, lon, numbers, share_with_responders: sosShare })
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

  // SOS sends only after a 2 s press, so a stray tap in a pocket does not text anyone.
  const startHold = () => {
    if (sosBusy) return
    setHolding(true)
    holdTimer.current = window.setTimeout(() => {
      setHolding(false)
      void handleSos()
    }, HOLD_MS)
  }
  const stopHold = () => {
    setHolding(false)
    if (holdTimer.current) window.clearTimeout(holdTimer.current)
    holdTimer.current = null
  }

  const sizes: [TextSize, string][] = [['small', 'A-'], ['normal', 'A'], ['large', 'A+']]

  return (
    <main className="dashboard" style={{ zoom: TEXT_ZOOM[textSize] }}>
      <div className="utility-bar">
        <span>{t('FloodSafe Portal — Flood-Aware Road Advisory Service')}</span>
        <div>
          <button type="button" aria-pressed={lang === 'en'} onClick={() => setSetting('lang', 'en')}>
            English
          </button>
          <span>|</span>
          <button type="button" lang="hi" aria-pressed={lang === 'hi'} onClick={() => setSetting('lang', 'hi')}>
            हिंदी
          </button>
          <span>{t('Text size:')}</span>
          {sizes.map(([size, label]) => (
            <button key={size} type="button" aria-pressed={textSize === size} onClick={() => setSetting('textSize', size)}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <header className="portal-header">
        <a className="portal-brand" href="#">
          <span className="portal-brand-icon">⚑</span>
          <span>
            <strong>FloodSafe</strong>
            <small>{t('Flood-Aware Road Advisory — Uttarakhand')}</small>
          </span>
        </a>

        <nav className="portal-nav">
          <a href="#live-status">{t('System Status')}</a>
          <a href="#guidance">{t('Flood Guidance')}</a>
          <a href="#ffgs">FFGS</a>
          <a href="#services">{t('Services')}</a>
          <a href="#hazard-reports">{t('Hazard Reports')}</a>
          <a className="nav-cta" href={MAP_HASH} target="_blank" rel="noopener">{t('Open Map Tool')}</a>
        </nav>
      </header>

      <div className="portal-content">
        <div className="advisory-banner">
          <strong>⚠ {t('Public Advisory:')}</strong>{' '}
          {t('Road conditions can change rapidly during monsoon season. Always verify local conditions before travel.')}
        </div>

        <section className="hero">
          <div className="hero-copy">
            <h2>{t("Fastest isn't always")} <span>{t('safe.')}</span></h2>
            <p>
              {t('FloodSafe advises road routes across Uttarakhand using a georeferenced flash-flood hazard classification, live rainfall data, and citizen-reported hazards — instead of distance alone.')}
            </p>
            <div className="hero-actions">
              <a className="primary-button" href={MAP_HASH} target="_blank" rel="noopener">{t('Open the Map Tool →')}</a>
              <a className="secondary-button" href="#live-status">{t('View System Status')}</a>
            </div>
            <button
              className="sos-button"
              type="button"
              aria-expanded={sosOpen}
              onClick={() => setSosOpen((open) => !open)}
            >
              <span className="sos-circle">SOS</span>
              <strong>{t('In danger? Text your family your location')}</strong>
            </button>
            {sosOpen && (
              <form className="sos-form" onSubmit={(event) => event.preventDefault()}>
                <label>
                  {t('Your name')}
                  <input
                    value={sosName}
                    onChange={(event) => setSosName(event.target.value)}
                    autoComplete="name"
                  />
                </label>
                <label>
                  {t('Phone numbers to text (comma separated, with country code)')}
                  <input
                    value={sosNumbers}
                    onChange={(event) => setSosNumbers(event.target.value)}
                    inputMode="tel"
                    placeholder="+919876543210, +911234567890"
                  />
                </label>
                <label className="sos-share">
                  <input
                    type="checkbox"
                    checked={sosShare}
                    onChange={(event) => setSosShare(event.target.checked)}
                  />
                  {t('Also show my location to responders for 24 hours')}
                </label>
                <button
                  type="button"
                  className={`sos-hold${holding ? ' is-holding' : ''}`}
                  disabled={sosBusy}
                  onPointerDown={startHold}
                  onPointerUp={stopHold}
                  onPointerLeave={stopHold}
                  onPointerCancel={stopHold}
                  onContextMenu={(event) => event.preventDefault()}
                  // A keyboard press has no hold, so Enter or Space sends at once.
                  onClick={(event) => {
                    if (event.detail === 0) void handleSos()
                  }}
                >
                  {sosBusy ? t('Getting your location…') : t('Send my location now')}
                  <small>{t('Hold for 2 seconds to send')}</small>
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
          <h3>{t('SYSTEM STATUS')}</h3>
          <div className="status-table">
            <div className="table-head"><span>{t('Metric')}</span><span>{t('Value')}</span></div>
            {status.map(([metric, value]) => (
              <div key={metric}>
                <span>{t(metric)}</span>
                <strong className={value ? undefined : 'preview-value'}>
                  {value ?? t('Waiting for live data')}
                </strong>
              </div>
            ))}
          </div>
          <div className="hazard-legend">
            <span><i className="hazard-low" /> LOW hazard</span>
            <span><i className="hazard-moderate" /> MODERATE hazard</span>
            <span><i className="hazard-significant" /> SIGNIFICANT hazard</span>
            <span><i className="hazard-extreme" /> EXTREME hazard</span>
          </div>
        </section>

        <section id="guidance" className="portal-section">
          <FloodGuidance />
        </section>

        <section id="map-tool" className="portal-section">
          <div className="section-heading">
            <div>
              <h3>Flood-aware map tool</h3>
              <p>{t('Road and area hazard overview')}</p>
            </div>
            <span className="map-tag">Surveyed data</span>
          </div>
          <p>
            {t('Route planning, hazard layers, shelters, hospitals and community reports open in a full-screen map in a new tab.')}
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
          <Suspense fallback={<p role="status">Loading…</p>}>
            <ResponderDashboard />
          </Suspense>
          <NotificationSettings />
          <ReplayMode />
        </section>

        <section id="hazard-reports" className="portal-section">
          <OfficialWarnings />
        </section>

        <footer className="portal-footer">
          <span>FloodSafe — Flood-Aware Road Advisory Service for Uttarakhand.</span>
          <span>{t('Use official guidance during emergencies.')}</span>
        </footer>
      </div>
    </main>
  )
}

export default App

