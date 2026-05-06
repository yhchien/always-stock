import React from "react"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

import SiteGate from "@/components/SiteGate"

const ORIGINAL_FETCH = global.fetch

function mockGateConfig() {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve({ max_attempts: 3, lockout_seconds: 300 }),
  } as Response)
}

function mockVerifyOk() {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) } as Response)
}

function mockVerifyWrong() {
  return Promise.resolve({
    ok: false,
    status: 403,
    json: () => Promise.resolve({ detail: "密碼錯誤" }),
  } as Response)
}

function setupFetch(verifyImpl: () => Promise<Response>) {
  // 第一個 call 是 /api/gate/config (mount-time)，之後都是 /api/gate/verify
  let calls = 0
  global.fetch = jest.fn().mockImplementation(() => {
    calls += 1
    if (calls === 1) return mockGateConfig()
    return verifyImpl()
  }) as unknown as typeof fetch
}

describe("SiteGate", () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    global.fetch = ORIGINAL_FETCH
    jest.restoreAllMocks()
  })

  it("renders prompt when no unlocked state in localStorage", async () => {
    setupFetch(mockVerifyOk)
    render(
      <SiteGate>
        <div data-testid="protected">SECRET</div>
      </SiteGate>
    )

    await waitFor(() => expect(screen.getByText("請輸入訪問密碼")).toBeInTheDocument())
    expect(screen.queryByTestId("protected")).not.toBeInTheDocument()
  })

  it("renders children when unlocked timestamp is still valid", async () => {
    window.localStorage.setItem(
      "always-stock:gate:unlocked_until",
      String(Date.now() + 1000 * 60 * 60)
    )
    setupFetch(mockVerifyOk)

    render(
      <SiteGate>
        <div data-testid="protected">SECRET</div>
      </SiteGate>
    )

    await waitFor(() => expect(screen.getByTestId("protected")).toBeInTheDocument())
  })

  it("unlocks after correct password and renders children", async () => {
    setupFetch(mockVerifyOk)

    render(
      <SiteGate>
        <div data-testid="protected">SECRET</div>
      </SiteGate>
    )

    await waitFor(() => expect(screen.getByText("請輸入訪問密碼")).toBeInTheDocument())

    const input = screen.getByLabelText("密碼") as HTMLInputElement
    fireEvent.change(input, { target: { value: "letmein" } })
    fireEvent.click(screen.getByRole("button", { name: "進入" }))

    await waitFor(() => expect(screen.getByTestId("protected")).toBeInTheDocument())
    expect(window.localStorage.getItem("always-stock:gate:unlocked_until")).toBeTruthy()
  })

  it("locks out after exceeding max attempts", async () => {
    setupFetch(mockVerifyWrong)

    render(
      <SiteGate>
        <div data-testid="protected">SECRET</div>
      </SiteGate>
    )

    await waitFor(() => expect(screen.getByText("請輸入訪問密碼")).toBeInTheDocument())

    // 前兩次：等錯誤訊息 + button enable，再進下一輪
    for (let i = 0; i < 2; i += 1) {
      const input = screen.getByLabelText("密碼") as HTMLInputElement
      const submit = screen.getByRole("button", { name: /進入|驗證中/ })
      fireEvent.change(input, { target: { value: `wrong${i}` } })
      fireEvent.click(submit)
      // eslint-disable-next-line no-await-in-loop
      await waitFor(() => expect(screen.getByText("密碼錯誤")).toBeInTheDocument())
      // eslint-disable-next-line no-await-in-loop
      await waitFor(() => expect(screen.getByRole("button", { name: "進入" })).not.toBeDisabled())
    }

    // 第三次：送出後不等 button（會切到 locked 畫面，button 從 DOM 消失）
    const finalInput = screen.getByLabelText("密碼") as HTMLInputElement
    fireEvent.change(finalInput, { target: { value: "wrongfinal" } })
    fireEvent.click(screen.getByRole("button", { name: "進入" }))

    await waitFor(() => expect(screen.getByText("已暫時鎖定")).toBeInTheDocument())
    expect(screen.queryByTestId("protected")).not.toBeInTheDocument()
    expect(window.localStorage.getItem("always-stock:gate:locked_until")).toBeTruthy()
  })

  it("shows locked screen on mount when locked_until is in the future", async () => {
    window.localStorage.setItem(
      "always-stock:gate:locked_until",
      String(Date.now() + 1000 * 60)
    )
    setupFetch(mockVerifyOk)

    render(
      <SiteGate>
        <div data-testid="protected">SECRET</div>
      </SiteGate>
    )

    await waitFor(() => expect(screen.getByText("已暫時鎖定")).toBeInTheDocument())
  })

  it("clears expired locked_until on mount and shows prompt", async () => {
    window.localStorage.setItem(
      "always-stock:gate:locked_until",
      String(Date.now() - 1000)
    )
    setupFetch(mockVerifyOk)

    render(
      <SiteGate>
        <div data-testid="protected">SECRET</div>
      </SiteGate>
    )

    await waitFor(() => expect(screen.getByText("請輸入訪問密碼")).toBeInTheDocument())
    expect(window.localStorage.getItem("always-stock:gate:locked_until")).toBeNull()
  })
})
