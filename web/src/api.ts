export const API = import.meta.env.VITE_API_URL

if (!API) {
  throw new Error(
    'VITE_API_URL is not set. Copy web/.env.example to web/.env.local.',
  )
}

export type RiskClass = 'LOW' | 'MODERATE' | 'SIGNIFICANT' | 'EXTREME'
export type RiskSource = 'surveyed' | 'modelled'
export type RouteMode = 'fastest' | 'safest'
export type ZoneStatus = 'SAFE' | 'WATCH' | 'CRITICAL'

export interface ZoneRainThresholds {
  h1: number
  h3: number
  h24: number
}

export interface Zone {
  id: string
  name: string
  parent: string | null
  district: string
  lat: number
  lon: number
  hazard_class: RiskClass
  risk_source: RiskSource
  status: ZoneStatus
  rain_mm: ZoneRainThresholds & { antecedent_48h: number }
  watch: ZoneRainThresholds
  critical: ZoneRainThresholds
  soil_group: 'A' | 'B' | 'C' | 'D'
  catchment_km2: number
}

export type ReportKind = 'flooded' | 'blocked' | 'landslide'
export type ReportTrust = 'verified' | 'unverified'

export interface CommunityReport {
  id: string
  lat: number
  lon: number
  kind: ReportKind
  note: string
  created_at: string
  trust: ReportTrust
  trust_reason: string
  confirmations: number
  near_locality: string
}

export interface ReportsResponse {
  items: CommunityReport[]
}

export interface CreateReportRequest {
  lat: number
  lon: number
  kind: ReportKind
  note: string
  reporter_id: string
}
export type WeatherSource = 'live' | 'relay' | 'replay'

export interface WeatherResponse {
  as_of: string
  source: WeatherSource
  current_mm_h: number
  last_72h_mm: number
  hourly: { time: string; mm: number }[]
}
export interface ZonesResponse {
  as_of: string
  source: 'live' | 'relay' | 'replay'
  replay: string | null
  threshold_basis: string
  zones: Zone[]
}

export interface RouteSegment {
  start: number
  end: number
  /** null when the stretch has no hazard rating, e.g. road outside Uttarakhand. */
  risk_class: RiskClass | null
  risk_source: RiskSource
  landslide_prone: boolean
  forecast_rain_mm: number | null
  report_id: string | null
}

export interface RouteResponse {
  mode: RouteMode
  depart: string
  replay: string | null
  distance_km: number
  duration_min: number
  geometry: {
    type: 'LineString'
    /** GeoJSON order: [lon, lat]. */
    coordinates: [number, number][]
  }
  segments: RouteSegment[]
  summary: {
    km_by_class: Record<RiskClass, number>
    extreme_segments: number
  }
  notes: string[]
}

export interface ApiError {
  error: string
  detail: string
}

async function reportRequest<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(url, init)
  } catch {
    throw new Error('Waiting for live data')
  }

  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`
    try {
      const body: ApiError = await response.json()
      if (body?.detail) detail = body.detail
    } catch {
      // Keep the status text when the response is not the documented error shape.
    }
    throw new Error(detail)
  }

  return (await response.json()) as T
}

export async function getReports(): Promise<ReportsResponse> {
  return reportRequest<ReportsResponse>(`${API}/reports`)
}

export async function createReport(
  request: CreateReportRequest,
): Promise<CommunityReport> {
  return reportRequest<CommunityReport>(`${API}/reports`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
}

export async function confirmReport(
  id: string,
  reporterId: string,
): Promise<CommunityReport> {
  return reportRequest<CommunityReport>(`${API}/reports/${encodeURIComponent(id)}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reporter_id: reporterId }),
  })
}
/** Fetch watched zones without falling back to the example contract file. */
export async function getZones(replay: string | null = null): Promise<ZonesResponse> {
  if (!API) {
    throw new Error(
      'VITE_API_URL is not set, so the app does not know where the FloodSafe API is.',
    )
  }

  let response: Response
  try {
    response = await fetch(`${API}/zones${replay ? `?replay=${replay}` : ''}`)
  } catch {
    throw new Error(`Could not reach the FloodSafe API at ${API}.`)
  }

  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`
    try {
      const body: ApiError = await response.json()
      if (body?.detail) detail = body.detail
    } catch {
      // Keep the status text when the response is not the documented error shape.
    }
    throw new Error(detail)
  }

  return (await response.json()) as ZonesResponse
}
export interface LatLon {
  lat: number
  lon: number
}

/** Fetch weather for a selected map point. */
export async function getWeather(
  point: LatLon,
  replay: string | null = null,
): Promise<WeatherResponse> {
  if (!API) {
    throw new Error(
      'VITE_API_URL is not set, so the app does not know where the FloodSafe API is.',
    )
  }

  const params = new URLSearchParams({
    lat: String(point.lat),
    lon: String(point.lon),
  })
  if (replay) params.set('replay', replay)

  let response: Response
  try {
    response = await fetch(`${API}/weather?${params}`)
  } catch {
    throw new Error(`Could not reach the FloodSafe API at ${API}.`)
  }

  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`
    try {
      const body: ApiError = await response.json()
      if (body?.detail) detail = body.detail
    } catch {
      // Keep the status text when the response is not the documented error shape.
    }
    throw new Error(detail)
  }

  return (await response.json()) as WeatherResponse
}
/** Throws the API's `detail` sentence when the response is not 200. */
/** When to leave (ISO, India time) and which past day to replay; both optional. */
export interface RouteWhen {
  depart?: string | null
  replay?: string | null
}

export async function getRoute(
  from: LatLon,
  to: LatLon,
  mode: RouteMode,
  when: RouteWhen = {},
): Promise<RouteResponse> {
  const params = new URLSearchParams({
    from: `${from.lat},${from.lon}`,
    to: `${to.lat},${to.lon}`,
    mode,
  })
  if (when.depart) params.set('depart', when.depart)
  if (when.replay) params.set('replay', when.replay)

  if (!API) {
    throw new Error(
      'VITE_API_URL is not set, so the app does not know where the FloodSafe API is.',
    )
  }

  let response: Response
  try {
    response = await fetch(`${API}/route?${params}`)
  } catch {
    throw new Error(`Could not reach the FloodSafe API at ${API}.`)
  }

  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`
    try {
      const body: ApiError = await response.json()
      if (body?.detail) detail = body.detail
    } catch {
      // Response body was not the documented error shape; keep the status text.
    }
    throw new Error(detail)
  }

  return (await response.json()) as RouteResponse
}

export interface RouteFailure {
  mode: RouteMode
  detail: string
}

export interface RouteComparison {
  safest: RouteResponse | null
  fastest: RouteResponse | null
  failures: RouteFailure[]
}

/**
 * Both modes for the same pair of points, asked for together. One mode can
 * fail while the other succeeds, so each failure is reported with the API's
 * own detail rather than losing the route that did come back.
 */
export async function getRouteComparison(
  from: LatLon,
  to: LatLon,
  when: RouteWhen = {},
): Promise<RouteComparison> {
  const modes: RouteMode[] = ['safest', 'fastest']
  const settled = await Promise.allSettled(
    modes.map((mode) => getRoute(from, to, mode, when)),
  )

  const comparison: RouteComparison = {
    safest: null,
    fastest: null,
    failures: [],
  }

  settled.forEach((result, index) => {
    const mode = modes[index]
    if (result.status === 'fulfilled') {
      comparison[mode] = result.value
    } else {
      comparison.failures.push({
        mode,
        detail:
          result.reason instanceof Error
            ? result.reason.message
            : 'The routing service could not be reached.',
      })
    }
  })

  return comparison
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (!API) {
    throw new Error(
      'VITE_API_URL is not set, so the app does not know where the FloodSafe API is.',
    )
  }

  let response: Response
  try {
    response = await fetch(`${API}${path}`, init)
  } catch {
    throw new Error(`Could not reach the FloodSafe API at ${API}.`)
  }

  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`
    try {
      const body: ApiError = await response.json()
      if (body?.detail) detail = body.detail
    } catch {
      // Not the documented error shape; keep the status text.
    }
    throw new Error(detail)
  }

  return (await response.json()) as T
}

// ---- Shelters, hospitals, localities -----------------------------------

export interface MapPlace {
  id: string
  name: string
  /** OpenStreetMap type, e.g. shelter, hospital, clinic. */
  kind: string
  lat: number
  lon: number
  near_locality: string | null
  source: string
}

export interface Locality {
  id: string
  name: string
  parent: string | null
  district: string
  lat: number
  lon: number
}

export const getShelters = () =>
  request<{ items: MapPlace[] }>('/shelters').then((body) => body.items)

export const getHospitals = () =>
  request<{ items: MapPlace[] }>('/hospitals').then((body) => body.items)

export const getLocalities = () =>
  request<{ items: Locality[] }>('/localities').then((body) => body.items)

// ---- SOS ----------------------------------------------------------------

export interface SosRequest {
  name: string
  lat: number
  lon: number
  numbers: string[]
  share_with_responders?: boolean
}

export interface SosResponse {
  status: 'sent' | 'fallback'
  sms_body: string
  link: string
}

// A sleeping free server can take a minute to answer; in an emergency the
// phone's own SMS app is opened instead after this long.
const SOS_TIMEOUT_MS = 8_000

export const sendSos = (body: SosRequest) =>
  request<SosResponse>('/sos', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(SOS_TIMEOUT_MS),
  })

export function getReporterId() {
  const key = 'floodsafe-reporter-id'
  try {
    let id = localStorage.getItem(key)
    if (!id) {
      id = `anon-${crypto.randomUUID().slice(0, 8)}`
      localStorage.setItem(key, id)
    }
    return id
  } catch {
    return 'anon-unknown'
  }
}

// ---- Official warnings, push, responder ------------------------------------

export interface OfficialAlert {
  id: string
  headline: string
  /** Empty when the alert was issued in English only. */
  headline_hi: string
  severity: string
  event: string
  area: string
  sender: string
  effective: string
  expires: string
  link: string
}

export interface WarningsResponse {
  status: 'ok' | 'unavailable'
  as_of: string | null
  alerts: OfficialAlert[]
}

export const getWarnings = () => request<WarningsResponse>('/warnings')

export interface ResponderSummary {
  as_of: string
  /** null when the database could not be read. */
  reports_last_24h: number | null
  critical_zones: { id: string; name: string; status: ZoneStatus }[] | null
  shelters_count: number | null
  sos_pings: { lat: number; lon: number; time: string }[]
}

export const getResponderSummary = () => request<ResponderSummary>('/responder/summary')

export const getHealth = () =>
  request<{ status: string; nodes: number; edges: number }>('/health')

export const getPushKey = () =>
  request<{ public_key: string }>('/push/public-key').then((body) => body.public_key)

export const subscribePush = (subscription: PushSubscriptionJSON, zoneId: string) =>
  request<{ ok: boolean }>('/push/subscribe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ subscription, zone_id: zoneId }),
  })
