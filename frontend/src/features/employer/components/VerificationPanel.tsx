import { useState, type FormEvent } from 'react'
import { errorMessage } from '@/api/errors'
import { CompanyTrustBadge } from '@/features/trust/CompanyTrustBadge'
import { useSubmitVerification, useVerification, useWithdrawVerification } from '@/features/trust/api'
import { Alert, Button, Input, Skeleton, VerifiedMark, useToast } from '@/ui'
import { BlockTitle } from './common'

/**
 * «Проверенная компания»: добровольная метка. Кандидат видит её в приглашении и вакансии. Метка ничего не блокирует:
 * без неё приглашения, вакансии и квоты работают как обычно. Реестр в этой версии учебный (вымышленные компании).
 */
export function VerificationPanel() {
  const q = useVerification()
  const submit = useSubmitVerification()
  const withdraw = useWithdrawVerification()
  const toast = useToast()
  const [inn, setInn] = useState('')
  const [ogrn, setOgrn] = useState('')

  const v = q.data
  const failed = v?.submitted && !v.verified && !v.revoked

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    try {
      const res = await submit.mutateAsync({ inn: inn.trim(), ogrn: ogrn.trim() })
      if (res.verified) toast.success(`Метка получена: ${res.label}.`)
    } catch {
      /* текст ошибки показывает submit.error */
    }
  }

  return (
    <section aria-labelledby="verify-h" className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-3">
        <BlockTitle>
          <span id="verify-h">Проверенная компания</span>
        </BlockTitle>
        <CompanyTrustBadge trust={v} />
      </div>
      <p className="t-body text-ink-2">
        Необязательно. Укажите ИНН и ОГРН: мы проверим контрольные суммы, найдём компанию в реестре, сверим статус «действующая», название из профиля и домен
        вашей рабочей почты. Кандидат увидит метку рядом с названием компании. Без метки всё работает как прежде.
      </p>

      {q.isPending ? (
        <Skeleton className="h-24 w-full rounded-panel" />
      ) : q.isError ? (
        <Alert tone="danger">{errorMessage(q.error)}</Alert>
      ) : (
        <>
          {v?.revoked && (
            <Alert tone="danger" title="Метка снята модератором платформы">
              Причина: {v.revoke_reason ?? 'не указана'}. Работа компании не ограничена. Чтобы вернуть метку, напишите в поддержку.
            </Alert>
          )}

          {v?.submitted && (
            <ul className="m-0 flex list-none flex-col gap-1.5 p-0" aria-label="Результат проверки">
              {v.checks.map((c) => (
                <li key={c.code} className="flex items-center gap-2 t-body">
                  {c.ok ? <VerifiedMark size={14} /> : <span aria-hidden="true" className="inline-block h-3.5 w-3.5 shrink-0 rounded-full border border-dashed border-ink-3" />}
                  <span className={c.ok ? 'text-ink' : 'text-ink-3'}>
                    {c.label}
                    <span className="sr-only">{c.ok ? ': пройдена' : ': не пройдена'}</span>
                  </span>
                </li>
              ))}
              {v.registry_name && <li className="t-small text-ink-3">В реестре: {v.registry_name}</li>}
            </ul>
          )}
          {failed && (
            <Alert title="Метка не выдана">
              Проверьте реквизиты и название компании в профиле. Это не мешает работе: приглашения и вакансии доступны.
            </Alert>
          )}

          {!v?.revoked && (
            <form onSubmit={onSubmit} noValidate className="grid gap-4 sm:grid-cols-2">
              <Input
                label="ИНН"
                inputMode="numeric"
                maxLength={12}
                value={inn}
                onChange={(e) => setInn(e.target.value.replace(/\D/g, ''))}
                placeholder={v?.inn ?? '10 или 12 цифр'}
              />
              <Input
                label="ОГРН"
                inputMode="numeric"
                maxLength={15}
                value={ogrn}
                onChange={(e) => setOgrn(e.target.value.replace(/\D/g, ''))}
                placeholder={v?.ogrn ?? '13 или 15 цифр'}
              />
              {submit.error ? (
                <div className="sm:col-span-2">
                  <Alert tone="danger">{errorMessage(submit.error)}</Alert>
                </div>
              ) : null}
              <div className="flex flex-wrap gap-3 sm:col-span-2">
                <Button type="submit" loading={submit.isPending} disabled={inn.length < 10 || ogrn.length < 13}>
                  {v?.submitted ? 'Проверить заново' : 'Проверить реквизиты'}
                </Button>
                {v?.submitted && (
                  <Button variant="outline" type="button" loading={withdraw.isPending} onClick={() => withdraw.mutate()}>
                    Убрать метку и реквизиты
                  </Button>
                )}
              </div>
            </form>
          )}
        </>
      )}
    </section>
  )
}
