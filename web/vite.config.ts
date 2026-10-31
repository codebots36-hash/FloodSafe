import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  build: {
    // MapLibre's single minified module is about 1.1 MB and is lazy-loaded.
    chunkSizeWarningLimit: 1200,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: 'map-vendor',
              test: /node_modules[\\/]maplibre-gl[\\/]/,
              maxSize: 400_000,
              priority: 20,
            },
            {
              name: 'vendor',
              test: /node_modules[\\/]/,
              maxSize: 400_000,
              priority: 10,
            },
          ],
        },
      },
    },
  },
})

