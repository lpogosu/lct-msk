import { useRef, useState, type ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { countWord, formatDecimal } from '@/lib/format'
import { Button, Identicon, Ruler, Skeleton, TrustNode } from '@/ui'
import { Drawer } from '@/ui/Drawer'
import { useCandidateCards, type SelectionItem } from '../../api'
import { bestOf, blindColumn, sumParts, type CompareColumn, type CompareEntry } from '../../compare'
import { FORMAT_LABEL, SCORE_PARTS } from '../../dict'
import { regionLabel } from '../../regions'
import { InterestTag, Tag } from '../common'

type Chip = SelectionItem['chips'][number]

/** Ячейка строки: колонка, её индекс и «лучшее значение в строке» (подсветка уже на фоне ячейки). */
type CellFn = (c: CompareColumn & { item: SelectionItem }, i: number, best: boolean) => ReactNode

interface Row {
  key: string
  label: ReactNode
  hint?: ReactNode
  /** Колонки с лучшим значением строки (пусто — не подсвечиваем) */
  best?: Set<number>
  cell: CellFn
}

interface Section {
  key: string
  title: string
  note?: ReactNode
  rows: Row[]
}

const PART_KEYS = SCORE_PARTS.map((p) => p.key)

function Muted({ children }: { children: ReactNode }) {
  return <span className="t-small text-ink-3">{children}</span>
}

function Multiplier({ value, note }: { value: number; note?: string }) {
  const below = value < 1 - 1e-6
  return (
    <span className="flex flex-wrap items-baseline gap-x-2">
      <span className={cn('t-label tabular-nums', below ? 'text-danger-text' : 'text-ink-2')}>×{formatDecimal(value, 2)}</span>
      {below && note && <span className="t-small text-ink-3">{note}</span>}
    </span>
  )
}

/** Вклад компонента: та же линейка, что в разложении Score панели доказательств (длина — доля от вес × 100). */
function PartCell({ value, max, best, note }: { value: number; max: number; best: boolean; note?: string }) {
  const frac = Math.max(0, Math.min(1, max > 0 ? value / max : 0))
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-center gap-3">
        <span className="relative block h-4 min-w-0 flex-1" aria-hidden="true">
          <span className="absolute inset-x-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-bar" />
          <span className="absolute left-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-brand" style={{ width: `${frac * 100}%` }} />
        </span>
        <span className={cn('w-12 shrink-0 text-right t-label tabular-nums', best ? 'text-brand' : 'text-ink')}>
          {formatDecimal(value, 1)}
        </span>
      </div>
      {note && <Muted>{note}</Muted>}
    </div>
  )
}

function SkillCell({ chip }: { chip: Chip | undefined }) {
  if (!chip) return <Muted>—</Muted>
  if (chip.state === 'confirmed' || chip.state === 'weak') {
    return (
      <span className="flex items-start gap-2 t-small text-ink">
        <TrustNode kind={chip.source === 'fsp' ? 'fsp' : 'test'} className="mt-0.5" />
        <span>
          {chip.label}
          {chip.state === 'weak' && <span className="text-ink-3"> · подтверждено слабо</span>}
        </span>
      </span>
    )
  }
  return (
    <span className="flex items-start gap-2 t-small text-ink-3">
      {chip.state === 'declared' ? (
        <TrustNode kind="claimed" className="mt-0.5" />
      ) : (
        <span aria-hidden="true" className="w-[14px] shrink-0 text-center leading-[18px]">
          ·
        </span>
      )}
      <span>
        {chip.state === 'declared' ? 'заявлено, не подтверждено' : 'нет данных'}
        {chip.untestable && ' · тестом не проверяется'}
      </span>
    </span>
  )
}

function chipsOf(cols: CompareColumn[], kind: 'must' | 'nice'): { id: string; name: string }[] {
  const seen = new Map<string, string>()
  for (const c of cols) for (const ch of c.item?.chips ?? []) if (ch.kind === kind && !seen.has(ch.skill_id)) seen.set(ch.skill_id, ch.name)
  return [...seen].map(([id, name]) => ({ id, name }))
}

const chipFor = (item: SelectionItem, id: string) => item.chips.find((ch) => ch.skill_id === id)

/** Строки экрана сравнения: всё — из слепой карточки; порядок — как читают карточку сверху вниз. */
function buildSections(cols: CompareColumn[], weights: Record<string, number>): Section[] {
  const v = (f: (it: SelectionItem) => number | null | undefined) => cols.map((c) => (c.item ? f(c.item) : null))
  const must = chipsOf(cols, 'must')
  const nice = chipsOf(cols, 'nice')
  const anyInterest = cols.some((c) => c.interest)

  const parts: Row[] = SCORE_PARTS.map((p) => {
    const w = weights[p.key] ?? 0
    const isF = p.key === 'F'
    return {
      key: `part-${p.key}`,
      label: p.label,
      hint: `${p.key} · вес ${formatDecimal(w, 2)} · до ${formatDecimal(w * 100, 0)}`,
      // ФСП — бонус: без истории F = 0 и это не минус, поэтому «лучший» здесь не подсвечивается
      best: isF ? undefined : bestOf(v((it) => it.parts[p.key] ?? 0)),
      cell: (c, _i, best) => (
        <PartCell
          value={c.item.parts[p.key] ?? 0}
          max={(weights[p.key] ?? 0.2) * 100}
          best={best}
          note={isF && !c.item.has_fsp ? 'нет истории ФСП — не штраф' : undefined}
        />
      ),
    }
  })

  return [
    {
      key: 'who',
      title: 'Категория и тест',
      rows: [
        {
          key: 'category',
          label: 'Категория',
          cell: (c) => (
            <div className="flex flex-col items-start gap-1">
              <span className="t-body text-ink">{c.categoryName || c.item.category}</span>
              {c.confirmed === false ? <Tag tone="warn">грейд не подтверждён</Tag> : <Tag tone="brand">подтверждена тестом</Tag>}
              {c.item.flags.includes('borderline') && <Muted>тест на границе грейда</Muted>}
            </div>
          ),
        },
        {
          key: 'level',
          label: 'Уровень по тесту',
          hint: 'интервал на линейке 300–600',
          cell: (c) => {
            const iv = c.item.test.interval
            if (!iv || iv.length < 2) return <Muted>тест ещё не пройден</Muted>
            return (
              <div className="flex max-w-[220px] flex-col gap-0.5">
                <Ruler interval={[iv[0], iv[1]]} size="compact" />
                <Muted>{c.item.test.band ? `${c.item.test.band} категории` : `${iv[0]}–${iv[1]}`}</Muted>
              </div>
            )
          },
        },
        {
          key: 'fresh',
          label: 'Свежесть теста',
          best: bestOf(v((it) => it.freshness?.relevance)),
          cell: (c, _i, best) => (
            <div className="flex flex-col items-start gap-0.5">
              <span className={cn('t-body', best ? 'text-brand' : 'text-ink')}>
                {c.testDate ? `Тест пройден ${c.testDate}` : (c.item.freshness?.label ?? '—')}
              </span>
              {c.item.freshness && (
                <Muted>
                  {c.testDate ? `${c.item.freshness.label}, ` : ''}активность {c.item.freshness.since}
                </Muted>
              )}
              {c.item.flags.includes('stale_test') && <Tag tone="warn">тест устарел</Tag>}
            </div>
          ),
        },
      ],
    },
    {
      key: 'score',
      title: 'Score и его разложение',
      note: 'Те же числа, что в карточке кандидата. Вклады уже учитывают множители G · Cov · Cond: Score = 100 · G · Cov · Cond · Σ вес × компонент, а сумма вкладов ровно равна Score.',
      rows: [
        {
          key: 'score',
          label: 'Score',
          hint: 'из 100',
          best: bestOf(v((it) => it.score)),
          cell: (c, _i, best) => (
            <span className={cn('t-num-m', best ? 'text-brand' : 'text-ink')} data-testid="compare-score">
              {formatDecimal(c.item.score, 1)}
            </span>
          ),
        },
        ...parts,
        {
          key: 'sum',
          label: 'Σ вкладов = Score',
          cell: (c) => {
            const s = sumParts(c.item.parts, PART_KEYS)
            const ok = Math.abs(s - c.item.score) < 0.05
            return (
              <span className="t-small text-ink-2 tabular-nums" data-testid="compare-sum">
                {PART_KEYS.map((k) => formatDecimal(c.item.parts[k] ?? 0, 1)).join(' + ')} ={' '}
                <span className="t-label text-ink">{formatDecimal(s, 1)}</span>
                {ok && (
                  <span className="text-brand" title="Сумма вкладов равна Score">
                    {' '}
                    ✓
                  </span>
                )}
              </span>
            )
          },
        },
        {
          key: 'g',
          label: 'G — смежность',
          hint: '1,00 — основная категория брифа',
          cell: (c) => <Multiplier value={c.item.factors.g} note="смежная категория" />,
        },
        {
          key: 'cov',
          label: 'Cov — покрытие обязательных',
          cell: (c) => <Multiplier value={c.item.factors.cov} note="подтверждено меньше половины обязательных" />,
        },
        {
          key: 'cond',
          label: 'Cond — условия',
          hint: 'зарплата × формат и город',
          cell: (c) => {
            const f = c.item.factors
            const sub = [
              f.cond_salary < 1 - 1e-6 && `зарплата ×${formatDecimal(f.cond_salary, 2)}`,
              f.cond_format < 1 - 1e-6 && `формат или город ×${formatDecimal(f.cond_format, 2)}`,
            ]
              .filter(Boolean)
              .join(', ')
            return <Multiplier value={f.cond} note={sub || undefined} />
          },
        },
      ],
    },
    {
      key: 'why',
      title: 'Почему в подборке',
      rows: [
        {
          key: 'pros',
          label: 'За',
          cell: (c) =>
            c.item.pros.length > 0 ? (
              <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
                {c.item.pros.map((r) => (
                  <li key={r.code + r.text} className="flex gap-2 t-small text-ink">
                    <span aria-hidden="true" className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-brand" />
                    <span>{r.text}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <Muted>Данных для объяснения пока мало</Muted>
            ),
        },
        {
          key: 'con',
          label: 'Против',
          hint: 'не больше одного; отсутствие ФСП сюда не попадает',
          cell: (c) =>
            c.item.con ? (
              <span className="flex gap-2 t-small text-danger-text">
                <span aria-hidden="true" className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-danger" />
                <span>{c.item.con.text}</span>
              </span>
            ) : (
              <Muted>нет</Muted>
            ),
        },
      ],
    },
    {
      key: 'skills',
      title: 'Что вы просили и чем это подтверждено',
      note: 'Заявленное кандидатом показано серым и на Score не влияет.',
      rows: [
        ...(must.length > 0
          ? [
              {
                key: 'must-cov',
                label: 'Обязательные навыки',
                hint: 'подтверждено тестом, задачей или ФСП',
                best: bestOf(v((it) => it.chips.filter((ch) => ch.kind === 'must' && ch.state === 'confirmed').length), 'max', 0.5),
                cell: (c, _i, best) => {
                  const m = c.item.chips.filter((ch) => ch.kind === 'must')
                  const ok = m.filter((ch) => ch.state === 'confirmed').length
                  const weak = m.filter((ch) => ch.state === 'weak').length
                  return (
                    <span className="flex flex-wrap items-baseline gap-x-2">
                      <span className={cn('t-label', best ? 'text-brand' : 'text-ink')}>
                        {ok} из {m.length} подтверждено
                      </span>
                      {weak > 0 && <Muted>ещё {weak} слабо</Muted>}
                    </span>
                  )
                },
              } satisfies Row,
            ]
          : []),
        ...must.map(
          (s): Row => ({
            key: `must-${s.id}`,
            label: s.name,
            hint: 'обязательно',
            cell: (c) => <SkillCell chip={chipFor(c.item, s.id)} />,
          }),
        ),
        ...(nice.length > 0
          ? [
              {
                key: 'nice',
                label: 'Желательные',
                hint: 'будут плюсом',
                cell: (c) => (
                  <ul className="m-0 flex list-none flex-col gap-1 p-0">
                    {nice.map((s) => (
                      <li key={s.id} className="flex flex-col">
                        <span className="t-small text-ink-2">{s.name}</span>
                        <SkillCell chip={chipFor(c.item, s.id)} />
                      </li>
                    ))}
                  </ul>
                ),
              } satisfies Row,
            ]
          : []),
      ],
    },
    {
      key: 'terms',
      title: 'ФСП и условия',
      rows: [
        {
          key: 'fsp',
          label: 'Достижения ФСП',
          hint: 'обобщённо; протокол — после согласия',
          cell: (c) =>
            c.item.has_fsp ? (
              <div className="flex flex-col items-start gap-1">
                <span className="t-small text-ink">{c.item.fsp_line}</span>
                {c.item.fsp_badges.length > 0 && (
                  <span className="flex flex-wrap gap-1.5">
                    {c.item.fsp_badges.map((b) => (
                      <Tag key={b} tone="brand">
                        {b}
                      </Tag>
                    ))}
                  </span>
                )}
              </div>
            ) : (
              <div className="flex flex-col">
                <span className="t-small text-ink-2">Нет истории ФСП</span>
                <Muted>Это не минус: оценка по тесту и задачам</Muted>
              </div>
            ),
        },
        {
          key: 'salary',
          label: 'Ожидания по зарплате',
          hint: 'относительно вашей вилки',
          best: bestOf(v((it) => it.factors.cond_salary), 'max', 0.005),
          cell: (c, _i, best) => (
            <span className={cn('t-body', best ? 'text-brand' : c.item.factors.cond_salary < 1 - 1e-6 ? 'text-ink-2' : 'text-ink')}>
              {c.item.salary?.signal ?? <Muted>не указаны</Muted>}
            </span>
          ),
        },
        {
          key: 'format',
          label: 'Формат и регион',
          hint: 'город — после раскрытия',
          best: bestOf(v((it) => it.factors.cond_format), 'max', 0.005),
          cell: (c, _i, best) => {
            const formats = c.formats.map((f) => FORMAT_LABEL[f] ?? f).join(', ')
            const region = regionLabel(c.region)
            const off = c.item.factors.cond_format < 1 - 1e-6
            return (
              <div className="flex flex-col">
                <span className={cn('t-body', best ? 'text-brand' : 'text-ink')}>{formats || '—'}</span>
                {region && <Muted>{region}</Muted>}
                {off && <Muted>не совпадает с брифом</Muted>}
              </div>
            )
          },
        },
        ...(anyInterest
          ? [
              {
                key: 'interest',
                label: 'Сигнал интереса',
                hint: 'отдельно от Score',
                cell: (c) => (c.interest ? <InterestTag interest={c.interest} /> : <Muted>—</Muted>),
              } satisfies Row,
            ]
          : []),
      ],
    },
  ]
}

function ColumnHead({ c, onRemove, compact }: { c: CompareColumn; onRemove: () => void; compact?: boolean }) {
  const it = c.item
  const place = it?.rank != null ? `#${it.rank} в общем топе` : it && it.hidden_by.length > 0 ? 'скрыт фильтрами' : null
  return (
    <div className="flex items-start justify-between gap-2">
      <div className="flex min-w-0 items-center gap-2.5">
        <Identicon seed={c.pseudonym} size={compact ? 28 : 32} />
        <div className="min-w-0">
          <p className="t-name text-ink">{c.pseudonym}</p>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 t-small text-ink-3">
            {place && <span>{place}</span>}
            {it?.shortlisted && <span className="text-brand">в шортлисте</span>}
          </p>
        </div>
      </div>
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Убрать ${c.pseudonym} из сравнения`}
        title="Убрать из сравнения"
        className="-mr-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-ink-3 hover:bg-panel hover:text-ink"
      >
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
          <path d="M3.5 3.5l7 7M10.5 3.5l-7 7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  )
}

function ColumnActions({
  c,
  invited,
  closed,
  onInvite,
  onOpenCard,
}: {
  c: CompareColumn
  invited: string | null
  closed: boolean
  onInvite: () => void
  onOpenCard: () => void
}) {
  if (c.state !== 'ready') return null
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      {invited ? (
        <Tag tone="brand">Приглашён: {invited}</Tag>
      ) : closed ? (
        <Muted>Потребность закрыта</Muted>
      ) : (
        <Button size="sm" onClick={onInvite} aria-label={`Пригласить ${c.pseudonym}`}>
          Пригласить
        </Button>
      )}
      <Button variant="text" onClick={onOpenCard}>
        Открыть карточку
      </Button>
    </div>
  )
}

/** Ячейка, пока колонка не готова: загрузка, ошибка или кандидат больше не виден. */
function PendingCell({ c, first }: { c: CompareColumn; first: boolean }) {
  if (c.state === 'loading') return <Skeleton className="h-5 w-3/4" />
  if (first) return <span className="t-small text-ink-2">{c.state === 'unavailable' ? c.note : 'Не удалось загрузить карточку'}</span>
  return <Muted>—</Muted>
}

/**
 * «Сравнить кандидатов» (до 3 бок о бок): колонки — кандидаты, строки — признаки слепой карточки. Подсветка — лучшее
 * значение в строке, без общего «победителя»: Score уже всё взвесил, решение за работодателем. На телефоне — по одному
 * кандидату на экран, листаются свайпом. Пригласить можно прямо отсюда — тем же окном приглашения, что из карточки.
 */
export function CompareDialog(props: {
  open: boolean
  onClose: () => void
  needId: string
  entries: CompareEntry[]
  /** Уже загруженные строки подборки (страница, скрытые, шортлист) — показываем сразу, пока идут карточки */
  known: Map<string, SelectionItem>
  weights: Record<string, number>
  /** Пояснение, если на экране не текущая версия подборки */
  versionNote?: string | null
  closed: boolean
  onRemove: (ref: string) => void
  onInvite: (ref: string) => void
  onOpenCard: (ref: string) => void
}) {
  if (!props.open) return null
  return <CompareBody {...props} />
}

function CompareBody({
  onClose,
  needId,
  entries,
  known,
  weights,
  versionNote,
  closed,
  onRemove,
  onInvite,
  onOpenCard,
}: Parameters<typeof CompareDialog>[0]) {
  const qs = useCandidateCards(
    needId,
    entries.map((e) => e.ref),
  )
  const cols = entries.map((e, i) => blindColumn(e, qs[i]?.data, known.get(e.ref), { isPending: !!qs[i]?.isPending, isError: !!qs[i]?.isError }))
  const sections = buildSections(cols, weights)
  const invitedOf = (c: CompareColumn) => known.get(c.ref)?.invitation_label ?? c.item?.invitation_label ?? null

  const scroller = useRef<HTMLDivElement>(null)
  const [active, setActive] = useState(0)
  const onScroll = () => {
    const el = scroller.current
    const card = el?.firstElementChild as HTMLElement | null
    if (!el || !card) return
    setActive(Math.min(cols.length - 1, Math.round(el.scrollLeft / (card.offsetWidth + 12))))
  }
  const scrollTo = (i: number) => {
    const el = scroller.current?.children[i] as HTMLElement | undefined
    el?.scrollIntoView({ behavior: 'smooth', inline: 'start', block: 'nearest' })
  }

  const renderCell = (row: Row, c: CompareColumn, i: number, first: boolean) => {
    if (c.state !== 'ready' || !c.item) return <PendingCell c={c} first={first} />
    return row.cell(c as CompareColumn & { item: SelectionItem }, i, !!row.best?.has(i))
  }

  return (
    <Drawer
      open
      onClose={onClose}
      title="Сравнение кандидатов"
      eyebrow={`Слепые карточки · ${countWord(cols.length, ['кандидат', 'кандидата', 'кандидатов'])}`}
      width={1200}
      footer={
        <p className="t-small text-ink-3">
          Только данные слепой карточки: имя и контакты откроются, когда кандидат ответит «да». Резюме и самоописание на Score
          не влияют.
        </p>
      }
    >
      {entries.length < 2 ? (
        <div className="flex flex-col items-start gap-4">
          <p className="t-body text-ink-2">Для сравнения нужно хотя бы два кандидата. Отметьте ещё кого-нибудь в подборке.</p>
          <Button variant="outline" size="sm" onClick={onClose}>
            Вернуться к подборке
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <p className="t-small text-ink-2">
            <span aria-hidden="true" className="mr-1.5 inline-block h-3 w-3 translate-y-0.5 rounded-[3px] bg-tint ring-1 ring-inset ring-heat-2" />
            Фоном отмечено лучшее значение в строке — это подсказка для взгляда, а не итог: решение за вами.
          </p>
          {versionNote && <p className="rounded-panel bg-panel px-3 py-2 t-small text-ink-2">{versionNote}</p>}

          {/* Десктоп и планшет: таблица, колонки — кандидаты */}
          <table className="hidden w-full table-fixed border-collapse text-left md:table" data-testid="compare-table">
            <caption className="sr-only">Сравнение кандидатов: строки — признаки, столбцы — кандидаты</caption>
            <colgroup>
              <col className="w-[190px] lg:w-[220px]" />
              {cols.map((c) => (
                <col key={c.ref} />
              ))}
            </colgroup>
            <thead>
              <tr>
                <td className="sticky top-0 z-10 bg-white shadow-[inset_0_-1px_0_#E4E4E7]" />
                {cols.map((c) => (
                  <th
                    key={c.ref}
                    scope="col"
                    className="sticky top-0 z-10 bg-white px-3 pb-3 pt-1 align-bottom font-normal shadow-[inset_0_-1px_0_#E4E4E7]"
                  >
                    <ColumnHead c={c} onRemove={() => onRemove(c.ref)} />
                  </th>
                ))}
              </tr>
            </thead>
            {sections.map((s, si) => (
              <tbody key={s.key}>
                <tr>
                  <th scope="colgroup" colSpan={cols.length + 1} className="pb-2 pt-6 text-left font-normal">
                    <span className="t-body-strong text-ink">{s.title}</span>
                    {s.note && <span className="mt-0.5 block max-w-[760px] t-small text-ink-3">{s.note}</span>}
                  </th>
                </tr>
                {s.rows.map((row, ri) => (
                  <tr key={row.key} className="border-b border-hairline align-top">
                    <th scope="row" className="py-3 pr-4 text-left font-normal">
                      <span className="block t-small text-ink">{row.label}</span>
                      {row.hint && <span className="block t-small text-ink-3">{row.hint}</span>}
                    </th>
                    {cols.map((c, i) => {
                      const best = c.state === 'ready' && !!row.best?.has(i)
                      return (
                        <td key={c.ref} className={cn('px-3 py-3', best && 'bg-tint')} data-best={best || undefined}>
                          {renderCell(row, c, i, si === 0 && ri === 0)}
                          {best && <span className="sr-only"> — лучшее значение в строке</span>}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            ))}
            <tfoot>
              <tr>
                <td />
                {cols.map((c) => (
                  <td key={c.ref} className="px-3 pb-1 pt-5">
                    <ColumnActions
                      c={c}
                      invited={invitedOf(c)}
                      closed={closed}
                      onInvite={() => onInvite(c.ref)}
                      onOpenCard={() => onOpenCard(c.ref)}
                    />
                  </td>
                ))}
              </tr>
            </tfoot>
          </table>

          {/* Телефон: по одному кандидату на экран, свайп вбок */}
          <div className="md:hidden">
            <div className="mb-2 flex items-center justify-between gap-3">
              <p className="t-small text-ink-3" aria-live="polite">
                Кандидат {active + 1} из {cols.length} · листайте вбок
              </p>
              <div className="flex gap-1.5">
                {cols.map((c, i) => (
                  <button
                    key={c.ref}
                    type="button"
                    onClick={() => scrollTo(i)}
                    aria-label={`Показать ${c.pseudonym}`}
                    aria-current={i === active || undefined}
                    className={cn('h-2 w-2 rounded-full', i === active ? 'bg-brand' : 'bg-bar')}
                  />
                ))}
              </div>
            </div>
            <div
              ref={scroller}
              onScroll={onScroll}
              className="-mx-5 flex snap-x snap-mandatory gap-3 overflow-x-auto scroll-px-5 px-5 pb-2"
            >
              {cols.map((c, i) => (
                <article
                  key={c.ref}
                  aria-label={c.pseudonym}
                  className="w-[86%] shrink-0 snap-start rounded-panel border border-hairline bg-white p-4"
                >
                  <ColumnHead c={c} compact onRemove={() => onRemove(c.ref)} />
                  {sections.map((s, si) => (
                    <section key={s.key} className="mt-4" aria-label={s.title}>
                      <h3 className="t-body-strong text-ink">{s.title}</h3>
                      <dl className="m-0">
                        {s.rows.map((row, ri) => {
                          const best = c.state === 'ready' && !!row.best?.has(i)
                          return (
                            <div key={row.key} className={cn('border-b border-hairline py-2.5', best && '-mx-2 rounded-cell border-transparent bg-tint px-2')}>
                              <dt className="mb-1 t-small text-ink-3">{row.label}</dt>
                              <dd className="m-0">
                                {renderCell(row, c, i, si === 0 && ri === 0)}
                                {best && <span className="sr-only"> — лучшее значение в строке</span>}
                              </dd>
                            </div>
                          )
                        })}
                      </dl>
                    </section>
                  ))}
                  <div className="mt-4">
                    <ColumnActions
                      c={c}
                      invited={invitedOf(c)}
                      closed={closed}
                      onInvite={() => onInvite(c.ref)}
                      onOpenCard={() => onOpenCard(c.ref)}
                    />
                  </div>
                </article>
              ))}
            </div>
          </div>
        </div>
      )}
    </Drawer>
  )
}
