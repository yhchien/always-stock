import {
  fetchBacktestAdvice,
  fetchBacktestCapabilities,
  interpretBacktest,
  fetchBacktestTemplates,
  fmtAmount,
  fmtShares,
  fmtStreak,
  fetchBrokerTrades,
  fetchIndustries,
  fetchIndustryStocks,
  fetchLatestTradeDate,
  fetchMarketHotMoney,
  fetchRealtimeQuotes,
  resetClientCacheForTests,
  fetchStockHistory,
  fetchSubIndustrySummary,
  runBacktest,
} from "@/lib/api"

// ── fmtAmount ────────────────────────────────────────────────────────────────

describe("fmtAmount", () => {
  it("formats positive value with + prefix in 億", () => {
    expect(fmtAmount(1_000_000_000)).toBe("+10.00億")
  })

  it("formats negative value without + prefix", () => {
    expect(fmtAmount(-500_000_000)).toBe("-5.00億")
  })

  it("formats zero with + prefix", () => {
    expect(fmtAmount(0)).toBe("+0.00億")
  })

  it("rounds to 2 decimal places", () => {
    expect(fmtAmount(123_456_789)).toBe("+1.23億")
  })
})

// ── fmtShares ────────────────────────────────────────────────────────────────

describe("fmtShares", () => {
  it("formats positive shares in 萬", () => {
    expect(fmtShares(50_000)).toBe("+5萬")
  })

  it("formats negative shares without + prefix", () => {
    expect(fmtShares(-20_000)).toBe("-2萬")
  })

  it("rounds to 0 decimal places", () => {
    expect(fmtShares(12_345)).toBe("+1萬")
  })
})

// ── fmtStreak ───────────────────────────────────────────────────────────────

describe("fmtStreak", () => {
  it("formats positive streak as consecutive buy days", () => {
    expect(fmtStreak(5)).toBe("連買5天")
  })

  it("formats negative streak as consecutive sell days", () => {
    expect(fmtStreak(-3)).toBe("連賣3天")
  })

  it("formats zero streak as dash", () => {
    expect(fmtStreak(0)).toBe("-")
  })

  it("formats streak of 31 as 30+", () => {
    expect(fmtStreak(31)).toBe("連買30+天")
    expect(fmtStreak(-31)).toBe("連賣30+天")
  })

  it("formats streak of 30 normally", () => {
    expect(fmtStreak(30)).toBe("連買30天")
    expect(fmtStreak(-30)).toBe("連賣30天")
  })
})

// ── fetchSubIndustrySummary ─────────────────────────────────────────────────

describe("fetchSubIndustrySummary", () => {
  afterEach(() => jest.restoreAllMocks())

  it("returns parsed JSON on success", async () => {
    const mockData = [{ sub_industry: "晶圓製造", total_net_amount: 1e9, streak: 3 }]
    const mockFetch = jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => mockData,
    } as Response)

    const result = await fetchSubIndustrySummary("半導體", "2026-04-01")
    expect(result).toEqual(mockData)
    expect(mockFetch.mock.calls.at(-1)?.[0]).toEqual(expect.stringContaining("/summary"))
    expect(mockFetch.mock.calls.at(-1)?.[0]).toEqual(expect.stringContaining("date=2026-04-01"))
  })

  it("encodes industry name in URL", async () => {
    const mockFetch = jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => [],
    } as Response)

    await fetchSubIndustrySummary("IC 設計", "2026-04-01")
    expect(mockFetch.mock.calls.at(-1)?.[0]).toEqual(expect.stringContaining("IC%20%E8%A8%AD%E8%A8%88"))
  })

  it("throws on non-ok response", async () => {
    jest.spyOn(global, "fetch").mockResolvedValue({
      ok: false,
      status: 404,
    } as Response)

    await expect(fetchSubIndustrySummary("半導體", "2026-04-01")).rejects.toThrow("404")
  })
})

// ── fetchIndustries ──────────────────────────────────────────────────────────

describe("fetchIndustries", () => {
  afterEach(() => {
    jest.restoreAllMocks()
    window.sessionStorage.clear()
    resetClientCacheForTests()
  })

  it("returns parsed JSON on success", async () => {
    const mockData = [{ industry_name: "半導體", total_net_amount: 1e9 }]
    const mockFetch = jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => mockData,
    } as Response)

    const result = await fetchIndustries("2026-04-01")
    expect(result).toEqual(mockData)
    expect(mockFetch.mock.calls.at(-1)?.[0]).toEqual(expect.stringContaining("date=2026-04-01"))
  })

  it("throws on non-ok response", async () => {
    jest.spyOn(global, "fetch").mockResolvedValue({
      ok: false,
      status: 404,
    } as Response)

    await expect(fetchIndustries("2026-04-01")).rejects.toThrow("404")
  })

  it("reuses client cache for the same date", async () => {
    const mockData = [{ industry_name: "半導體", total_net_amount: 1e9 }]
    const mockFetch = jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => mockData,
    } as Response)

    const countIndustryCalls = () =>
      mockFetch.mock.calls.filter(([url]) => String(url).includes("/api/industries?date=2026-04-01")).length

    const before = countIndustryCalls()
    await fetchIndustries("2026-04-01")
    const afterFirst = countIndustryCalls()
    await fetchIndustries("2026-04-01")

    expect(afterFirst).toBeGreaterThan(before)
    expect(countIndustryCalls()).toBe(afterFirst)
  })
})

describe("home bootstrap caches", () => {
  afterEach(() => {
    jest.restoreAllMocks()
    window.sessionStorage.clear()
    resetClientCacheForTests()
  })

  it("caches latest trade date", async () => {
    const mockFetch = jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ trade_date: "2026-04-30" }),
    } as Response)

    const countCalls = () =>
      mockFetch.mock.calls.filter(([url]) => String(url).includes("/api/market/latest-trade-date")).length

    const before = countCalls()
    await fetchLatestTradeDate()
    const afterFirst = countCalls()
    await fetchLatestTradeDate()

    expect(afterFirst).toBeGreaterThan(before)
    expect(countCalls()).toBe(afterFirst)
  })

  it("caches market hot money by date window", async () => {
    const mockFetch = jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ start_date: "2026-04-28", end_date: "2026-04-30", trade_dates: [], items: [] }),
    } as Response)

    const countCalls = () =>
      mockFetch.mock.calls.filter(([url]) => String(url).includes("/api/market/hot-money?date=2026-04-30")).length

    const before = countCalls()
    await fetchMarketHotMoney("2026-04-30", 3, 20)
    const afterFirst = countCalls()
    await fetchMarketHotMoney("2026-04-30", 3, 20)

    expect(afterFirst).toBeGreaterThan(before)
    expect(countCalls()).toBe(afterFirst)
  })
})

// ── fetchIndustryStocks ───────────────────────────────────────────────────────

describe("fetchIndustryStocks", () => {
  afterEach(() => jest.restoreAllMocks())

  it("encodes industry name in URL", async () => {
    const mockFetch = jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => [],
    } as Response)

    await fetchIndustryStocks("IC 設計", "2026-04-01")
    expect(mockFetch.mock.calls.at(-1)?.[0]).toEqual(expect.stringContaining("IC%20%E8%A8%AD%E8%A8%88"))
  })

  it("throws on non-ok response", async () => {
    jest.spyOn(global, "fetch").mockResolvedValue({
      ok: false,
      status: 404,
    } as Response)

    await expect(fetchIndustryStocks("半導體", "2026-04-01")).rejects.toThrow("404")
  })
})

// ── fetchStockHistory ─────────────────────────────────────────────────────────

describe("fetchStockHistory", () => {
  afterEach(() => jest.restoreAllMocks())

  it("includes days param in URL", async () => {
    const mockData = { stock_id: "2330", stock_name: "台積電", industry_name: "半導體", sub_industry: null, history: [] }
    const mockFetch = jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => mockData,
    } as Response)

    await fetchStockHistory("2330", 30)
    expect(mockFetch.mock.calls.at(-1)?.[0]).toEqual(expect.stringContaining("days=30"))
  })

  it("includes end_date param when provided", async () => {
    const mockFetch = jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ history: [] }),
    } as Response)

    await fetchStockHistory("2330", 60, "2026-04-01")
    expect(mockFetch.mock.calls.at(-1)?.[0]).toEqual(expect.stringContaining("end_date=2026-04-01"))
  })

  it("throws on non-ok response", async () => {
    jest.spyOn(global, "fetch").mockResolvedValue({
      ok: false,
      status: 404,
    } as Response)

    await expect(fetchStockHistory("9999")).rejects.toThrow("404")
  })
})

describe("fetchBacktestTemplates", () => {
  afterEach(() => jest.restoreAllMocks())

  it("returns parsed template list", async () => {
    const mockData = [{ id: "demo", name: "Demo", description: "desc", strategy_text: "text" }]
    jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => mockData,
    } as Response)

    const result = await fetchBacktestTemplates()
    expect(result).toEqual(mockData)
  })
})

describe("fetchBacktestCapabilities", () => {
  afterEach(() => jest.restoreAllMocks())

  it("returns parsed capability catalog", async () => {
    const mockData = {
      indicators: [{ id: "close_above_ma", category: "price", label: "收盤價站上 N 日均線", examples: ["收盤價站上20日均線"] }],
      risk_controls: [{ id: "stop_loss_pct", label: "固定停損", examples: ["停損8%"] }],
      notes: ["note"],
    }
    jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => mockData,
    } as Response)

    const result = await fetchBacktestCapabilities()
    expect(result).toEqual(mockData)
  })
})

describe("runBacktest", () => {
  afterEach(() => jest.restoreAllMocks())

  it("posts request payload to the backtest endpoint", async () => {
    const payload = {
      stock_id: "2330",
      start_date: "2024-01-01",
      end_date: "2024-12-31",
      initial_capital: 1000000,
      strategy_text: "demo strategy",
    }
    const mockFetch = jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ supported: true }),
    } as Response)

    await runBacktest(payload)

    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/backtest/run"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(payload),
      })
    )
  })
})

describe("interpretBacktest", () => {
  afterEach(() => jest.restoreAllMocks())

  it("posts request payload to the interpret endpoint", async () => {
    const payload = {
      stock_id: "2330",
      start_date: "2024-01-01",
      end_date: "2024-12-31",
      initial_capital: 1000000,
      strategy_text: "demo strategy",
    }
    const mockFetch = jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ supported: true, normalized_text: "demo", strategy: {}, unsupported_conditions: [], ai_mapped_conditions: [], warnings: [] }),
    } as Response)

    await interpretBacktest(payload)

    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/backtest/interpret"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(payload),
      })
    )
  })

  it("surfaces backend detail in thrown error", async () => {
    jest.spyOn(global, "fetch").mockResolvedValue({
      ok: false,
      json: async () => ({ detail: "Unsupported strategy conditions: 突破60日高點" }),
      status: 422,
    } as Response)

    await expect(
      interpretBacktest({
        stock_id: "2330",
        start_date: "2024-01-01",
        end_date: "2024-12-31",
        initial_capital: 1000000,
        strategy_text: "demo strategy",
      })
    ).rejects.toThrow("Unsupported strategy conditions: 突破60日高點")
  })
})

describe("fetchBacktestAdvice", () => {
  afterEach(() => jest.restoreAllMocks())

  it("posts advice payload to the advice endpoint", async () => {
    const payload = {
      stock_id: "2330",
      strategy_text: "demo strategy",
      normalized_text: "demo normalized",
      metrics: {
        total_return_pct: 1,
        annual_return_pct: 1,
        win_rate_pct: 50,
        max_drawdown_pct: -2,
        sharpe_ratio: 1,
        trade_count: 3,
        ending_equity: 1010000,
        benchmark_return_pct: 0.5,
        excess_return_pct: 0.5,
        avg_trade_return_pct: 1,
        avg_holding_days: 10,
        profit_factor: 1.2,
        avg_gain_pct: 2,
        avg_loss_pct: -1,
        max_consecutive_wins: 2,
        max_consecutive_losses: 1,
      },
      trades: [],
      latest_recommendation: {
        latest_signal_date: "2024-01-30",
        action: "hold" as const,
        reason: "demo",
      },
    }
    const mockFetch = jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ summary: "ok", strengths: [], weaknesses: [], rewrite_suggestions: [], risk_notes: [], source: "heuristic" }),
    } as Response)

    await fetchBacktestAdvice(payload)

    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/backtest/advice"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(payload),
      })
    )
  })
})

// ── fetchRealtimeQuotes ──────────────────────────────────────────────────────

describe("fetchRealtimeQuotes", () => {
  afterEach(() => jest.restoreAllMocks())

  it("splits requests into batches of 50", async () => {
    const mockFetch = jest.spyOn(global, "fetch")
    mockFetch.mockClear()
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ stock_id: "1101" }],
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ stock_id: "9999" }],
      } as Response)

    const ids = Array.from({ length: 51 }, (_, i) => String(1000 + i))
    const result = await fetchRealtimeQuotes(ids)

    expect(mockFetch).toHaveBeenCalledTimes(2)
    expect(mockFetch.mock.calls[0][0]).toEqual(expect.stringContaining("stock_ids=1000,1001"))
    expect(mockFetch.mock.calls[1][0]).toEqual(expect.stringContaining("stock_ids=1050"))
    expect(result).toEqual([{ stock_id: "1101" }, { stock_id: "9999" }])
  })
})

// ── fetchBrokerTrades ───────────────────────────────────────────────────────

describe("fetchBrokerTrades", () => {
  afterEach(() => jest.restoreAllMocks())

  it("passes AbortSignal to fetch", async () => {
    const controller = new AbortController()
    const mockData = {
      stock_id: "2330",
      trade_date: "2026-04-01",
      category: "day_trade",
      category_label: "當沖",
      brokers: [],
    }

    const mockFetch = jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => mockData,
    } as Response)

    await fetchBrokerTrades("2330", "day_trade", "2026-04-01", 1, {
      signal: controller.signal,
    })

    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/stocks/2330/brokers?"),
      expect.objectContaining({ signal: controller.signal }),
    )
  })

  it("retries once on transient network failure", async () => {
    const mockData = {
      stock_id: "2330",
      trade_date: "2026-04-01",
      category: "day_trade",
      category_label: "當沖",
      brokers: [],
    }

    const mockFetch = jest.spyOn(global, "fetch")
    mockFetch
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => mockData,
      } as Response)
    mockFetch.mockClear()

    const result = await fetchBrokerTrades("2330", "day_trade", "2026-04-01")

    expect(mockFetch.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(result).toEqual(mockData)
  })
})
