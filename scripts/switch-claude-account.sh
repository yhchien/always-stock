#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 1 ]; then
  cat <<'EOF'
Usage: ./scripts/switch-claude-account <a|b>
  a: brian780223@gmail.com (default, no Claude env vars loaded)
  b: hsuan4store@gmail.com (Claude env vars loaded via direnv)

Example: ./scripts/switch-claude-account b
         ./scripts/switch-claude-account a
EOF
  exit 1
fi

account="$1"

case "$account" in
  a|brian|brian780223@gmail.com)
    echo "a" > "$(cd "$(dirname "$0")/.." && pwd)/.direnv_account"
    echo "✓ 已切換到帳號 a (brian780223@gmail.com)"
    echo "⚡ 執行: direnv allow"
    direnv allow "$(cd "$(dirname "$0")/.." && pwd)"
    ;;
  b|hsuan|hsuan4store@gmail.com)
    echo "b" > "$(cd "$(dirname "$0")/.." && pwd)/.direnv_account"
    echo "✓ 已切換到帳號 b (hsuan4store@gmail.com)"
    echo "⚡ 執行: direnv allow"
    direnv allow "$(cd "$(dirname "$0")/.." && pwd)"
    ;;
  *)
    echo "錯誤：未知帳號 '$account'"
    echo "可用帳號: a, b, brian, hsuan, brian780223@gmail.com, hsuan4store@gmail.com"
    exit 1
    ;;
esac

echo ""
echo "環境已更新。如果沒有自動起效，請執行："
echo "  direnv reload"
