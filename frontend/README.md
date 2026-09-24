# React + TypeScript + Vite

## Install the app on a phone (PWA, 2026-09-24)

The app ships a web manifest and an app-shell service worker
(`src/lib/pwaConfig.ts`). Both exist **only in a build**: the dev server
(`npm run dev`, and the Docker `web` container) registers no service worker,
so hot reload and the `/simulator` frames behave as they always have.

To install on a phone on the same Wi-Fi:

1. Create the LAN certificate once — the `openssl` command in the comment at
   the top of `vite.config.ts` writes `certs/dev-{key,cert}.pem` for your LAN IP.
2. `npm run build`
3. `HTTPS=1 npm run preview -- --port 5473` — `vite preview` inherits the dev
   server's HTTPS certificate, `host: true` and `/api` proxy, so the phone
   talks to one origin exactly as it does in development.
4. On the phone, open `https://<your-lan-ip>:5473`, trust the certificate,
   then **Add to Home screen** (Android Chrome: ⋮ menu; iOS Safari: Share).

**The certificate has to be trusted on the phone**, not just clicked past: a
browser will not register a service worker for a page whose certificate it
has not accepted, and the app then runs as an ordinary web page, uninstalled.

The worker never touches `/api` (the SSE stream included), `/ws`, `/docs`,
`/redoc` or `/openapi.json`, and caches no API response — offline data is a
separate task (I02). When a new build is deployed, a toast offers **Reload**;
nothing reloads until it is tapped, so a half-filled visit form is never lost.

---

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...

      // Remove tseslint.configs.recommended and replace with this
      tseslint.configs.recommendedTypeChecked,
      // Alternatively, use this for stricter rules
      tseslint.configs.strictTypeChecked,
      // Optionally, add this for stylistic rules
      tseslint.configs.stylisticTypeChecked,

      // Other configs...
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```

You can also install [eslint-plugin-react-x](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...
      // Enable lint rules for React
      reactX.configs['recommended-typescript'],
      // Enable lint rules for React DOM
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```
