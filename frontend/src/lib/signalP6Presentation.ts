import type {
  SignalObservationDecision,
  SignalObservationStatus,
  SignalOutcomeLabel,
} from "@/lib/api"

export const P3_DECISION_LABELS: Record<string, string> = {
  RECOMMEND: "正式推薦",
  NOT_SELECTED: "未列入今日推薦",
  REMOVE: "明確移除",
}

export const OBSERVATION_STATUS_LABELS: Record<SignalObservationStatus, string> = {
  OBSERVING: "觀察中",
  CAUTION: "警戒觀察",
  STOPPED: "已停止觀察",
}

export const OBSERVATION_DECISION_LABELS: Record<SignalObservationDecision, string> = {
  CONTINUE: "繼續觀察",
  CAUTION: "警戒觀察",
  STOP_OBSERVING: "停止觀察",
  REVIEW_FAILED: "追蹤檢查失敗",
}

export const OUTCOME_LABELS: Record<SignalOutcomeLabel, string> = {
  WINNER: "大漲達標",
  NEUTRAL: "持平",
  BIG_LOSER: "大跌虧損",
  IMMATURE: "尚未滿10日",
  OUTCOME_DATA_MISSING: "資料缺漏",
}

export const SELECTION_REASON_LABELS: Record<string, string> = {
  LOWER_RELATIVE_PRIORITY: "同日相對順位較低",
  POSITIVE_CASE_INCOMPLETE: "正向案例尚未完整",
  CATALYST_UNCONFIRMED: "催化尚未確認",
  PARTICIPATION_NOT_DISTINCTIVE: "市場參與不夠突出",
  EVIDENCE_COHERENCE_WEAK: "證據連貫性偏弱",
  THESIS_OVERLAP: "推薦論點重疊",
  SETUP_NEEDS_CONFIRMATION: "型態仍待確認",
  RESEARCH_CONFIDENCE_LOW: "研究信心較低",
  NO_DISTINCT_DAILY_EDGE: "今日缺少明確相對優勢",
}

export const REVIEW_CATEGORY_LABELS: Record<string, string> = {
  NOT_SELECTED_WINNER: "未推薦後來卻大漲",
  HIGH_RANK_NOT_SELECTED_WINNER: "排名靠前卻未推薦，後來大漲",
  RECOMMEND_BIG_LOSER: "推薦後大跌",
  RANK_OVERRIDE_BIG_LOSER: "越級推薦後大跌",
  PREMATURE_STOP_CANDIDATE: "疑似過早停止觀察",
  OUTCOME_DATA_MISSING: "結果資料缺漏",
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "—"
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`
}

export function formatRate(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "—"
  return `${(value * 100).toFixed(digits)}%`
}

export function selectionCompleteness(
  status: string | undefined,
  complete: boolean | undefined,
): "COMPLETE" | "PARTIAL_FAILURE" | "GLOBAL_SELECTION_FAILED" {
  if (status === "FAILED" || complete === false) return "GLOBAL_SELECTION_FAILED"
  if (status === "COMPLETED" && complete === true) return "COMPLETE"
  return "PARTIAL_FAILURE"
}
