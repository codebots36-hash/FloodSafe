# FloodSafe web app

The FloodSafe app: React, TypeScript and Vite, with MapLibre GL for the map. It installs as a PWA and keeps the last route, shelters and viewed map tiles for use without signal.

## Run it

Create `.env.local` in this folder with the API's address (no trailing slash):

```
VITE_API_URL=http://localhost:8000
```

Then:

```
npm install
npm run dev
```

Restart the dev server after changing `.env.local`. `npm run build` type-checks and builds to `dist/`, and `npm run lint` runs ESLint.

## What is where

| File | What it does |
| --- | --- |
| `src/App.tsx` | app shell and navigation |
| `src/api.ts` | calls to the API; shapes match [contracts/](../contracts/README.md) |
| `src/components/RiskMap.tsx` | map with road risk, zones, reports and shelters |
| `src/components/RoutePanel.tsx` | Fastest and Safest routes, departure time and the route verdict |
| `src/components/ReplayMode.tsx` | replay a past day on the rain that actually fell |
| `src/components/OfficialWarnings.tsx` | NDMA SACHET warnings, shown as issued |
| `src/components/NotificationSettings.tsx` | push alerts when your zone turns CRITICAL |
| `src/components/FloodGuidance.tsx` | a zone's rain against its WATCH and CRITICAL levels, with advice for its status |
| `src/components/ResponderDashboard.tsx` | responder view of reports, zones not SAFE, shelters and shared SOS pings |
| `src/settings.ts` | language (English or Hindi), text size and the replay date |
| `public/sw.js`, `public/manifest.webmanifest` | service worker and install manifest |
