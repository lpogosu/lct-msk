import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import type { components } from '@/api/schema'
import { API_BASE, getAccessToken } from '@/api/session'
import { errorMessage } from '@/api/errors'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { SectionTitle } from '@/features/candidate/components/Bits'
import { formatDate } from '@/lib/format'
import { Alert, Button, Checkbox, MobileHeader, Panel, useToast } from '@/ui'

type Credential = components['schemas']['CredentialOut']
const STATUS: Record<Credential['status'], string> = { active: 'Действует', revoked: 'Отозван', expired: 'Срок истёк' }
const KEY = ['candidate', 'credentials'] as const

/** К8а «Паспорт навыков»: подписанные учётные данные (W3C VC / Open Badges 3.0) — выпуск, JSON, QR, отзыв. Издатель демонстрационный. */
export default function CandidatePassport() {
  const qc = useQueryClient()
  const toast = useToast()
  const [withName, setWithName] = useState(false)
  const list = useQuery({ queryKey: KEY, queryFn: () => unwrap(api.GET('/api/credentials/me')), retry: false })

  const issue = useMutation({
    mutationFn: (o: { kind: 'category' | 'fsp'; ref: string }) =>
      unwrap(api.POST('/api/credentials/me', { body: { kind: o.kind, ref: o.ref, include_name: withName } })),
    onSuccess: () => {
      toast.success('Паспорт выпущен.')
      void qc.invalidateQueries({ queryKey: KEY })
    },
  })
  const revoke = useMutation({
    mutationFn: (id: string) => unwrap(api.POST('/api/credentials/me/{credential_id}/revoke', { params: { path: { credential_id: id } } })),
    onSuccess: () => {
      toast.success('Паспорт отозван: проверка его больше не пройдёт.')
      void qc.invalidateQueries({ queryKey: KEY })
    },
  })
  const download = useMutation({
    mutationFn: async (c: Credential) => {
      const res = await fetch(`${API_BASE}/api/credentials/me/${c.id}/file`, { headers: { Authorization: `Bearer ${getAccessToken() ?? ''}` } })
      if (!res.ok) throw new Error('Не удалось скачать файл')
      const url = URL.createObjectURL(await res.blob())
      const a = document.createElement('a')
      a.href = url
      a.download = `vzaimno-passport-${c.code.slice(0, 8)}.json`
      document.body.appendChild(a)
      a.click()
      a.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
    },
  })

  const err = issue.error ?? revoke.error ?? download.error
  return (
    <>
      <MobileHeader title="Паспорт навыков" backTo="/candidate/profile" />
      <div className="flex flex-col gap-7">
        <header>
          <h1 className="t-title text-brand">Паспорт навыков</h1>
          <p className="mt-2 t-body text-ink-2">
            Подписанный файл с вашей категорией или достижением ФСП. Его можно отправить кому угодно: подлинность проверяется на странице /v без входа.
          </p>
        </header>
        <Alert>Демонстрационный издатель: паспорт не является официальным документом ФСП.</Alert>
        {err ? <Alert tone="danger">{errorMessage(err)}</Alert> : null}

        <QueryBoundary query={list}>
          {(d) => (
            <>
              <section>
                <SectionTitle as="h3">Что можно выпустить</SectionTitle>
                <Checkbox checked={withName} onChange={(e) => setWithName(e.target.checked)} label="Указать моё имя в паспорте (по умолчанию — только псевдонимный идентификатор)" />
                {d.offers.length === 0 ? (
                  <p className="mt-3 t-body text-ink-2">Пока нечего выпускать: пройдите тест или привяжите ФСП ID.</p>
                ) : (
                  <ul className="mt-3 flex list-none flex-col gap-2 p-0">
                    {d.offers.map((o) => (
                      <li key={`${o.kind}:${o.ref}`}>
                        <Panel variant="panel" radius="md" padding="sm" className="flex items-center justify-between gap-3">
                          <div>
                            <p className="t-body-strong text-ink">{o.title}</p>
                            <p className="t-small text-ink-3">{o.detail}</p>
                          </div>
                          <Button size="sm" loading={issue.isPending} onClick={() => issue.mutate({ kind: o.kind, ref: o.ref })}>
                            Выпустить
                          </Button>
                        </Panel>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section>
                <SectionTitle as="h3">Выпущенные</SectionTitle>
                {d.items.length === 0 ? (
                  <p className="t-body text-ink-2">Ещё ничего не выпущено.</p>
                ) : (
                  <ul className="flex list-none flex-col gap-3 p-0">
                    {d.items.map((c) => (
                      <li key={c.id}>
                        <Panel variant="panel" radius="lg" padding="sm" className="flex flex-col gap-3">
                          <div className="flex items-start gap-3">
                            <img src={c.qr} alt={`QR-код проверки: ${c.title}`} width={96} height={96} className="rounded-md bg-white" />
                            <div className="min-w-0">
                              <p className="t-body-strong text-ink">{c.title}</p>
                              <p className="t-small text-ink-3">
                                {STATUS[c.status]} · выдан {formatDate(c.issued_at)} · до {formatDate(c.valid_until)}
                              </p>
                              <p className="t-small text-ink-3">{c.include_name ? 'С именем' : 'Без имени (псевдоним)'}</p>
                              <a className="break-all t-small text-brand underline" href={c.verify_url} target="_blank" rel="noreferrer">
                                {new URL(c.verify_url, window.location.origin).toString()}
                              </a>
                            </div>
                          </div>
                          <div className="flex flex-wrap gap-2">
                            <Button size="sm" variant="outline" onClick={() => download.mutate(c)}>
                              Скачать JSON
                            </Button>
                            {c.status === 'active' && (
                              <Button size="sm" variant="outline" loading={revoke.isPending} onClick={() => revoke.mutate(c.id)}>
                                Отозвать
                              </Button>
                            )}
                          </div>
                        </Panel>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              <p className="t-small text-ink-3">Издатель: {d.issuer_did}</p>
            </>
          )}
        </QueryBoundary>
      </div>
    </>
  )
}
