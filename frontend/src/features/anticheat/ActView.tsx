import type { ReactNode } from 'react'
import { formatDate } from '@/lib/format'
import { cn } from '@/lib/cn'
import { Illustration } from '@/ui'
import type { Act, ActLine, ActStat } from './api'

/**
 * «Акт сверки» — экран Figma 39:2 («Трасса · Акт сверки (Антиподсказчик)»): две колонки «показано на экране / попало в
 * буфер обмена» с подсвеченным литералом, оба ключа, ответ кандидата с развилкой «совпал с ключом копии / не совпал с
 * ключом экрана», четыре числа и «Что это значит». Кнопки решения методиста передаются в `actions`.
 */
export function ActView({
  act,
  actions,
  compact,
  children,
}: {
  act: Act
  /** Кнопки решения справа от «Что это значит» (лаборатория); в песочнице жюри их нет */
  actions?: ReactNode
  /** Компактная шапка (в песочнице акт стоит внутри страницы результата) */
  compact?: boolean
  /** Дополнительные блоки под актом (чек-лист, хронология) */
  children?: ReactNode
}) {
  const copyMatched = act.matched === 'twin_key' || act.matched === 'twin_err' || act.matched === 'twin_token'
  return (
    <article className="flex flex-col gap-10" aria-label={act.title_ru}>
      <header className="flex items-start justify-between gap-6">
        <div className="flex min-w-0 flex-col gap-3">
          <p className="t-small text-ink-3">{act.label}</p>
          {compact ? (
            <h2 className="t-title text-brand">{act.headline_ru}</h2>
          ) : (
            <h1 className="t-display text-brand">{act.headline_ru}</h1>
          )}
          <p className="t-lead max-w-[820px] text-ink-2">{act.lead_ru}</p>
        </div>
        {!compact && <Illustration name="detector" className="hidden w-[200px] shrink-0 md:block" alt="" eager />}
      </header>

      <section className="grid gap-6 lg:grid-cols-2" aria-label="Показано и скопировано">
        <CodeColumn
          title="Показано на экране"
          lines={act.lines}
          side="shown"
          keyLabel="Ключ, вычисленный исполнением:"
          keyValue={act.keys.shown}
        />
        <CodeColumn
          title="Попало в буфер обмена"
          lines={act.lines}
          side="clipboard"
          keyLabel="Ключ копии, тоже исполнением:"
          keyValue={act.keys.clipboard}
          danger
        />
      </section>

      <AnswerPanel act={act} copyMatched={copyMatched} />

      <dl className="grid gap-y-6 sm:grid-cols-2 lg:grid-cols-4">
        {act.stats.map((s, i) => (
          <Stat key={s.id} stat={s} first={i === 0} />
        ))}
      </dl>

      <section className="flex flex-col justify-between gap-6 lg:flex-row lg:items-center" aria-labelledby="act-means">
        <div className="flex max-w-[919px] flex-col gap-1.5">
          <h2 id="act-means" className="t-name text-ink">
            {act.means_title}
          </h2>
          <p className="t-body text-ink-2">{act.means_ru}</p>
        </div>
        {actions && <div className="flex shrink-0 flex-wrap gap-3">{actions}</div>}
      </section>

      {children}
    </article>
  )
}

// ---------------------------------------------------------------------------------------------------------------------

function CodeColumn({
  title,
  lines,
  side,
  keyLabel,
  keyValue,
  danger,
}: {
  title: string
  lines: ActLine[]
  side: 'shown' | 'clipboard'
  keyLabel: string
  keyValue: string
  danger?: boolean
}) {
  return (
    <div className="flex min-w-0 flex-col gap-3.5">
      <h3 className="t-name text-ink">{title}</h3>
      <pre
        translate="no"
        className="m-0 overflow-x-auto rounded-panel bg-code-bg px-5 py-[18px] t-code text-white"
        tabIndex={0}
        aria-label={title}
      >
        {lines.map((ln) => (
          <CodeLine key={ln.no} line={ln} side={side} />
        ))}
      </pre>
      <p className="flex flex-wrap items-baseline gap-x-3 t-small text-ink-2">
        <span>{keyLabel}</span>
        <span className={cn('t-num-m break-all', danger ? 'text-danger-text' : 'text-ink')} translate="no">
          {keyValue}
        </span>
      </p>
    </div>
  )
}

function CodeLine({ line, side }: { line: ActLine; side: 'shown' | 'clipboard' }) {
  const text = side === 'shown' ? line.shown : line.clipboard
  const span = side === 'shown' ? line.shown_span : line.clipboard_span
  const mark =
    side === 'shown'
      ? 'rounded-[3px] bg-heat-4/60 px-px text-white'
      : 'rounded-[3px] bg-danger/55 px-px text-white'
  return (
    <span className="flex min-h-[28px] items-center gap-4 whitespace-pre">
      <span className="w-[18px] shrink-0 select-none text-right text-code-dim">{line.no}</span>
      <span>
        {span ? (
          <>
            {text.slice(0, span[0])}
            <mark className={mark}>{text.slice(span[0], span[1])}</mark>
            {text.slice(span[1])}
          </>
        ) : (
          text
        )}
      </span>
    </span>
  )
}

/** Ответ кандидата и развилка: сплошная линия к тому ключу, с которым ответ совпал, пунктир — к другому. */
function AnswerPanel({ act, copyMatched }: { act: Act; copyMatched: boolean }) {
  const screenMatched = act.matched === 'key'
  const copyLabel = copyMatched ? (act.matched === 'twin_key' ? 'совпал с ключом копии' : act.matched_ru) : 'не совпал с ключом копии'
  const screenLabel = screenMatched ? 'совпал с ключом экрана' : 'не совпал с ключом экрана'
  return (
    <section
      className="flex flex-col gap-6 rounded-panel bg-panel px-7 py-6 md:flex-row md:items-center"
      aria-label="Ответ кандидата"
    >
      <div className="flex min-w-0 shrink-0 flex-col gap-1 md:w-[200px]">
        <p className="t-small text-ink-3">Ответ кандидата</p>
        <p
          className={cn(
            'break-all font-mono font-medium tracking-[-0.5px] text-ink',
            (act.candidate_answer ?? '').length > 12 ? 'text-[18px] leading-[26px]' : 'text-[28px] leading-[34px]',
          )}
          translate="no"
        >
          {act.candidate_answer || '—'}
        </p>
      </div>
      <Fork copy={copyMatched} screen={screenMatched} />
      <div className="flex min-w-0 flex-1 flex-col gap-3.5">
        <p className={cn('t-body', copyMatched ? 'text-danger-text' : 'text-ink-3')}>
          <span className={copyMatched ? 't-label' : undefined}>{copyLabel}</span>{' '}
          <span className="font-mono" translate="no">
            {act.keys.clipboard}
          </span>
        </p>
        <p className={cn('t-body', screenMatched ? 'text-brand' : 'text-ink-3')}>
          <span className={screenMatched ? 't-label' : undefined}>{screenLabel}</span>{' '}
          <span className="font-mono" translate="no">
            {act.keys.shown}
          </span>
        </p>
      </div>
    </section>
  )
}

/** Развилка из Figma 39:70: от чёрной точки два пути — к ключу копии (сверху) и к ключу экрана (снизу). */
function Fork({ copy, screen }: { copy: boolean; screen: boolean }) {
  const trunk = copy ? 'stroke-danger' : screen ? 'stroke-brand' : 'stroke-steel'
  const top = copy ? 'stroke-danger' : 'stroke-steel'
  const bottom = screen ? 'stroke-brand' : 'stroke-steel'
  return (
    <svg width="300" height="40" viewBox="0 0 300 40" fill="none" aria-hidden="true" className="hidden shrink-0 md:block">
      <path d="M10 20 H70" className={trunk} strokeWidth="1.5" />
      <path d="M70 20 C85 20 90 8 105 8 H288" className={top} strokeWidth="1.5" strokeDasharray={copy ? undefined : '3 4'} />
      <path d="M70 20 C85 20 90 32 105 32 H288" className={bottom} strokeWidth="1.5" strokeDasharray={screen ? undefined : '3 4'} />
      <circle cx="6" cy="20" r="4.5" className="fill-ink" />
      <circle cx="294" cy="8" r="5" className={copy ? 'fill-danger stroke-danger' : 'fill-white stroke-steel'} strokeWidth="1.5" />
      <circle cx="294" cy="32" r="5" className={screen ? 'fill-brand stroke-brand' : 'fill-white stroke-steel'} strokeWidth="1.5" />
    </svg>
  )
}

function Stat({ stat, first }: { stat: ActStat; first: boolean }) {
  return (
    <div
      className={cn(
        'flex flex-col-reverse justify-end gap-1.5 sm:pr-6',
        !first && 'sm:border-l sm:border-hairline sm:pl-6',
        'lg:[&:nth-child(1)]:border-l-0 lg:[&:nth-child(1)]:pl-0',
      )}
    >
      <dt className="max-w-[300px] t-small text-ink-2">{stat.caption}</dt>
      <dd className={cn('t-num-l m-0', stat.tone === 'brand' ? 'text-brand' : 'text-ink')}>{stat.value}</dd>
    </div>
  )
}

// ---------------------------------------------------------------------------------------------------------------------

/** Чек-лист альтернативных объяснений, хронология и сводка по сессии: то, чего нет на экране Figma, но нужно методисту. */
export function ActDetails({ act }: { act: Act }) {
  const when = (v: string | null | undefined) =>
    v
      ? `${formatDate(v)} ${new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date(v))}`
      : 'нет в журнале'
  return (
    <section className="grid gap-8 border-t border-hairline pt-8 lg:grid-cols-3" aria-label="Подробности для методиста">
      <div className="flex flex-col gap-3">
        <h3 className="t-name text-ink">Вердикт</h3>
        <p className="t-body text-ink-2">{act.verdict_ru}</p>
        <p className="t-small text-ink-3">{act.p_honest_text}</p>
      </div>
      <div className="flex flex-col gap-3">
        <h3 className="t-name text-ink">Хронология</h3>
        <dl className="flex flex-col gap-1.5 t-body text-ink-2">
          <Row k="Предупреждение показано" v={when(act.warned_at)} />
          <Row k="Копирование" v={when(act.copy_at)} />
          <Row k="Ответ" v={when(act.answered_at)} />
          <Row k="Момент" v={act.phase_ru} />
        </dl>
        {act.session && (
          <dl className="mt-2 flex flex-col gap-1.5 border-t border-hairline pt-3 t-body text-ink-2">
            <Row k="По сессии" v={act.session.state_ru} />
            <Row k="Предлагаемый уровень" v={act.session.proposed_level_ru} />
            <Row k="Решает" v={act.session.decision} />
          </dl>
        )}
      </div>
      <div className="flex flex-col gap-3">
        <h3 className="t-name text-ink">Альтернативные объяснения</h3>
        <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
          {act.checklist.map((c) => (
            <li key={c.id} className="flex flex-col gap-0.5">
              <p className="t-label text-ink">
                {c.title_ru}{' '}
                <span className={cn('font-normal', c.status === 'attention' ? 'text-danger-text' : 'text-ink-3')}>— {c.status_ru}</span>
              </p>
              {c.note_ru && <p className="t-small text-ink-3">{c.note_ru}</p>}
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex flex-wrap justify-between gap-x-3">
      <dt className="text-ink-3">{k}</dt>
      <dd className="m-0 ml-auto text-right">{v}</dd>
    </div>
  )
}
