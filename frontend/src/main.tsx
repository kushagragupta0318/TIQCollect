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

// Drop index.html's boot splash now the first render has painted. It is a
// sibling of #root, so React never owned it and cannot remove it itself.
// Unconditional: if the app rendered, the splash has done its job — a page
// that then shows its own loader (a lazy route, the dashboard hold) is already
// breathing the same mark, so there is nothing to hand over to.
document.getElementById('boot')?.remove()

// 2026-09-24 (I01) — the app-shell service worker, in a build only (a no-op
// under `vite dev`). An update waits for the user to tap Reload; see
// lib/registerServiceWorker.tsx.
registerServiceWorker()
