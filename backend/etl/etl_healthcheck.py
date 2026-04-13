"""
ETL 健康檢查模組
監控 ETL 執行狀況、資料缺口、API 配額使用
"""

import logging
from datetime import datetime, timedelta, date
from typing import Dict, Any, Optional, List

from sqlalchemy.orm import Session
from sqlalchemy import func

logger = logging.getLogger(__name__)


class ETLHealthChecker:
    """ETL 健康檢查類"""

    def __init__(self, db: Session):
        self.db = db

    def check_daily_price_lag(
        self,
        target_date: Optional[date] = None,
        lag_threshold_hours: int = 25
    ) -> Dict[str, Any]:
        """
        檢查 daily_price 資料延遲

        Args:
            target_date: 目標日期（None = 今天）
            lag_threshold_hours: 告警閾值，預設 25 小時（超過一個交易日）

        Returns:
            {
                "status": "ok" | "warning" | "critical",
                "latest_date": date,
                "latest_ingested_at": datetime,
                "lag_hours": float,
                "message": str
            }
        """
        from app.models import DailyPrice

        if target_date is None:
            target_date = date.today()

        # 取最新一筆資料
        latest = self.db.query(DailyPrice).order_by(
            DailyPrice.ingested_at.desc()
        ).first()

        if not latest:
            return {
                "status": "critical",
                "latest_date": None,
                "latest_ingested_at": None,
                "lag_hours": None,
                "message": "No data in daily_price table",
            }

        now = datetime.utcnow()
        lag_delta = now - latest.ingested_at
        lag_hours = lag_delta.total_seconds() / 3600

        if lag_hours > lag_threshold_hours:
            status = "critical"
            message = f"ETL lag critical: {lag_hours:.1f} hours"
        elif lag_hours > lag_threshold_hours * 0.8:
            status = "warning"
            message = f"ETL lag warning: {lag_hours:.1f} hours"
        else:
            status = "ok"
            message = f"ETL lag normal: {lag_hours:.1f} hours"

        return {
            "status": status,
            "latest_date": latest.trade_date,
            "latest_ingested_at": latest.ingested_at,
            "lag_hours": lag_hours,
            "message": message,
        }

    def check_inst_flow_coverage(
        self,
        check_date: Optional[date] = None
    ) -> Dict[str, Any]:
        """
        檢查特定日期的法人資金流向覆蓋率

        Args:
            check_date: 檢查日期（None = 昨天）

        Returns:
            {
                "status": "ok" | "incomplete",
                "check_date": date,
                "covered_stocks": int,
                "total_stocks": int,
                "coverage_pct": float,
                "missing_stocks": [stock_id, ...],
                "message": str
            }
        """
        from app.models import InstStockFlow, StockMaster

        if check_date is None:
            check_date = date.today() - timedelta(days=1)

        # 該日期有資料的股票
        covered = self.db.query(InstStockFlow.stock_id).filter(
            InstStockFlow.trade_date == check_date
        ).distinct().count()

        # 上市公司總數
        total = self.db.query(StockMaster).filter(
            StockMaster.market == "twse"
        ).count()

        coverage_pct = (covered / total * 100) if total > 0 else 0

        # 取得缺失的股票
        covered_ids = self.db.query(InstStockFlow.stock_id).filter(
            InstStockFlow.trade_date == check_date
        ).distinct().all()
        covered_set = {row[0] for row in covered_ids}

        all_ids = self.db.query(StockMaster.stock_id).filter(
            StockMaster.market == "twse"
        ).all()
        all_set = {row[0] for row in all_ids}

        missing_stocks = sorted(all_set - covered_set)

        status = "ok" if coverage_pct >= 99 else "incomplete"

        return {
            "status": status,
            "check_date": check_date,
            "covered_stocks": covered,
            "total_stocks": total,
            "coverage_pct": round(coverage_pct, 2),
            "missing_stocks": missing_stocks[:20],  # 只顯示前 20 個
            "total_missing": len(missing_stocks),
            "message": f"Coverage: {coverage_pct:.1f}% ({covered}/{total})",
        }

    def check_source_distribution(self) -> Dict[str, Any]:
        """
        檢查目前 DB 中各資料源的分佈

        Returns:
            {
                "daily_price_sources": {"twse": count, "finmind": count, ...},
                "inst_flow_sources": {...},
                "broker_trade_sources": {...},
                "message": str
            }
        """
        from app.models import DailyPrice, InstStockFlow, BrokerTrade

        daily_price_sources = self.db.query(
            DailyPrice.source,
            func.count(DailyPrice.id).label("count")
        ).group_by(DailyPrice.source).all()

        inst_flow_sources = self.db.query(
            InstStockFlow.source,
            func.count(InstStockFlow.id).label("count")
        ).group_by(InstStockFlow.source).all()

        broker_trade_sources = self.db.query(
            BrokerTrade.source,
            func.count(BrokerTrade.id).label("count")
        ).group_by(BrokerTrade.source).all()

        return {
            "daily_price_sources": {source: count for source, count in daily_price_sources},
            "inst_flow_sources": {source: count for source, count in inst_flow_sources},
            "broker_trade_sources": {source: count for source, count in broker_trade_sources},
            "message": "Source distribution across tables",
        }

    def check_date_gaps(
        self,
        table_name: str,
        expected_dates: List[date],
        tolerance_days: int = 5
    ) -> Dict[str, Any]:
        """
        檢查特定表中的日期缺口

        Args:
            table_name: 表名 ("daily_price", "inst_stock_flow", "broker_trade")
            expected_dates: 預期的日期列表
            tolerance_days: 容許的缺失天數

        Returns:
            {
                "status": "ok" | "gap_detected",
                "expected_dates": count,
                "actual_dates": count,
                "missing_dates": [date, ...],
                "message": str
            }
        """
        from app.models import DailyPrice, InstStockFlow, BrokerTrade

        models = {
            "daily_price": DailyPrice,
            "inst_stock_flow": InstStockFlow,
            "broker_trade": BrokerTrade,
        }

        if table_name not in models:
            return {
                "status": "error",
                "message": f"Unknown table: {table_name}",
            }

        model = models[table_name]

        # 查詢該表中的所有日期
        actual_dates = set(
            row[0] for row in self.db.query(model.trade_date).distinct().all()
        )

        expected_set = set(expected_dates)
        missing_dates = sorted(expected_set - actual_dates)

        status = "gap_detected" if len(missing_dates) > tolerance_days else "ok"

        return {
            "status": status,
            "table": table_name,
            "expected_dates": len(expected_dates),
            "actual_dates": len(actual_dates),
            "missing_dates": missing_dates,
            "total_missing": len(missing_dates),
            "message": f"Date gaps in {table_name}: {len(missing_dates)} missing",
        }

    def get_full_health_report(self) -> Dict[str, Any]:
        """
        取得完整的健康檢查報告

        Returns:
            {
                "timestamp": datetime,
                "daily_price_lag": {...},
                "inst_flow_coverage": {...},
                "source_distribution": {...},
                "overall_status": "ok" | "warning" | "critical",
                "alerts": [alert, ...],
            }
        """
        alerts = []

        # 檢查各項
        price_lag = self.check_daily_price_lag()
        inst_coverage = self.check_inst_flow_coverage()
        source_dist = self.check_source_distribution()

        if price_lag["status"] == "critical":
            alerts.append(f"CRITICAL: {price_lag['message']}")
        elif price_lag["status"] == "warning":
            alerts.append(f"WARNING: {price_lag['message']}")

        if inst_coverage["status"] == "incomplete":
            alerts.append(f"WARNING: {inst_coverage['message']}")

        # 綜合判定
        if any("CRITICAL" in a for a in alerts):
            overall_status = "critical"
        elif alerts:
            overall_status = "warning"
        else:
            overall_status = "ok"

        return {
            "timestamp": datetime.utcnow(),
            "overall_status": overall_status,
            "daily_price_lag": price_lag,
            "inst_flow_coverage": inst_coverage,
            "source_distribution": source_dist,
            "alerts": alerts,
        }


def run_health_check(db: Session) -> None:
    """執行健康檢查並記錄結果"""
    checker = ETLHealthChecker(db)
    report = checker.get_full_health_report()

    logger.info("=" * 80)
    logger.info("ETL HEALTH CHECK REPORT")
    logger.info("=" * 80)
    logger.info(f"Overall Status: {report['overall_status'].upper()}")
    logger.info(f"Timestamp: {report['timestamp']}")

    if report["alerts"]:
        logger.warning("Alerts:")
        for alert in report["alerts"]:
            logger.warning(f"  - {alert}")

    logger.info("\nDaily Price Lag:")
    logger.info(f"  Status: {report['daily_price_lag']['status']}")
    logger.info(f"  Latest Date: {report['daily_price_lag']['latest_date']}")
    logger.info(f"  Lag Hours: {report['daily_price_lag']['lag_hours']:.1f}")

    logger.info("\nInstitutional Flow Coverage:")
    logger.info(f"  Status: {report['inst_flow_coverage']['status']}")
    logger.info(f"  Coverage: {report['inst_flow_coverage']['coverage_pct']:.1f}%")

    logger.info("\nSource Distribution:")
    for table, sources in [
        ("daily_price", report["source_distribution"]["daily_price_sources"]),
        ("inst_flow", report["source_distribution"]["inst_flow_sources"]),
        ("broker_trade", report["source_distribution"]["broker_trade_sources"]),
    ]:
        logger.info(f"  {table}: {sources}")

    logger.info("=" * 80)

    return report
