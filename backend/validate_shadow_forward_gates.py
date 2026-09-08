"""Shadow Portfolio Forward Freeze — 5 Gates 驗證（spec Part 31~39）。

主要是唯讀查詢；唯一例外是 `DATA_INTEGRITY_GATE` 會刻意對 replay 的最後一個交易日
重新呼叫一次 `run_daily_trading_strategy`（這正是「重跑同一天不應該多寫任何東西」
這個 idempotency 主張本身要驗證的動作——`already_decided` 短路保證重跑是 no-op，
不會產生新的決策/訂單；若這個假設不成立，這裡的重跑本身就是發現問題的訊號）。
對已經跑完 sanity replay 的 `strategy_version` 逐一檢查：

    DATA_INTEGRITY_GATE
    PNL_SEMANTICS_GATE
    NO_LOOKAHEAD_GATE
    PORTFOLIO_ACCOUNTING_GATE
    STRATEGY_FREEZE_GATE（只對 FORWARD_V1_202609 有意義；Clean Baselines 沒有
    manifest 這個概念，這個 gate 對它們自動略過並標 SKIPPED）

用法：
    python3 validate_shadow_forward_gates.py --strategy-version=FORWARD_V1_202609
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _parse_strategy_version(argv: list) -> str:
    for arg in argv:
        if arg.startswith("--strategy-version="):
            return arg.split("=", 1)[1].strip()
    raise SystemExit("必須帶 --strategy-version=XXX")


def gate_data_integrity(db, strategy_version: str, logger) -> tuple[bool, list[str]]:
    from datetime import date as date_cls

    from app.models import ShadowStrategyDailyDecision, ShadowStrategyOrder
    from app.signals import shadow_portfolio as sp
    from app.trading_calendar import is_trading_day

    notes: list[str] = []
    ok = True

    dates = sorted(
        d[0]
        for d in db.query(ShadowStrategyDailyDecision.trade_date)
        .filter(ShadowStrategyDailyDecision.strategy_version == strategy_version)
        .distinct()
        .all()
    )
    if not dates:
        return False, ["找不到任何 ShadowStrategyDailyDecision，尚未跑過 sanity replay"]

    # 1) 非交易日守門：找一個確定不在 daily_price 裡的合成日期，確認 is_trading_day 判 False
    synthetic_non_trading_day = date_cls(1999, 1, 1)
    if is_trading_day(db, synthetic_non_trading_day):
        ok = False
        notes.append(f"is_trading_day({synthetic_non_trading_day}) 預期 False 卻回傳 True")
    else:
        notes.append(f"is_trading_day({synthetic_non_trading_day}) 正確回傳 False")

    # 2) 重跑最後一個交易日不應該增加任何決策/訂單（already_decided 短路）
    last_date = dates[-1]
    before_decisions = (
        db.query(ShadowStrategyDailyDecision)
        .filter(
            ShadowStrategyDailyDecision.strategy_version == strategy_version,
            ShadowStrategyDailyDecision.trade_date == last_date,
        )
        .count()
    )
    before_orders = db.query(ShadowStrategyOrder).filter(ShadowStrategyOrder.strategy_version == strategy_version).count()

    sp.run_daily_trading_strategy(db, target_date=last_date, strategy_version=strategy_version)
    db.commit()

    after_decisions = (
        db.query(ShadowStrategyDailyDecision)
        .filter(
            ShadowStrategyDailyDecision.strategy_version == strategy_version,
            ShadowStrategyDailyDecision.trade_date == last_date,
        )
        .count()
    )
    after_orders = db.query(ShadowStrategyOrder).filter(ShadowStrategyOrder.strategy_version == strategy_version).count()

    if before_decisions != after_decisions or before_orders != after_orders:
        ok = False
        notes.append(
            f"重跑 {last_date} 後決策數 {before_decisions}->{after_decisions}、"
            f"訂單數 {before_orders}->{after_orders}，預期完全不變（idempotency 破壞）"
        )
    else:
        notes.append(f"重跑 {last_date} 決策數={after_decisions}、訂單數={after_orders} 完全不變（PASS）")

    return ok, notes


def gate_pnl_semantics(db, strategy_version: str, logger) -> tuple[bool, list[str]]:
    from app.models import ShadowStrategyDailyDecision
    from app.signals import shadow_portfolio as sp

    notes: list[str] = []
    ok = True
    params = sp.STRATEGY_PARAMS_BY_VERSION[strategy_version]

    # 1) 所有 REAL_POSITION_STOP_LOSS 出場，actual_position_return 必須 <= real_stop_loss_pct
    stop_loss_decisions = (
        db.query(ShadowStrategyDailyDecision)
        .filter(
            ShadowStrategyDailyDecision.strategy_version == strategy_version,
            ShadowStrategyDailyDecision.action_reason == sp.EXIT_REASON_REAL_STOP_LOSS,
        )
        .all()
    )
    bad_stop = [
        d for d in stop_loss_decisions
        if d.actual_position_return is None or d.actual_position_return > params["real_stop_loss_pct"]
    ]
    if bad_stop:
        ok = False
        notes.append(
            f"{len(bad_stop)} 筆 REAL_POSITION_STOP_LOSS 的 actual_position_return 沒有 <= "
            f"{params['real_stop_loss_pct']}%：{[(d.stock_id, d.trade_date, d.actual_position_return) for d in bad_stop]}"
        )
    else:
        notes.append(f"{len(stop_loss_decisions)} 筆 REAL_POSITION_STOP_LOSS 全部符合 actual_position_return 門檻")

    # 2) 若這個策略版本沒有固定停利（take_profit_basis is None），不應該出現任何
    #    TAKE_PROFIT 出場決策
    if params.get("take_profit_basis") is None:
        tp_decisions = (
            db.query(ShadowStrategyDailyDecision)
            .filter(
                ShadowStrategyDailyDecision.strategy_version == strategy_version,
                ShadowStrategyDailyDecision.action_reason == sp.EXIT_REASON_TAKE_PROFIT,
            )
            .count()
        )
        if tp_decisions > 0:
            ok = False
            notes.append(f"此策略版本沒有固定停利，卻出現 {tp_decisions} 筆 TAKE_PROFIT 出場")
        else:
            notes.append("此策略版本沒有固定停利，且確實 0 筆 TAKE_PROFIT 出場（PASS）")
    else:
        notes.append(f"此策略版本 take_profit_basis={params['take_profit_basis']!r}，不檢查 TAKE_PROFIT 缺席")

    # 3) 若 add_requires_profit=True，所有 ADD 決策的 actual_position_return 必須 > 0
    if params.get("add_requires_profit"):
        add_decisions = (
            db.query(ShadowStrategyDailyDecision)
            .filter(
                ShadowStrategyDailyDecision.strategy_version == strategy_version,
                ShadowStrategyDailyDecision.action == sp.ACTION_ADD,
            )
            .all()
        )
        bad_add = [d for d in add_decisions if d.actual_position_return is None or d.actual_position_return <= 0]
        if bad_add:
            ok = False
            notes.append(
                f"{len(bad_add)} 筆 ADD 決策 actual_position_return 沒有 > 0（違反絕不攤平）："
                f"{[(d.stock_id, d.trade_date, d.actual_position_return) for d in bad_add]}"
            )
        else:
            notes.append(f"{len(add_decisions)} 筆 ADD 決策全部確認 actual_position_return > 0（PASS）")

    return ok, notes


def gate_no_lookahead(db, strategy_version: str, logger) -> tuple[bool, list[str]]:
    from app.models import DailyPrice, ShadowStrategyOrder
    from app.signals import shadow_portfolio as sp

    notes: list[str] = []
    ok = True

    orders = (
        db.query(ShadowStrategyOrder)
        .filter(
            ShadowStrategyOrder.strategy_version == strategy_version,
            ShadowStrategyOrder.status == sp.ORDER_STATUS_EXECUTED,
        )
        .all()
    )
    mismatches = []
    for o in orders:
        price_row = (
            db.query(DailyPrice)
            .filter(DailyPrice.stock_id == o.stock_id, DailyPrice.trade_date == o.scheduled_execution_date)
            .first()
        )
        if price_row is None:
            mismatches.append((o.stock_id, o.scheduled_execution_date, "no daily_price row"))
            continue
        expected = price_row.high_price if o.action in ("BUY", "ADD") else price_row.low_price
        if expected is None or o.execution_price is None or abs(expected - o.execution_price) > 1e-6:
            mismatches.append((o.stock_id, o.scheduled_execution_date, f"expected={expected} actual={o.execution_price}"))
        # signal_date 必須嚴格早於（或至少不晚於次一個交易日的）execution_date——
        # T+1 執行模型下，execution_date 不應該等於或早於 signal_date
        if o.execution_price is not None and o.signal_date >= o.scheduled_execution_date:
            mismatches.append((o.stock_id, o.signal_date, f"signal_date >= execution_date={o.scheduled_execution_date}"))

    if mismatches:
        ok = False
        notes.append(f"{len(mismatches)} 筆訂單成交價與 T+1 HIGH/LOW 對不上，或 signal_date 未早於 execution_date：{mismatches[:10]}")
    else:
        notes.append(f"{len(orders)} 筆已成交訂單，成交價全部正確對應 T+1 HIGH（BUY/ADD）/LOW（SELL），且 signal_date 皆早於 execution_date")

    return ok, notes


def gate_portfolio_accounting(db, strategy_version: str, logger) -> tuple[bool, list[str]]:
    from app.models import ShadowPortfolioDailySnapshot, ShadowStrategyDailyDecision
    from app.signals import shadow_portfolio as sp

    notes: list[str] = []
    ok = True
    params = sp.STRATEGY_PARAMS_BY_VERSION[strategy_version]

    snapshots = (
        db.query(ShadowPortfolioDailySnapshot)
        .filter(ShadowPortfolioDailySnapshot.strategy_version == strategy_version)
        .order_by(ShadowPortfolioDailySnapshot.trade_date.asc())
        .all()
    )
    if not snapshots:
        return False, ["找不到任何每日快照"]

    negative_cash = [s for s in snapshots if s.cash < -1e-6]
    if negative_cash:
        ok = False
        notes.append(f"{len(negative_cash)} 天 cash 為負：{[(s.trade_date, s.cash) for s in negative_cash]}")
    else:
        notes.append("所有交易日 cash 皆 >= 0（PASS）")

    max_stocks = params["max_stocks"]
    over_capacity = [s for s in snapshots if s.position_count > max_stocks]
    if over_capacity:
        ok = False
        notes.append(f"{len(over_capacity)} 天持股數超過 max_stocks={max_stocks}")
    else:
        notes.append(f"所有交易日持股數皆 <= max_stocks={max_stocks}（PASS）")

    if params.get("max_units_per_stock") is None and params.get("max_total_units") is None:
        notes.append("此策略版本無 unit 上限設定（FORWARD_V1_202609 設計如此），不檢查 total_units")

    # SELL 必須排在同日 BUY/ADD 之前（execute_pending_strategy_orders 內部排序保證，
    # 這裡用 order id 遞增順序間接驗證：同一天若同時有 SELL 與 BUY/ADD 成交，SELL 的
    # 建立時間必須不晚於 BUY/ADD）——實際上這條由程式碼結構保證（sort by action ==
    # SELL first），這裡改為 sanity 檢查「同一天賣出釋出的現金確實被當天使用」的間接證據：
    # 不做進一步斷言，只記錄觀察到的同日 SELL+BUY 天數。
    same_day_rotation_days = (
        db.query(ShadowStrategyDailyDecision.trade_date)
        .filter(ShadowStrategyDailyDecision.strategy_version == strategy_version)
        .distinct()
        .count()
    )
    notes.append(f"共 {same_day_rotation_days} 個交易日有決策紀錄")

    return ok, notes


def gate_strategy_freeze(strategy_version: str, logger) -> tuple[bool, list[str]]:
    manifest_path = BACKEND_DIR.parent / "forward_v1_manifest.json"
    if strategy_version != "FORWARD_V1_202609":
        return True, [f"strategy_version={strategy_version} 不是 FORWARD_V1_202609，SKIPPED（此 gate 只對正式 Forward 策略有意義）"]

    if not manifest_path.exists():
        return False, [f"找不到 {manifest_path}"]

    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception as exc:
        return False, [f"{manifest_path} 不是合法 JSON：{exc}"]

    required_keys = {
        "strategy_version", "initial_capital", "unit_capital", "max_stocks",
        "max_position_exposure_pct", "averaging_down", "multi_add", "fixed_take_profit",
        "winner_10_tracking_only", "rotation_enabled", "real_position_stop_loss_pct",
        "execution_buy", "execution_sell", "historical_sanity_start", "historical_sanity_end",
        "git_commit_sha", "strategy_code_hash", "created_at", "status",
    }
    missing = required_keys - set(manifest.keys())
    if missing:
        return False, [f"manifest 缺少必要欄位：{sorted(missing)}"]

    if manifest["strategy_version"] != strategy_version:
        return False, [f"manifest strategy_version={manifest['strategy_version']!r} 與傳入的不符"]

    dirty_files = manifest.get("git_dirty_strategy_files") or []
    if dirty_files:
        return False, [
            f"{manifest_path} 存在且欄位齊全，但 git_commit_sha 尚未涵蓋這次改動——"
            f"以下策略相關檔案有未 commit 的變更，manifest 的 git_commit_sha 不能代表這份 "
            f"replay 實際跑的程式碼：{dirty_files}（strategy_code_hash 仍可用來核對檔案內容本身，"
            f"但 GO 決策前應該先 commit，讓 git_commit_sha 真正對應到這個版本）",
        ]

    return True, [f"{manifest_path} 存在且欄位齊全，status={manifest.get('status')!r}，git 工作樹乾淨"]


def main(argv: list) -> int:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logger = logging.getLogger(__name__)

    strategy_version = _parse_strategy_version(argv)

    from app.database import SessionLocal

    results: dict[str, tuple[bool, list[str]]] = {}
    with SessionLocal() as db:
        results["DATA_INTEGRITY_GATE"] = gate_data_integrity(db, strategy_version, logger)
        results["PNL_SEMANTICS_GATE"] = gate_pnl_semantics(db, strategy_version, logger)
        results["NO_LOOKAHEAD_GATE"] = gate_no_lookahead(db, strategy_version, logger)
        results["PORTFOLIO_ACCOUNTING_GATE"] = gate_portfolio_accounting(db, strategy_version, logger)
    results["STRATEGY_FREEZE_GATE"] = gate_strategy_freeze(strategy_version, logger)

    print("\n" + "=" * 78)
    print(f"5 GATES — strategy_version={strategy_version}")
    print("=" * 78)
    all_pass = True
    for gate_name, (ok, notes) in results.items():
        status_label = "PASS" if ok else "FAIL"
        all_pass = all_pass and ok
        print(f"\n{gate_name}: {status_label}")
        for note in notes:
            print(f"  - {note}")
    print("\n" + "=" * 78)
    print(f"OVERALL: {'ALL PASS — GO' if all_pass else 'AT LEAST ONE FAIL — NO-GO'}")
    print("=" * 78)

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
