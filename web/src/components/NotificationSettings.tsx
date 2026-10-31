import { useEffect, useState } from 'react'
import { getPushKey, getZones, subscribePush, type Zone } from '../api'
import { useT } from '../settings'

function keyBytes(base64url: string) {
  const base64 = (base64url + '='.repeat((4 - (base64url.length % 4)) % 4))
    .replace(/-/g, '+')
    .replace(/_/g, '/')
  return Uint8Array.from(atob(base64), (char) => char.charCodeAt(0))
}

const IS_IOS = /iphone|ipad|ipod/i.test(navigator.userAgent)
const STANDALONE = window.matchMedia('(display-mode: standalone)').matches

/** Subscribe this device to a push alert when one zone turns CRITICAL. */
export default function NotificationSettings() {
  const t = useT()
  const [zones, setZones] = useState<Zone[]>([])
  const [zoneId, setZoneId] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    getZones()
      .then((body) => setZones([...body.zones].sort((a, b) => a.name.localeCompare(b.name))))
      .catch(() => setZones([]))
  }, [])

  const supported = 'serviceWorker' in navigator && 'PushManager' in window

  const turnOn = async () => {
    if (!zoneId) return
    setBusy(true)
    setMessage(null)
    try {
      if ((await Notification.requestPermission()) !== 'granted') {
        throw new Error('Notifications are blocked for this site. Allow them in the browser settings.')
      }
      const registration = await navigator.serviceWorker.ready
      const subscription =
        (await registration.pushManager.getSubscription()) ??
        (await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: keyBytes(await getPushKey()),
        }))
      await subscribePush(subscription.toJSON(), zoneId)
      const name = zones.find((zone) => zone.id === zoneId)?.name ?? zoneId
      setMessage(`Done. This device will be alerted when ${name} turns CRITICAL.`)
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Could not turn on alerts.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="notification-card">
      <div className="section-heading">
        <div>
          <p className="eyebrow">{t('ALERT PREFERENCES')}</p>
          <h3>{t('Push notifications')}</h3>
          <p>{t('Get an alert on this device when the zone you choose turns CRITICAL.')}</p>
        </div>
      </div>

      {IS_IOS && !STANDALONE ? (
        <p className="data-note">
          On iPhone, alerts work once FloodSafe is on the Home Screen: tap Share, then “Add to Home
          Screen”, and open it from there.
        </p>
      ) : !supported ? (
        <p className="data-note">This browser cannot receive push alerts.</p>
      ) : (
        <div className="notification-options">
          <label>
            {t('Zone')}{' '}
            <select value={zoneId} onChange={(event) => setZoneId(event.target.value)}>
              <option value="">{t('Choose a zone')}</option>
              {zones.map((zone) => (
                <option key={zone.id} value={zone.id}>
                  {zone.name} ({zone.district})
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="primary-button" disabled={!zoneId || busy} onClick={turnOn}>
            {busy ? '…' : t('Turn on alerts')}
          </button>
        </div>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  )
}
