import { useCallback, useEffect, useRef } from 'react'
import { api } from '@/api/client'

type EventType = 'blur' | 'focus' | 'paste' | 'reconnect'
type Payload = Record<string, string | number | boolean | null>

interface TestEvent {
  type: EventType
  at: string
  payload?: Payload
}

const FLUSH_EVERY_MS = 5000
const MAX_BATCH = 50
const MAX_BUFFER = 300

/**
 * Журнал событий страницы теста: уход со вкладки и возврат, вставка, возврат связи. Пачки уходят на
 * `POST /api/tests/sessions/{id}/events` раз в 5 секунд и перед завершением.
 * Бэкенд пишет их только при действующем согласии на журналирование (К3), поэтому лишнего мы не собираем.
 * Вставку в поле ответа не блокируем: честный кандидат вправе вставить свой черновик, это лишь мягкий признак.
 *
 * Копирование условия, вырезание, перетаскивание и вставку в своё поле («двойник в буфере») здесь не ловим:
 * их шлёт `useClipboardTwin` (с `seq` и `render_id`), иначе в журнале были бы дубли без номера задания.
 */
export function useTestEvents(sessionId: string | undefined) {
  const buffer = useRef<TestEvent[]>([])
  const sending = useRef(false)

  const flush = useCallback(async () => {
    if (!sessionId || sending.current || buffer.current.length === 0) return
    sending.current = true
    const batch = buffer.current.splice(0, MAX_BATCH)
    try {
      const res = await api.POST('/api/tests/sessions/{session_id}/events', {
        params: { path: { session_id: sessionId } },
        body: { events: batch },
      })
      if (res.error !== undefined) throw new Error('events')
    } catch {
      // связи нет — вернём пачку в начало очереди (ограничив размер), попробуем в следующий раз
      buffer.current = [...batch, ...buffer.current].slice(-MAX_BUFFER)
    } finally {
      sending.current = false
    }
  }, [sessionId])

  useEffect(() => {
    if (!sessionId) return
    const push = (type: EventType, payload?: Payload) => {
      buffer.current.push({ type, at: new Date().toISOString(), ...(payload ? { payload } : {}) })
      if (buffer.current.length >= 20) void flush()
    }
    const inAnswer = (t: EventTarget | null) => (t instanceof Element ? !!t.closest('[data-answer-field]') : false)

    const onBlur = () => push('blur')
    const onFocus = () => push('focus')
    const onPaste = (e: ClipboardEvent) => push('paste', { field: inAnswer(e.target) ? 'answer' : 'page' })
    const onOnline = () => {
      push('reconnect')
      void flush()
    }
    const onUnload = () => void flush()

    window.addEventListener('blur', onBlur)
    window.addEventListener('focus', onFocus)
    document.addEventListener('paste', onPaste)
    window.addEventListener('online', onOnline)
    window.addEventListener('pagehide', onUnload)
    const timer = window.setInterval(() => void flush(), FLUSH_EVERY_MS)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('blur', onBlur)
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('paste', onPaste)
      window.removeEventListener('online', onOnline)
      window.removeEventListener('pagehide', onUnload)
      void flush()
    }
  }, [sessionId, flush])

  return { flush }
}
