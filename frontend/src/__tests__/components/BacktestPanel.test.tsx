import React from "react"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

import BacktestPanel from "@/components/BacktestPanel"
import * as api from "@/lib/api"

jest.mock("echarts-for-react", () => {
  return function MockECharts() {
    return <div data-testid="echart">chart</div>
  }
})

const mockRunResult: api.BacktestRunResponse = {
  supported: true,
  normalized_text: "買進：收盤價站上 MA20 且外資連買 3 天；賣出：跌破 MA20 或外資賣超",
  strategy: {},
  unsupported_conditions: [],
  ai_mapped_conditions: [],
  metrics: {
    total_return_pct: 12.5,
    annual_return_pct: 8.2,
    win_rate_pct: 60,
    max_drawdown_pct: -7.5,
    sharpe_ratio: 1.21,
    trade_count: 3,
    ending_equity: 1125000,
    benchmark_return_pct: 10.2,
    excess_return_pct: 2.3,
    avg_trade_return_pct: 4.1,
    avg_holding_days: 12,
    profit_factor: 1.4,
    avg_gain_pct: 5.2,
    avg_loss_pct: -2.1,
    max_consecutive_wins: 2,
    max_consecutive_losses: 1,
  },
  equity_curve: [
    { trade_date: "2024-01-02", equity: 1000000, benchmark_equity: 1000000 },
    { trade_date: "2024-01-03", equity: 1010000, benchmark_equity: 1005000 },
  ],
  period_returns: {
    monthly: [{ period: "2024-01", return_pct: 3.2 }],
    quarterly: [{ period: "2024-Q1", return_pct: 5.1 }],
    yearly: [{ period: "2024", return_pct: 12.5 }],
  },
  trades: [
    {
      entry_date: "2024-01-02",
      exit_date: "2024-01-10",
      entry_price: 100,
      exit_price: 106,
      holding_days: 8,
      return_pct: 6,
      pnl_amount: 60000,
      exit_reason: "close_below_ma",
    },
  ],
  latest_recommendation: {
    latest_signal_date: "2024-01-10",
    action: "hold",
    reason: "目前仍持有部位，且尚未出現新的出場訊號。",
  },
  warnings: ["資料區間少於策略所需的 20 日 lookback，前段資料只會累積指標，不會立即觸發訊號。"],
}

describe("BacktestPanel", () => {
  afterEach(() => {
    jest.restoreAllMocks()
  })

  it("loads templates, runs backtest, and shows advice", async () => {
    jest.spyOn(api, "fetchBacktestTemplates").mockResolvedValue([
      {
        id: "demo",
        name: "Demo",
        description: "測試模板",
        strategy_text: "demo strategy",
      },
    ])
    jest.spyOn(api, "fetchBacktestCapabilities").mockResolvedValue({
      indicators: [{ id: "close_above_ma", category: "price", label: "收盤價站上 N 日均線", examples: ["收盤價站上20日均線"] }],
      risk_controls: [{ id: "stop_loss_pct", label: "固定停損", examples: ["停損8%"] }],
      notes: ["目前只支援日線、單檔、long-only、next_open 成交。"],
    })
    const interpretSpy = jest.spyOn(api, "interpretBacktest").mockResolvedValue({
      supported: true,
      normalized_text: "買進：demo；賣出：demo",
      strategy: {
        entry_rules: [{ indicator: "close_above_ma", params: { window: 20 } }],
        exit_rules: [],
        stop_loss_pct: 8,
      },
      unsupported_conditions: [],
      ai_mapped_conditions: [],
      warnings: [],
    })
    const runSpy = jest.spyOn(api, "runBacktest").mockResolvedValue(mockRunResult)
    const adviceSpy = jest.spyOn(api, "fetchBacktestAdvice").mockResolvedValue({
      summary: "這是測試摘要",
      strengths: ["有正報酬"],
      weaknesses: ["樣本偏少"],
      rewrite_suggestions: ["加入停損"],
      risk_notes: ["留意滑價"],
      source: "heuristic",
    })

    render(<BacktestPanel stockId="2330" />)

    await waitFor(() => {
      expect(screen.getByDisplayValue("demo strategy")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: "執行回測" }))

    await waitFor(() => {
      expect(interpretSpy).toHaveBeenCalled()
      expect(runSpy).toHaveBeenCalled()
      expect(adviceSpy).toHaveBeenCalled()
    })

    expect(screen.getByText("這是測試摘要")).toBeInTheDocument()
    expect(screen.getByText("策略判讀預覽")).toBeInTheDocument()
    expect(screen.getByText("可執行回測")).toBeInTheDocument()
    expect(screen.getAllByText("Summary").length).toBeGreaterThan(0)
    expect(screen.getByText("Performance Analysis")).toBeInTheDocument()
    expect(screen.getByText("Entry DSL")).toBeInTheDocument()
    expect(screen.getAllByText("Risk Controls").length).toBeGreaterThan(0)
    expect(screen.getByText("目前可判讀的條件 catalog")).toBeInTheDocument()
    expect(screen.getByText("最大連續獲利")).toBeInTheDocument()
    expect(screen.getByText("月度報酬")).toBeInTheDocument()
    expect(screen.getByText("2024-01")).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText(/加入停損/)).toBeInTheDocument()
      expect(screen.getByTestId("echart")).toBeInTheDocument()
    })
    expect(screen.getByText(/回測提醒/)).toBeInTheDocument()
    expect(screen.getByText(/lookback/)).toBeInTheDocument()
  })

  it("shows validation error before calling API when strategy is blank", async () => {
    jest.spyOn(api, "fetchBacktestTemplates").mockResolvedValue([])
    jest.spyOn(api, "fetchBacktestCapabilities").mockResolvedValue({
      indicators: [],
      risk_controls: [],
      notes: [],
    })
    const interpretSpy = jest.spyOn(api, "interpretBacktest").mockResolvedValue({
      supported: true,
      normalized_text: "demo",
      strategy: {},
      unsupported_conditions: [],
      ai_mapped_conditions: [],
      warnings: [],
    })
    const runSpy = jest.spyOn(api, "runBacktest").mockResolvedValue(mockRunResult)

    render(<BacktestPanel stockId="2330" />)

    const textarea = screen.getByLabelText("策略文字")
    fireEvent.change(textarea, { target: { value: "   " } })
    fireEvent.click(screen.getByRole("button", { name: "執行回測" }))

    await waitFor(() => {
      expect(screen.getByText("策略文字不能空白")).toBeInTheDocument()
    })
    expect(interpretSpy).not.toHaveBeenCalled()
    expect(runSpy).not.toHaveBeenCalled()
  })

  it("shows partial-support preview and does not run backtest when strategy is unsupported", async () => {
    jest.spyOn(api, "fetchBacktestTemplates").mockResolvedValue([
      {
        id: "demo",
        name: "Demo",
        description: "測試模板",
        strategy_text: "demo strategy",
      },
    ])
    jest.spyOn(api, "fetchBacktestCapabilities").mockResolvedValue({
      indicators: [{ id: "close_above_ma", category: "price", label: "收盤價站上 N 日均線", examples: ["收盤價站上20日均線"] }],
      risk_controls: [],
      notes: [],
    })
    const interpretSpy = jest.spyOn(api, "interpretBacktest").mockResolvedValue({
      supported: false,
      normalized_text: "買進：demo；賣出：demo",
      strategy: {
        entry_rules: [],
        exit_rules: [],
      },
      unsupported_conditions: ["突破60日高點"],
      ai_mapped_conditions: [],
      warnings: ["部分條件目前不支援，因此無法直接執行這組策略。"],
    })
    const runSpy = jest.spyOn(api, "runBacktest").mockResolvedValue(mockRunResult)

    render(<BacktestPanel stockId="2330" />)

    await waitFor(() => {
      expect(screen.getByDisplayValue("demo strategy")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: "執行回測" }))

    await waitFor(() => {
      expect(interpretSpy).toHaveBeenCalled()
      expect(screen.getByText("部分支援")).toBeInTheDocument()
      expect(screen.getByText(/突破60日高點/)).toBeInTheDocument()
    })

    expect(screen.getByText("這句策略目前只能部分判讀，請先調整不支援的條件。")).toBeInTheDocument()
    expect(runSpy).not.toHaveBeenCalled()
  })

  it("shows detailed translated error when interpret rejects with backend detail", async () => {
    jest.spyOn(api, "fetchBacktestTemplates").mockResolvedValue([
      {
        id: "demo",
        name: "Demo",
        description: "測試模板",
        strategy_text: "demo strategy",
      },
    ])
    jest.spyOn(api, "fetchBacktestCapabilities").mockResolvedValue({
      indicators: [],
      risk_controls: [],
      notes: [],
    })
    jest.spyOn(api, "interpretBacktest").mockRejectedValue(
      new Error("Failed to interpret backtest: Unsupported strategy conditions: 突破60日高點")
    )

    render(<BacktestPanel stockId="2330" />)

    await waitFor(() => {
      expect(screen.getByDisplayValue("demo strategy")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: "執行回測" }))

    await waitFor(() => {
      expect(screen.getByText("目前不支援這些條件：突破60日高點")).toBeInTheDocument()
    })
  })

  it("shows AI mapped conditions badge when interpret returns ai_mapped_conditions", async () => {
    jest.spyOn(api, "fetchBacktestTemplates").mockResolvedValue([
      { id: "demo", name: "Demo", description: "測試模板", strategy_text: "demo strategy" },
    ])
    jest.spyOn(api, "fetchBacktestCapabilities").mockResolvedValue({
      indicators: [{ id: "close_above_ma", category: "price", label: "收盤價站上 N 日均線", examples: [] }],
      risk_controls: [],
      notes: [],
    })
    jest.spyOn(api, "interpretBacktest").mockResolvedValue({
      supported: true,
      normalized_text: "買進：收盤價站上 N 日均線；賣出：demo",
      strategy: { entry_rules: [], exit_rules: [] },
      unsupported_conditions: [],
      ai_mapped_conditions: ["close_above_ma"],
      warnings: [],
    })
    jest.spyOn(api, "runBacktest").mockResolvedValue({
      ...mockRunResult,
      ai_mapped_conditions: ["close_above_ma"],
    })
    jest.spyOn(api, "fetchBacktestAdvice").mockResolvedValue({
      summary: "AI 測試摘要",
      strengths: [],
      weaknesses: [],
      rewrite_suggestions: [],
      risk_notes: [],
      source: "heuristic",
    })

    render(<BacktestPanel stockId="2330" />)

    await waitFor(() => {
      expect(screen.getByDisplayValue("demo strategy")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: "執行回測" }))

    await waitFor(() => {
      expect(screen.getByText("AI 補充解析的條件")).toBeInTheDocument()
      expect(screen.getAllByText(/收盤價站上 N 日均線/).length).toBeGreaterThanOrEqual(1)
    })
  })
})
