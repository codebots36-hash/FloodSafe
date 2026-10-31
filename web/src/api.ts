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

/** Fetch watched zones without falling back to the example contract file. */
export async function getZones(): Promise<ZonesResponse> {
  if (!API) {
    throw new Error(
      'VITE_API_URL is not set, so the app does not know where the FloodSafe API is.',
    )
  }

  let response: Response
  try {
    response = await fetch(`${API}/zones`)
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
export async function getWeather(point: LatLon): Promise<WeatherResponse> {
  if (!API) {
    throw new Error(
      'VITE_API_URL is not set, so the app does not know where the FloodSafe API is.',
    )
  }

  const params = new URLSearchParams({
    lat: String(point.lat),
    lon: String(point.lon),
  })

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


