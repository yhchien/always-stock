import { render, screen, waitFor } from "@testing-library/react"

import SignalRecommendationsPage from "@/app/signals/(product)/recommendations/page"
import {
  fetchSignalArchive,
  fetchSignalObservations,
  fetchSignalRecommendations,
} from "@/lib/api"

jest.mock("next/navigation", () => ({ usePathname: () => "/signals/recommendations" }))
jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api")
  return {
    ...actual,
    fetchSignalRecommendations: jest.fn(),
    fetchSignalObservations: jest.fn(),
    fetchSignalArchive: jest.fn(),
  }
})
// 這份測試在驗證工程稽核內容（NOT_SELECTED／REMOVE／技術失敗分區）；這些內容在正式版／
// 工程版 toggle 上線後只在工程版顯示，直接 mock 成工程版繞過 Provider／localStorage。
jest.mock("@/lib/signalsViewMode", () => ({
  useSignalsViewMode: () => ({
    mode: "engineering",
    isEngineering: true,
    setMode: jest.fn(),
    toggle: jest.fn(),
  }),
}))

const recommendationMock = fetchSignalRecommendations as jest.MockedFunction<
  typeof fetchSignalRecommendations
>
const observationsMock = fetchSignalObservations as jest.MockedFunction<
  typeof fetchSignalObservations
>
const archiveMock = fetchSignalArchive as jest.MockedFunction<typeof fetchSignalArchive>

function renderPage() {
  return render(<SignalRecommendationsPage />)
}

function payload(selectionComplete = true) {
  return {
    snapshot_date: "2026-07-29",
    generated_at: "2026-07-29T12:00:00",
    llm_model: "gpt-5",
    prompt_version: "v7",
    navigation: {
      previous_date: "2026-07-28",
      next_date: null,
      latest_date: "2026-07-29",
    },
    data: {
      market_context: {},
      candidate_pool_size: 100,
      final_watchlist_size: 2,
      watchlist: [
        {
          stock: "2454",
          name: "聯發科",
          recommendation_rank: 2,
          selection_status: "RECOMMEND",
          recommendation_thesis: "推薦二",
        },
        {
          stock: "2330",
          name: "台積電",
          recommendation_rank: 1,
          selection_status: "RECOMMEND",
          recommendation_thesis: "推薦一",
        },
      ],
      not_selected: [
        {
          stock: "0050",
          name: "元大台灣50",
          asset_type: "ETF",
          selection_reason_code: "NO_DISTINCT_DAILY_EDGE",
          selection_reason: "候選有效但今日相對優勢不足。",
        },
      ],
      removed: [
        {
          stock: "9999",
          name: "事實不符",
          veto_reason: "BUSINESS_MISMATCH",
          short_reason: "業務不符。",
        },
      ],
      technical_failures: [
        {
          stock_id: "3008",
          processing_status: "RESEARCH_FAILED",
          error_summary: "timeout",
        },
      ],
      summary: {
        processing_summary: {
          raw_union_count: 100,
          llm_eligible_count: 35,
          research_completed_count: 34,
          global_selection_eligible_count: 29,
          global_selection_status: selectionComplete ? "COMPLETED" : "FAILED",
          selection_complete: selectionComplete,
        },
        selection_summary: {
          selection_complete: selectionComplete,
          global_eligible_count: 29,
          recommended_count: 2,
          not_selected_count: 1,
          veto_removed_count: 1,
        },
      },
    },
  }
}

describe("SignalRecommendationsPage", () => {
  beforeEach(() => {
    recommendationMock.mockResolvedValue(payload() as never)
    archiveMock.mockResolvedValue({ as_of_trade_date: null, retention_trade_days: 30, items: [] })
    observationsMock.mockResolvedValue({
      as_of_date: "2026-07-29",
      observations: [
        {
          id: 1,
          stock: "0050",
          name: "元大台灣50",
          asset_type: "ETF",
          episode_id: "episode-etf",
          status: "CAUTION",
          started_at: "2026-07-20T00:00:00",
          started_signal_date: "2026-07-20",
          last_review_date: "2026-07-29",
          latest_decision: "CAUTION",
          consecutive_caution_count: 1,
          latest_reason_codes: [],
          latest_reason: "警戒",
          latest_review_technical_status: null,
          stopped_at: null,
          stop_reason_code: null,
          stop_reason: null,
          baseline_quality: "P3_COMPLETE",
          selection_version: "v7_global_selector",
          recommended_today: false,
        },
      ],
    })
  })

  it("sorts only RECOMMEND in the main list and separates neutral/veto/technical buckets", async () => {
    renderPage()
    await screen.findByText("今日正式推薦（2）")
    const ranks = screen.getAllByText(/^#[12]$/).map((node) => node.textContent)
    expect(ranks).toEqual(["#1", "#2"])
    expect(screen.getByText("未列入今日推薦（1）")).toBeInTheDocument()
    expect(screen.getByText("明確移除（1）")).toBeInTheDocument()
    expect(screen.getByText("技術失敗")).toBeInTheDocument()
    expect(screen.getByText(/今日未列入推薦，但既有觀察仍繼續/)).toBeInTheDocument()
    expect(screen.getByText("ETF")).toBeInTheDocument()
  })

  it("does not render a global selection failure as a legitimate zero recommendation", async () => {
    recommendationMock.mockResolvedValue(payload(false) as never)
    renderPage()
    expect(
      await screen.findByText(
        "本次研究已完成，但正式推薦選擇未完成；目前結果不可視為完整推薦名單。",
      ),
    ).toBeInTheDocument()
    await waitFor(() => expect(recommendationMock).toHaveBeenCalled())
  })

  it("keeps a pre-P3 WATCH snapshot crash-free without calling it a formal recommendation", async () => {
    const historical = payload()
    historical.data.watchlist = [
      {
        stock: "1101",
        name: "舊 WATCH",
        decision: "WATCH",
      },
    ]
    historical.data.not_selected = []
    historical.data.removed = []
    historical.data.technical_failures = []
    historical.data.summary = {}
    recommendationMock.mockResolvedValue(historical as never)
    renderPage()
    expect(await screen.findByText("今日正式推薦（0）")).toBeInTheDocument()
    expect(screen.queryByText("舊 WATCH")).not.toBeInTheDocument()
  })
})
