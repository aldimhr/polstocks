#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${POLSTOCK_BACKEND_DB:-/opt/hermes/politics_stock_mapper/data/polstock_backend.db}"
BACKUP_DIR="${POLSTOCK_BACKUP_DIR:-/opt/hermes/politics_stock_mapper/data/backups}"
DATE_TAG="$(date +%Y%m%d_%H%M%S)"
DEST="${BACKUP_DIR}/polstock_backend_${DATE_TAG}.db"

mkdir -p "${BACKUP_DIR}"
cp "${DB_PATH}" "${DEST}"
