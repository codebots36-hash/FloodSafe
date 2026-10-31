# API contracts

One example file per endpoint. The API returns exactly these fields with these types. **Values show shape, not real data:** place names and the Rishikesh (30.0869, 78.2676) and Dehradun (30.3165, 78.0322) coordinates are real, but every reading, score, count, id and name marked "Example" is made up to show the type.

Conventions:

- Coordinates in GeoJSON are `[lon, lat]`. Everywhere else they are separate `lat` and `lon` numbers.
- Times are ISO 8601 with the India offset, for example `2026-10-31T14:00:00+05:30`.
- Risk classes are `LOW`, `MODERATE`, `SIGNIFICANT` or `EXTREME`. `risk_source` is `surveyed` (from the published hazard atlas) or `modelled` (from our statewide model).
- `replay` is `null` for live data, or the past date (`YYYY-MM-DD`) being replayed.
- Optional fields may be added later. No field is ever renamed or removed.

## GET /health → [health.json](health.json)

- `status`: always `"ok"` when the server is up.

## GET /route?from=lat,lon&to=lat,lon&mode=fastest|safest&depart=ISO&replay=YYYY-MM-DD → [route.json](route.json)

`depart` and `replay` are optional.

- `mode`: `"fastest"` or `"safest"`, as requested.
- `depart`: departure time used for the rain forecast (ISO time).
- `replay`: `null`, or the replayed date.
- `distance_km`: route length in km (number).
- `duration_min`: driving time in minutes (number).
- `geometry`: GeoJSON LineString of the whole route.
- `segments[]`: consecutive stretches of the route, each with:
  - `start`, `end`: indexes into `geometry.coordinates` where the stretch begins and ends.
  - `risk_class`: risk class of the stretch, or `null` where the road is outside Uttarakhand and has no score. Draw `null` as "no data", never as `LOW`.
  - `risk_source`: `surveyed` or `modelled`, or `null` when `risk_class` is `null`.
  - `landslide_prone`: `true` if the stretch is in a landslide-prone area.
  - `forecast_rain_mm`: rain forecast for the time you reach it, in mm, or `null` if unknown.
  - `report_id`: id of a community report on this stretch, or `null`.
- `summary.km_by_class`: km of route in each risk class (`LOW`, `MODERATE`, `SIGNIFICANT`, `EXTREME`). Stretches with no score are left out, so these can sum to less than `distance_km`; `notes` then says how many km have no score.
- `summary.extreme_segments`: number of segments rated `EXTREME`.
- `notes[]`: short sentences to show the user about this route.

## GET /weather?lat=&lon=&replay= → [weather.json](weather.json)

- `as_of`: when the reading was taken (ISO time).
- `source`: `live` (fetched now), `relay` (from the hourly relay copy) or `replay` (historical).
- `current_mm_h`: current rain rate in mm per hour.
- `last_72h_mm`: total rain in the past 72 hours, in mm.
- `hourly[]`: forecast for the next 24 hours (24 items; the example shows 3), each `time` (ISO hour) and `mm` (rain in that hour).

## GET /zones?replay= → [zones.json](zones.json)

- `as_of`: time of the rain data used (ISO time).
- `source`: `live`, `relay` or `replay`, as in /weather.
- `replay`: `null`, or the replayed date.
- `threshold_basis`: one sentence on how the WATCH and CRITICAL thresholds were set.
- `zones[]`: one per watched place, each with:
  - `id`: stable zone id (also used by `/push/subscribe`).
  - `name`: place name.
  - `parent`: larger place it belongs to, or `null`.
  - `district`: district name.
  - `lat`, `lon`: zone centre.
  - `hazard_class`: risk class of the area.
  - `risk_source`: `surveyed` or `modelled`.
  - `status`: `SAFE`, `WATCH` or `CRITICAL`.
  - `rain_mm`: observed rain in mm over the last 1 h (`h1`), 3 h (`h3`), 24 h (`h24`), and the 48 h before that (`antecedent_48h`).
  - `watch`: rain in mm over 1, 3 and 24 h (`h1`, `h3`, `h24`) at which the zone turns WATCH.
  - `critical`: the same, for CRITICAL.
  - `soil_group`: hydrologic soil group, `A` to `D`.
  - `catchment_km2`: catchment area upstream of the zone, in km².

## Reports

A **report** ([report.json](report.json)) is:

- `id`: report id.
- `lat`, `lon`: where it was reported.
- `kind`: `flooded`, `blocked` or `landslide`.
- `note`: free text from the reporter (may be empty).
- `created_at`: when it was posted (ISO time).
- `trust`: `verified` or `unverified`.
- `trust_reason`: short sentence saying why it has that trust level.
- `confirmations`: number of other people who confirmed it.
- `near_locality`: name of the nearest locality.

### GET /reports → [reports.json](reports.json)

- `items[]`: list of reports.

### POST /reports, body [report_create_request.json](report_create_request.json) → [report.json](report.json)

- `lat`, `lon`: where the problem is.
- `kind`: `flooded`, `blocked` or `landslide`.
- `note`: free text (may be empty).
- `reporter_id`: random id the app keeps on the device.

### POST /reports/{id}/confirm, body [report_confirm_request.json](report_confirm_request.json) → [report.json](report.json)

- `reporter_id`: id of the person confirming.

## GET /shelters → [shelters.json](shelters.json) and GET /hospitals → [hospitals.json](hospitals.json)

- `items[]`: list of places, each with:
  - `id`: OpenStreetMap element id, for example `osm:node/123`.
  - `name`: place name.
  - `kind`: OpenStreetMap type, for example `shelter`, `hospital` or `clinic`.
  - `lat`, `lon`: location.
  - `near_locality`: name of the nearest locality.
  - `source`: always `"OpenStreetMap"`.

## GET /localities → [localities.json](localities.json)

- `items[]`: places the user can search for, each with:
  - `id`: locality id.
  - `name`: place name.
  - `parent`: larger place it belongs to, or `null`.
  - `district`: district name.
  - `lat`, `lon`: location.

## GET /warnings → [warnings.json](warnings.json)

- `status`: `ok`, or `unavailable` if the official alert feed could not be read.
- `as_of`: when the feed was last read (ISO time).
- `alerts[]`: current official alerts for Uttarakhand, each with:
  - `id`: alert id from the feed.
  - `headline`: headline in English.
  - `headline_hi`: headline in Hindi.
  - `severity`: severity as given by the feed, for example `Severe`.
  - `event`: event type, for example `Heavy Rain`.
  - `area`: area the alert covers.
  - `sender`: agency that issued it.
  - `effective`: when it starts (ISO time).
  - `expires`: when it ends (ISO time).
  - `link`: URL of the alert.

## GET /push/public-key → [push_key.json](push_key.json)

- `public_key`: VAPID public key (base64url) for the browser's push subscription.

## POST /push/subscribe, body [push_subscribe_request.json](push_subscribe_request.json) → [ok.json](ok.json)

- `subscription`: the browser's PushSubscription as JSON (`endpoint`, `expirationTime`, `keys.p256dh`, `keys.auth`).
- `zone_id`: id of the zone to get alerts for.
- Response `ok`: `true` when saved.

## POST /sos, body [sos_request.json](sos_request.json) → [sos.json](sos.json)

Request:

- `name`: name of the person in trouble.
- `lat`, `lon`: their location.
- `numbers[]`: phone numbers to text, with country code.

Response:

- `status`: `sent` (the server sent the SMS) or `fallback` (the app should open the phone's SMS app with `sms_body`).
- `sms_body`: the message text, written by the server.
- `link`: map link to the location.

## GET /responder/summary → [responder_summary.json](responder_summary.json)

- `as_of`: when the summary was made (ISO time).
- `reports_last_24h`: number of community reports in the last 24 hours.
- `critical_zones[]`: zones not SAFE, each `id`, `name`, `status`.
- `shelters_count`: number of shelters known.
- `sos_pings[]`: recent SOS locations, each `lat`, `lon`, `time`.

## GET /layers/road-risk → [layer_road_risk.geojson](layer_road_risk.geojson)

GeoJSON FeatureCollection of road LineStrings rated `MODERATE` or worse. Properties:

- `risk_class`: risk class of the road.
- `risk_source`: `surveyed` or `modelled`.

## GET /layers/atlas → [layer_atlas.geojson](layer_atlas.geojson)

GeoJSON FeatureCollection of hazard atlas polygons. Properties:

- `hazard_class`: risk class of the polygon.

## Any error → [error.json](error.json)

Sent with a matching HTTP status (400, 404, 500 and so on).

- `error`: short machine-readable code, for example `not_found`.
- `detail`: one sentence for people.
