"""
整合入口（Phase 1）：把 asset_type / industry_mapping / stock_overrides / etf_mapping
串成單一 `classify_security()`，供 backfill script 與（未來）API 即時查詢共用。

刻意設計：這裡**只讀** `stocks_master`，**只寫**新的
`security_classification` / `etf_classification` 兩張表；不觸碰
`stocks_master.industry_name` / `sub_industry`，也不被 `app/signals/*` import
（維持 Phase 1「顯示層 / 選股層」平行不交會）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.classification.asset_type import classify_asset_type
from app.classification.etf_mapping import classify_etf
from app.classification.industry_mapping import (
    NEEDS_OVERRIDE,
    map_industry_name_to_primary_sector,
)
from app.classification.stock_overrides import STOCK_OVERRIDES
from app.classification.taxonomy import (
    ASSET_TYPE_ETF,
    ASSET_TYPE_ETN,
    ASSET_TYPE_INDEX_BENCHMARK,
    ASSET_TYPE_REIT,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    MAPPING_VERSION,
)
from app.models import StockMaster


@dataclass
class SecurityClassificationResult:
    stock_id: str
    stock_name: str
    asset_type: str
    source_industry: Optional[str]
    primary_sector: Optional[str]
    sub_sector: Optional[str]
    secondary_sectors: List[str] = field(default_factory=list)
    theme_clusters: List[str] = field(default_factory=list)
    is_financial: bool = False
    is_etf: bool = False
    classification_confidence: Optional[str] = None
    classification_reason: str = ""
    review_required: bool = False
    mapping_version: str = MAPPING_VERSION
    # 只有 asset_type in (ETF, ETN) 才會有值
    etf_detail: Optional[Dict[str, Any]] = None


def classify_security(
    stock_id: str,
    stock_name: str,
    industry_name: Optional[str],
    sub_industry: Optional[str],
) -> SecurityClassificationResult:
    asset_type = classify_asset_type(stock_id, stock_name, industry_name)

    if asset_type == ASSET_TYPE_INDEX_BENCHMARK:
        return SecurityClassificationResult(
            stock_id=stock_id,
            stock_name=stock_name,
            asset_type=asset_type,
            source_industry=industry_name,
            primary_sector=None,
            sub_sector=None,
            classification_confidence=None,
            classification_reason="非真實證券（指數/大盤佔位列），不列入分類統計",
            review_required=False,
        )

    if asset_type in (ASSET_TYPE_ETF, ASSET_TYPE_ETN):
        etf_result = classify_etf(stock_id, stock_name)
        return SecurityClassificationResult(
            stock_id=stock_id,
            stock_name=stock_name,
            asset_type=asset_type,
            source_industry=industry_name,
            primary_sector=None,
            sub_sector=None,
            theme_clusters=etf_result["themes"],
            is_etf=True,
            classification_confidence=etf_result["confidence"],
            classification_reason="",
            review_required=etf_result["confidence"] == CONFIDENCE_LOW,
            etf_detail=dict(etf_result),
        )

    if asset_type == ASSET_TYPE_REIT:
        return SecurityClassificationResult(
            stock_id=stock_id,
            stock_name=stock_name,
            asset_type=asset_type,
            source_industry=industry_name,
            primary_sector="REAL_ESTATE_TRUST",
            sub_sector=sub_industry or "不動產投資信託受益證券",
            classification_confidence=CONFIDENCE_HIGH,
            classification_reason="",
            review_required=False,
        )

    # COMMON_STOCK / PREFERRED_STOCK / DR：先查個股 override，查不到才走系統性映射
    override = STOCK_OVERRIDES.get(stock_id)
    if override:
        primary_sector = override["primary_sector"]
        sub_sector = override.get("sub_sector") or sub_industry
        confidence = override["confidence"]
        theme_clusters = list(override.get("theme_clusters", []))
        reason = override.get("reason", "")
    else:
        mapped = map_industry_name_to_primary_sector(industry_name)
        if mapped is None or mapped == NEEDS_OVERRIDE:
            primary_sector = "DIVERSIFIED_OTHER"
            confidence = CONFIDENCE_LOW
            reason = (
                f"industry_name='{industry_name}' 無系統性映射也無個股 override，"
                "需人工複核"
            )
        else:
            primary_sector = mapped
            confidence = CONFIDENCE_HIGH
            reason = ""
        sub_sector = sub_industry
        theme_clusters = []

    is_financial = primary_sector == "FINANCIAL"

    return SecurityClassificationResult(
        stock_id=stock_id,
        stock_name=stock_name,
        asset_type=asset_type,
        source_industry=industry_name,
        primary_sector=primary_sector,
        sub_sector=sub_sector,
        theme_clusters=theme_clusters,
        is_financial=is_financial,
        classification_confidence=confidence,
        classification_reason=reason,
        review_required=confidence == CONFIDENCE_LOW,
    )


def classify_all(db: Session) -> List[SecurityClassificationResult]:
    """對 `stocks_master` 全表（含 is_active=false，Phase 1 要求完整 universe）分類。"""
    rows = db.query(StockMaster).all()
    return [
        classify_security(r.stock_id, r.stock_name, r.industry_name, r.sub_industry)
        for r in rows
    ]
