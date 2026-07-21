"""
魚尾 Phase 2：Canonical Momentum Pipeline（2026-07-21 啟動）。

**這整個套件只在 shadow mode 下執行**（`SIGNALS_PIPELINE_MODE=phase2_shadow`），
production 預設（`legacy`，未設定時的預設值）完全不會 import 或呼叫這裡的任何函式——
`app/signals/pipeline.py` 的 legacy 路徑對這個套件零依賴，行為與 Phase 2 開工前
逐 byte 相同。

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
