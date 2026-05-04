/**
 * Feature flags 讀取 build-time 環境變數。
 *
 * NEXT_PUBLIC_DISABLE_AUTH=true 時：
 * - <RequireAuth> 直接放行（不檢查 useAuth）
 * - Navbar 隱藏登入/註冊/登出按鈕
 * - /login 頁面顯示「停用中」提示
 *
 * 後端對應 env 是 DISABLE_AUTH=true，兩端要一起設才會行為一致。
 * 此 flag 是 build-time replace，需重新 build 前端才會生效。
 */
export function isAuthDisabled(): boolean {
  return process.env.NEXT_PUBLIC_DISABLE_AUTH === "true"
}
