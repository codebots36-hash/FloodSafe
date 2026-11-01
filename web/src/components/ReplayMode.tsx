import { useState } from 'react'
import { setSetting, useSettings, useT } from '../settings'

// Past floods whose rain is kept in the API (api/data/weather_history), so
// they replay even without a network. Any other past date is fetched once.
const EVENTS = [
  { date: '2025-08-05', label: 'Dharali (Uttarkashi) flash flood, 5 Aug 2025' },
  { date: '2013-06-16', label: 'Kedarnath disaster, 16 Jun 2013' },
  { date: '2013-06-17', label: 'Kedarnath disaster, 17 Jun 2013' },
]

// The ERA5 record is published about five days late; the API refuses anything newer.
const ARCHIVE_LAG_DAYS = 6
const latestDay = () =>
  new Date(Date.now() - ARCHIVE_LAG_DAYS * 86_400_000).toLocaleDateString('en-CA', {
    timeZone: 'Asia/Kolkata',
  })

/**
 * One switch for the whole app: routing, zones, guidance and rain then run on
 * the rain that actually fell that day, and every screen says "Replay of".
 */
export default function ReplayMode() {
  const t = useT()
  const { replay } = useSettings()
  const [date, setDate] = useState(replay ?? EVENTS[0].date)

  return (
    <section className="replay-card">
      <div className="section-heading">
        <div>
          <p className="eyebrow">{t('HISTORICAL REVIEW')}</p>
          <h3>{t('Replay mode')}</h3>
          <p>{t('Run routing, zones and guidance on the rain that actually fell on a past day.')}</p>
        </div>
      </div>

      <div className="replay-events">
        {EVENTS.map((event) => (
          <button
            key={event.date}
            type="button"
            className={date === event.date ? 'is-selected' : ''}
            onClick={() => setDate(event.date)}
          >
            {event.label}
          </button>
        ))}
      </div>

      <label className="replay-date">
        {t('Replay date')}{' '}
        <input
          type="date"
          min="1940-01-01"
          max={latestDay()}
          value={date}
          onChange={(event) => setDate(event.target.value)}
        />
      </label>

      <div className="replay-actions">
        <button
          type="button"
          className="primary-button"
          disabled={!date}
          onClick={() => setSetting('replay', date)}
        >
          {t('Start replay')}
        </button>
        {replay && (
          <button type="button" className="secondary-button" onClick={() => setSetting('replay', null)}>
            {t('Back to live')}
          </button>
        )}
      </div>

      <p className="data-note">
        {replay ? `${t('Replay of')} ${replay}. ` : ''}
        Rain from the ERA5 record (Open-Meteo archive): what fell, hour by hour, not a forecast.
      </p>
    </section>
  )
}
