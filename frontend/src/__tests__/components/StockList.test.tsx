import React from "react"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import StockList from "@/components/StockList"
import * as api from "@/lib/api"

// Mock next/navigation
const mockPush = jest.fn()
const mockBack = jest.fn()
const mockReplace = jest.fn()
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, back: mockBack, replace: mockReplace }),
  useSearchParams: () => new URLSearchParams(),
}))

const MOCK_SUMMARY: api.SubIndustrySummaryItem[] = [
  {
    sub_industry: "晶圓製造",
    chain: "上游",
    total_net_amount: 4_675_000_000,
    foreign_net_amount: 4_140_000_000,
    trust_net_amount: 866_500_000,
    dealer_net_amount: -419_500_000,
    streak: 5,
  },
  {
    sub_industry: "IC封裝測試",
    chain: "下游",
    total_net_amount: 165_000_000,
    foreign_net_amount: 150_000_000,
    trust_net_amount: 30_000_000,
    dealer_net_amount: -15_000_000,
    streak: -1,
  },
]

const MOCK_STOCKS: api.StockFlowItem[] = [
  {
    stock_id: "2330",
    stock_name: "台積電",
    industry_name: "半導體",
    chain: "上游",
    sub_industry: "晶圓製造",
    close_price: 850.0,
    prev_close_price: 840.0,
    price_change: 10.0,
    price_change_pct: 1.19,
    foreign_net_shares: 5000,
    trust_net_shares: 1000,
    dealer_net_shares: -500,
    foreign_net_amount: 4_250_000_000,
    trust_net_amount: 850_000_000,
    dealer_net_amount: -425_000_000,
  },
  {
    stock_id: "2303",
    stock_name: "聯電",
    industry_name: "半導體",
    chain: "上游",
    sub_industry: "晶圓製造",
    close_price: 55.0,
    prev_close_price: 56.0,
    price_change: -1.0,
    price_change_pct: -1.79,
    foreign_net_shares: -2000,
    trust_net_shares: 300,
    dealer_net_shares: 100,
    foreign_net_amount: -110_000_000,
    trust_net_amount: 16_500_000,
    dealer_net_amount: 5_500_000,
  },
  {
    stock_id: "3711",
    stock_name: "日月光投控",
    industry_name: "半導體",
    chain: "下游",
    sub_industry: "IC封裝測試",
    close_price: 150.0,
    prev_close_price: 148.0,
    price_change: 2.0,
    price_change_pct: 1.35,
    foreign_net_shares: 1000,
    trust_net_shares: 200,
    dealer_net_shares: -100,
    foreign_net_amount: 150_000_000,
    trust_net_amount: 30_000_000,
    dealer_net_amount: -15_000_000,
  },
]

describe("StockList", () => {
  afterEach(() => {
    jest.restoreAllMocks()
    mockPush.mockReset()
    mockBack.mockReset()
  })

  function mockBothApis(stocks = MOCK_STOCKS, summary = MOCK_SUMMARY) {
    jest.spyOn(api, "fetchIndustryStocks").mockResolvedValue(stocks)
    jest.spyOn(api, "fetchSubIndustrySummary").mockResolvedValue(summary)
  }

  it("shows loading state then renders stock cards", async () => {
    mockBothApis()

    render(<StockList industryName="半導體" defaultDate="2026-04-01" />)

    // Skeleton shown while loading — data not yet visible
    expect(screen.queryByText("台積電")).not.toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText("台積電")).toBeInTheDocument()
      expect(screen.getByText("聯電")).toBeInTheDocument()
      expect(screen.getByText("日月光投控")).toBeInTheDocument()
    })
  })

  it("groups stocks by chain", async () => {
    mockBothApis()

    render(<StockList industryName="半導體" defaultDate="2026-04-01" />)

    await waitFor(() => {
      // "上游"/"下游" appear in both summary table and card section headers
      expect(screen.getAllByText("上游").length).toBeGreaterThanOrEqual(1)
      expect(screen.getAllByText("下游").length).toBeGreaterThanOrEqual(1)
    })
  })

  it("shows error message when fetch fails", async () => {
    jest.spyOn(api, "fetchIndustryStocks").mockRejectedValue(new Error("Industry not found"))
    jest.spyOn(api, "fetchSubIndustrySummary").mockRejectedValue(new Error("Industry not found"))

    render(<StockList industryName="不存在" defaultDate="2026-04-01" />)

    await waitFor(() => {
      expect(screen.getByText(/Industry not found/)).toBeInTheDocument()
    })
  })

  it("shows no-data message when API returns empty", async () => {
    mockBothApis([], [])

    render(<StockList industryName="半導體" defaultDate="2026-04-07" />)

    await waitFor(() => {
      expect(screen.getByText(/此日期無資料/)).toBeInTheDocument()
    })
  })

  it("displays industry name as page title", async () => {
    mockBothApis([], [])

    render(<StockList industryName="半導體" defaultDate="2026-04-01" />)

    expect(screen.getByText("半導體")).toBeInTheDocument()
  })

  it("navigates to stock detail when card is clicked", async () => {
    mockBothApis()

    render(<StockList industryName="半導體" defaultDate="2026-04-01" />)
    await waitFor(() => screen.getByText("台積電"))

    fireEvent.click(screen.getByText("台積電"))
    expect(mockPush).toHaveBeenCalledWith("/stocks/2330?date=2026-04-01")
  })

  it("calls router.back when back button is clicked", async () => {
    mockBothApis([], [])

    render(<StockList industryName="半導體" defaultDate="2026-04-01" />)

    fireEvent.click(screen.getByText(/返回/))
    expect(mockBack).toHaveBeenCalled()
  })

  it("displays close price on cards", async () => {
    mockBothApis()

    render(<StockList industryName="半導體" defaultDate="2026-04-01" />)

    await waitFor(() => {
      expect(screen.getByText("850.00")).toBeInTheDocument()
      expect(screen.getByText("55.00")).toBeInTheDocument()
    })
  })

  it("shows stock count in header", async () => {
    mockBothApis()

    render(<StockList industryName="半導體" defaultDate="2026-04-01" />)

    await waitFor(() => {
      expect(screen.getByText("3 檔")).toBeInTheDocument()
    })
  })

  it("shows institutional flow labels on cards", async () => {
    mockBothApis()

    render(<StockList industryName="半導體" defaultDate="2026-04-01" />)

    await waitFor(() => {
      const foreignLabels = screen.getAllByText("外資")
      expect(foreignLabels.length).toBeGreaterThan(0)
    })
  })

  it("renders sub-industry summary table with streak", async () => {
    mockBothApis()

    render(<StockList industryName="半導體" defaultDate="2026-04-01" />)

    await waitFor(() => {
      expect(screen.getByText("子產業彙總")).toBeInTheDocument()
      // "晶圓製造" appears in both summary table and cards, so use getAllByText
      expect(screen.getAllByText("晶圓製造").length).toBeGreaterThanOrEqual(1)
      expect(screen.getAllByText("IC封裝測試").length).toBeGreaterThanOrEqual(1)
      expect(screen.getByText("連買5天")).toBeInTheDocument()
      expect(screen.getByText("連賣1天")).toBeInTheDocument()
    })
  })

  it("filters cards when clicking a sub-industry row", async () => {
    mockBothApis()

    render(<StockList industryName="半導體" defaultDate="2026-04-01" />)
    await waitFor(() => expect(screen.getAllByText("晶圓製造").length).toBeGreaterThanOrEqual(1))

    // Click "IC封裝測試" in the summary table (first match)
    const summaryRows = screen.getAllByText("IC封裝測試")
    fireEvent.click(summaryRows[0])

    await waitFor(() => {
      // Filter active — should show clear button
      expect(screen.getByText(/清除篩選/)).toBeInTheDocument()
      // Only 日月光投控 should remain (IC封裝測試)
      expect(screen.getByText("日月光投控")).toBeInTheDocument()
      // 台積電 and 聯電 (晶圓製造) should be filtered out
      expect(screen.queryByText("台積電")).not.toBeInTheDocument()
    })
  })

  it("applies red tint class to cards with positive price change", async () => {
    mockBothApis()

    render(<StockList industryName="半導體" defaultDate="2026-04-01" />)
    await waitFor(() => screen.getByText("台積電"))

    // 台積電: price_change 10.0 (positive) → border-red-900/40
    const card = screen.getByText("台積電").closest("[class*='border-red-900']")
    expect(card).toBeTruthy()
  })

  it("applies green tint class to cards with negative price change", async () => {
    mockBothApis()

    render(<StockList industryName="半導體" defaultDate="2026-04-01" />)
    await waitFor(() => screen.getByText("聯電"))

    // 聯電: price_change -1.0 (negative) → border-green-900/40
    const card = screen.getByText("聯電").closest("[class*='border-green-900']")
    expect(card).toBeTruthy()
  })

  it("clears filter when clicking clear button", async () => {
    mockBothApis()

    render(<StockList industryName="半導體" defaultDate="2026-04-01" />)
    await waitFor(() => expect(screen.getAllByText("晶圓製造").length).toBeGreaterThanOrEqual(1))

    // Apply filter
    const summaryRows = screen.getAllByText("IC封裝測試")
    fireEvent.click(summaryRows[0])
    await waitFor(() => screen.getByText(/清除篩選/))

    // Clear filter
    fireEvent.click(screen.getByText(/清除篩選/))

    await waitFor(() => {
      // All stocks should be visible again
      expect(screen.getByText("台積電")).toBeInTheDocument()
      expect(screen.getByText("日月光投控")).toBeInTheDocument()
    })
  })
})
