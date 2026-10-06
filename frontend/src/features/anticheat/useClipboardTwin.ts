import { useEffect, useMemo, useRef } from 'react'
import { api } from '@/api/client'
import { useToast } from '@/ui'
import {
  composeShown,
  escapeHtml,
  planTwin,
  restoreOnPaste,
  twinSelection,
  wholeTaskSelected,
  type ClipPayload,
  type ShownItem,
} from './clipboardTwin'

/** Задание в том виде, в каком его отдаёт `GET /api/tests/sessions/{id}/next` (нужны только эти поля). */
export interface TwinItem extends ShownItem {
  seq?: number
  clip?: ClipPayload | { [key: string]: unknown } | null
}

export type TwinEventType = 'copy' | 'cut' | 'dragstart' | 'self_paste'
export type TwinEventPayload = Record<string, string | number | boolean | null>

export interface ClipboardTwinOptions {
  /** Сессия теста: события уходят в `POST /api/tests/sessions/{id}/events`. Без неё — только `onEvent`. */
  sessionId?: string
  /** Вызывается на каждое событие (песочница жюри собирает по нему «копировал»). */
  onEvent?: (type: TwinEventType, payload: TwinEventPayload) => void
  /** Нейтральный тост при первом копировании. По умолчанию включён (spec §2.1: предупреждение M4). */
  toast?: boolean
  toastMessage?: string
  /** Ключ localStorage для «тост уже показан» (по умолчанию — по сессии). */
  storageKey?: string
}

export const COPY_TOAST = 'Копирование условия фиксируется'
const TASK_BLOCK = '[data-task-block]'
const ANSWER_FIELD = '[data-answer-field]'

function isClip(v: unknown): v is ClipPayload {
  return (
    !!v &&
    typeof v === 'object' &&
    typeof (v as ClipPayload).clipboard_text === 'string' &&
    typeof (v as ClipPayload).render_id === 'string'
  )
}

function remembered(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === '1'
  } catch {
    return false
  }
}
function remember(key: string): void {
  try {
    window.localStorage.setItem(key, '1')
  } catch {
    /* приватный режим: тост просто покажем ещё раз */
  }
}

function isTextField(t: EventTarget | null): t is HTMLInputElement | HTMLTextAreaElement {
  return t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement
}

/** Вставляет текст в поле на место выделения так, чтобы React увидел изменение. */
function insertIntoField(el: HTMLInputElement | HTMLTextAreaElement, text: string): void {
  el.focus()
  if (document.execCommand('insertText', false, text)) return
  const start = el.selectionStart ?? el.value.length
  const end = el.selectionEnd ?? el.value.length
  el.setRangeText(text, start, end, 'end')
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

/**
 * «Двойник в буфере» на странице теста (Антиподсказчик, spec-antiassist §2.1).
 *
 * Подключение — один вызов в компоненте, который показывает задание:
 *
 *   const { twinActive } = useClipboardTwin(item, { sessionId })
 *
 * Что делает:
 * 1. На `document` вешает `copy` / `cut` / `dragstart`. Если выделение пересекает блок `[data-task-block]` и покрывает
 *    изменённый токен, в буфер уходит `clip.clipboard_text` (соответствующий фрагмент); выделение без токена и любое
 *    выделение вне блока копируются как есть. На экране и в дереве доступности остаётся настоящий текст.
 * 2. При первом копировании в сессии показывает нейтральный тост «Копирование условия фиксируется».
 * 3. В поле `[data-answer-field]` (и любом input/textarea страницы) вставка текста копии заменяется обратно на текст экрана
 *    и шлёт `self_paste`: честный кандидат, копирующий значение в свой черновик, ловушку не задевает.
 * 4. Шлёт события `copy` / `cut` / `dragstart` / `self_paste` с `seq` и `render_id` — по ним сервер собирает контекст акта
 *    сверки. Содержимое чужого буфера не читается.
 *
 * Без `item.clip` (двойника у задания нет) хук ничего не делает. Блоки кода, SQL и таблиц на странице должны иметь
 * `translate="no"`, чтобы переводчик страницы не тронул токен.
 */
export function useClipboardTwin(item: TwinItem | null | undefined, options: ClipboardTwinOptions = {}) {
  const toast = useToast()
  const clip = item && isClip(item.clip) ? item.clip : null
  const shown = useMemo(() => (item && clip ? composeShown(item) : ''), [item, clip])
  const plan = useMemo(() => (clip && shown ? planTwin(shown, clip.clipboard_text) : null), [clip, shown])

  // Живое состояние для обработчиков: подписки на document не пересоздаются при каждой перерисовке
  const live = useRef({ item, clip, shown, plan, options, toast })
  live.current = { item, clip, shown, plan, options, toast }

  const active = clip !== null

  useEffect(() => {
    if (!active) return

    const send = (type: TwinEventType, extra: TwinEventPayload) => {
      const { item: it, clip: c, options: o } = live.current
      if (!c) return
      const payload: TwinEventPayload = { render_id: c.render_id, ...(it?.seq != null ? { seq: it.seq } : {}), ...extra }
      o.onEvent?.(type, payload)
      if (o.sessionId) {
        void api
          .POST('/api/tests/sessions/{session_id}/events', {
            params: { path: { session_id: o.sessionId } },
            body: { events: [{ type, at: new Date().toISOString(), payload }] },
          })
          .catch(() => undefined) // связи нет — событие потеряно, ответ кандидата это не затрагивает
      }
    }

    const warnOnce = (): boolean => {
      const { options: o, toast: t } = live.current
      const key = o.storageKey ?? `vz:twin-warned:${o.sessionId ?? 'local'}`
      if (remembered(key)) return false
      remember(key)
      if (o.toast !== false) t.show({ message: o.toastMessage ?? COPY_TOAST })
      return true
    }

    /** Выделение страницы, пересекающее блок задания, и его текст; иначе null (копируем как есть — M2). */
    const taskSelection = (): string | null => {
      const sel = window.getSelection()
      if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null
      const block = document.querySelector(TASK_BLOCK)
      if (!block || !sel.containsNode(block, true)) return null
      return sel.toString()
    }

    /** Текст для буфера: с подменой токена; null — выделение не покрывает токен. */
    const twinned = (selection: string): string | null => {
      const { plan: p, clip: c, shown: s } = live.current
      if (!c) return null
      if (p) {
        const r = twinSelection(selection, p)
        return r.substituted ? r.text : null
      }
      // Токен найти не удалось (составные тексты разошлись): выделено почти всё задание — копия целиком
      return wholeTaskSelected(selection, s) ? c.clipboard_text : null
    }

    const onCopy = (e: ClipboardEvent) => {
      if (isTextField(e.target)) return // копирование из собственного поля — не из условия
      const selection = taskSelection()
      if (selection === null) return
      const text = twinned(selection)
      const warned = warnOnce()
      if (text !== null && e.clipboardData) {
        e.clipboardData.setData('text/plain', text)
        e.clipboardData.setData('text/html', `<pre>${escapeHtml(text)}</pre>`)
        e.preventDefault()
      }
      send(e.type === 'cut' ? 'cut' : 'copy', { substituted: text !== null, warned })
    }

    const onDragStart = (e: DragEvent) => {
      const selection = taskSelection()
      if (selection === null) return
      const text = twinned(selection)
      if (text !== null && e.dataTransfer) e.dataTransfer.setData('text/plain', text)
      send('dragstart', { substituted: text !== null, warned: warnOnce() })
    }

    const onPaste = (e: ClipboardEvent) => {
      const p = live.current.plan
      const field = e.target
      if (!p || !isTextField(field) || !e.clipboardData) return
      const inAnswer = field.closest(ANSWER_FIELD) !== null
      const pasted = e.clipboardData.getData('text/plain')
      const r = restoreOnPaste(pasted, p)
      if (!r.substituted) return
      e.preventDefault()
      insertIntoField(field, r.text)
      send('self_paste', { field: inAnswer ? 'answer' : 'page' })
    }

    document.addEventListener('copy', onCopy)
    document.addEventListener('cut', onCopy)
    document.addEventListener('dragstart', onDragStart)
    document.addEventListener('paste', onPaste)
    return () => {
      document.removeEventListener('copy', onCopy)
      document.removeEventListener('cut', onCopy)
      document.removeEventListener('dragstart', onDragStart)
      document.removeEventListener('paste', onPaste)
    }
  }, [active])

  return { twinActive: active }
}
