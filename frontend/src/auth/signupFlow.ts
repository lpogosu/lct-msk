// Состояние между экранами «регистрация → код из письма». Пароль держим ТОЛЬКО в памяти вкладки:
// бэкенд подтверждает email по коду И паролю из регистрации (иначе чужая повторная регистрация
// могла бы подменить пароль). После перезагрузки страницы пароль спросим заново.
// Email (не секрет) дублируем в sessionStorage, чтобы экран кода пережил перезагрузку.

const EMAIL_KEY = 'vz_signup_email'

interface PendingSignup {
  email: string
  password: string | null
  role: 'candidate' | 'employer' | null
  /** Когда можно запросить код повторно (мс, Date.now()) */
  resendAvailableAt: number
  /** Срок жизни кода, секунд (из ответа регистрации) */
  codeTtlS: number
}

let pending: PendingSignup | null = null

export function startSignup(p: Omit<PendingSignup, 'resendAvailableAt'> & { resendAvailableInS: number }): void {
  pending = {
    email: p.email,
    password: p.password,
    role: p.role,
    codeTtlS: p.codeTtlS,
    resendAvailableAt: Date.now() + p.resendAvailableInS * 1000,
  }
  try {
    sessionStorage.setItem(EMAIL_KEY, p.email)
  } catch {
    /* приватный режим — не критично */
  }
}

export function getPendingSignup(): PendingSignup | null {
  return pending
}

export function rememberedEmail(): string {
  try {
    return sessionStorage.getItem(EMAIL_KEY) ?? ''
  } catch {
    return ''
  }
}

export function setResendCooldown(seconds: number): void {
  if (pending) pending.resendAvailableAt = Date.now() + seconds * 1000
}

export function clearSignup(): void {
  pending = null
  try {
    sessionStorage.removeItem(EMAIL_KEY)
  } catch {
    /* noop */
  }
}
