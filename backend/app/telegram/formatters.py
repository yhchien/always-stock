"""純文字訊息組裝（list 指令與模擬交易每日廣播）。

設計：
- 不依賴 Telegram client；單純收 dataclass / model → 回 string
- 所有訊息不超過 Telegram 單訊息 4096 字元上限；超長由 caller chunk
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Sequence

from app.models import TelegramChat
from app.routers.analysis import KeyFactor, TradeQualityResponse
from app.telegram.watchlist_service import (
    WATCHLIST_LIMIT,
    AddResult,
    AdminChatSummary,
    DeleteResult,
    StockSnapshot,
)

HELP_TEXT = (
    "📋 *清單功能指令*\n\n"
    "*基本操作：*\n"
    "`list help` — 顯示此說明\n"
    "`list register <密碼>` — 註冊此 chat（首次使用）\n"
    "`list show` — 顯示我的清單（含最新股價）\n\n"
    "*新增 / 刪除（可用 `,` 一次多檔）：*\n"
    "`list add 2330` — 新增單檔\n"
    "`list add 2330, 2317` — 新增多檔\n"
    "`list delete 2330` — 刪除單檔\n"
    "`list delete 2330, 2317` — 刪除多檔\n\n"
    "*交易質量分析：*\n"
    "`list watch 2330 detail` — 查看 2330 最新分析報告\n"
    "`list run 2330` — 重跑 2330 分析（背景跑、跑完推送）\n"
    "`list run all` — 重跑全部清單（鎖定其他指令直到完成）\n\n"
    f"清單上限：{WATCHLIST_LIMIT} 檔\n"
    "每日 21:30 自動推送台股行事曆策略報告"
)


def _format_pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%"


def _format_price(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"${value:.2f}"


def _format_snapshot_line(s: StockSnapshot) -> str:
    """單行：`2330 台積電 (積體電路業)  收 $1050.00 (+1.20%)`"""
    industry = ""
    if s.sub_industry:
        industry = f"（{s.sub_industry}）"
    elif s.industry_name:
        industry = f"（{s.industry_name}）"

    if s.close_price is None:
        price_part = "尚無股價資料"
    else:
        price_part = f"收 {_format_price(s.close_price)} ({_format_pct(s.spread_pct)})"

    return f"`{s.stock_id}` *{s.stock_name}* {industry}\n    {price_part}"


def format_add_result(result: AddResult) -> str:
    lines: List[str] = []
    if result.added:
        lines.append(f"✅ 已新增 {len(result.added)} 檔：")
        for s in result.added:
            lines.append(_format_snapshot_line(s))
    if result.duplicates:
        lines.append("")
        lines.append(f"ℹ️ 已在清單中（跳過）：{', '.join(result.duplicates)}")
    if result.not_found:
        lines.append("")
        lines.append(f"❌ 找不到代號：{', '.join(result.not_found)}")
    if result.over_limit:
        lines.append("")
        lines.append(
            f"⚠️ 超過 {WATCHLIST_LIMIT} 檔上限，未加入：{', '.join(result.over_limit)}"
        )
    lines.append("")
    lines.append(f"目前清單：{result.current_count}/{WATCHLIST_LIMIT}")

    if not lines:
        return "❓ 沒有有效的股票代號輸入。範例：`list add 2330` 或 `list add 2330, 2317`"

    return "\n".join(lines)


def format_delete_result(result: DeleteResult) -> str:
    lines: List[str] = []
    if result.removed:
        lines.append(f"✅ 已刪除：{', '.join(result.removed)}")
    if result.not_in_list:
        lines.append(f"❌ 不在清單中：{', '.join(result.not_in_list)}")

    lines.append("")
    if result.remaining:
        lines.append(f"📋 目前清單（{result.current_count}/{WATCHLIST_LIMIT}）：")
        for s in result.remaining:
            lines.append(_format_snapshot_line(s))
    else:
        lines.append("📋 清單已清空。")

    return "\n".join(lines)


def format_watchlist(snapshots: List[StockSnapshot]) -> str:
    if not snapshots:
        return (
            "📋 你的清單目前是空的。\n\n"
            "輸入 `list add 2330` 或 `list add 2330, 2317` 新增。"
        )
    lines = [f"📋 你的清單（{len(snapshots)}/{WATCHLIST_LIMIT}）："]
    for s in snapshots:
        lines.append(_format_snapshot_line(s))
    return "\n".join(lines)


def _format_target_price(low: Optional[float], high: Optional[float]) -> Optional[str]:
    if low is None and high is None:
        return None
    if low is not None and high is not None:
        return f"${low:.2f} ~ ${high:.2f}"
    return _format_price(low if low is not None else high)


_RATING_EMOJI = {
    "STRONG_BUY": "🟢🟢",
    "BUY": "🟢",
    "NEUTRAL": "🟡",
    "WATCH": "🟠",
    "RUN": "🔴",
}


@dataclass(frozen=True)
class ShadowActionReport:
    """一筆模擬策略的待執行動作。"""

    action: str
    stock_id: str
    stock_name: str
    units: int = 1


@dataclass(frozen=True)
class ShadowPositionReport:
    """一檔模擬策略目前持倉的顯示資料。"""

    stock_id: str
    stock_name: str
    entry_date: date
    entry_price: float
    current_price: Optional[float]
    performance_pct: Optional[float]
    units: int = 1


@dataclass(frozen=True)
class ShadowStrategyReport:
    """一套模擬策略的每日廣播區塊。"""

    label: str
    actions: Sequence[ShadowActionReport]
    positions: Sequence[ShadowPositionReport]


@dataclass(frozen=True)
class MarketCalendarStrategyReport:
    """台股行事曆策略的 Telegram 廣播資料。"""

    signal_date: date
    target_date: Optional[date]
    predicted_binary: str
    predicted_direction: Optional[str]
    predicted_return: Optional[float]
    predicted_return_low: Optional[float]
    predicted_return_high: Optional[float]
    explanation: Optional[dict]


def _factors_to_lines(factors: Optional[List[KeyFactor]]) -> List[str]:
    if not factors:
        return []
    category_labels = {
        "industry": "產業",
        "industry_heat": "產業熱度",
        "return": "報酬",
        "chip": "籌碼",
        "technical": "技術",
        "fundamental": "基本面",
    }
    level_emoji = {"A": "🟢", "B": "🟡", "C": "🔴"}
    trend_arrow = {
        "improving": "↑",
        "stable": "→",
        "weakening": "↓",
        "deteriorating": "↓↓",
    }
    out = []
    for f in factors:
        label = category_labels.get(f.category, f.category)
        emoji = level_emoji.get(f.level, "")
        arrow = trend_arrow.get(f.trend, "")
        note = f.note or ""
        out.append(f"  {emoji} {label} {arrow}  {note}")
    return out


def format_trade_quality_brief(response: TradeQualityResponse) -> str:
    """短版（list run 跑完推送 / 每日報告每檔的一段）。"""
    emoji = _RATING_EMOJI.get(response.rating, "")
    lines = [
        f"{emoji} `{response.stock_id}` *{response.stock_name}*",
        f"動作建議：{response.rating_label}（{response.classification or '—'} 類）",
    ]

    target = _format_target_price(response.target_price_low, response.target_price_high)
    exit_range = _format_target_price(response.exit_price_low, response.exit_price_high)
    if target:
        lines.append(f"目標價：{target}")
    if exit_range:
        lines.append(f"出場價：{exit_range}")

    if response.summary:
        lines.append("")
        lines.append(f"_{response.summary}_")

    factor_lines = _factors_to_lines(response.key_factors)
    if factor_lines:
        lines.append("")
        lines.append("燈號：")
        lines.extend(factor_lines)

    return "\n".join(lines)


def format_trade_quality_detail(response: TradeQualityResponse) -> str:
    """長版（list watch <id> detail 用）；含完整 report_markdown。

    若 markdown 過長，由 caller 切 chunk 推送（Telegram 4096 字上限）。
    """
    brief = format_trade_quality_brief(response)
    if response.report_markdown:
        return f"{brief}\n\n———\n\n{response.report_markdown}"
    return brief


def format_trade_quality_not_found(stock_id: str) -> str:
    return (
        f"❌ `{stock_id}` 尚無分析報告。\n\n"
        f"請先用 `list run {stock_id}` 跑一次分析。"
    )


def format_daily_report(
    chat_label: Optional[str],
    snapshots_with_quality: List[tuple[StockSnapshot, Optional[TradeQualityResponse]]],
) -> str:
    """每日 21:30 cron 推送的訊息（每檔精簡一段）。"""
    header = "📊 *每日清單報告*"
    if chat_label:
        header += f"｜{chat_label}"

    if not snapshots_with_quality:
        return f"{header}\n\n清單目前是空的，請用 `list add 2330` 新增。"

    sections = [header, ""]
    for idx, (snap, quality) in enumerate(snapshots_with_quality, start=1):
        sections.append(f"*{idx}. {_format_snapshot_line(snap)}*")
        if quality is not None:
            emoji = _RATING_EMOJI.get(quality.rating, "")
            sections.append(f"  {emoji} {quality.rating_label}（{quality.classification or '—'}）")
            if quality.summary:
                sections.append(f"  _{quality.summary[:120]}_")
        else:
            sections.append("  ⚠️ 尚無分析資料")
        sections.append("")
    return "\n".join(sections).rstrip()


def _format_report_date(value: Optional[date]) -> str:
    return value.isoformat() if value is not None else "—"


def _format_report_price(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.2f}"


def _format_report_pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def format_shadow_daily_report(
    reports: Sequence[ShadowStrategyReport],
    *,
    signal_date: date,
    execution_date: date,
) -> str:
    """格式化兩套模擬策略的每日交易廣播。

    這是 Telegram cron 專用格式，刻意不包含個人 watchlist 或交易品質分析，
    只呈現每套策略的預計動作與目前持倉。
    """
    lines = [
        "📊 魚尾模擬交易每日廣播",
        f"資料日期：{_format_report_date(signal_date)}",
        "",
    ]

    for index, report in enumerate(reports):
        if index:
            lines.append("")
        lines.append(f"【{report.label}】")
        lines.append(f"今日 {_format_report_date(execution_date)} 預計執行")
        if report.actions:
            for action in report.actions:
                action_label = {
                    "BUY": "買進",
                    "ADD": "加碼",
                    "SELL": "賣出",
                }.get(action.action, action.action)
                units = f"（{action.units} 單位）" if action.units != 1 else ""
                lines.append(f"{action_label} {action.stock_id} {action.stock_name}{units}")
        else:
            lines.append("沒有交易動作")

        lines.append("")
        lines.append("目前持倉")
        if report.positions:
            for position in report.positions:
                units = f"（{position.units} 單位）" if position.units != 1 else ""
                lines.append(
                    f"{position.stock_id} {position.stock_name}{units}｜"
                    f"{_format_report_date(position.entry_date)} 買進｜"
                    f"買進價 {_format_report_price(position.entry_price)}｜"
                    f"現在價 {_format_report_price(position.current_price)}｜"
                    f"績效 {_format_report_pct(position.performance_pct)}"
                )
        else:
            lines.append("無持倉")

    return "\n".join(lines)


def format_market_calendar_strategy_report(
    report: MarketCalendarStrategyReport,
) -> str:
    """格式化台股行事曆策略結果，不包含個股清單或持倉。"""
    explanation = report.explanation or {}
    ai = explanation.get("ai_interpretation") or {}
    lines = [
        "📅 魚尾台股行事曆策略",
        f"訊號日：{_format_report_date(report.signal_date)}",
        f"預測日：{_format_report_date(report.target_date)}",
        "",
        f"策略結論：*{report.predicted_binary}*",
        f"盤勢類別：{report.predicted_direction or '—'}",
        f"預估漲跌幅：{_format_report_pct(report.predicted_return)}",
    ]
    if report.predicted_return_low is not None or report.predicted_return_high is not None:
        lines.append(
            "預估區間："
            f"{_format_report_pct(report.predicted_return_low)} ~ "
            f"{_format_report_pct(report.predicted_return_high)}"
        )

    summary = ai.get("summary")
    if summary:
        lines.extend(["", f"AI 判讀：{summary}"])

    regime = ai.get("regime")
    if regime:
        lines.append(f"市場狀態：{regime}")

    bullish = ai.get("bullish_factors") or []
    bearish = ai.get("bearish_factors") or []
    if bullish:
        lines.extend(["", "偏多因素："])
        for item in bullish[:3]:
            factor = item.get("factor") or item.get("name") or "因素"
            meaning = item.get("interpretation") or item.get("meaning") or ""
            lines.append(f"• {factor}：{meaning}")
    if bearish:
        lines.extend(["", "偏空因素："])
        for item in bearish[:3]:
            factor = item.get("factor") or item.get("name") or "因素"
            meaning = item.get("interpretation") or item.get("meaning") or ""
            lines.append(f"• {factor}：{meaning}")

    risks = ai.get("risks") or []
    if risks:
        lines.extend(["", "風險："])
        lines.extend(f"• {risk}" for risk in risks[:3])
    return "\n".join(lines)


def format_admin_chats(items: List[AdminChatSummary]) -> str:
    """`list admin chats` 總覽訊息：所有註冊 chat + 清單大小，依 last_seen DESC。"""
    if not items:
        return "🔐 *Admin*\n\n目前沒有任何註冊的 chat。"

    lines = [f"🔐 *Admin: 所有註冊 chat（{len(items)} 個）*", ""]
    for i, item in enumerate(items, start=1):
        label = item.chat_label or "—"
        registered = item.registered_at.strftime("%Y-%m-%d %H:%M")
        last_seen = item.last_seen_at.strftime("%m/%d %H:%M")
        lines.append(
            f"{i}. `{item.chat_id}` ({label})"
        )
        lines.append(
            f"    註冊 {registered} · 最後活動 {last_seen} · "
            f"清單 {item.watchlist_size}/{WATCHLIST_LIMIT}"
        )
    lines.append("")
    lines.append("用 `list admin show <chat_id>` 看單一 chat 的完整清單。")
    return "\n".join(lines)


def format_admin_chat_detail(chat: TelegramChat, snapshots: List[StockSnapshot]) -> str:
    """`list admin show <id>` 詳細訊息：單一 chat 的完整觀察清單。"""
    label = chat.chat_label or "—"
    lines = [
        f"🔐 *Admin: chat `{chat.chat_id}` ({label})*",
        "",
        f"註冊：{chat.registered_at.strftime('%Y-%m-%d %H:%M')}",
        f"最後活動：{chat.last_seen_at.strftime('%Y-%m-%d %H:%M')}",
        f"清單：{len(snapshots)}/{WATCHLIST_LIMIT}",
        "",
    ]
    if snapshots:
        for s in snapshots:
            lines.append(_format_snapshot_line(s))
    else:
        lines.append("（清單為空）")
    return "\n".join(lines)


def chunk_for_telegram(text: str, chunk_size: int = 3900) -> List[str]:
    """切成 ≤ chunk_size 的段；逐行切，避免切斷單行。"""
    if len(text) <= chunk_size:
        return [text]
    chunks: List[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > chunk_size:
            if current:
                chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks
