import { useEffect, useRef, useState } from 'react'
import { Map as MapLibreMap, setWorkerUrl, type GeoJSONSource } from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { FeatureCollection } from 'geojson'
import { getReports, getResponderSummary, type CommunityReport, type ResponderSummary } from '../api'
import { formatWhen, useT } from '../settings'
import './Zones.css'

setWorkerUrl(workerUrl)

const REFRESH_MS = 60_000

function points(items: { lat: number; lon: number }[]): FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: items.map((item) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [item.lon, item.lat] },
      properties: {},
    })),
  }
}

/**
 * A district control room's view: community reports as a heatmap, SOS pings
 * people chose to share, the zones not SAFE and how many shelters are known.
 */
export default function ResponderDashboard() {
  const t = useT()
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<MapLibreMap | null>(null)
  const [ready, setReady] = useState(false)
  const [summary, setSummary] = useState<ResponderSummary | null>(null)
  const [reports, setReports] = useState<CommunityReport[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    const load = () => {
      getResponderSummary()
        .then((body) => {
          if (!active) return
          setSummary(body)
          setError(null)
        })
        .catch((err: unknown) => {
          if (active) setError(err instanceof Error ? err.message : 'Could not load the summary.')
        })
      getReports()
        .then((body) => active && setReports(body.items))
        .catch(() => undefined)
    }
    load()
    const timer = window.setInterval(load, REFRESH_MS)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    if (!container.current || map.current) return
    const instance = new MapLibreMap({
      container: container.current,
      center: [79.2, 30.1],
      zoom: 6.6,
      style: {
        version: 8,
        sources: {
          osm: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
          },
        },
        layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
      },
    })
    instance.on('load', () => {
      instance.addSource('reports', { type: 'geojson', data: points([]) })
      instance.addLayer({
        id: 'reports-heat',
        type: 'heatmap',
        source: 'reports',
        paint: {
          'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 6, 18, 12, 40],
          'heatmap-opacity': 0.8,
        },
      })
      instance.addSource('sos', { type: 'geojson', data: points([]) })
      instance.addLayer({
        id: 'sos-points',
        type: 'circle',
        source: 'sos',
        paint: {
          'circle-radius': 7,
          'circle-color': '#c92727',
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 2,
        },
      })
      setReady(true)
    })
    map.current = instance
    return () => {
      instance.remove()
      map.current = null
      setReady(false)
    }
  }, [])

  useEffect(() => {
    if (!ready || !map.current) return
    ;(map.current.getSource('reports') as GeoJSONSource).setData(points(reports))
    ;(map.current.getSource('sos') as GeoJSONSource).setData(points(summary?.sos_pings ?? []))
  }, [ready, reports, summary])

  const stat = (value: number | null | undefined) =>
    value === null || value === undefined ? '—' : value

  return (
    <section className="responder-card">
      <div className="section-heading">
        <div>
          <p className="eyebrow">{t('EMERGENCY RESPONSE')}</p>
          <h3>{t('Responder dashboard')}</h3>
        </div>
      </div>

      <div className="responder-stats">
        <div>
          <span>{t('Community reports (24 h)')}</span>
          <strong>{stat(summary?.reports_last_24h)}</strong>
        </div>
        <div>
          <span>{t('Zones not SAFE')}</span>
          <strong>{stat(summary?.critical_zones?.length)}</strong>
        </div>
        <div>
          <span>{t('Shelters listed')}</span>
          <strong>{stat(summary?.shelters_count)}</strong>
        </div>
        <div>
          <span>{t('SOS shared (24 h)')}</span>
          <strong>{stat(summary?.sos_pings.length)}</strong>
        </div>
      </div>

      <div ref={container} className="responder-map" aria-label="Map of reports and SOS pings" />

      {summary?.critical_zones && summary.critical_zones.length > 0 && (
        <ul className="responder-zones">
          {summary.critical_zones.map((zone) => (
            <li key={zone.id}>
              <i className={`zone-dot zone-dot-${zone.status.toLowerCase()}`} aria-hidden="true" />
              {zone.name} — {zone.status}
            </li>
          ))}
        </ul>
      )}
      {error && <p role="alert">{error}</p>}
      {summary && (
        <p className="data-note">
          As of {formatWhen(summary.as_of)}. Heat = community reports; red dots = SOS
          locations people chose to share.
        </p>
      )}
    </section>
  )
}
