import { useCallback, useMemo, useState } from 'react'
import { ApiException } from '@/api/errors'
import { errorMessage } from '@/api/errors'
import { ActView } from '@/features/anticheat/ActView'
import {
  startSandbox,
  submitSandbox,
  type SandboxDiagnosis,
  type SandboxItem,
  type SandboxResult,
  type SandboxStart,
} from '@/features/anticheat/api'
import { useClipboardTwin, type TwinEventType } from '@/features/anticheat/useClipboardTwin'
import { cn } from '@/lib/cn'
import { Alert, Button, EmptyState, Input, Panel, Segmented, Spinner, ToastProvider } from '@/ui'

type Phase = 'intro' | 'loading' | 'test' | 'sending' | 'result' | 'unavailable'

const CHANNEL_TONE: Record<string, string> = {
  clipboard_text: 'bg-tint text-brand',
  screen_or_retyped: 'bg-panel text-ink-2',
  misconception: 'bg-panel text-ink-2',
  unknown: 'bg-panel text-ink-3',
}

/**
 * Песочница жюри «Попробуй списать» (публичная, только DEMO_MODE): мини-тест из 4 заданий, у каждого есть двойник в
 * буфере. Списывайте как угодно — после ответов видно, что именно заметила система и чего не ловит. Страница использует
 * тот же хук `useClipboardTwin`, что и тест кандидата: подмена в буфере работает так же, как в боевом плеере.
 */
export default function CheatLab() {
  return (
    <ToastProvider>
      <Inner />
    </ToastProvider>
  )
}

function Inner() {
  const [phase, setPhase] = useState<Phase>('intro')
  const [test, setTest] = useState<SandboxStart | null>(null)
  const [step, setStep] = useState(0)
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [copied, setCopied] = useState<number[]>([])
  const [result, setResult] = useState<SandboxResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const item = phase === 'test' && test ? test.items[step] : null
  const onEvent = useCallback(
    (type: TwinEventType, payload: Record<string, unknown>) => {
      if (type === 'self_paste') return
      const no = Number(payload.seq)
      if (Number.isFinite(no)) setCopied((c) => (c.includes(no) ? c : [...c, no]))
    },
    [],
  )
  useClipboardTwin(
    item ? { seq: item.no, prompt: item.prompt, code: item.code, table: item.table, clip: item.clip } : null,
    { onEvent, storageKey: test ? `vz:twin-warned:sandbox:${test.test_id}` : undefined },
  )

  async function begin() {
    setError(null)
    setPhase('loading')
    try {
      const t = await startSandbox()
      setTest(t)
      setStep(0)
      setAnswers({})
      setCopied([])
      setResult(null)
      setPhase('test')
    } catch (e) {
      if (e instanceof ApiException && e.status === 404) setPhase('unavailable')
      else {
        setError(errorMessage(e))
        setPhase('intro')
      }
    }
  }

  async function send() {
    if (!test) return
    setPhase('sending')
    setError(null)
    try {
      setResult(await submitSandbox(test.test_id, answers, copied))
      setPhase('result')
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (e) {
      setError(errorMessage(e))
      setPhase('test')
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-[1040px] flex-col gap-8 px-4 py-10 sm:px-6 lg:py-14">
      {phase === 'unavailable' && (
        <EmptyState
          image="detector"
          title="Песочница доступна только в демо-режиме"
          description="Здесь жюри может попробовать списать и увидеть, что заметит система. На боевом стенде этой страницы нет."
          action={<Button to="/">На главную</Button>}
          headingLevel={1}
        />
      )}

      {(phase === 'intro' || phase === 'loading') && <Intro loading={phase === 'loading'} onStart={() => void begin()} error={error} />}

      {(phase === 'test' || phase === 'sending') && test && (
        <TestStep
          test={test}
          step={step}
          answers={answers}
          copiedNos={copied}
          sending={phase === 'sending'}
          error={error}
          onAnswer={(no, v) => setAnswers((a) => ({ ...a, [String(no)]: v }))}
          onStep={(i) => {
            setStep(i)
            window.scrollTo({ top: 0 })
          }}
          onSend={() => void send()}
        />
      )}

      {phase === 'result' && result && <Result result={result} onAgain={() => void begin()} />}
    </div>
  )
}

function Intro({ loading, onStart, error }: { loading: boolean; onStart: () => void; error: string | null }) {
  return (
    <>
      <header className="flex flex-col gap-4">
        <p className="t-small text-ink-3">Для жюри · «Антиподсказчик»</p>
        <h1 className="t-display text-brand">Попробуй списать</h1>
        <p className="t-lead max-w-[760px] text-ink-2">
          Списывайте как угодно: чат-бот, скриншот, IDE. Четыре задания, у каждого есть «двойник в буфере». После ответов вы увидите, что именно
          заметила система, — и чего она не ловит.
        </p>
      </header>
      <Panel variant="tint" radius="lg" className="flex flex-col gap-4">
        <h2 className="t-name text-ink">Как это работает</h2>
        <ol className="m-0 flex list-none flex-col gap-2.5 p-0 t-body text-ink-2">
          <li>
            <b className="text-ink">1.</b> Выделите условие и нажмите Ctrl+C. На экране ничего не меняется, а в буфер попадает почти то же задание —
            с одним изменённым числом. Появится спокойное уведомление «Копирование условия фиксируется».
          </li>
          <li>
            <b className="text-ink">2.</b> Вставьте условие в любой чат-бот и перенесите его ответ сюда. Модель честно решит ту задачу, которую
            ей вставили.
          </li>
          <li>
            <b className="text-ink">3.</b> Или решите по экрану, сделайте скриншот, перепечатайте условие — это обходит двойника, и мы об этом скажем прямо.
          </li>
        </ol>
        <p className="t-small text-ink-3">С ноутбука: поведение копирования на телефонах не проверено. Сессия помечена demo_assisted и в метрики не входит.</p>
      </Panel>
      {error && <Alert tone="danger">{error}</Alert>}
      <div>
        <Button size="lg" onClick={onStart} loading={loading}>
          Начать
        </Button>
      </div>
    </>
  )
}

function TestStep({
  test,
  step,
  answers,
  copiedNos,
  sending,
  error,
  onAnswer,
  onStep,
  onSend,
}: {
  test: SandboxStart
  step: number
  answers: Record<string, string>
  copiedNos: number[]
  sending: boolean
  error: string | null
  onAnswer: (no: number, v: string) => void
  onStep: (i: number) => void
  onSend: () => void
}) {
  const item = test.items[step]
  const last = step === test.items.length - 1
  const value = answers[String(item.no)] ?? ''
  return (
    <>
      <header className="flex flex-col gap-3">
        <p className="t-small text-ink-3">{test.banner_ru}</p>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="t-title text-brand">
            Задание {item.no} из {test.items.length}
          </h1>
          <Segmented
            aria-label="Задание"
            value={String(step)}
            onChange={(v) => onStep(Number(v))}
            options={test.items.map((it, i) => ({ value: String(i), label: `${it.no}` }))}
          />
        </div>
        <p className="font-mono text-[13px] text-ink-3">вариант #{item.variant_code}</p>
      </header>

      <div key={item.no} data-task-block className="flex flex-col gap-4">
        <h2 className="t-name text-ink">{item.prompt}</h2>
        {item.code && (
          <pre translate="no" className="m-0 overflow-x-auto rounded-panel bg-code-bg px-5 py-4 t-code text-white">
            {item.code}
          </pre>
        )}
        {item.table && <Table table={item.table} />}
      </div>

      <div data-answer-field className="max-w-[560px]">
        <Input
          label="Ваш ответ"
          mono
          value={value}
          onChange={(e) => onAnswer(item.no, e.target.value)}
          hint={item.answer_hint ?? 'Можно вставить ответ чат-бота'}
          autoComplete="off"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
        />
      </div>

      {error && <Alert tone="danger">{error}</Alert>}

      <div className="flex flex-wrap items-center gap-3">
        {step > 0 && (
          <Button variant="outline" onClick={() => onStep(step - 1)} disabled={sending}>
            Назад
          </Button>
        )}
        {!last && <Button onClick={() => onStep(step + 1)}>Дальше</Button>}
        {last && (
          <Button onClick={onSend} loading={sending}>
            Отправить ответы
          </Button>
        )}
        <span className="t-small text-ink-3">
          Отвечено {Object.values(answers).filter((a) => a.trim()).length} из {test.items.length}
          {copiedNos.length > 0 ? ` · копировали: ${copiedNos.length}` : ''}
        </span>
      </div>
      {sending && (
        <p className="flex items-center gap-2 t-small text-ink-3">
          <Spinner /> Сверяем ответы…
        </p>
      )}
    </>
  )
}

function Table({ table }: { table: NonNullable<SandboxItem['table']> }) {
  const t = table as { name?: string; columns?: unknown[]; rows?: unknown[][]; tables?: { name?: string; columns?: unknown[]; rows?: unknown[][] }[] }
  const list = t.tables && t.tables.length > 0 ? t.tables : [t]
  return (
    <div className="flex flex-col gap-4 overflow-x-auto" translate="no">
      {list.map((x, k) => (
        <div key={k}>
          <p className="mb-1 font-mono text-[12px] text-ink-3">{x.name}</p>
          <table className="border-collapse font-mono text-[14px]">
            <thead>
              <tr>
                {(x.columns ?? []).map((c, i) => (
                  <th key={i} className="border-b border-hairline px-3 py-1.5 text-left font-medium text-ink-3">
                    {String(c)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(x.rows ?? []).map((r, i) => (
                <tr key={i}>
                  {r.map((v, j) => (
                    <td key={j} className="px-3 py-1">
                      {v === null ? 'NULL' : String(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}

function Result({ result, onAgain }: { result: SandboxResult; onAgain: () => void }) {
  const [open, setOpen] = useState<number | null>(() => result.items.find((i) => i.act)?.no ?? null)
  const caught = useMemo(() => result.items.filter((i) => i.caught).length, [result])
  return (
    <>
      <header className="flex flex-col gap-3">
        <p className="t-small text-ink-3">Результат · сессия demo_assisted, в метрики не входит</p>
        <h1 className="t-display text-brand">{caught > 0 ? 'Вас поймал двойник' : 'Двойник вас не поймал'}</h1>
        <p className="t-lead max-w-[820px] text-ink-2">{result.summary_ru}</p>
      </header>

      <ul className="m-0 flex list-none flex-col gap-3 p-0">
        {result.items.map((d) => (
          <Diagnosis key={d.no} d={d} open={open === d.no} onToggle={() => setOpen(open === d.no ? null : d.no)} />
        ))}
      </ul>

      <Panel variant="tint" radius="lg" className="flex flex-col gap-2">
        <h2 className="t-name text-ink">Чего двойник не ловит</h2>
        <p className="t-body text-ink-2">{result.next_step_ru}</p>
      </Panel>

      <div>
        <Button variant="outline" onClick={onAgain}>
          Попробовать ещё раз с новым набором
        </Button>
      </div>
    </>
  )
}

function Diagnosis({ d, open, onToggle }: { d: SandboxDiagnosis; open: boolean; onToggle: () => void }) {
  return (
    <li className="flex flex-col gap-4 rounded-panel-lg border border-hairline p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1.5">
          <p className="t-small text-ink-3">
            Задание {d.no} · вариант #{d.variant_code}
          </p>
          <p className="t-body-strong text-ink">{d.diagnosis_ru}</p>
          <p className="t-small text-ink-2">
            Ваш ответ: <span className="font-mono text-ink">{d.answer || '—'}</span> · ключ экрана:{' '}
            <span className="font-mono text-ink">{d.key_shown}</span> · ключ копии: <span className="font-mono text-danger-text">{d.key_clipboard}</span>
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn('inline-flex rounded-pill px-2.5 py-0.5 t-small', CHANNEL_TONE[d.channel] ?? CHANNEL_TONE.unknown)}>{d.channel_ru}</span>
          {d.copied && <span className="inline-flex rounded-pill bg-panel px-2.5 py-0.5 t-small text-ink-2">копировали условие</span>}
        </div>
      </div>
      {d.act && (
        <>
          <div>
            <Button variant="text" onClick={onToggle} aria-expanded={open}>
              {open ? 'Скрыть акт сверки' : 'Показать акт сверки'}
            </Button>
          </div>
          {open && (
            <div className="border-t border-hairline pt-6">
              <ActView act={d.act} compact />
            </div>
          )}
        </>
      )}
    </li>
  )
}
