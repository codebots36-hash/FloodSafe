export default function ResponderDashboard() {
  return (
    <section className="responder-card">
      <div className="section-heading">
        <div>
          <p className="eyebrow">EMERGENCY RESPONSE</p>
          <h3>Responder dashboard</h3>
          <p>Response coordination overview</p>
        </div>
        <span className="status-label">DEMO</span>
      </div>

      <div className="responder-stats">
        <div>
          <span>Active incidents</span>
          <strong>0</strong>
        </div>
        <div>
          <span>Responders available</span>
          <strong>0</strong>
        </div>
        <div>
          <span>Shelters listed</span>
          <strong>0</strong>
        </div>
      </div>

      <p className="data-note">
        Demo interface — no live incident or responder data connected.
      </p>
    </section>
  )
}