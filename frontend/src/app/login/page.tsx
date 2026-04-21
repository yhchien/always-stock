"use client"

import { Suspense, useEffect, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"

import { useAuth } from "@/lib/auth"

type Mode = "login" | "register"

function LoginForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const next = searchParams.get("next") ?? "/"
  const { user, status, login, register } = useAuth()

  const [mode, setMode] = useState<Mode>("login")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [name, setName] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (status === "authenticated" && user) {
      router.replace(next)
    }
  }, [status, user, router, next])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (!email.trim()) {
      setError("請輸入 Email")
      return
    }
    if (mode === "register" && password.length < 8) {
      setError("密碼至少 8 碼")
      return
    }

    setSubmitting(true)
    try {
      if (mode === "login") {
        await login(email.trim(), password)
      } else {
        await register(email.trim(), password, name.trim() || undefined)
      }
      router.replace(next)
    } catch (err) {
      const msg = err instanceof Error ? err.message : "登入失敗"
      setError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-md flex-col gap-4 px-4 py-12">
      <h1 className="text-lg font-semibold text-slate-100">
        {mode === "login" ? "登入" : "註冊"}
      </h1>

      <div className="flex border-b border-slate-700/60 text-sm">
        <button
          onClick={() => {
            setMode("login")
            setError(null)
          }}
          className={`px-4 py-2 -mb-px border-b-2 transition-colors ${
            mode === "login"
              ? "border-sky-500 text-slate-100"
              : "border-transparent text-slate-500 hover:text-slate-300"
          }`}
        >
          登入
        </button>
        <button
          onClick={() => {
            setMode("register")
            setError(null)
          }}
          className={`px-4 py-2 -mb-px border-b-2 transition-colors ${
            mode === "register"
              ? "border-sky-500 text-slate-100"
              : "border-transparent text-slate-500 hover:text-slate-300"
          }`}
        >
          註冊
        </button>
      </div>

      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Email
          <input
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-100 focus:border-sky-500 focus:outline-none"
            required
          />
        </label>

        {mode === "register" && (
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            顯示名稱（選填）
            <input
              type="text"
              autoComplete="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-100 focus:border-sky-500 focus:outline-none"
            />
          </label>
        )}

        <label className="flex flex-col gap-1 text-xs text-slate-400">
          {mode === "register" ? "密碼（至少 8 碼）" : "密碼"}
          <input
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-100 focus:border-sky-500 focus:outline-none"
            required
            minLength={mode === "register" ? 8 : 1}
          />
        </label>

        {error && (
          <p className="text-xs text-rose-400">{error}</p>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="mt-2 rounded-md bg-sky-600 px-4 py-2 text-sm font-medium text-slate-50 transition-colors hover:bg-sky-500 disabled:opacity-50"
        >
          {submitting ? "處理中…" : mode === "login" ? "登入" : "註冊並登入"}
        </button>
      </form>

      <p className="text-[11px] text-slate-500">
        {mode === "login"
          ? "第一次使用？切到上方「註冊」頁籤建立帳號。"
          : "已有帳號？切到上方「登入」頁籤。"}
      </p>
    </main>
  )
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  )
}
