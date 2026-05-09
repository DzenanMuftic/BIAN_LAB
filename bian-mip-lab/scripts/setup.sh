#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "${BASE_DIR}/oracle/init"
mkdir -p "${BASE_DIR}/debezium"
mkdir -p "${BASE_DIR}/logs"
mkdir -p "${BASE_DIR}/data/valkey"
mkdir -p "${BASE_DIR}/data/kafka"
mkdir -p "${BASE_DIR}/data/oracle"

if [[ ! -f "${BASE_DIR}/ojdbc8.jar" ]]; then
  echo "WARN: ojdbc8.jar not found in ${BASE_DIR}. Debezium Oracle connector needs this jar mounted."
fi

chmod +x "${BASE_DIR}/scripts/register-connector.sh"

echo "Lab folders prepared under ${BASE_DIR}."
