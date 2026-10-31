
import { lazy, Suspense } from 'react'
import './App.css'
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

function App() {
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
          <a className="nav-cta" href="#map-tool">Open Map Tool</a>
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
              <a className="primary-button" href="#map-tool">Open the Map Tool →</a>
              <a className="secondary-button" href="#live-status">View System Status</a>
            </div>
            <button className="sos-button" type="button">
              <span className="sos-circle">SOS</span>
              <strong>In danger? Text your family your location</strong>
            </button>
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
          <Suspense fallback={<p role="status">Loading map…</p>}>
            <RiskMap />
          </Suspense>
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

