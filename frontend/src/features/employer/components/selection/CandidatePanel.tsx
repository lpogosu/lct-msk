import { cn } from '@/lib/cn'
import { formatDecimal } from '@/lib/format'
import { Button, Illustration, NodeLegend, ScoreRing, Skeleton, TraceDiagram, type TraceDiagramRow, type TraceEnd } from '@/ui'
import { useBankCard, useCandidateCard, type BankItem, type CandidateCard, type SelectionItem } from '../../api'
import { BANK_PARTS, SCORE_PARTS } from '../../dict'
import { BlockTitle, InterestTag, Label, LoadError, Tag } from '../common'

type Chip = SelectionItem['chips'][number]

function chipRow(c: Chip): TraceDiagramRow {
  let end: TraceEnd
  if (c.state === 'no_data') end = 'none'
  else if (c.state === 'declared') end = 'claimed'
  else end = c.source === 'fsp' ? 'fsp' : 'test'

  const unconfirmedMust = c.kind === 'must' && c.state !== 'confirmed'
  let evidence = c.label
  if (unconfirmedMust && c.state === 'declared') evidence = 'не подтверждена'
  if (unconfirmedMust && c.state === 'no_data') evidence = 'нет данных'
  return {
    key: c.skill_id,
    label: (
      <span className={cn(c.kind === 'nice' && 'text-ink-2')}>
        {c.name}
        {c.kind === 'nice' && <span className="ml-1 t-small text-ink-3">желательно</span>}
      </span>
    ),
    end,
    tone: unconfirmedMust ? (c.state === 'no_data' ? 'muted' : 'danger') : c.state === 'confirmed' ? 'default' : 'muted',
    evidence,
  }
}

const DISCLOSED_FIELDS: [keyof NonNullable<CandidateCard['disclosed']>, string][] = [
  ['full_name', 'Имя'],
  ['email', 'Email'],
  ['phone', 'Телефон'],
  ['telegram', 'Telegram'],
  ['city', 'Город'],
  ['current_employer', 'Работает'],
  ['university', 'Вуз'],
]

/** Раскрытый профиль (CandidateDisclosed): только после взаимного «да» и только те поля, что разрешил кандидат. */
function DisclosedBlock({ d }: { d: NonNullable<CandidateCard['disclosed']> }) {
  return (
    <section aria-label="Контакты кандидата" className="flex flex-col gap-3 rounded-panel bg-tint p-4">
      <BlockTitle>Контакты открыты</BlockTitle>
      <dl className="m-0 grid grid-cols-[96px_minmax(0,1fr)] gap-x-3 gap-y-1 t-body">
        {DISCLOSED_FIELDS.map(([k, label]) =>
          d[k] ? (
            <div key={k} className="contents">
              <dt className="t-small text-ink-3">{label}</dt>
              <dd className="m-0 text-ink [overflow-wrap:anywhere]">{String(d[k])}</dd>
            </div>
          ) : null,
        )}
      </dl>
      {d.resume && (
        <div>
          <Label>Самоописание — заявлено кандидатом, на Score не влияет</Label>
          <p className="t-small text-ink-2 [overflow-wrap:anywhere]">{d.resume}</p>
        </div>
      )}
      {d.skills_passport?.verified && (
        <p className="m-0 t-body text-ink" data-testid="skills-passport">
          <span aria-hidden="true" className="text-brand">✓ </span>
          {d.skills_passport.label}
          <span className="block t-small text-ink-3">
            {d.skills_passport.titles.join(' · ')} · {d.skills_passport.issuer}
          </span>
        </p>
      )}
      {d.fsp_extract && (
        <pre className="m-0 whitespace-pre-wrap rounded-panel bg-code-bg p-3 font-mono text-[12px] leading-5 text-white [overflow-wrap:anywhere]">
          {d.fsp_extract}
        </pre>
      )}
    </section>
  )
}

/** Декомпозиция Score: Σ вкладов = Score. Длина — доля от максимума компонента (вес × 100). */
function ScoreBars({
  parts,
  weights,
  defs = SCORE_PARTS,
}: {
  parts: Record<string, number>
  weights: Record<string, number>
  defs?: readonly { key: string; label: string; hint: string }[]
}) {
  return (
    <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
      {defs.map((p) => {
        const value = parts[p.key] ?? 0
        const max = (weights[p.key] ?? 0.2) * 100
        const frac = Math.max(0, Math.min(1, max > 0 ? value / max : 0))
        return (
          <li key={p.key} className="grid grid-cols-[minmax(0,130px)_minmax(0,1fr)_44px] items-center gap-x-3 sm:grid-cols-[minmax(0,150px)_minmax(0,1fr)_52px]">
            <span className="t-body text-ink" title={p.hint}>
              {p.label}
            </span>
            <span className="relative block h-4" role="img" aria-label={`${p.label}: ${formatDecimal(value, 1)} из ${formatDecimal(max, 0)}`}>
              <span className="absolute inset-x-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-bar" />
              <span className="absolute left-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-brand" style={{ width: `${frac * 100}%` }} />
              {frac > 0 && (
                <span className="absolute top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand" style={{ left: `${frac * 100}%` }} />
              )}
            </span>
            <span className="t-num-m text-right text-ink">{formatDecimal(value, 1)}</span>
          </li>
        )
      })}
    </ul>
  )
}

type Factors = SelectionItem['factors']

/**
 * Множители Score: вклады выше уже учитывают их (Score = Σ вкладов = 100·G·Cov·Cond·Σ w·x).
 * Показываем только те, что меньше 1, чтобы порядок подборки не менялся «молча».
 */
function FactorsNote({ factors }: { factors: Factors }) {
  const rows: { key: string; label: string; value: number; sub?: string }[] = [
    { key: 'g', label: 'Смежная категория (G)', value: factors.g },
    { key: 'cov', label: 'Покрытие обязательных (Cov)', value: factors.cov },
    {
      key: 'cond',
      label: 'Условия (Cond)',
      value: factors.cond,
      sub:
        [factors.cond_salary < 1 && `зарплата ×${formatDecimal(factors.cond_salary, 2)}`, factors.cond_format < 1 && `формат или город ×${formatDecimal(factors.cond_format, 2)}`]
          .filter(Boolean)
          .join(', ') || undefined,
    },
  ].filter((r) => r.value < 1 - 1e-6)
  if (rows.length === 0) return null
  return (
    <section aria-label="Множители Score" className="flex flex-col gap-1.5">
      <BlockTitle>Множители, уже учтённые в вкладах</BlockTitle>
      <ul className="m-0 flex list-none flex-col gap-1 p-0">
        {rows.map((r) => (
          <li key={r.key} className="flex flex-wrap items-baseline justify-between gap-x-3 t-body text-ink">
            <span>
              {r.label}
              {r.sub && <span className="ml-1 t-small text-ink-3">({r.sub})</span>}
            </span>
            <span className="t-num-m text-danger-text">×{formatDecimal(r.value, 2)}</span>
          </li>
        ))}
      </ul>
      <p className="t-small text-ink-3">Score = 100 · G · Cov · Cond · Σ вес × компонент; сумма вкладов выше равна Score.</p>
    </section>
  )
}

/**
 * Правая панель кандидата (Р6, Figma 28:5): слепая карточка с кольцом Score, разложением, «что вы просили и чем это
 * подтверждено», обобщённым ФСП, свежестью и заданиями. До «да» здесь нет ни ФИО, ни контактов — это CandidateBlind.
 */
export function CandidatePanel({
  needId,
  refId,
  rowItem,
  bankItem,
  weights,
  pinned,
  pinBusy,
  invited,
  onTogglePin,
  onInvite,
  onClose,
}: {
  /** Бриф, в рамках которого открыта карточка; null — банк без брифа */
  needId: string | null
  refId: string
  rowItem: SelectionItem | null
  /** Строка банка (без брифа): Score банка 0,55·T + 0,25·F + 0,20·A */
  bankItem?: BankItem | null
  weights: Record<string, number>
  pinned: boolean
  pinBusy: boolean
  /** Активное приглашение уже есть — повторно приглашать нельзя */
  invited: string | null
  onTogglePin: () => void
  onInvite: () => void
  onClose: () => void
}) {
  const needQ = useCandidateCard(needId ?? undefined, needId ? refId : null)
  const bankQ = useBankCard(refId, !needId)
  const q = needId ? needQ : bankQ
  const card: CandidateCard | undefined = q.data
  const item: SelectionItem | null = card?.selection ?? rowItem
  const hasRow = !!item || !!bankItem

  if (q.isPending && !hasRow) {
    return (
      <div className="flex flex-col gap-4" role="status" aria-label="Открываем карточку">
        <Skeleton className="h-10 w-1/2" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }
  if (q.isError) return <LoadError error={q.error} onRetry={() => void q.refetch()} title="Не удалось открыть карточку" />
  if (card && !card.available) {
    return (
      <div className="flex flex-col gap-3">
        <h2 className="t-title text-ink">{card.pseudonym}</h2>
        <p className="t-body text-ink-2">{card.note ?? 'Кандидат сейчас не виден в подборе: он изменил настройки или ушёл в «не ищу».'}</p>
        <Button variant="outline" size="sm" onClick={onClose}>
          Закрыть
        </Button>
      </div>
    )
  }

  const pseudonym = card?.pseudonym ?? item?.pseudonym ?? bankItem?.pseudonym ?? ''
  const categoryName = card?.category?.name ?? item?.category ?? bankItem?.category ?? ''
  const unconfirmed = (item?.confirmed ?? bankItem?.confirmed ?? card?.category?.confirmed) === false
  const otherConfirmed = (card?.category?.all ?? []).filter((n) => n !== categoryName)
  const otherUnconfirmed = (card?.category?.unconfirmed ?? []).filter((n) => n !== categoryName)
  const rows = [...(item?.chips ?? [])].sort((a, b) => (a.kind === b.kind ? 0 : a.kind === 'must' ? -1 : 1)).map(chipRow)
  const hasFsp = item?.has_fsp ?? bankItem?.has_fsp ?? card?.has_fsp ?? false
  const fspLine = item?.fsp_line ?? bankItem?.fsp_line ?? card?.fsp_line ?? null
  // Роль в команде — из заявки участника, в протоколе ФСП её нет: показываем отдельно серым, в Score не входит
  const fspDeclaredRole = item?.fsp_declared_role ?? bankItem?.fsp_declared_role ?? card?.fsp_declared_role ?? null
  const fspBadges = item?.fsp_badges ?? bankItem?.fsp_badges ?? card?.fsp_badges ?? []
  const fresh = item?.freshness ?? bankItem?.freshness ?? card?.freshness
  const modules = card?.skills ?? []
  const tasks = card?.tasks ?? []
  const history = card?.grade_history ?? []
  const declared = card?.declared_skills ?? []
  const workHistory = card?.work_history ?? []
  const salarySignal = item?.salary?.signal ?? card?.salary?.signal ?? (card as { salary_min?: { signal?: string } } | undefined)?.salary_min?.signal ?? null

  return (
    <div className={cn('flex flex-col gap-7', q.isFetching && 'opacity-80')}>
      <header className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="t-title text-ink">{pseudonym}</h2>
          <p className="mt-1 t-small text-ink-2">
            {categoryName}
            {card?.test_date ? `, тест пройден ${card.test_date}` : ''}
          </p>
          {card?.minor_notice && (
            <p className="mt-2 inline-block rounded-panel bg-tint px-3 py-1.5 t-small text-brand">{card.minor_notice}</p>
          )}
          {unconfirmed && (
            <p className="t-small text-ink-3">
              Грейд не подтверждён: категория заявлена, тестом пока не подтверждена. В подборке такие кандидаты стоят ниже подтверждённых.
            </p>
          )}
          {otherConfirmed.length > 0 && <p className="t-small text-ink-2">Также подтверждено тестом: {otherConfirmed.join(', ')}</p>}
          {otherUnconfirmed.length > 0 && (
            <p className="t-small text-ink-3">Заявлено, тестом не подтверждено: {otherUnconfirmed.join(', ')}</p>
          )}
          <button
            type="button"
            onClick={onClose}
            className="mt-2 t-small text-ink-3 underline-offset-4 hover:text-ink hover:underline xl:hidden"
          >
            Закрыть карточку
          </button>
        </div>
        {(item ?? bankItem) && (
          <ScoreRing
            value={Math.round((item ?? bankItem)!.score)}
            size={80}
            label={`${item ? 'Score' : 'Балл банка'} ${formatDecimal((item ?? bankItem)!.score, 1)} из 100`}
          >
            {Math.round((item ?? bankItem)!.score)}
          </ScoreRing>
        )}
      </header>

      {card?.view === 'disclosed' && card.disclosed && <DisclosedBlock d={card.disclosed} />}

      {item ? (
        <>
          <ScoreBars parts={item.parts} weights={weights} />
          {item.factors && <FactorsNote factors={item.factors} />}
        </>
      ) : bankItem ? (
        <ScoreBars parts={bankItem.parts} weights={weights} defs={BANK_PARTS} />
      ) : null}

      {card?.interest && (
        <section aria-label="Сигнал интереса" className="flex flex-col gap-1.5">
          <BlockTitle>Сигнал интереса</BlockTitle>
          <p className="m-0 flex flex-wrap items-center gap-2 t-body text-ink">
            <InterestTag interest={card.interest} />
            <span>{card.interest.text}</span>
          </p>
          <p className="m-0 t-small text-ink-3">Это отдельный сигнал: в Score он не входит и вклады выше не меняет.</p>
        </section>
      )}

      {modules.length > 0 && (
        <section aria-label="Модули теста" className="flex flex-col gap-1.5">
          <BlockTitle>Модули теста</BlockTitle>
          <ul className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 t-body text-ink">
            {modules.map((m) => (
              <li key={m.module}>
                {m.name} <span className="text-brand">{m.symbol}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {item && (item.pros.length > 0 || item.con) && (
        <section aria-labelledby="why-title" className="flex flex-col gap-2">
          <BlockTitle>
            <span id="why-title">Почему в подборке</span>
          </BlockTitle>
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {item.pros.map((r) => (
              <li key={r.code + r.text} className="flex gap-2 t-body text-ink">
                <span aria-hidden="true" className="mt-[9px] h-1.5 w-1.5 shrink-0 rounded-full bg-brand" />
                <span>{r.text}</span>
              </li>
            ))}
            {item.con && (
              <li className="flex gap-2 t-body text-danger-text">
                <span aria-hidden="true" className="mt-[9px] h-1.5 w-1.5 shrink-0 rounded-full bg-danger" />
                <span>
                  <span className="font-medium">Против: </span>
                  {item.con.text}
                </span>
              </li>
            )}
          </ul>
          {item.neighbor_below && <p className="t-small text-ink-3">Почему выше соседа: {item.neighbor_below}</p>}
        </section>
      )}

      {rows.length > 0 && (
        <section aria-labelledby="proof-title" className="flex flex-col gap-3">
          <BlockTitle>
            <span id="proof-title">Что вы просили и чем это подтверждено</span>
          </BlockTitle>
          <TraceDiagram rows={rows} labelWidth={128} aria-label="Требования брифа и их подтверждения" />
          <NodeLegend />
          {declared.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <Label>Заявлено кандидатом — на Score не влияет</Label>
              <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
                {declared.slice(0, 10).map((d) => (
                  <li key={d.skill_id} className="rounded-pill border border-dashed border-steel px-2.5 py-0.5 t-small text-ink-3">
                    {d.name}
                  </li>
                ))}
                {declared.length > 10 && <li className="py-0.5 t-small text-ink-3">и ещё {declared.length - 10}</li>}
              </ul>
            </div>
          )}
        </section>
      )}

      {workHistory.length > 0 && (
        <section aria-label="Опыт работы" className="flex flex-col gap-1.5">
          <BlockTitle>Опыт работы</BlockTitle>
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {workHistory.map((w, i) => (
              <li key={i} className="t-body text-ink">
                {w.role} <span className="text-ink-3">· {w.duration}{w.current ? ', сейчас' : ''}</span>
              </li>
            ))}
          </ul>
          <p className="m-0 t-small text-ink-3">Заявлено кандидатом, без названий компаний. На Score не влияет: названия откроются после взаимного «да».</p>
        </section>
      )}

      <section aria-label="Достижения ФСП" className="flex items-start gap-4">
        {hasFsp ? (
          <>
            <Illustration name="medal_fsp" className="w-14 shrink-0" />
            <div className="min-w-0">
              <p className="t-body-strong text-ink">{fspLine}</p>
              {fspDeclaredRole && (
                <p className="t-small text-ink-3" title="В протоколах ФСП роли в команде нет: на оценку не влияет">
                  {fspDeclaredRole}
                </p>
              )}
              {fspBadges.length > 0 && (
                <p className="mt-1 flex flex-wrap gap-1.5">
                  {fspBadges.map((b) => (
                    <Tag key={b} tone="brand">
                      {b}
                    </Tag>
                  ))}
                </p>
              )}
              <p className="mt-1 t-small text-ink-2">Протокол откроется после согласия кандидата.</p>
            </div>
          </>
        ) : (
          <p className="t-body text-ink-2">
            {fspLine ?? 'Достижения ФСП не привязаны — оценка по тесту и задачам.'}
            <span className="block t-small text-ink-3">Это не минус: на ранг влияет только то, что подтверждено.</span>
          </p>
        )}
      </section>

      {(tasks.length > 0 || fresh || salarySignal || history.length > 0) && (
        <section aria-label="Активность и условия" className="flex flex-col gap-3">
          {tasks.map((t) => (
            <div key={t.ref}>
              <p className="t-body-strong text-ink">Задача «{t.title}»</p>
              <p className="t-small text-ink-2">
                Решена {t.when}, результат {t.result}.
              </p>
            </div>
          ))}
          {fresh && (
            <p className="t-small text-ink-2">
              <span className="text-ink-3">Свежесть: </span>
              {fresh.label}, последняя активность {fresh.since}
            </p>
          )}
          {salarySignal && (
            <p className="t-small text-ink-2">
              <span className="text-ink-3">Ожидания по зарплате: </span>
              {salarySignal}
            </p>
          )}
          {history.length > 0 && (
            <p className="t-small text-ink-2">
              <span className="text-ink-3">История грейда: </span>
              {history.map((h, i) => (
                <span key={i}>
                  {i > 0 && ' → '}
                  {h.to_grade ?? '—'} ({h.reason}, {h.when})
                </span>
              ))}
            </p>
          )}
        </section>
      )}

      <div className="flex flex-wrap items-center gap-3">
        {invited ? (
          <Tag tone="brand">Приглашён: {invited}</Tag>
        ) : (
          <Button onClick={onInvite}>Пригласить</Button>
        )}
        <Button variant="outline" onClick={onTogglePin} loading={pinBusy} aria-pressed={pinned}>
          {pinned ? 'Убрать из шортлиста' : 'В шортлист'}
        </Button>
      </div>
      <p className="t-small text-ink-3">Имя и контакты откроются, только когда кандидат ответит «да».</p>
    </div>
  )
}
