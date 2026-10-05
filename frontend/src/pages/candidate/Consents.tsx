import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import { Chip, SectionTitle } from '@/features/candidate/components/Bits'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { Sheet } from '@/features/candidate/components/Sheet'
import { qk } from '@/features/candidate/queries'
import type { Schemas } from '@/api/types'
import { Alert, Button, MobileHeader, Panel, useToast } from '@/ui'

type Entry = Schemas['ConsentJournalOut']
type Doc = Schemas['ConsentDocumentOut']

/**
 * Журнал согласий (152-ФЗ, К13): каждое согласие — отдельная запись с версией текста; отзыв не удаляет запись,
 * а ставит дату отзыва. Согласие на обработку ПДн отзывается только вместе с удалением аккаунта.
 */
export default function CandidateConsents() {
  const journal = useQuery({ queryKey: qk.consents, queryFn: () => unwrap(api.GET('/api/candidates/me/consents')) })
  const docs = useQuery({ queryKey: qk.consentDocs, queryFn: () => unwrap(api.GET('/api/consents/documents')), staleTime: 10 * 60_000 })
  const [revoke, setRevoke] = useState<Entry | null>(null)

  return (
    <>
      <MobileHeader title="Согласия" backTo="/candidate/profile" />
      <div className="flex flex-col gap-8">
        <header>
          <h1 className="t-title text-brand">Согласия</h1>
          <p className="mt-2 t-body text-ink-2">Каждое согласие записано отдельно, с версией текста. Отозвать можно в любой момент.</p>
        </header>

        <QueryBoundary query={journal}>
          {(entries) => {
            const active = entries.filter((e) => e.active)
            const past = entries.filter((e) => !e.active)
            const givenPurposes = new Set(active.map((e) => e.purpose))
            const missing = (docs.data ?? []).filter((d) => d.grantable_in_journal && !givenPurposes.has(d.purpose))
            return (
              <>
                <section>
                  <SectionTitle>Действуют</SectionTitle>
                  {active.length === 0 ? (
                    <p className="t-body text-ink-3">Действующих согласий нет.</p>
                  ) : (
                    <ul className="flex flex-col gap-3">
                      {active.map((e) => (
                        <li key={e.id}>
                          <EntryCard e={e} onRevoke={() => setRevoke(e)} />
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                {missing.length > 0 && (
                  <section>
                    <SectionTitle>Можно дать</SectionTitle>
                    <ul className="flex flex-col gap-3">
                      {missing.map((d) => (
                        <li key={d.purpose}>
                          <GrantCard d={d} />
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

                {past.length > 0 && (
                  <section>
                    <SectionTitle>Отозваны</SectionTitle>
                    <ul className="flex flex-col gap-3">
                      {past.map((e) => (
                        <li key={e.id}>
                          <EntryCard e={e} />
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
              </>
            )
          }}
        </QueryBoundary>
      </div>
      <RevokeSheet entry={revoke} onClose={() => setRevoke(null)} />
    </>
  )
}

function EntryCard({ e, onRevoke }: { e: Entry; onRevoke?: () => void }) {
  const company = typeof e.scope?.company_name === 'string' ? e.scope.company_name : null
  return (
    <Panel variant="outline" radius="lg" padding="sm" className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-3">
        <p className="t-body-strong text-ink">{e.title}</p>
        <Chip tone={e.active ? 'tint' : 'panel'}>{e.active ? 'действует' : 'отозвано'}</Chip>
      </div>
      {company && <p className="t-small text-ink-2">Компания: {company}</p>}
      <p className="t-small text-ink-3">
        Версия текста {e.text_version} · дано {e.granted_ago}
        {e.revoked_ago ? ` · отозвано ${e.revoked_ago}` : ''}
      </p>
      {e.active && <p className="t-small text-ink-3">Если отозвать: {e.effect_of_revoke}</p>}
      {e.active && onRevoke && (
        <div>
          {e.revocable ? (
            <Button variant="outline" size="sm" onClick={onRevoke}>
              Отозвать
            </Button>
          ) : (
            <p className="t-small text-ink-3">Отозвать кнопкой нельзя: напишите в поддержку.</p>
          )}
        </div>
      )}
    </Panel>
  )
}

function GrantCard({ d }: { d: Doc }) {
  const qc = useQueryClient()
  const toast = useToast()
  const grant = useMutation({
    mutationFn: () => unwrap(api.POST('/api/consents', { body: { purpose: d.purpose, text_version: d.version } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.consents })
      void qc.invalidateQueries({ queryKey: qk.privacy })
      toast.success('Согласие записано в журнал.')
    },
    onError: (e) => toast.error(errorMessage(e)),
  })
  return (
    <Panel variant="panel" radius="lg" padding="sm" className="flex flex-col gap-2">
      <p className="t-body-strong text-ink">{d.title}</p>
      <p className="t-small text-ink-2">{d.summary}</p>
      <div>
        <Button size="sm" variant="outline" loading={grant.isPending} onClick={() => grant.mutate()}>
          Дать согласие
        </Button>
      </div>
    </Panel>
  )
}

function RevokeSheet({ entry, onClose }: { entry: Entry | null; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const revoke = useMutation({
    mutationFn: () => unwrap(api.POST('/api/consents/{consent_id}/revoke', { params: { path: { consent_id: entry!.id } } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.consents })
      void qc.invalidateQueries({ queryKey: qk.privacy })
      void qc.invalidateQueries({ queryKey: qk.fsp })
      toast.success('Согласие отозвано, запись осталась в журнале.')
      onClose()
    },
  })
  return (
    <Sheet open={entry != null} onClose={onClose} title="Отозвать согласие?" description={entry?.title}>
      <div className="flex flex-col gap-4">
        <p className="t-body text-ink-2">{entry?.effect_of_revoke}</p>
        {revoke.error ? <Alert tone="danger">{errorMessage(revoke.error)}</Alert> : null}
        <Button block variant="outline" loading={revoke.isPending} onClick={() => revoke.mutate()}>
          Отозвать
        </Button>
        <Button block variant="text" onClick={onClose}>
          Оставить
        </Button>
      </div>
    </Sheet>
  )
}
