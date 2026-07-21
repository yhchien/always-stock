"""
Phase 1 Canonical Market Classification backfill（2026-07-21）。

對 `stocks_master` 全表（含 is_active=false）跑 `app/classification/build.py`，
UPSERT 進 `security_classification` / `etf_classification` 兩張新表，並輸出
validation 統計 + Phase 1 交付檔案（CSV/JSON/MD）到
`docs/plans/canonical_classification/`。

**不觸碰**：`stocks_master.industry_name/sub_industry`、任何 `app/signals/*` 選股邏輯。

用法：
    python run_classification_backfill.py
    python run_classification_backfill.py --dry-run   # 只跑分類 + 印統計，不寫 DB
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from app.classification.build import classify_all, SecurityClassificationResult
from app.classification.taxonomy import (
    ASSET_TYPE_ETF,
    ASSET_TYPE_ETN,
    CONFIDENCE_LOW,
    MAPPING_VERSION,
    PRIMARY_SECTORS,
)
from app.database import SessionLocal
from app.models import EtfClassification, SecurityClassification

REPORT_DIR = Path(__file__).resolve().parents[1] / "docs" / "plans" / "canonical_classification"


def _upsert(db, results: list[SecurityClassificationResult]) -> None:
    for r in results:
        if r.asset_type in (ASSET_TYPE_ETF, ASSET_TYPE_ETN):
            row = db.get(EtfClassification, r.stock_id)
            detail = r.etf_detail or {}
            if row is None:
                row = EtfClassification(stock_id=r.stock_id)
                db.add(row)
            row.asset_type = r.asset_type
            row.asset_class = detail.get("asset_class", "OTHER")
            row.region = detail.get("region", "OTHER")
            row.strategy = detail.get("strategy", "OTHER")
            row.themes = detail.get("themes") or []
            row.tracking_index = detail.get("tracking_index")
            row.is_leveraged = bool(detail.get("is_leveraged"))
            row.is_inverse = bool(detail.get("is_inverse"))
            row.is_active = bool(detail.get("is_active"))
            row.classification_confidence = r.classification_confidence or "MEDIUM"
            row.mapping_version = MAPPING_VERSION
        else:
            row = db.get(SecurityClassification, r.stock_id)
            if row is None:
                row = SecurityClassification(stock_id=r.stock_id)
                db.add(row)
            row.asset_type = r.asset_type
            row.source_industry = r.source_industry
            row.primary_sector = r.primary_sector
            row.sub_sector = r.sub_sector
            row.secondary_sectors = r.secondary_sectors or []
            row.theme_clusters = r.theme_clusters or []
            row.is_financial = r.is_financial
            row.is_etf = False
            row.classification_confidence = r.classification_confidence
            row.classification_reason = r.classification_reason
            row.review_required = r.review_required
            row.mapping_version = MAPPING_VERSION
    db.commit()


def _write_reports(results: list[SecurityClassificationResult]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    common = [r for r in results if r.asset_type not in (ASSET_TYPE_ETF, ASSET_TYPE_ETN)]
    etfs = [r for r in results if r.asset_type in (ASSET_TYPE_ETF, ASSET_TYPE_ETN)]

    with open(REPORT_DIR / "stock_sector_mapping.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "stock_id", "stock_name", "asset_type", "source_industry",
            "primary_sector", "primary_sector_label", "sub_sector",
            "theme_clusters", "is_financial", "confidence", "review_required", "reason",
        ])
        for r in common:
            w.writerow([
                r.stock_id, r.stock_name, r.asset_type, r.source_industry or "",
                r.primary_sector or "", PRIMARY_SECTORS.get(r.primary_sector or "", ""),
                r.sub_sector or "", "|".join(r.theme_clusters or []),
                r.is_financial, r.classification_confidence or "",
                r.review_required, r.classification_reason or "",
            ])

    with open(REPORT_DIR / "etf_classification.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "stock_id", "stock_name", "asset_type", "asset_class", "region",
            "strategy", "themes", "tracking_index", "is_leveraged", "is_inverse",
            "is_active", "confidence",
        ])
        for r in etfs:
            d = r.etf_detail or {}
            w.writerow([
                r.stock_id, r.stock_name, r.asset_type, d.get("asset_class", ""),
                d.get("region", ""), d.get("strategy", ""),
                "|".join(d.get("themes") or []), d.get("tracking_index") or "",
                d.get("is_leveraged"), d.get("is_inverse"), d.get("is_active"),
                r.classification_confidence or "",
            ])

    review_rows = [r for r in results if r.review_required]
    with open(REPORT_DIR / "sector_mapping_manual_review.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["stock_id", "stock_name", "asset_type", "source_industry",
                    "primary_sector", "confidence", "reason"])
        for r in review_rows:
            w.writerow([r.stock_id, r.stock_name, r.asset_type, r.source_industry or "",
                        r.primary_sector or "", r.classification_confidence or "",
                        r.classification_reason or ""])

    catch_all = [r for r in common if r.primary_sector == "DIVERSIFIED_OTHER"]
    with open(REPORT_DIR / "catch_all_remap_report.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["stock_id", "stock_name", "source_industry", "confidence", "reason"])
        for r in catch_all:
            w.writerow([r.stock_id, r.stock_name, r.source_industry or "",
                        r.classification_confidence or "", r.classification_reason or ""])

    with open(REPORT_DIR / "canonical_sector_taxonomy.json", "w", encoding="utf-8") as f:
        json.dump({"mapping_version": MAPPING_VERSION, "primary_sectors": PRIMARY_SECTORS},
                   f, ensure_ascii=False, indent=2)

    with open(REPORT_DIR / "listed_security_universe.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["stock_id", "stock_name", "asset_type", "source_industry"])
        for r in results:
            w.writerow([r.stock_id, r.stock_name, r.asset_type, r.source_industry or ""])


def _print_validation(results: list[SecurityClassificationResult]) -> dict:
    asset_type_counts = Counter(r.asset_type for r in results)
    common = [r for r in results if r.asset_type not in (ASSET_TYPE_ETF, ASSET_TYPE_ETN)
              and r.asset_type != "INDEX_BENCHMARK"]
    etfs = [r for r in results if r.asset_type in (ASSET_TYPE_ETF, ASSET_TYPE_ETN)]

    conf_counts = Counter(r.classification_confidence for r in common)
    primary_counts = Counter(r.primary_sector for r in common)
    review_count = sum(1 for r in common if r.review_required)
    catch_all_count = primary_counts.get("DIVERSIFIED_OTHER", 0)

    etf_conf_counts = Counter(r.classification_confidence for r in etfs)
    etf_region_counts = Counter((r.etf_detail or {}).get("region") for r in etfs)
    etf_strategy_counts = Counter((r.etf_detail or {}).get("strategy") for r in etfs)

    print("=" * 70)
    print("Phase 1 Canonical Classification — Validation Report")
    print("=" * 70)
    print(f"\n總證券數：{len(results)}")
    for k, v in asset_type_counts.most_common():
        print(f"  {k}: {v}")

    print(f"\n普通股（含金融/特別股/DR/REIT）分類覆蓋：{len(common)} 檔")
    for k, v in conf_counts.most_common():
        print(f"  confidence={k}: {v} ({v/len(common)*100:.1f}%)")
    print(f"  review_required: {review_count} ({review_count/len(common)*100:.1f}%)")
    print(f"  DIVERSIFIED_OTHER（未 remap 的 catch-all 殘留）: {catch_all_count}")

    print(f"\nPrimary sector 分布（前 15 大）：")
    for sector, count in primary_counts.most_common(15):
        label = PRIMARY_SECTORS.get(sector or "", sector or "(None)")
        print(f"  {label}: {count}")

    print(f"\nETF/ETN 分類：{len(etfs)} 檔")
    for k, v in etf_conf_counts.most_common():
        print(f"  confidence={k}: {v}")
    print(f"  region 分布：{dict(etf_region_counts)}")
    print(f"  strategy 分布：{dict(etf_strategy_counts)}")
    print("=" * 70)

    return {
        "total": len(results),
        "asset_type_counts": dict(asset_type_counts),
        "common_confidence_counts": dict(conf_counts),
        "review_required": review_count,
        "catch_all_remaining": catch_all_count,
        "primary_sector_distribution": dict(primary_counts),
        "etf_confidence_counts": dict(etf_conf_counts),
        "etf_region_distribution": dict(etf_region_counts),
        "etf_strategy_distribution": dict(etf_strategy_counts),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只分類 + 印統計，不寫 DB")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        results = classify_all(db)
        stats = _print_validation(results)
        _write_reports(results)
        if not args.dry_run:
            _upsert(db, results)
            print(f"\n已 UPSERT {len(results)} 筆分類至 security_classification / etf_classification")
        else:
            print("\n[--dry-run] 未寫入 DB")

        with open(REPORT_DIR / "validation_stats.json", "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
