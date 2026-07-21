"""
魚尾 Phase 2：Canonical Momentum Pipeline（2026-07-21 shadow 上線 / 2026-07-22
production cutover）。

**由 `SIGNALS_PIPELINE_MODE` 控制執行方式**（`app/signals/pipeline.py`）：
    - "legacy"：完全不 import 或呼叫這裡的任何函式，行為與 Phase 2 開工前逐 byte 相同
    - "phase2_shadow"：跑這個套件但只寫進 `signal_shadow_snapshots` 供比對，不影響
      真正回傳給使用者的 watchlist/removed（那些仍來自 legacy）
    - "phase2"（2026-07-22 起為預設值）：這個套件的存活者**就是**真正送進 LLM、
      寫進 `signal_snapshots` / `signal_watch_hits` 的候選來源；legacy chain 仍會
      照跑，只當作 fail-safe fallback（Phase 2 丟例外時退回使用）與持續監控比較
      基準（見 `signal_shadow_snapshots.comparison_summary`）。

目標（見對話中 Phase 2 完整 spec）：把「產業」從「股票生死的硬條件」變成「描述股票
所處環境的一組 context」。核心問題：
    - 漢翔（AEROSPACE_DEFENSE，樣本數=1）不該因為「產業樣本太小」被當掉，
      也不該因為樣本=1 產生 100 percentile 假訊號
    - 台虹（PCB 產業整體弱）不該因為所在產業弱就被判死，個股層級的強度應該
      有獨立的表達空間
    - 航運類股沒有一檔完美 6/6 LEADER 時，FOLLOWER/LAGGARD 不該全滅
    - 台化（追蹤中的強勢股）不該每天重新參加「新人 Role 選秀」，應該問
      「原本的強勢邏輯還在嗎」
    - hit_count < 3 不該單獨造成 RISK_OFF hard exclusion（incumbency bias）

模組：
    sector_context.py    hierarchical peer_scope（SUB_SECTOR → PRIMARY_SECTOR →
                          MARKET_ONLY）+ sector_strength / peer_rs 分離
    roles.py              Role taxonomy（SECTOR_LEADER/CO_LEADER/INDEPENDENT_LEADER/
                          SECTOR_FOLLOWER/ROTATION_LAGGARD/EMERGING_MOMENTUM/
                          UNCLASSIFIED_MOMENTUM）+ evidence-count 判定
    sector_cluster.py     Sector momentum cluster（ACTIVE/NEUTRAL/COOLING/FAILED/
                          UNAVAILABLE），讓 FOLLOWER 不必依賴 formal LEADER 存在
    entry_state.py        Entry state（NEAR_HIGH/NORMAL_PULLBACK/DEEP_PULLBACK/
                          REACCELERATING/STRUCTURE_DAMAGED）+ ATR normalize，
                          與 role 分離（不再是 role 判定的一部分）
    tracking_state.py     New discovery vs Existing tracking 分流；
                          tracking_momentum_state（ACTIVE_TREND/HEALTHY_PULLBACK/
                          REACCELERATING/DETERIORATING/INVALIDATED）
    regime_gate.py        Phase 2 版 regime gate：hit_count 從 hard eligibility gate
                          改為 conviction enhancer
    explain_trace.py      每檔 candidate 的完整決策追蹤（signal_explain_trace）
    funnel_metrics.py     每日 funnel 統計（candidate → role → risk → regime → llm）
    pipeline_v2.py        整合入口：把上述模組串成完整 shadow pipeline
"""
