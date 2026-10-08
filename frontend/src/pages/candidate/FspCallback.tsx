import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import { getSession } from '@/api/session'
import { useAuth } from '@/auth/useAuth'
import { qk } from '@/features/candidate/queries'
import { useFspAuthResult } from '@/pages/auth/useFspAuthResult'
import { Alert, Button, Spinner } from '@/ui'

/**
 * Возврат из ФСП ID (OIDC authorization code + PKCE делает бэкенд). Страница публичная: без сессии это вход/создание
 * аккаунта кандидата (Q-13), с сессией — привязка ФСП ID к текущему аккаунту. Ждём, пока восстановится сессия.
 */
export default function FspCallback() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { status } = useAuth()
  const onAuth = useFspAuthResult()
  const [error, setError] = useState<string | null>(null)
  const done = useRef(false)

  useEffect(() => {
    if (done.current || status === 'unknown') return
    done.current = true
    const code = params.get('code')
    const state = params.get('state')
    if (!code || !state) {
      setError(params.get('error_description') ?? 'В адресе нет кода возврата от ФСП ID. Начните вход заново.')
      return
    }
    const wasAuthed = getSession().status === 'authenticated'
    unwrap(api.GET('/api/fsp/callback', { params: { query: { code, state } } }))
      .then((r) => {
        if (r.result === 'linked') {
          void qc.invalidateQueries({ queryKey: qk.fsp })
          navigate('/candidate/fsp', { replace: true })
        } else {
          onAuth(r)
          if (!wasAuthed && r.result === 'logged_in') navigate('/candidate', { replace: true })
        }
      })
      .catch((e) => setError(errorMessage(e)))
  }, [params, navigate, qc, status, onAuth])

  return error ? (
    <div className="flex flex-col gap-4">
      <Alert tone="danger" title="Не удалось войти через ФСП ID">
        {error}
      </Alert>
      <Button to="/auth/login">Вернуться ко входу</Button>
    </div>
  ) : (
    <div className="flex items-center gap-3 text-ink-2">
      <Spinner label="Завершаем вход через ФСП ID" />
      <p className="t-body">Завершаем вход через ФСП ID…</p>
    </div>
  )
}
