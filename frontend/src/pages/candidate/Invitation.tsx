import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import { Chip, SectionTitle, TraceList } from '@/features/candidate/components/Bits'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { salaryHero } from '@/features/candidate/format'
import { useInvitationActions } from '@/features/candidate/invitation/actions'
import { MutualView } from '@/features/candidate/invitation/MutualView'
import { SalaryTrace } from '@/features/candidate/invitation/SalaryTrace'
import {
  AcceptSheet,
  CounterSheet,
  DeclineSheet,
  DisputeSheet,
  GrantSheet,
  ReportSheet,
  RevokeSheet,
} from '@/features/candidate/invitation/Sheets'
import { qk, usePrivacy, type InvitationDetail } from '@/features/candidate/queries'
import { cn } from '@/lib/cn'
import { Alert, Button, MobileHeader, Panel } from '@/ui'

type SheetName = 'accept' | 'counter' | 'decline' | 'report' | 'dispute' | 'revoke' | 'grant' | null

const FINAL_TEXT: Record<string, string> = {
  declined: 'Вы отказались от этого приглашения. Компания увидит только причину — без имени и контактов.',
  counter_declined: 'Компания не согласилась на встречное условие. Контакты остались скрытыми.',
  expired: 'Срок ответа истёк, приглашение закрыто. Это не штраф: компания сможет написать позже.',
  withdrawn: 'Компания отозвала приглашение.',
  closed: 'Работа по этому приглашению завершена.',
  consent_revoked: 'Вы отозвали согласие: контакты у компании снова скрыты.',
}

/**
 * К10 «Приглашение» (Figma 29:2) и «Взаимно» (29:48). Вилка — главное, под ней — сравнение с вашим минимумом;
 * ответы: «Да, интересно» (с отдельным согласием на передачу контактов), «Да, если…» (≤ 2 условий), «Нет» с причиной.
 */
export default function CandidateInvitation() {
  const { id = '' } = useParams()
  const q = useQuery({
    queryKey: qk.invitation(id),
    queryFn: () => unwrap(api.GET('/api/invitations/inbox/{invitation_id}', { params: { path: { invitation_id: id } } })),
    retry: false,
  })

  return (
    <QueryBoundary query={q} skeleton={<><MobileHeader title="Приглашение" backTo="/candidate/invitations" /><InvitationSkeleton /></>} errorTitle="Не удалось открыть приглашение">
      {(d) => <InvitationView d={d} id={id} />}
    </QueryBoundary>
  )
}

function InvitationSkeleton() {
  return (
    <div className="flex flex-col gap-4" role="status" aria-label="Загрузка">
      <div className="h-6 w-1/2 animate-pulse rounded bg-hairline" />
      <div className="h-16 w-3/4 animate-pulse rounded bg-hairline" />
      <div className="h-24 w-full animate-pulse rounded-panel bg-hairline" />
    </div>
  )
}

function InvitationView({ d, id }: { d: InvitationDetail; id: string }) {
  const actions = useInvitationActions(id)
  const [sheet, setSheet] = useState<SheetName>(null)
  const close = () => setSheet(null)
  const can = (a: string) => d.actions.includes(a)

  const mutual = d.status === 'accepted' && d.contacts_open

  const headerRight =
    d.status === 'sent' || d.status === 'viewed' ? (
      d.timers.respond_by_text ? <span className="t-label text-danger-text">Ответить {d.timers.respond_by_text}</span> : null
    ) : d.status === 'countered' && d.timers.counter_respond_by_text ? (
      <span className="t-label text-ink-2">Ответ компании {d.timers.counter_respond_by_text}</span>
    ) : null

  return (
    <>
      <MobileHeader title="Приглашение" backTo="/candidate/invitations" right={headerRight} />

      {mutual ? (
        <>
          <MutualView d={d} onDispute={() => setSheet('dispute')} onRevoke={() => setSheet('revoke')} />
          {can('check') && <OutcomeCheck d={d} actions={actions} />}
          <Footer d={d} />
        </>
      ) : (
        <div className="flex flex-col gap-7">
          <CompanyLine d={d} />
          <SalaryBlock d={d} />

          {d.caution && (
            <Alert tone="danger" title={d.caution}>
              {d.caution_reasons.length > 0 && (
                <ul className="mt-1 list-disc pl-4">
                  {d.caution_reasons.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              )}
            </Alert>
          )}

          <section>
            <h2 className="text-[20px] font-semibold leading-[26px] text-ink">{d.title}</h2>
            <p className="mt-2 whitespace-pre-line t-lead text-ink-2">{d.terms.description}</p>
            <TermsMeta d={d} />
          </section>

          {d.why_you.length > 0 && (
            <section aria-labelledby="why-h">
              <SectionTitle as="h3">
                <span id="why-h">Почему позвали вас</span>
              </SectionTitle>
              <TraceList items={d.why_you} />
            </section>
          )}

          {d.counter && (
            <Panel variant="tint" radius="lg" padding="md" className="flex flex-col gap-2">
              <p className="t-name text-ink">Вы предложили условие</p>
              <p className="t-body text-ink-2">{d.counter.summary}</p>
              {d.counter.comment && <p className="t-small text-ink-3">«{d.counter.comment}»</p>}
              <p className="t-small text-ink-3">
                {d.counter.auto_disclose
                  ? 'Если компания согласится, контакты откроются сразу.'
                  : 'Если компания согласится, мы отдельно спросим, открывать ли контакты.'}
              </p>
            </Panel>
          )}

          {d.status === 'countered' && !d.counter && <Alert>Компания рассматривает ваше встречное условие.</Alert>}

          {d.status === 'accepted' && !d.contacts_open && (
            <Panel variant="tint" radius="lg" padding="md" className="flex flex-col gap-3">
              <p className="t-name text-ink">{d.company?.name ?? 'Компания'} приняла ваши условия</p>
              <p className="t-body text-ink-2">Контакты откроются после вашего согласия на передачу данных.</p>
              {can('grant-consent') && (
                <Button block onClick={() => setSheet('grant')}>
                  Открыть контакты
                </Button>
              )}
            </Panel>
          )}

          {FINAL_TEXT[d.status] && d.status !== 'accepted' && (
            <Panel variant="panel" radius="lg" padding="md" className="flex flex-col gap-2">
              <p className="t-name text-ink">{d.status_label}</p>
              <p className="t-body text-ink-2">{d.outcome_label ? `${FINAL_TEXT[d.status]} Итог: ${d.outcome_label.toLowerCase()}.` : FINAL_TEXT[d.status]}</p>
            </Panel>
          )}

          {d.feedback.length > 0 && <Feedback d={d} />}

          <Reliability d={d} />

          {(can('accept') || can('counter') || can('decline')) && (
            <div className="flex flex-col gap-3">
              {can('accept') && (
                <Button block size="lg" onClick={() => setSheet('accept')}>
                  Да, интересно
                </Button>
              )}
              {can('counter') && d.counter_limits && (
                <Button block size="lg" variant="outline" onClick={() => setSheet('counter')}>
                  Да, если…
                </Button>
              )}
              {can('decline') && (
                <Button variant="text" className="self-center text-ink-2" onClick={() => setSheet('decline')}>
                  Нет
                </Button>
              )}
              <p className="text-center t-small text-ink-3">Имя и контакты компания увидит только после вашего «да».</p>
            </div>
          )}

          <Footer d={d} onReport={d.reported ? undefined : () => setSheet('report')} />
        </div>
      )}

      <AcceptSheet open={sheet === 'accept'} onClose={close} detail={d} actions={actions} />
      <GrantSheet open={sheet === 'grant'} onClose={close} detail={d} actions={actions} />
      <CounterSheet open={sheet === 'counter'} onClose={close} detail={d} actions={actions} onDecline={() => setSheet('decline')} />
      <DeclineSheet open={sheet === 'decline'} onClose={close} detail={d} actions={actions} onCounter={() => setSheet('counter')} />
      <ReportSheet open={sheet === 'report'} onClose={close} detail={d} actions={actions} />
      <DisputeSheet open={sheet === 'dispute'} onClose={close} actions={actions} />
      <RevokeSheet open={sheet === 'revoke'} onClose={close} detail={d} actions={actions} />
    </>
  )
}

// --- блоки ----------------------------------------------------------------------------------------------------------------

function CompanyLine({ d }: { d: InvitationDetail }) {
  const c = d.company
  if (!c) return null
  return (
    <div>
      <h2 className="t-name text-ink">{c.name}</h2>
      <p className="mt-0.5 t-small text-ink-2">
        {[c.field, c.reliability.display].filter(Boolean).join('. ')}
      </p>
      {(c.personal_email_label || c.reliability.discrepancy_badge) && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {c.personal_email_label && <Chip tone="panel">{c.personal_email_label}</Chip>}
          {c.reliability.discrepancy_badge && <Chip tone="danger">{c.reliability.discrepancy_badge}</Chip>}
        </div>
      )}
    </div>
  )
}

function SalaryBlock({ d }: { d: InvitationDetail }) {
  const privacy = usePrivacy()
  const { range, unit } = salaryHero(d.salary.salary_from, d.salary.salary_to)
  const sameBase = privacy.data?.salary_type === d.salary.salary_type
  const min = privacy.data?.salary_min && sameBase ? privacy.data.salary_min : null
  return (
    <section aria-label="Вилка">
      <p className="font-mono text-[48px] font-medium leading-[56px] tracking-[-1.5px] text-brand max-[380px]:text-[40px]">{range}</p>
      <p className="mt-1 t-lead text-ink">
        {unit} {d.salary.salary_type_label} в месяц
      </p>
      <div className="mt-4">
        <SalaryTrace from={d.salary.salary_from} to={d.salary.salary_to} min={min} />
      </div>
      {d.salary_vs_min ? (
        <p className="mt-1 t-small text-ink-2">{d.salary_vs_min}</p>
      ) : (
        <p className="mt-1 t-small text-ink-3">
          Минимум зарплаты не указан — сравнить вилку не с чем.{' '}
          <Link to="/candidate/privacy" className="text-brand underline-offset-4 hover:underline">
            Указать
          </Link>
        </p>
      )}
    </section>
  )
}

function TermsMeta({ d }: { d: InvitationDetail }) {
  const t = d.terms
  const rows: [string, string][] = []
  if (t.format_label) rows.push(['Формат', t.format_label])
  if (t.start_date) rows.push(['Выход', new Date(t.start_date).toLocaleDateString('ru-RU')])
  rows.push(['Способ связи', t.contact_method_label ?? t.contact_method])
  return (
    <div className="mt-4 flex flex-col gap-3">
      <dl className="flex flex-wrap gap-x-8 gap-y-2">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt className="t-small text-ink-3">{k}</dt>
            <dd className="t-label text-ink">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="t-small text-ink-3">
        Условия зафиксированы{d.terms_history.length > 0 ? ` (версия ${t.ver})` : ''}: {t.verified ? 'подпись проверена' : 'подпись не сходится'} ·
        <span className="ml-1 font-mono">{t.sha256.slice(0, 10)}…</span>
      </p>
    </div>
  )
}

function Reliability({ d }: { d: InvitationDetail }) {
  const r = d.company?.reliability
  if (!r) return null
  return (
    <section aria-labelledby="rel-h">
      <SectionTitle as="h3">
        <span id="rel-h">Надёжность компании</span>
      </SectionTitle>
      <p className="t-body text-ink-2">{r.display}</p>
      {r.breakdown.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1 t-small text-ink-3">
          {r.breakdown.map((b) => (
            <li key={b}>· {b}</li>
          ))}
        </ul>
      )}
      {d.company?.description && <p className="mt-3 t-small text-ink-3">{d.company.description}</p>}
    </section>
  )
}

function Feedback({ d }: { d: InvitationDetail }) {
  return (
    <section aria-labelledby="fb-h" className="flex flex-col gap-3">
      <SectionTitle as="h3">
        <span id="fb-h">Что сказала компания</span>
      </SectionTitle>
      {d.feedback.map((f, i) => (
        <Panel key={i} variant="panel" radius="lg" padding="sm" className="flex flex-col gap-2">
          <p className="t-body-strong text-ink">{f.label}</p>
          {f.comment && <p className="t-small text-ink-2">«{f.comment}»</p>}
          {f.links.map((l) => (
            <p key={l.skill} className="t-small text-ink-2">
              {l.text}{' '}
              {l.kind === 'task' && (
                <Link to="/candidate/tasks" className="text-brand underline-offset-4 hover:underline">
                  К задачам
                </Link>
              )}
            </p>
          ))}
        </Panel>
      ))}
    </section>
  )
}

function OutcomeCheck({ d, actions }: { d: InvitationDetail; actions: ReturnType<typeof useInvitationActions> }) {
  const [pick, setPick] = useState<string | null>(null)
  const [early, setEarly] = useState(false)
  const opts = Object.entries(d.outcome_check_options ?? {})
  if (opts.length === 0) return null
  // сервер спрашивает через 10 дней после «да»; раньше срока пускаем только по явной просьбе (он сам ответит, можно ли)
  const dueAt = d.timers.outcome_check_at ? new Date(d.timers.outcome_check_at).getTime() : null
  const due = dueAt != null && Date.now() >= dueAt
  if (!due && !early) {
    return (
      <p className="mt-6 text-center t-small text-ink-3">
        Через 10 дней после «да» спросим, совпали ли условия.{' '}
        <button type="button" onClick={() => setEarly(true)} className="text-brand underline-offset-4 hover:underline">
          Ответить сейчас
        </button>
      </p>
    )
  }
  return (
    <Panel variant="tint" radius="lg" padding="md" className="mt-6 flex flex-col gap-3">
      <p className="t-name text-ink">Условия совпали?</p>
      <p className="t-small text-ink-2">Один вопрос: ваш ответ уходит в надёжность компании.</p>
      <div role="radiogroup" aria-label="Совпали ли условия" className="flex flex-wrap gap-2">
        {opts.map(([code, label]) => (
          <button
            key={code}
            type="button"
            role="radio"
            aria-checked={pick === code}
            onClick={() => setPick(code)}
            className={cn(
              'rounded-pill border px-4 py-2 t-label',
              pick === code ? 'border-brand bg-brand text-white' : 'border-steel bg-white text-ink hover:border-brand',
            )}
          >
            {label}
          </button>
        ))}
      </div>
      {actions.check.error ? <Alert tone="danger">{errorMessage(actions.check.error)}</Alert> : null}
      <Button disabled={!pick} loading={actions.check.isPending} onClick={() => pick && actions.check.mutate({ answer: pick })}>
        Отправить ответ
      </Button>
    </Panel>
  )
}

function Footer({ d, onReport }: { d: InvitationDetail; onReport?: () => void }) {
  return (
    <div className="mt-8 flex flex-col items-center gap-2 border-t border-hairline pt-5">
      {onReport ? (
        <button type="button" onClick={onReport} className="t-small text-ink-3 underline-offset-4 hover:text-ink hover:underline">
          Пожаловаться на приглашение
        </button>
      ) : d.reported ? (
        <p className="t-small text-ink-3">Жалоба отправлена — спасибо.</p>
      ) : null}
      <Link to="/candidate/invitations" className="t-small text-ink-3 underline-offset-4 hover:underline">
        Все приглашения
      </Link>
    </div>
  )
}
