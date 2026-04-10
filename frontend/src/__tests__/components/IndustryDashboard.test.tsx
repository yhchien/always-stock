import React from "react"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import IndustryDashboard from "@/components/IndustryDashboard"
import * as api from "@/lib/api"

const MOCK_ROWS: api.IndustryFlowItem[] = [
  {
    industry_name: "半導體",
    total_net_amount: 2_000_000_000,
    foreign_net_amount: 1_500_000_000,
    trust_net_amount: 300_000_000,
    dealer_net_amount: 200_000_000,
    total_buy_amount: 5_000_000_000,
    total_sell_amount: 3_000_000_000,
    streak: 3,
  },
  {
    industry_name: "電子零組件",
    total_net_amount: -500_000_000,
    foreign_net_amount: -400_000_000,
    trust_net_amount: -50_000_000,
    dealer_net_amount: -50_000_000,
    total_buy_amount: 1_000_000_000,
    total_sell_amount: 1_500_000_000,
    streak: -2,
  },
]

describe("IndustryDashboard", () => {
  afterEach(() => jest.restoreAllMocks())

  it("shows loading state then renders table rows", async () => {
    jest.spyOn(api, "fetchIndustries").mockResolvedValue(MOCK_ROWS)

    render(<IndustryDashboard defaultDate="2026-04-01" />)

    // Skeleton is shown while loading — data not yet visible
    expect(screen.queryByText("半導體")).not.toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText("半導體")).toBeInTheDocument()
      expect(screen.getByText("電子零組件")).toBeInTheDocument()
    })
  })

  it("shows error message when fetch fails", async () => {
    jest.spyOn(api, "fetchIndustries").mockRejectedValue(new Error("No data for 2026-04-07"))

    render(<IndustryDashboard defaultDate="2026-04-07" />)

    await waitFor(() => {
      expect(screen.getByText(/No data for/)).toBeInTheDocument()
    })
  })

  it("shows no-data message when API returns empty array", async () => {
    jest.spyOn(api, "fetchIndustries").mockResolvedValue([])

    render(<IndustryDashboard defaultDate="2026-04-07" />)

    await waitFor(() => {
      expect(screen.getByText(/此日期無資料/)).toBeInTheDocument()
    })
  })

  it("re-fetches when date input changes", async () => {
    const spy = jest.spyOn(api, "fetchIndustries").mockResolvedValue(MOCK_ROWS)

    render(<IndustryDashboard defaultDate="2026-04-01" />)
    await waitFor(() => expect(spy).toHaveBeenCalledWith("2026-04-01", expect.any(Object)))

    const input = screen.getByDisplayValue("2026-04-01")
    fireEvent.change(input, { target: { value: "2026-03-31" } })

    await waitFor(() => expect(spy).toHaveBeenCalledWith("2026-03-31", expect.any(Object)))
  })

  it("shows friendly busy message for 503 errors", async () => {
    jest.spyOn(api, "fetchIndustries").mockRejectedValue(new Error("Failed to fetch industries: 503"))

    render(<IndustryDashboard defaultDate="2026-04-08" />)

    await waitFor(() => {
      expect(screen.getByText("資料庫忙碌中，請稍後再試")).toBeInTheDocument()
    })
  })

  it("calls onSelectIndustry when a row is clicked", async () => {
    jest.spyOn(api, "fetchIndustries").mockResolvedValue(MOCK_ROWS)
    const onSelect = jest.fn()

    render(<IndustryDashboard defaultDate="2026-04-01" onSelectIndustry={onSelect} />)
    await waitFor(() => screen.getByText("半導體"))

    fireEvent.click(screen.getByText("半導體"))
    expect(onSelect).toHaveBeenCalledWith("半導體")
  })

  it("displays amounts in 億 with correct sign colors", async () => {
    jest.spyOn(api, "fetchIndustries").mockResolvedValue(MOCK_ROWS)

    render(<IndustryDashboard defaultDate="2026-04-01" />)
    await waitFor(() => screen.getByText("半導體"))

    // Positive total → +20.00億
    expect(screen.getByText("+20.00億")).toBeInTheDocument()
    // Negative total → -5.00億
    expect(screen.getByText("-5.00億")).toBeInTheDocument()
  })

  it("renders all four tabs", async () => {
    jest.spyOn(api, "fetchIndustries").mockResolvedValue([])

    render(<IndustryDashboard defaultDate="2026-04-01" />)

    expect(screen.getByRole("tab", { name: "合計" })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "外資" })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "投信" })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "自營商" })).toBeInTheDocument()
  })

  it("displays streak column with correct text", async () => {
    jest.spyOn(api, "fetchIndustries").mockResolvedValue(MOCK_ROWS)

    render(<IndustryDashboard defaultDate="2026-04-01" />)

    await waitFor(() => {
      expect(screen.getByText("連買3天")).toBeInTheDocument()
      expect(screen.getByText("連賣2天")).toBeInTheDocument()
    })
  })

  it("filters industries by search term", async () => {
    jest.spyOn(api, "fetchIndustries").mockResolvedValue(MOCK_ROWS)

    render(<IndustryDashboard defaultDate="2026-04-01" />)
    await waitFor(() => screen.getByText("半導體"))

    fireEvent.change(screen.getByPlaceholderText("搜尋產業..."), { target: { value: "半導體" } })

    expect(screen.getByText("半導體")).toBeInTheDocument()
    expect(screen.queryByText("電子零組件")).not.toBeInTheDocument()
  })

  it("shows result count X / Y when search is active", async () => {
    jest.spyOn(api, "fetchIndustries").mockResolvedValue(MOCK_ROWS)

    render(<IndustryDashboard defaultDate="2026-04-01" />)
    await waitFor(() => screen.getByText("半導體"))

    fireEvent.change(screen.getByPlaceholderText("搜尋產業..."), { target: { value: "半導體" } })

    expect(screen.getByText("1 / 2")).toBeInTheDocument()
  })

  it("clears search and restores all rows when × is clicked", async () => {
    jest.spyOn(api, "fetchIndustries").mockResolvedValue(MOCK_ROWS)

    render(<IndustryDashboard defaultDate="2026-04-01" />)
    await waitFor(() => screen.getByText("半導體"))

    fireEvent.change(screen.getByPlaceholderText("搜尋產業..."), { target: { value: "半導體" } })
    expect(screen.queryByText("電子零組件")).not.toBeInTheDocument()

    fireEvent.click(screen.getByLabelText("清除搜尋"))

    expect(screen.getByText("電子零組件")).toBeInTheDocument()
  })

  it("renders sortable column headers", async () => {
    jest.spyOn(api, "fetchIndustries").mockResolvedValue(MOCK_ROWS)

    render(<IndustryDashboard defaultDate="2026-04-01" />)

    await waitFor(() => screen.getByText("半導體"))

    // Column headers should contain sort indicator for active column
    const headerCells = screen.getAllByRole("columnheader")
    const headerTexts = headerCells.map((h) => h.textContent)
    // "合計" is the default sort key, should have ▼ indicator
    expect(headerTexts.some((t) => t?.includes("合計") && t?.includes("▼"))).toBe(true)
  })

  it("toggles sort direction when clicking active column header", async () => {
    jest.spyOn(api, "fetchIndustries").mockResolvedValue(MOCK_ROWS)

    render(<IndustryDashboard defaultDate="2026-04-01" />)
    await waitFor(() => screen.getByText("半導體"))

    // Click "合計" header to toggle ascending
    const totalHeader = screen.getAllByRole("columnheader").find((h) => h.textContent?.includes("合計"))
    fireEvent.click(totalHeader!)

    // After clicking, sort should flip — 電子零組件 (negative) should be first
    const rows = screen.getAllByRole("row")
    // rows[0] is header, rows[1] is first data row
    expect(rows[1].textContent).toContain("電子零組件")
  })
})
