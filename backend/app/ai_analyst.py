"""
AI analyst sub-agent powered by OpenAI.

Takes stock data (institutional flow, price, industry) from the database
and asks OpenAI to provide investment analysis and commentary.
"""
import logging
from datetime import date, timedelta
from typing import Optional

from openai import OpenAI
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DailyPrice, InstStockFlow, StockMaster
from app.settings import get_openai_api_key, get_openai_model

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一位專業的台股投資分析師，擅長籌碼面分析。
你的任務是根據提供的三大法人買賣超資料，給出簡潔、有洞察力的分析。

規則：
- 使用繁體中文回覆
- 回覆控制在 300 字以內
- 分析要包含：籌碼面觀察、法人動向解讀、短期多空看法
- 語氣專業但易懂，適合一般散戶閱讀
- 不要給出明確的買賣建議或目標價
- 結尾加上免責聲明：「⚠️ 以上為 AI 分析僅供參考，不構成投資建議。」
"""


def _collect_stock_context(db: Session, stock_id: str, days: int = 5) -> Optional[str]:
    """
    Collect recent stock data from DB and format as context for LLM.
    Returns None if stock not found.
    """
    stock = db.get(StockMaster, stock_id)
    if not stock:
        return None

    # Find latest trade date
    latest_date = db.query(func.max(DailyPrice.trade_date)).scalar()
    if not latest_date:
        return None

    # Get recent prices
    start_date = latest_date - timedelta(days=days * 2)  # buffer for weekends
    prices = (
        db.query(DailyPrice)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date >= start_date,
            DailyPrice.trade_date <= latest_date,
        )
        .order_by(DailyPrice.trade_date.desc())
        .limit(days)
        .all()
    )

    # Get recent flows
    flows = (
        db.query(InstStockFlow)
        .filter(
            InstStockFlow.stock_id == stock_id,
            InstStockFlow.trade_date >= start_date,
            InstStockFlow.trade_date <= latest_date,
        )
        .order_by(InstStockFlow.trade_date.desc())
        .all()
    )

    # Build flow map: {date: {inst_type: flow}}
    flow_map = {}
    for f in flows:
        d = f.trade_date
        if d not in flow_map:
            flow_map[d] = {}
        flow_map[d][f.inst_type] = f

    # Format context
    lines = [
        f"股票：{stock.stock_name}（{stock.stock_id}）",
        f"產業：{stock.industry_name}",
    ]
    if stock.sub_industry:
        lines.append(f"子產業：{stock.sub_industry}")
    if stock.chain:
        lines.append(f"供應鏈位置：{stock.chain}")

    lines.append(f"\n最近 {len(prices)} 個交易日資料（由新到舊）：")

    for p in prices:
        d = p.trade_date
        day_flows = flow_map.get(d, {})

        price_str = f"  {d} 收盤 {p.close_price:.2f}" if p.close_price else f"  {d} 無收盤價"

        flow_parts = []
        for inst_type, label in [("foreign", "外資"), ("trust", "投信"), ("dealer", "自營")]:
            f = day_flows.get(inst_type)
            if f and f.net_shares:
                lots = f.net_shares / 1000
                flow_parts.append(f"{label} {lots:+,.0f}張")

        flow_str = "｜".join(flow_parts) if flow_parts else "無法人資料"
        lines.append(f"{price_str}｜{flow_str}")

    return "\n".join(lines)


def analyze_stock(stock_id: str) -> str:
    """
    Run AI analysis for a given stock_id.
    Returns formatted analysis string.
    """
    openai_api_key = get_openai_api_key()
    if not openai_api_key:
        return "⚠️ AI 分析功能未啟用（OPENAI_API_KEY 未設定）。"

    db = SessionLocal()
    try:
        context = _collect_stock_context(db, stock_id)
        if context is None:
            return f"❌ 找不到股票 `{stock_id}` 的資料。"

        logger.info("AI analysis request: stock_id=%s", stock_id)

        client = OpenAI(api_key=openai_api_key)
        response = client.chat.completions.create(
            model=get_openai_model(),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"請分析以下股票的近期籌碼動向：\n\n{context}"},
            ],
            max_tokens=500,
            temperature=0.7,
        )

        analysis = response.choices[0].message.content.strip()
        return f"🤖 *AI 籌碼分析 — {stock_id}*\n\n{analysis}"

    except Exception:
        logger.exception("AI analysis failed for %s", stock_id)
        return "⚠️ AI 分析時發生錯誤，請稍後再試。"
    finally:
        db.close()
