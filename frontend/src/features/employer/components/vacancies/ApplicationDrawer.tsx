import { useState } from 'react'
import { errorMessage } from '@/api/errors'
import { formatDecimal } from '@/lib/format'
import { Alert, Button, ScoreRing, Skeleton, useToast } from '@/ui'
import { Drawer } from '@/ui/Drawer'
import { useApplication, useApplicationAction } from '../../api'
import { REJECT_REASONS, SCORE_PARTS } from '../../dict'
import { BlockTitle, Label, LoadError, Tag } from '../common'
import { ReasonDrawer } from '../ReasonDrawer'

const FIELD_LABEL: Record<string, string> = {
  full_name: 'Имя',
  email: 'Email',
  phone: 'Телефон',
  telegram: 'Telegram',
  city: 'Город',
  current_employer: 'Работает',
  university: 'Вуз',
}

/**
 * Отклик кандидата (Р10): он откликнулся сам и дал согласие, поэтому контакты видны сразу. Score — против брифа вакансии.
 * Ответить нужно за 7 дней: «есть интерес» или отказ с причиной (причина уходит кандидату со ссылкой на задачу).
 */
export function ApplicationDrawer({
  id,
  skills,
  onClose,
}: {
  id: string
  /** Обязательные навыки вакансии — для причины «не хватило навыков» */
  skills: { id: string; name: string }[]
  onClose: () => void
}) {
  const toast = useToast()
  const q = useApplication(id)
  const act = useApplicationAction()
  const [rejecting, setRejecting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const d = q.data
  const cand = (d?.candidate ?? {}) as Record<string, unknown>
  const snap = (d?.score_snapshot ?? null) as { score?: number; parts?: Record<string, number> } | null

  async function interest() {
    try {
      await act.mutateAsync({ id, action: { kind: 'interest' } })
      toast.success('Отметили: есть интерес. Кандидат увидит это в своих откликах.')
    } catch (e) {
      toast.error(errorMessage(e), 'Не получилось')
    }
  }

  return (
    <>
      <Drawer open onClose={onClose} width={600} eyebrow={d ? d.status_label : 'Отклик'} title={d ? d.pseudonym : 'Отклик'}>
        {q.isPending ? (
          <div className="flex flex-col gap-4" role="status" aria-label="Загрузка">
            <Skeleton className="h-8 w-2/3" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : q.isError ? (
          <LoadError error={q.error} onRetry={() => void q.refetch()} />
        ) : d ? (
          <div className="flex flex-col gap-7">
            <header className="flex items-start justify-between gap-4">
              <div>
                <p className="t-name text-ink">{d.vacancy.title}</p>
                <p className="t-small text-ink-3">
                  {d.category} · откликнулся {d.submitted_text}
                </p>
                {d.timers.sla_due_text && <p className="mt-1 t-small text-danger-text">Ответить {d.timers.sla_due_text}</p>}
              </div>
              {snap?.score != null && (
                <ScoreRing value={Math.round(snap.score)} size={80} label={`Score ${formatDecimal(snap.score, 1)} из 100`} />
              )}
            </header>

            {snap?.parts && (
              <ul className="m-0 flex list-none flex-wrap gap-x-5 gap-y-1 p-0 t-small text-ink-2">
                {SCORE_PARTS.map((p) => (
                  <li key={p.key}>
                    {p.label}: <span className="font-mono text-ink">{formatDecimal(snap.parts?.[p.key] ?? 0, 1)}</span>
                  </li>
                ))}
              </ul>
            )}

            <section className="flex flex-col gap-2" aria-label="Контакты кандидата">
              <BlockTitle>Кандидат</BlockTitle>
              {d.contacts_visible ? (
                <dl className="m-0 grid grid-cols-[110px_minmax(0,1fr)] gap-x-4 gap-y-1 t-body">
                  {Object.entries(FIELD_LABEL).map(([k, label]) =>
                    cand[k] ? (
                      <div key={k} className="contents">
                        <dt className="t-small text-ink-3">{label}</dt>
                        <dd className="m-0 text-ink [overflow-wrap:anywhere]">{String(cand[k])}</dd>
                      </div>
                    ) : null,
                  )}
                </dl>
              ) : (
                <p className="t-body text-ink-2">Контакты скрыты: кандидат отозвал отклик или согласие.</p>
              )}
              {typeof cand.fsp_line === 'string' && <p className="t-small text-ink-2">{cand.fsp_line}</p>}
              {typeof cand.resume === 'string' && cand.resume && (
                <div>
                  <Label>Самоописание — заявлено кандидатом, на Score не влияет</Label>
                  <p className="t-body text-ink-2 [overflow-wrap:anywhere]">{cand.resume}</p>
                </div>
              )}
            </section>

            {d.reject_reason_label && (
              <Alert tone="info" title="Вы отказали">
                {d.reject_reason_label}
              </Alert>
            )}
            {d.status === 'interested' && <Tag tone="brand">Есть интерес — свяжитесь с кандидатом</Tag>}
            {d.actions.includes('view') && <p className="t-small text-ink-3">Отклик открыт и отмечен просмотренным.</p>}

            <section className="flex flex-col gap-2" aria-label="Журнал">
              <BlockTitle>Журнал</BlockTitle>
              <ol className="m-0 flex list-none flex-col gap-1 p-0">
                {d.events.map((e) => (
                  <li key={e.seq} className="flex gap-3 t-small">
                    <span className="w-5 shrink-0 font-mono text-ink-3">{e.seq}</span>
                    <span className="text-ink">{e.label}</span>
                  </li>
                ))}
              </ol>
            </section>

            {(d.actions.includes('interest') || d.actions.includes('reject')) && (
              <div className="flex flex-wrap gap-3">
                {d.actions.includes('interest') && (
                  <Button loading={act.isPending} onClick={() => void interest()}>
                    Есть интерес
                  </Button>
                )}
                {d.actions.includes('reject') && (
                  <Button variant="outline" disabled={act.isPending} onClick={() => setRejecting(true)}>
                    Отказать с причиной
                  </Button>
                )}
              </div>
            )}
          </div>
        ) : null}
      </Drawer>
      {rejecting && (
        <ReasonDrawer
          open
          onClose={() => setRejecting(false)}
          eyebrow={d?.pseudonym}
          title="Отказ по отклику"
          description="Кандидат получит причину и ссылку на короткую задачу по модулю теста — так отказ становится полезным."
          options={REJECT_REASONS}
          submitLabel="Отказать"
          skills={skills}
          busy={act.isPending}
          error={error}
          onSubmit={async (r) => {
            setError(null)
            try {
              await act.mutateAsync({ id, action: { kind: 'reject', reason: r.reason, comment: r.comment, skillIds: r.skillIds } })
              toast.success('Отказали. Причина ушла кандидату.')
              setRejecting(false)
            } catch (e) {
              setError(errorMessage(e))
            }
          }}
        />
      )}
    </>
  )
}
