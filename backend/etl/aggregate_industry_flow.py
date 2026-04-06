"""
Aggregate inst_stock_flow into industry_daily_flow.

Aggregation level (effective_industry):
  - Use stocks_master.sub_industry if set
  - Otherwise fall back to stocks_master.industry_name (FinMind top-level)

Amount columns sourced from inst_stock_flow.{buy,sell,net}_amount_est:
  foreign_net_amount = SUM(net_amount_est WHERE inst_type='foreign')
  trust_net_amount   = SUM(net_amount_est WHERE inst_type='trust')
  dealer_net_amount  = SUM(net_amount_est WHERE inst_type='dealer')
  total_net_amount   = foreign + trust + dealer
  total_buy_amount   = SUM(buy_amount_est)  across all inst_types
  total_sell_amount  = SUM(sell_amount_est) across all inst_types
"""
import logging
from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session

from app.models import InstStockFlow, IndustryDailyFlow, StockMaster

logger = logging.getLogger(__name__)


def aggregate_industry_flow(db: Session, trade_date: date) -> int:
    """
    Read inst_stock_flow for the given date, aggregate by effective_industry,
    and upsert into industry_daily_flow.

    Args:
        db: SQLAlchemy session
        trade_date: target date

    Returns:
        number of industry rows inserted or updated
    """
    # Build stock_id → effective_industry mapping
    masters = db.query(StockMaster).all()
    industry_map = {
        m.stock_id: (m.sub_industry if m.sub_industry else m.industry_name)
        for m in masters
    }
    logger.debug("Industry map built: %d stocks", len(industry_map))

    # Load all institutional flows for the day
    flows = (
        db.query(InstStockFlow)
        .filter_by(trade_date=trade_date)
        .all()
    )

    if not flows:
        logger.warning("No inst_stock_flow found for %s, skipping aggregation", trade_date)
        return 0

    # Accumulate by effective_industry
    # structure: {industry: {foreign_net, trust_net, dealer_net, total_buy, total_sell}}
    agg = defaultdict(lambda: {
        "foreign_net": 0.0,
        "trust_net":   0.0,
        "dealer_net":  0.0,
        "total_buy":   0.0,
        "total_sell":  0.0,
    })

    for flow in flows:
        industry = industry_map.get(flow.stock_id)
        if not industry:
            continue  # stock not in stocks_master, skip

        bucket = agg[industry]
        bucket["total_buy"]  += flow.buy_amount_est or 0.0
        bucket["total_sell"] += flow.sell_amount_est or 0.0

        if flow.inst_type == "foreign":
            bucket["foreign_net"] += flow.net_amount_est or 0.0
        elif flow.inst_type == "trust":
            bucket["trust_net"] += flow.net_amount_est or 0.0
        elif flow.inst_type == "dealer":
            bucket["dealer_net"] += flow.net_amount_est or 0.0

    count = 0
    for industry, vals in agg.items():
        total_net = vals["foreign_net"] + vals["trust_net"] + vals["dealer_net"]

        existing = (
            db.query(IndustryDailyFlow)
            .filter_by(trade_date=trade_date, industry_name=industry)
            .first()
        )
        if existing:
            existing.total_buy_amount    = vals["total_buy"]
            existing.total_sell_amount   = vals["total_sell"]
            existing.total_net_amount    = total_net
            existing.foreign_net_amount  = vals["foreign_net"]
            existing.trust_net_amount    = vals["trust_net"]
            existing.dealer_net_amount   = vals["dealer_net"]
        else:
            db.add(IndustryDailyFlow(
                trade_date          = trade_date,
                industry_name       = industry,
                total_buy_amount    = vals["total_buy"],
                total_sell_amount   = vals["total_sell"],
                total_net_amount    = total_net,
                foreign_net_amount  = vals["foreign_net"],
                trust_net_amount    = vals["trust_net"],
                dealer_net_amount   = vals["dealer_net"],
            ))
        count += 1

    db.commit()
    logger.info("Industry flow aggregated: %d industries for %s", count, trade_date)
    return count
