"""
M23 Step 1~5：DB ingest、產業/個股 ranking、候選池建立 + 擴散。

Slice 5：deterministic 邏輯實作完成。

對應 spec：
  - §5 Step 1～5
  - §6 Candidate Pool 設計
"""
from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.hot_money_service import compute_hot_money, get_recent_trade_dates
from app.industry_flow_service import load_industry_flow_rows_for_dates
from app.models import (
    DailyPrice,
    InstStockFlow,
    MarginTrade,
    SignalWatchHit,
    StockMaster,
)
from app.signals import momentum
from app.signals.exclusions import (
    find_group_for_stock,
    get_group_members,
    is_blacklisted,
    is_etf,
    is_financial,
)

# LLM v6 contract（2026-07-22）：asset_type 三分類，供 LLM research 決定用哪一套
# 研究流程（公司業務 vs ETF 曝險），本身**不可**成為 REMOVE / Hard Exclusion / 弱勢
# 判斷的理由——只用於 research 模式選擇、UI 顯示、feature-missing 語意判斷。
ASSET_TYPE_COMMON_STOCK = "COMMON_STOCK"
ASSET_TYPE_FINANCIAL = "FINANCIAL"
ASSET_TYPE_ETF = "ETF"


def _resolve_asset_type(stock_id: str, stock_name: Optional[str], industry_name: Optional[str]) -> str:
    if is_etf(stock_id, stock_name):
        return ASSET_TYPE_ETF
    if is_financial(industry_name):
        return ASSET_TYPE_FINANCIAL
    return ASSET_TYPE_COMMON_STOCK

# Spec §再偵測閘門（2026-05-26）：首次抓到後驗證失敗的閾值
# 與 archive.py 的兩條 early-exit（-30% / drawdown 30%）是不同層級的早期警示
# 這條更早觸發（3 個交易日內未驗證主升段），但只用來「不再進入新候選池」，不主動結算 cycle
TRACKING_FAILED_DAYS_THRESHOLD = 3
TRACKING_FAILED_MAX_POSITIVE_PCT = 3.0
TRACKING_FAILED_MAX_NEGATIVE_PCT = -6.0

# v2.2 episode 統計（fishtail momentum upgrade spec §7.4）：
# 兩次命中之間「未命中的交易日數」>= 5 → 視為新的獨立 episode；<= 3 同一 episode；
# 4 天為模糊帶，依 spec「至少 5 個交易日未命中才算新」歸為同一 episode。
EPISODE_NEW_GAP_TRADE_DAYS = 5

logger = logging.getLogger(__name__)

_INST_TYPES = ("foreign", "trust", "dealer")

# Spec §5 Step 1 / Step 2
# 進池排序窗（2026-06-08）：個股 + 產業排行從 3 日縮為 2 日搶反應速度（更早抓到主線啟動）。
# 下游 classification / metrics 仍用 3d/5d 評強度，當日賣超煞車維持 1 日。
# 注意：rankings dict key 與 candidate flag 沿用 `_3d` 歷史命名（避免下游連動改名），
#       實際窗已是 RANKING_WINDOW_DAYS；語義以本常數為準。
RANKING_WINDOW_DAYS = 2
# 產業：N 日法人買超前 10 大「非金融」產業（金融類順延），再剔除「當日賣超前 10」（2026-06-05 改版）
TOP_INDUSTRIES_LIMIT = 10
TODAY_SELL_BLACKLIST_LIMIT = 10  # 當日（1 日）淨額最賣超的前 N 產業，落在此名單的產業剔除
TOP_STOCKS_LIMIT = 30
TOP_STOCKS_INNER = 6  # spec §6 group expansion 取 top 6

# ---------- Step 1：ingest ----------


def ingest_data(db: Session, target_date: date) -> Dict[str, Any]:
    """讀取 DB 原始資料的窗口元資料（spec §5 Step 1）。

    回傳：
      {
        "target_date": date,
        "trade_dates_3d": [...],   # 由舊到新（最舊→最新）
        "trade_dates_5d": [...],
        "trade_dates_10d": [...],
        "trade_dates_60d": [...],
        "stocks_master": {stock_id: StockMaster, ...} (僅 is_active=True)
      }
    """
    trade_dates_60d = get_recent_trade_dates(db, target_date, 60)
    if not trade_dates_60d:
        return {
            "target_date": target_date,
            "trade_dates_2d": [],
            "trade_dates_3d": [],
            "trade_dates_5d": [],
            "trade_dates_10d": [],
            "trade_dates_60d": [],
            "stocks_master": {},
        }
    trade_dates_5d = trade_dates_60d[-5:]
    trade_dates_3d = trade_dates_60d[-3:]
    trade_dates_2d = trade_dates_60d[-RANKING_WINDOW_DAYS:]
    trade_dates_10d = trade_dates_60d[-10:]

    masters = (
        db.query(StockMaster)
        .filter(StockMaster.is_active.is_(True))
        .all()
    )

    return {
        "target_date": target_date,
        "trade_dates_2d": trade_dates_2d,
        "trade_dates_3d": trade_dates_3d,
        "trade_dates_5d": trade_dates_5d,
        "trade_dates_10d": trade_dates_10d,
        "trade_dates_60d": trade_dates_60d,
        "stocks_master": {m.stock_id: m for m in masters},
    }


# ---------- Step 2/3：rankings ----------


def compute_rankings(
    db: Session,
    target_date: date,
    ingestion: Dict[str, Any],
) -> Dict[str, Any]:
    """spec §5 Step 1 / Step 2：產業排行 + 個股熱錢前 30（排序窗 = RANKING_WINDOW_DAYS）。

    產業規則（2026-06-05 改版，2026-06-08 排序窗 3→2 日）：
      1. 全市場各產業以「N 日（RANKING_WINDOW_DAYS）」法人淨買超排序
      2. 由高往低取，遇金融類產業跳過順延，湊滿 TOP_INDUSTRIES_LIMIT 個非金融產業
      3. 另算「當日（1 日）」各產業淨額，最賣超的前 TODAY_SELL_BLACKLIST_LIMIT 個產業為黑名單
      4. 步驟 2 結果落在黑名單者剔除（不再回補，剩幾個算幾個）
    """
    trade_dates_rank = ingestion.get("trade_dates_2d") or []
    masters: Dict[str, StockMaster] = ingestion.get("stocks_master") or {}

    if not trade_dates_rank:
        return {"top_industries_3d": [], "top_stocks_3d": []}

    # 產業 N 日累計淨買超（排序窗 = RANKING_WINDOW_DAYS；從 industry_daily_flow 聚合，已含三大法人總額）
    industry_totals_rank: Dict[str, float] = {}
    for row in load_industry_flow_rows_for_dates(db, trade_dates_rank):
        industry_totals_rank[row.industry_name] = (
            industry_totals_rank.get(row.industry_name, 0.0) + float(row.total_net_amount or 0.0)
        )

    # 當日（1 日）各產業淨額 → 取最賣超的前 N 個產業為黑名單（煞車維持 1 日）
    today = trade_dates_rank[-1]
    industry_totals_1d: Dict[str, float] = {}
    for row in load_industry_flow_rows_for_dates(db, [today]):
        industry_totals_1d[row.industry_name] = (
            industry_totals_1d.get(row.industry_name, 0.0) + float(row.total_net_amount or 0.0)
        )
    # 只有「當日真的淨賣超（net < 0）」的產業才可能進黑名單；買超產業永遠不算賣超。
    # 由最賣超往上取前 N；若當日淨賣超產業不足 N 個，黑名單就只有那幾個。
    today_sell_blacklist: Set[str] = {
        ind
        for ind, net in sorted(industry_totals_1d.items(), key=lambda item: item[1])[
            :TODAY_SELL_BLACKLIST_LIMIT
        ]
        if net < 0
    }

    # 由高往低取，遇金融類順延，湊滿 TOP_INDUSTRIES_LIMIT 個非金融產業
    selected: List[Tuple[str, float]] = []
    for ind, net in sorted(industry_totals_rank.items(), key=lambda item: item[1], reverse=True):
        if is_financial(ind):
            continue
        selected.append((ind, net))
        if len(selected) >= TOP_INDUSTRIES_LIMIT:
            break

    # 剔除當日賣超前 N 的產業（不再回補）
    industry_rows = [(ind, net) for ind, net in selected if ind not in today_sell_blacklist]

    industry_counts: Dict[str, int] = {}
    for master in masters.values():
        if master.industry_name:
            industry_counts[master.industry_name] = (
                industry_counts.get(master.industry_name, 0) + 1
            )

    top_industries = [
        {
            "industry_name": ind,
            "net_3d": float(net or 0.0),
            "stock_count": industry_counts.get(ind, 0),
            "rank": idx,
        }
        for idx, (ind, net) in enumerate(industry_rows, start=1)
    ]

    # 個股排行：沿用 M22 hot_money_service（排序窗 = RANKING_WINDOW_DAYS）
    hot_result = compute_hot_money(db, target_date, days=RANKING_WINDOW_DAYS, limit=TOP_STOCKS_LIMIT)
    top_stocks = [
        {
            "stock_id": item.stock_id,
            "name": item.stock_name,
            "industry": item.industry_name,
            "sub_industry": item.sub_industry,
            "net_3d": float(item.total_net_amount or 0.0),
            "price_change_3d": item.price_change_pct,
            "rank": item.rank,
        }
        for item in hot_result.items
    ]

    return {
        "top_industries_3d": top_industries,
        "top_stocks_3d": top_stocks,
    }


# ---------- Step 4/5：candidate pool ----------


def build_candidate_pool(
    db: Session,
    target_date: date,
    ingestion: Dict[str, Any],
    rankings: Dict[str, Any],
    momentum_frame: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """spec §5 Step 4 + §6：候選池組合 + 擴散 + deterministic ordering。

    v2.1（fishtail momentum upgrade）：候選池從單一「法人資金」通道升級為多通道聯集：
      A. 法人資金（既有：熱錢前 30 + 前 10 非金融產業成分股 + 集團擴散）
      B. 價格動能（rs_market_percentile_20d / 產業內 RS / 創 20 日新高帶量 / 60d 報酬前段）
      C. 動能加速（rs_rank_improvement_5d >= 200 且 rs_market >= 70）
      D. 基本面動能（2026-07-15 第二輪上線：月營收 yoy 加速 / 轉正 / 產業內前 20%；
         用 `momentum.revenue_available_date`（次月 10 日）當可用日 gate，無資料穿越）
    每檔候選 merge momentum frame 特徵並算出 deterministic `momentum_score`。
    """
    masters: Dict[str, StockMaster] = ingestion.get("stocks_master") or {}
    top_industries = rankings.get("top_industries_3d") or []
    top_stocks = rankings.get("top_stocks_3d") or []

    if not masters or (not top_industries and not top_stocks):
        return []

    # v2.1：全市場動能特徵 frame（B/C 通道選股 + 每檔 enrich 都要用）。
    # v2.2：pipeline 會預先算好傳入（與 market_breadth 共用同一次全市場 query）；
    # 未傳入（測試 / 舊 caller）→ 內部自算，行為不變。
    if momentum_frame is None:
        momentum_frame = momentum.compute_market_momentum_frame(db, target_date, masters)
    channels = momentum.select_momentum_candidates(momentum_frame)
    price_momentum_ids = set(channels.get("price_momentum") or [])
    acceleration_ids = set(channels.get("acceleration") or [])
    fundamental_ids = set(channels.get("fundamental") or [])

    # 1. 收集候選 stock_id（聯集）。A/B/C/D 是來源資訊，不是淘汰理由。
    source_a_ids: Set[str] = set()

    # 1a. top_stocks_3d 前 30
    for s in top_stocks:
        source_a_ids.add(s["stock_id"])

    # 1b. top_industries_3d 前 10 的所有成分股
    industry_set = {ind["industry_name"] for ind in top_industries}
    for sid, master in masters.items():
        if master.industry_name in industry_set:
            source_a_ids.add(sid)

    # 1c. top_stocks_3d 前 6 的同集團（spec §6）
    for s in top_stocks[:TOP_STOCKS_INNER]:
        group_name = find_group_for_stock(s["stock_id"])
        if not group_name:
            continue
        for member_id in get_group_members(group_name):
            source_a_ids.add(member_id)

    # 1d. v2.1 B/C/D 通道（frame universe 已排除 ETF / 金融，這裡直接聯集）
    candidate_ids = (
        source_a_ids
        | price_momentum_ids
        | acceleration_ids
        | fundamental_ids
    )

    # 2. 排除人工黑名單 / 不在 stocks_master 的（無業務面資料）
    #
    # 2026-07-22（LLM v6 contract 對齊）：ETF / 金融股**不再**在這裡被排除。
    # Phase 2 hard exclusion（2026-07-22 第一輪重構）已經確認資產類型不該是
    # 排除理由，只有真正的失效條件才是；若候選池 Step 1 這裡還是把 ETF/金融
    # 濾掉，等於矛盾——「hard exclusion 說可以進，candidate pool 卻連門都不讓
    # 進」。人工黑名單仍然排除。
    #
    # legacy 路徑不受影響：`filters._is_hard_excluded` 自己也獨立呼叫
    # `exclusions.should_exclude()`（含 ETF/金融判斷）作為它自己的 rule #1，
    # 所以 ETF/金融就算進了候選池，legacy 最終輸出仍會在 hard exclusion 那關
    # 被擋下，legacy 行為零改變。
    filtered_ids: List[str] = []
    for sid in candidate_ids:
        master = masters.get(sid)
        if master is None:
            continue
        if is_blacklisted(sid):
            continue
        filtered_ids.append(sid)

    if not filtered_ids:
        return []

    # 3. 計算每檔的 metrics（一次性 query，不 per-stock）
    metrics = _compute_pool_metrics(db, ingestion, filtered_ids)

    # 3b. 追蹤狀態：join signal_watch_hits 抓「首次抓到後的驗證表現」
    tracking_by_stock = _load_tracking_status(db, filtered_ids, target_date)

    # 3c. 產業層級 1d / 3d 資金流（v5 deterministic_signals 的 sector_rotation_status 用）
    industry_flow_totals = _load_industry_flow_totals(db, ingestion)

    # 4. 為每檔 enrich 集團 / industry / top flag
    top_stock_id_set = {s["stock_id"] for s in top_stocks}

    candidates: List[Dict[str, Any]] = []
    for sid in filtered_ids:
        master = masters.get(sid)
        if master is None:
            continue
        m = metrics.get(sid) or _empty_metrics()
        ts = tracking_by_stock.get(sid) or _empty_tracking_status()
        mf = momentum_frame.get(sid) or momentum.empty_momentum_features()
        in_top = master.industry_name in industry_set
        asset_type = _resolve_asset_type(sid, master.stock_name, master.industry_name)
        source_flags = {
            "source_A": sid in source_a_ids,
            "source_B": sid in price_momentum_ids,
            "source_C": sid in acceleration_ids,
            "source_D": sid in fundamental_ids,
        }
        candidate = {
            "stock_id": sid,
            "name": master.stock_name,
            "industry": master.industry_name,
            "sub_industry": master.sub_industry,
            "asset_type": asset_type,
            # is_etf / is_financial 保留給既有 caller 相容（legacy filters._is_hard_excluded
            # 仍用 exclusions.should_exclude() 自己重新判斷，不依賴這兩個欄位）
            "is_etf": asset_type == ASSET_TYPE_ETF,
            "is_financial": asset_type == ASSET_TYPE_FINANCIAL,
            "group_name": find_group_for_stock(sid),
            "in_top_industries_3d": in_top,
            "in_top_stocks_3d": sid in top_stock_id_set,
            "in_price_momentum_pool": sid in price_momentum_ids,
            "in_acceleration_pool": sid in acceleration_ids,
            "in_fundamental_pool": sid in fundamental_ids,
            **source_flags,
            "candidate_sources": [
                source
                for source in ("A", "B", "C", "D")
                if source_flags[f"source_{source}"]
            ],
            **m,
            **{k: v for k, v in mf.items() if not k.startswith("_")},
            **ts,
            **(industry_flow_totals.get(_normalized_industry(master.industry_name)) or
               {"industry_flow_1d": None, "industry_flow_3d": None}),
        }
        # v2.1 momentum_score：需要 frame 特徵 + pool metrics（OHLC / 連買日 / 量比）都到齊後才算
        candidate.update(momentum.compute_momentum_score(candidate))
        # v5：組 momentum_signals nested dict（LLM evidence view 直接採用）+ flat grade/phase
        candidate["momentum_signals"] = momentum.build_momentum_signals(candidate)
        candidate["momentum_grade"] = candidate["momentum_signals"]["momentum_grade"]
        candidate["momentum_phase"] = candidate["momentum_signals"]["momentum_phase"]
        candidates.append(candidate)

    # 5. 算「該產業內」price_change_5d / net_3d 排名
    _attach_industry_rankings(candidates)

    # 6. 僅排序、不截斷。排序決定 processing/debug/snapshot 順序，不影響 eligibility。
    candidates.sort(
        key=lambda c: (
            -(c.get("momentum_score") or 0.0),
            -(c.get("total_institution_flow_3d") or 0.0),
            str(c.get("stock_id") or ""),
        )
    )

    return candidates


# ---------- helpers：產業資金流（sector rotation 用） ----------


def _normalized_industry(industry_name: Optional[str]) -> Optional[str]:
    """industry_daily_flow 的產業名經 canonicalize（「半導體業」→「半導體」），
    stocks_master 是原始名；比對前先 normalize。"""
    try:
        from app.industry_names import normalize_industry_name

        return normalize_industry_name(industry_name)
    except Exception:
        return industry_name


def _load_industry_flow_totals(
    db: Session,
    ingestion: Dict[str, Any],
) -> Dict[str, Dict[str, Optional[float]]]:
    """{canonical 產業名: {industry_flow_1d, industry_flow_3d}}（三大法人合計淨額）。"""
    trade_dates_3d: List[date] = ingestion.get("trade_dates_3d") or []
    if not trade_dates_3d:
        return {}
    last_day = trade_dates_3d[-1]
    totals: Dict[str, Dict[str, Optional[float]]] = {}
    for row in load_industry_flow_rows_for_dates(db, trade_dates_3d):
        bucket = totals.setdefault(
            row.industry_name, {"industry_flow_1d": 0.0, "industry_flow_3d": 0.0}
        )
        amt = float(row.total_net_amount or 0.0)
        bucket["industry_flow_3d"] = (bucket["industry_flow_3d"] or 0.0) + amt
        if row.trade_date == last_day:
            bucket["industry_flow_1d"] = (bucket["industry_flow_1d"] or 0.0) + amt
    return totals


# ---------- helpers：tracking_status（再偵測閘門用） ----------


def _empty_tracking_status() -> Dict[str, Any]:
    """無歷史命中的股票預設值（首次出現在候選池）。"""
    return {
        "is_tracked": False,
        "first_seen_date": None,
        "days_since_first_seen": None,
        "hit_count": None,
        "consecutive_hit_count": None,
        "independent_hit_count": None,
        "max_positive_return_pct": None,
        "max_negative_return_pct": None,
        "failed_follow_through": False,
    }


def _load_tracking_status(
    db: Session,
    stock_ids: Sequence[str],
    target_date: date,
) -> Dict[str, Dict[str, Any]]:
    """讀 `signal_watch_hits` 算每檔 active tracking 的驗證表現。

    重點欄位：
      - first_seen_date：MIN(snapshot_date)，該檔在當前 cycle 內第一次被抓到的日子
      - days_since_first_seen：first_seen_date 之後（不含當天）到 target_date 為止的交易日數
        - first_seen_date == target_date → 0（首日，尚未進入驗證期）
        - target_date 為 first_seen_date 後第 1 個交易日 → 1
      - max_positive_return_pct / max_negative_return_pct：archive cron 每天更新後的當前 cycle 累計值
      - hit_count：同 stock_id 命中過幾個 snapshot_date（DISTINCT）
      - failed_follow_through：days >= 3 AND max_pos < +3% AND max_neg < -6%（spec §再偵測閘門）

    Note: 若該 stock 已被 archive 早退（hits 已被刪），此函式不會看到資料，回 empty_tracking_status。
          這是刻意設計：早退結算（機制 A）是另一條獨立路徑，與此處的「再偵測閘門」分工明確。
    """
    if not stock_ids:
        return {}

    hit_rows = (
        db.query(
            SignalWatchHit.stock_id,
            SignalWatchHit.snapshot_date,
            SignalWatchHit.max_positive_return_pct,
            SignalWatchHit.max_negative_return_pct,
        )
        .filter(SignalWatchHit.stock_id.in_(list(stock_ids)))
        .all()
    )
    if not hit_rows:
        return {}

    by_stock: Dict[str, List[Any]] = {}
    for row in hit_rows:
        by_stock.setdefault(row.stock_id, []).append(row)

    # 一次取出區間內所有交易日（用 daily_price.trade_date 為準），避免 N+1
    oldest_first_seen = min(
        min(r.snapshot_date for r in rows) for rows in by_stock.values()
    )
    trade_date_rows = (
        db.query(DailyPrice.trade_date)
        .filter(
            DailyPrice.trade_date >= oldest_first_seen,
            DailyPrice.trade_date <= target_date,
        )
        .distinct()
        .all()
    )
    trade_dates_sorted = sorted({d[0] for d in trade_date_rows})
    trade_index = {d: i for i, d in enumerate(trade_dates_sorted)}

    out: Dict[str, Dict[str, Any]] = {}
    for sid, rows in by_stock.items():
        first_seen = min(r.snapshot_date for r in rows)

        # archive cron 每天會把同 cycle 內每筆 hit 的 max_* 都同步更新成最新值，
        # 但保守起見用 max/min 聚合，避免某 row 因 partial update 殘留舊值
        pos_values = [r.max_positive_return_pct for r in rows if r.max_positive_return_pct is not None]
        neg_values = [r.max_negative_return_pct for r in rows if r.max_negative_return_pct is not None]
        max_pos = max(pos_values) if pos_values else None
        max_neg = min(neg_values) if neg_values else None
        hit_count = len({r.snapshot_date for r in rows})

        days_since = sum(1 for d in trade_dates_sorted if first_seen < d <= target_date)

        failed = (
            days_since >= TRACKING_FAILED_DAYS_THRESHOLD
            and max_pos is not None and max_pos < TRACKING_FAILED_MAX_POSITIVE_PCT
            and max_neg is not None and max_neg < TRACKING_FAILED_MAX_NEGATIVE_PCT
        )

        consecutive_hits, independent_episodes = _episode_counts(
            sorted({r.snapshot_date for r in rows}), trade_index
        )

        out[sid] = {
            "is_tracked": True,
            "first_seen_date": first_seen,
            "days_since_first_seen": days_since,
            "hit_count": hit_count,
            "consecutive_hit_count": consecutive_hits,
            "independent_hit_count": independent_episodes,
            "max_positive_return_pct": max_pos,
            "max_negative_return_pct": max_neg,
            "failed_follow_through": failed,
        }
    return out


def _episode_counts(
    hit_dates_sorted: List[date],
    trade_index: Dict[date, int],
) -> Tuple[int, int]:
    """(當前 episode 命中次數, 獨立 episode 總數)。

    v2.2 spec §7.4：兩次命中間「未命中交易日數」>= EPISODE_NEW_GAP_TRADE_DAYS →
    新 episode。命中日不在 trade_index（資料異常）時保守視為間隔 0（同 episode）。
    """
    if not hit_dates_sorted:
        return 0, 0
    episodes = 1
    current_len = 1
    for prev, cur in zip(hit_dates_sorted, hit_dates_sorted[1:]):
        pi = trade_index.get(prev)
        ci = trade_index.get(cur)
        gap = (ci - pi - 1) if (pi is not None and ci is not None) else 0
        if gap >= EPISODE_NEW_GAP_TRADE_DAYS:
            episodes += 1
            current_len = 1
        else:
            current_len += 1
    return current_len, episodes


# ---------- helpers：metrics 計算 ----------


def _empty_metrics() -> Dict[str, Any]:
    return {
        "price_change_1d": None,
        "price_change_3d": None,
        "price_change_5d": None,
        "price_change_10d": None,
        "volume_1d": None,
        "volume_5d_to_60d_ratio": None,
        "volume_1d_to_60d_ratio": None,
        "volume_1d_to_5d_ratio": None,
        "avg_turnover_5d": None,
        "open_1d": None,
        "high_1d": None,
        "low_1d": None,
        "close_1d": None,
        "high_10d": None,
        "low_10d": None,
        "ma_5d": None,
        "ma_10d": None,
        "foreign_flow_1d": 0.0,
        "foreign_flow_3d": 0.0,
        "foreign_flow_5d": 0.0,
        "trust_flow_1d": 0.0,
        "trust_flow_3d": 0.0,
        "trust_flow_5d": 0.0,
        "dealer_flow_1d": 0.0,
        "dealer_flow_3d": 0.0,
        "dealer_flow_5d": 0.0,
        "total_institution_flow_1d": 0.0,
        "total_institution_flow_3d": 0.0,
        "total_institution_flow_5d": 0.0,
        "consecutive_buy_days_3d": 0,
        "margin_change_1d": None,
        "margin_change_3d": None,
        "short_change_1d": None,
        "short_change_3d": None,
        # M23 2026-05-25：加細數據給 LLM margin_analysis 用（張數絕對值 + 券資比）
        # 與 *_change_1d / 3d（百分比變動率）並存，前者是「值」，後者是「率」。
        "margin_balance_shares": None,
        "margin_change_shares": None,
        "short_balance_shares": None,
        "short_change_shares": None,
        "margin_short_ratio_pct": None,
    }


def _compute_pool_metrics(
    db: Session,
    ingestion: Dict[str, Any],
    stock_ids: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    """一次性 query daily_price / inst_stock_flow / margin_trade，再 per-stock 計算。"""
    if not stock_ids:
        return {}

    trade_dates_3d: List[date] = ingestion.get("trade_dates_3d") or []
    trade_dates_5d: List[date] = ingestion.get("trade_dates_5d") or []
    trade_dates_10d: List[date] = ingestion.get("trade_dates_10d") or []
    trade_dates_60d: List[date] = ingestion.get("trade_dates_60d") or []
    if not trade_dates_5d:
        return {sid: _empty_metrics() for sid in stock_ids}

    # daily_price 60d
    price_rows = (
        db.query(DailyPrice)
        .filter(
            DailyPrice.trade_date.in_(trade_dates_60d),
            DailyPrice.stock_id.in_(list(stock_ids)),
        )
        .all()
    )
    by_stock: Dict[str, List[DailyPrice]] = {}
    for row in price_rows:
        by_stock.setdefault(row.stock_id, []).append(row)
    for sid in by_stock:
        by_stock[sid].sort(key=lambda r: r.trade_date)

    # inst_stock_flow 5d（涵蓋 1d/3d/5d）
    flow_by_stock = _aggregate_flow_by_day(db, trade_dates_5d, stock_ids)

    # margin_trade 5d
    margin_by_stock = _load_margin_by_day(db, trade_dates_5d, stock_ids)

    metrics: Dict[str, Dict[str, Any]] = {}
    target_date = trade_dates_5d[-1]
    for sid in stock_ids:
        rows = by_stock.get(sid, [])
        m = _build_stock_metrics(
            rows,
            flow_by_stock.get(sid, {}),
            margin_by_stock.get(sid, {}),
            target_date=target_date,
            trade_dates_3d=trade_dates_3d,
            trade_dates_5d=trade_dates_5d,
            trade_dates_10d=trade_dates_10d,
        )
        metrics[sid] = m
    return metrics


def _aggregate_flow_by_day(
    db: Session,
    trade_dates: Sequence[date],
    stock_ids: Sequence[str],
) -> Dict[str, Dict[date, Dict[str, float]]]:
    """`{stock_id: {trade_date: {foreign, trust, dealer, total}}}`"""
    if not trade_dates or not stock_ids:
        return {}
    rows = (
        db.query(
            InstStockFlow.stock_id,
            InstStockFlow.trade_date,
            InstStockFlow.inst_type,
            InstStockFlow.net_amount_est,
        )
        .filter(
            InstStockFlow.trade_date.in_(list(trade_dates)),
            InstStockFlow.stock_id.in_(list(stock_ids)),
            InstStockFlow.inst_type.in_(_INST_TYPES),
        )
        .all()
    )
    bucket: Dict[str, Dict[date, Dict[str, float]]] = {}
    for sid, td, inst, amt in rows:
        day = bucket.setdefault(sid, {}).setdefault(
            td, {"foreign": 0.0, "trust": 0.0, "dealer": 0.0, "total": 0.0}
        )
        v = float(amt or 0.0)
        day[inst] += v
        day["total"] += v
    return bucket


def _load_margin_by_day(
    db: Session,
    trade_dates: Sequence[date],
    stock_ids: Sequence[str],
) -> Dict[str, Dict[date, Dict[str, Optional[int]]]]:
    if not trade_dates or not stock_ids:
        return {}
    rows = (
        db.query(MarginTrade)
        .filter(
            MarginTrade.trade_date.in_(list(trade_dates)),
            MarginTrade.stock_id.in_(list(stock_ids)),
        )
        .all()
    )
    bucket: Dict[str, Dict[date, Dict[str, Optional[int]]]] = {}
    for row in rows:
        bucket.setdefault(row.stock_id, {})[row.trade_date] = {
            "margin_balance": row.margin_balance,
            "margin_change": row.margin_change,
            "short_balance": row.short_balance,
            "short_change": row.short_change,
        }
    return bucket


def _build_stock_metrics(
    price_rows: Sequence[DailyPrice],
    flow_by_day: Dict[date, Dict[str, float]],
    margin_by_day: Dict[date, Dict[str, Optional[int]]],
    *,
    target_date: date,
    trade_dates_3d: Sequence[date],
    trade_dates_5d: Sequence[date],
    trade_dates_10d: Sequence[date],
) -> Dict[str, Any]:
    out = _empty_metrics()
    if not price_rows:
        # 即使無 price，仍要算 flow（可能停牌但仍有法人資料）
        _fill_flow_metrics(out, flow_by_day, trade_dates_3d, trade_dates_5d)
        _fill_margin_metrics(out, margin_by_day, trade_dates_3d)
        return out

    # 索引：trade_date → row
    by_date = {r.trade_date: r for r in price_rows}
    last_row = price_rows[-1]
    out["open_1d"] = last_row.open_price
    out["high_1d"] = last_row.high_price
    out["low_1d"] = last_row.low_price
    out["close_1d"] = last_row.close_price
    out["volume_1d"] = last_row.volume  # 當日成交量（股數）；流動性死線用

    # price_change_*：close(target) / close(target-Nd 前一交易日) - 1
    out["price_change_1d"] = _pct_change(price_rows, n=1)
    out["price_change_3d"] = _pct_change(price_rows, n=3)
    out["price_change_5d"] = _pct_change(price_rows, n=5)
    out["price_change_10d"] = _pct_change(price_rows, n=10)

    # 5MA / 10MA
    closes = [r.close_price for r in price_rows if r.close_price is not None]
    if closes:
        out["ma_5d"] = sum(closes[-5:]) / min(5, len(closes))
        out["ma_10d"] = sum(closes[-10:]) / min(10, len(closes))

    # 60d / 5d / 1d 量能
    volumes = [r.volume for r in price_rows if r.volume is not None and r.volume > 0]
    if volumes:
        avg_60d = sum(volumes) / len(volumes)
        avg_5d = sum(volumes[-5:]) / min(5, len(volumes))
        last_vol = volumes[-1]
        if avg_60d > 0:
            out["volume_5d_to_60d_ratio"] = avg_5d / avg_60d
            out["volume_1d_to_60d_ratio"] = last_vol / avg_60d
        if avg_5d > 0:
            out["volume_1d_to_5d_ratio"] = last_vol / avg_5d

    # avg_turnover_5d（流動性 filter 用）
    turnovers = [r.turnover for r in price_rows[-5:] if r.turnover is not None]
    if turnovers:
        out["avg_turnover_5d"] = sum(turnovers) / len(turnovers)

    # high_10d / low_10d
    last_10 = [r for r in price_rows if r.trade_date in trade_dates_10d]
    if last_10:
        highs = [r.high_price for r in last_10 if r.high_price is not None]
        lows = [r.low_price for r in last_10 if r.low_price is not None]
        if highs:
            out["high_10d"] = max(highs)
        if lows:
            out["low_10d"] = min(lows)

    _fill_flow_metrics(out, flow_by_day, trade_dates_3d, trade_dates_5d)
    _fill_margin_metrics(out, margin_by_day, trade_dates_3d)
    return out


def _pct_change(price_rows: Sequence[DailyPrice], *, n: int) -> Optional[float]:
    """close(latest) / close(latest - n trade days) - 1，% 表示。"""
    if len(price_rows) < n + 1:
        return None
    cur = price_rows[-1].close_price
    prev = price_rows[-(n + 1)].close_price
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / prev * 100.0


def _fill_flow_metrics(
    out: Dict[str, Any],
    flow_by_day: Dict[date, Dict[str, float]],
    trade_dates_3d: Sequence[date],
    trade_dates_5d: Sequence[date],
) -> None:
    """法人 1d / 3d / 5d 累計 + consecutive_buy_days_3d。"""
    if not trade_dates_5d:
        return
    last_day = trade_dates_5d[-1]

    def _sum(window: Sequence[date], key: str) -> float:
        return sum(
            (flow_by_day.get(d, {}).get(key) or 0.0) for d in window
        )

    out["foreign_flow_1d"] = float(flow_by_day.get(last_day, {}).get("foreign", 0.0))
    out["trust_flow_1d"] = float(flow_by_day.get(last_day, {}).get("trust", 0.0))
    out["dealer_flow_1d"] = float(flow_by_day.get(last_day, {}).get("dealer", 0.0))
    out["total_institution_flow_1d"] = float(
        flow_by_day.get(last_day, {}).get("total", 0.0)
    )

    out["foreign_flow_3d"] = _sum(trade_dates_3d, "foreign")
    out["trust_flow_3d"] = _sum(trade_dates_3d, "trust")
    out["dealer_flow_3d"] = _sum(trade_dates_3d, "dealer")
    out["total_institution_flow_3d"] = _sum(trade_dates_3d, "total")

    out["foreign_flow_5d"] = _sum(trade_dates_5d, "foreign")
    out["trust_flow_5d"] = _sum(trade_dates_5d, "trust")
    out["dealer_flow_5d"] = _sum(trade_dates_5d, "dealer")
    out["total_institution_flow_5d"] = _sum(trade_dates_5d, "total")

    # 三大法人合計近 3 日連買日數（spec §7.1）
    out["consecutive_buy_days_3d"] = sum(
        1 for d in trade_dates_3d if (flow_by_day.get(d, {}).get("total", 0.0) or 0.0) > 0
    )


def _fill_margin_metrics(
    out: Dict[str, Any],
    margin_by_day: Dict[date, Dict[str, Optional[int]]],
    trade_dates_3d: Sequence[date],
) -> None:
    """spec §10.1 margin_change_1d / 3d 為「相對昨日餘額的變動率」。

    2026-05-25 加塞絕對張數 + 券資比，讓 LLM margin_analysis 能寫表格。
    """
    if not trade_dates_3d:
        return
    today = trade_dates_3d[-1]
    today_row = margin_by_day.get(today)

    if today_row:
        bal = today_row.get("margin_balance")
        chg = today_row.get("margin_change")
        s_bal = today_row.get("short_balance")
        s_chg = today_row.get("short_change")

        # 絕對值（無論變動率算不算得出）
        out["margin_balance_shares"] = bal
        out["margin_change_shares"] = chg
        out["short_balance_shares"] = s_bal
        out["short_change_shares"] = s_chg
        if bal and s_bal is not None and bal != 0:
            out["margin_short_ratio_pct"] = round(s_bal / bal * 100.0, 4)

        # 既有變動率
        if chg is not None and bal:
            prev = bal - chg
            if prev:
                out["margin_change_1d"] = chg / prev
        if s_chg is not None and s_bal:
            prev_s = s_bal - s_chg
            if prev_s:
                out["short_change_1d"] = s_chg / prev_s

    # 3d ratio：今日 - 3d 前的餘額 / 3d 前餘額
    if len(trade_dates_3d) >= 3:
        oldest = trade_dates_3d[0]
        oldest_row = margin_by_day.get(oldest)
        if today_row and oldest_row:
            today_bal = today_row.get("margin_balance")
            old_bal = oldest_row.get("margin_balance")
            if today_bal is not None and old_bal:
                # old_bal 是「該日收盤」，前一日餘額 = old_bal - old_change
                old_chg = oldest_row.get("margin_change") or 0
                base = old_bal - old_chg
                if base:
                    out["margin_change_3d"] = (today_bal - base) / base

            today_short = today_row.get("short_balance")
            old_short = oldest_row.get("short_balance")
            if today_short is not None and old_short:
                old_s_chg = oldest_row.get("short_change") or 0
                base_s = old_short - old_s_chg
                if base_s:
                    out["short_change_3d"] = (today_short - base_s) / base_s


def _attach_industry_rankings(candidates: List[Dict[str, Any]]) -> None:
    """為每筆候選股加 `industry_rank_5d` / `industry_rank_net_3d` / `industry_count`。

    排名為 1-indexed，None 值排在最後。
    """
    by_industry: Dict[str, List[Dict[str, Any]]] = {}
    for c in candidates:
        ind = c.get("industry") or ""
        by_industry.setdefault(ind, []).append(c)

    for ind, group in by_industry.items():
        count = len(group)
        # price_change_5d 排名（None 視為 -inf）
        ranked_price = sorted(
            group,
            key=lambda c: (
                c.get("price_change_5d") if c.get("price_change_5d") is not None else float("-inf")
            ),
            reverse=True,
        )
        for idx, c in enumerate(ranked_price, start=1):
            c["industry_rank_5d"] = idx
            c["industry_count"] = count
        # net_3d 排名
        ranked_net = sorted(
            group,
            key=lambda c: c.get("total_institution_flow_3d") or 0.0,
            reverse=True,
        )
        for idx, c in enumerate(ranked_net, start=1):
            c["industry_rank_net_3d"] = idx


def _industry_top_pct(rank: Optional[int], count: Optional[int], pct: float) -> bool:
    """rank 是否在前 pct%（用 ceil；count<=0 視為 False）。"""
    if rank is None or count is None or count <= 0:
        return False
    threshold = max(1, math.ceil(count * pct))
    return rank <= threshold
