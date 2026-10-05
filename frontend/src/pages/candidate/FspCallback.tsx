import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import { qk } from '@/features/candidate/queries'
import { Alert, Button, MobileHeader, Spinner } from '@/ui'

/** Возврат из ФСП ID: обмениваем `code` и `state` на привязку (OIDC authorization code + PKCE делает бэкенд). */
export default function CandidateFspCallback() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const done = useRef(false)

  useEffect(() => {
    if (done.current) return
    done.current = true
    const code = params.get('code')
    const state = params.get('state')
    if (!code || !state) {
      setError(params.get('error_description') ?? 'В адресе нет кода возврата от ФСП ID. Начните привязку заново.')
      return
    }
    unwrap(api.GET('/api/fsp/callback', { params: { query: { code, state } } }))
      .then(() => {
        void qc.invalidateQueries({ queryKey: qk.fsp })
        navigate('/candidate/fsp', { replace: true })
      })
      .catch((e) => setError(errorMessage(e)))
  }, [params, navigate, qc])

  return (
    <>
      <MobileHeader title="ФСП ID" backTo="/candidate/fsp" />
      {error ? (
        <div className="flex flex-col gap-4">
          <Alert tone="danger" title="Не удалось привязать ФСП ID">
            {error}
          </Alert>
          <Button to="/candidate/fsp">Вернуться к ФСП</Button>
        </div>
      ) : (
        <div className="flex items-center gap-3 text-ink-2">
          <Spinner label="Привязываем ФСП ID" />
          <p className="t-body">Привязываем ФСП ID…</p>
        </div>
      )}
    </>
  )
}
