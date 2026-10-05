import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { authApi } from '@/api/auth'
import { ApiException, errorMessage } from '@/api/errors'
import { clearSignup, getPendingSignup, rememberedEmail, setResendCooldown } from '@/auth/signupFlow'
import { useCountdown } from '@/hooks/useCountdown'
import { Alert, Button, Input, PasswordInput } from '@/ui'
import { validateCode, validateEmail } from './validation'

/** Подсказка к ошибкам кода: сообщение бэкенда уже русское, добавляем остаток попыток. */
function codeErrorText(err: ApiException): string {
  const left = err.attemptsLeft
  if (left != null && (err.code === 'code_invalid' || err.code === 'password_mismatch')) {
    const base = /[.!?]$/.test(err.message) ? err.message : `${err.message}.`
    return `${base} Осталось попыток: ${left}.`
  }
  return err.message
}

export default function Verify() {
  const [params] = useSearchParams()
  const pending = getPendingSignup()
  const email = pending?.email ?? params.get('email') ?? rememberedEmail()
  // Пароль нужен бэкенду для подтверждения; из памяти его нет, если страницу перезагрузили или открыли по ссылке из письма
  const needPassword = !pending?.password

  const [emailInput, setEmailInput] = useState(email)
  // ссылка из письма приносит ?email=…&code=…: подставляем код, пароль всё равно спросим
  const [code, setCode] = useState((params.get('code') ?? '').replace(/\D/g, '').slice(0, 6))
  const [password, setPassword] = useState('')
  const [errors, setErrors] = useState<{ email?: string; code?: string; password?: string; form?: string }>({})
  const [loading, setLoading] = useState(false)
  const [resendInfo, setResendInfo] = useState<string | null>(null)
  const [resending, setResending] = useState(false)
  const [resendAt, setResendAt] = useState(pending?.resendAvailableAt ?? 0)
  const cooldown = useCountdown(resendAt)

  const effectiveEmail = (email || emailInput).trim()

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    const next = {
      email: email ? undefined : (validateEmail(emailInput) ?? undefined),
      code: validateCode(code) ?? undefined,
      password: needPassword && !password ? 'Введите пароль, который вы указали при регистрации' : undefined,
    }
    if (next.email || next.code || next.password) {
      setErrors(next)
      return
    }
    setErrors({})
    setLoading(true)
    try {
      await authApi.verifyEmail({
        email: effectiveEmail,
        code: code.trim(),
        password: pending?.password ?? password,
      })
      clearSignup()
      // сессия открыта — RedirectIfAuthed на маршруте уведёт в кабинет по роли
    } catch (err) {
      if (err instanceof ApiException) {
        if (err.code === 'password_mismatch') setErrors({ password: codeErrorText(err) })
        else if (err.code === 'already_verified')
          setErrors({ form: 'Этот email уже подтверждён. Войдите с паролем.' })
        else if (err.fieldIssues.length)
          setErrors({
            code: err.fieldIssues.find((i) => i.field === 'code')?.message,
            password: err.fieldIssues.find((i) => i.field === 'password')?.message,
            form: err.fieldIssues.every((i) => i.field === 'code' || i.field === 'password') ? undefined : err.message,
          })
        else setErrors({ code: codeErrorText(err) })
      } else {
        setErrors({ form: errorMessage(err) })
      }
    } finally {
      setLoading(false)
    }
  }

  async function onResend() {
    setResending(true)
    setResendInfo(null)
    setErrors({})
    try {
      const r = await authApi.resendCode(effectiveEmail)
      setResendAt(Date.now() + r.resend_available_in_s * 1000)
      setResendCooldown(r.resend_available_in_s)
      setCode('')
      setResendInfo(
        r.sent
          ? `Новый код отправлен. Прежние коды больше не действуют. Осталось отправок в этот час: ${r.sends_left_this_hour}.`
          : 'Письмо не ушло: почта временно недоступна. Повторите через минуту.',
      )
    } catch (err) {
      if (err instanceof ApiException && err.retryAfterS) setResendAt(Date.now() + err.retryAfterS * 1000)
      setErrors({ form: errorMessage(err) })
    } finally {
      setResending(false)
    }
  }

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-3">
        <h1 className="t-title text-brand">Введите код из письма</h1>
        <p className="t-body text-ink-2">
          {effectiveEmail ? (
            <>
              Мы отправили 6 цифр на <span className="t-body-strong text-ink">{effectiveEmail}</span>. Код действует 15
              минут.
            </>
          ) : (
            'Укажите email, на который пришёл код.'
          )}
        </p>
      </header>

      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-5">
        {!email && (
          <Input
            label="Email"
            type="email"
            autoComplete="email"
            value={emailInput}
            onChange={(e) => setEmailInput(e.target.value)}
            error={errors.email}
          />
        )}

        <Input
          label="Код из письма"
          name="one-time-code"
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

        {needPassword && (
          <PasswordInput
            label="Пароль, указанный при регистрации"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={errors.password}
            hint="Код доказывает владение почтой, пароль — что аккаунт подтверждает тот, кто его создал."
          />
        )}

        {errors.form && <Alert tone="danger">{errors.form}</Alert>}
        {resendInfo && <Alert>{resendInfo}</Alert>}

        <Button type="submit" size="lg" block loading={loading}>
          Подтвердить
        </Button>
      </form>

      <div className="flex flex-col gap-2 t-body text-ink-2">
        <p>
          Не пришло письмо?{' '}
          {cooldown > 0 ? (
            <span className="text-ink-3">Отправить ещё раз можно через {cooldown} с</span>
          ) : (
            <Button
              variant="text"
              onClick={onResend}
              loading={resending}
              disabled={!effectiveEmail}
              className="align-baseline"
            >
              Отправить код ещё раз
            </Button>
          )}
        </p>
        <p>
          <Link to="/auth/register" className="t-label text-brand underline-offset-4 hover:underline">
            Изменить email или пароль
          </Link>
        </p>
      </div>
    </div>
  )
}
