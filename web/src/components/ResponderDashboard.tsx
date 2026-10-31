import PreviewBadge from './PreviewBadge'

// No responder feed is connected, so every figure shows as unavailable.
// A "0" here would read as "no active incidents", which is not known.
const responderStats = [
  'Active incidents',
  'Responders available',
  'Shelters listed',
]

export default function ResponderDashboard() {
  return (
    <section className="responder-card">
      <div className="section-heading">
        <div>
          <p className="eyebrow">EMERGENCY RESPONSE</p>
          <h3>Responder dashboard</h3>
          <p>Response coordination overview</p>
        </div>
        <PreviewBadge />
      </div>

      <div className="responder-stats">
        {responderStats.map((label) => (
          <div key={label}>
            <span>{label}</span>
            <strong className="preview-value">Not connected</strong>
          </div>
        ))}
      </div>

      <p className="data-note">
        Layout preview — no live incident or responder data is connected, so
        no counts are shown.
      </p>
    </section>
  )
}
