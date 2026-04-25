"""
M23 deterministic 排除規則：ETF、金融股、人工黑名單，以及集團對照表載入。

Spec §3.1.1（ETF / 金融股判斷）+ §6.2（group_stocks 對照表）。

設計：
  - 純規則、不查 DB（讓 candidate_pool / filters 自己組查 DB 後呼叫）
  - 跨 slice 5/6 都會用：候選池建立階段排除、filter 階段二次驗證
  - group_stocks.json 在 import 時讀一次到 module-level cache，測試可以 monkeypatch
    `_GROUP_STOCKS_CACHE` 注入 fixture 資料
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

# ETF：股票代號 4-6 碼且以 00 開頭（0050 / 00878 / 006208 等）
_ETF_ID_PATTERN = re.compile(r"^00\d{2,}$")

# ETF 名稱關鍵字（覆蓋 ID 不是 00 開頭的少數案例，例如複委託 ETF）
_ETF_NAME_KEYWORDS = ("ETF", "指數型基金", "指數股票型")

# 金融股 industry_name 關鍵字（FinMind 細分類含「金融保險業」「銀行業」「保險業」等）
_FINANCIAL_INDUSTRY_KEYWORDS = ("金融", "銀行", "保險", "證券")

# 人工黑名單（金控旗下、複委託 ETF、暫停交易等邊界 case）
# 第一版為空 set，依 slice 5/6 / 上線後觀察結果再補
EXCLUSION_BLACKLIST: Set[str] = set()


_GROUP_STOCKS_PATH = Path(__file__).parent / "group_stocks.json"
_GROUP_STOCKS_CACHE: Optional[Dict[str, Dict]] = None


def is_etf(stock_id: str, stock_name: Optional[str] = None) -> bool:
    """判斷是否為 ETF。

    規則（spec §3.1.1）：
      1. stock_id 開頭 `^00\\d{2,}` → ETF
      2. stock_name 含關鍵字（ETF / 指數型基金 / 指數股票型）→ ETF

    任一條件成立即視為 ETF。
    """
    if stock_id and _ETF_ID_PATTERN.match(stock_id):
        return True
    if stock_name:
        upper = stock_name.upper()
        for kw in _ETF_NAME_KEYWORDS:
            if kw.upper() in upper:
                return True
    return False


def is_financial(industry_name: Optional[str]) -> bool:
    """判斷是否為金融股。

    規則（spec §3.1.1）：industry_name 含「金融 / 銀行 / 保險 / 證券」任一關鍵字。
    """
    if not industry_name:
        return False
    for kw in _FINANCIAL_INDUSTRY_KEYWORDS:
        if kw in industry_name:
            return True
    return False


def is_blacklisted(stock_id: str) -> bool:
    """判斷股票是否在人工黑名單。"""
    return stock_id in EXCLUSION_BLACKLIST


def should_exclude(
    stock_id: str,
    stock_name: Optional[str] = None,
    industry_name: Optional[str] = None,
) -> bool:
    """三條規則合一：ETF / 金融 / 黑名單任一命中 → 排除。

    候選池建立階段（slice 5）會傳入 stocks_master 取得的 name / industry，
    一次判斷三條規則。
    """
    if is_blacklisted(stock_id):
        return True
    if is_etf(stock_id, stock_name):
        return True
    if is_financial(industry_name):
        return True
    return False


def load_group_stocks(force_reload: bool = False) -> Dict[str, Dict]:
    """讀取 group_stocks.json 並 cache 到 module-level。

    回傳 dict（key = 集團名，value = `{leader, members}`），不含 `_meta`。
    測試可用 `force_reload=True` 重新讀檔，或直接 monkeypatch
    `app.signals.exclusions._GROUP_STOCKS_CACHE` 注入 fixture。
    """
    global _GROUP_STOCKS_CACHE
    if _GROUP_STOCKS_CACHE is not None and not force_reload:
        return _GROUP_STOCKS_CACHE

    with _GROUP_STOCKS_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    _GROUP_STOCKS_CACHE = {
        key: value for key, value in raw.items() if not key.startswith("_")
    }
    return _GROUP_STOCKS_CACHE


def find_group_for_stock(stock_id: str) -> Optional[str]:
    """給定 stock_id 找出所屬集團名；不在任何集團 → None。"""
    groups = load_group_stocks()
    for group_name, info in groups.items():
        members = info.get("members", [])
        if stock_id in members:
            return group_name
    return None


def get_group_members(group_name: str) -> List[str]:
    """取得集團所有成員 stock_id；找不到集團 → 空 list。"""
    groups = load_group_stocks()
    info = groups.get(group_name)
    if info is None:
        return []
    return list(info.get("members", []))


def get_group_leader(group_name: str) -> Optional[str]:
    """取得集團 leader stock_id；找不到集團 → None。"""
    groups = load_group_stocks()
    info = groups.get(group_name)
    if info is None:
        return None
    return info.get("leader")
