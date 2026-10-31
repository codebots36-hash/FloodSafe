export const API = import.meta.env.VITE_API_URL

export type RiskClass = 'LOW' | 'MODERATE' | 'SIGNIFICANT' | 'EXTREME'
export type RiskSource = 'surveyed' | 'modelled'
export type RouteMode = 'fastest' | 'safest'

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

export interface LatLon {
  lat: number
  lon: number
}

/** Throws the API's `detail` sentence when the response is not 200. */
export async function getRoute(
  from: LatLon,
  to: LatLon,
  mode: RouteMode,
): Promise<RouteResponse> {
  const params = new URLSearchParams({
    from: `${from.lat},${from.lon}`,
    to: `${to.lat},${to.lon}`,
    mode,
  })

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
): Promise<RouteComparison> {
  const modes: RouteMode[] = ['safest', 'fastest']
  const settled = await Promise.allSettled(
    modes.map((mode) => getRoute(from, to, mode)),
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

// ---- Community reports --------------------------------------------------

export type ReportKind = 'flooded' | 'blocked' | 'landslide'

export interface Report {
  id: string
  lat: number
  lon: number
  kind: ReportKind
  note: string
  created_at: string
  trust: 'verified' | 'unverified'
  trust_reason: string
  confirmations: number
  near_locality: string
}

export const getReports = () =>
  request<{ items: Report[] }>('/reports').then((body) => body.items)

// ---- SOS ----------------------------------------------------------------

export interface SosRequest {
  name: string
  lat: number
  lon: number
  numbers: string[]
}

export interface SosResponse {
  status: 'sent' | 'fallback'
  sms_body: string
  link: string
}

export const sendSos = (body: SosRequest) =>
  request<SosResponse>('/sos', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
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

export const createReport = (body: {
  lat: number
  lon: number
  kind: ReportKind
  note: string
}) =>
  request<Report>('/reports', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, reporter_id: getReporterId() }),
  })
