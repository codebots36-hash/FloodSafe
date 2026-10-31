
import { useEffect, useRef, useState } from 'react'
import {
  Map as MapLibreMap,
  NavigationControl,
  setWorkerUrl,
} from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'

setWorkerUrl(workerUrl)

const atlasUrl = '/data/layer_atlas.geojson'
const roadRiskUrl = '/data/layer_road_risk.geojson'

export default function RiskMap() {
  const mapContainer = useRef<HTMLDivElement>(null)
  const map = useRef<MapLibreMap | null>(null)

  const [showAtlas, setShowAtlas] = useState(true)
  const [showRoadRisk, setShowRoadRisk] = useState(true)

  useEffect(() => {
    if (!mapContainer.current || map.current) return

    const instance = new MapLibreMap({
      container: mapContainer.current,
      center: [78.25, 30.07],
      zoom: 10,
      style: {
        version: 8,
        sources: {
          osm: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            attribution:
              '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
          },
        },
        layers: [
          {
            id: 'osm-tiles',
            type: 'raster',
            source: 'osm',
          },
        ],
      },
    })

    map.current = instance
    instance.addControl(new NavigationControl(), 'top-right')

    instance.on('load', async () => {
      try {
        const [atlasResponse, roadResponse] = await Promise.all([
          fetch(atlasUrl),
          fetch(roadRiskUrl),
        ])

        if (!atlasResponse.ok || !roadResponse.ok) {
          throw new Error('Could not load hazard GeoJSON files')
        }

        const [atlasData, roadRiskData] = await Promise.all([
          atlasResponse.json(),
          roadResponse.json(),
        ])

        if (map.current !== instance) return

        instance.addSource('atlas-risk', {
          type: 'geojson',
          data: atlasData,
        })

        instance.addLayer({
          id: 'atlas-risk-fill',
          type: 'fill',
          source: 'atlas-risk',
          paint: {
            'fill-color': '#e74c3c',
            'fill-opacity': 0.3,
          },
        })

        instance.addLayer({
          id: 'atlas-risk-outline',
          type: 'line',
          source: 'atlas-risk',
          paint: {
            'line-color': '#c0392b',
            'line-width': 2,
          },
        })

        instance.addSource('road-risk', {
          type: 'geojson',
          data: roadRiskData,
        })

        instance.addLayer({
          id: 'road-risk-line',
          type: 'line',
          source: 'road-risk',
          paint: {
            'line-color': '#8e44ad',
            'line-width': 5,
            'line-opacity': 0.85,
          },
        })

        // Set initial visibility to match the default checked controls.
        instance.setLayoutProperty(
          'atlas-risk-fill',
          'visibility',
          'visible',
        )
        instance.setLayoutProperty(
          'atlas-risk-outline',
          'visibility',
          'visible',
        )
        instance.setLayoutProperty(
          'road-risk-line',
          'visibility',
          'visible',
        )
      } catch (error) {
        console.error('Failed to load FloodSafe hazard layers:', error)
      }
    })

    return () => {
      instance.remove()
      map.current = null
    }
  }, [])

  useEffect(() => {
    const instance = map.current
    if (!instance) return

    const atlasVisibility = showAtlas ? 'visible' : 'none'
    const roadVisibility = showRoadRisk ? 'visible' : 'none'

    if (instance.getLayer('atlas-risk-fill')) {
      instance.setLayoutProperty(
        'atlas-risk-fill',
        'visibility',
        atlasVisibility,
      )
    }

    if (instance.getLayer('atlas-risk-outline')) {
      instance.setLayoutProperty(
        'atlas-risk-outline',
        'visibility',
        atlasVisibility,
      )
    }

    if (instance.getLayer('road-risk-line')) {
      instance.setLayoutProperty(
        'road-risk-line',
        'visibility',
        roadVisibility,
      )
    }
  }, [showAtlas, showRoadRisk])

  return (
    <div className="risk-map-wrapper">
      <div className="risk-map-controls">
        <label>
          <input
            type="checkbox"
            checked={showAtlas}
            onChange={(event) => setShowAtlas(event.target.checked)}
          />
          Flood hazard area
        </label>

        <label>
          <input
            type="checkbox"
            checked={showRoadRisk}
            onChange={(event) => setShowRoadRisk(event.target.checked)}
          />
          Road risk
        </label>
      </div>

      <div ref={mapContainer} className="risk-map-canvas" />

      <div className="risk-map-legend">
        <strong>Map legend</strong>
        <div>
          <span className="legend-swatch atlas-swatch" />
          Hazard area
        </div>
        <div>
          <span className="legend-swatch road-swatch" />
          Risk road
        </div>
      </div>
    </div>
  )
}
