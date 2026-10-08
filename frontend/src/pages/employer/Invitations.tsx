import { useState } from 'react'
import { errorMessage } from '@/api/errors'
import {
  useInvitation,
  useInvitationAction,
  useKanban,
  useNeed,
  useNeeds,
  type InvitationAction,
  type InvitationCard,
} from '@/features/employer/api'
import { BlockTitle, LoadError, PageHead, PageSkeleton } from '@/features/employer/components/common'
import { CounterOfferCard } from '@/features/employer/components/invitations/CounterOfferCard'
import { InvitationDetailDrawer } from '@/features/employer/components/invitations/InvitationDetailDrawer'
import { Pipeline } from '@/features/employer/components/invitations/Pipeline'
import { ReasonDrawer, type ReasonResult } from '@/features/employer/components/ReasonDrawer'
import { COUNTER_DECLINE_REASONS, NOT_MATCHED_REASONS, WITHDRAW_REASONS } from '@/features/employer/dict'
import { formatDecimal, formatInt, plural } from '@/lib/format'
import { Button, Counters, EmptyState, Panel, useToast } from '@/ui'
import { Select } from '@/ui/Select'

type Dialog = { kind: 'withdraw' | 'decline' | 'outcome'; card: InvitationCard }

/** Р8 «Приглашения»: цифры воронки, встречные условия наверху, конвейер по статусам, причины отказов. */
export default function Invitations() {
  const toast = useToast()
  const needs = useNeeds()
  const [needId, setNeedId] = useState<string>('')
  const kanban = useKanban(needId || null)
  const act = useInvitationAction()
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [dialogError, setDialogError] = useState<string | null>(null)

  const k = kanban.data
  const countered = k?.columns.find((c) => c.status === 'countered')?.items ?? []
  const funnel = k?.funnel ?? {}
  const total = funnel.sent ?? 0

  async function run(card: InvitationCard, action: InvitationAction, success: string) {
    setBusyId(card.id)
    setDialogError(null)
    try {
      await act.mutateAsync({ id: card.id, action })
      toast.success(success)
      setDialog(null)
    } catch (e) {
      if (dialog) setDialogError(errorMessage(e))
      else toast.error(errorMessage(e), 'Действие не выполнено')
    } finally {
      setBusyId(null)
    }
  }

  function submitDialog(d: Dialog, r: ReasonResult) {
    if (d.kind === 'withdraw') return run(d.card, { kind: 'withdraw', reason: r.reason, comment: r.comment }, 'Приглашение отозвано')
    if (d.kind === 'decline')
      return run(d.card, { kind: 'decline_counter', reason: r.reason, comment: r.comment }, 'Отказали. Причина ушла кандидату.')
    if (r.reason === 'hired') return run(d.card, { kind: 'hired' }, 'Отметили: найм')
    return run(d.card, { kind: 'not_matched', reason: r.reason, comment: r.comment, skillIds: r.skillIds }, 'Отметили: не сложилось. Причина ушла кандидату.')
  }

  return (
    <div className="flex flex-col gap-10">
      <PageHead
        title="Приглашения"
        lead={
          total > 0
            ? `Вы пригласили ${formatInt(total)} ${plural(total, ['человека', 'человек', 'человек'])}.${countered.length > 0 ? ` ${countered.length === 1 ? 'Один ответил' : `${countered.length} ответили`} «да, если…» и ждут вашего решения.` : ''}`
            : 'Здесь появятся приглашения, их статусы и ответы кандидатов.'
        }
        actions={
          (needs.data?.length ?? 0) > 1 ? (
            <Select aria-label="Потребность" value={needId} onChange={(e) => setNeedId(e.target.value)} wrapperClassName="min-w-[260px]">
              <option value="">Все потребности</option>
              {needs.data?.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.title || n.category}
                </option>
              ))}
            </Select>
          ) : undefined
        }
      />

      {kanban.isPending ? (
        <PageSkeleton rows={4} />
      ) : kanban.isError ? (
        <LoadError error={kanban.error} onRetry={() => void kanban.refetch()} />
      ) : k ? (
        total === 0 && k.columns.every((c) => c.count === 0) ? (
          <EmptyState
            title="Приглашений пока нет — выберите кандидатов в подборке"
            description="Откройте подборку, закрепите подходящих кандидатов в шортлист и пригласите их с вилкой. Контакты откроются, только когда кандидат ответит «да»."
            action={<Button to="/employer/needs">К потребностям</Button>}
          />
        ) : (
          <>
            <Counters
              items={[
                { value: formatInt(funnel.sent ?? 0), label: 'отправлено' },
                { value: formatInt(funnel.viewed ?? 0), label: 'просмотрено' },
                { value: formatInt(funnel.accepted ?? 0), label: 'приняли' },
                { value: formatInt(countered.length), label: 'ждут вашего ответа', tone: countered.length > 0 ? 'brand' : 'default' },
                k.avg_rank_at_send != null
                  ? { value: formatDecimal(k.avg_rank_at_send, 1), label: 'средняя позиция приглашённых в выдаче' }
                  : { value: formatInt(funnel.contacted ?? 0), label: 'вы уже связались' },
              ]}
            />

            {countered.length > 0 && (
              <section aria-label="Встречные условия" className="flex flex-col gap-4">
                {countered.map((c) => (
                  <CounterOffer
                    key={c.id}
                    card={c}
                    busy={busyId === c.id}
                    onAccept={() => void run(c, { kind: 'accept_counter' }, 'Согласились. Условия зафиксированы новой версией.')}
                    onDecline={() => {
                      setDialogError(null)
                      setDialog({ kind: 'decline', card: c })
                    }}
                  />
                ))}
              </section>
            )}

            <Pipeline
              kanban={k}
              handlers={{
                onOpen: (c) => setOpenId(c.id),
                onWithdraw: (c) => {
                  setDialogError(null)
                  setDialog({ kind: 'withdraw', card: c })
                },
                onOutcome: (c) => {
                  setDialogError(null)
                  setDialog({ kind: 'outcome', card: c })
                },
                onQuick: (c, kind) => void run(c, { kind }, kind === 'contacted' ? 'Отметили: связались' : 'Этап: собеседование'),
                busyId,
              }}
            />

            {kanban.hasNextPage && (
              <div className="flex justify-center">
                <Button variant="outline" loading={kanban.isFetchingNextPage} onClick={() => void kanban.fetchNextPage()}>
                  Показать ещё
                </Button>
              </div>
            )}

            <DeclineSummary kanban={k} />
          </>
        )
      ) : null}

      {dialog && dialog.kind === 'withdraw' && (
        <ReasonDrawer
          key={dialog.card.id + 'w'}
          open
          onClose={() => setDialog(null)}
          eyebrow={dialog.card.pseudonym}
          title="Отозвать приглашение"
          description="Пока кандидат не ответил, приглашение можно отозвать. Он получит причину; повторно пригласить его получится не сразу."
          options={WITHDRAW_REASONS}
          submitLabel="Отозвать приглашение"
          busy={busyId === dialog.card.id}
          error={dialogError}
          onSubmit={(r) => void submitDialog(dialog, r)}
        />
      )}
      {dialog && dialog.kind === 'decline' && (
        <ReasonDrawer
          key={dialog.card.id + 'd'}
          open
          onClose={() => setDialog(null)}
          eyebrow={dialog.card.pseudonym}
          title="Отказать во встречном условии"
          description="Кандидат увидит причину. Если дело в зарплате, пригласить его раньше срока получится с вилкой, где «от» выше прежнего «до» минимум на 15%."
          options={COUNTER_DECLINE_REASONS}
          submitLabel="Отказать"
          busy={busyId === dialog.card.id}
          error={dialogError}
          onSubmit={(r) => void submitDialog(dialog, r)}
        />
      )}
      {dialog && dialog.kind === 'outcome' && (
        <OutcomeDialog
          card={dialog.card}
          busy={busyId === dialog.card.id}
          error={dialogError}
          onClose={() => setDialog(null)}
          onSubmit={(r) => void submitDialog(dialog, r)}
        />
      )}
      {openId && <InvitationDetailDrawer id={openId} onClose={() => setOpenId(null)} />}
    </div>
  )
}

function CounterOffer({ card, busy, onAccept, onDecline }: { card: InvitationCard; busy: boolean; onAccept: () => void; onDecline: () => void }) {
  const detail = useInvitation(card.id)
  return <CounterOfferCard card={card} detail={detail.data} busy={busy} onAccept={onAccept} onDecline={onDecline} />
}

/** Итог по приглашению: найм или «не сложилось» с причиной и навыками брифа, которых не хватило. */
function OutcomeDialog({
  card,
  busy,
  error,
  onClose,
  onSubmit,
}: {
  card: InvitationCard
  busy: boolean
  error: string | null
  onClose: () => void
  onSubmit: (r: ReasonResult) => void
}) {
  const need = useNeed(card.need_id ?? undefined)
  const skills = (need.data?.parse.skills ?? []).filter((s) => s.kind === 'must').map((s) => ({ id: s.skill_id, name: s.name }))
  return (
    <ReasonDrawer
      open
      onClose={onClose}
      eyebrow={card.pseudonym}
      title="Итог"
      description="Если не сложилось, причина уйдёт кандидату со ссылкой на короткую задачу по модулю теста или на интервью."
      options={[{ code: 'hired', label: 'Нанят' }, ...NOT_MATCHED_REASONS]}
      submitLabel="Сохранить итог"
      skills={skills}
      busy={busy}
      error={error}
      onSubmit={onSubmit}
    />
  )
}

/** Сводка причин отказов с подсказкой по вилке: что чаще всего ломает «да» (концепция §3.2, шаг 9). */
function DeclineSummary({ kanban }: { kanban: NonNullable<ReturnType<typeof useKanban>['data']> }) {
  if (kanban.decline_summary.length === 0) return null
  const max = Math.max(...kanban.decline_summary.map((d) => d.count), 1)
  return (
    <Panel variant="panel" radius="xl" padding="lg" className="flex flex-col gap-4">
      <BlockTitle>Почему отказываются</BlockTitle>
      <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
        {kanban.decline_summary.map((d) => (
          <li key={d.reason} className="grid grid-cols-[minmax(0,200px)_minmax(0,1fr)_32px] items-center gap-x-4">
            <span className="t-body text-ink">{d.label}</span>
            <span className="relative block h-4" aria-hidden="true">
              <span className="absolute inset-x-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-bar" />
              <span className="absolute left-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-brand" style={{ width: `${(d.count / max) * 100}%` }} />
            </span>
            <span className="t-num-m text-right text-ink">{d.count}</span>
          </li>
        ))}
      </ul>
      {kanban.decline_hint && <p className="t-body text-ink-2">{kanban.decline_hint}</p>}
    </Panel>
  )
}
