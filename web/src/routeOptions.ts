import type { RiskClass } from './api'

export interface Place {
  id: string
  name: string
  lat: number
  lon: number
}

/** Real places users can route between. */
export const PLACES: Place[] = [
  { id: 'rishikesh', name: 'Rishikesh', lat: 30.0869, lon: 78.2676 },
  { id: 'dehradun', name: 'Dehradun', lat: 30.3165, lon: 78.0322 },
  { id: 'haridwar', name: 'Haridwar', lat: 29.9457, lon: 78.1642 },
  { id: 'uttarkashi', name: 'Uttarkashi', lat: 30.729, lon: 78.443 },
  { id: 'joshimath', name: 'Joshimath', lat: 30.5565, lon: 79.5644 },
  { id: 'haldwani', name: 'Haldwani', lat: 29.2183, lon: 79.513 },
  { id: 'pithoragarh', name: 'Pithoragarh', lat: 29.5829, lon: 80.2182 },
  { id: 'nainital', name: 'Nainital', lat: 29.3803, lon: 79.4636 },
]

export const RISK_COLOURS: Record<RiskClass, string> = {
  LOW: '#2e7d32',
  MODERATE: '#f1c40f',
  SIGNIFICANT: '#e67e22',
  EXTREME: '#c0392b',
}

/** Shown for segments the API rates as `null` — never presented as LOW. */
export const NO_DATA_COLOUR = '#8a8f98'

export const RISK_ORDER: RiskClass[] = [
  'LOW',
  'MODERATE',
  'SIGNIFICANT',
  'EXTREME',
]

export interface Endpoint {
  label: string
  lat: number
  lon: number
}
