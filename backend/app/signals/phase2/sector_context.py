"""
Phase 2 §D/§E：Hierarchical Canonical Sector Context。

核心規則（spec §D）：
    1. canonical mapping usable（confidence HIGH/MEDIUM）且 sub_sector 有效樣本數
       >= MIN_PEER_SAMPLE → peer_scope_used = SUB_SECTOR
    2. 否則 primary_sector 有效樣本數 >= MIN_PEER_SAMPLE → PRIMARY_SECTOR
    3. 否則 → MARKET_ONLY，sector_context_quality = UNUSABLE
       （**不是** FAIL——不可因樣本不足直接判股票死亡，只是這個維度的資訊不可用）

核心規則（spec §E）：sector_strength 與 peer_rs 是兩個獨立概念，不可用同一個
`industry_rs_percentile` 混著表示：
    - `sector_strength_percentile_20d`：這整個 sector（相對其他 sector）強不強
    - `peer_rs_percentile_20d`：這檔股票在自己的 peers 裡強不強

單一樣本（樣本數=1）的 sector **絕對不能**產生 100 percentile 假訊號——這裡的設計
方式是「樣本數 < MIN_PEER_SAMPLE 時，這個 scope 直接不被採用」（往下 fallback），
不是「算出來但硬夾在某個值」。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

MIN_PEER_SAMPLE = 5

QUALITY_HIGH = "HIGH"
QUALITY_MEDIUM = "MEDIUM"
QUALITY_UNUSABLE = "UNUSABLE"

PEER_SCOPE_SUB_SECTOR = "SUB_SECTOR"
PEER_SCOPE_PRIMARY_SECTOR = "PRIMARY_SECTOR"
PEER_SCOPE_MARKET_ONLY = "MARKET_ONLY"

_USABLE_CONFIDENCE = ("HIGH", "MEDIUM")


def is_mapping_usable(confidence: Optional[str]) -> bool:
    """Phase 1 confidence HIGH/MEDIUM 才可用於 hard gating／sector 分組；
    LOW（84 檔待複核）不可用（spec §B `canonical_mapping_usable`）。"""
    return confidence in _USABLE_CONFIDENCE


def _percentile_rank(value: Optional[float], population: List[float]) -> Optional[float]:
    """0~100 percentile（越高越強），沿用 momentum._percentile_map 的
    「嚴格小於的數量 / (n-1)」慣例；population 含 value 本身。樣本 < 2 → None。"""
    if value is None:
        return None
    n = len(population)
    if n < 2:
        return None
    sorted_vals = sorted(population)
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_vals[mid] < value:
            lo = mid + 1
        else:
            hi = mid
    return round(100.0 * lo / (n - 1), 2)


def _mean(values: List[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return (sum(vals) / len(vals)) if vals else None


def compute_sector_context(
    frame: Dict[str, Dict[str, Any]],
    classifications: Dict[str, Dict[str, Any]],
    min_peer_sample: int = MIN_PEER_SAMPLE,
) -> Dict[str, Dict[str, Any]]:
    """
    輸入：
        frame：stock_id -> momentum frame 特徵（至少需要 `rs_market_percentile_20d`）
        classifications：stock_id -> {"primary_sector", "sub_sector", "confidence"}
                          （Phase 1 `security_classification` 表內容）
        min_peer_sample：peer/sector 分組最小可信樣本數（預設 5）

    輸出：stock_id -> sector context dict（每檔 frame 內的股票都會有一筆，即使
    mapping 不可用或樣本不足——這時候只是 peer_scope_used=MARKET_ONLY /
    sector_context_quality=UNUSABLE，兩個 percentile 都是 None，**不是剔除**）。
    """
    # 分組（只收 mapping usable 且在 frame 內的股票；樣本數統計只算這些）
    primary_groups: Dict[str, List[str]] = {}
    sub_groups: Dict[tuple, List[str]] = {}

    for sid in frame:
        cls = classifications.get(sid)
        if not cls or not is_mapping_usable(cls.get("confidence")):
            continue
        primary_sector = cls.get("primary_sector")
        if not primary_sector:
            continue
        primary_groups.setdefault(primary_sector, []).append(sid)
        sub_sector = cls.get("sub_sector")
        if sub_sector:
            sub_groups.setdefault((primary_sector, sub_sector), []).append(sid)

    # sector-strength universe：只有樣本數足夠的 group 才進入「跟其他 sector 比」的母體
    def _group_rs_values(ids: List[str]) -> List[float]:
        return [frame[i].get("rs_market_percentile_20d") for i in ids if frame[i].get("rs_market_percentile_20d") is not None]

    primary_group_means = {
        ps: _mean(_group_rs_values(ids))
        for ps, ids in primary_groups.items()
        if len(ids) >= min_peer_sample
    }
    sub_group_means = {
        key: _mean(_group_rs_values(ids))
        for key, ids in sub_groups.items()
        if len(ids) >= min_peer_sample
    }
    primary_strength_population = [v for v in primary_group_means.values() if v is not None]
    sub_strength_population = [v for v in sub_group_means.values() if v is not None]

    results: Dict[str, Dict[str, Any]] = {}
    for sid, feats in frame.items():
        cls = classifications.get(sid) or {}
        confidence = cls.get("confidence")
        primary_sector = cls.get("primary_sector")
        sub_sector = cls.get("sub_sector")
        mapping_usable = bool(primary_sector) and is_mapping_usable(confidence)

        primary_ids = primary_groups.get(primary_sector, []) if mapping_usable else []
        sub_ids = sub_groups.get((primary_sector, sub_sector), []) if (mapping_usable and sub_sector) else []
        primary_count = len(primary_ids)
        sub_count = len(sub_ids)

        stock_rs = feats.get("rs_market_percentile_20d")

        if mapping_usable and sub_count >= min_peer_sample:
            peer_scope = PEER_SCOPE_SUB_SECTOR
            quality = QUALITY_HIGH
            peer_rs_pct = _percentile_rank(stock_rs, _group_rs_values(sub_ids))
            group_mean = sub_group_means.get((primary_sector, sub_sector))
            sector_strength_pct = (
                _percentile_rank(group_mean, sub_strength_population)
                if len(sub_strength_population) >= min_peer_sample
                else None
            )
        elif mapping_usable and primary_count >= min_peer_sample:
            peer_scope = PEER_SCOPE_PRIMARY_SECTOR
            quality = QUALITY_MEDIUM
            peer_rs_pct = _percentile_rank(stock_rs, _group_rs_values(primary_ids))
            group_mean = primary_group_means.get(primary_sector)
            sector_strength_pct = (
                _percentile_rank(group_mean, primary_strength_population)
                if len(primary_strength_population) >= min_peer_sample
                else None
            )
        else:
            peer_scope = PEER_SCOPE_MARKET_ONLY
            quality = QUALITY_UNUSABLE
            peer_rs_pct = None
            sector_strength_pct = None

        results[sid] = {
            "primary_sector": primary_sector,
            "sub_sector": sub_sector,
            "primary_sector_stock_count": primary_count,
            "sub_sector_stock_count": sub_count,
            "peer_scope_used": peer_scope,
            "sector_context_quality": quality,
            "peer_rs_percentile_20d": peer_rs_pct,
            "sector_strength_percentile_20d": sector_strength_pct,
            "canonical_mapping_usable": mapping_usable,
        }

    return results
