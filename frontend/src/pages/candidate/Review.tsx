import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { qk } from '@/features/candidate/queries'
import { CodeBlock, DataTable } from '@/features/candidate/test/ItemPlayer'
import { Alert, Button, EmptyState, MobileHeader, Panel } from '@/ui'

/** Разбор ошибок после теста: свой экземпляр задания, свой ответ, верный ответ и объяснение заблуждения. */
export default function CandidateReview() {
  const { sessionId = '' } = useParams()
  const q = useQuery({
    queryKey: qk.review(sessionId),
    queryFn: () => unwrap(api.GET('/api/tests/sessions/{session_id}/review', { params: { path: { session_id: sessionId } } })),
    retry: false,
  })
  return (
    <>
      <MobileHeader title="Разбор ошибок" backTo={`/candidate/result/${sessionId}`} />
      <QueryBoundary query={q} errorTitle="Разбор недоступен">
        {(r) => (
          <div className="flex flex-col gap-6">
            <header>
              <h1 className="t-title text-brand">Разбор ошибок</h1>
              <p className="mt-2 t-body text-ink-2">
                Заданий: {r.n_total}, с ошибкой: {r.n_wrong}.
              </p>
            </header>
            <p className="t-small text-ink-3">{r.note}</p>

            {r.items.length === 0 ? (
              <EmptyState
                size="compact"
                title="Ошибок нет"
                description="Все зачётные задания решены верно. Разбирать нечего."
                action={<Button to={`/candidate/result/${sessionId}`}>К результату</Button>}
              />
            ) : (
              <ol className="m-0 flex list-none flex-col gap-6 p-0">
                {r.items.map((it) => (
                  <li key={it.seq}>
                    <article className="flex flex-col gap-3 rounded-panel-lg border border-hairline p-4">
                      <p className="flex flex-wrap items-center justify-between gap-2 t-small text-ink-3">
                        <span>
                          Задание {it.seq} · {it.module.label}
                        </span>
                        <span className="font-mono">вариант {it.variant_code}</span>
                      </p>
                      <h2 className="t-body-strong text-ink">{it.prompt}</h2>
                      {it.code && <CodeBlock code={it.code} />}
                      {it.table && <DataTable table={it.table} />}
                      {(it.options ?? []).length > 0 && (
                        <ul className="flex flex-col gap-1 t-small text-ink-2">
                          {it.options!.map((o) => (
                            <li key={o.id} className="font-mono">
                              {o.id.toUpperCase()}. {o.text}
                            </li>
                          ))}
                        </ul>
                      )}
                      <dl className="flex flex-col gap-2">
                        <div>
                          <dt className="t-small text-ink-3">Ваш ответ</dt>
                          <dd className="whitespace-pre-wrap break-words font-mono text-[14px] text-ink">
                            {it.timed_out
                              ? 'Время вышло'
                              : (it.your_answer_text ?? 'Пропущено')}
                          </dd>
                        </div>
                        <div>
                          <dt className="t-small text-ink-3">Верный ответ</dt>
                          <dd className="whitespace-pre-wrap break-words font-mono text-[14px] text-brand">{it.correct_answer_text}</dd>
                        </div>
                      </dl>
                      {it.explanation && (
                        <Panel variant="panel" radius="md" padding="sm">
                          <p className="whitespace-pre-line t-small text-ink-2">{it.explanation}</p>
                        </Panel>
                      )}
                      {it.misconception && <Alert title="Что выдаёт ваш ответ">{it.misconception}</Alert>}
                    </article>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
      </QueryBoundary>
    </>
  )
}
