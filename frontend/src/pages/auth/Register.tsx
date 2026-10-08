import { useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { authApi } from '@/api/auth'
import { ApiException, errorMessage } from '@/api/errors'
import { startSignup } from '@/auth/signupFlow'
import { Alert, Button, Checkbox, Input, PasswordInput, Segmented, Skeleton } from '@/ui'
import { validateBirthYear, validateEmail, validateNewPassword } from './validation'

type RoleChoice = 'candidate' | 'employer'

const ROLE_OPTIONS = [
  { value: 'candidate', label: 'Ищу работу' },
  { value: 'employer', label: 'Нанимаю' },
] as const

const ROLE_LEAD: Record<RoleChoice, string> = {
  candidate:
    'Ответьте на короткий опрос, пройдите адаптивный тест и получите категорию «специализация × грейд». Работодатели видят вас без имени.',
  employer:
    'Опишите потребность текстом с вилкой — мы предложим категории и построим подборку с объяснениями. Контакты откроются после взаимного «да».',
}

export default function Register() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const initialRole: RoleChoice = params.get('role') === 'employer' ? 'employer' : 'candidate'

  const [role, setRole] = useState<RoleChoice>(initialRole)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [birthYear, setBirthYear] = useState('')
  const [consent, setConsent] = useState(false)
  const [showConsentText, setShowConsentText] = useState(false)
  const [errors, setErrors] = useState<{ email?: string; password?: string; birthYear?: string; consent?: string; form?: string }>({})
  const [loading, setLoading] = useState(false)

  // Актуальная версия текста согласия: отправляем её вместе с регистрацией (бэкенд журналирует)
  const consentDocs = useQuery({ queryKey: ['consent-documents'], queryFn: authApi.consentDocuments })
  const processing = consentDocs.data?.find((d) => d.purpose === 'processing')

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    const next = {
      email: validateEmail(email) ?? undefined,
      password: validateNewPassword(password) ?? undefined,
      birthYear: role === 'candidate' ? validateBirthYear(birthYear) ?? undefined : undefined,
      consent: consent ? undefined : 'Без согласия на обработку персональных данных создать аккаунт нельзя',
    }
    if (next.email || next.password || next.birthYear || next.consent) {
      setErrors(next)
      return
    }
    setErrors({})
    setLoading(true)
    try {
      const res = await authApi.register({
        email: email.trim(),
        password,
        role,
        consent_personal_data: true,
        birth_year: role === 'candidate' ? Number(birthYear) : null,
        consent_text_version: processing?.version ?? null,
      })
      if (res.status === 'active') {
        // DEMO_MODE: письмо с кодом не нужно, аккаунт уже активен — входим сразу (RedirectIfAuthed откроет кабинет)
        await authApi.login({ email: email.trim(), password })
        return
      }
      startSignup({
        email: res.email,
        password,
        role,
        codeTtlS: res.code_ttl_s,
        resendAvailableInS: res.resend_available_in_s,
      })
      navigate('/verify-email', { replace: false, state: { codeSent: res.code_sent } })
    } catch (err) {
      if (err instanceof ApiException) {
        const issues = err.fieldIssues
        if (issues.length) {
          setErrors({
            email: issues.find((i) => i.field === 'email')?.message,
            password: issues.find((i) => i.field === 'password')?.message,
            birthYear: issues.find((i) => i.field === 'birth_year')?.message,
            consent: issues.find((i) => i.field === 'consent_personal_data')?.message,
            form: issues.some((i) => !['email', 'password', 'birth_year', 'consent_personal_data'].includes(i.field ?? ''))
              ? err.message
              : undefined,
          })
        } else if (err.code === 'consent_outdated') {
          void consentDocs.refetch()
          setErrors({ form: 'Текст согласия обновился. Прочитайте его ещё раз и подтвердите.' })
          setConsent(false)
        } else {
          setErrors({ form: err.message })
        }
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
        <h1 className="t-title text-brand">Создайте аккаунт</h1>
        <p className="t-body text-ink-2">{ROLE_LEAD[role]}</p>
      </header>

      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-5">
        <Segmented
          aria-label="Кто вы"
          options={ROLE_OPTIONS}
          value={role}
          onChange={setRole}
          block
        />

        <Input
          label="Email"
          type="email"
          name="email"
          autoComplete="email"
          inputMode="email"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value)
            setErrors((x) => ({ ...x, email: undefined, form: undefined }))
          }}
          error={errors.email}
          hint={
            role === 'employer'
              ? 'Лучше рабочая почта: по домену мы определим вашу компанию.'
              : 'На этот адрес придёт 6-значный код.'
          }
        />

        <PasswordInput
          label="Пароль"
          name="new-password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => {
            setPassword(e.target.value)
            setErrors((x) => ({ ...x, password: undefined, form: undefined }))
          }}
          error={errors.password}
          hint="Минимум 8 символов, хотя бы одна буква и одна цифра."
        />

        {role === 'candidate' && (
          <Input
            label="Год рождения"
            name="birth_year"
            inputMode="numeric"
            autoComplete="bday-year"
            maxLength={4}
            value={birthYear}
            onChange={(e) => {
              setBirthYear(e.target.value.replace(/\D/g, '').slice(0, 4))
              setErrors((x) => ({ ...x, birthYear: undefined, form: undefined }))
            }}
            error={errors.birthYear}
            hint="Платформа доступна с 15 лет. Если вам 15–17, понадобится согласие родителя или законного представителя; работодатели смогут предлагать только стажировку или лёгкий труд."
          />
        )}

        <div className="flex flex-col gap-2">
          <Checkbox
            checked={consent}
            onChange={(e) => {
              setConsent(e.target.checked)
              setErrors((x) => ({ ...x, consent: undefined }))
            }}
            error={errors.consent}
            label={
              <>
                Даю согласие на обработку персональных данных{' '}
                <button
                  type="button"
                  onClick={(e) => {
                    e.preventDefault()
                    setShowConsentText((v) => !v)
                  }}
                  aria-expanded={showConsentText}
                  className="t-label text-brand underline-offset-4 hover:underline"
                >
                  {showConsentText ? 'Скрыть текст' : 'Что это значит'}
                </button>
              </>
            }
          />
          {showConsentText && (
            <div className="ml-6 rounded-panel bg-panel p-4 t-small text-ink-2">
              {consentDocs.isPending ? (
                <Skeleton className="h-12 w-full" />
              ) : processing ? (
                <>
                  <p className="t-label text-ink">{processing.title}</p>
                  <p className="mt-1">{processing.summary}</p>
                  <p className="mt-2 text-ink-3">Версия текста от {processing.version}. Другие согласия вы даёте отдельно, по мере необходимости.</p>
                </>
              ) : (
                <p>Не удалось загрузить текст согласия. Обновите страницу.</p>
              )}
            </div>
          )}
        </div>

        {errors.form && <Alert tone="danger">{errors.form}</Alert>}

        <Button type="submit" size="lg" block loading={loading}>
          Получить код
        </Button>
      </form>

      <p className="t-body text-ink-2">
        Уже есть аккаунт?{' '}
        <Link to="/auth/login" className="t-label text-brand underline-offset-4 hover:underline">
          Войти
        </Link>
      </p>
    </div>
  )
}
