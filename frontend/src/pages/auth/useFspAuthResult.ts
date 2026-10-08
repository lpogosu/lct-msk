import { useNavigate } from 'react-router-dom'
import { applyTokenResponse } from '@/api/session'
import type { Schemas } from '@/api/types'
import { startSignup } from '@/auth/signupFlow'

type AuthResult = Schemas['FspAuthResultOut']

/** Обработка ответа входа через ФСП ID (общая для демо-входа и страницы возврата из Keycloak). */
export function useFspAuthResult() {
  const navigate = useNavigate()
  return (r: AuthResult) => {
    if (r.result === 'logged_in' && r.token) {
      applyTokenResponse(r.token) // RedirectIfAuthed откроет кабинет кандидата
      return
    }
    if (r.result === 'email_verification_required' && r.verification) {
      // Первый вход вне DEMO: код на email из ФСП ID, пароль задаётся на экране кода
      startSignup({
        email: r.verification.email,
        password: null,
        role: 'candidate',
        codeTtlS: r.verification.code_ttl_s,
        resendAvailableInS: r.verification.resend_available_in_s,
      })
      navigate('/verify-email')
    }
  }
}
