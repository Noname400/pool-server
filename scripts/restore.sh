#!/usr/bin/env bash
# ============================================
# Pool Server — SQLite Restore Script
# ============================================
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
ok()   { echo -e "${GREEN}[  OK  ]${NC} $*"; }
warn() { echo -e "${YELLOW}[ WARN ]${NC} $*"; }
err()  { echo -e "${RED}[ERROR ]${NC} $*" >&2; }
die()  { err "$*"; exit 1; }

DATA_DIR="${POOL_DATA_DIR:-/data/pool}"
DB_FILE="${DATA_DIR}/pool.db"

BACKUP_FILE="${1:-}"

if [[ -z "$BACKUP_FILE" ]]; then
    echo "Usage: $(basename "$0") <backup_file.db.gz>"
    echo ""
    echo "Available backups:"
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    APP_DIR="$(dirname "$SCRIPT_DIR")"
    BACKUP_DIR="${APP_DIR}/backups"
    if [[ -d "$BACKUP_DIR" ]]; then
        ls -lh "$BACKUP_DIR"/pool_*.db.gz 2>/dev/null || echo "  No backups found in $BACKUP_DIR"
    fi
    exit 1
fi

[[ ! -f "$BACKUP_FILE" ]] && die "Backup file not found: $BACKUP_FILE"
[[ ! -s "$BACKUP_FILE" ]] && die "Backup file is empty: $BACKUP_FILE"

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
log "Backup file: $BACKUP_FILE ($BACKUP_SIZE)"

echo ""
warn "WARNING: This will REPLACE the database at '$DB_FILE'"
warn "The pool-app container should be stopped first!"
echo ""
read -p "Are you sure you want to continue? (type 'yes' to confirm): " CONFIRM

if [[ "$CONFIRM" != "yes" ]]; then
    log "Restore cancelled."
    exit 0
fi

log "Stopping pool-app if running..."
docker stop pool-app 2>/dev/null || true

if [[ -f "$DB_FILE" ]]; then
    TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
    cp "$DB_FILE" "${DB_FILE}.pre-restore.${TIMESTAMP}"
    ok "Current DB backed up to ${DB_FILE}.pre-restore.${TIMESTAMP}"
fi

log "Restoring from backup..."
gunzip -c "$BACKUP_FILE" > "$DB_FILE"

ok "Database restored: $DB_FILE"
log ""
log "Start the pool:"
log "  docker start pool-app"
