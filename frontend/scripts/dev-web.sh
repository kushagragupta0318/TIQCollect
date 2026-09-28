#!/bin/sh
# Dev only: the `web` service in docker-compose.yml runs this (the prod image
# builds the SPA in the Dockerfile and never uses it).
#
# Installs dependencies, starts Vite, and exits when package-lock.json changes,
# so `restart: unless-stopped` brings the container back up and reinstalls.
# Without it a merge that adds a package (vite-plugin-pwa, 2026-09-28) left the
# running dev server failing on the missing import until someone restarted it.
set -e

lock_hash() { sha1sum package-lock.json | cut -d' ' -f1; }

npm install --no-audit --no-fund
started=$(lock_hash)

npm run dev -- --host 0.0.0.0 &
vite=$!

while kill -0 "$vite" 2>/dev/null; do
  sleep 15
  if [ "$(lock_hash)" != "$started" ]; then
    echo "[web] package-lock.json changed: restarting to reinstall"
    kill "$vite" 2>/dev/null || true
    wait "$vite" 2>/dev/null || true
    exit 1
  fi
done
exit 1
