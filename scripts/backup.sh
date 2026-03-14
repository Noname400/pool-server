#!/usr/bin/env bash
# ============================================
# Pool Server — PostgreSQL Backup Script
# Keeps last 7 daily backups with compression
# ============================================
set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
ok()  { echo -e "${GREEN}[  OK  ]${NC} $*"; }
err() { echo -e "${RED}[ERROR ]${NC} $*" >&2; }

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${APP_DIR}/backups"
CONTAINER_NAME="pool_server_postgres"
DB_NAME="pool_server"
DB_USER="postgres"
KEEP_DAYS=7

TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
BACKUP_FILE="${BACKUP_DIR}/pool_server_${TIMESTAMP}.sql.gz"

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

# ---- Create backup ----
log "Starting PostgreSQL backup..."
log "Database: $DB_NAME | Container: $CONTAINER_NAME"

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    err "Container '$CONTAINER_NAME' is not running"
    exit 1
fi

docker exec "$CONTAINER_NAME" \
    pg_dump -U "$DB_USER" -d "$DB_NAME" --no-owner --no-acl \
    | gzip > "$BACKUP_FILE"

if [[ ! -s "$BACKUP_FILE" ]]; then
    err "Backup file is empty — something went wrong"
    rm -f "$BACKUP_FILE"
    exit 1
fi

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
ok "Backup created: $BACKUP_FILE ($BACKUP_SIZE)"

# ---- Rotate old backups ----
log "Rotating backups (keeping last $KEEP_DAYS days)..."
DELETED=0
find "$BACKUP_DIR" -name "pool_server_*.sql.gz" -mtime +${KEEP_DAYS} -type f | while read -r old_file; do
    rm -f "$old_file"
    log "  Deleted: $(basename "$old_file")"
    DELETED=$((DELETED + 1))
done

TOTAL=$(find "$BACKUP_DIR" -name "pool_server_*.sql.gz" -type f | wc -l | tr -d ' ')
ok "Backup rotation complete. Total backups: $TOTAL"

log "Backup finished successfully."
