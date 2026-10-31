
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  type ExpressionSpecification,
  type GeoJSONSource,
  LngLatBounds,
  Map as MapLibreMap,
  Marker,
  NavigationControl,
  Popup,
  setWorkerUrl,
} from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Feature, FeatureCollection, LineString } from 'geojson'
import {
  API,
  confirmReport,
  createReport,
  getHospitals,
  getLocalities,
  getReports,
  getRouteComparison,
  getShelters,
  getZones,
  getWeather,
  type CommunityReport,
  type Locality,
  type MapPlace,
  type ReportKind,
  type RouteFailure,
  type RouteMode,
  type RouteResponse,
  type Zone,
  type WeatherResponse,
} from '../api'
import { routeVerdict } from '../routeVerdict'
import { useSettings } from '../settings'
import RoutePanel from './RoutePanel'
import './Zones.css'
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

// The road-risk GeoJSON is about 11 MB. On a phone it starts off and is only
// downloaded when the user switches it on.
const IS_PHONE =
  typeof window !== 'undefined' && window.matchMedia('(max-width: 600px)').matches

function addRoadLayer(instance: MapLibreMap) {
  if (instance.getSource('road-risk')) return

  instance.addSource('road-risk', {
    type: 'geojson',
    data: roadRiskUrl,
    // Coarser simplification and a lower tiling ceiling keep the worker's
    // per-tile work and the geometry sent to the GPU small.
    tolerance: 1.5,
    maxzoom: 12,
    buffer: 32,
  })

  instance.addLayer({
    id: 'road-risk-line',
    type: 'line',
    source: 'road-risk',
    minzoom: 8,
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': classColour('risk_class'),
      'line-width': ['interpolate', ['linear'], ['zoom'], 8, 1, 12, 3],
      // Surveyed roads are solid; modelled ones are drawn lighter.
      'line-opacity': ['match', ['get', 'risk_source'], 'surveyed', 0.6, 0.3],
    },
  })
}

const NO_DATA = 'NO_DATA'

/** Drawn bottom to top: Fastest sits underneath Safest. */
const ROUTE_MODES: RouteMode[] = ['fastest', 'safest']

const REPORTER_STORAGE_KEY = 'floodsafe.reporter_id'
let fallbackReporterId: string | null = null

function getOrCreateReporterId() {
  try {
    const saved = window.localStorage.getItem(REPORTER_STORAGE_KEY)
    if (saved) return saved

    const randomId = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)
    const reporterId = `anon-${randomId}`
    window.localStorage.setItem(REPORTER_STORAGE_KEY, reporterId)
    return reporterId
  } catch {
    fallbackReporterId ??= `anon-${Math.random().toString(36).slice(2)}`
    return fallbackReporterId
  }
}

function errorDetail(error: unknown) {
  return error instanceof Error ? error.message : 'Waiting for live data'
}

const REPORT_KIND_ICONS: Record<ReportKind, string> = {
  flooded: '≈',
  blocked: '!',
  landslide: '▲',
}

const REPORT_KIND_LABELS: Record<ReportKind, string> = {
  flooded: 'Flooded',
  blocked: 'Blocked',
  landslide: 'Landslide',
}
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

const PLACE_LAYERS = {
  shelters: { source: 'shelters', layer: 'shelters-circle', colour: '#1f6fb5' },
  hospitals: { source: 'hospitals', layer: 'hospitals-circle', colour: '#c0392b' },
} as const

const REPORT_LOOK: Record<ReportKind, { colour: string; glyph: string; label: string }> = {
  flooded: { colour: '#1f6fb5', glyph: '≈', label: 'Flooded' },
  blocked: { colour: '#e67e22', glyph: '✕', label: 'Road blocked' },
  landslide: { colour: '#8d5a2b', glyph: '▲', label: 'Landslide' },
}

function popupBody(parts: (string | HTMLElement)[]) {
  const root = document.createElement('div')
  root.style.cssText = 'font-size:13px;line-height:1.4;max-width:220px'
  parts.forEach((part) => {
    if (typeof part === 'string') {
      const line = document.createElement('div')
      line.textContent = part
      root.appendChild(line)
    } else {
      root.appendChild(part)
    }
  })
  return root
}

function strongLine(text: string) {
  const line = document.createElement('strong')
  line.textContent = text
  line.style.display = 'block'
  return line
}

function reportPopup(report: CommunityReport) {
  const look = REPORT_LOOK[report.kind]
  const badge = document.createElement('span')
  badge.textContent = report.trust === 'verified' ? 'Verified' : 'Unverified'
  badge.title = report.trust_reason
  badge.style.cssText =
    'display:inline-block;margin:4px 0;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:700;color:#fff;background:' +
    (report.trust === 'verified' ? '#2e7d32' : '#8a8f98')

  const when = new Date(report.created_at)
  return popupBody([
    strongLine(look.label),
    report.note || 'No note left.',
    Number.isNaN(when.getTime()) ? '' : when.toLocaleString(),
    badge,
    report.trust_reason,
    `${report.confirmations} confirmation${report.confirmations === 1 ? '' : 's'}`,
    report.near_locality ? `Near ${report.near_locality}` : '',
  ].filter((part) => part !== ''))
}

const ENDPOINTS_KEY = 'floodsafe-last-trip'

/** The last trip's ends, so an offline reload can ask for the cached route again. */
function savedEndpoints(): { from: Endpoint | null; to: Endpoint | null } {
  try {
    return { from: null, to: null, ...JSON.parse(localStorage.getItem(ENDPOINTS_KEY) ?? '{}') }
  } catch {
    return { from: null, to: null }
  }
}

export default function RiskMap() {
  const { replay } = useSettings()
  const [depart, setDepart] = useState('')
  const mapContainer = useRef<HTMLDivElement>(null)
  const map = useRef<MapLibreMap | null>(null)
  const rainPopup = useRef<Popup | null>(null)
  const endpointMarkers = useRef<Marker[]>([])
  const zoneMarkers = useRef<Marker[]>([])
  const noDataLabels = useRef<Marker[]>([])
  const reportMarkers = useRef<Marker[]>([])
  const pickedMarker = useRef<Marker | null>(null)
  const pickTarget = useRef<'from' | 'to'>('from')
  const reportModeRef = useRef(false)
  const placeVisibility = useRef({ shelters: true, hospitals: true })

  const stageRef = useRef<HTMLDivElement>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)

  useEffect(() => {
    const sync = () => setIsFullscreen(document.fullscreenElement === stageRef.current)
    document.addEventListener('fullscreenchange', sync)
    return () => document.removeEventListener('fullscreenchange', sync)
  }, [])

  const toggleFullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen()
    else void stageRef.current?.requestFullscreen?.()
  }

  const [reportMode, setReportMode] = useState(false)
  const [showShelters, setShowShelters] = useState(true)
  const [showHospitals, setShowHospitals] = useState(true)
  const [showReports, setShowReports] = useState(true)
  const [placesError, setPlacesError] = useState<string | null>(null)
  const [localities, setLocalities] = useState<Locality[]>([])
  const [searchText, setSearchText] = useState('')

  const [mapReady, setMapReady] = useState(false)
  const [zones, setZones] = useState<Zone[] | null>(null)
  const [showAllZones, setShowAllZones] = useState(false)
  const [zoneSearch, setZoneSearch] = useState('')
  const [rainPoint, setRainPoint] = useState<Endpoint | null>(null)
  const [weather, setWeather] = useState<WeatherResponse | null>(null)
  const [reports, setReports] = useState<CommunityReport[] | null>(null)
  const [reportListError, setReportListError] = useState<string | null>(null)
  const [reportPoint, setReportPoint] = useState<Endpoint | null>(null)
  const [reportKind, setReportKind] = useState<ReportKind>('flooded')
  const [reportNote, setReportNote] = useState('')
  const [reportActionError, setReportActionError] = useState<string | null>(null)
  const [confirmingReportId, setConfirmingReportId] = useState<string | null>(null)
  const [showAtlas, setShowAtlas] = useState(true)
  const [showRoadRisk, setShowRoadRisk] = useState(!IS_PHONE)
  const [roadLayerStatus, setRoadLayerStatus] = useState<RoadLayerStatus>(
    IS_PHONE ? 'ready' : 'loading',
  )
  const [layerError, setLayerError] = useState<string | null>(null)
  // Read when the layers are added, so a toggle flipped before then sticks.
  const layerVisibility = useRef({ atlas: true, roadRisk: !IS_PHONE })

  const [from, setFrom] = useState<Endpoint | null>(() => savedEndpoints().from)
  const [to, setTo] = useState<Endpoint | null>(() => savedEndpoints().to)
  const [nextClickSets, setNextClickSets] = useState<'from' | 'to'>('from')
  const [routes, setRoutes] = useState<Record<RouteMode, RouteResponse | null>>({
    safest: null,
    fastest: null,
  })
  const [failures, setFailures] = useState<RouteFailure[]>([])
  const [hovered, setHovered] = useState<RouteMode | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const filteredZones = zones?.filter((zone) =>
    `${zone.name} ${zone.district}`.toLocaleLowerCase().includes(zoneSearch.trim().toLocaleLowerCase()),
  ) ?? null

  useEffect(() => {
    let active = true
    getZones(replay)
      .then((response) => {
        if (active) setZones(response.zones)
      })
      .catch(() => {
        // Keep the neutral waiting state until the live endpoint is available.
        if (active) setZones(null)
      })

    return () => {
      active = false
    }
  }, [replay])
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
      if (reportModeRef.current) {
        const lat = Number(event.lngLat.lat.toFixed(5))
        const lon = Number(event.lngLat.lng.toFixed(5))
        reportModeRef.current = false
        setReportMode(false)
        setReportKind('flooded')
        setReportNote('')
        setReportActionError(null)
        setReportPoint({ label: `Picked ${lat}, ${lon}`, lat, lon })
        return
      }

      const placeLayers = Object.values(PLACE_LAYERS)
        .map((item) => item.layer)
        .filter((id) => instance.getLayer(id))
      if (
        placeLayers.length > 0 &&
        instance.queryRenderedFeatures(event.point, { layers: placeLayers }).length > 0
      ) {
        return
      }

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

      const popupContent = document.createElement('div')
      popupContent.className = 'rain-popup'
      const popupButton = document.createElement('button')
      popupButton.type = 'button'
      popupButton.className = 'rain-here-button'
      popupButton.textContent = 'Rain here'
      popupButton.addEventListener('click', (popupEvent) => {
        popupEvent.stopPropagation()
        setWeather(null)
        setRainPoint(picked)
      })
      const reportButton = document.createElement('button')
      reportButton.type = 'button'
      reportButton.className = 'rain-here-button report-here-button'
      reportButton.textContent = 'Report here'
      reportButton.addEventListener('click', (popupEvent) => {
        popupEvent.stopPropagation()
        setReportKind('flooded')
        setReportNote('')
        setReportActionError(null)
        setReportPoint(picked)
      })
      popupContent.append(popupButton, reportButton)
      rainPopup.current?.remove()
      rainPopup.current = new Popup({ closeButton: true, closeOnClick: false, offset: 12 })
        .setLngLat([picked.lon, picked.lat])
        .setDOMContent(popupContent)
        .addTo(instance)
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

      if (layerVisibility.current.roadRisk) addRoadLayer(instance)

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
      zoneMarkers.current.forEach((marker) => marker.remove())
      zoneMarkers.current = []
      reportMarkers.current.forEach((marker) => marker.remove())
      reportMarkers.current = []
      noDataLabels.current.forEach((marker) => marker.remove())
      noDataLabels.current = []
      rainPopup.current?.remove()
      rainPopup.current = null
      pickedMarker.current?.remove()
      pickedMarker.current = null
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

    if (showRoadRisk && mapReady && API && !instance.getSource('road-risk')) {
      setRoadLayerStatus('loading')
      addRoadLayer(instance)
      bringRouteToFront(instance)
    }

    if (instance.getLayer('road-risk-line')) {
      instance.setLayoutProperty(
        'road-risk-line',
        'visibility',
        roadVisibility,
      )
    }
  }, [showAtlas, showRoadRisk, mapReady])

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

  // Live watched zones are shown as status-coloured markers only; contract
  // sample rainfall and threshold values are intentionally never rendered.
  useEffect(() => {
    const instance = map.current
    if (!instance || !mapReady) return

    zoneMarkers.current.forEach((marker) => marker.remove())
    zoneMarkers.current = []
    if (!zones) return

    zones.forEach((zone) => {
      const element = document.createElement('button')
      element.type = 'button'
      element.className = `zone-marker zone-marker-${zone.status.toLowerCase()}`
      element.title = `${zone.name} — ${zone.status}`
      element.setAttribute('aria-label', `${zone.name}, ${zone.status}`)
      element.addEventListener('click', (event) => {
        event.stopPropagation()
        instance.flyTo({ center: [zone.lon, zone.lat], zoom: 12, duration: 800 })
      })
      zoneMarkers.current.push(
        new Marker({ element, anchor: 'center' })
          .setLngLat([zone.lon, zone.lat])
          .addTo(instance),
      )
    })
  }, [zones, mapReady])
  useEffect(() => {
    if (!rainPoint) return

    let active = true
    getWeather({ lat: rainPoint.lat, lon: rainPoint.lon }, replay)
      .then((response) => {
        if (active) setWeather(response)
      })
      .catch(() => {
        // Null weather keeps the card on its neutral waiting message.
        if (active) setWeather(null)
      })

    return () => {
      active = false
    }
  }, [rainPoint, replay])
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

  // Shelters and hospitals: circle layers (cheap even with hundreds of points)
  // with a click popup.
  useEffect(() => {
    const instance = map.current
    if (!instance || !mapReady || !API) return
    let cancelled = false

    const load = async (
      key: keyof typeof PLACE_LAYERS,
      fetcher: () => Promise<MapPlace[]>,
    ) => {
      const ids = PLACE_LAYERS[key]
      try {
        const items = await fetcher()
        if (cancelled || !map.current) return
        const data: FeatureCollection = {
          type: 'FeatureCollection',
          features: items.map((place) => ({
            type: 'Feature' as const,
            geometry: { type: 'Point' as const, coordinates: [place.lon, place.lat] },
            properties: {
              name: place.name,
              kind: place.kind,
              near: place.near_locality ?? '',
            },
          })),
        }
        if (instance.getSource(ids.source)) {
          ;(instance.getSource(ids.source) as GeoJSONSource).setData(data)
          return
        }
        instance.addSource(ids.source, { type: 'geojson', data })
        instance.addLayer({
          id: ids.layer,
          type: 'circle',
          source: ids.source,
          layout: {
            visibility: placeVisibility.current[key] ? 'visible' : 'none',
          },
          paint: {
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 4, 13, 8],
            'circle-color': ids.colour,
            'circle-stroke-color': '#ffffff',
            'circle-stroke-width': 2,
          },
        })
        instance.on('click', ids.layer, (event) => {
          const feature = event.features?.[0]
          if (!feature || feature.geometry.type !== 'Point') return
          const props = feature.properties as { name: string; kind: string; near: string }
          new Popup({ offset: 10 })
            .setLngLat(feature.geometry.coordinates as [number, number])
            .setDOMContent(
              popupBody([
                strongLine(props.name || 'Unnamed'),
                props.kind,
                props.near ? `Near ${props.near}` : '',
                'Source: OpenStreetMap',
              ].filter((part) => part !== '')),
            )
            .addTo(instance)
        })
        instance.on('mouseenter', ids.layer, () => {
          if (!reportModeRef.current) instance.getCanvas().style.cursor = 'pointer'
        })
        instance.on('mouseleave', ids.layer, () => {
          instance.getCanvas().style.cursor = reportModeRef.current ? 'crosshair' : ''
        })
        bringRouteToFront(instance)
      } catch (err) {
        if (!cancelled) {
          setPlacesError(err instanceof Error ? err.message : 'Could not load places.')
        }
      }
    }

    void load('shelters', getShelters)
    void load('hospitals', getHospitals)
    return () => {
      cancelled = true
    }
  }, [mapReady])

  useEffect(() => {
    const instance = map.current
    if (!instance || !mapReady) return
    placeVisibility.current = { shelters: showShelters, hospitals: showHospitals }
    ;(Object.keys(PLACE_LAYERS) as (keyof typeof PLACE_LAYERS)[]).forEach((key) => {
      const id = PLACE_LAYERS[key].layer
      if (instance.getLayer(id)) {
        instance.setLayoutProperty(
          id,
          'visibility',
          placeVisibility.current[key] ? 'visible' : 'none',
        )
      }
    })
  }, [showShelters, showHospitals, mapReady])

  // Locality list for the search box.
  useEffect(() => {
    if (!API) return
    getLocalities()
      .then(setLocalities)
      .catch(() => setLocalities([]))
  }, [])

  // Community reports: fetched on mount, on demand, and every minute.
  useEffect(() => {
    if (!API) return
    let cancelled = false
    const load = () =>
      getReports()
        .then((response) => {
          if (cancelled) return
          setReports(response.items)
          setReportListError(null)
        })
        .catch((err) => {
          if (!cancelled) setReportListError(errorDetail(err))
        })
    void load()
    const timer = window.setInterval(load, 60_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    const instance = map.current
    if (!instance || !mapReady) return

    reportMarkers.current.forEach((marker) => marker.remove())
    reportMarkers.current = []
    if (!showReports || !reports) return

    reports.forEach((report) => {
      const look = REPORT_LOOK[report.kind] ?? REPORT_LOOK.blocked
      const element = document.createElement('div')
      element.title = look.label
      element.textContent = look.glyph
      element.style.cssText =
        `display:flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:50%;` +
        `background:${look.colour};color:#fff;font-weight:800;font-size:14px;cursor:pointer;` +
        `border:2px solid ${report.trust === 'verified' ? '#ffffff' : '#ffffffaa'};` +
        `box-shadow:0 1px 5px rgb(0 0 0 / .45);opacity:${report.trust === 'verified' ? 1 : 0.8}`
      reportMarkers.current.push(
        new Marker({ element })
          .setLngLat([report.lon, report.lat])
          .setPopup(new Popup({ offset: 18 }).setDOMContent(reportPopup(report)))
          .addTo(instance),
      )
    })
  }, [reports, showReports, mapReady])

  // "Report a problem" mode: crosshair cursor, next click picks the point.
  useEffect(() => {
    reportModeRef.current = reportMode
    const instance = map.current
    if (!instance) return
    instance.getCanvas().style.cursor = reportMode ? 'crosshair' : ''
  }, [reportMode])

  const searchMatches =
    searchText.trim().length >= 2
      ? localities
          .filter((item) => item.name.toLowerCase().includes(searchText.trim().toLowerCase()))
          .slice(0, 6)
      : []

  const flyToLocality = (item: Locality) => {
    map.current?.flyTo({ center: [item.lon, item.lat], zoom: 13 })
    setSearchText('')
  }

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

    try {
      localStorage.setItem(ENDPOINTS_KEY, JSON.stringify({ from, to }))
    } catch {
      // Remembering the trip is optional.
    }
    const comparison = await getRouteComparison(
      { lat: from.lat, lon: from.lon },
      { lat: to.lat, lon: to.lon },
      { depart: depart || null, replay },
    )

    setRoutes({ safest: comparison.safest, fastest: comparison.fastest })
    if (!comparison.safest && !comparison.fastest) {
      setError(comparison.failures[0]?.detail ?? 'No route came back.')
    } else {
      setFailures(comparison.failures)
    }
    setLoading(false)
  }, [from, to, depart, replay])

  const handleReportSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!reportPoint) return

    setReportActionError(null)
    try {
      const report = await createReport({
        lat: reportPoint.lat,
        lon: reportPoint.lon,
        kind: reportKind,
        note: reportNote.trim(),
        reporter_id: getOrCreateReporterId(),
      })
      setReports((current) => (current ? [report, ...current] : [report]))
      setReportPoint(null)
      setReportNote('')
      // Redo a planned route, so Safest can steer around the new report.
      if (from && to) void handleSubmit()
    } catch (requestError) {
      setReportActionError(errorDetail(requestError))
    }
  }

  const handleConfirmReport = async (reportId: string) => {
    setReportActionError(null)
    setConfirmingReportId(reportId)
    try {
      const updated = await confirmReport(reportId, getOrCreateReporterId())
      setReports((current) =>
        current?.map((report) => (report.id === updated.id ? updated : report)) ?? null,
      )
    } catch (requestError) {
      setReportActionError(errorDetail(requestError))
    } finally {
      setConfirmingReportId(null)
    }
  }
  useEffect(() => {
    const instance = map.current
    if (!instance || !mapReady || !reportPoint) return
    const pin = document.createElement('div')
    pin.style.cssText =
      'width:18px;height:18px;border-radius:50%;background:#c92727;border:3px solid #fff;box-shadow:0 1px 5px rgb(0 0 0 / .5)'
    const marker = new Marker({ element: pin })
      .setLngLat([reportPoint.lon, reportPoint.lat])
      .addTo(instance)
    pickedMarker.current = marker
    return () => {
      marker.remove()
      pickedMarker.current = null
    }
  }, [reportPoint, mapReady])

  return (
    <div className="risk-map-wrapper">
      <div className="locality-search">
        <input
          type="search"
          value={searchText}
          onChange={(event) => setSearchText(event.target.value)}
          placeholder="Search a place, e.g. Rishikesh"
          aria-label="Search a locality"
        />
        {searchText.trim().length >= 2 && (
          <ul role="listbox">
            {searchMatches.length === 0 && <li className="locality-empty">No match</li>}
            {searchMatches.map((item) => (
              <li key={item.id}>
                <button type="button" onClick={() => flyToLocality(item)}>
                  {item.name}
                  <small>
                    {[item.parent, item.district].filter(Boolean).join(', ')}
                  </small>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

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
        depart={depart}
        onDepartChange={setDepart}
        replay={replay}
      />

      <div className="risk-map-stage" ref={stageRef}>
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

          <label>
            <input
              type="checkbox"
              checked={showShelters}
              onChange={(event) => setShowShelters(event.target.checked)}
            />
            <span className="dot dot-shelter" /> Shelters
          </label>

          <label>
            <input
              type="checkbox"
              checked={showHospitals}
              onChange={(event) => setShowHospitals(event.target.checked)}
            />
            <span className="dot dot-hospital" /> Hospitals
          </label>

          <label>
            <input
              type="checkbox"
              checked={showReports}
              onChange={(event) => setShowReports(event.target.checked)}
            />
            Community reports
          </label>

        </div>

        <div
          style={{
            position: 'absolute',
            top: 12,
            right: 56,
            zIndex: 8,
            display: 'flex',
            gap: 8,
          }}
        >
          <button
            type="button"
            onClick={() => setReportMode((on) => !on)}
            aria-pressed={reportMode}
            style={{
              padding: '10px 14px',
              border: 0,
              borderRadius: 6,
              background: reportMode ? '#6b7280' : '#c92727',
              color: '#fff',
              font: 'inherit',
              fontWeight: 700,
              cursor: 'pointer',
              boxShadow: '0 2px 10px rgb(0 0 0 / .3)',
            }}
          >
            {reportMode ? 'Cancel' : 'Report a problem'}
          </button>
          <button
            type="button"
            onClick={toggleFullscreen}
            aria-label={isFullscreen ? 'Exit full screen' : 'Full screen map'}
            style={{
              padding: '10px 14px',
              border: '1px solid #dbe3e8',
              borderRadius: 6,
              background: '#fff',
              color: '#1f2937',
              font: 'inherit',
              fontWeight: 700,
              cursor: 'pointer',
              boxShadow: '0 2px 10px rgb(0 0 0 / .3)',
            }}
          >
            {isFullscreen ? 'Exit full screen' : 'Full screen'}
          </button>
        </div>

        {reportMode && (
          <p
            role="status"
            style={{
              position: 'absolute',
              bottom: 12,
              left: '50%',
              transform: 'translateX(-50%)',
              zIndex: 7,
              margin: 0,
              padding: '8px 14px',
              borderRadius: 999,
              background: '#c92727',
              color: '#fff',
              fontSize: 13,
              fontWeight: 700,
              boxShadow: '0 2px 10px rgb(0 0 0 / .3)',
              whiteSpace: 'nowrap',
            }}
          >
            Click the map where the problem is
          </p>
        )}

        <div ref={mapContainer} className="risk-map-canvas" />

        {placesError && (
          <p className="risk-layer-status risk-layer-status-error" role="alert" style={{ top: 'auto', bottom: 12 }}>
            {placesError}
          </p>
        )}

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

      <section className="reports-panel" aria-labelledby="reports-heading">
        <div className="reports-panel-heading">
          <div>
            <h4 id="reports-heading">Community reports</h4>
            <p>Recent reports from the community</p>
          </div>
        </div>

        {reportPoint && (
          <form className="report-form" onSubmit={handleReportSubmit}>
            <h5>Report a problem</h5>
            <p>Selected map point: {reportPoint.lat.toFixed(5)}, {reportPoint.lon.toFixed(5)}</p>
            <label>
              Problem type
              <select value={reportKind} onChange={(event) => setReportKind(event.target.value as ReportKind)}>
                <option value="flooded">Flooded</option>
                <option value="blocked">Blocked</option>
                <option value="landslide">Landslide</option>
              </select>
            </label>
            <label>
              Note (optional)
              <textarea value={reportNote} onChange={(event) => setReportNote(event.target.value)} rows={3} maxLength={500} />
            </label>
            {reportActionError && <p className="report-error" role="alert">{reportActionError}</p>}
            <div className="report-form-actions">
              <button type="submit">Submit report</button>
              <button type="button" onClick={() => setReportPoint(null)}>Cancel</button>
            </div>
          </form>
        )}

        {reportActionError && !reportPoint && <p className="report-error" role="alert">{reportActionError}</p>}

        {reports === null ? (
          <p className="reports-waiting" role={reportListError === 'Waiting for live data' ? 'status' : 'alert'}>
            {reportListError ?? 'Waiting for live data'}
          </p>
        ) : reports.length === 0 ? (
          <p className="reports-waiting" role="status">No community reports available.</p>
        ) : (
          <ul className="reports-list">
            {reports.map((report) => (
              <li className="community-report" key={report.id}>
                <div className="community-report-heading">
                  <span className={`report-kind-badge report-kind-${report.kind}`}>
                    {REPORT_KIND_ICONS[report.kind]} {REPORT_KIND_LABELS[report.kind]}
                  </span>
                  <span className={`trust-badge trust-${report.trust}`}>{report.trust}</span>
                </div>
                <p className="community-report-note">{report.note || 'No note provided.'}</p>
                <p className="community-report-meta">
                  {new Date(report.created_at).toLocaleString()} · Near {report.near_locality}
                </p>
                <p className="community-report-trust-reason">{report.trust_reason}</p>
                <div className="community-report-footer">
                  <span>{report.confirmations} confirmations</span>
                  <button
                    type="button"
                    disabled={confirmingReportId === report.id}
                    onClick={() => handleConfirmReport(report.id)}
                  >
                    {confirmingReportId === report.id ? 'Confirming…' : 'Confirm'}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
      {rainPoint && (
        <section className="rain-card" aria-labelledby="rain-card-heading">
          <div className="rain-card-heading">
            <h4 id="rain-card-heading">Rain at selected point</h4>
            <button type="button" onClick={() => setRainPoint(null)}>Close</button>
          </div>
          {!weather ? (
            <p className="rain-waiting" role="status">Waiting for live data</p>
          ) : (
            <>
              <div className="rain-reading-grid">
                <div><span>Current rain</span><strong>{weather.current_mm_h} mm/h</strong></div>
                <div><span>Last 72 hours</span><strong>{weather.last_72h_mm} mm</strong></div>
              </div>
              <p className="rain-forecast-label">Next 24 hours</p>
              <div className="rain-chart" role="img" aria-label="Hourly rainfall forecast for the next 24 hours">
                {weather.hourly.map((hour) => {
                  const maximum = Math.max(...weather.hourly.map((item) => item.mm), 0)
                  const height = maximum > 0 ? Math.max(4, (hour.mm / maximum) * 100) : 4
                  return (
                    <span
                      key={hour.time}
                      className="rain-bar"
                      style={{ height: `${height}%` }}
                      title={`${new Date(hour.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}: ${hour.mm} mm`}
                    />
                  )
                })}
              </div>
              <div className="rain-meta">
                <span>As of {new Date(weather.as_of).toLocaleString()}</span>
                <span>Source: {weather.source}</span>
              </div>
            </>
          )}
        </section>
      )}
      <section className="zones-panel" aria-labelledby="zones-heading">
        <div className="zones-panel-heading">
          <div>
            <h4 id="zones-heading">Watched zones</h4>
            <p>Name, district and current status</p>
          </div>
          <div className="zones-status-legend" aria-label="Zone status colours">
            <span><i className="zone-dot zone-dot-safe" /> SAFE</span>
            <span><i className="zone-dot zone-dot-watch" /> WATCH</span>
            <span><i className="zone-dot zone-dot-critical" /> CRITICAL</span>
          </div>
        </div>
        {zones && zones.length > 0 && (
          <div className="zones-search">
            <label htmlFor="zones-search-input">Search zones</label>
            <input
              id="zones-search-input"
              type="search"
              value={zoneSearch}
              onChange={(event) => setZoneSearch(event.target.value)}
              placeholder="Search by zone or district"
            />
            <span className="zones-search-count">
              {filteredZones?.length ?? 0} {filteredZones?.length === 1 ? 'zone' : 'zones'}
            </span>
          </div>
        )}
        {zones === null ? (
          <p className="zones-waiting" role="status">Waiting for live data</p>
        ) : zones.length === 0 ? (
          <p className="zones-waiting" role="status">No watched zones available.</p>
        ) : filteredZones?.length === 0 ? (
          <p className="zones-waiting" role="status">No zones match your search.</p>
        ) : (
          <ul className="zones-list">
            {filteredZones?.slice(0, showAllZones ? filteredZones.length : 4).map((zone) => (
              <li key={zone.id}>
                <button
                  type="button"
                  className="zone-row"
                  onClick={() =>
                    map.current?.flyTo({
                      center: [zone.lon, zone.lat],
                      zoom: 12,
                      duration: 800,
                    })
                  }
                >
                  <i className={`zone-dot zone-dot-${zone.status.toLowerCase()}`} aria-hidden="true" />
                  <span className="zone-location">
                    <strong>{zone.name}</strong>
                    <small>{zone.district}</small>
                  </span>
                  <strong className={`zone-status zone-status-${zone.status.toLowerCase()}`}>
                    {zone.status}
                  </strong>
                </button>
              </li>
            ))}
          </ul>
        )}
        {filteredZones && filteredZones.length > 4 && (
          <button
            type="button"
            className="zones-toggle"
            aria-expanded={showAllZones}
            onClick={() => setShowAllZones((visible) => !visible)}
          >
            {showAllZones ? 'Show fewer zones' : `Show all ${filteredZones.length} zones`}
          </button>
        )}
      </section>
    </div>
  )
}
