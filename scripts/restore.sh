#!/usr/bin/env bash
# ============================================
# Pool Server — PostgreSQL Restore Script
# ============================================
set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
ok()   { echo -e "${GREEN}[  OK  ]${NC} $*"; }
warn() { echo -e "${YELLOW}[ WARN ]${NC} $*"; }
err()  { echo -e "${RED}[ERROR ]${NC} $*" >&2; }
die()  { err "$*"; exit 1; }

# Configuration
CONTAINER_NAME="pool_server_postgres"
DB_NAME="pool_server"
DB_USER="postgres"

# ---- Validate input ----
BACKUP_FILE="${1:-}"

if [[ -z "$BACKUP_FILE" ]]; then
    echo "Usage: $(basename "$0") <backup_file.sql.gz>"
    echo ""
    echo "Available backups:"
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    APP_DIR="$(dirname "$SCRIPT_DIR")"
    BACKUP_DIR="${APP_DIR}/backups"
    if [[ -d "$BACKUP_DIR" ]]; then
        ls -lh "$BACKUP_DIR"/pool_server_*.sql.gz 2>/dev/null || echo "  No backups found in $BACKUP_DIR"
    fi
    exit 1
fi

[[ ! -f "$BACKUP_FILE" ]] && die "Backup file not found: $BACKUP_FILE"
[[ ! -s "$BACKUP_FILE" ]] && die "Backup file is empty: $BACKUP_FILE"

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
log "Backup file: $BACKUP_FILE ($BACKUP_SIZE)"

# ---- Confirmation ----
echo ""
warn "WARNING: This will DROP and RECREATE the database '$DB_NAME'"
warn "All current data will be LOST!"
echo ""
read -p "Are you sure you want to continue? (type 'yes' to confirm): " CONFIRM

if [[ "$CONFIRM" != "yes" ]]; then
    log "Restore cancelled."
    exit 0
fi

# ---- Check container ----
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    die "Container '$CONTAINER_NAME' is not running"
fi

# ---- Restore ----
log "Stopping application connections..."
docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d postgres -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid();" \
    >/dev/null 2>&1 || true

log "Dropping and recreating database..."
docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS $DB_NAME;"
docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"

log "Restoring from backup..."
gunzip -c "$BACKUP_FILE" | docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" --quiet

ok "Database restored successfully from: $(basename "$BACKUP_FILE")"
log ""
log "You may need to restart the application:"
log "  docker compose restart app"
