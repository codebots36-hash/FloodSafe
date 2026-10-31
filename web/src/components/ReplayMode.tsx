import { useState } from 'react'

const replaySamples = [
  { time: '08:00', rainfall: '12 mm', risk: 'SAFE' },
  { time: '10:00', rainfall: '24 mm', risk: 'WATCH' },
  { time: '12:00', rainfall: '38 mm', risk: 'CRITICAL' },
  { time: '14:00', rainfall: '19 mm', risk: 'WATCH' },
]

export default function ReplayMode() {
  const [index, setIndex] = useState(0)
  const sample = replaySamples[index]

  return (
    <section className="replay-card">
      <div className="section-heading">
        <div>
          <p className="eyebrow">HISTORICAL REVIEW</p>
          <h3>Replay mode</h3>
          <p>Explore how conditions changed over time.</p>
        </div>
        <span className="status-label">DEMO</span>
      </div>

      <label className="replay-timeline">
        <span>Timeline: {sample.time}</span>
        <input
          type="range"
          min={0}
          max={replaySamples.length - 1}
          step={1}
          value={index}
          onChange={(event) => setIndex(Number(event.target.value))}
        />
      </label>

      <div className="replay-summary">
        <div>
          <span>Rainfall</span>
          <strong>{sample.rainfall}</strong>
        </div>
        <div>
          <span>Risk level</span>
          <strong className={`replay-risk replay-${sample.risk.toLowerCase()}`}>
            {sample.risk}
          </strong>
        </div>
      </div>

      <p className="data-note">
        Illustrative sample data only — not historical or live measurements.
      </p>
    </section>
  )
}