import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    allowedHosts: [
      'localhost',
      '127.0.0.1',
      'unaustere-charlotte-precipitous.ngrok-free.dev' // 👈 add your ngrok domain here
    ]
  }
})

