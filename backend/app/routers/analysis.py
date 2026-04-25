"""
Trade quality analysis endpoint.

POST /api/analysis/trade-quality
  Given a stock_id + optional buy_date, reconstruct the observable market
  context as of that date, then call OpenAI with the bundled buy-side research
  prompt in `backend/app/prompts/trade_quality.md`.

Output is a structured JSON with a 5-level rating plus a full markdown
report in Traditional Chinese. No hindsight bias — only pre-buy-date data.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from openai import OpenAI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.analysis import build_trade_quality_context
from app.auth import get_optional_user, require_user
from app.database import get_db
from app.industry_flow_service import get_latest_industry_trade_date
from app.models import (
    DailyPrice,
    FinancialStatement,
    InstStockFlow,
    MonthlyRevenue,
    StockMaster,
    User,
)
from app.rate_limit import limiter, trade_quality_limit_value
from app.settings import get_openai_api_key, get_openai_model

logger = logging.getLogger(__name__)
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
TW_MARKET_OPEN_TIME = time(hour=9, minute=0)

router = APIRouter(tags=["analysis"])

RATING_LABELS = {
    "STRONG_BUY": "強烈推薦",
    "BUY": "推薦",
    "NEUTRAL": "中立",
    "WATCH": "再看看",
    "RUN": "快跑",
}

PROMPT_FILES = [
    Path(__file__).resolve().parents[1] / "prompts" / "trade_quality.md",
    Path(__file__).resolve().parents[3] / "docs" / "trade_quality_prompt.md",
]


# ── Request / Response schemas ───────────────────────────────────────────────


class TradeQualityRequest(BaseModel):
    stock_id: str = Field(..., min_length=1, max_length=20)
    buy_date: Optional[date] = None


class TradeQualityResponse(BaseModel):
    stock_id: str
    stock_name: str
    buy_date: str
    rating: str  # STRONG_BUY | BUY | NEUTRAL | WATCH | RUN
    rating_label: str
    classification: Optional[str] = None  # A | B | C
    market_state: Optional[str] = None  # Hot | Cold
    quadrant: Optional[str] = None  # AA | AB | BA | BB
    expectation_gap: Optional[str] = None  # High | Medium | Low | Negative
    action: Optional[str] = None
    summary: str
    core_logic: Optional[str] = None
    risk_level: Optional[str] = None
    target_price_low: Optional[float] = None
    target_price_high: Optional[float] = None
    time_horizon_days: Optional[int] = None
    exit_price_low: Optional[float] = None
    exit_price_high: Optional[float] = None
    max_holding_days: Optional[int] = None
    report_markdown: str
    warnings: List[str] = []
    source: str  # "openai" | "unavailable" | "market_not_open"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _load_system_prompt() -> str:
    for prompt_file in PROMPT_FILES:
        if prompt_file.exists():
            return prompt_file.read_text(encoding="utf-8")

    logger.error("Prompt file missing. Tried: %s", ", ".join(str(path) for path in PROMPT_FILES))
    return ""


def _resolve_buy_date(db: Session, requested: Optional[date]) -> Optional[date]:
    """Resolve buy_date: explicit user input wins; otherwise fall back to latest trade date."""
    if requested is not None:
        return requested
    return get_latest_industry_trade_date(db)


def _get_taipei_now() -> datetime:
    return datetime.now(TAIPEI_TZ)


def _is_market_not_open_yet(requested: date, now: Optional[datetime] = None) -> bool:
    now = now or _get_taipei_now()
    if requested > now.date():
        return True
    return requested == now.date() and now.timetz().replace(tzinfo=None) < TW_MARKET_OPEN_TIME


def _build_market_not_open_response(stock: StockMaster, buy_date: date) -> TradeQualityResponse:
    message = "還沒開盤"
    return TradeQualityResponse(
        stock_id=stock.stock_id,
        stock_name=stock.stock_name,
        buy_date=str(buy_date),
        rating="WATCH",
        rating_label=RATING_LABELS["WATCH"],
        summary=message,
        report_markdown="還沒開盤，請等台股開盤後再分析。",
        warnings=["所選日期台股還沒開盤"],
        source="market_not_open",
    )


def _format_price_rows(rows: list[DailyPrice]) -> str:
    if not rows:
        return "無資料"
    lines = []
    for r in rows:
        spread_pct: Optional[float] = None
        if r.spread is not None:
            spread_pct = float(r.spread)
        lines.append(
            f"{r.trade_date} | O:{_fmt(r.open_price)} H:{_fmt(r.high_price)} "
            f"L:{_fmt(r.low_price)} C:{_fmt(r.close_price)} "
            f"Vol:{_fmt(r.volume)} Δ%:{_fmt(spread_pct)}"
        )
    return "\n".join(lines)


def _format_inst_flows(rows: list[InstStockFlow]) -> str:
    if not rows:
        return "無資料"
    by_date: dict[date, dict[str, float]] = {}
    for f in rows:
        by_date.setdefault(f.trade_date, {})[f.inst_type] = float(f.net_shares or 0)
    lines = []
    for d in sorted(by_date.keys()):
        entry = by_date[d]
        lines.append(
            f"{d} | 外資:{int(entry.get('foreign', 0))} "
            f"投信:{int(entry.get('trust', 0))} "
            f"自營:{int(entry.get('dealer', 0))}"
        )
    return "\n".join(lines)


def _format_monthly_revenue(rows: list[MonthlyRevenue]) -> str:
    if not rows:
        return "無資料"
    lines = []
    for r in rows:
        lines.append(
            f"{r.revenue_month} | 營收:{_fmt(r.revenue)} 百萬元 "
            f"YoY:{_fmt_pct(r.yoy_pct)} MoM:{_fmt_pct(r.mom_pct)}"
        )
    return "\n".join(lines)


def _fmt(x: Any) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.2f}"
    return str(x)


def _fmt_pct(x: Any) -> str:
    if x is None:
        return "—"
    return f"{float(x):+.2f}%"


def _collect_context(
    db: Session, stock_id: str, buy_date: date
) -> tuple[StockMaster, dict, list[str]]:
    warnings: list[str] = []

    stock = db.get(StockMaster, stock_id)
    if not stock:
        raise HTTPException(status_code=404, detail=f"Stock not found: {stock_id}")

    # M21 Phase B：raw OHLC / flows 僅保留 5 日供 AI 對照，結論層訊號由 build_trade_quality_context 提供
    start_raw = buy_date - timedelta(days=14)  # 5 交易日 ≈ 7~14 曆日
    prices = (
        db.query(DailyPrice)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date <= buy_date,
            DailyPrice.trade_date >= start_raw,
        )
        .order_by(DailyPrice.trade_date)
        .all()
    )
    prices = prices[-5:]
    if not prices:
        warnings.append("無近 5 交易日股價資料")

    flows = (
        db.query(InstStockFlow)
        .filter(
            InstStockFlow.stock_id == stock_id,
            InstStockFlow.trade_date <= buy_date,
            InstStockFlow.trade_date >= start_raw,
        )
        .order_by(InstStockFlow.trade_date)
        .all()
    )
    if not flows:
        warnings.append("無近 5 交易日三大法人資料")

    revenue_rows = (
        db.query(MonthlyRevenue)
        .filter(
            MonthlyRevenue.stock_id == stock_id,
            MonthlyRevenue.revenue_month <= buy_date,
        )
        .order_by(MonthlyRevenue.revenue_month.desc())
        .limit(3)
        .all()
    )
    revenue_rows = list(reversed(revenue_rows))
    if not revenue_rows:
        warnings.append("無買進日前月營收資料")

    latest_close = prices[-1].close_price if prices else None

    context = {
        "stock_id": stock.stock_id,
        "stock_name": stock.stock_name,
        "industry_name": stock.industry_name,
        "sub_industry": stock.sub_industry,
        "buy_date": str(buy_date),
        "latest_close": latest_close,
        "prices_text": _format_price_rows(prices),
        "flows_text": _format_inst_flows(flows),
        "revenue_text": _format_monthly_revenue(revenue_rows),
    }
    return stock, context, warnings


def _build_deterministic_context(
    db: Session, stock_id: str, buy_date: date, warnings: list[str]
) -> Optional[dict]:
    try:
        return build_trade_quality_context(db, stock_id, buy_date)
    except ValueError:
        # stock 找不到的情況已在 _collect_context 擋下；其他 ValueError 不預期
        raise
    except Exception:
        logger.exception("M21 context pipeline failed; falling back to raw-only context")
        warnings.append("deterministic 訊號管線暫時不可用，僅以原始資料判斷")
        return None


def _build_user_message(
    context: dict, m21_context: Optional[dict], warnings: list[str]
) -> str:
    news_note = (
        "本次分析無 10 天內新聞資料可供佐證；"
        "請依系統 prompt 規則 15 處理 —— 若你認為缺新聞導致無法建立有效交易判斷，"
        "請明講並歸類為 (C)。"
    )

    warning_block = ""
    if warnings:
        warning_block = "\n[資料警告]\n- " + "\n- ".join(warnings)

    if m21_context is not None:
        m21_block = (
            "[M21 預聚合訊號（deterministic，結論層）]\n"
            "說明：以下 6 個 section 已用純規則從 DB 預聚合完成；\n"
            "請**直接消費結論**，不要重新從 raw 數據推算相同訊號。\n"
            "欄位語義詳見 docs/plans/trade_quality_context_spec.md。\n"
            "下方 raw OHLC / 法人僅保留 5 日供你交叉驗證，不是主要推論依據。\n\n"
            "```json\n"
            f"{json.dumps(m21_context, ensure_ascii=False, indent=2)}\n"
            "```\n"
        )
    else:
        m21_block = "[M21 預聚合訊號]\n（不可用：請以下方原始資料自行推論）\n"

    return f"""[INPUT]
{{"stock": "{context['stock_name']} ({context['stock_id']})", "buy_date": "{context['buy_date']}"}}

[股票基本資訊]
- 代號：{context['stock_id']}
- 名稱：{context['stock_name']}
- 產業：{context['industry_name']}
- 細產業：{context['sub_industry'] or '—'}
- 買進日收盤價：{_fmt(context['latest_close'])}

{m21_block}
[近 5 交易日 OHLC / 成交量 / 漲跌%（僅供對照）]
{context['prices_text']}

[近 5 交易日三大法人淨買賣（張，僅供對照）]
{context['flows_text']}

[最近 3 個月月營收 + YoY/MoM]
{context['revenue_text']}

[新聞]
{news_note}
{warning_block}

[輸出要求]
請依系統 prompt 的分析邏輯（市場狀態 / 股票本質 / 四象限 / 預期差 / A/B/C 分類）完成分析，
並回傳單一 JSON object，欄位如下：

{{
  "stock": "股票名稱 (代號)",
  "buy_date": "YYYY-MM-DD",
  "market_state": "Hot" | "Cold",
  "quadrant": "AA" | "AB" | "BA" | "BB",
  "classification": "A" | "B" | "C",
  "classification_reason": "一句話",
  "expectation_gap": "High" | "Medium" | "Low" | "Negative",
  "action": "BUY" | "HOLD" | "EXIT" | "SHORT-TERM-TRADE",
  "core_logic": "一句話核心邏輯",
  "risk_level": "LOW" | "MEDIUM" | "HIGH",
  "rating": "STRONG_BUY" | "BUY" | "NEUTRAL" | "WATCH" | "RUN",
  "summary": "約 60~100 字的段落摘要，綜合判斷原因",
  "target_price_low": number 或 null,
  "target_price_high": number 或 null,
  "time_horizon_days": number 或 null,
  "exit_price_low": number 或 null,
  "exit_price_high": number 或 null,
  "max_holding_days": number 或 null,
  "report_markdown": "精簡版中文分析報告（markdown 字串）"
}}

rating 對應規則（由你依分析強度自行判斷，不依死板 A/B/C 映射）：
- STRONG_BUY 強烈推薦：A 類且多重條件同時成立
- BUY 推薦：A 類但部分條件仍待驗證
- NEUTRAL 中立：B 類
- WATCH 再看看：B 類偏弱 / C 類但不必立即退出
- RUN 快跑：C 類且應快速退出或停損

若 rating = RUN，target_price_low/high 應為 null，改填 exit_price_low/high。
若 rating = STRONG_BUY/BUY/NEUTRAL，exit_price_low/high 可為 null。

嚴格規則：
- 禁止 hindsight bias
- report_markdown 必須是繁體中文、完整段落、可讀
- report_markdown 請精簡：
  - 固定保留 5 個章節標題
  - 每個章節 1 小段，2~4 句即可
  - 總長盡量控制在 800~1200 個中文字內
  - 不要重複 JSON 已經表達過的欄位
- classification 與 rating 邏輯需一致（C 不可對應 STRONG_BUY）

⚠️ 輸出值域（強制，避免與系統 prompt 內部分類混淆）：
- `classification` 僅能是 `A` / `B` / `C` —— 這是「個股整體交易質量」分類。
  系統 prompt 的「產業熱錢等級（S/A/B/C）」是分析過程中的中間判斷，
  不可填入本欄位；產業熱錢等級若要提及，請寫在 `core_logic` 或 `report_markdown`。
- `rating` 僅能是 `STRONG_BUY` / `BUY` / `NEUTRAL` / `WATCH` / `RUN` 五個值之一。
  任何其他字串都會被後端視為 `NEUTRAL`。
"""


def _call_openai(system_prompt: str, user_msg: str) -> Optional[dict]:
    api_key = get_openai_api_key()
    if not api_key:
        logger.warning("OPENAI_API_KEY not configured; trade-quality analysis unavailable")
        return None

    model = get_openai_model()
    client = OpenAI(api_key=api_key)

    def request_json(messages: list[dict[str, str]], max_completion_tokens: int = 4000) -> str:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.3,
            max_completion_tokens=max_completion_tokens,
        )
        return response.choices[0].message.content or ""

    try:
        base_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]
        raw = request_json(base_messages)
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.exception("Failed to parse OpenAI JSON response for trade-quality; retrying once")
        try:
            retry_messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"{user_msg}\n\n"
                        "[重要補充]\n"
                        "你上一版輸出不是合法 JSON，請重新輸出一次。\n"
                        "只回傳單一、完整、可被 json.loads 解析的 JSON object。\n"
                        "不要加 markdown code fence、不要加任何前言或結尾。"
                    ),
                },
            ]
            retry_raw = request_json(retry_messages, max_completion_tokens=4500)
            return json.loads(retry_raw)
        except Exception:
            logger.exception("Retry also failed for trade-quality JSON parse")
            return None
    except Exception:
        logger.exception("OpenAI call failed for trade-quality")
        return None


def _normalize_response(
    payload: dict,
    stock: StockMaster,
    buy_date: date,
    warnings: list[str],
    source: str,
) -> TradeQualityResponse:
    rating = str(payload.get("rating", "NEUTRAL")).upper()
    if rating not in RATING_LABELS:
        rating = "NEUTRAL"

    return TradeQualityResponse(
        stock_id=stock.stock_id,
        stock_name=stock.stock_name,
        buy_date=str(buy_date),
        rating=rating,
        rating_label=RATING_LABELS[rating],
        classification=payload.get("classification"),
        market_state=payload.get("market_state"),
        quadrant=payload.get("quadrant"),
        expectation_gap=payload.get("expectation_gap"),
        action=payload.get("action"),
        summary=str(payload.get("summary", "")).strip() or "（AI 未提供摘要）",
        core_logic=payload.get("core_logic"),
        risk_level=payload.get("risk_level"),
        target_price_low=_to_float(payload.get("target_price_low")),
        target_price_high=_to_float(payload.get("target_price_high")),
        time_horizon_days=_to_int(payload.get("time_horizon_days")),
        exit_price_low=_to_float(payload.get("exit_price_low")),
        exit_price_high=_to_float(payload.get("exit_price_high")),
        max_holding_days=_to_int(payload.get("max_holding_days")),
        report_markdown=str(payload.get("report_markdown", "")).strip()
        or "（AI 未提供完整報告）",
        warnings=warnings,
        source=source,
    )


def _to_float(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _to_int(x: Any) -> Optional[int]:
    if x is None or x == "":
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


# ── Endpoint ─────────────────────────────────────────────────────────────────


@router.post("/analysis/trade-quality", response_model=TradeQualityResponse)
@limiter.limit(trade_quality_limit_value)
def analyze_trade_quality(
    request: Request,
    req: TradeQualityRequest,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
):
    """
    Produce a buy-side analyst report for a given stock + buy_date.
    Reconstructs observable market context up to buy_date (OHLC / institutional
    flows / monthly revenue), then calls OpenAI with the bundled prompt in
    backend/app/prompts/trade_quality.md.
    """
    logger.info("POST /analysis/trade-quality stock=%s buy_date=%s", req.stock_id, req.buy_date)

    if req.buy_date and _is_market_not_open_yet(req.buy_date):
        stock = db.get(StockMaster, req.stock_id)
        if not stock:
            raise HTTPException(status_code=404, detail=f"Stock not found: {req.stock_id}")
        return _build_market_not_open_response(stock, req.buy_date)

    resolved = _resolve_buy_date(db, req.buy_date)
    if not resolved:
        raise HTTPException(status_code=404, detail="資料庫無交易日資料")

    stock, context, warnings = _collect_context(db, req.stock_id, resolved)
    m21_context = _build_deterministic_context(db, req.stock_id, resolved, warnings)

    system_prompt = _load_system_prompt()
    if not system_prompt:
        raise HTTPException(status_code=500, detail="分析 prompt 檔案缺失")

    user_msg = _build_user_message(context, m21_context, warnings)
    payload = _call_openai(system_prompt, user_msg)

    if payload is None:
        # Fallback: build a minimal "data insufficient" response
        return TradeQualityResponse(
            stock_id=stock.stock_id,
            stock_name=stock.stock_name,
            buy_date=str(resolved),
            rating="WATCH",
            rating_label=RATING_LABELS["WATCH"],
            summary="AI 分析服務目前不可用（OpenAI 未設定或呼叫失敗），無法建立有效交易判斷。",
            report_markdown="⚠️ 無法連線 AI 分析服務，請稍後再試或檢查 OpenAI 設定。",
            warnings=warnings + ["OpenAI 服務不可用"],
            source="unavailable",
        )

    return _normalize_response(payload, stock, resolved, warnings, source="openai")


# ── Streaming endpoint（progress bar 用）─────────────────────────────────────
#
# POST /api/analysis/trade-quality/stream
#   與 /analysis/trade-quality 同樣輸入，但回傳 NDJSON（line-delimited JSON）。
#   前端可邊讀邊更新進度條。整體流程約 5~30 秒，stage 分四段：
#     1. collect_raw   — 從 DB 撈 raw OHLC / 法人 / 月營收
#     2. build_context — 跑 M21 deterministic context pipeline
#     3. openai_call   — 呼叫 OpenAI 等回應（最慢的一段）
#     4. done          — 完成，payload 為完整 TradeQualityResponse
#   失敗時最後 emit error event；HTTP 仍回 200（streaming 已 commit header）。
#   Pre-flight 檢查（stock 找不到 / prompt 缺檔）在 stream 開始前 raise，仍走 4xx/5xx。


def _emit(stage: str, label: str, payload: Optional[dict] = None) -> str:
    event: dict[str, Any] = {"stage": stage, "label": label}
    if payload is not None:
        event["payload"] = payload
    return json.dumps(event, ensure_ascii=False) + "\n"


@router.post("/analysis/trade-quality/stream")
@limiter.limit(trade_quality_limit_value)
def analyze_trade_quality_stream(
    request: Request,
    req: TradeQualityRequest,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
):
    logger.info(
        "POST /analysis/trade-quality/stream stock=%s buy_date=%s",
        req.stock_id,
        req.buy_date,
    )

    # Pre-flight：market 未開盤直接走單一 event stream（與非 stream 行為一致）
    if req.buy_date and _is_market_not_open_yet(req.buy_date):
        stock = db.get(StockMaster, req.stock_id)
        if not stock:
            raise HTTPException(status_code=404, detail=f"Stock not found: {req.stock_id}")
        early_response = _build_market_not_open_response(stock, req.buy_date)

        def market_closed_stream():
            yield _emit("done", "完成", payload=jsonable_encoder(early_response))

        return StreamingResponse(market_closed_stream(), media_type="application/x-ndjson")

    resolved = _resolve_buy_date(db, req.buy_date)
    if not resolved:
        raise HTTPException(status_code=404, detail="資料庫無交易日資料")

    # Pre-flight：stock 找不到 / prompt 缺檔，raise 走 HTTP 4xx/5xx，前端可正確錯誤處理
    stock_check = db.get(StockMaster, req.stock_id)
    if not stock_check:
        raise HTTPException(status_code=404, detail=f"Stock not found: {req.stock_id}")

    system_prompt = _load_system_prompt()
    if not system_prompt:
        raise HTTPException(status_code=500, detail="分析 prompt 檔案缺失")

    def generate():
        try:
            yield _emit("collect_raw", "正在跟後台要資料")
            stock, context, warnings = _collect_context(db, req.stock_id, resolved)

            yield _emit("build_context", "組建分析訊號中")
            m21_context = _build_deterministic_context(db, req.stock_id, resolved, warnings)

            yield _emit("openai_call", "AI 分析中（依 prompt 推論評級與目標價）")
            user_msg = _build_user_message(context, m21_context, warnings)
            payload = _call_openai(system_prompt, user_msg)

            if payload is None:
                fallback = TradeQualityResponse(
                    stock_id=stock.stock_id,
                    stock_name=stock.stock_name,
                    buy_date=str(resolved),
                    rating="WATCH",
                    rating_label=RATING_LABELS["WATCH"],
                    summary="AI 分析服務目前不可用（OpenAI 未設定或呼叫失敗），無法建立有效交易判斷。",
                    report_markdown="⚠️ 無法連線 AI 分析服務，請稍後再試或檢查 OpenAI 設定。",
                    warnings=warnings + ["OpenAI 服務不可用"],
                    source="unavailable",
                )
                yield _emit("done", "完成", payload=jsonable_encoder(fallback))
                return

            response = _normalize_response(payload, stock, resolved, warnings, source="openai")
            yield _emit("done", "完成", payload=jsonable_encoder(response))
        except HTTPException:
            # 已在 pre-flight 處理過；generator 內若仍 raise 視為例外路徑
            raise
        except Exception:
            logger.exception("trade-quality stream failed during generation")
            yield _emit(
                "error",
                "分析過程發生錯誤",
                payload={"detail": "分析過程發生錯誤，請稍後再試"},
            )

    return StreamingResponse(generate(), media_type="application/x-ndjson")


# ── Context endpoint（M21）──────────────────────────────────────────────────
#
# GET /api/analysis/context
#   Pre-aggregated 6-section JSON for trade quality AI.
#   Deterministic, no hindsight, 需要登入。
#   spec: docs/plans/trade_quality_context_spec.md
#   impl: docs/plans/m21_context_pipeline_implementation.md
@router.get("/analysis/context")
def get_trade_quality_context(
    stock_id: str,
    buy_date: Optional[date] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    resolved = _resolve_buy_date(db, buy_date)
    if resolved is None:
        raise HTTPException(status_code=404, detail="資料庫無交易日資料")
    try:
        return build_trade_quality_context(db, stock_id, resolved)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
