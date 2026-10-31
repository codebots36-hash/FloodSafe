
import './App.css'
import RiskMap from './components/RiskMap'

function App() {
  return (
    <main className="dashboard">
      <header className="topbar">
        <div className="brand">
          <span className="brand-icon">F</span>
          <div>
            <h1>FloodSafe</h1>
            <p>Flood-aware navigation</p>
          </div>
        </div>

        <button className="language-button" type="button">
          हिंदी
        </button>
      </header>

      <section className="welcome">
        <p className="eyebrow">UTTARAKHAND</p>
        <h2>Stay safe. Travel informed.</h2>
        <p>Check local conditions before you begin your journey.</p>
      </section>

      <section className="status-card">
        <div className="status-heading">
          <span className="status-dot" />
          <span>Zone status</span>
          <span className="status-label">WATCH</span>
        </div>
        <h3>Rishikesh</h3>
        <p>Rainfall is approaching the watch threshold.</p>

        <div className="rain-grid">
          <div>
            <span>Last 1 hour</span>
            <strong>4.2 mm</strong>
          </div>
          <div>
            <span>Last 3 hours</span>
            <strong>28.0 mm</strong>
          </div>
          <div>
            <span>Last 24 hours</span>
            <strong>38.5 mm</strong>
          </div>
        </div>
        <p className="data-note">Contract sample data — not a live reading</p>
      </section>

      <section className="map-card">
        <div className="section-heading">
          <div>
            <h3>Risk map</h3>
            <p>Road and area hazard overview</p>
          </div>
          <span className="map-tag">Surveyed data</span>
        </div>
        <RiskMap />
      </section>

      <section className="warning-card">
        <div>
          <p className="eyebrow">SAFETY</p>
          <h3>Official warnings</h3>
          <p>Live warning feed will appear here when connected.</p>
        </div>
        <span className="warning-icon">!</span>
      </section>

      <footer>
        <span>FloodSafe</span>
        <span>Use official guidance during emergencies.</span>
      </footer>
    </main>
  )
}

export default App