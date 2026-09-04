"""
M27 Market Regime v2 — Market Stress Overlay 原始市場指標 ETL

抓取 Family B（台灣資金／衍生品）與 Family C/D（全球風險／總體商品）需要的
raw values，寫進 `market_stress_indicators`（一天一筆，寬表）。

Family A（LOCAL_MARKET_INTERNALS）沿用既有 `app/signals/market_breadth.py`
（從 momentum frame 算），外資現貨流向沿用既有 `inst_stock_flow`（已存在）——
這兩者不需要新 ETL，本模組只補真正缺的部分。

資料源查證（docs/signals/market_regime_v2_data_audit.md）：
- 外資臺指期 OI：TaiwanFuturesInstitutionalInvestors（data_id=TX）
- TXO 買賣權：TaiwanOptionInstitutionalInvestors（data_id=TXO，僅三大法人加總，
  非全市場含散戶——已在欄位 docstring／audit 文件說明這個範圍限制）
- 美國 VIX / Nasdaq / SOX：USStockPrice（data_id=^VIX / ^IXIC / ^SOX）
- WTI / Brent：CrudeOilPrices（data_id=WTI / Brent）
- 黃金：GoldPrice（無 data_id，dataset-level 全球單一序列）
- USD/TWD：TaiwanExchangeRate（data_id=USD）
- 台灣 VIX、美國 10 年期公債殖利率：FinMind **沒有**對應 dataset，
  結構性缺席，永久 NULL（不是抓取失敗）
"""

import logging
from datetime import date
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _to_date_map(df: Optional[pd.DataFrame], value_col: str) -> Dict[date, float]:
    """單一數值欄位（每個 trade_date 唯一一筆）轉成 {trade_date: value}。"""
    out: Dict[date, float] = {}
    if df is None or df.empty or value_col not in df.columns:
        return out
    for _, row in df.iterrows():
        d = pd.to_datetime(row["date"]).date()
        v = row.get(value_col)
        if v is not None and pd.notna(v):
            out[d] = float(v)
    return out


def _foreign_tx_oi_map(df: Optional[pd.DataFrame]) -> Dict[date, tuple]:
    """外資臺指期未平倉：篩 institutional_investors=='外資'，
    回 {trade_date: (long_oi, short_oi)}（同日多筆用 sum 保護）。
    """
    out: Dict[date, tuple] = {}
    if df is None or df.empty or "institutional_investors" not in df.columns:
        return out
    foreign = df[df["institutional_investors"] == "外資"]
    grouped = foreign.groupby("date").agg(
        long_oi=("long_open_interest_balance_volume", "sum"),
        short_oi=("short_open_interest_balance_volume", "sum"),
    )
    for d_str, row in grouped.iterrows():
        d = pd.to_datetime(d_str).date()
        out[d] = (float(row["long_oi"]), float(row["short_oi"]))
    return out


def _txo_activity_map(df: Optional[pd.DataFrame]) -> Dict[date, Dict[str, float]]:
    """TXO 買賣權法人成交量／未平倉量：三大法人身份加總（自營商/投信/外資），
    分 put/call，回 {trade_date: {put_volume, call_volume, put_oi, call_oi}}。

    「volume」= 法人 long_deal_volume + short_deal_volume（法人雙向交易活動量，
    非全市場含散戶成交量）；「oi」同理用 long+short 未平倉量加總。這是本輪
    唯一能取得的 TXO 資料（無全市場逐筆資料），刻意標明是法人口徑的活動量，
    不等同 TAIFEX 官方公告的全市場 Put/Call Ratio。
    """
    out: Dict[date, Dict[str, float]] = {}
    if df is None or df.empty or "call_put" not in df.columns:
        return out
    df = df.copy()
    df["volume_total"] = pd.to_numeric(
        df.get("long_deal_volume"), errors="coerce"
    ).fillna(0) + pd.to_numeric(df.get("short_deal_volume"), errors="coerce").fillna(0)
    df["oi_total"] = pd.to_numeric(
        df.get("long_open_interest_balance_volume"), errors="coerce"
    ).fillna(0) + pd.to_numeric(
        df.get("short_open_interest_balance_volume"), errors="coerce"
    ).fillna(0)
    grouped = df.groupby(["date", "call_put"]).agg(
        volume=("volume_total", "sum"), oi=("oi_total", "sum")
    )
    for (d_str, call_put), row in grouped.iterrows():
        d = pd.to_datetime(d_str).date()
        bucket = out.setdefault(
            d, {"put_volume": 0.0, "call_volume": 0.0, "put_oi": 0.0, "call_oi": 0.0}
        )
        key = "put" if "put" in str(call_put).lower() or "賣" in str(call_put) else "call"
        bucket[f"{key}_volume"] += float(row["volume"])
        bucket[f"{key}_oi"] += float(row["oi"])
    return out


def fetch_and_upsert_market_stress_indicators(
    db: Session,
    start_date: date,
    end_date: date,
    client: Any,  # FinMindSDKClient
) -> Dict[str, Any]:
    """抓取 Family B/C/D 所有指標，per-trade_date 合併後 bulk upsert 進
    `market_stress_indicators`。任一子資料源失敗只記 warning、該欄位留 NULL，
    不讓單一資料源掛掉拖垮整個 ETL（`market_stress.py` 的資料缺失政策本來就
    要處理缺值，不需要在這裡假裝全部成功）。
    """
    result: Dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "upserted": 0,
        "status": "ok",
        "field_status": {},
    }
    s = start_date.strftime("%Y-%m-%d")
    e = end_date.strftime("%Y-%m-%d")

    foreign_tx: Dict[date, tuple] = {}
    txo: Dict[date, Dict[str, float]] = {}
    us_vix: Dict[date, float] = {}
    nasdaq: Dict[date, float] = {}
    sox: Dict[date, float] = {}
    wti: Dict[date, float] = {}
    brent: Dict[date, float] = {}
    gold: Dict[date, float] = {}
    usdtwd: Dict[date, float] = {}

    def _try(name: str, fn):
        try:
            data = fn()
            result["field_status"][name] = "ok"
            return data
        except Exception as exc:  # noqa: BLE001 — 單一子來源失敗不可拖垮整批
            logger.warning("market_stress_indicators: %s fetch failed: %s", name, exc)
            result["field_status"][name] = f"error: {exc}"
            return None

    df = _try("foreign_tx_oi", lambda: client.fetch_taiwan_futures_institutional_dataset(s, e))
    foreign_tx = _foreign_tx_oi_map(df)

    df = _try("txo_activity", lambda: client.fetch_taiwan_option_institutional_dataset(s, e))
    txo = _txo_activity_map(df)

    df = _try("us_vix", lambda: client.fetch_us_stock_price_dataset(s, e, "^VIX"))
    us_vix = _to_date_map(df, "Close")

    df = _try("nasdaq", lambda: client.fetch_us_stock_price_dataset(s, e, "^IXIC"))
    nasdaq = _to_date_map(df, "Close")

    df = _try("sox", lambda: client.fetch_us_stock_price_dataset(s, e, "^SOX"))
    sox = _to_date_map(df, "Close")

    df = _try("wti", lambda: client.fetch_crude_oil_dataset(s, e, "WTI"))
    wti = _to_date_map(df, "price")

    df = _try("brent", lambda: client.fetch_crude_oil_dataset(s, e, "Brent"))
    brent = _to_date_map(df, "price")

    df = _try("gold", lambda: client.fetch_gold_price_dataset(s, e))
    gold = _to_date_map(df, "Price")

    df = _try("usdtwd", lambda: client.fetch_exchange_rate_dataset(s, e, "USD"))
    if df is not None and not df.empty:
        df = df.copy()
        df["spot_mid"] = (
            pd.to_numeric(df.get("spot_buy"), errors="coerce")
            + pd.to_numeric(df.get("spot_sell"), errors="coerce")
        ) / 2.0
        usdtwd = _to_date_map(df, "spot_mid")

    all_dates = set()
    for m in (foreign_tx, txo, us_vix, nasdaq, sox, wti, brent, gold, usdtwd):
        all_dates.update(m.keys())

    if not all_dates:
        result["status"] = "no_data"
        return result

    records = []
    for d in sorted(all_dates):
        long_oi, short_oi = foreign_tx.get(d, (None, None))
        net_oi = (
            long_oi - short_oi if long_oi is not None and short_oi is not None else None
        )
        t = txo.get(d, {})
        records.append(
            {
                "trade_date": d,
                "foreign_tx_long_oi": long_oi,
                "foreign_tx_short_oi": short_oi,
                "foreign_tx_net_oi": net_oi,
                "txo_put_volume": t.get("put_volume"),
                "txo_call_volume": t.get("call_volume"),
                "txo_put_oi": t.get("put_oi"),
                "txo_call_oi": t.get("call_oi"),
                "us_vix_close": us_vix.get(d),
                "nasdaq_close": nasdaq.get(d),
                "sox_close": sox.get(d),
                "wti_price": wti.get(d),
                "brent_price": brent.get(d),
                "gold_price": gold.get(d),
                "usdtwd_spot": usdtwd.get(d),
                "source": None,
            }
        )

    for r in records:
        db.execute(
            text(
                """
                INSERT INTO market_stress_indicators (
                    trade_date, foreign_tx_long_oi, foreign_tx_short_oi,
                    foreign_tx_net_oi, txo_put_volume, txo_call_volume,
                    txo_put_oi, txo_call_oi, us_vix_close, nasdaq_close,
                    sox_close, wti_price, brent_price, gold_price,
                    usdtwd_spot, ingested_at
                ) VALUES (
                    :trade_date, :foreign_tx_long_oi, :foreign_tx_short_oi,
                    :foreign_tx_net_oi, :txo_put_volume, :txo_call_volume,
                    :txo_put_oi, :txo_call_oi, :us_vix_close, :nasdaq_close,
                    :sox_close, :wti_price, :brent_price, :gold_price,
                    :usdtwd_spot, CURRENT_TIMESTAMP
                )
                ON CONFLICT (trade_date) DO UPDATE SET
                    foreign_tx_long_oi = COALESCE(EXCLUDED.foreign_tx_long_oi, market_stress_indicators.foreign_tx_long_oi),
                    foreign_tx_short_oi = COALESCE(EXCLUDED.foreign_tx_short_oi, market_stress_indicators.foreign_tx_short_oi),
                    foreign_tx_net_oi = COALESCE(EXCLUDED.foreign_tx_net_oi, market_stress_indicators.foreign_tx_net_oi),
                    txo_put_volume = COALESCE(EXCLUDED.txo_put_volume, market_stress_indicators.txo_put_volume),
                    txo_call_volume = COALESCE(EXCLUDED.txo_call_volume, market_stress_indicators.txo_call_volume),
                    txo_put_oi = COALESCE(EXCLUDED.txo_put_oi, market_stress_indicators.txo_put_oi),
                    txo_call_oi = COALESCE(EXCLUDED.txo_call_oi, market_stress_indicators.txo_call_oi),
                    us_vix_close = COALESCE(EXCLUDED.us_vix_close, market_stress_indicators.us_vix_close),
                    nasdaq_close = COALESCE(EXCLUDED.nasdaq_close, market_stress_indicators.nasdaq_close),
                    sox_close = COALESCE(EXCLUDED.sox_close, market_stress_indicators.sox_close),
                    wti_price = COALESCE(EXCLUDED.wti_price, market_stress_indicators.wti_price),
                    brent_price = COALESCE(EXCLUDED.brent_price, market_stress_indicators.brent_price),
                    gold_price = COALESCE(EXCLUDED.gold_price, market_stress_indicators.gold_price),
                    usdtwd_spot = COALESCE(EXCLUDED.usdtwd_spot, market_stress_indicators.usdtwd_spot),
                    ingested_at = CURRENT_TIMESTAMP
                """
            ),
            r,
        )
    db.commit()
    result["upserted"] = len(records)
    logger.info("market_stress_indicators ETL upserted %d rows", len(records))
    return result
