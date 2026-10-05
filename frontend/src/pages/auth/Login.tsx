import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { authApi } from '@/api/auth'
import { ApiException, errorMessage } from '@/api/errors'
import { startSignup } from '@/auth/signupFlow'
import { Alert, Button, Input, PasswordInput } from '@/ui'
import { validateEmail } from './validation'

export default function Login() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [errors, setErrors] = useState<{ email?: string; password?: string; form?: string }>({})
  const [loading, setLoading] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    const next = {
      email: validateEmail(email) ?? undefined,
      password: password ? undefined : 'Введите пароль',
    }
    if (next.email || next.password) {
      setErrors(next)
      return
    }
    setErrors({})
    setLoading(true)
    try {
      await authApi.login({ email: email.trim(), password })
      // сессия открыта — RedirectIfAuthed вернёт на запрошенную страницу или в кабинет по роли
    } catch (err) {
      if (err instanceof ApiException && err.code === 'email_not_verified') {
        // аккаунт создан, но почта не подтверждена: ведём на ввод кода (пароль уже введён — запомним в памяти)
        const d = (err.details ?? {}) as { resend_available_in_s?: number }
        startSignup({
          email: email.trim(),
          password,
          role: null,
          codeTtlS: 900,
          resendAvailableInS: d.resend_available_in_s ?? 0,
        })
        navigate('/verify-email')
        return
      }
      setErrors({ form: errorMessage(err) })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-3">
        <h1 className="t-title text-brand">Вход</h1>
        <p className="t-body text-ink-2">Продолжите с того места, где остановились.</p>
      </header>

      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-5">
        <Input
          label="Email"
          type="email"
          name="email"
          autoComplete="email"
          inputMode="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          error={errors.email}
        />
        <PasswordInput
          label="Пароль"
          name="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          error={errors.password}
        />

        {errors.form && <Alert tone="danger">{errors.form}</Alert>}

        <Button type="submit" size="lg" block loading={loading}>
          Войти
        </Button>
      </form>

      <div className="flex flex-col gap-2 t-body text-ink-2">
        <p>
          <Link to="/reset-password" className="t-label text-brand underline-offset-4 hover:underline">
            Забыли пароль?
          </Link>
        </p>
        <p>
          Нет аккаунта?{' '}
          <Link to="/auth/register" className="t-label text-brand underline-offset-4 hover:underline">
            Зарегистрироваться
          </Link>
        </p>
      </div>
    </div>
  )
}
