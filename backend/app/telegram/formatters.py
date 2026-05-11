"""純文字訊息組裝（list show / list add 成功訊息 / list watch detail / 每日報告）。

設計：
- 不依賴 Telegram client；單純收 dataclass / model → 回 string
- 所有訊息不超過 Telegram 單訊息 4096 字元上限；超長由 caller chunk
"""
from __future__ import annotations

from typing import List, Optional

from app.routers.analysis import KeyFactor, TradeQualityResponse
from app.telegram.watchlist_service import (
    WATCHLIST_LIMIT,
    AddResult,
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
    "每日 21:30 自動推送清單報告"
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
