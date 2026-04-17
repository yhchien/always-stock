#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TARGET_SCRIPT="$PROJECT_ROOT/scripts/backfill_finmind.sh"

extract_resume_helper() {
  python3 - "$TARGET_SCRIPT" <<'PY'
from pathlib import Path
import sys

script = Path(sys.argv[1]).read_text()
start = script.index("resolve_resume_from_checkpoint() {")
end = script.index("\n\n# 載入環境變數", start)
print(script[start:end])
PY
}

run_case() {
  local contents="$1"
  local expected="$2"
  local tmpdir
  tmpdir=$(mktemp -d)
  local checkpoint_file="$tmpdir/checkpoint.txt"
  printf "%s\n" "$contents" > "$checkpoint_file"

  local actual
  actual=$(
    bash -lc "$(extract_resume_helper)
resolve_resume_from_checkpoint '$checkpoint_file'"
  )

  if [ "$actual" != "$expected" ]; then
    echo "expected '$expected' but got '$actual'"
    rm -rf "$tmpdir"
    exit 1
  fi

  rm -rf "$tmpdir"
}

run_case "DONE 2026-04-10 2026-04-14 10:00:00" "2026-04-11"
run_case "FAILED 2026-04-10 exit=3 2026-04-14 10:00:00" "2026-04-11"
run_case "QUOTA_EXHAUSTED 2026-04-10 2026-04-14 10:00:00" "2026-04-10"

echo "backfill checkpoint resume tests passed"
