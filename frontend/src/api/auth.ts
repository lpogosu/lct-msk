// Методы аутентификации поверх типизированного клиента. Порядок потока (backend/app/api/auth.py):
//   register → verify-email (код + ПАРОЛЬ из регистрации) → сессия;  login → сессия;  refresh — автоматически.

import { api, unwrap } from './client'
import { applyTokenResponse, clearSession } from './session'
import type { ConsentDocument, RegisterRequest, TokenResponse } from './types'

export const authApi = {
  /** Создаёт аккаунт `pending_email` и отправляет 6-значный код (действует 15 минут). */
  register: (body: RegisterRequest) => unwrap(api.POST('/api/auth/register', { body })),

  /** Подтверждение email: код из письма + пароль из регистрации. Сразу открывает сессию. */
  async verifyEmail(body: { email: string; code: string; password: string }): Promise<TokenResponse> {
    const t = await unwrap(api.POST('/api/auth/verify-email', { body }))
    applyTokenResponse(t)
    return t
  },

  resendCode: (email: string) => unwrap(api.POST('/api/auth/resend-code', { body: { email } })),

  async login(body: { email: string; password: string }): Promise<TokenResponse> {
    const t = await unwrap(api.POST('/api/auth/login', { body }))
    applyTokenResponse(t)
    return t
  },

  async logout(): Promise<void> {
    try {
      await unwrap(api.POST('/api/auth/logout'))
    } finally {
      clearSession()
    }
  },

  me: () => unwrap(api.GET('/api/auth/me')),

  passwordResetRequest: (email: string) => unwrap(api.POST('/api/auth/password-reset/request', { body: { email } })),

  async passwordResetConfirm(body: { email: string; code: string; new_password: string }): Promise<TokenResponse> {
    const t = await unwrap(api.POST('/api/auth/password-reset/confirm', { body }))
    applyTokenResponse(t)
    return t
  },

  /** Актуальные тексты согласий (без входа). Версию текста отправляем при регистрации. */
  consentDocuments: (): Promise<ConsentDocument[]> => unwrap(api.GET('/api/consents/documents')),
}
