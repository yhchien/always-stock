import type { SignalDecisionType } from "@/lib/api"

const DECISION_LABELS: Record<SignalDecisionType, string> = {
  LEADER: "領漲",
  FOLLOWER: "跟漲",
  LAGGARD: "補漲",
}

const VALUE_LABELS: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
  none: "無",
  strong: "強",
  moderate: "中等",
  weak: "偏弱",
  accumulating: "籌碼集中",
  neutral: "中性",
  weakening: "轉弱",
  retail_overheated: "散戶過熱",
  short_squeeze_potential: "軋空潛力",
  positive: "正向",
  negative: "負向",
  breakout: "突破",
  steady_uptrend: "穩定上升",
  early_turn: "初步轉強",
  range_bound: "區間整理",
  distribution: "出貨",
  strong_bull: "強多",
  structural_bull: "結構偏多",
  range: "盤整",
  risk_on: "偏多",
  risk_off: "避險",
  long: "偏多",
  short: "偏空",
  unavailable: "資料不足",
}

// 訊號清單表格用的欄位專屬標籤；同欄字數刻意對齊，避免 chip 寬度不一致。
const FIELD_VALUE_LABELS: Record<string, Record<string, string>> = {
  theme_fit: { high: "強", medium: "中", low: "弱" },
  capital_flow: { strong: "強", moderate: "中", weak: "弱", none: "無" },
  chip_trend: {
    accumulating: "集中",
    neutral: "中性",
    weakening: "轉弱",
    distribution: "出貨",
  },
  margin_short_signal: {
    retail_overheated: "過熱",
    short_squeeze_potential: "軋空",
    neutral: "中性",
    none: "無感",
  },
  technical_status: {
    breakout: "突破",
    steady_uptrend: "上升",
    early_turn: "轉強",
    range_bound: "盤整",
    distribution: "出貨",
    weak: "偏弱",
  },
}

export function decisionBadgeClass(type: SignalDecisionType | null | undefined): string {
  switch (type) {
    case "LEADER":
      return "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"
    case "FOLLOWER":
      return "bg-sky-500/20 text-sky-300 border-sky-500/40"
    case "LAGGARD":
      return "bg-amber-500/20 text-amber-300 border-amber-500/40"
    default:
      return "bg-slate-500/20 text-slate-300 border-slate-500/40"
  }
}

export function signalDecisionLabel(type: SignalDecisionType | null | undefined): string {
  if (!type) return "未分類"
  return DECISION_LABELS[type] ?? type
}

export function signalValueTone(kind: string, rawValue: string | null | undefined): "green" | "amber" | "red" | "slate" {
  const value = String(rawValue ?? "").toLowerCase()
  if (!value) return "slate"

  if (kind === "market_state") {
    if (value === "strong_bull" || value === "structural_bull") return "green"
    if (value === "range") return "amber"
    if (value === "weak") return "red"
  }

  if (kind === "vix_status") {
    if (value === "risk_on") return "green"
    if (value === "neutral") return "amber"
    if (value === "risk_off") return "red"
  }

  if (kind === "futures_bias") {
    if (value === "long") return "green"
    if (value === "neutral") return "amber"
    if (value === "short") return "red"
  }

  const green = new Set([
    "high",
    "strong",
    "accumulating",
    "positive",
    "breakout",
    "steady_uptrend",
    "early_turn",
  ])
  const amber = new Set([
    "medium",
    "moderate",
    "neutral",
    "range_bound",
    "short_squeeze_potential",
  ])
  const red = new Set([
    "low",
    "none",
    "weak",
    "weakening",
    "retail_overheated",
    "negative",
    "distribution",
  ])

  if (kind === "type" && value === "laggard") return "red"
  if (green.has(value)) return "green"
  if (amber.has(value)) return "amber"
  if (red.has(value)) return "red"
  return "slate"
}

export function toneChipClass(tone: "green" | "amber" | "red" | "slate"): string {
  switch (tone) {
    case "green":
      return "border-emerald-500/40 bg-emerald-500/15 text-emerald-200"
    case "amber":
      return "border-amber-500/40 bg-amber-500/15 text-amber-200"
    case "red":
      return "border-rose-500/40 bg-rose-500/15 text-rose-200"
    default:
      return "border-slate-600/60 bg-slate-700/40 text-slate-300"
  }
}

export function signalValueLabel(
  rawValue: string | null | undefined,
  kind?: string,
): string {
  const value = String(rawValue ?? "")
  if (!value) return "—"
  const normalized = value.toLowerCase()
  if (kind && FIELD_VALUE_LABELS[kind]?.[normalized]) {
    return FIELD_VALUE_LABELS[kind][normalized]
  }
  if (VALUE_LABELS[normalized]) return VALUE_LABELS[normalized]
  return value.replaceAll("_", " ").toUpperCase()
}
