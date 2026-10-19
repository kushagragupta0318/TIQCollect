import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { registerServiceWorker } from './lib/registerServiceWorker.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// 2026-09-24 (I01) — the app-shell service worker, in a build only (a no-op
// under `vite dev`). An update waits for the user to tap Reload; see
// lib/registerServiceWorker.tsx.
registerServiceWorker()
