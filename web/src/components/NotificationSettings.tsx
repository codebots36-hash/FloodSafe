import { useState } from 'react'
import PreviewBadge from './PreviewBadge'

export default function NotificationSettings() {
  const [enabled, setEnabled] = useState(false)

  return (
    <section className="notification-card">
      <div className="section-heading">
        <div>
          <p className="eyebrow">ALERT PREFERENCES</p>
          <h3>Push notifications</h3>
          <p>Choose whether to receive flood safety alerts.</p>
          <PreviewBadge />
        </div>

        <label className="notification-toggle">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          <span>{enabled ? 'Enabled' : 'Disabled'}</span>
        </label>
      </div>

      <div className="notification-options">
        <label>
          <input type="checkbox" disabled={!enabled} defaultChecked />
          Critical flood warnings
        </label>
        <label>
          <input type="checkbox" disabled={!enabled} defaultChecked />
          Route hazard updates
        </label>
        <label>
          <input type="checkbox" disabled={!enabled} />
          Weather advisories
        </label>
      </div>

      <p className="data-note">
        UI preview only — push delivery and browser permission are not connected.
      </p>
    </section>
  )
}