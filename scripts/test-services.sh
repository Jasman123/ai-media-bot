#!/usr/bin/env bash
# Start/stop the throwaway Postgres + Redis used by integration and e2e tests.
# Ports 55432/56379 are chosen to avoid clashing with anything on 5432/6379.
set -euo pipefail

PG=aimb-test-pg
REDIS=aimb-test-redis

case "${1:-up}" in
  up)
    docker inspect "$PG" >/dev/null 2>&1 || docker run -d --name "$PG" \
      -e POSTGRES_USER=aimb -e POSTGRES_PASSWORD=aimb -e POSTGRES_DB=aimb \
      -p 127.0.0.1:55432:5432 --tmpfs /var/lib/postgresql/data postgres:16-alpine >/dev/null
    docker inspect "$REDIS" >/dev/null 2>&1 || docker run -d --name "$REDIS" \
      -p 127.0.0.1:56379:6379 redis:7-alpine >/dev/null
    docker start "$PG" "$REDIS" >/dev/null
    for _ in $(seq 1 30); do
      docker exec "$PG" pg_isready -U aimb >/dev/null 2>&1 && break
      sleep 1
    done
    docker exec "$REDIS" redis-cli ping >/dev/null
    echo "test services up (postgres :55432, redis :56379)"
    ;;
  down)
    docker rm -f "$PG" "$REDIS" >/dev/null 2>&1 || true
    echo "test services removed"
    ;;
  *)
    echo "usage: $0 up|down" >&2
    exit 2
    ;;
esac
