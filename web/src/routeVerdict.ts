import type { RiskClass, RouteResponse } from './api'

/** Worst first: the verdict names the most serious class the routes differ on. */
const COMPARED_CLASSES: RiskClass[] = ['EXTREME', 'SIGNIFICANT', 'MODERATE']

/** Below this, a difference is rounding noise rather than a real detour. */
const SIGNIFICANT_KM = 0.1

function formatKm(km: number) {
  return km >= 10 ? `${Math.round(km)} km` : `${km.toFixed(1)} km`
}

function kmIn(route: RouteResponse, riskClass: RiskClass) {
  return route.summary.km_by_class[riskClass] ?? 0
}

/**
 * One line comparing the two routes, built only from each response's
 * distance_km and summary.km_by_class — nothing is assumed about the roads.
 */
export function routeVerdict(
  safest: RouteResponse,
  fastest: RouteResponse,
): string {
  const distanceDiff = safest.distance_km - fastest.distance_km

  const avoided = COMPARED_CLASSES.map((riskClass) => ({
    riskClass,
    km: kmIn(fastest, riskClass) - kmIn(safest, riskClass),
  }))

  const saving = avoided.find((entry) => entry.km >= SIGNIFICANT_KM)
  const cost = avoided.find((entry) => entry.km <= -SIGNIFICANT_KM)

  // A saving in one class can come with more road in another; say both so
  // the trade-off is not hidden.
  let riskPhrase: string
  if (saving && cost) {
    riskPhrase =
      `avoids ${formatKm(saving.km)} of ${saving.riskClass} road, ` +
      `but adds ${formatKm(-cost.km)} of ${cost.riskClass} road`
  } else if (saving) {
    riskPhrase = `avoids ${formatKm(saving.km)} of ${saving.riskClass} road`
  } else if (cost) {
    riskPhrase = `carries ${formatKm(-cost.km)} more ${cost.riskClass} road`
  } else {
    riskPhrase = 'carries the same risk mix'
  }

  if (Math.abs(distanceDiff) < SIGNIFICANT_KM) {
    if (!saving && !cost) {
      return 'Both modes come back the same length with the same risk mix.'
    }
    return `Safest is the same length as Fastest and ${riskPhrase}.`
  }

  const lengthPhrase =
    distanceDiff > 0
      ? `Safest is ${formatKm(distanceDiff)} longer`
      : `Safest is ${formatKm(-distanceDiff)} shorter`

  return `${lengthPhrase} and ${riskPhrase}.`
}
