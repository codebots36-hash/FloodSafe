// FloodSafe service worker: offline copies and push alerts.
//
// Everything the app reads (its own files, the API's GET answers: the last
// route, shelters, hospitals, zones, warnings) is fetched from the network
// first and kept, so with no signal the last copy is served. Map tiles are
// served from the cache first: they never change, and only tiles you have
// already looked at are kept (OpenStreetMap does not allow bulk downloading).
// ponytail: no size cap on the caches; add eviction if phones run short of space.

const CACHE = 'floodsafe-v1'
const TILES = 'floodsafe-tiles-v1'
const SHELL = ['/', '/manifest.webmanifest', '/icon-192.png']

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)))
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE && k !== TILES).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

async function networkFirst(request, fallbackUrl) {
  const cache = await caches.open(CACHE)
  try {
    const response = await fetch(request)
    if (response.ok) cache.put(request, response.clone())
    return response
  } catch (error) {
    const kept = (await cache.match(request)) || (fallbackUrl && (await cache.match(fallbackUrl)))
    if (kept) return kept
    throw error
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(TILES)
  const kept = await cache.match(request)
  if (kept) return kept
  const response = await fetch(request)
  if (response.ok) cache.put(request, response.clone())
  return response
}

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  if (url.hostname === 'tile.openstreetmap.org') {
    event.respondWith(cacheFirst(request))
  } else if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request, '/'))
  } else if (url.protocol.startsWith('http')) {
    event.respondWith(networkFirst(request))
  }
})

self.addEventListener('push', (event) => {
  let alert = { title: 'FloodSafe', body: 'A zone you follow has changed status.' }
  try {
    alert = { ...alert, ...event.data.json() }
  } catch {
    // Keep the generic text if the payload is not JSON.
  }
  event.waitUntil(
    self.registration.showNotification(alert.title, {
      body: alert.body,
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      tag: alert.zone_id || 'floodsafe',
      data: { url: '/#guidance' },
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  event.waitUntil(
    self.clients.matchAll({ type: 'window' }).then((windows) => {
      const open = windows.find((w) => 'focus' in w)
      return open ? open.focus() : self.clients.openWindow(event.notification.data.url)
    }),
  )
})
