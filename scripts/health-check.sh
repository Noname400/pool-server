#!/usr/bin/env bash
# ============================================
# Pool Server — Health Check Script
# Exit codes: 0 = all healthy, 1 = something down
# ============================================
set -uo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

HEALTHY=true

check_service() {
    local name="$1"
    local container="$2"

    if docker ps --format '{{.Names}}' | grep -q "^${container}$"; then
        local status
        status=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "running")
        if [[ "$status" == "healthy" || "$status" == "running" ]]; then
            echo -e "  ${GREEN}[OK]${NC}  $name ($container) — $status"
        else
            echo -e "  ${YELLOW}[!!]${NC}  $name ($container) — $status"
            HEALTHY=false
        fi
    else
        echo -e "  ${RED}[DOWN]${NC}  $name ($container) — not running"
        HEALTHY=false
    fi
}

check_http() {
    local name="$1"
    local url="$2"

    if curl -sf --max-time 5 "$url" >/dev/null 2>&1; then
        echo -e "  ${GREEN}[OK]${NC}  $name — $url"
    else
        echo -e "  ${RED}[DOWN]${NC}  $name — $url (not responding)"
        HEALTHY=false
    fi
}

echo ""
echo "Pool Server — Health Check"
echo "=========================="
echo ""

echo "Containers:"
check_service "Pool Server" "pool-server"
echo ""

echo "HTTP Endpoints:"
check_http "App (direct)" "http://localhost:8421/status"
check_http "Nginx (HTTP)" "http://localhost:80/"
echo ""

echo "Resource Usage:"
docker stats --no-stream --format "  {{.Name}}: CPU {{.CPUPerc}} | MEM {{.MemUsage}}" \
    pool-server 2>/dev/null || true
echo ""

echo "Disk:"
echo "  Volumes:"
docker system df -v 2>/dev/null | grep -E "pool" | head -5 || true
echo ""

if $HEALTHY; then
    echo -e "${GREEN}All services are healthy.${NC}"
    exit 0
else
    echo -e "${RED}One or more services are unhealthy!${NC}"
    exit 1
fi
