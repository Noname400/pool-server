#!/usr/bin/env bash
# ============================================
# Pool Server — Create Admin API Key
# Creates a new admin key directly in SQLite via the container.
# ============================================
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

CONTAINER_NAME="${1:-pool-app}"

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo -e "${RED}Container '${CONTAINER_NAME}' is not running.${NC}"
    echo "Usage: $0 [container_name]"
    exit 1
fi

echo -e "${GREEN}Creating new admin API key in container '${CONTAINER_NAME}'...${NC}"

docker exec "$CONTAINER_NAME" python3 -c "
import asyncio, sys, uuid
sys.path.insert(0, '/app')
from app.auth.api_keys import generate_api_key
from app.db.sqlite import init_db, create_api_key, get_db, list_users

async def main():
    await init_db()
    async with get_db() as db:
        users = await list_users(db)
        admin = next((u for u in users if u['role'] == 'admin'), None)
        if not admin:
            print('ERROR: No admin user found in DB.', flush=True)
            sys.exit(1)
        plaintext, key_hash = generate_api_key()
        await create_api_key(db, admin['id'], key_hash, plaintext[:4], 'created-by-script', 'admin')
        print(f'API_KEY={plaintext}', flush=True)

asyncio.run(main())
" 2>/dev/null

echo ""
echo -e "${YELLOW}Save this key — it cannot be recovered later.${NC}"
