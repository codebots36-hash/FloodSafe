import PreviewBadge from './PreviewBadge'

export default function OfficialWarnings() {
  return (
    <section className="warning-card">
      <div>
        <p className="eyebrow">SAFETY</p>
        <h3>Official warnings</h3>
        <PreviewBadge />
        <p>
          Live NDMA warnings are not connected yet.
          Check official local advisories before travelling.
        </p>
      </div>
      <span className="warning-icon">!</span>
    </section>
  )
}
