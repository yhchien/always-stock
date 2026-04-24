"""
M21 Trade Quality Context 資料管線的所有 lookback 視窗與門檻常數。

單一事實來源：調整門檻只改這一檔，不接受 env 覆寫（第一版刻意保持簡單）。
單位：所有 lookback 均以**交易日**計，不是 calendar days。

詳見 docs/plans/trade_quality_context_spec.md 附錄 B。
"""
from __future__ import annotations

# === Lookback windows（交易日） ===
INDUSTRY_PRICE_LOOKBACK_DAYS = 5
INDUSTRY_VOLUME_RECENT_DAYS = 3
INDUSTRY_VOLUME_BASELINE_DAYS = 5
INDUSTRY_INSTITUTION_LOOKBACK_DAYS = 3
FALSE_HOT_SPIKE_DAYS = 2

CHIP_PRIMARY_LOOKBACK_DAYS = 7
CHIP_SECONDARY_LOOKBACK_DAYS = 15

PRICE_TREND_LOOKBACK_DAYS = 10
PRICE_BREAKOUT_BASELINE_DAYS = 20
PRICE_CONSOLIDATION_RANGE_PCT = 0.05  # 5% 區間內視為盤整

# === 產業門檻 ===
INDUSTRY_PRICE_STRONG_MIN_POSITIVE_STOCKS = 3
INDUSTRY_PRICE_MEDIUM_POSITIVE_STOCKS = 2
INDUSTRY_VOLUME_EXPANDING_PCT = 0.15        # 近 3d avg > 前 5d avg × 1.15
INDUSTRY_VOLUME_INTERMITTENT_PCT = 0.05     # 近 3d avg > 前 5d avg × 1.05 但未達 expanding
INDUSTRY_INSTITUTION_STRONG_MIN_STOCKS = 3  # 產業裡 >= 3 檔近 3 日 >= 2 日法人淨買
INDUSTRY_INSTITUTION_MIXED_MIN_STOCKS = 1   # 至少 1 檔才算 mixed

# === 個股籌碼 ===
CHIP_VOLUME_INCREASING_PCT = 0.10    # 近 3d avg > 前 5d avg × 1.10
CHIP_VOLUME_SPIKE_PCT = 0.50         # 單日 > 前 5d avg × 1.5 為 spike
CHIP_VOLUME_DECLINING_PCT = -0.10    # 近 3d avg < 前 5d avg × 0.90
CHIP_ACCUMULATION_MAX_SINGLE_DAY_PCT = 0.05  # 單日漲幅 <= 5% 才算 gradual

# === Peer rank ===
LEADER_RETURN_PCT_TOP = 0.30          # 報酬率排行 top 30% 算 leader 條件之一
LEADER_REQUIRED_CRITERIA = 2          # 4 個條件滿足 >= 2 個即 leader
PEER_RANK_MIN_PEERS = 3               # 同產業 active peers 至少要這麼多，否則 percentile 回 null

# === Price structure ===
PRICE_TREND_UPTREND_SLOPE = 0.02      # 10 日 close 線性斜率 / 起點 >= 2% 視為 uptrend
PRICE_TREND_DOWNTREND_SLOPE = -0.02
PRICE_ACCELERATING_RATIO = 1.5        # 近 5 日斜率 / 前 5 日斜率 >= 1.5 算 accelerating

# === Hot level 分數對應 ===
HOT_LEVEL_S_MIN = 7  # 7~8 -> S
HOT_LEVEL_A_MIN = 5  # 5~6 -> A
HOT_LEVEL_B_MIN = 3  # 3~4 -> B
# else -> C
