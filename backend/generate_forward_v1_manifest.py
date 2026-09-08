"""產生 `forward_v1_manifest.json`（spec Part 35）。

**只在** FORWARD_V1_202609 sanity replay 跑完、`validate_shadow_forward_gates.py` 5 個
gate 全部 PASS 之後才執行——manifest 裡的 `status` 欄位預設寫 `"PENDING_USER_GO"`，
**不會自動寫成 `"FROZEN"`**（spec Part 57：Freeze 是需要人工明確 GO 的決策點，不是
腳本能自己拍板的）。使用者確認 GO 之後，另外用 `--freeze` 把既有 manifest 的
status 改成 `"FROZEN"`（不重新產生其餘欄位，避免不小心用不同的 code 版本覆蓋掉
已經審查過的內容）。

用法：
    python3 generate_forward_v1_manifest.py            # 產生/更新 PENDING_USER_GO manifest
    python3 generate_forward_v1_manifest.py --freeze    # 使用者已 GO，把 status 改 FROZEN
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
MANIFEST_PATH = REPO_ROOT / "forward_v1_manifest.json"

# 這次重構真正決定 FORWARD_V1_202609 行為的原始碼——改動這些檔案中任何一個都應該讓
# code hash 跟著變，manifest 才能真正反映「這份 JSON 描述的是哪一版程式碼的行為」。
STRATEGY_CODE_FILES = [
    BACKEND_DIR / "app" / "signals" / "shadow_portfolio.py",
    BACKEND_DIR / "app" / "trading_calendar.py",
    BACKEND_DIR / "run_shadow_portfolio.py",
]


def _compute_strategy_code_hash() -> str:
    h = hashlib.sha256()
    for path in STRATEGY_CODE_FILES:
        h.update(path.read_bytes())
    return h.hexdigest()


def _git_commit_sha() -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _git_dirty_strategy_files() -> list[str]:
    """`git_commit_sha` 只在工作樹乾淨時才真正代表「這份 manifest 描述的就是這個
    commit 的行為」——若策略相關檔案有未 commit 的改動，誠實回報清單，不能讓
    `git_commit_sha` 看起來像已經涵蓋這次改動（那會讓「有沒有偷改參數」這個查核點
    失去意義：查核者必須能對照到真正跑出這份 replay 結果的程式碼版本）。"""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--", "backend/"],
        capture_output=True, text=True, check=True,
    )
    dirty = [line[3:] for line in result.stdout.splitlines() if line.strip()]
    strategy_related = [
        f for f in dirty
        if "shadow_portfolio" in f or "trading_calendar" in f or "run_shadow_portfolio" in f
    ]
    return strategy_related


def _build_manifest() -> dict:
    sys.path.insert(0, str(BACKEND_DIR))
    from app.signals import shadow_portfolio as sp

    params = sp.STRATEGY_PARAMS_BY_VERSION[sp.STRATEGY_VERSION_FORWARD_V1]

    return {
        "strategy_version": sp.STRATEGY_VERSION_FORWARD_V1,
        "initial_capital": params["initial_capital"],
        "unit_capital": params["unit_capital"],
        "max_stocks": params["max_stocks"],
        "max_total_units": params.get("max_total_units"),
        "max_units_per_stock": params.get("max_units_per_stock"),
        "max_position_exposure_pct": params["max_position_exposure_pct"],
        "averaging_down": not params["add_requires_profit"],
        "multi_add": True,
        "fixed_take_profit": params["take_profit_signal_pct"],
        "winner_10_tracking_only": params["track_winners"],
        "rotation_enabled": False,
        "real_position_stop_loss_pct": params["real_stop_loss_pct"],
        "execution_buy": "NEXT_TRADING_DAY_HIGH",
        "execution_sell": "NEXT_TRADING_DAY_LOW",
        "historical_sanity_start": "2026-08-07",
        "historical_sanity_end": "2026-09-04",
        "git_commit_sha": _git_commit_sha(),
        "git_dirty_strategy_files": _git_dirty_strategy_files(),
        "strategy_code_hash": _compute_strategy_code_hash(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "PENDING_USER_GO",
    }


def main(argv: list) -> int:
    if "--freeze" in argv:
        if not MANIFEST_PATH.exists():
            print(f"找不到 {MANIFEST_PATH}，無法 freeze——請先不帶 --freeze 產生一次", file=sys.stderr)
            return 1
        manifest = json.loads(MANIFEST_PATH.read_text())
        manifest["status"] = "FROZEN"
        manifest["frozen_at"] = datetime.now(timezone.utc).isoformat()
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        print(f"已將 {MANIFEST_PATH} 標記為 FROZEN")
        return 0

    manifest = _build_manifest()
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"已產生 {MANIFEST_PATH}（status=PENDING_USER_GO，尚未 freeze）")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
