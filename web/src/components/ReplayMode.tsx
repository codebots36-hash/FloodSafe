import { useState } from 'react'
import PreviewBadge from './PreviewBadge'

// Steps only show how the timeline will move; no replay data is connected,
// so no rainfall or risk values are invented for them.
const REPLAY_STEPS = 4

export default function ReplayMode() {
  const [index, setIndex] = useState(0)

  return (
    <section className="replay-card">
      <div className="section-heading">
        <div>
          <p className="eyebrow">HISTORICAL REVIEW</p>
          <h3>Replay mode</h3>
          <p>Explore how conditions changed over time.</p>
        </div>
        <PreviewBadge />
      </div>

      <label className="replay-timeline">
        <span>
          Timeline: step {index + 1} of {REPLAY_STEPS}
        </span>
        <input
          type="range"
          min={0}
          max={REPLAY_STEPS - 1}
          step={1}
          value={index}
          onChange={(event) => setIndex(Number(event.target.value))}
        />
      </label>

      <div className="replay-summary">
        <div>
          <span>Rainfall</span>
          <strong className="preview-value">Not connected</strong>
        </div>
        <div>
          <span>Risk level</span>
          <strong className="preview-value">Not connected</strong>
        </div>
      </div>

      <p className="data-note">
        Layout preview — no historical rainfall is connected, so no readings are
        shown.
      </p>
    </section>
  )
}
