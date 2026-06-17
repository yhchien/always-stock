"""個人交易策略回測（魚尾永久紀錄區 + 分批進出 + 賣弱買強）。

機制化定義見 docs/strategy/personal_trading_strategy.md。
三個版本：
  v1   : 每檔獨立、簡化觀察（3 日內站回進場價）。
  v1_5 : 每檔獨立、真實觀察規則（大盤/族群/籌碼/5 日線，2 日窗）。
  v2   : 組合資金池 + 賣弱買強跨股輪動。

用法：
    cd backend
    set -a && . ./.env && set +a
    PYTHONPATH=. python3 scripts/backtest_personal_strategy.py --version v2 --seed 42
    PYTHONPATH=. python3 scripts/backtest_personal_strategy.py --version v2 -n 10
    PYTHONPATH=. python3 scripts/backtest_personal_strategy.py --version v2 --all
"""
from __future__ import annotations

import argparse
import random
import time
from datetime import date
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy import create_engine, text

from app.database import DATABASE_URL

# ===== 機制化常數 =====
PART = 100_000                  # 1 份 = 10 萬
INIT_PARTS = 0.5                # 初始 0.5 份
STRENGTH_ADD_PARTS = 0.3        # 走強加碼 0.3 份
LOSS_ADD_PARTS = 0.25           # 虧損補倉 0.25 份
ROTATE_ADD_PARTS = 0.3          # 輪動加碼 0.3 份
SINGLE_CAP = 1.0 * PART         # 單檔上限 1 份 = 10 萬
TOTAL_CAPITAL = 1_000_000       # 100 萬
MAX_POSITIONS = 10              # 最多 10 檔

ADD_TRIGGER = 0.09              # +9% 走強加碼（對 r_avg）
STOP_TRIGGER = -0.10            # -10% 進觀察（對 r_avg）
SIDEWAYS_DAYS = 13              # 持有 13 天視為盤整
SIDEWAYS_RENTRY = 0.05          # 盤整：r_entry < +5%
STRONG_RENTRY = 0.12            # 強勢候選 / B2：r_entry >= +12%
RECOVER_RENTRY_FLOOR = -0.08    # 站回：r_entry > -8%
OBSERVE_WINDOW = 2              # 觀察窗 2 個交易日（v1_5/v2）
OBSERVE_WINDOW_V1 = 3           # v1 觀察窗 3 日
MAX_TRACK_DAYS = 30
ROTATE_GAP = 0.10               # B_r_entry - A_r_entry >= 10%
WEAK_SCORE_MIN = 2
STRENGTH_SCORE_MIN = 3
MAX_ROTATIONS_PER_DAY = 2
TOP_SELL_RANK = 50              # 全市場賣超壓力前 50 名


# ============================================================
# 資料載入與訊號預計算
# ============================================================
class MarketData:
    """把回測窗內所需的全部訊號預先算好，之後 O(1) 查詢。"""

    def __init__(self, engine, win_start: date, win_end: date):
        self.engine = engine
        self.win_start = win_start
        self.win_end = win_end
        self._load()
        self._build()

    def _load(self):
        with self.engine.connect() as c:
            price = pd.read_sql(
                text(
                    """SELECT trade_date, stock_id, close_price, volume
                       FROM daily_price
                       WHERE trade_date BETWEEN :s AND :e
                         AND close_price IS NOT NULL AND close_price > 0"""
                ),
                c, params={"s": self.win_start, "e": self.win_end},
            )
            flow = pd.read_sql(
                text(
                    """SELECT trade_date, stock_id, inst_type, net_amount_est
                       FROM inst_stock_flow
                       WHERE trade_date BETWEEN :s AND :e
                         AND inst_type IN ('foreign','trust')"""
                ),
                c, params={"s": self.win_start, "e": self.win_end},
            )
            master = pd.read_sql(
                text("SELECT stock_id, stock_name, market, industry_name, sub_industry FROM stocks_master"),
                c,
            )
        price["trade_date"] = pd.to_datetime(price["trade_date"]).dt.date
        flow["trade_date"] = pd.to_datetime(flow["trade_date"]).dt.date
        self.price = price
        self.flow = flow
        self.master = master.set_index("stock_id")

    def _build(self):
        p = self.price
        self.dates = sorted(p["trade_date"].unique())
        self.date_idx = {d: i for i, d in enumerate(self.dates)}

        # --- 大盤等權廣度指數 + 10 日均線 ---
        p2 = p.sort_values(["stock_id", "trade_date"]).copy()
        p2["ret"] = p2.groupby("stock_id")["close_price"].pct_change()
        mkt_ret = p2.groupby("trade_date")["ret"].mean()
        level = (1 + mkt_ret.fillna(0)).cumprod() * 100
        ma10 = level.rolling(10).mean()
        ret3 = level.pct_change(3)  # 近 3 日（3 個交易日）報酬
        self.market_weak = {}
        for d in level.index:
            below_ma10 = pd.notna(ma10[d]) and level[d] < ma10[d]
            drop_3d = pd.notna(ret3[d]) and ret3[d] <= -0.05
            self.market_weak[d] = bool(below_ma10 or drop_3d)

        # --- 個股收盤 / ma5 / 日報酬 ---
        self.close: Dict[str, pd.Series] = {}
        self.ma5: Dict[str, pd.Series] = {}
        self.ret: Dict[str, pd.Series] = {}
        for sid, g in p2.groupby("stock_id"):
            s = g.set_index("trade_date")["close_price"].sort_index()
            self.close[sid] = s
            self.ma5[sid] = s.rolling(5).mean()
            self.ret[sid] = s.pct_change()

        # --- 籌碼：外資+投信 net_amount_est ---
        ft = self.flow.groupby(["stock_id", "trade_date"])["net_amount_est"].sum().reset_index()
        self.ftnet: Dict[str, pd.Series] = {}
        self.chip_consec3: Dict[str, pd.Series] = {}
        for sid, g in ft.groupby("stock_id"):
            s = g.set_index("trade_date")["net_amount_est"].sort_index()
            self.ftnet[sid] = s
            self.chip_consec3[sid] = (s < 0).rolling(3).sum().eq(3)
        # 全市場每日賣超壓力前 50 名（net 最負）
        self.top_sell: Dict[date, set] = {}
        for d, g in ft.groupby("trade_date"):
            worst = g.nsmallest(TOP_SELL_RANK, "net_amount_est")
            self.top_sell[d] = set(worst[worst["net_amount_est"] < 0]["stock_id"])

        # --- 族群（sub_industry）轉弱 ---
        sub_of = self.master["sub_industry"].to_dict()
        ft_sub = ft.copy()
        ft_sub["sub"] = ft_sub["stock_id"].map(sub_of)
        sub_flow = ft_sub.dropna(subset=["sub"]).groupby(["sub", "trade_date"])["net_amount_est"].sum()
        self.sub_weak_flow: Dict[str, pd.Series] = {}
        for sub, s in sub_flow.groupby(level=0):
            ss = s.droplevel(0).sort_index()
            self.sub_weak_flow[sub] = (ss < 0).rolling(2).sum().eq(2)
        # 報酬率中位數 fallback
        pr = p2.copy()
        pr["sub"] = pr["stock_id"].map(sub_of)
        sub_ret = pr.dropna(subset=["sub"]).groupby(["sub", "trade_date"])["ret"].median()
        self.sub_weak_ret: Dict[str, pd.Series] = {}
        for sub, s in sub_ret.groupby(level=0):
            ss = s.droplevel(0).sort_index()
            self.sub_weak_ret[sub] = (ss < 0).rolling(2).sum().eq(2)

    # ---- O(1) 查詢 helpers ----
    def get_close(self, sid, d) -> Optional[float]:
        s = self.close.get(sid)
        if s is None or d not in s.index:
            return None
        v = s[d]
        return float(v) if pd.notna(v) else None

    def get_ma5(self, sid, d) -> Optional[float]:
        s = self.ma5.get(sid)
        if s is None or d not in s.index:
            return None
        v = s[d]
        return float(v) if pd.notna(v) else None

    def is_market_weak(self, d) -> bool:
        return self.market_weak.get(d, False)

    def is_chip_bad(self, sid, d) -> bool:
        c3 = self.chip_consec3.get(sid)
        consec = bool(c3[d]) if (c3 is not None and d in c3.index and pd.notna(c3[d])) else False
        in_top = sid in self.top_sell.get(d, set())
        return consec or in_top

    def is_sector_weak(self, sid, d) -> bool:
        sub = self.master["sub_industry"].get(sid)
        if not sub:
            return False
        sf = self.sub_weak_flow.get(sub)
        if sf is not None and sf.abs().sum() > 0 and d in sf.index and pd.notna(sf[d]):
            return bool(sf[d])
        sr = self.sub_weak_ret.get(sub)
        if sr is not None and d in sr.index and pd.notna(sr[d]):
            return bool(sr[d])
        return False

    def sub_of(self, sid):
        return self.master["sub_industry"].get(sid)


# ============================================================
# 持股狀態
# ============================================================
class Position:
    def __init__(self, sid, name, signal_type, entry_date, entry_price, shares, invested):
        self.sid = sid
        self.name = name
        self.signal_type = signal_type
        self.entry_date = entry_date
        self.entry_price = entry_price
        self.shares = shares
        self.invested = invested
        self.avg_cost = entry_price
        self.state = "normal"
        self.has_strength_add = False
        self.has_loss_add = False
        self.observe_start_idx: Optional[int] = None
        self.days_held = 0

    def r_avg(self, close):
        return close / self.avg_cost - 1.0

    def r_entry(self, close):
        return close / self.entry_price - 1.0

    def add(self, amount, price):
        amount = min(amount, SINGLE_CAP - self.invested)
        if amount <= 0:
            return 0.0
        self.shares += amount / price
        self.invested += amount
        self.avg_cost = self.invested / self.shares
        return amount


# ============================================================
# 共用：載入標的池
# ============================================================
def load_pool(engine) -> List[dict]:
    sql = text(
        """SELECT a.stock_id, a.stock_name, a.latest_signal_type,
                  a.baseline_trade_date, a.baseline_price, a.completed_trade_date,
                  sm.sub_industry, sm.market
           FROM signal_watch_completed_archives a
           LEFT JOIN stocks_master sm ON sm.stock_id = a.stock_id
           WHERE a.baseline_price IS NOT NULL AND a.baseline_price > 0
           ORDER BY a.baseline_trade_date, a.stock_id"""
    )
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(sql)]


SIGNAL_RANK = {"LEADER": 0, "FOLLOWER": 1, "LAGGARD": 2}


# ============================================================
# v1 / v1_5：每檔獨立
# ============================================================
def sim_independent(arc: dict, md: MarketData, version: str) -> dict:
    sid = arc["stock_id"]
    entry_date = arc["baseline_trade_date"]
    entry_price = float(arc["baseline_price"])
    completed = arc["completed_trade_date"]

    pos = Position(sid, arc["stock_name"], arc["latest_signal_type"],
                   entry_date, entry_price, INIT_PARTS * PART / entry_price, INIT_PARTS * PART)

    s = md.close.get(sid)
    fwd_dates = [d for d in (s.index if s is not None else []) if entry_date < d <= completed][:MAX_TRACK_DAYS]
    if not fwd_dates:
        return _result(pos, entry_date, entry_price, "no_forward_data", 0.0)

    bh_ret = md.get_close(sid, fwd_dates[-1]) / entry_price - 1.0
    obs_count = 0
    exit_d = exit_p = None
    exit_reason = "period_end"

    for k, d in enumerate(fwd_dates):
        close = md.get_close(sid, d)
        if close is None:
            continue
        pos.days_held = k + 1
        r_avg = pos.r_avg(close)
        r_entry = pos.r_entry(close)

        if pos.state == "normal":
            if (not pos.has_strength_add) and r_avg >= ADD_TRIGGER:
                pos.add(STRENGTH_ADD_PARTS * PART, close)
                pos.has_strength_add = True
                continue
            if r_avg <= STOP_TRIGGER:
                if version == "v1_5" and pos.has_loss_add:
                    exit_d, exit_p, exit_reason = d, close, "stop_reentry"
                    break
                pos.state = "observe"
                obs_count = 0
                continue
            if pos.days_held >= SIDEWAYS_DAYS and (not pos.has_strength_add):
                cond = (STOP_TRIGGER < r_avg < ADD_TRIGGER) if version == "v1" else (r_entry < SIDEWAYS_RENTRY)
                if cond:
                    exit_d, exit_p, exit_reason = d, close, "sideways"
                    break
        else:  # observe
            obs_count += 1
            if version == "v1_5":
                if md.is_market_weak(d):
                    exit_d, exit_p, exit_reason = d, close, "market_weak"; break
                if md.is_sector_weak(sid, d):
                    exit_d, exit_p, exit_reason = d, close, "sector_weak"; break
                if md.is_chip_bad(sid, d):
                    exit_d, exit_p, exit_reason = d, close, "chip_bad"; break
                ma5 = md.get_ma5(sid, d)
                recovered = (ma5 is not None and close >= ma5 and r_entry > RECOVER_RENTRY_FLOOR)
            else:  # v1：站回進場價
                recovered = r_entry >= 0.0
            window = OBSERVE_WINDOW if version == "v1_5" else OBSERVE_WINDOW_V1
            if recovered:
                if not pos.has_loss_add:
                    pos.add(LOSS_ADD_PARTS * PART, close)
                    pos.has_loss_add = True
                pos.state = "normal"
                continue
            if obs_count >= window:
                exit_d, exit_p, exit_reason = d, close, "stop_loss"; break

    if exit_d is None:
        exit_d, exit_p = fwd_dates[-1], md.get_close(sid, fwd_dates[-1])
        exit_reason = "period_end"
    return _result(pos, exit_d, exit_p, exit_reason, bh_ret)


# 'best' 可調參數（由 CLI 設定）
BEST_INIT_PARTS = 0.6        # 初始份數
BEST_MAX_PARTS = 1.0         # 走強加碼上限（單檔上限本來就 1 份）
BEST_USE_TRAIL = False       # 是否掛移動停利
BEST_TRAIL_ARM = 0.25        # 峰值 r_entry 超過此值才啟動 trailing
BEST_TRAIL_GIVEBACK = 0.15   # 從峰值回落此比例才停利
BEST_HARD_STOP = -0.12       # 硬停損（r_avg）


def sim_best(arc: dict, md: MarketData) -> dict:
    """'best' 候選策略：部署更多 + 抱住贏家 + 只砍真輸家。

    - 進場 BEST_INIT_PARTS 份；r_avg>=+9% 加碼到 BEST_MAX_PARTS 份（一次）。
    - 硬停損：r_avg<=BEST_HARD_STOP → 出場（砍真輸家）。
    - 移動停利（選用）：峰值 r_entry>=BEST_TRAIL_ARM 後，收盤從峰值回落>=BEST_TRAIL_GIVEBACK → 出場。
    - 預設不掛 trailing、抱到追蹤期滿（多頭趨勢中讓贏家跑）。
    """
    sid = arc["stock_id"]
    entry_date = arc["baseline_trade_date"]
    entry_price = float(arc["baseline_price"])
    completed = arc["completed_trade_date"]
    init_cost = BEST_INIT_PARTS * PART
    pos = Position(sid, arc["stock_name"], arc["latest_signal_type"],
                   entry_date, entry_price, init_cost / entry_price, init_cost)
    s = md.close.get(sid)
    fwd = [d for d in (s.index if s is not None else []) if entry_date < d <= completed][:MAX_TRACK_DAYS]
    if not fwd:
        return _result(pos, entry_date, entry_price, "no_forward_data", 0.0)
    bh_ret = md.get_close(sid, fwd[-1]) / entry_price - 1.0
    peak = entry_price
    exit_d = exit_p = None
    reason = "period_end"
    for k, d in enumerate(fwd):
        close = md.get_close(sid, d)
        if close is None:
            continue
        pos.days_held = k + 1
        peak = max(peak, close)
        r_avg = pos.r_avg(close)
        peak_rentry = peak / entry_price - 1.0
        if r_avg <= BEST_HARD_STOP:
            exit_d, exit_p, reason = d, close, "stop_loss"; break
        if (not pos.has_strength_add) and r_avg >= ADD_TRIGGER:
            pos.add((BEST_MAX_PARTS - BEST_INIT_PARTS) * PART, close)
            pos.has_strength_add = True
        if BEST_USE_TRAIL and peak_rentry >= BEST_TRAIL_ARM and close <= peak * (1 - BEST_TRAIL_GIVEBACK):
            exit_d, exit_p, reason = d, close, "take_profit"; break
    if exit_d is None:
        exit_d, exit_p = fwd[-1], md.get_close(sid, fwd[-1])
        reason = "period_end"
    return _result(pos, exit_d, exit_p, reason, bh_ret)


def _result(pos: Position, exit_d, exit_p, reason, bh_ret) -> dict:
    proceeds = pos.shares * exit_p
    profit = proceeds - pos.invested
    return {
        "sid": pos.sid, "name": pos.name, "signal_type": pos.signal_type,
        "entry_date": pos.entry_date, "entry_price": pos.entry_price,
        "exit_date": exit_d, "exit_price": exit_p, "exit_reason": reason,
        "cost": pos.invested, "proceeds": proceeds, "profit": profit,
        "ret_on_cost": profit / pos.invested if pos.invested else 0.0,
        "added_strength": pos.has_strength_add, "added_loss": pos.has_loss_add,
        "bh_ret": bh_ret, "held_days": pos.days_held,
    }


# ============================================================
# v2：組合資金池 + 賣弱買強
# ============================================================
def sim_portfolio(picks: List[dict], md: MarketData) -> dict:
    by_entry: Dict[date, List[dict]] = {}
    completed_of: Dict[str, date] = {}
    arc_of: Dict[str, dict] = {}
    for a in picks:
        by_entry.setdefault(a["baseline_trade_date"], []).append(a)
        completed_of[a["stock_id"]] = a["completed_trade_date"]
        arc_of[a["stock_id"]] = a

    win_start = min(a["baseline_trade_date"] for a in picks)
    win_end = max(a["completed_trade_date"] for a in picks)
    dates = [d for d in md.dates if win_start <= d <= win_end]

    cash = TOTAL_CAPITAL
    holdings: Dict[str, Position] = {}
    sold_ever: set = set()        # spec §21：賣出後剩餘期間不再買回
    trades: List[dict] = []
    closed: List[dict] = []
    equity_curve: List[float] = []
    peak = TOTAL_CAPITAL
    max_dd = 0.0
    util_sum = 0.0

    def log(d, sid, action, price, amount, pos, reason, paired=None):
        trades.append({"date": d, "sid": sid, "action": action, "price": price,
                       "amount": amount, "reason": reason, "paired": paired})

    def close_position(d, sid, price, reason):
        nonlocal cash
        pos = holdings.pop(sid)
        proceeds = pos.shares * price
        cash += proceeds
        closed.append({**_result(pos, d, price, reason, 0.0)})
        log(d, sid, "sell", price, proceeds, pos, reason)
        sold_ever.add(sid)        # 賣出後不再買回

    for d in dates:
        held_days_check = []
        # 1) 更新 days_held
        for pos in holdings.values():
            if d > pos.entry_date:
                pos.days_held += 1

        # 2)+3) observe 風險檢查與站回補倉
        for sid in list(holdings.keys()):
            pos = holdings[sid]
            if pos.state != "observe":
                continue
            close = md.get_close(sid, d)
            if close is None:
                continue
            pos.observe_count = getattr(pos, "observe_count", 0) + 1
            r_entry = pos.r_entry(close)
            if md.is_market_weak(d):
                close_position(d, sid, close, "market_weak"); continue
            if md.is_sector_weak(sid, d):
                close_position(d, sid, close, "sector_weak"); continue
            if md.is_chip_bad(sid, d):
                close_position(d, sid, close, "chip_bad"); continue
            ma5 = md.get_ma5(sid, d)
            if ma5 is not None and close >= ma5 and r_entry > RECOVER_RENTRY_FLOOR:
                if not pos.has_loss_add:
                    amt = min(LOSS_ADD_PARTS * PART, SINGLE_CAP - pos.invested, cash)
                    got = pos.add(amt, close) if amt > 0 else 0.0
                    if got > 0:
                        cash -= got
                        pos.has_loss_add = True
                        log(d, sid, "loss_add", close, got, pos, "recover_ma5")
                pos.state = "normal"
                pos.observe_count = 0
                continue
            if pos.observe_count >= OBSERVE_WINDOW:
                close_position(d, sid, close, "stop_loss"); continue

        # 4) normal -> observe (-10%)
        for sid in list(holdings.keys()):
            pos = holdings[sid]
            if pos.state != "normal":
                continue
            close = md.get_close(sid, d)
            if close is None:
                continue
            if pos.r_avg(close) <= STOP_TRIGGER:
                if pos.has_loss_add:
                    close_position(d, sid, close, "stop_reentry")
                else:
                    pos.state = "observe"
                    pos.observe_count = 0

        # 5) normal 走強加碼 (+9%)
        for sid in list(holdings.keys()):
            pos = holdings[sid]
            if pos.state != "normal" or pos.has_strength_add:
                continue
            close = md.get_close(sid, d)
            if close is None:
                continue
            if pos.r_avg(close) >= ADD_TRIGGER:
                amt = min(STRENGTH_ADD_PARTS * PART, SINGLE_CAP - pos.invested, cash)
                got = pos.add(amt, close) if amt > 0 else 0.0
                if got > 0:
                    cash -= got
                    pos.has_strength_add = True
                    log(d, sid, "strength_add", close, got, pos, "strength_9pct")

        # 強勢候選存在？（持股池或候選池中任一 r_entry >= +12%）
        def tradable_today(sid):
            a = arc_of[sid]
            return a["baseline_trade_date"] < d <= a["completed_trade_date"]
        strong_exists = False
        for a in picks:
            sid = a["stock_id"]
            if not tradable_today(sid):
                continue
            c = md.get_close(sid, d)
            if c is not None and (c / float(a["baseline_price"]) - 1.0) >= STRONG_RENTRY:
                strong_exists = True
                break

        # 6) 盤整出場（需有強勢候選）
        for sid in list(holdings.keys()):
            pos = holdings[sid]
            if pos.state != "normal":
                continue
            close = md.get_close(sid, d)
            if close is None:
                continue
            if pos.days_held >= SIDEWAYS_DAYS and (not pos.has_strength_add) \
               and pos.r_entry(close) < SIDEWAYS_RENTRY and strong_exists:
                close_position(d, sid, close, "sideways")

        # 7) 弱股 A 清單
        weak = []
        for sid, pos in holdings.items():
            close = md.get_close(sid, d)
            if close is None:
                continue
            r_avg = pos.r_avg(close); r_entry = pos.r_entry(close)
            ma5 = md.get_ma5(sid, d)
            A1 = r_avg < 0
            A2 = ma5 is not None and close < ma5
            A3 = md.is_chip_bad(sid, d)
            A4 = md.is_sector_weak(sid, d)
            A5 = pos.days_held >= SIDEWAYS_DAYS and r_entry < SIDEWAYS_RENTRY
            score = sum([A1, A2, A3, A4, A5])
            if score >= WEAK_SCORE_MIN and (A1 or A5):
                weak.append({"sid": sid, "score": score, "r_avg": r_avg, "r_entry": r_entry,
                             "days": pos.days_held, "close": close})

        # 8) 強股 B 清單（持股 + 候選池）
        strong = []
        for a in picks:
            sid = a["stock_id"]
            if not tradable_today(sid):
                continue
            close = md.get_close(sid, d)
            if close is None:
                continue
            held = sid in holdings
            if not held and sid in sold_ever:   # 賣出後不再買回
                continue
            r_entry = close / float(a["baseline_price"]) - 1.0
            ma5 = md.get_ma5(sid, d)
            invested = holdings[sid].invested if held else 0.0
            B1 = r_entry > 0
            B2 = r_entry >= STRONG_RENTRY
            B3 = ma5 is not None and close >= ma5
            B4 = not md.is_chip_bad(sid, d)
            B5 = not md.is_sector_weak(sid, d)
            B6 = invested < SINGLE_CAP
            score = sum([B1, B2, B3, B4, B5, B6])
            if score >= STRENGTH_SCORE_MIN and B2 and B6:
                dist = abs(close / ma5 - 1.0) if ma5 else 9.9
                ftn = md.ftnet.get(sid)
                chip = float(ftn[d]) if (ftn is not None and d in ftn.index and pd.notna(ftn[d])) else 0.0
                strong.append({"sid": sid, "score": score, "r_entry": r_entry, "held": held,
                               "invested": invested, "close": close, "dist": dist, "chip": chip})

        # 9) 賣弱買強，最多 2 次
        weak.sort(key=lambda x: (-x["score"], x["r_avg"], x["r_entry"], -x["days"], x["sid"]))
        strong.sort(key=lambda x: (-x["score"], -x["r_entry"], x["dist"], -x["chip"], x["sid"]))
        rotations = 0
        used_b = set()
        for A in weak:
            if rotations >= MAX_ROTATIONS_PER_DAY:
                break
            if A["sid"] not in holdings:
                continue
            B = None
            for cand in strong:
                if cand["sid"] in used_b or cand["sid"] == A["sid"]:
                    continue
                if cand["r_entry"] - A["r_entry"] >= ROTATE_GAP and cand["invested"] < SINGLE_CAP:
                    B = cand
                    break
            if B is None:
                continue
            # 賣 A 全部
            close_position(d, A["sid"], A["close"], "rotate_sell")
            # 買/加碼 B
            bsid = B["sid"]
            if B["held"]:
                if getattr(holdings[bsid], "has_rotate_add", False):
                    continue
                amt = min(ROTATE_ADD_PARTS * PART, SINGLE_CAP - holdings[bsid].invested, cash)
                got = holdings[bsid].add(amt, B["close"]) if amt > 0 else 0.0
                if got > 0:
                    cash -= got
                    holdings[bsid].has_rotate_add = True
                    log(d, bsid, "rotate_add", B["close"], got, holdings[bsid], "rotate", paired=A["sid"])
            else:
                if cash >= INIT_PARTS * PART and len(holdings) < MAX_POSITIONS and bsid not in sold_ever:
                    amt = INIT_PARTS * PART
                    a = arc_of[bsid]
                    pos = Position(bsid, a["stock_name"], a["latest_signal_type"],
                                   d, B["close"], amt / B["close"], amt)
                    holdings[bsid] = pos
                    cash -= amt
                    log(d, bsid, "rotate_buy", B["close"], amt, pos, "rotate", paired=A["sid"])
            used_b.add(bsid)
            rotations += 1

        # 10) 當日新進場候選
        cands = by_entry.get(d, [])
        cands_sorted = sorted(cands, key=lambda a: (SIGNAL_RANK.get(a["latest_signal_type"], 9), a["stock_id"]))
        for a in cands_sorted:
            sid = a["stock_id"]
            if sid in holdings or sid in sold_ever:
                continue
            if len(holdings) >= MAX_POSITIONS or cash < INIT_PARTS * PART:
                continue
            price = float(a["baseline_price"])
            amt = INIT_PARTS * PART
            pos = Position(sid, a["stock_name"], a["latest_signal_type"], d, price, amt / price, amt)
            holdings[sid] = pos
            cash -= amt
            log(d, sid, "buy", price, amt, pos, "entry")

        # 11) 期末出場 / 市值更新
        for sid in list(holdings.keys()):
            if d >= completed_of[sid]:
                close = md.get_close(sid, d) or holdings[sid].avg_cost
                close_position(d, sid, close, "period_end")

        mv = sum(p.shares * (md.get_close(p.sid, d) or p.avg_cost) for p in holdings.values())
        equity = cash + mv
        equity_curve.append(equity)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        util_sum += mv / TOTAL_CAPITAL

    # 結算剩餘（理論上都已 period_end）
    last = dates[-1]
    for sid in list(holdings.keys()):
        close = md.get_close(sid, last) or holdings[sid].avg_cost
        close_position(last, sid, close, "force_close")

    return {
        "closed": closed, "trades": trades, "final_cash": cash,
        "max_dd": max_dd, "util": util_sum / len(dates) if dates else 0.0,
        "n_days": len(dates),
    }


# ============================================================
# 報表
# ============================================================
def print_independent(results: List[dict], version: str):
    hdr = (f"{'代號':<6}{'名稱':<9}{'類型':<9}{'進場':<11}{'出場':<11}"
           f"{'出場原因':<13}{'加碼':<5}{'補倉':<5}{'天':>4}{'投入':>9}{'損益':>10}{'報酬%':>8}{'B&H%':>8}")
    print(hdr); print("-" * len(hdr))
    tc = tp = tpro = tbh = 0.0
    reasons = {}
    for r in sorted(results, key=lambda x: x["ret_on_cost"], reverse=True):
        reasons[r["exit_reason"]] = reasons.get(r["exit_reason"], 0) + 1
        tc += r["cost"]; tp += r["profit"]; tpro += r["proceeds"]
        tbh += INIT_PARTS * PART * r["bh_ret"]
        print(f"{r['sid']:<6}{r['name']:<9}{r['signal_type']:<9}{str(r['entry_date']):<11}"
              f"{str(r['exit_date']):<11}{r['exit_reason']:<13}"
              f"{'是' if r['added_strength'] else '-':<5}{'是' if r['added_loss'] else '-':<5}"
              f"{r['held_days']:>4}{r['cost']:>9,.0f}{r['profit']:>10,.0f}"
              f"{r['ret_on_cost']*100:>7.1f}{r['bh_ret']*100:>8.1f}")
    win = sum(1 for r in results if r["profit"] > 0)
    print("\n" + "=" * 60)
    print(f"版本：{version}　標的數：{len(results)}")
    print(f"出場原因：{reasons}")
    print(f"勝率：{win}/{len(results)} = {win/len(results)*100:.0f}%")
    print(f"投入合計：{tc:,.0f}　賣出合計：{tpro:,.0f}　總損益：{tp:,.0f}")
    print(f"投入資金報酬率：{tp/tc*100:.2f}%")
    print(f"總資金報酬率（/100萬）：{tp/TOTAL_CAPITAL*100:.2f}%")
    print("-" * 60)
    bh_cost = INIT_PARTS * PART * len(results)
    print(f"[對照 B&H] 投入：{bh_cost:,.0f}　損益：{tbh:,.0f}　報酬率：{tbh/bh_cost*100:.2f}%")
    print(f"[對照 B&H] 策略超額損益：{tp-tbh:,.0f}")


def print_portfolio(res: dict):
    closed = res["closed"]
    hdr = (f"{'代號':<6}{'名稱':<9}{'類型':<9}{'進場':<11}{'出場':<11}"
           f"{'出場原因':<13}{'加碼':<5}{'補倉':<5}{'投入':>9}{'損益':>10}{'報酬%':>8}")
    print(hdr); print("-" * len(hdr))
    tc = tp = 0.0
    reasons = {}
    for r in sorted(closed, key=lambda x: x["ret_on_cost"], reverse=True):
        reasons[r["exit_reason"]] = reasons.get(r["exit_reason"], 0) + 1
        tc += r["cost"]; tp += r["profit"]
        print(f"{r['sid']:<6}{r['name']:<9}{r['signal_type']:<9}{str(r['entry_date']):<11}"
              f"{str(r['exit_date']):<11}{r['exit_reason']:<13}"
              f"{'是' if r['added_strength'] else '-':<5}{'是' if r['added_loss'] else '-':<5}"
              f"{r['cost']:>9,.0f}{r['profit']:>10,.0f}{r['ret_on_cost']*100:>7.1f}")
    win = sum(1 for r in closed if r["profit"] > 0)
    final_equity = res["final_cash"]
    print("\n" + "=" * 60)
    print(f"版本：v2（組合資金池 + 賣弱買強）")
    print(f"交易筆數：{len(res['trades'])}　出場筆數：{len(closed)}　出場原因：{reasons}")
    action_count = {}
    for t in res["trades"]:
        action_count[t["action"]] = action_count.get(t["action"], 0) + 1
    print(f"動作分布：{action_count}")
    print(f"勝率（出場筆）：{win}/{len(closed)} = {win/len(closed)*100:.0f}%" if closed else "無出場")
    print(f"投入合計：{tc:,.0f}　總損益（已實現）：{tp:,.0f}")
    print(f"期末總資產：{final_equity:,.0f}（起始 {TOTAL_CAPITAL:,.0f}）")
    print(f"總資金報酬率（/100萬）：{(final_equity-TOTAL_CAPITAL)/TOTAL_CAPITAL*100:.2f}%")
    print(f"投入資金報酬率：{tp/tc*100:.2f}%" if tc else "")
    print(f"最大回撤：{res['max_dd']*100:.2f}%　平均資金使用率：{res['util']*100:.1f}%　模擬天數：{res['n_days']}")


# ============================================================
def run_groups(engine, pool, n_groups, version):
    # round-robin 分組：標的已依 baseline_trade_date 排序，round-robin 讓進場日分散到各組
    groups: List[List[dict]] = [[] for _ in range(n_groups)]
    for i, a in enumerate(pool):
        groups[i % n_groups].append(a)

    win_start = min(a["baseline_trade_date"] for a in pool)
    win_end = max(a["completed_trade_date"] for a in pool)
    md = MarketData(engine, win_start, win_end)

    print(f"\n分成 {n_groups} 組（round-robin）　版本：{version}")
    print("=" * 78)
    hdr = f"{'組':<4}{'檔數':>4}{'總資產':>13}{'/100萬報酬%':>13}{'投入報酬%':>11}{'勝率':>9}{'最大回撤%':>11}"
    print(hdr); print("-" * len(hdr))

    rets = []
    invested_rets = []
    for gi, g in enumerate(groups, 1):
        if version == "v2":
            res = sim_portfolio(g, md)
            closed = res["closed"]
            tp = sum(r["profit"] for r in closed)
            tc = sum(r["cost"] for r in closed)
            final = res["final_cash"]
            ret = (final - TOTAL_CAPITAL) / TOTAL_CAPITAL * 100
            inv_ret = tp / tc * 100 if tc else 0.0
            win = sum(1 for r in closed if r["profit"] > 0)
            wr = f"{win}/{len(closed)}"
            dd = res["max_dd"] * 100
        else:
            if version == "best":
                results = [sim_best(a, md) for a in g]
            else:
                results = [sim_independent(a, md, version) for a in g]
            tp = sum(r["profit"] for r in results)
            tc = sum(r["cost"] for r in results)
            ret = tp / TOTAL_CAPITAL * 100
            inv_ret = tp / tc * 100 if tc else 0.0
            win = sum(1 for r in results if r["profit"] > 0)
            wr = f"{win}/{len(results)}"
            final = TOTAL_CAPITAL + tp
            dd = float("nan")
        rets.append(ret); invested_rets.append(inv_ret)
        print(f"{gi:<4}{len(g):>4}{final:>13,.0f}{ret:>13.2f}{inv_ret:>11.2f}{wr:>9}"
              f"{dd:>11.2f}" if dd == dd else
              f"{gi:<4}{len(g):>4}{final:>13,.0f}{ret:>13.2f}{inv_ret:>11.2f}{wr:>9}{'—':>11}")

    print("-" * len(hdr))
    avg = sum(rets) / len(rets)
    avg_inv = sum(invested_rets) / len(invested_rets)
    print(f"7 組平均：/100萬報酬 {avg:.2f}%　投入報酬 {avg_inv:.2f}%")
    print(f"最佳組：{max(rets):.2f}%　最差組：{min(rets):.2f}%")
    if version == "v2":
        print("（v2 每組都在獨立的 100 萬資金池內、受 10 檔上限與賣弱買強約束）")
    else:
        print("（v1/v1_5 每檔獨立，未受資金池/檔數上限約束；/100萬僅在每組投入≤100萬時才有意義）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", choices=["v1", "v1_5", "v2", "best"], default="v2")
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--groups", type=int, default=None, help="把全部標的平均分成 N 組各跑一個組合")
    ap.add_argument("--init-parts", type=float, default=0.6, help="best：初始份數")
    ap.add_argument("--max-parts", type=float, default=1.0, help="best：加碼上限份數")
    ap.add_argument("--trail", action="store_true", help="best：掛移動停利")
    ap.add_argument("--skip-laggard", action="store_true", help="排除 LAGGARD 類型")
    args = ap.parse_args()

    global BEST_INIT_PARTS, BEST_MAX_PARTS, BEST_USE_TRAIL
    BEST_INIT_PARTS = args.init_parts
    BEST_MAX_PARTS = args.max_parts
    BEST_USE_TRAIL = args.trail

    engine = create_engine(DATABASE_URL)
    pool = load_pool(engine)
    print(f"永久紀錄區可用標的：{len(pool)} 檔")
    if args.skip_laggard:
        pool = [a for a in pool if a["latest_signal_type"] != "LAGGARD"]
        print(f"排除 LAGGARD 後：{len(pool)} 檔")

    if args.groups:
        run_groups(engine, pool, args.groups, args.version)
        return

    if args.all:
        picks = pool
        print(f"模式：全部 {len(picks)} 檔　版本：{args.version}\n")
    else:
        seed = args.seed if args.seed is not None else int(time.time() * 1000) % (2**32)
        rng = random.Random(seed)
        n = min(args.n, len(pool))
        picks = rng.sample(pool, n)
        print(f"模式：隨機 {n} 檔（seed={seed}，--seed {seed} 可重現）　版本：{args.version}\n")

    win_start = min(a["baseline_trade_date"] for a in picks)
    win_end = max(a["completed_trade_date"] for a in picks)
    md = MarketData(engine, win_start, win_end)

    if args.version == "v2":
        res = sim_portfolio(picks, md)
        print_portfolio(res)
    else:
        if args.version == "best":
            results = [sim_best(a, md) for a in picks]
        else:
            results = [sim_independent(a, md, args.version) for a in picks]
        print_independent(results, args.version)


if __name__ == "__main__":
    main()
