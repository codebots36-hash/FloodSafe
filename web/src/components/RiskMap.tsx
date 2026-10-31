
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  type ExpressionSpecification,
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
import {
  API,
  getRouteComparison,
  type RouteFailure,
  type RouteMode,
  type RouteResponse,
} from '../api'
import { routeVerdict } from '../routeVerdict'
import RoutePanel from './RoutePanel'
import {
  NO_DATA_COLOUR,
  PLACES,
  RISK_COLOURS,
  RISK_ORDER,
  type Endpoint,
} from '../routeOptions'

setWorkerUrl(workerUrl)

const atlasUrl = `${API}/layers/atlas`
const roadRiskUrl = `${API}/layers/road-risk`

/** Colour a feature by one of its risk-class properties. */
function classColour(property: 'risk_class' | 'hazard_class'): ExpressionSpecification {
  return [
    'match',
    ['get', property],
    'LOW',
    RISK_COLOURS.LOW,
    'MODERATE',
    RISK_COLOURS.MODERATE,
    'SIGNIFICANT',
    RISK_COLOURS.SIGNIFICANT,
    'EXTREME',
    RISK_COLOURS.EXTREME,
    NO_DATA_COLOUR,
  ]
}

type RoadLayerStatus = 'loading' | 'ready' | 'failed'

const NO_DATA = 'NO_DATA'

/** Drawn bottom to top: Fastest sits underneath Safest. */
const ROUTE_MODES: RouteMode[] = ['fastest', 'safest']

const routeIds = (mode: RouteMode) => ({
  source: `route-${mode}`,
  line: `route-${mode}-line`,
  noData: `route-${mode}-nodata`,
})

/**
 * Safest is the solid main line; Fastest is thinner and see-through so it
 * reads as the alternative. Hovering a card swaps in the highlight values.
 */
const ROUTE_LOOK: Record<RouteMode, { width: number; opacity: number }> = {
  safest: { width: 6, opacity: 1 },
  fastest: { width: 4, opacity: 0.45 },
}
const HIGHLIGHT_LOOK = { width: 8, opacity: 1 }
const DIMMED_OPACITY = 0.2

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

const EARTH_RADIUS_KM = 6371

function haversineKm(a: number[], b: number[]) {
  const toRadians = (degrees: number) => (degrees * Math.PI) / 180
  const dLat = toRadians(b[1] - a[1])
  const dLon = toRadians(b[0] - a[0])
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRadians(a[1])) * Math.cos(toRadians(b[1])) * Math.sin(dLon / 2) ** 2
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(h)))
}

function lineLengthKm(coordinates: number[][]) {
  let km = 0
  for (let index = 1; index < coordinates.length; index += 1) {
    km += haversineKm(coordinates[index - 1], coordinates[index])
  }
  return km
}

/**
 * summary.km_by_class only covers rated stretches, so the unrated ones are
 * measured from the geometry rather than inferred from the totals.
 */
function noDataKmOf(route: RouteResponse) {
  return route.segments
    .filter((segment) => segment.risk_class === null)
    .reduce(
      (km, segment) =>
        km +
        lineLengthKm(route.geometry.coordinates.slice(segment.start, segment.end + 1)),
      0,
    )
}

/**
 * Hazard overlays load after the first route, so keep the routes on top —
 * and keep Fastest under Safest while doing it.
 */
function bringRouteToFront(instance: MapLibreMap) {
  ROUTE_MODES.forEach((mode) => {
    const ids = routeIds(mode)
    ;[ids.noData, ids.line].forEach((layerId) => {
      if (instance.getLayer(layerId)) instance.moveLayer(layerId)
    })
  })
}

const RISK_COLOUR_EXPRESSION: ExpressionSpecification = [
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
]

function addRouteLayers(instance: MapLibreMap, mode: RouteMode) {
  const ids = routeIds(mode)
  const look = ROUTE_LOOK[mode]

  instance.addSource(ids.source, {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: [] },
  })

  instance.addLayer({
    id: ids.noData,
    type: 'line',
    source: ids.source,
    filter: ['==', ['get', 'risk'], NO_DATA],
    layout: { 'line-cap': 'butt', 'line-join': 'round' },
    paint: {
      'line-color': NO_DATA_COLOUR,
      'line-width': look.width,
      'line-opacity': look.opacity,
      'line-dasharray': [2, 2],
    },
  })

  instance.addLayer({
    id: ids.line,
    type: 'line',
    source: ids.source,
    filter: ['!=', ['get', 'risk'], NO_DATA],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': RISK_COLOUR_EXPRESSION,
      'line-width': look.width,
      'line-opacity': look.opacity,
    },
  })
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
  const [roadLayerStatus, setRoadLayerStatus] =
    useState<RoadLayerStatus>('loading')
  const [layerError, setLayerError] = useState<string | null>(null)
  // Read when the layers are added, so a toggle flipped before then sticks.
  const layerVisibility = useRef({ atlas: true, roadRisk: true })

  const [from, setFrom] = useState<Endpoint | null>(null)
  const [to, setTo] = useState<Endpoint | null>(null)
  const [nextClickSets, setNextClickSets] = useState<'from' | 'to'>('from')
  const [routes, setRoutes] = useState<Record<RouteMode, RouteResponse | null>>({
    safest: null,
    fastest: null,
  })
  const [failures, setFailures] = useState<RouteFailure[]>([])
  const [hovered, setHovered] = useState<RouteMode | null>(null)
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

    instance.on('load', () => {
      setMapReady(true)

      if (!API) {
        setRoadLayerStatus('failed')
        setLayerError(
          'VITE_API_URL is not set, so the risk layers cannot be loaded.',
        )
        return
      }

      const visibility = (shown: boolean) => (shown ? 'visible' : 'none')

      // MapLibre fetches and parses these URLs in its worker; the road layer
      // is about 11 MB, which would stall the page if parsed here.
      instance.addSource('atlas-risk', { type: 'geojson', data: atlasUrl })

      instance.addLayer({
        id: 'atlas-risk-fill',
        type: 'fill',
        source: 'atlas-risk',
        layout: { visibility: visibility(layerVisibility.current.atlas) },
        paint: {
          'fill-color': classColour('hazard_class'),
          'fill-opacity': 0.15,
        },
      })

      instance.addLayer({
        id: 'atlas-risk-outline',
        type: 'line',
        source: 'atlas-risk',
        layout: { visibility: visibility(layerVisibility.current.atlas) },
        paint: {
          'line-color': classColour('hazard_class'),
          'line-width': 1,
          'line-opacity': 0.35,
        },
      })

      instance.addSource('road-risk', { type: 'geojson', data: roadRiskUrl })

      instance.addLayer({
        id: 'road-risk-line',
        type: 'line',
        source: 'road-risk',
        layout: {
          visibility: visibility(layerVisibility.current.roadRisk),
          'line-cap': 'round',
          'line-join': 'round',
        },
        paint: {
          'line-color': classColour('risk_class'),
          'line-width': ['interpolate', ['linear'], ['zoom'], 7, 1, 12, 3],
          // Surveyed roads are solid; modelled ones are drawn lighter.
          'line-opacity': ['match', ['get', 'risk_source'], 'surveyed', 0.6, 0.3],
        },
      })

      bringRouteToFront(instance)
    })

    // The road layer's data has been parsed once its metadata event fires.
    instance.on('sourcedata', (event) => {
      if (event.sourceId === 'road-risk' && event.sourceDataType === 'metadata') {
        setRoadLayerStatus('ready')
      }
    })

    instance.on('error', (event) => {
      const sourceId = (event as { sourceId?: string }).sourceId
      if (sourceId !== 'road-risk' && sourceId !== 'atlas-risk') {
        console.error('Map error:', event.error)
        return
      }
      if (sourceId === 'road-risk') setRoadLayerStatus('failed')
      setLayerError(
        sourceId === 'road-risk'
          ? 'Could not load the road risk layer.'
          : 'Could not load the hazard area layer.',
      )
      console.error(`Failed to load ${sourceId}:`, event.error)
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

    layerVisibility.current = { atlas: showAtlas, roadRisk: showRoadRisk }

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

  // Draw both routes, segment by segment, and fit the map to them.
  useEffect(() => {
    const instance = map.current
    if (!instance || !mapReady) return

    ROUTE_MODES.forEach((mode) => {
      const ids = routeIds(mode)
      if (!instance.getSource(ids.source)) addRouteLayers(instance, mode)

      const route = routes[mode]
      const data: FeatureCollection<LineString> = {
        type: 'FeatureCollection',
        features: route ? toSegmentFeatures(route) : [],
      }
      ;(instance.getSource(ids.source) as GeoJSONSource).setData(data)
    })

    bringRouteToFront(instance)

    const allCoordinates = ROUTE_MODES.flatMap(
      (mode) => routes[mode]?.geometry.coordinates ?? [],
    )
    if (allCoordinates.length === 0) return

    const bounds = allCoordinates.reduce(
      (box, coordinate) => box.extend(coordinate),
      new LngLatBounds(allCoordinates[0], allCoordinates[0]),
    )
    instance.fitBounds(bounds, { padding: 60, duration: 600 })
  }, [routes, mapReady])

  // Hovering a card lifts its line and fades the other one.
  useEffect(() => {
    const instance = map.current
    if (!instance || !mapReady) return

    ROUTE_MODES.forEach((mode) => {
      const ids = routeIds(mode)
      if (!instance.getLayer(ids.line)) return

      let look = ROUTE_LOOK[mode]
      if (hovered === mode) look = HIGHLIGHT_LOOK
      else if (hovered) look = { width: look.width, opacity: DIMMED_OPACITY }

      ;[ids.line, ids.noData].forEach((layerId) => {
        instance.setPaintProperty(layerId, 'line-width', look.width)
        instance.setPaintProperty(layerId, 'line-opacity', look.opacity)
      })
    })

    // A highlighted Fastest line has to come up over Safest to be seen.
    bringRouteToFront(instance)
    if (hovered === 'fastest') {
      const ids = routeIds('fastest')
      instance.moveLayer(ids.noData)
      instance.moveLayer(ids.line)
    }
  }, [hovered, routes, mapReady])

  // Label unrated stretches so they never read as LOW. Only the line in
  // focus is labelled, otherwise overlapping routes would double each label.
  useEffect(() => {
    const instance = map.current
    if (!instance || !mapReady) return

    noDataLabels.current.forEach((marker) => marker.remove())
    noDataLabels.current = []

    const focus: RouteMode = hovered ?? (routes.safest ? 'safest' : 'fastest')
    const route = routes[focus]
    if (!route) return

    toSegmentFeatures(route)
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
  }, [hovered, routes, mapReady])

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
    setFailures([])
    setHovered(null)

    const comparison = await getRouteComparison(
      { lat: from.lat, lon: from.lon },
      { lat: to.lat, lon: to.lon },
    )

    setRoutes({ safest: comparison.safest, fastest: comparison.fastest })
    if (!comparison.safest && !comparison.fastest) {
      setError(comparison.failures[0]?.detail ?? 'No route came back.')
    } else {
      setFailures(comparison.failures)
    }
    setLoading(false)
  }, [from, to])

  return (
    <div className="risk-map-wrapper">
      <RoutePanel
        from={from}
        to={to}
        loading={loading}
        error={error}
        routes={routes}
        failures={failures}
        verdict={
          routes.safest && routes.fastest
            ? routeVerdict(routes.safest, routes.fastest)
            : null
        }
        noDataKm={{
          safest: routes.safest ? noDataKmOf(routes.safest) : 0,
          fastest: routes.fastest ? noDataKmOf(routes.fastest) : 0,
        }}
        hovered={hovered}
        nextClickSets={nextClickSets}
        onSelectFrom={(value) => selectEndpoint(value, setFrom)}
        onSelectTo={(value) => selectEndpoint(value, setTo)}
        onHover={setHovered}
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

        {roadLayerStatus === 'loading' && !layerError && (
          <p className="risk-layer-status" role="status">
            <span className="route-spinner" aria-hidden="true" />
            Loading risk layer…
          </p>
        )}

        {layerError && (
          <p className="risk-layer-status risk-layer-status-error" role="alert">
            {layerError}
          </p>
        )}

        <div className="risk-map-legend">
          <strong>Map legend</strong>
          <div>
            <span className="legend-swatch atlas-swatch" />
            Hazard area — shaded by class
          </div>
          <div>
            <span className="legend-swatch road-swatch-surveyed" />
            Risk road — surveyed
          </div>
          <div>
            <span className="legend-swatch road-swatch-modelled" />
            Risk road — modelled (lighter)
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
          <div>
            <span className="legend-swatch legend-swatch-safest" />
            Safest — solid
          </div>
          <div>
            <span className="legend-swatch legend-swatch-fastest" />
            Fastest — thin, underneath
          </div>
        </div>
      </div>
    </div>
  )
}
