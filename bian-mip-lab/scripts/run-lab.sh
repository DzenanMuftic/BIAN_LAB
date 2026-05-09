#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"${BASE_DIR}/scripts/setup.sh"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is not installed. Install Docker Engine and Docker Compose plugin first."
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: docker compose plugin is not available."
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq is required for connector registration."
  exit 1
fi

if [[ ! -f "${BASE_DIR}/ojdbc8.jar" ]]; then
  echo "ERROR: Missing ${BASE_DIR}/ojdbc8.jar (required by Debezium Oracle connector)."
  exit 1
fi

cd "${BASE_DIR}"
docker compose up -d --build

echo "Waiting for Debezium Connect on http://localhost:8083 ..."
for i in {1..60}; do
  if curl -fsS "http://localhost:8083/connectors" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

"${BASE_DIR}/scripts/register-connector.sh" "http://localhost:8083"

echo "Lab is up. Open: http://localhost:8080"
