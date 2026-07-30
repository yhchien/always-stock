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
  WINNER: "Winner／正向結果",
  NEUTRAL: "中性結果",
  BIG_LOSER: "大幅負報酬結果",
  IMMATURE: "尚未成熟",
  OUTCOME_DATA_MISSING: "結果資料缺漏",
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
  NOT_SELECTED_WINNER: "未列入後成為 Winner",
  HIGH_RANK_NOT_SELECTED_WINNER: "前段順位未列入後成為 Winner",
  RECOMMEND_BIG_LOSER: "正式推薦後大幅負報酬",
  RANK_OVERRIDE_BIG_LOSER: "Rank Override 後大幅負報酬",
  PREMATURE_STOP_CANDIDATE: "可能過早停止觀察",
  OUTCOME_DATA_MISSING: "Outcome 資料缺漏",
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
