#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT_DIR/logs"
GEN_DIR="$ROOT_DIR/generated"
LOCK_FILE="$GEN_DIR/refresh.lock"
LOG_FILE="$LOG_DIR/refresh-$(date +%F).log"

mkdir -p "$LOG_DIR" "$GEN_DIR"

if command -v flock >/dev/null 2>&1; then
    flock -n "$LOCK_FILE" python "$ROOT_DIR/server_clash_merge.py" "$@" >>"$LOG_FILE" 2>&1
else
    python "$ROOT_DIR/server_clash_merge.py" "$@" >>"$LOG_FILE" 2>&1
fi
