import { Alert, Skeleton } from '@/ui'
import { Drawer } from '@/ui/Drawer'
import { useInvitation } from '../../api'
import { BlockTitle, Label, LoadError, Tag } from '../common'

const ROLE_LABEL: Record<string, string> = { employer: 'вы', candidate: 'кандидат', system: 'система' }

/**
 * Карточка приглашения: неизменяемые снимки условий с хешем (v1, после встречного — v2), «почему вы» и журнал событий.
 * До взаимного «да» кандидат здесь — псевдоним; имя и контакты есть, только если он согласился их передать.
 */
export function InvitationDetailDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useInvitation(id)
  const d = q.data
  const cand = (d?.candidate ?? {}) as Record<string, unknown>
  const disclosed = cand.view === 'disclosed'

  return (
    <Drawer open onClose={onClose} width={600} eyebrow={d ? d.status_label : 'Приглашение'} title={d ? d.pseudonym : 'Приглашение'}>
      {q.isPending ? (
        <div className="flex flex-col gap-4" role="status" aria-label="Загрузка">
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : q.isError ? (
        <LoadError error={q.error} onRetry={() => void q.refetch()} />
      ) : d ? (
        <div className="flex flex-col gap-7">
          <div className="flex flex-col gap-1">
            <p className="t-name text-ink [overflow-wrap:anywhere]">{d.title}</p>
            <p className="t-num-m text-ink">{d.salary.text}</p>
            <p className="t-small text-ink-3">
              {d.sent_text}
              {d.score != null ? ` · Score при отправке ${d.score}` : ''}
              {d.rank_at_send != null ? ` · место в выдаче ${d.rank_at_send}` : ''}
            </p>
          </div>

          {d.caution.length > 0 && (
            <Alert tone="danger" title="Требует осторожности">
              {d.caution.join('; ')}
            </Alert>
          )}

          <section className="flex flex-col gap-2" aria-label="Кандидат">
            <BlockTitle>Кандидат</BlockTitle>
            {disclosed ? (
              <dl className="m-0 grid grid-cols-[110px_minmax(0,1fr)] gap-x-4 gap-y-1 t-body">
                {(['full_name', 'email', 'phone', 'telegram', 'city', 'current_employer', 'university'] as const).map((k) =>
                  cand[k] ? (
                    <div key={k} className="contents">
                      <dt className="t-small text-ink-3">
                        {{ full_name: 'Имя', email: 'Email', phone: 'Телефон', telegram: 'Telegram', city: 'Город', current_employer: 'Работает', university: 'Вуз' }[k]}
                      </dt>
                      <dd className="m-0 text-ink [overflow-wrap:anywhere]">{String(cand[k])}</dd>
                    </div>
                  ) : null,
                )}
              </dl>
            ) : (
              <p className="t-body text-ink-2">
                Имя и контакты скрыты до взаимного «да». Вы видите категорию, уровень по тесту и достижения — без персональных данных.
              </p>
            )}
            {typeof cand.fsp_line === 'string' && <p className="t-small text-ink-2">{cand.fsp_line}</p>}
            {disclosed && typeof cand.resume === 'string' && cand.resume && (
              <div>
                <Label>Самоописание (заявлено кандидатом)</Label>
                <p className="t-body text-ink-2 [overflow-wrap:anywhere]">{cand.resume}</p>
              </div>
            )}
          </section>

          {d.why_you.length > 0 && (
            <section className="flex flex-col gap-2" aria-label="Почему вы">
              <BlockTitle>Что мы написали кандидату: «почему вы»</BlockTitle>
              <ul className="m-0 flex list-none flex-col gap-1 p-0">
                {d.why_you.map((w) => (
                  <li key={w} className="flex gap-2 t-body text-ink">
                    <span aria-hidden="true" className="mt-[9px] h-1.5 w-1.5 shrink-0 rounded-full bg-brand" />
                    {w}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="flex flex-col gap-3" aria-label="Версии условий">
            <BlockTitle>Условия: неизменяемые версии</BlockTitle>
            <ul className="m-0 flex list-none flex-col gap-3 p-0">
              {d.terms.map((t) => (
                <li key={t.ver} className="rounded-panel border border-hairline p-4">
                  <p className="flex flex-wrap items-center gap-2 t-label text-ink">
                    v{t.ver}
                    <Tag>{t.author === 'candidate' ? 'после встречного' : 'ваше предложение'}</Tag>
                    {t.verified && <Tag tone="brand">хеш сверен</Tag>}
                  </p>
                  <p className="mt-1 t-body text-ink">{t.text}</p>
                  <p className="t-small text-ink-3">
                    {[t.format_label, t.start_date && `выход ${t.start_date}`, t.contact_method_label && `связь: ${t.contact_method_label}`].filter(Boolean).join(' · ')}
                  </p>
                  <p className="mt-1 break-all font-mono text-[11px] text-ink-3">sha256 {t.sha256}</p>
                </li>
              ))}
            </ul>
          </section>

          <section className="flex flex-col gap-2" aria-label="Журнал событий">
            <BlockTitle>Журнал</BlockTitle>
            <ol className="m-0 flex list-none flex-col gap-1.5 p-0">
              {d.events.map((e) => (
                <li key={e.seq} className="flex gap-3 t-small">
                  <span className="w-5 shrink-0 font-mono text-ink-3">{e.seq}</span>
                  <span className="text-ink">{e.label}</span>
                  <span className="text-ink-3">· {ROLE_LABEL[e.actor_role] ?? e.actor_role}</span>
                </li>
              ))}
            </ol>
          </section>
        </div>
      ) : null}
    </Drawer>
  )
}
