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

  const response = await fetch(`${API}/route?${params}`)

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
