#!/usr/bin/env bash
set -euo pipefail

CONNECT_URL="${1:-http://localhost:8083}"
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${BASE_DIR}/debezium/oracle-connector.json"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "ERROR: Missing connector config at ${CONFIG_FILE}"
  exit 1
fi

curl -sS -X PUT \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  "${CONNECT_URL}/connectors/oracle19c-banking-connector/config" \
  -d "$(jq -c '.config' "${CONFIG_FILE}")" | jq .

echo "Connector registration request sent to ${CONNECT_URL}."
