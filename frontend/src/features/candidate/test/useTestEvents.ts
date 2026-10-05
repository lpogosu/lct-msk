import { useCallback, useEffect, useRef, type MutableRefObject } from 'react'
import { api } from '@/api/client'

type EventType = 'blur' | 'focus' | 'paste' | 'copy' | 'cut' | 'dragstart' | 'reconnect'
type Payload = Record<string, string | number | boolean | null>

interface TestEvent {
  type: EventType
  at: string
  payload?: Payload
}

/** Данные «двойника в буфере» от защиты от подсказчика (spec-antiassist §2.1). Сейчас бэкенд отдаёт null. */
export interface ClipData {
  text?: unknown
  clip_text?: unknown
  render_id?: unknown
}

const FLUSH_EVERY_MS = 5000
const MAX_BATCH = 50
const MAX_BUFFER = 300

/**
 * Журнал событий страницы теста: уход со вкладки и возврат, вставка, копирование, вырезание, перетаскивание,
 * возврат связи. Пачки уходят на `POST /api/tests/sessions/{id}/events` раз в 5 секунд и перед завершением.
 * Бэкенд пишет их только при действующем согласии на журналирование (К3), поэтому лишнего мы не собираем.
 * Вставку в поле ответа не блокируем: честный кандидат вправе вставить свой черновик, это лишь мягкий признак.
 *
 * `clipRef` — актуальное задание: если защита выдала «двойника» (`clip`), то при копировании условия в буфер
 * кладётся его вариант текста, а в событии `copy` уходит `render_id`.
 */
export function useTestEvents(sessionId: string | undefined, clipRef: MutableRefObject<ClipData | null>) {
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
    const inTask = (): boolean => {
      const sel = window.getSelection()
      if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return false
      const node = sel.anchorNode instanceof Element ? sel.anchorNode : sel.anchorNode?.parentElement
      return !!node?.closest('[data-task-block]')
    }

    const onBlur = () => push('blur')
    const onFocus = () => push('focus')
    const onPaste = (e: ClipboardEvent) => push('paste', { field: inAnswer(e.target) ? 'answer' : 'page' })
    const onCut = (e: Event) => push('cut', { field: inAnswer(e.target) ? 'answer' : 'page' })
    const onDrag = () => push('dragstart')
    const onCopy = (e: ClipboardEvent) => {
      const clip = clipRef.current
      const text = clip && (typeof clip.clip_text === 'string' ? clip.clip_text : typeof clip.text === 'string' ? clip.text : null)
      if (text && inTask() && e.clipboardData) {
        e.clipboardData.setData('text/plain', text)
        e.preventDefault()
      }
      const render = clip && (typeof clip.render_id === 'string' || typeof clip.render_id === 'number') ? clip.render_id : null
      push('copy', { field: inAnswer(e.target) ? 'answer' : inTask() ? 'task' : 'page', ...(render != null ? { render_id: render } : {}) })
    }
    const onOnline = () => {
      push('reconnect')
      void flush()
    }
    const onUnload = () => void flush()

    window.addEventListener('blur', onBlur)
    window.addEventListener('focus', onFocus)
    document.addEventListener('paste', onPaste)
    document.addEventListener('cut', onCut)
    document.addEventListener('copy', onCopy)
    document.addEventListener('dragstart', onDrag)
    window.addEventListener('online', onOnline)
    window.addEventListener('pagehide', onUnload)
    const timer = window.setInterval(() => void flush(), FLUSH_EVERY_MS)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('blur', onBlur)
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('paste', onPaste)
      document.removeEventListener('cut', onCut)
      document.removeEventListener('copy', onCopy)
      document.removeEventListener('dragstart', onDrag)
      window.removeEventListener('online', onOnline)
      window.removeEventListener('pagehide', onUnload)
      void flush()
    }
  }, [sessionId, flush, clipRef])

  return { flush }
}
