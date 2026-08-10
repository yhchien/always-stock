import { render, screen } from "@testing-library/react"

import SignalOutcomesPage from "@/app/signals/(product)/outcomes/page"
import * as api from "@/lib/api"

jest.mock("next/navigation", () => ({ usePathname: () => "/signals/outcomes" }))
jest.mock("@/components/OutcomeCharts", () => ({
  OutcomeDistributionChart: () => <div>Distribution Chart</div>,
  OutcomeTimeseriesChart: () => <div>Timeseries Chart</div>,
}))
jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api")
  return {
    ...actual,
    fetchSignalOutcomeSummary: jest.fn(),
    fetchSignalOutcomeTimeseries: jest.fn(),
    fetchSignalOutcomeItems: jest.fn(),
    fetchSignalObservationAnalytics: jest.fn(),
    fetchSignalOutcomeReviewQueue: jest.fn(),
    signalOutcomeCsvUrl: jest.fn(() => "/csv"),
    updateSignalOutcomeReview: jest.fn(),
  }
})

describe("SignalOutcomesPage", () => {
  it("shows denominator-aware core goals and observation analytics", async () => {
    ;(api.fetchSignalOutcomeSummary as jest.Mock).mockResolvedValue({
      date_range: { requested_start: null, requested_end: null, actual_start: "2026-07-01", actual_end: "2026-07-29" },
      sample: { total: 10, matured: 10, immature: 0, missing: 0 },
      recommendation: {
        winner_count: 5,
        neutral_count: 3,
        big_loser_count: 2,
        winner_rate: 0.5,
        neutral_rate: 0.3,
        big_loser_rate: 0.2,
        acceptable_rate: 0.8,
        acceptable_target_met: true,
        winner_greater_than_neutral: true,
        average_recommend_count: 2,
      },
      selection: {
        winner_recall: 0.7,
        not_selected_winner_count: 3,
        not_selected_winner_rate: 0.2,
        not_selected_winner_by_reason: { LOWER_RELATIVE_PRIORITY: 3 },
        average_compression_rate: 0.6,
        average_phase2_eligible_count: 35,
        average_global_eligible_count: 29,
        average_recommended_count: 2,
        rank_override_count: 1,
        rank_override_big_loser_count: 0,
        backend_rank_distribution: {
          recommend: { "1-10": 5 },
          not_selected: { "11-25": 3 },
          winner: { "1-10": 4 },
        },
      },
      observation: {
        caution_recovery_rate: 0.5,
        caution_event_recovery_rate: 0.5,
        caution_episode_recovery_rate: 0.5,
        premature_stop_candidate_count: 1,
        stop_before_big_loss_rate: 0.75,
        average_trading_days_to_stop: 4,
        rerecommended_episode_count: 2,
      },
      versions: { prompt_family: ["v7"], selection_version: ["v7_global_selector"] },
      definitions: { outcome_definition_version: "day10_v1" },
    })
    ;(api.fetchSignalOutcomeTimeseries as jest.Mock).mockResolvedValue({ outcome_definition_version: "day10_v1", items: [] })
    ;(api.fetchSignalOutcomeItems as jest.Mock).mockResolvedValue({ page: 1, page_size: 25, total: 0, pages: 0, items: [] })
    ;(api.fetchSignalObservationAnalytics as jest.Mock).mockResolvedValue({
      summary: {
        caution_recovery_rate: 0.5,
        caution_event_recovery_rate: 0.5,
        caution_episode_recovery_rate: 0.5,
        premature_stop_candidate_count: 1,
        stop_before_big_loss_rate: 0.75,
        average_trading_days_to_stop: 4,
        rerecommended_episode_count: 2,
      },
      definitions: { premature_stop_definition_version: "stop_day10_plus10_v1" },
      premature_stop_candidates: [],
      stopped_before_big_loss: {},
      average_days_to_stop: {},
      rerecommended_episodes: [],
    })
    ;(api.fetchSignalOutcomeReviewQueue as jest.Mock).mockResolvedValue({ page: 1, page_size: 20, total: 0, items: [] })

    render(<SignalOutcomesPage />)
    expect(await screen.findByText("80.0%")).toBeInTheDocument()
    expect(screen.getByText("符合")).toBeInTheDocument()
    expect(screen.getByText("20.0% / 70.0%")).toBeInTheDocument()
    expect(screen.getByText("50.0%")).toBeInTheDocument()
    expect(screen.getByText("75.0%")).toBeInTheDocument()
    expect(screen.getByText("Distribution Chart")).toBeInTheDocument()
  })
})
