import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ApiException, errorMessage } from '@/api/errors'
import { useChatThread, useSend, type ChatKind } from './hooks'
import { cn } from '@/lib/cn'
import { Alert, Button, Panel, Skeleton } from '@/ui'

function timeLabel(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/**
 * Чат после «Взаимно»: текст, до 1000 знаков, пока контакт открыт. Закрытый чат (контакт завершён или согласие отозвано)
 * показывает историю без поля ввода. Если «взаимного да» ещё нет, сервер отвечает 403 — панель тогда молчит.
 */
export function ChatPanel({ kind, refId, title = 'Чат', className }: { kind: ChatKind; refId: string; title?: string; className?: string }) {
  const q = useChatThread(kind, refId)
  const send = useSend(kind, refId)
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const count = q.data?.messages.length ?? 0

  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [count])

  if (q.isError) {
    const closed = q.error instanceof ApiException && q.error.status === 403
    if (closed && q.error instanceof ApiException && q.error.code === 'chat_unavailable') return null
    return (
      <Panel variant="panel" radius="lg" padding="md" className={className}>
        <p className="t-body-strong text-ink">{title}</p>
        <p className="mt-1 t-small text-ink-3">{errorMessage(q.error)}</p>
      </Panel>
    )
  }

  const d = q.data
  const max = d?.max_len ?? 1000

  async function onSubmit(ev: FormEvent) {
    ev.preventDefault()
    const body = text.trim()
    if (!body) return
    setError(null)
    try {
      await send.mutateAsync(body)
      setText('')
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <Panel variant="panel" radius="lg" padding="md" className={cn('flex flex-col gap-3', className)} aria-label={title}>
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="t-body-strong text-ink">{title}</h2>
        {d && <span className="t-small text-ink-3">{d.title}</span>}
      </div>

      {q.isPending ? (
        <Skeleton className="h-24 w-full" />
      ) : (
        <>
          <ul
            ref={listRef}
            className="m-0 flex max-h-[320px] min-h-[96px] list-none flex-col gap-2 overflow-y-auto p-0"
            aria-live="polite"
            aria-label="Сообщения"
          >
            {d?.messages.length === 0 && (
              <li className="t-small text-ink-3">Сообщений пока нет. Напишите первым — контакт уже открыт.</li>
            )}
            {d?.messages.map((m) => (
              <li key={m.id} className={cn('flex max-w-[85%] flex-col gap-0.5', m.mine ? 'self-end items-end' : 'self-start items-start')}>
                <p
                  className={cn(
                    't-body whitespace-pre-line rounded-panel px-3 py-2 [overflow-wrap:anywhere]',
                    m.mine ? 'bg-brand text-white' : 'bg-white text-ink',
                  )}
                >
                  {m.body}
                </p>
                <span className="t-small text-ink-3">
                  {timeLabel(m.created_at)}
                  {m.mine && m.read ? ' · прочитано' : ''}
                </span>
              </li>
            ))}
          </ul>

          {d && !d.can_send ? (
            <Alert tone="info" title="Чат закрыт">
              {d.readonly_reason}
            </Alert>
          ) : (
            <form onSubmit={onSubmit} className="flex flex-col gap-2" noValidate>
              <label htmlFor={`chat-${refId}`} className="sr-only">
                Сообщение
              </label>
              <textarea
                id={`chat-${refId}`}
                rows={2}
                value={text}
                maxLength={max}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) void onSubmit(e as unknown as FormEvent)
                }}
                placeholder="Сообщение (Ctrl+Enter — отправить)"
                className="block w-full resize-none rounded-panel border border-hairline bg-white px-3 py-2 t-body text-ink placeholder:text-ink-3 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand"
              />
              <div className="flex items-center justify-between gap-3">
                <span className="t-small text-ink-3">
                  {text.length} из {max}
                </span>
                <Button type="submit" size="sm" loading={send.isPending} disabled={!text.trim()}>
                  Отправить
                </Button>
              </div>
              {error && (
                <p role="alert" className="t-small text-danger-text">
                  {error}
                </p>
              )}
            </form>
          )}
        </>
      )}
    </Panel>
  )
}
