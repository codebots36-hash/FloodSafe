import { useEffect, useState } from 'react'
import { getWarnings, type WarningsResponse } from '../api'
import { formatWhen, useSettings, useT } from '../settings'

const REFRESH_MS = 10 * 60 * 1000
const SACHET_SITE = 'https://sachet.ndma.gov.in/'

/**
 * NDMA SACHET alerts, shown as issued and kept apart from FloodSafe's own
 * zone status. "No warnings" is only said when the feed was actually read.
 */
export default function OfficialWarnings() {
  const t = useT()
  const { lang } = useSettings()
  const [feed, setFeed] = useState<WarningsResponse | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true
    const load = () =>
      getWarnings()
        .then((body) => {
          if (!active) return
          setFeed(body)
          setFailed(false)
        })
        .catch(() => {
          if (active) setFailed(true)
        })
    void load()
    const timer = window.setInterval(load, REFRESH_MS)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [])

  const unavailable = failed || feed?.status === 'unavailable'
  const alerts = feed?.alerts ?? []

  return (
    <section className="warning-card">
      <div>
        <p className="eyebrow">{t('SAFETY')}</p>
        <h3>{t('Official warnings')}</h3>
        <p>{t('From NDMA SACHET, as issued by IMD, CWC and the state disaster authority.')}</p>

        {unavailable && (
          <p className="warning-unavailable" role="alert">
            {t('The official warning feed could not be read just now, so warnings may be missing. Check sachet.ndma.gov.in.')}
          </p>
        )}
        {!feed && !failed && <p role="status">{t('Checking official warnings…')}</p>}
        {feed && !unavailable && alerts.length === 0 && (
          <p role="status">{t('No current official warnings for Uttarakhand.')}</p>
        )}

        {alerts.length > 0 && (
          <ul className="official-alerts">
            {alerts.map((alert) => {
              const hindi = lang === 'hi' && alert.headline_hi
              return (
                <li key={alert.id} className="official-alert">
                  <strong>{alert.event || alert.severity}</strong>
                  <span className="official-alert-severity">{alert.severity}</span>
                  <p lang={hindi ? 'hi' : 'en'}>{hindi ? alert.headline_hi : alert.headline}</p>
                  <small>
                    {alert.sender} · {alert.area}
                    {alert.expires && ` · ${t('Until')} ${formatWhen(alert.expires)}`}
                  </small>
                  {/* alert.link is the raw CAP XML; SACHET has no page per alert, so open its site. */}
                  <a href={SACHET_SITE} target="_blank" rel="noopener noreferrer">
                    sachet.ndma.gov.in
                  </a>
                </li>
              )
            })}
          </ul>
        )}
        {feed?.as_of && (
          <p className="data-note">As of {formatWhen(feed.as_of)}</p>
        )}
      </div>
      <span className="warning-icon">!</span>
    </section>
  )
}
