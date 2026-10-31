import type { RouteMode, RouteResponse } from '../api'
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
  mode: RouteMode
  loading: boolean
  error: string | null
  route: RouteResponse | null
  nextClickSets: 'from' | 'to'
  noDataKm: number
  onSelectFrom: (value: string) => void
  onSelectTo: (value: string) => void
  onModeChange: (mode: RouteMode) => void
  onSubmit: () => void
}

function isPicked(endpoint: Endpoint | null) {
  return Boolean(endpoint) && !PLACES.some((place) => place.name === endpoint!.label)
}

function selectValue(endpoint: Endpoint | null) {
  if (!endpoint) return ''
  if (isPicked(endpoint)) return 'picked'
  return PLACES.find((place) => place.name === endpoint.label)!.id
}

export default function RoutePanel({
  from,
  to,
  mode,
  loading,
  error,
  route,
  nextClickSets,
  noDataKm,
  onSelectFrom,
  onSelectTo,
  onModeChange,
  onSubmit,
}: RoutePanelProps) {
  const kmByClass = route?.summary.km_by_class
  const maxKm = kmByClass
    ? Math.max(
        noDataKm,
        ...RISK_ORDER.map((riskClass) => kmByClass[riskClass] ?? 0),
      )
    : 0

  return (
    <div className="route-panel">
      <div className="route-panel-head">
        <h4>Plan a flood-aware route</h4>
        <p>
          Pick two places, or click the map to set{' '}
          <strong>{nextClickSets === 'from' ? 'From' : 'To'}</strong>.
        </p>
      </div>

      <div className="route-panel-fields">
        <label>
          <span>From</span>
          <select
            value={selectValue(from)}
            onChange={(event) => onSelectFrom(event.target.value)}
          >
            <option value="">Select a place…</option>
            {isPicked(from) && <option value="picked">{from!.label}</option>}
            {PLACES.map((place) => (
              <option key={place.id} value={place.id}>
                {place.name}
              </option>
            ))}
          </select>
        </label>

        <label>
          <span>To</span>
          <select
            value={selectValue(to)}
            onChange={(event) => onSelectTo(event.target.value)}
          >
            <option value="">Select a place…</option>
            {isPicked(to) && <option value="picked">{to!.label}</option>}
            {PLACES.map((place) => (
              <option key={place.id} value={place.id}>
                {place.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="route-panel-actions">
        <div className="route-mode-toggle" role="group" aria-label="Route mode">
          <button
            type="button"
            className={mode === 'fastest' ? 'active' : undefined}
            aria-pressed={mode === 'fastest'}
            onClick={() => onModeChange('fastest')}
          >
            Fastest
          </button>
          <button
            type="button"
            className={mode === 'safest' ? 'active' : undefined}
            aria-pressed={mode === 'safest'}
            onClick={() => onModeChange('safest')}
          >
            Safest
          </button>
        </div>

        <button
          type="button"
          className="route-submit"
          onClick={onSubmit}
          disabled={loading || !from || !to}
        >
          {loading ? 'Finding route…' : 'Get route'}
        </button>
      </div>

      {loading && (
        <p className="route-loading" role="status">
          <span className="route-spinner" aria-hidden="true" />
          Checking rainfall and hazard ratings along the road — a safest route
          can take about a second.
        </p>
      )}

      {error && !loading && (
        <p className="route-error" role="alert">
          <strong>Could not get a route.</strong> {error}
        </p>
      )}

      {route && !loading && !error && (
        <div className="route-result">
          <div className="route-figures">
            <div>
              <span>Distance</span>
              <strong>{route.distance_km.toFixed(1)} km</strong>
            </div>
            <div>
              <span>Driving time</span>
              <strong>{Math.round(route.duration_min)} min</strong>
            </div>
            <div>
              <span>Mode</span>
              <strong className="route-mode-value">{route.mode}</strong>
            </div>
            <div>
              <span>EXTREME segments</span>
              <strong>{route.summary.extreme_segments}</strong>
            </div>
          </div>

          <table className="route-km-table">
            <caption>Kilometres by risk class</caption>
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
                          width: maxKm > 0 ? `${(km / maxKm) * 100}%` : '0%',
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
                      style={{
                        width: maxKm > 0 ? `${(noDataKm / maxKm) * 100}%` : '0%',
                      }}
                    />
                  </td>
                  <td className="route-km-value">{noDataKm.toFixed(1)} km</td>
                </tr>
              )}
            </tbody>
          </table>

          {noDataKm > 0 && (
            <p className="route-nodata-note">
              Grey dashed stretches are marked <strong>no data</strong> — the
              road lies outside Uttarakhand, so it has no hazard rating.
            </p>
          )}

          {route.notes.length > 0 && (
            <ul className="route-notes">
              {route.notes.map((note, index) => (
                <li key={index}>{note}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
