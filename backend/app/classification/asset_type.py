"""
asset_type 判斷（Phase 1 §4）。

規則優先序（第一個命中就採用）：
    1. `stock_id` 符合 Index/大盤佔位列黑名單 → INDEX_BENCHMARK
    2. `industry_name` 為「存託憑證」 → DR
    3. `industry_name` 為「受益證券」 → REIT
    4. `stock_id` 符合特別股 pattern（4 碼數字 + 1 碼英文字母）→ PREFERRED_STOCK
    5. `stock_id` 符合 ETF pattern（沿用 exclusions.is_etf 規則）且非 ETN → ETF
    6. `industry_name` 為「ETN」或名稱含「N」證券慣用後綴（見 `_ETN_INDUSTRY_NAMES`）→ ETN
    7. 其餘 → COMMON_STOCK（含金融股、創新板股票、TDR 已在第 2 點攔截）

沿用既有 `app/signals/exclusions.py` 的 ETF regex，不重新發明規則。
"""
from __future__ import annotations

import re
from typing import Optional

from app.classification.taxonomy import (
    ASSET_TYPE_COMMON_STOCK,
    ASSET_TYPE_DR,
    ASSET_TYPE_ETF,
    ASSET_TYPE_ETN,
    ASSET_TYPE_INDEX_BENCHMARK,
    ASSET_TYPE_PREFERRED_STOCK,
    ASSET_TYPE_REIT,
)

# ETF：00 開頭 + 2-6 碼數字 + 可選 1 碼英文字母後綴（0050 / 00878 / 006208 / 00631L 槓桿 /
# 00632R 反向 / 00400A 主動式 / 00981T 平衡型）。比 exclusions.is_etf() 的純數字 pattern
# 多了可選字母後綴——2023 年後新掛牌的主動式/槓桿反向/多重資產 ETF 大量使用此後綴，
# 若只比對純數字會把約 130 檔 ETF 誤判為 COMMON_STOCK（Phase 1 backfill 首次 dry-run
# 時發現，見 sector_mapping_validation_report.md）
_ETF_ID_PATTERN = re.compile(r"^00\d{2,6}[A-Za-z]?$")
_ETF_NAME_KEYWORDS = ("ETF", "指數型基金", "指數股票型")

# 特別股：4 碼數字 + 1 碼英文字母（例：2881A / 1101B / 9941A）
_PREFERRED_STOCK_PATTERN = re.compile(r"^\d{4}[A-Z]$")

# Index 佔位列：stock_id 非數字開頭（例：Automobile / TAIEX）
_INDEX_BENCHMARK_ID_PATTERN = re.compile(r"^[A-Za-z]")

_DR_INDUSTRY_NAMES = {"存託憑證"}
_REIT_INDUSTRY_NAMES = {"受益證券"}
_ETN_INDUSTRY_NAMES = {"ETN"}


def classify_asset_type(
    stock_id: str,
    stock_name: Optional[str] = None,
    industry_name: Optional[str] = None,
) -> str:
    """判斷 asset_type。輸入為 `stocks_master` 的原始欄位。"""
    sid = (stock_id or "").strip()
    name = (stock_name or "").strip()
    industry = (industry_name or "").strip()

    if sid and _INDEX_BENCHMARK_ID_PATTERN.match(sid):
        return ASSET_TYPE_INDEX_BENCHMARK

    if industry in _DR_INDUSTRY_NAMES:
        return ASSET_TYPE_DR

    if industry in _REIT_INDUSTRY_NAMES:
        return ASSET_TYPE_REIT

    if sid and _PREFERRED_STOCK_PATTERN.match(sid):
        return ASSET_TYPE_PREFERRED_STOCK

    if industry in _ETN_INDUSTRY_NAMES:
        return ASSET_TYPE_ETN

    is_etf_like = bool(sid and _ETF_ID_PATTERN.match(sid)) or any(
        kw.upper() in name.upper() for kw in _ETF_NAME_KEYWORDS
    )
    if is_etf_like:
        return ASSET_TYPE_ETF

    return ASSET_TYPE_COMMON_STOCK
