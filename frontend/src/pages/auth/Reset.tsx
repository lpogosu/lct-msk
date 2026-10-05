import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { authApi } from '@/api/auth'
import { ApiException, errorMessage } from '@/api/errors'
import { useCountdown } from '@/hooks/useCountdown'
import { Alert, Button, Input, PasswordInput } from '@/ui'
import { validateCode, validateEmail, validateNewPassword } from './validation'

/** Восстановление пароля (экран О4): email → код из письма → новый пароль → вход. */
export default function Reset() {
  // ссылка из письма: /reset-password?email=…&code=… — сразу на шаг ввода нового пароля
  const [params] = useSearchParams()
  const linkEmail = params.get('email') ?? ''
  const linkCode = (params.get('code') ?? '').replace(/\D/g, '').slice(0, 6)
  const [stage, setStage] = useState<'email' | 'code'>(linkEmail && linkCode ? 'code' : 'email')
  const [email, setEmail] = useState(linkEmail)
  const [code, setCode] = useState(linkCode)
  const [password, setPassword] = useState('')
  const [errors, setErrors] = useState<{ email?: string; code?: string; password?: string; form?: string }>({})
  const [loading, setLoading] = useState(false)
  const [resendAt, setResendAt] = useState(0)
  const cooldown = useCountdown(resendAt)

  async function requestCode(e?: FormEvent) {
    e?.preventDefault()
    const msg = validateEmail(email)
    if (msg) return setErrors({ email: msg })
    setErrors({})
    setLoading(true)
    try {
      const r = await authApi.passwordResetRequest(email.trim())
      setResendAt(Date.now() + r.resend_available_in_s * 1000)
      setStage('code')
    } catch (err) {
      if (err instanceof ApiException && err.retryAfterS) setResendAt(Date.now() + err.retryAfterS * 1000)
      setErrors({ form: errorMessage(err) })
    } finally {
      setLoading(false)
    }
  }

  async function confirm(e: FormEvent) {
    e.preventDefault()
    const next = { code: validateCode(code) ?? undefined, password: validateNewPassword(password) ?? undefined }
    if (next.code || next.password) return setErrors(next)
    setErrors({})
    setLoading(true)
    try {
      await authApi.passwordResetConfirm({ email: email.trim(), code: code.trim(), new_password: password })
      // сессия открыта — RedirectIfAuthed уведёт в кабинет
    } catch (err) {
      if (err instanceof ApiException && err.fieldIssues.length) {
        setErrors({
          code: err.fieldIssues.find((i) => i.field === 'code')?.message,
          password: err.fieldIssues.find((i) => i.field === 'new_password')?.message,
        })
      } else if (err instanceof ApiException && err.attemptsLeft != null) {
        setErrors({ code: `${/[.!?]$/.test(err.message) ? err.message : `${err.message}.`} Осталось попыток: ${err.attemptsLeft}.` })
      } else {
        setErrors({ form: errorMessage(err) })
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-3">
        <h1 className="t-title text-brand">Восстановление пароля</h1>
        <p className="t-body text-ink-2">
          {stage === 'email'
            ? 'Пришлём на почту 6-значный код, по нему вы зададите новый пароль.'
            : 'Если такой аккаунт есть, письмо с кодом уже в пути. Код действует 15 минут.'}
        </p>
      </header>

      {stage === 'email' ? (
        <form onSubmit={requestCode} noValidate className="flex flex-col gap-5">
          <Input
            label="Email"
            type="email"
            autoComplete="email"
            inputMode="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            error={errors.email}
          />
          {errors.form && <Alert tone="danger">{errors.form}</Alert>}
          <Button type="submit" size="lg" block loading={loading}>
            Прислать код
          </Button>
        </form>
      ) : (
        <form onSubmit={confirm} noValidate className="flex flex-col gap-5">
          <Input
            label="Код из письма"
            mono
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            placeholder="000000"
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
            error={errors.code}
            className="text-center text-[22px] tracking-[0.5em]"
            autoFocus
          />
          <PasswordInput
            label="Новый пароль"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={errors.password}
            hint="Минимум 8 символов, хотя бы одна буква и одна цифра. Прежние сессии завершатся."
          />
          {errors.form && <Alert tone="danger">{errors.form}</Alert>}
          <Button type="submit" size="lg" block loading={loading}>
            Сохранить и войти
          </Button>
          <p className="t-body text-ink-2">
            {cooldown > 0 ? (
              <span className="text-ink-3">Отправить код ещё раз можно через {cooldown} с</span>
            ) : (
              <Button variant="text" onClick={() => requestCode()} loading={loading}>
                Отправить код ещё раз
              </Button>
            )}
          </p>
        </form>
      )}

      <p className="t-body text-ink-2">
        <Link to="/auth/login" className="t-label text-brand underline-offset-4 hover:underline">
          Вернуться ко входу
        </Link>
      </p>
    </div>
  )
}
