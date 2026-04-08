import React from "react"
import { render, screen, waitFor } from "@testing-library/react"
import StockChart from "@/components/StockChart"
import * as api from "@/lib/api"

// Mock next/navigation
const mockBack = jest.fn()
jest.mock("next/navigation", () => ({
  useRouter: () => ({ back: mockBack }),
}))

// Mock echarts-for-react to avoid canvas issues in jsdom
jest.mock("echarts-for-react", () => {
  return function MockECharts({ option }: { option: Record<string, unknown> }) {
    return <div data-testid="echart">{JSON.stringify(option).slice(0, 100)}</div>
  }
})

const MOCK_HISTORY: api.StockHistoryResponse = {
  stock_id: "2330",
  stock_name: "台積電",
  industry_name: "半導體",
  sub_industry: "IC 製造",
  history: [
    {
      trade_date: "2026-03-01",
      open_price: 835,
      high_price: 845,
      low_price: 830,
      close_price: 840,
      foreign_net_shares: 3000,
      trust_net_shares: 500,
      dealer_net_shares: -200,
      foreign_cumulative: 3000,
      trust_cumulative: 500,
      dealer_cumulative: -200,
    },
    {
      trade_date: "2026-03-02",
      open_price: 842,
      high_price: 855,
      low_price: 840,
      close_price: 850,
      foreign_net_shares: 2000,
      trust_net_shares: 500,
      dealer_net_shares: -300,
      foreign_cumulative: 5000,
      trust_cumulative: 1000,
      dealer_cumulative: -500,
    },
  ],
}

describe("StockChart", () => {
  afterEach(() => {
    jest.restoreAllMocks()
    mockBack.mockReset()
  })

  it("shows loading state then renders chart and header", async () => {
    jest.spyOn(api, "fetchStockHistory").mockResolvedValue(MOCK_HISTORY)

    render(<StockChart stockId="2330" />)

    // Skeleton shown while loading — chart not yet visible
    expect(screen.queryByTestId("echart")).not.toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText(/2330/)).toBeInTheDocument()
      expect(screen.getByText(/台積電/)).toBeInTheDocument()
      expect(screen.getByTestId("echart")).toBeInTheDocument()
    })
  })

  it("shows error message when fetch fails", async () => {
    jest.spyOn(api, "fetchStockHistory").mockRejectedValue(new Error("Stock not found: 9999"))

    render(<StockChart stockId="9999" />)

    await waitFor(() => {
      expect(screen.getByText(/Stock not found/)).toBeInTheDocument()
    })
  })

  it("shows sub_industry label when present", async () => {
    jest.spyOn(api, "fetchStockHistory").mockResolvedValue(MOCK_HISTORY)

    render(<StockChart stockId="2330" />)

    await waitFor(() => {
      expect(screen.getByText("IC 製造")).toBeInTheDocument()
    })
  })

  it("shows industry_name as fallback when sub_industry is null", async () => {
    const noSubIndustry = { ...MOCK_HISTORY, sub_industry: null }
    jest.spyOn(api, "fetchStockHistory").mockResolvedValue(noSubIndustry)

    render(<StockChart stockId="2330" />)

    await waitFor(() => {
      expect(screen.getByText("半導體")).toBeInTheDocument()
    })
  })

  it("shows empty-data message when history is empty", async () => {
    jest.spyOn(api, "fetchStockHistory").mockResolvedValue({
      ...MOCK_HISTORY,
      history: [],
    })

    render(<StockChart stockId="2330" />)

    await waitFor(() => {
      expect(screen.getByText(/此區間無資料/)).toBeInTheDocument()
    })
  })

  it("defaults to 90 days", async () => {
    const spy = jest.spyOn(api, "fetchStockHistory").mockResolvedValue(MOCK_HISTORY)

    render(<StockChart stockId="2330" />)

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith("2330", 90, undefined)
    })
  })

  it("passes custom days and endDate to fetchStockHistory", async () => {
    const spy = jest.spyOn(api, "fetchStockHistory").mockResolvedValue(MOCK_HISTORY)

    render(<StockChart stockId="2330" days={30} defaultDate="2026-04-01" />)

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith("2330", 30, "2026-04-01")
    })
  })

  it("calls router.back when back button is clicked", async () => {
    jest.spyOn(api, "fetchStockHistory").mockResolvedValue(MOCK_HISTORY)

    render(<StockChart stockId="2330" />)

    const backBtn = screen.getByText(/返回/)
    backBtn.click()
    expect(mockBack).toHaveBeenCalled()
  })
})
