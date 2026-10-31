import { useState } from 'react'
import { useT } from '../settings'
import type { RouteFailure, RouteMode, RouteResponse } from '../api'
import {
  NO_DATA_COLOUR,
  PLACES,
  RISK_COLOURS,
  RISK_ORDER,
  type Endpoint,
} from '../routeOptions'

interface RoutePanelProps {
  from: Endpoint | null
  to: Endpoint | null
  loading: boolean
  error: string | null
  routes: Record<RouteMode, RouteResponse | null>
  failures: RouteFailure[]
  verdict: string | null
  noDataKm: Record<RouteMode, number>
  hovered: RouteMode | null
  nextClickSets: 'from' | 'to'
  onSelectFrom: (value: string) => void
  onSelectTo: (value: string) => void
  onHover: (mode: RouteMode | null) => void
  onSubmit: () => void
  /** "YYYY-MM-DDTHH:MM" in India time, or '' to leave now (on a replay, at the day's worst rain). */
  depart: string
  onDepartChange: (value: string) => void
  replay: string | null
}

const pad = (n: number) => String(n).padStart(2, '0')

/** A Date as "YYYY-MM-DDTHH:MM" in India time, for a datetime-local field. */
function istLocal(date: Date) {
  const ist = new Date(date.getTime() + 330 * 60_000)
  return `${ist.getUTCFullYear()}-${pad(ist.getUTCMonth() + 1)}-${pad(ist.getUTCDate())}T${pad(ist.getUTCHours())}:${pad(ist.getUTCMinutes())}`
}

/** The wettest forecast hour on a route, for the card. */
function wettest(route: RouteResponse) {
  const values = route.segments
    .map((segment) => segment.forecast_rain_mm)
    .filter((value): value is number => value !== null)
  return values.length ? Math.max(...values) : null
}

const CARD_ORDER: RouteMode[] = ['safest', 'fastest']

const CARD_TITLES: Record<RouteMode, string> = {
  safest: 'Safest',
  fastest: 'Fastest',
}

const CARD_LINE_HINTS: Record<RouteMode, string> = {
  safest: 'Solid line',
  fastest: 'Thin line underneath',
}

function isPicked(endpoint: Endpoint | null) {
  return Boolean(endpoint) && !PLACES.some((place) => place.name === endpoint!.label)
}

function selectValue(endpoint: Endpoint | null) {
  if (!endpoint) return ''
  if (isPicked(endpoint)) return 'picked'
  return PLACES.find((place) => place.name === endpoint.label)!.id
}

interface RouteCardProps {
  mode: RouteMode
  route: RouteResponse | null
  failure: RouteFailure | undefined
  noDataKm: number
  maxKm: number
  highlighted: boolean
  onHover: (mode: RouteMode | null) => void
}

function RouteCard({
  mode,
  route,
  failure,
  noDataKm,
  maxKm,
  highlighted,
  onHover,
}: RouteCardProps) {
  const t = useT()
  const barWidth = (km: number) => (maxKm > 0 ? `${(km / maxKm) * 100}%` : '0%')

  return (
    <article
      className={`route-card route-card-${mode}${highlighted ? ' is-highlighted' : ''}`}
      tabIndex={route ? 0 : -1}
      aria-label={`${CARD_TITLES[mode]} route`}
      onMouseEnter={() => route && onHover(mode)}
      onMouseLeave={() => onHover(null)}
      onFocus={() => route && onHover(mode)}
      onBlur={() => onHover(null)}
    >
      <header className="route-card-head">
        <h5>{t(CARD_TITLES[mode])}</h5>
        <span className={`route-card-line route-card-line-${mode}`}>
          {t(CARD_LINE_HINTS[mode])}
        </span>
      </header>

      {!route && (
        <p className="route-error" role="alert">
          <strong>No {mode} route.</strong>{' '}
          {failure?.detail ?? 'The API did not return this route.'}
        </p>
      )}

      {route && (
        <>
          <div className="route-figures">
            <div>
              <span>{t('Distance')}</span>
              <strong>{route.distance_km.toFixed(1)} km</strong>
            </div>
            <div>
              <span>{t('Driving time')}</span>
              <strong>{Math.round(route.duration_min)} min</strong>
            </div>
            {wettest(route) !== null && (
              <div>
                <span>{t('Wettest hour on the way')}</span>
                <strong>{wettest(route)} mm</strong>
              </div>
            )}
          </div>

          <table className="route-km-table">
            <caption>{t('Kilometres by risk class')}</caption>
            <tbody>
              {RISK_ORDER.map((riskClass) => {
                const km = route.summary.km_by_class[riskClass] ?? 0
                return (
                  <tr key={riskClass}>
                    <th scope="row">
                      <span
                        className="route-swatch"
                        style={{ background: RISK_COLOURS[riskClass] }}
                      />
                      {riskClass}
                    </th>
                    <td>
                      <span
                        className="route-bar"
                        style={{
                          width: barWidth(km),
                          background: RISK_COLOURS[riskClass],
                        }}
                      />
                    </td>
                    <td className="route-km-value">{km.toFixed(1)} km</td>
                  </tr>
                )
              })}
              {noDataKm > 0 && (
                <tr>
                  <th scope="row">
                    <span
                      className="route-swatch route-swatch-nodata"
                      style={{ background: NO_DATA_COLOUR }}
                    />
                    no data
                  </th>
                  <td>
                    <span
                      className="route-bar route-bar-nodata"
                      style={{ width: barWidth(noDataKm) }}
                    />
                  </td>
                  <td className="route-km-value">{noDataKm.toFixed(1)} km</td>
                </tr>
              )}
            </tbody>
          </table>

          {route.notes.length > 0 && (
            <ul className="route-notes">
              {route.notes.map((note, index) => (
                <li key={index}>{note}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </article>
  )
}

export default function RoutePanel({
  from,
  to,
  loading,
  error,
  routes,
  failures,
  verdict,
  noDataKm,
  hovered,
  nextClickSets,
  onSelectFrom,
  onSelectTo,
  onHover,
  onSubmit,
  depart,
  onDepartChange,
  replay,
}: RoutePanelProps) {
  const t = useT()
  const hasResult = Boolean(routes.safest || routes.fastest)
  // The live departure window, fixed when the panel opens.
  const [openedAt] = useState(() => Date.now())

  // One scale for both cards so the bars can be compared across them.
  const maxKm = Math.max(
    0,
    ...CARD_ORDER.flatMap((mode) => {
      const route = routes[mode]
      if (!route) return []
      return [
        noDataKm[mode],
        ...RISK_ORDER.map((riskClass) => route.summary.km_by_class[riskClass] ?? 0),
      ]
    }),
  )

  const anyNoData = CARD_ORDER.some((mode) => routes[mode] && noDataKm[mode] > 0)

  return (
    <div className="route-panel">
      <div className="route-panel-head">
        <h4>{t('Compare flood-aware routes')}</h4>
        <p>
          Pick two places, or click the map to set{' '}
          <strong>{nextClickSets === 'from' ? 'From' : 'To'}</strong>. Both the
          safest and the fastest route are fetched.
        </p>
      </div>

      <div className="route-panel-fields">
        <label>
          <span>{t('From')}</span>
          <select
            value={selectValue(from)}
            onChange={(event) => onSelectFrom(event.target.value)}
          >
            <option value="">{t('Select a place…')}</option>
            {isPicked(from) && <option value="picked">{from!.label}</option>}
            {PLACES.map((place) => (
              <option key={place.id} value={place.id}>
                {place.name}
              </option>
            ))}
          </select>
        </label>

        <label>
          <span>{t('To')}</span>
          <select
            value={selectValue(to)}
            onChange={(event) => onSelectTo(event.target.value)}
          >
            <option value="">{t('Select a place…')}</option>
            {isPicked(to) && <option value="picked">{to!.label}</option>}
            {PLACES.map((place) => (
              <option key={place.id} value={place.id}>
                {place.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <label className="route-depart">
        <span>{replay ? `${t('Leave on')} ${replay}` : t('Leave')}</span>
        <input
          type="datetime-local"
          value={depart}
          min={replay ? `${replay}T00:00` : istLocal(new Date(openedAt))}
          max={replay ? `${replay}T23:59` : istLocal(new Date(openedAt + 47 * 3_600_000))}
          onChange={(event) => onDepartChange(event.target.value)}
        />
        {depart && (
          <button type="button" onClick={() => onDepartChange('')}>
            {replay ? t('Worst hour') : t('Now')}
          </button>
        )}
        <small>
          {t('Each stretch is judged on the rain at the hour you reach it (India time).')}
        </small>
      </label>

      <div className="route-panel-actions">
        <button
          type="button"
          className="route-submit"
          onClick={onSubmit}
          disabled={loading || !from || !to}
        >
          {loading ? t('Finding routes…') : t('Get routes')}
        </button>
      </div>

      {loading && (
        <p className="route-loading" role="status">
          <span className="route-spinner" aria-hidden="true" />
          Fetching the safest and fastest routes — the safest one can take about
          a second.
        </p>
      )}

      {error && !loading && (
        <p className="route-error" role="alert">
          <strong>{t('Could not get a route.')}</strong> {error}
        </p>
      )}

      {hasResult && !loading && !error && (
        <div className="route-result">
          {verdict && (
            <p className="route-verdict" role="status">
              {verdict}
            </p>
          )}

          <div className="route-compare">
            {CARD_ORDER.map((mode) => (
              <RouteCard
                key={mode}
                mode={mode}
                route={routes[mode]}
                failure={failures.find((failure) => failure.mode === mode)}
                noDataKm={noDataKm[mode]}
                maxKm={maxKm}
                highlighted={hovered === mode}
                onHover={onHover}
              />
            ))}
          </div>

          {anyNoData && (
            <p className="route-nodata-note">
              Grey dashed stretches are marked <strong>no data</strong> — the
              road lies outside Uttarakhand, so it has no hazard rating.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
