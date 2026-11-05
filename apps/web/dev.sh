#!/usr/bin/env bash
# Start the Vite development server for the Spatial-QAT frontend.
# Run `npm install` first if node_modules is missing.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d "node_modules" ]; then
  echo "node_modules not found. Running npm install..."
  npm install
fi
npx vite --host 0.0.0.0 --port 3000
