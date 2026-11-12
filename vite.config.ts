import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', proxy: { '/api': 'http://127.0.0.1:8765' } },
  build: {
    outDir: 'dist',
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: 'language',
              test: /node_modules[\\/](@lezer|@codemirror[\\/](lang-markdown|language))/,
              priority: 20,
            },
            { name: 'editor', test: /node_modules[\\/](@codemirror|@lezer|style-mod|crelt|w3c-keyname)/ },
            { name: 'react', test: /node_modules[\\/](react|react-dom|scheduler)/ },
          ],
        },
      },
    },
  },
})
