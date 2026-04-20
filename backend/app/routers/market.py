"""
Market-level analysis endpoints.

GET /api/market/daily-brief?date=YYYY-MM-DD
  Collects institutional flow + top industry data from DB,
  fetches external market indicators (VIX, oil, USD/TWD) via Yahoo Finance,
  then calls OpenAI to produce a pre-market briefing in the fixed format
  defined by DAILY_BRIEF_SYSTEM_PROMPT.
"""
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from openai import OpenAI
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.industry_flow_service import (
    get_latest_industry_trade_date,
    get_recent_industry_trade_dates,
    load_industry_flow_rows,
    load_industry_flow_rows_for_dates,
)
from app.models import InstStockFlow, StockMaster
from app.settings import get_openai_api_key, get_openai_model

logger = logging.getLogger(__name__)

router = APIRouter(tags=["market"])

# ── System prompt (fixed output format) ─────────────────────────────────────

DAILY_BRIEF_SYSTEM_PROMPT = """你現在是一個台股盤前分析助手。

你的任務是根據我提供的資料，輸出「今日台股應注意事項、操作傾向、值得關注的細產業與推薦個股」。

## 分析輸入
你會拿到以下資料：

1. 前一天台股恐懼指數
2. 前一天美國恐懼指數
3. 前三天石油價格走勢
4. 前一天美元匯率走勢
5. 前一天台股三大法人買賣超情況
6. 前一天三大法人買賣超最多的細產業排名 Top N
7. 前三日三大法人累積買賣超最多的細產業排名 Top N
8. 每個細產業對應的代表個股或主要個股清單
9. 可選：前一天大盤漲跌、成交量、權值股表現
10. 可選：可選股的前一天股價表現、成交量、技術面簡述

---

## 你的分析邏輯

請依照下面順序分析：

### A. 總體風險情緒判讀
依序判斷：
- 台股恐懼指數高低
- 美國恐懼指數高低
- 石油近三日是上漲、下跌還是高檔震盪
- 美元匯率是走強還是走弱
- 三大法人整體是買超還是賣超

每一項都要用以下格式輸出：

{指標名稱}: {數值或趨勢}
{一句簡短分析}

分析要短，不能空泛，要直接講市場意義，例如：
- 市場情緒偏穩定
- 風險偏好下降
- 通膨壓力仍在
- 外資流出壓力增加
- 內資撐盤但外資保守

---

### B. 綜合判斷今日操作傾向
綜合 A 的所有因子，最後只給出以下三選一結論之一：

- 積極買進
- 留倉觀望
- 偏保守撤退

並補一句非常簡短的理由，格式如下：

綜合判斷: {三選一}
原因: {一句話總結}

判斷原則：
- 若風險情緒穩定、美元無明顯走強、法人偏買超，可偏向「積極買進」
- 若多空分歧、法人不一致、油價或美元造成壓力，偏向「留倉觀望」
- 若恐慌升高、美元明顯走強、法人明顯賣超，偏向「偏保守撤退」

---

### C. 細產業資金分析
你要同時看兩份排名：

1. 前一天三大法人買超最多的細產業
2. 前三日三大法人累積買超最多的細產業

請找出兩者的「交集細產業」。

定義如下：
- 只出現在前1日榜：屬於短線題材
- 只出現在前3日榜：屬於布局中
- 同時出現在前1日榜與前3日榜：屬於主流強勢產業，優先關注

輸出格式如下：

前1日買超前三細產業:
1. {產業A}
2. {產業B}
3. {產業C}

前3日累積買超前三細產業:
1. {產業X}
2. {產業Y}
3. {產業Z}

交集細產業:
1. {交集產業1}: {一句原因}
2. {交集產業2}: {一句原因}

如果沒有交集，請直接寫：
交集細產業: 無明確交集
並補一句：
代表資金輪動快速，主流尚未集中

---

### D. 推薦關注個股
只從「交集細產業」中挑選推薦個股。

每個交集細產業推薦 2 檔個股。
如果資料不足，至少推薦 1 檔。
推薦個股時必須附原因，原因必須根據以下邏輯：
- 是否受惠該產業近期資金流入
- 是否具備題材性（AI、油價、原物料、設備、政策、景氣循環等）
- 是否屬於該細產業代表股或龍頭股
- 若有提供技術面/量能資料，可補充「量能放大、走勢較強」之類的短句

輸出格式如下：

推薦關注個股:

[{細產業1}]
- {股票1}: {推薦原因}
- {股票2}: {推薦原因}

[{細產業2}]
- {股票3}: {推薦原因}
- {股票4}: {推薦原因}

推薦原因要具體、簡短，不要寫成空泛句子。
例如：
- 受惠先進製程擴產，屬設備龍頭
- AI ASIC 題材延續，法人持續布局
- 油價支撐報價，屬塑化上游代表股
- 近期量能增溫，短線動能較強

---

## 嚴格輸出要求

1. 不要出現任何 AI 口吻
2. 不要出現「建議您」「你可以考慮」「是否要我幫你」這類句子
3. 不要加 emoji
4. 不要加表格
5. 不要寫太多廢話
6. 每個段落都要短、直接、像盤前摘要
7. 不要虛構資料；若資料不足，直接寫「資料不足，無法判定」
8. 推薦個股一定要附原因
9. 最終輸出只能包含以下段落，順序不能變：

- 市場因子分析
- 綜合判斷
- 細產業資金分析
- 推薦關注個股

---

## 請按照這個固定格式輸出

市場因子分析

台股恐懼指數: {內容}
{簡短分析}

美國恐懼指數: {內容}
{簡短分析}

石油價格(前三日): {內容}
{簡短分析}

美元匯率: {內容}
{簡短分析}

三大法人買賣超: {內容}
{簡短分析}

綜合判斷

綜合判斷: {積極買進 / 留倉觀望 / 偏保守撤退}
原因: {一句簡短總結}

細產業資金分析

前1日買超前三細產業:
1. {內容}
2. {內容}
3. {內容}

前3日累積買超前三細產業:
1. {內容}
2. {內容}
3. {內容}

交集細產業:
1. {內容}
2. {內容}

推薦關注個股

[{細產業名稱}]
- {股票名稱}: {原因}
- {股票名稱}: {原因}

[{細產業名稱}]
- {股票名稱}: {原因}
- {股票名稱}: {原因}
"""


# ── Response schema ──────────────────────────────────────────────────────────

class DailyBriefResponse(BaseModel):
    trade_date: str
    content: str
    source: str  # "openai" | "unavailable"


class LatestTradeDateResponse(BaseModel):
    trade_date: Optional[str]


# ── Yahoo Finance helper ─────────────────────────────────────────────────────

def _yf_price(symbol: str, days: int = 5, end_date: Optional[date] = None) -> list[dict]:
    """Fetch recent daily OHLCV from Yahoo Finance v8 API. Returns [] on failure."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"interval": "1d", "includePrePost": "false"}
    if end_date is not None:
        lookback_days = max(days * 3, 10)
        start_date = end_date - timedelta(days=lookback_days)
        end_exclusive = end_date + timedelta(days=1)
        params["period1"] = int(datetime.combine(start_date, time.min, tzinfo=timezone.utc).timestamp())
        params["period2"] = int(datetime.combine(end_exclusive, time.min, tzinfo=timezone.utc).timestamp())
    else:
        params["range"] = f"{days}d"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=6)
        resp.raise_for_status()
        data = resp.json()
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        rows = []
        for ts, c in zip(timestamps, closes):
            if c is None:
                continue
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).date()
            rows.append({"date": str(dt), "close": c})
        if end_date is not None:
            rows = [row for row in rows if row["date"] <= str(end_date)]
        return rows[-days:]
    except Exception as exc:
        logger.warning("Yahoo Finance fetch failed for %s: %s", symbol, exc)
        return []


def _format_oil_trend(rows: list[dict]) -> str:
    """Summarise 3-day oil price movement."""
    if len(rows) < 2:
        return "資料不足，無法判定"
    closes = [r["close"] for r in rows[-3:]]
    prices_str = " / ".join(f"{c:.1f}" for c in closes)
    delta = closes[-1] - closes[0]
    direction = "上漲" if delta > 0.5 else "下跌" if delta < -0.5 else "橫盤震盪"
    return f"{prices_str} USD（{direction} {delta:+.1f}）"


def _format_usd_twd(rows: list[dict]) -> str:
    """Summarise USD/TWD rate direction."""
    if len(rows) < 2:
        return "資料不足，無法判定"
    prev = rows[-2]["close"]
    curr = rows[-1]["close"]
    delta = curr - prev
    direction = "走強" if delta > 0.05 else "走弱" if delta < -0.05 else "持平"
    return f"{curr:.2f}（{direction} {delta:+.2f}）"


def _format_vix(rows: list[dict], label: str) -> str:
    if not rows:
        return "資料不足，無法判定"
    val = rows[-1]["close"]
    level = "高恐慌（>30）" if val > 30 else "偏高（20-30）" if val > 20 else "中性（15-20）" if val > 15 else "偏低（<15）"
    return f"{val:.1f}  {level}"


# ── DB helpers ───────────────────────────────────────────────────────────────

def _get_latest_trade_date(db: Session) -> Optional[date]:
    return get_latest_industry_trade_date(db)


def _resolve_trade_date(db: Session, requested: Optional[date]) -> Optional[date]:
    """
    Return the most recent trading date in DB that is <= requested (or <= today if None).
    This ensures we always land on a date that actually has data, regardless of
    weekends or market holidays.
    """
    return get_latest_industry_trade_date(db, requested)


def _get_inst_totals(db: Session, trade_date: date) -> dict:
    """Aggregate total net_amount_est per inst_type across all stocks on given date."""
    rows = (
        db.query(InstStockFlow.inst_type, func.sum(InstStockFlow.net_amount_est))
        .filter(InstStockFlow.trade_date == trade_date)
        .group_by(InstStockFlow.inst_type)
        .all()
    )
    totals = {r[0]: (r[1] or 0.0) for r in rows}
    return totals


def _top_industries(db: Session, trade_date: date, n: int = 5) -> list[dict]:
    """Top N industries by total_net_amount on the given date."""
    rows = load_industry_flow_rows(db, trade_date)
    return [
        {"industry_name": row.industry_name, "total_net_amount": row.total_net_amount}
        for row in rows[:n]
    ]


def _top_industries_3d(db: Session, end_date: date, n: int = 5) -> list[dict]:
    """Top N industries by cumulative total_net_amount over last 3 trading days."""
    date_list = get_recent_industry_trade_dates(db, end_date, limit=3)
    if not date_list:
        return []

    snapshots = load_industry_flow_rows_for_dates(db, date_list)
    totals = defaultdict(float)
    for row in snapshots:
        totals[row.industry_name] += row.total_net_amount

    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:n]
    return [{"industry_name": industry_name, "cum_net_amount": amount} for industry_name, amount in ranked]


def _top_stocks_by_industry(db: Session, trade_date: date, industry_names: list[str], per_industry: int = 3) -> dict:
    """For each industry name, get top stocks by total net flow."""
    result = defaultdict(list)
    for ind in industry_names:
        stocks = (
            db.query(StockMaster.stock_id, StockMaster.stock_name, StockMaster.sub_industry)
            .filter(StockMaster.industry_name == ind)
            .all()
        )
        if not stocks:
            continue
        stock_ids = [s[0] for s in stocks]
        stock_map = {s[0]: s for s in stocks}

        flows = (
            db.query(
                InstStockFlow.stock_id,
                func.sum(InstStockFlow.net_amount_est).label("total"),
            )
            .filter(
                InstStockFlow.trade_date == trade_date,
                InstStockFlow.stock_id.in_(stock_ids),
            )
            .group_by(InstStockFlow.stock_id)
            .order_by(func.sum(InstStockFlow.net_amount_est).desc())
            .limit(per_industry)
            .all()
        )
        for f in flows:
            sm = stock_map.get(f[0])
            if sm:
                result[ind].append({
                    "stock_id": sm[0],
                    "stock_name": sm[1],
                    "sub_industry": sm[2],
                    "net_amount": f[1] or 0.0,
                })
    return result


def _choose_market_stance(
    us_vix_str: str,
    oil_str: str,
    usd_twd_str: str,
    inst_totals: dict,
) -> tuple[str, str]:
    total_net = sum(inst_totals.values())
    is_risk_off = "高恐慌" in us_vix_str or "偏高" in us_vix_str or "上漲" in oil_str or "走強" in usd_twd_str
    is_supportive = "偏低" in us_vix_str or "中性" in us_vix_str

    if total_net > 0 and is_supportive and not is_risk_off:
        return "積極買進", "法人偏多且外部風險指標未明顯惡化"
    if total_net < 0 and is_risk_off:
        return "偏保守撤退", "法人偏空，且波動與匯率訊號同步轉弱"
    return "留倉觀望", "資金與外部因子未形成明確單邊訊號"


def _build_fallback_brief(
    trade_date: date,
    us_vix_str: str,
    oil_str: str,
    usd_twd_str: str,
    inst_totals: dict,
    top1d: list[dict],
    top3d: list[dict],
    top_stocks: dict,
) -> str:
    top1d_names = [row["industry_name"] for row in top1d[:3]]
    top3d_names = [row["industry_name"] for row in top3d[:3]]
    overlap = [name for name in top1d_names if name in top3d_names]
    stance, reason = _choose_market_stance(us_vix_str, oil_str, usd_twd_str, inst_totals)

    overlap_lines = []
    for index, name in enumerate(overlap[:2], start=1):
        overlap_lines.append(f"{index}. {name}: 短中期資金榜同步出現，延續性較佳")
    if not overlap_lines:
        overlap_lines = ["交集細產業: 無明確交集", "代表資金輪動快速，主流尚未集中"]

    recommendation_lines = ["推薦關注個股"]
    if overlap:
        for industry_name in overlap[:2]:
            recommendation_lines.append("")
            recommendation_lines.append(f"[{industry_name}]")
            stocks = top_stocks.get(industry_name, [])[:2]
            if not stocks:
                recommendation_lines.append("- 資料不足，無法判定")
                continue
            for stock in stocks:
                recommendation_lines.append(
                    f"- {stock['stock_name']}: 法人同日淨流入 {_fmt_amount(stock['net_amount'])}，屬該族群代表股"
                )
    else:
        recommendation_lines.append("")
        recommendation_lines.append("[資料不足]")
        recommendation_lines.append("- 暫無明確交集細產業，先觀察資金是否重新集中")

    return "\n".join([
        "市場因子分析",
        "",
        "台股恐懼指數: 資料不足，無法判定",
        "缺少一致指標，先以法人與外部市場因子輔助判讀。",
        "",
        f"美國恐懼指數: {us_vix_str}",
        "用波動高低觀察風險偏好是否降溫。",
        "",
        f"石油價格(前三日): {oil_str}",
        "油價變動可反映通膨與景氣敏感族群壓力。",
        "",
        f"美元匯率: {usd_twd_str}",
        "美元走強通常代表資金偏保守。",
        "",
        f"三大法人買賣超: 合計 {_fmt_amount(sum(inst_totals.values()))}",
        "外資、投信、自營商合計資金方向可作為短線風向。",
        "",
        "綜合判斷",
        "",
        f"綜合判斷: {stance}",
        f"原因: {reason}",
        "",
        "細產業資金分析",
        "",
        "前1日買超前三細產業:",
        *(f"{i}. {name}" for i, name in enumerate(top1d_names, start=1)),
        "前3日累積買超前三細產業:",
        *(f"{i}. {name}" for i, name in enumerate(top3d_names, start=1)),
        "交集細產業:" if overlap else overlap_lines[0],
        *([] if not overlap else overlap_lines),
        *(overlap_lines[1:] if not overlap else []),
        "",
        *recommendation_lines,
    ])


# ── Build LLM user message ────────────────────────────────────────────────────

def _fmt_amount(v: float) -> str:
    b = v / 1e8
    sign = "+" if b > 0 else ""
    return f"{sign}{b:.1f} 億"


def _build_user_message(
    trade_date: str,
    tw_vix: str,
    us_vix: str,
    oil: str,
    usd_twd: str,
    inst_totals: dict,
    top1d: list[dict],
    top3d: list[dict],
    top_stocks: dict,
) -> str:
    foreign = _fmt_amount(inst_totals.get("foreign", 0))
    trust = _fmt_amount(inst_totals.get("trust", 0))
    dealer = _fmt_amount(inst_totals.get("dealer", 0))
    total = _fmt_amount(sum(inst_totals.values()))

    ind_1d = "\n".join(f"{i+1}. {r['industry_name']}（{_fmt_amount(r['total_net_amount'])}）"
                       for i, r in enumerate(top1d))
    ind_3d = "\n".join(f"{i+1}. {r['industry_name']}（累積 {_fmt_amount(r['cum_net_amount'])}）"
                       for i, r in enumerate(top3d))

    stocks_section_lines = []
    for ind_name, stocks in top_stocks.items():
        if not stocks:
            continue
        stocks_section_lines.append(f"\n[{ind_name}]")
        for s in stocks:
            stocks_section_lines.append(
                f"  {s['stock_id']} {s['stock_name']}"
                + (f"（{s['sub_industry']}）" if s['sub_industry'] else "")
                + f" 法人淨買 {_fmt_amount(s['net_amount'])}"
            )
    stocks_section = "\n".join(stocks_section_lines) if stocks_section_lines else "（無資料）"

    return f"""分析日期：{trade_date}

【市場指標】
台股恐懼指數：{tw_vix}
美國恐懼指數（VIX）：{us_vix}
石油價格（近三日，WTI）：{oil}
美元兌台幣（USD/TWD）：{usd_twd}

【三大法人買賣超】
外資：{foreign}
投信：{trust}
自營商：{dealer}
合計：{total}

【前1日法人買超最多細產業 Top5】
{ind_1d}

【前3日累積法人買超最多細產業 Top5】
{ind_3d}

【各細產業代表股（前1日法人淨買）】
{stocks_section}
"""


# ── Endpoint ─────────────────────────────────────────────────────────────────


@router.get("/market/latest-trade-date", response_model=LatestTradeDateResponse)
def get_latest_trade_date(db: Session = Depends(get_db)):
    """Return the most recent trading date that has industry flow data in DB."""
    latest = _get_latest_trade_date(db)
    return LatestTradeDateResponse(trade_date=str(latest) if latest else None)


def build_daily_brief(db: Session, requested_date: Optional[date]) -> DailyBriefResponse:
    """
    Build a pre-market briefing payload. Shared by the HTTP endpoint and the
    Telegram bot so both surfaces return identical content.

    Raises ValueError when no trade data is available.
    """
    trade_date = _resolve_trade_date(db, requested_date)
    if not trade_date:
        raise ValueError("資料庫無產業流向資料")

    logger.info("daily-brief requested for %s", trade_date)

    # DB data
    inst_totals = _get_inst_totals(db, trade_date)
    top1d = _top_industries(db, trade_date, n=5)
    top3d = _top_industries_3d(db, trade_date, n=5)

    # Get top stocks for industries appearing in either list
    all_ind_names = list({r["industry_name"] for r in top1d + top3d})
    top_stocks = _top_stocks_by_industry(db, trade_date, all_ind_names, per_industry=3)

    # External market data (best-effort)
    us_vix_rows = _yf_price("^VIX", days=3, end_date=trade_date)
    oil_rows = _yf_price("CL=F", days=5, end_date=trade_date)
    usd_twd_rows = _yf_price("TWD=X", days=3, end_date=trade_date)  # USD/TWD

    tw_vix_str = "資料不足，無法判定"  # Taiwan doesn't have a standard fear index via Yahoo
    us_vix_str = _format_vix(us_vix_rows, "美國")
    oil_str = _format_oil_trend(oil_rows)
    usd_twd_str = _format_usd_twd(usd_twd_rows)

    user_msg = _build_user_message(
        trade_date=str(trade_date),
        tw_vix=tw_vix_str,
        us_vix=us_vix_str,
        oil=oil_str,
        usd_twd=usd_twd_str,
        inst_totals=inst_totals,
        top1d=top1d,
        top3d=top3d,
        top_stocks=top_stocks,
    )

    openai_api_key = get_openai_api_key()
    content = ""
    source = "unavailable"
    if openai_api_key:
        openai_model = get_openai_model()
        logger.debug("Calling OpenAI for daily brief (model=%s)", openai_model)
        try:
            client = OpenAI(api_key=openai_api_key)
            response = client.chat.completions.create(
                model=openai_model,
                messages=[
                    {"role": "system", "content": DAILY_BRIEF_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                max_completion_tokens=1200,
                temperature=0.4,
            )
            content = response.choices[0].message.content.strip()
            source = "openai"
        except Exception:
            logger.exception("OpenAI call failed for daily brief, falling back to deterministic summary")
    else:
        logger.warning("OPENAI_API_KEY not configured for daily brief, using deterministic fallback")

    if not content:
        content = _build_fallback_brief(
            trade_date=trade_date,
            us_vix_str=us_vix_str,
            oil_str=oil_str,
            usd_twd_str=usd_twd_str,
            inst_totals=inst_totals,
            top1d=top1d,
            top3d=top3d,
            top_stocks=top_stocks,
        )

    return DailyBriefResponse(
        trade_date=str(trade_date),
        content=content,
        source=source,
    )


@router.get("/market/daily-brief", response_model=DailyBriefResponse)
def get_daily_brief(
    date: Optional[date] = Query(default=None, description="trade date YYYY-MM-DD; defaults to latest available"),
    db: Session = Depends(get_db),
):
    try:
        return build_daily_brief(db, date)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
