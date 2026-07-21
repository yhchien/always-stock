"""
Phase 1 Canonical Market Classification API（2026-07-21）。

顯示層專用：只回傳 `security_classification` / `etf_classification` 兩張表的內容，
供前端顯示 primary_sector / sub_sector / ETF taxonomy。**不影響**魚尾選股 pipeline
（`app/signals/*` 完全不 import 本檔）。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.classification.taxonomy import MAPPING_VERSION, PRIMARY_SECTORS
from app.database import get_db
from app.models import EtfClassification, SecurityClassification

router = APIRouter(tags=["classification"])


class EtfClassificationSchema(BaseModel):
    asset_class: str
    region: str
    strategy: str
    themes: List[str] = []
    tracking_index: Optional[str] = None
    is_leveraged: bool
    is_inverse: bool
    is_active: bool
    confidence: str


class SecurityClassificationSchema(BaseModel):
    stock_id: str
    asset_type: str
    source_industry: Optional[str] = None
    primary_sector: Optional[str] = None
    primary_sector_label: Optional[str] = None
    sub_sector: Optional[str] = None
    secondary_sectors: List[str] = []
    theme_clusters: List[str] = []
    is_financial: bool = False
    confidence: Optional[str] = None
    review_required: bool = False
    mapping_version: str = MAPPING_VERSION
    # 只有 asset_type in (ETF, ETN) 才會有值；canonical UI 顯示層需區分兩種卡片樣式
    etf: Optional[EtfClassificationSchema] = None


def _serialize(
    stock_id: str,
    sc: Optional[SecurityClassification],
    ec: Optional[EtfClassification],
) -> Optional[SecurityClassificationSchema]:
    if ec is not None:
        return SecurityClassificationSchema(
            stock_id=stock_id,
            asset_type=ec.asset_type,
            source_industry=None,
            primary_sector=None,
            primary_sector_label=None,
            sub_sector=None,
            confidence=ec.classification_confidence,
            review_required=ec.classification_confidence == "LOW",
            mapping_version=ec.mapping_version,
            etf=EtfClassificationSchema(
                asset_class=ec.asset_class,
                region=ec.region,
                strategy=ec.strategy,
                themes=ec.themes or [],
                tracking_index=ec.tracking_index,
                is_leveraged=ec.is_leveraged,
                is_inverse=ec.is_inverse,
                is_active=ec.is_active,
                confidence=ec.classification_confidence,
            ),
        )
    if sc is not None:
        return SecurityClassificationSchema(
            stock_id=stock_id,
            asset_type=sc.asset_type,
            source_industry=sc.source_industry,
            primary_sector=sc.primary_sector,
            primary_sector_label=PRIMARY_SECTORS.get(sc.primary_sector or "", None),
            sub_sector=sc.sub_sector,
            secondary_sectors=sc.secondary_sectors or [],
            theme_clusters=sc.theme_clusters or [],
            is_financial=sc.is_financial,
            confidence=sc.classification_confidence,
            review_required=sc.review_required,
            mapping_version=sc.mapping_version,
        )
    return None


def get_classification_for_stock(
    db: Session, stock_id: str
) -> Optional[SecurityClassificationSchema]:
    """供其他 router（例：stocks.py L2 個股頁）additive 嵌入使用的公開 helper。"""
    sc = db.get(SecurityClassification, stock_id)
    ec = db.get(EtfClassification, stock_id)
    return _serialize(stock_id, sc, ec)


@router.get("/classification/{stock_id}", response_model=SecurityClassificationSchema)
def get_classification(stock_id: str, db: Session = Depends(get_db)):
    result = get_classification_for_stock(db, stock_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No classification for stock_id={stock_id}")
    return result


@router.get("/classification", response_model=List[SecurityClassificationSchema])
def get_classification_batch(
    stock_ids: str = Query(..., description="逗號分隔的股票代號，例：2330,2454,0050"),
    db: Session = Depends(get_db),
):
    ids = [s.strip() for s in stock_ids.split(",") if s.strip()]
    if not ids:
        return []

    sc_rows = {
        r.stock_id: r
        for r in db.query(SecurityClassification).filter(SecurityClassification.stock_id.in_(ids)).all()
    }
    ec_rows = {
        r.stock_id: r
        for r in db.query(EtfClassification).filter(EtfClassification.stock_id.in_(ids)).all()
    }

    results = []
    for sid in ids:
        r = _serialize(sid, sc_rows.get(sid), ec_rows.get(sid))
        if r is not None:
            results.append(r)
    return results
