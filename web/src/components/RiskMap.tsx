
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  type GeoJSONSource,
  LngLatBounds,
  Map as MapLibreMap,
  Marker,
  NavigationControl,
  setWorkerUrl,
} from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Feature, FeatureCollection, LineString } from 'geojson'
import { getRoute, type RouteMode, type RouteResponse } from '../api'
import RoutePanel from './RoutePanel'
import {
  NO_DATA_COLOUR,
  PLACES,
  RISK_COLOURS,
  RISK_ORDER,
  type Endpoint,
} from '../routeOptions'

setWorkerUrl(workerUrl)

const atlasUrl = '/data/layer_atlas.geojson'
const roadRiskUrl = '/data/layer_road_risk.geojson'

const ROUTE_SOURCE = 'route-segments'
const ROUTE_LAYER = 'route-segments-line'
const ROUTE_NODATA_LAYER = 'route-segments-nodata'
const NO_DATA = 'NO_DATA'

type SegmentFeature = Feature<LineString, { risk: string }>

/**
 * One feature per segment, covering geometry.coordinates[start..end].
 * Segments the API rates as null become NO_DATA — drawn grey and dashed,
 * never folded into LOW.
 */
function toSegmentFeatures(route: RouteResponse): SegmentFeature[] {
  const coordinates = route.geometry.coordinates

  return route.segments
    .map((segment) => {
      const slice = coordinates.slice(segment.start, segment.end + 1)
      return {
        type: 'Feature' as const,
        geometry: { type: 'LineString' as const, coordinates: slice },
        properties: { risk: segment.risk_class ?? NO_DATA },
      }
    })
    .filter((feature) => feature.geometry.coordinates.length >= 2)
}

function midpoint(coordinates: number[][]) {
  return coordinates[Math.floor(coordinates.length / 2)] as [number, number]
}

/** Kilometres of route the API gave no risk class for. */
function noDataKmOf(route: RouteResponse) {
  const classified = RISK_ORDER.reduce(
    (total, riskClass) => total + (route.summary.km_by_class[riskClass] ?? 0),
    0,
  )
  return Math.max(0, Math.round((route.distance_km - classified) * 10) / 10)
}

export default function RiskMap() {
  const mapContainer = useRef<HTMLDivElement>(null)
  const map = useRef<MapLibreMap | null>(null)
  const endpointMarkers = useRef<Marker[]>([])
  const noDataLabels = useRef<Marker[]>([])
  const pickTarget = useRef<'from' | 'to'>('from')

  const [mapReady, setMapReady] = useState(false)
  const [showAtlas, setShowAtlas] = useState(true)
  const [showRoadRisk, setShowRoadRisk] = useState(true)

  const [from, setFrom] = useState<Endpoint | null>(null)
  const [to, setTo] = useState<Endpoint | null>(null)
  const [mode, setMode] = useState<RouteMode>('safest')
  const [nextClickSets, setNextClickSets] = useState<'from' | 'to'>('from')
  const [route, setRoute] = useState<RouteResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!mapContainer.current || map.current) return

    const instance = new MapLibreMap({
      container: mapContainer.current,
      center: [78.25, 30.07],
      zoom: 10,
      style: {
        version: 8,
        sources: {
          osm: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            attribution:
              '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
          },
        },
        layers: [
          {
            id: 'osm-tiles',
            type: 'raster',
            source: 'osm',
          },
        ],
      },
    })

    map.current = instance
    instance.addControl(new NavigationControl(), 'top-right')

    instance.on('click', (event) => {
      const target = pickTarget.current
      const picked: Endpoint = {
        label: `Picked ${event.lngLat.lat.toFixed(4)}, ${event.lngLat.lng.toFixed(4)}`,
        lat: Number(event.lngLat.lat.toFixed(5)),
        lon: Number(event.lngLat.lng.toFixed(5)),
      }

      if (target === 'from') {
        setFrom(picked)
        pickTarget.current = 'to'
        setNextClickSets('to')
      } else {
        setTo(picked)
        pickTarget.current = 'from'
        setNextClickSets('from')
      }
    })

    instance.on('load', async () => {
      setMapReady(true)

      try {
        const [atlasResponse, roadResponse] = await Promise.all([
          fetch(atlasUrl),
          fetch(roadRiskUrl),
        ])

        if (!atlasResponse.ok || !roadResponse.ok) {
          throw new Error('Could not load hazard GeoJSON files')
        }

        const [atlasData, roadRiskData] = await Promise.all([
          atlasResponse.json(),
          roadResponse.json(),
        ])

        if (map.current !== instance) return

        instance.addSource('atlas-risk', {
          type: 'geojson',
          data: atlasData,
        })

        instance.addLayer({
          id: 'atlas-risk-fill',
          type: 'fill',
          source: 'atlas-risk',
          paint: {
            'fill-color': '#e74c3c',
            'fill-opacity': 0.3,
          },
        })

        instance.addLayer({
          id: 'atlas-risk-outline',
          type: 'line',
          source: 'atlas-risk',
          paint: {
            'line-color': '#c0392b',
            'line-width': 2,
          },
        })

        instance.addSource('road-risk', {
          type: 'geojson',
          data: roadRiskData,
        })

        instance.addLayer({
          id: 'road-risk-line',
          type: 'line',
          source: 'road-risk',
          paint: {
            'line-color': '#8e44ad',
            'line-width': 5,
            'line-opacity': 0.85,
          },
        })

        // Set initial visibility to match the default checked controls.
        instance.setLayoutProperty(
          'atlas-risk-fill',
          'visibility',
          'visible',
        )
        instance.setLayoutProperty(
          'atlas-risk-outline',
          'visibility',
          'visible',
        )
        instance.setLayoutProperty(
          'road-risk-line',
          'visibility',
          'visible',
        )
      } catch (loadError) {
        console.error('Failed to load FloodSafe hazard layers:', loadError)
      }
    })

    return () => {
      endpointMarkers.current.forEach((marker) => marker.remove())
      endpointMarkers.current = []
      noDataLabels.current.forEach((marker) => marker.remove())
      noDataLabels.current = []
      instance.remove()
      map.current = null
      setMapReady(false)
    }
  }, [])

  useEffect(() => {
    const instance = map.current
    if (!instance) return

    const atlasVisibility = showAtlas ? 'visible' : 'none'
    const roadVisibility = showRoadRisk ? 'visible' : 'none'

    if (instance.getLayer('atlas-risk-fill')) {
      instance.setLayoutProperty(
        'atlas-risk-fill',
        'visibility',
        atlasVisibility,
      )
    }

    if (instance.getLayer('atlas-risk-outline')) {
      instance.setLayoutProperty(
        'atlas-risk-outline',
        'visibility',
        atlasVisibility,
      )
    }

    if (instance.getLayer('road-risk-line')) {
      instance.setLayoutProperty(
        'road-risk-line',
        'visibility',
        roadVisibility,
      )
    }
  }, [showAtlas, showRoadRisk])

  // Markers for the chosen start and end.
  useEffect(() => {
    const instance = map.current
    if (!instance || !mapReady) return

    endpointMarkers.current.forEach((marker) => marker.remove())
    endpointMarkers.current = []

    const ends: [Endpoint | null, string][] = [
      [from, 'from'],
      [to, 'to'],
    ]

    ends.forEach(([endpoint, kind]) => {
      if (!endpoint) return
      const element = document.createElement('div')
      element.className = `route-endpoint route-endpoint-${kind}`
      element.textContent = kind === 'from' ? 'A' : 'B'
      element.title = endpoint.label
      endpointMarkers.current.push(
        new Marker({ element })
          .setLngLat([endpoint.lon, endpoint.lat])
          .addTo(instance),
      )
    })
  }, [from, to, mapReady])

  // Draw the route, segment by segment, and fit the map to it.
  useEffect(() => {
    const instance = map.current
    if (!instance || !mapReady) return

    noDataLabels.current.forEach((marker) => marker.remove())
    noDataLabels.current = []

    const features = route ? toSegmentFeatures(route) : []
    const data: FeatureCollection<LineString> = {
      type: 'FeatureCollection',
      features,
    }

    const existing = instance.getSource(ROUTE_SOURCE)
    if (existing) {
      ;(existing as GeoJSONSource).setData(data)
    } else {
      instance.addSource(ROUTE_SOURCE, { type: 'geojson', data })

      instance.addLayer({
        id: ROUTE_NODATA_LAYER,
        type: 'line',
        source: ROUTE_SOURCE,
        filter: ['==', ['get', 'risk'], NO_DATA],
        layout: { 'line-cap': 'butt', 'line-join': 'round' },
        paint: {
          'line-color': NO_DATA_COLOUR,
          'line-width': 6,
          'line-dasharray': [2, 2],
        },
      })

      instance.addLayer({
        id: ROUTE_LAYER,
        type: 'line',
        source: ROUTE_SOURCE,
        filter: ['!=', ['get', 'risk'], NO_DATA],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': [
            'match',
            ['get', 'risk'],
            'LOW',
            RISK_COLOURS.LOW,
            'MODERATE',
            RISK_COLOURS.MODERATE,
            'SIGNIFICANT',
            RISK_COLOURS.SIGNIFICANT,
            'EXTREME',
            RISK_COLOURS.EXTREME,
            NO_DATA_COLOUR,
          ],
          'line-width': 6,
        },
      })
    }

    if (!route || features.length === 0) return

    // Label unrated stretches on the map so they can never read as LOW.
    features
      .filter((feature) => feature.properties.risk === NO_DATA)
      .forEach((feature) => {
        const element = document.createElement('span')
        element.className = 'route-nodata-label'
        element.textContent = 'no data'
        noDataLabels.current.push(
          new Marker({ element })
            .setLngLat(midpoint(feature.geometry.coordinates))
            .addTo(instance),
        )
      })

    const bounds = route.geometry.coordinates.reduce(
      (box, coordinate) => box.extend(coordinate as [number, number]),
      new LngLatBounds(
        route.geometry.coordinates[0] as [number, number],
        route.geometry.coordinates[0] as [number, number],
      ),
    )
    instance.fitBounds(bounds, { padding: 60, duration: 600 })
  }, [route, mapReady])

  const selectEndpoint = useCallback(
    (value: string, set: (endpoint: Endpoint | null) => void) => {
      if (!value || value === 'picked') return
      const place = PLACES.find((item) => item.id === value)
      if (!place) return
      set({ label: place.name, lat: place.lat, lon: place.lon })
    },
    [],
  )

  const handleSubmit = useCallback(async () => {
    if (!from || !to) return

    setLoading(true)
    setError(null)

    try {
      const result = await getRoute(
        { lat: from.lat, lon: from.lon },
        { lat: to.lat, lon: to.lon },
        mode,
      )
      setRoute(result)
    } catch (requestError) {
      setRoute(null)
      setError(
        requestError instanceof Error
          ? requestError.message
          : 'The routing service could not be reached.',
      )
    } finally {
      setLoading(false)
    }
  }, [from, to, mode])

  return (
    <div className="risk-map-wrapper">
      <RoutePanel
        from={from}
        to={to}
        mode={mode}
        loading={loading}
        error={error}
        route={route}
        nextClickSets={nextClickSets}
        noDataKm={route ? noDataKmOf(route) : 0}
        onSelectFrom={(value) => selectEndpoint(value, setFrom)}
        onSelectTo={(value) => selectEndpoint(value, setTo)}
        onModeChange={setMode}
        onSubmit={handleSubmit}
      />

      <div className="risk-map-stage">
        <div className="risk-map-controls">
          <label>
            <input
              type="checkbox"
              checked={showAtlas}
              onChange={(event) => setShowAtlas(event.target.checked)}
            />
            Flood hazard area
          </label>

          <label>
            <input
              type="checkbox"
              checked={showRoadRisk}
              onChange={(event) => setShowRoadRisk(event.target.checked)}
            />
            Road risk
          </label>
        </div>

        <div ref={mapContainer} className="risk-map-canvas" />

        <div className="risk-map-legend">
          <strong>Map legend</strong>
          <div>
            <span className="legend-swatch atlas-swatch" />
            Hazard area
          </div>
          <div>
            <span className="legend-swatch road-swatch" />
            Risk road
          </div>
          {RISK_ORDER.map((riskClass) => (
            <div key={riskClass}>
              <span
                className="legend-swatch"
                style={{ background: RISK_COLOURS[riskClass] }}
              />
              Route — {riskClass}
            </div>
          ))}
          <div>
            <span className="legend-swatch legend-swatch-nodata" />
            Route — no data
          </div>
        </div>
      </div>
    </div>
  )
}
