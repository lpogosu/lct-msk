import { useEffect, useMemo, useState, type MutableRefObject } from 'react'
import type { Schemas } from '@/api/types'
import { cn } from '@/lib/cn'
import { Button, Input } from '@/ui'

export type TestItem = Schemas['ItemOut']
export type AnswerValue = string | number | number[] | { line: number; fix: string } | null

/** Тёмная панель кода (Figma К4): номера строк приглушены, код — JetBrains Mono. Строки можно выбирать (bug_line). */
export function CodeBlock({
  code,
  selectedLine,
  onSelectLine,
  label,
}: {
  code: string
  selectedLine?: number | null
  onSelectLine?: (line: number) => void
  label?: string
}) {
  const lines = code.split('\n')
  return (
    <div
      className="overflow-x-auto rounded-panel-lg bg-code-bg py-3 text-white"
      role={onSelectLine ? 'group' : 'figure'}
      aria-label={label ?? 'Код задания'}
      translate="no"
    >
      <pre className="m-0 min-w-max font-mono text-[14px] leading-[22px]">
        {lines.map((ln, i) => {
          const n = i + 1
          const selected = selectedLine === n
          const content = (
            <>
              <span aria-hidden="true" className="w-6 shrink-0 select-none text-right text-code-dim">
                {n}
              </span>
              <span className="whitespace-pre pr-4">{ln === '' ? ' ' : ln}</span>
            </>
          )
          return onSelectLine ? (
            <button
              key={i}
              type="button"
              onClick={() => onSelectLine(n)}
              aria-pressed={selected}
              aria-label={`Строка ${n}: ${ln}`}
              className={cn(
                'flex w-full gap-4 px-3.5 text-left font-mono text-[14px] leading-[22px] text-white transition-colors',
                selected ? 'bg-brand/60' : 'hover:bg-white/10',
              )}
            >
              {content}
            </button>
          ) : (
            <div key={i} className="flex gap-4 px-3.5">
              {content}
            </div>
          )
        })}
      </pre>
    </div>
  )
}

/** Мини-таблица для SQL-заданий: NULL показываем явно (иначе его легко принять за пустую строку). */
export function DataTable({ table }: { table: NonNullable<TestItem['table']> }) {
  const name = typeof table.name === 'string' ? table.name : null
  const columns = Array.isArray(table.columns) ? (table.columns as unknown[]).map(String) : []
  const rows = Array.isArray(table.rows) ? (table.rows as unknown[][]) : []
  return (
    <div className="overflow-x-auto rounded-panel-lg border border-hairline" translate="no">
      {name && <p className="border-b border-hairline px-3.5 py-2 font-mono text-[13px] text-ink-2">{name}</p>}
      <table className="w-full border-collapse font-mono text-[13px] leading-5">
        <thead>
          <tr className="bg-panel text-left text-ink-2">
            {columns.map((c) => (
              <th key={c} scope="col" className="px-3.5 py-1.5 font-medium">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-hairline">
              {r.map((v, j) => (
                <td key={j} className="px-3.5 py-1.5 text-ink">
                  {v === null || v === undefined ? <span className="italic text-ink-3">NULL</span> : String(v)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function OptionList({
  options,
  value,
  onChange,
  label,
}: {
  options: TestItem['options']
  value: string | null
  onChange: (id: string) => void
  label: string
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-col gap-2.5">
      {(options ?? []).map((o) => {
        const on = value === o.id
        return (
          <button
            key={o.id}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => onChange(o.id)}
            className={cn(
              'flex items-start gap-3 rounded-panel-lg border px-4 py-3 text-left transition-colors',
              on ? 'border-brand bg-tint' : 'border-steel bg-white hover:border-brand',
            )}
          >
            <span
              aria-hidden="true"
              className={cn(
                'mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border font-mono text-[11px] uppercase',
                on ? 'border-brand bg-brand text-white' : 'border-steel text-ink-3',
              )}
            >
              {o.id}
            </span>
            <span className="min-w-0 whitespace-pre-wrap break-words font-mono text-[14px] leading-[22px] text-ink">{o.text}</span>
          </button>
        )
      })}
    </div>
  )
}

function OrderList({ lines, order, onChange }: { lines: string[]; order: number[]; onChange: (o: number[]) => void }) {
  const move = (pos: number, dir: -1 | 1) => {
    const to = pos + dir
    if (to < 0 || to >= order.length) return
    const next = [...order]
    ;[next[pos], next[to]] = [next[to], next[pos]]
    onChange(next)
  }
  return (
    <ol className="m-0 flex list-none flex-col gap-2 p-0" aria-label="Порядок строк: сверху — первая команда">
      {order.map((idx, pos) => (
        <li key={idx} className="flex items-center gap-2">
          <span className="flex w-6 shrink-0 items-center justify-center font-mono text-[13px] text-ink-3">{pos + 1}</span>
          <span className="min-w-0 flex-1 whitespace-pre-wrap break-words rounded-panel border border-steel bg-white px-3.5 py-2.5 font-mono text-[14px] leading-[22px] text-ink">
            {lines[idx]}
          </span>
          <span className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={() => move(pos, -1)}
              disabled={pos === 0}
              aria-label={`Поднять выше: ${lines[idx]}`}
              className="flex h-10 w-9 items-center justify-center rounded-md border border-steel text-ink hover:border-brand hover:text-brand disabled:cursor-not-allowed disabled:opacity-35"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                <path d="M3 8.5L7 4.5l4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            <button
              type="button"
              onClick={() => move(pos, 1)}
              disabled={pos === order.length - 1}
              aria-label={`Опустить ниже: ${lines[idx]}`}
              className="flex h-10 w-9 items-center justify-center rounded-md border border-steel text-ink hover:border-brand hover:text-brand disabled:cursor-not-allowed disabled:opacity-35"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                <path d="M3 5.5l4 4 4-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </span>
        </li>
      ))}
    </ol>
  )
}

/**
 * Одно задание (К4): условие, код, таблица и поле ответа по режиму `answer_mode`:
 * text — строка, choice — один вариант, line_choice — строка кода + исправление, order — порядок строк.
 * Компонент создаётся заново для каждого задания (key = seq), поэтому состояние ответа не «протекает».
 */
export function ItemPlayer({
  item,
  submitting,
  onSubmit,
  answerRef,
  disabled,
  hintAnswer,
}: {
  item: TestItem
  submitting: boolean
  onSubmit: (answer: AnswerValue) => void
  /** Родитель забирает текущий ответ при истечении таймера */
  answerRef: MutableRefObject<() => AnswerValue>
  disabled?: boolean
  /** Режим жюри (только DEMO_MODE): верный ответ, который нужно подставить в поле */
  hintAnswer?: unknown
}) {
  const mode = item.answer_mode
  const lines = useMemo(() => item.lines ?? [], [item.lines])
  const [text, setText] = useState('')
  const [choice, setChoice] = useState<string | null>(null)
  const [line, setLine] = useState<number | null>(null)
  const [fix, setFix] = useState<string | null>(null)
  const [order, setOrder] = useState<number[]>(() => lines.map((_, i) => i))

  const current = (): AnswerValue => {
    switch (mode) {
      case 'text':
        return text.trim() === '' ? null : text
      case 'choice':
        return choice
      case 'line_choice':
        return line != null && fix != null ? { line, fix } : null
      case 'order':
        return order
      default:
        return null
    }
  }

  useEffect(() => {
    answerRef.current = current
  })

  useEffect(() => {
    if (hintAnswer === undefined || hintAnswer === null) return
    if (mode === 'text') setText(String(hintAnswer))
    else if (mode === 'choice') setChoice(String(hintAnswer))
    else if (mode === 'line_choice' && typeof hintAnswer === 'object') {
      const h = hintAnswer as { line?: number; fix?: string }
      if (typeof h.line === 'number') setLine(h.line)
      if (typeof h.fix === 'string') setFix(h.fix)
    } else if (mode === 'order' && Array.isArray(hintAnswer)) {
      const idx = (hintAnswer as unknown[]).map((t) => lines.indexOf(String(t))).filter((i) => i >= 0)
      if (idx.length === lines.length) setOrder(idx)
    }
  }, [hintAnswer, mode, lines])

  const ready = current() !== null

  return (
    <div className="flex flex-col gap-5">
      <div data-task-block className="flex flex-col gap-4">
        <h2 className="t-name text-ink">{item.prompt}</h2>
        {item.code && mode !== 'line_choice' && <CodeBlock code={item.code} label={item.language ? `Код на ${item.language}` : undefined} />}
        {item.code && mode === 'line_choice' && (
          <>
            <CodeBlock code={item.code} selectedLine={line} onSelectLine={setLine} label="Код: нажмите на строку с ошибкой" />
            <p className="-mt-2 t-small text-ink-3">
              {line == null ? 'Нажмите на строку с ошибкой.' : `Выбрана строка ${line}.`}
            </p>
          </>
        )}
        {item.table && <DataTable table={item.table} />}
      </div>

      {mode === 'text' && (
        <div data-answer-field>
          <Input
            label="Ваш ответ"
            mono
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && ready && !submitting && !disabled) {
                e.preventDefault()
                onSubmit(current())
              }
            }}
            hint={item.answer_hint ?? undefined}
            autoComplete="off"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            enterKeyHint="send"
          />
        </div>
      )}

      {mode === 'choice' && (
        <div className="flex flex-col gap-2">
          <p className="t-label text-ink-2">Ваш ответ</p>
          <OptionList options={item.options} value={choice} onChange={setChoice} label="Варианты ответа" />
          {item.answer_hint && <p className="t-small text-ink-3">{item.answer_hint}</p>}
        </div>
      )}

      {mode === 'line_choice' && (
        <div className="flex flex-col gap-2">
          <p className="t-label text-ink-2">Исправление</p>
          <OptionList options={item.options} value={fix} onChange={setFix} label="Варианты исправления" />
          {item.answer_hint && <p className="t-small text-ink-3">{item.answer_hint}</p>}
        </div>
      )}

      {mode === 'order' && (
        <div className="flex flex-col gap-2">
          <p className="t-label text-ink-2">Ваш порядок</p>
          <OrderList lines={lines} order={order} onChange={setOrder} />
          {item.answer_hint && <p className="t-small text-ink-3">{item.answer_hint}</p>}
        </div>
      )}

      <div className="flex flex-col gap-2">
        <Button block size="lg" disabled={!ready || disabled} loading={submitting} onClick={() => onSubmit(current())}>
          Ответить
        </Button>
        <Button variant="text" className="self-center" disabled={submitting || disabled} onClick={() => onSubmit(null)}>
          Не знаю — пропустить
        </Button>
      </div>
    </div>
  )
}
