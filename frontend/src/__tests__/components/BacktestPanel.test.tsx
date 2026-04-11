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
  },
  equity_curve: [
    { trade_date: "2024-01-02", equity: 1000000, benchmark_equity: 1000000 },
    { trade_date: "2024-01-03", equity: 1010000, benchmark_equity: 1005000 },
  ],
  period_returns: {
    monthly: [],
    quarterly: [],
    yearly: [],
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
      expect(runSpy).toHaveBeenCalled()
      expect(adviceSpy).toHaveBeenCalled()
    })

    expect(screen.getByText("這是測試摘要")).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText(/加入停損/)).toBeInTheDocument()
      expect(screen.getByTestId("echart")).toBeInTheDocument()
    })
    expect(screen.getByText(/回測提醒/)).toBeInTheDocument()
    expect(screen.getByText(/lookback/)).toBeInTheDocument()
  })

  it("shows validation error before calling API when strategy is blank", async () => {
    jest.spyOn(api, "fetchBacktestTemplates").mockResolvedValue([])
    const runSpy = jest.spyOn(api, "runBacktest").mockResolvedValue(mockRunResult)

    render(<BacktestPanel stockId="2330" />)

    const textarea = screen.getByLabelText("策略文字")
    fireEvent.change(textarea, { target: { value: "   " } })
    fireEvent.click(screen.getByRole("button", { name: "執行回測" }))

    await waitFor(() => {
      expect(screen.getByText("策略文字不能空白")).toBeInTheDocument()
    })
    expect(runSpy).not.toHaveBeenCalled()
  })
})
