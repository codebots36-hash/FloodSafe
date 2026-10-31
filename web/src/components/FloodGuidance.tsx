import { useEffect, useState } from 'react'
import { getZones, type ZonesResponse, type ZoneStatus } from '../api'
import { useSettings, useT } from '../settings'

const ZONE_KEY = 'floodsafe-guidance-zone'

const guidance: Record<ZoneStatus, { title: string; message: string; actions: string[] }> = {
  SAFE: {
    title: 'Stay aware',
    message: 'Rain here is below the level that has come before floods. Keep checking updates.',
    actions: [
      'Check official weather and local alerts before travelling.',
      'Avoid crossing flooded roads or streams.',
    ],
  },
  WATCH: {
    title: 'Be prepared',
    message: 'Rain here has reached the WATCH level. Plan ahead.',
    actions: [
      'Avoid low-lying roads and areas near rivers.',
      'Keep your phone charged and monitor official alerts.',
      'Consider delaying non-essential travel.',
    ],
  },
  CRITICAL: {
    title: 'Take immediate precautions',
    message: 'Rain here has reached the CRITICAL level. Follow instructions from local authorities.',
    actions: [
      'Move away from flooded areas and rising water.',
      'Move to higher ground if advised by authorities.',
      'Call local emergency services if you are in immediate danger.',
    ],
  },
}

// Shown until a zone is chosen or while the status cannot be read: advice
// that holds in any conditions, with no claim about what they are.
const generalGuidance = {
  title: 'Before you travel',
  message: 'Choose a zone to see its rain against its own WATCH and CRITICAL levels.',
  actions: [
    'Check official weather and local alerts before travelling.',
    'Avoid crossing flooded roads or streams.',
    'Avoid low-lying roads and areas near rivers during heavy rain.',
    'Keep your phone charged during monsoon travel.',
  ],
}

function savedZone() {
  try {
    return localStorage.getItem(ZONE_KEY) ?? ''
  } catch {
    return ''
  }
}

const mm = (value: number | null) => (value === null ? '—' : `${value} mm`)

export default function FloodGuidance() {
  const t = useT()
  const { replay } = useSettings()
  const [data, setData] = useState<ZonesResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [zoneId, setZoneId] = useState(savedZone)

  useEffect(() => {
    let active = true
    getZones(replay)
      .then((body) => {
        if (!active) return
        setData(body)
        setError(null)
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : 'Could not read the zones.')
      })
    return () => {
      active = false
    }
  }, [replay])

  const choose = (id: string) => {
    setZoneId(id)
    try {
      localStorage.setItem(ZONE_KEY, id)
    } catch {
      // Remembering the zone is optional.
    }
  }

  const zone = data?.zones.find((item) => item.id === zoneId) ?? null
  const advice = zone ? guidance[zone.status] : generalGuidance
  const variant = zone ? zone.status.toLowerCase() : 'general'

  return (
    <section className={`guidance-card guidance-${variant}`}>
      <p className="eyebrow">{t('FLOOD GUIDANCE')}</p>
      <label className="guidance-zone">
        {t('Zone')}{' '}
        <select value={zoneId} onChange={(event) => choose(event.target.value)} disabled={!data}>
          <option value="">{t('Choose a zone')}</option>
          {data?.zones
            .slice()
            .sort((a, b) => a.name.localeCompare(b.name))
            .map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} ({item.district}) — {item.status}
              </option>
            ))}
        </select>
      </label>
      {error && <p role="alert">{error}</p>}

      <h3>
        {zone ? `${zone.name}: ${zone.status} — ` : ''}
        {t(advice.title)}
      </h3>
      <p>{t(advice.message)}</p>

      {zone && (
        <table className="guidance-rain">
          <thead>
            <tr>
              <th />
              <th>1 h</th>
              <th>3 h</th>
              <th>24 h</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th>{t('Rain so far')}</th>
              <td>{mm(zone.rain_mm.h1)}</td>
              <td>{mm(zone.rain_mm.h3)}</td>
              <td>{mm(zone.rain_mm.h24)}</td>
            </tr>
            <tr>
              <th>{t('WATCH at')}</th>
              <td>{zone.watch.h1} mm</td>
              <td>{zone.watch.h3} mm</td>
              <td>{zone.watch.h24} mm</td>
            </tr>
            <tr>
              <th>{t('CRITICAL at')}</th>
              <td>{zone.critical.h1} mm</td>
              <td>{zone.critical.h3} mm</td>
              <td>{zone.critical.h24} mm</td>
            </tr>
          </tbody>
        </table>
      )}
      {zone && (
        <p className="guidance-facts">
          {t('Rain in the 48 h before that')}: {mm(zone.rain_mm.antecedent_48h)} · {t('Hazard class')}:{' '}
          {zone.hazard_class} ({zone.risk_source}) · {t('Soil group')}: {zone.soil_group} · {t('Catchment')}:{' '}
          {zone.catchment_km2} km²
        </p>
      )}

      <ul>
        {advice.actions.map((action) => (
          <li key={action}>{t(action)}</li>
        ))}
      </ul>

      {data && (
        <p className="guidance-note">
          {data.threshold_basis} {data.source === 'replay' ? `Replay of ${data.replay}.` : ''} As of{' '}
          {new Date(data.as_of).toLocaleString()} ({data.source}).
        </p>
      )}
      <p className="guidance-note">
        {t('General safety guidance. Follow official local instructions during emergencies.')}
      </p>
    </section>
  )
}
