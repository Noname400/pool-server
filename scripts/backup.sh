#!/usr/bin/env bash
# ============================================
# Pool Server — SQLite Backup Script
# Keeps last 7 daily backups with compression
# ============================================
set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
ok()  { echo -e "${GREEN}[  OK  ]${NC} $*"; }
err() { echo -e "${RED}[ERROR ]${NC} $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${APP_DIR}/backups"
DATA_DIR="${POOL_DATA_DIR:-/data/pool}"
DB_FILE="${DATA_DIR}/pool.db"
KEEP_DAYS=7

TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
BACKUP_FILE="${BACKUP_DIR}/pool_${TIMESTAMP}.db.gz"

mkdir -p "$BACKUP_DIR"

if [[ ! -f "$DB_FILE" ]]; then
    err "Database not found: $DB_FILE"
    exit 1
fi

log "Starting SQLite backup..."
log "Database: $DB_FILE"

sqlite3 "$DB_FILE" ".backup '${BACKUP_DIR}/pool_${TIMESTAMP}.db'" 2>/dev/null || \
    cp "$DB_FILE" "${BACKUP_DIR}/pool_${TIMESTAMP}.db"

gzip "${BACKUP_DIR}/pool_${TIMESTAMP}.db"

if [[ ! -s "$BACKUP_FILE" ]]; then
    err "Backup file is empty"
    rm -f "$BACKUP_FILE"
    exit 1
fi

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
ok "Backup created: $BACKUP_FILE ($BACKUP_SIZE)"

log "Rotating backups (keeping last $KEEP_DAYS days)..."
find "$BACKUP_DIR" -name "pool_*.db.gz" -mtime +${KEEP_DAYS} -type f -delete

TOTAL=$(find "$BACKUP_DIR" -name "pool_*.db.gz" -type f | wc -l | tr -d ' ')
ok "Backup complete. Total backups: $TOTAL"
