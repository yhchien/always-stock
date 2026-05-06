/**
 * Feature flags。
 *
 * `isAuthDisabled()` 永遠回 true：全站已改為免註冊/登入；訪問控制改由
 * SiteGate 單一密碼閘門負責。這個函式保留是為了讓既有 caller
 * （<RequireAuth /> / Navbar / /login）邏輯一行不動，可逆性高。
 */
export function isAuthDisabled(): boolean {
  return true
}
