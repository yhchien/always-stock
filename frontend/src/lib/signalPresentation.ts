import type { SignalDecisionType } from "@/lib/api"

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

export function signalValueTone(kind: string, rawValue: string | null | undefined): "green" | "amber" | "red" | "slate" {
  const value = String(rawValue ?? "").toLowerCase()
  if (!value) return "slate"

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

export function signalValueLabel(rawValue: string | null | undefined): string {
  const value = String(rawValue ?? "")
  if (!value) return "—"
  return value.replaceAll("_", " ").toUpperCase()
}
