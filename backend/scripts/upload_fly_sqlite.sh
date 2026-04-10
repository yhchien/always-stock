#!/bin/bash

set -euo pipefail

APP="${APP:-always-stock-api}"
LOCAL_DB="${LOCAL_DB:-$(cd "$(dirname "$0")/.." && pwd)/db/tw_stock.db}"
REMOTE_DB="${REMOTE_DB:-/data/tw_stock.db}"
CHUNK_SIZE="${CHUNK_SIZE:-8m}"
UPLOAD_ID="${UPLOAD_ID:-$(date +%Y%m%d_%H%M%S)}"
MAX_RETRIES="${MAX_RETRIES:-3}"
PART_DELAY_SECS="${PART_DELAY_SECS:-1}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WORK_DIR="${WORK_DIR:-/tmp/always-stock-upload-$UPLOAD_ID}"
LOG_PREFIX="[fly-db-upload]"
STATE_FILE="$WORK_DIR/upload_state.txt"

info() {
  printf '%s %s\n' "$LOG_PREFIX" "$1"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

require_cmd flyctl
require_cmd zstd
require_cmd split
require_cmd sqlite3

if [ ! -f "$LOCAL_DB" ]; then
  echo "Local DB not found: $LOCAL_DB" >&2
  exit 1
fi

mkdir -p "$WORK_DIR"

DB_NAME="$(basename "$REMOTE_DB")"
REMOTE_DIR="$(dirname "$REMOTE_DB")"
REMOTE_TMP_DIR="$REMOTE_DIR/upload_$UPLOAD_ID"
REMOTE_ARCHIVE="$REMOTE_TMP_DIR/$DB_NAME.zst"
REMOTE_NEW_DB="$REMOTE_TMP_DIR/$DB_NAME"
REMOTE_BACKUP="$REMOTE_DIR/${DB_NAME%.db}.pre_upload.$UPLOAD_ID.db"

ARCHIVE="$WORK_DIR/$DB_NAME.zst"
PART_PREFIX="$WORK_DIR/$DB_NAME.part."

info "Resolving machine for $APP"
MACHINE_ID="${MACHINE_ID:-$(flyctl machine list -a "$APP" | awk 'NR>1 && $1 ~ /^[0-9a-f]+$/ {print $1; exit}')}"

if [ -z "${MACHINE_ID:-}" ]; then
  echo "Could not resolve machine id for $APP" >&2
  exit 1
fi

info "Using machine $MACHINE_ID"
info "Starting machine if needed"
flyctl machine start "$MACHINE_ID" -a "$APP" >/dev/null 2>&1 || true

if [ ! -f "$ARCHIVE" ]; then
  info "Compressing local DB: $LOCAL_DB"
  zstd -T0 -3 -f "$LOCAL_DB" -o "$ARCHIVE"
else
  info "Reusing existing archive: $ARCHIVE"
fi

if ! ls "${PART_PREFIX}"* >/dev/null 2>&1; then
  info "Splitting archive into $CHUNK_SIZE chunks"
  split -b "$CHUNK_SIZE" -d -a 4 "$ARCHIVE" "$PART_PREFIX"
else
  info "Reusing existing split parts from $WORK_DIR"
fi

info "Preparing remote temp dir: $REMOTE_TMP_DIR"
flyctl ssh console -a "$APP" --machine "$MACHINE_ID" -C "mkdir -p '$REMOTE_TMP_DIR'"

remote_size() {
  local remote_path="$1"
  flyctl ssh console -a "$APP" --machine "$MACHINE_ID" \
    -C "python - <<'PY'
import os
path = '$remote_path'
print(os.path.getsize(path) if os.path.exists(path) else -1)
PY" 2>/dev/null | tail -n 1 | tr -d '\r'
}

upload_part() {
  local part="$1"
  local base remote_path local_size remote_current attempt
  base="$(basename "$part")"
  remote_path="$REMOTE_TMP_DIR/$base"
  local_size="$(wc -c < "$part" | tr -d ' ')"
  remote_current="$(remote_size "$remote_path")"

  if [ "$remote_current" = "$local_size" ]; then
    info "Skipping $base (already uploaded)"
    printf 'last_success_part=%s\n' "$base" > "$STATE_FILE"
    return 0
  fi

  attempt=1
  while [ "$attempt" -le "$MAX_RETRIES" ]; do
    info "Uploading $base (attempt $attempt/$MAX_RETRIES)"
    if flyctl ssh sftp put "$part" "$remote_path" -a "$APP" --machine "$MACHINE_ID"; then
      remote_current="$(remote_size "$remote_path")"
      if [ "$remote_current" = "$local_size" ]; then
        printf 'last_success_part=%s\n' "$base" > "$STATE_FILE"
        info "Uploaded $base successfully"
        return 0
      fi
      info "Uploaded $base but remote size mismatch ($remote_current != $local_size)"
    fi
    attempt=$((attempt + 1))
    sleep 2
  done

  echo "Failed to upload $base after $MAX_RETRIES attempts" >&2
  exit 1
}

for part in "${PART_PREFIX}"*; do
  [ -f "$part" ] || continue
  upload_part "$part"
  if [ "$PART_DELAY_SECS" != "0" ]; then
    sleep "$PART_DELAY_SECS"
  fi
done

info "Reassembling archive on remote"
flyctl ssh console -a "$APP" --machine "$MACHINE_ID" -C "cat '$REMOTE_TMP_DIR'/$DB_NAME.part.* > '$REMOTE_ARCHIVE'"

info "Decompressing remote archive"
flyctl ssh console -a "$APP" --machine "$MACHINE_ID" -C "zstd -d -f '$REMOTE_ARCHIVE' -o '$REMOTE_NEW_DB'"

info "Running SQLite quick_check on uploaded DB"
flyctl ssh console -a "$APP" --machine "$MACHINE_ID" -C "python - <<'PY'
import sqlite3
db = sqlite3.connect('$REMOTE_NEW_DB')
result = db.execute('PRAGMA quick_check').fetchone()[0]
db.close()
print(result)
assert result == 'ok', result
PY"

info "Backing up current remote DB to $REMOTE_BACKUP"
flyctl ssh console -a "$APP" --machine "$MACHINE_ID" -C "cp '$REMOTE_DB' '$REMOTE_BACKUP'"

info "Replacing remote DB"
flyctl ssh console -a "$APP" --machine "$MACHINE_ID" -C "mv '$REMOTE_NEW_DB' '$REMOTE_DB'"

info "Cleaning remote temp dir"
flyctl ssh console -a "$APP" --machine "$MACHINE_ID" -C "rm -rf '$REMOTE_TMP_DIR'"

info "Restarting machine"
flyctl machine restart "$MACHINE_ID" -a "$APP"

info "Done"
info "Remote backup: $REMOTE_BACKUP"
info "Local work dir kept at: $WORK_DIR"
