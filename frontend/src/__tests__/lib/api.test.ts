import { fmtAmount, fmtShares, fetchIndustries, fetchIndustryStocks, fetchStockHistory } from "@/lib/api"

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

// ── fetchIndustries ──────────────────────────────────────────────────────────

describe("fetchIndustries", () => {
  afterEach(() => jest.restoreAllMocks())

  it("returns parsed JSON on success", async () => {
    const mockData = [{ industry_name: "半導體", total_net_amount: 1e9 }]
    jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => mockData,
    } as Response)

    const result = await fetchIndustries("2026-04-01")
    expect(result).toEqual(mockData)
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("date=2026-04-01"))
  })

  it("throws on non-ok response", async () => {
    jest.spyOn(global, "fetch").mockResolvedValue({
      ok: false,
      status: 404,
    } as Response)

    await expect(fetchIndustries("2026-04-01")).rejects.toThrow("404")
  })
})

// ── fetchIndustryStocks ───────────────────────────────────────────────────────

describe("fetchIndustryStocks", () => {
  afterEach(() => jest.restoreAllMocks())

  it("encodes industry name in URL", async () => {
    jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => [],
    } as Response)

    await fetchIndustryStocks("IC 設計", "2026-04-01")
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("IC%20%E8%A8%AD%E8%A8%88"))
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
    jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => mockData,
    } as Response)

    await fetchStockHistory("2330", 30)
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("days=30"))
  })

  it("includes end_date param when provided", async () => {
    jest.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ history: [] }),
    } as Response)

    await fetchStockHistory("2330", 60, "2026-04-01")
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("end_date=2026-04-01"))
  })

  it("throws on non-ok response", async () => {
    jest.spyOn(global, "fetch").mockResolvedValue({
      ok: false,
      status: 404,
    } as Response)

    await expect(fetchStockHistory("9999")).rejects.toThrow("404")
  })
})
