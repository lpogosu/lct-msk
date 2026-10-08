import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, unwrap } from '@/api/client'
import { ApiException, errorMessage } from '@/api/errors'
import { Sheet } from '@/features/candidate/components/Sheet'
import { Alert, Button, Checkbox, Skeleton } from '@/ui'
import { useFspAuthResult } from './useFspAuthResult'

/**
 * «Войти через ФСП ID» на экранах входа и регистрации (Q-13). Настоящий ФСП ID (Keycloak) — если он отвечает,
 * иначе на демо-стенде — «демо-вход ФСП» с вымышленными спортсменами. Для кандидата: аккаунт привязан к ФСП ID,
 * достижения подтянутся при согласии на обогащение. Регистрация по email остаётся рядом.
 */
export function FspIdButton() {
  const [open, setOpen] = useState(false)
  const [consent, setConsent] = useState(false)
  const [enrich, setEnrich] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const onResult = useFspAuthResult()

  const avail = useQuery({
    queryKey: ['auth', 'fsp-availability'],
    queryFn: () => unwrap(api.GET('/api/fsp/availability')),
    retry: false,
    enabled: open,
  })
  const athletes = useQuery({
    queryKey: ['auth', 'fsp-demo-athletes'],
    queryFn: () => unwrap(api.GET('/api/fsp/demo/athletes')),
    enabled: open && avail.data?.demo_login_available === true,
    retry: false,
  })

  const needConsent = () => {
    if (consent) return false
    setError('Отметьте согласие на обработку персональных данных: без него нельзя создать аккаунт через ФСП ID')
    return true
  }
  const fail = (e: unknown) =>
    setError(e instanceof ApiException && e.code === 'consent_required' ? e.message : errorMessage(e))

  const startOidc = useMutation({
    mutationFn: () =>
      unwrap(
        api.GET('/api/fsp/login', {
          params: { query: { mode: 'login', consent_personal_data: consent, consent_fsp_enrichment: enrich } },
        }),
      ),
    onSuccess: (r) => {
      window.location.href = r.authorization_url
    },
    onError: fail,
  })
  const demo = useMutation({
    mutationFn: (sub: string) =>
      unwrap(
        api.POST('/api/fsp/demo-login', {
          body: { sub, mode: 'login', consent_personal_data: consent, consent_fsp_enrichment: enrich },
        }),
      ),
    onSuccess: onResult,
    onError: fail,
  })

  return (
    <>
      <Button type="button" variant="outline" size="lg" block onClick={() => setOpen(true)}>
        Войти через ФСП ID
      </Button>
      <Sheet
        open={open}
        onClose={() => setOpen(false)}
        title="Вход через ФСП ID"
        description="Аккаунт кандидата привяжется к вашему ФСП ID, а достижения из реестра подтянутся в профиль. Они усиливают подбор, но не меняют грейд."
      >
        <div className="flex flex-col gap-4">
          <Checkbox
            checked={consent}
            onChange={(e) => {
              setConsent(e.target.checked)
              setError(null)
            }}
            label="Даю согласие на обработку персональных данных (нужно, если аккаунта ещё нет)"
          />
          <Checkbox
            checked={enrich}
            onChange={(e) => setEnrich(e.target.checked)}
            label="Подтянуть достижения из реестра ФСП в профиль"
          />
          {avail.isPending && <Skeleton className="h-12 w-full rounded-pill" />}
          {avail.data?.fsp_id_available && (
            <Button
              type="button"
              size="lg"
              block
              loading={startOidc.isPending}
              onClick={() => {
                if (!needConsent()) startOidc.mutate()
              }}
            >
              Продолжить в ФСП ID
            </Button>
          )}
          {avail.data?.demo_login_available && (
            <div className="flex flex-col gap-3">
              <Alert>
                {avail.data.fsp_id_available
                  ? 'Или демо-вход: вымышленные спортсмены демо-реестра ФСП.'
                  : 'ФСП ID сейчас недоступен, но на стенде работает «демо-вход ФСП»: выберите вымышленного спортсмена демо-реестра.'}
              </Alert>
              {athletes.isPending && <Skeleton className="h-24 w-full rounded-panel-lg" />}
              {athletes.data?.map((a) => (
                <button
                  key={a.sub}
                  type="button"
                  disabled={demo.isPending}
                  onClick={() => {
                    if (!needConsent()) demo.mutate(a.sub)
                  }}
                  className="flex flex-col gap-0.5 rounded-panel-lg border border-hairline bg-white px-4 py-3 text-left hover:border-brand disabled:opacity-60"
                >
                  <span className="t-label text-ink">{a.label}</span>
                  <span className="t-small text-ink-3">{a.summary}</span>
                </button>
              ))}
            </div>
          )}
          {avail.data && !avail.data.fsp_id_available && !avail.data.demo_login_available && (
            <Alert tone="danger" title="ФСП ID недоступен">
              Войти через ФСП ID сейчас нельзя. Зарегистрируйтесь по email: ФСП ID можно привязать позже в разделе «ФСП».
            </Alert>
          )}
          {avail.isError && <Alert tone="danger">{errorMessage(avail.error)}</Alert>}
          {error && <Alert tone="danger">{error}</Alert>}
        </div>
      </Sheet>
    </>
  )
}
