import React from "react"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

import Navbar from "@/components/Navbar"
import * as api from "@/lib/api"

const mockPush = jest.fn()

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams("date=2026-04-24"),
}))

jest.mock("@/lib/auth", () => ({
  useAuth: () => ({
    user: { email: "demo@example.com", name: "Demo", is_admin: false },
    status: "authenticated",
    logout: jest.fn(),
  }),
}))

jest.mock("@/lib/watchlist", () => ({
  useWatchlist: () => ({
    total: 1,
    capacity: 20,
  }),
}))

describe("Navbar", () => {
  afterEach(() => {
    jest.restoreAllMocks()
    mockPush.mockReset()
  })

  it("navigates to L3 stock page when an exact stock id is found", async () => {
    jest.spyOn(api, "searchStocks").mockResolvedValue([
      { stock_id: "2355", stock_name: "敬鵬", industry_name: "印刷電路板" },
    ])

    render(<Navbar />)

    fireEvent.change(screen.getByLabelText("搜尋股票代號或名稱"), { target: { value: "2355" } })
    fireEvent.submit(screen.getByRole("button", { name: "搜尋" }).closest("form")!)

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/stocks/2355?date=2026-04-24")
    })
  })

  it("shows the specified error message when stock is not found", async () => {
    jest.spyOn(api, "searchStocks").mockResolvedValue([])

    render(<Navbar />)

    fireEvent.change(screen.getByLabelText("搜尋股票代號或名稱"), { target: { value: "9999" } })
    fireEvent.submit(screen.getByRole("button", { name: "搜尋" }).closest("form")!)

    await waitFor(() => {
      expect(screen.getByText("沒有該股票，請確定股票代號跟確定為上市股")).toBeInTheDocument()
    })
    expect(mockPush).not.toHaveBeenCalled()
  })
})
