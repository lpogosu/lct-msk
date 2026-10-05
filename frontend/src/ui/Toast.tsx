import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react'
import { cn } from '@/lib/cn'

export type ToastTone = 'default' | 'success' | 'danger'

export interface ToastInput {
  title?: string
  message: string
  tone?: ToastTone
  /** Через сколько мс скрыть (0 — не скрывать). По умолчанию 5000, ошибки — 8000 */
  durationMs?: number
}

interface ToastItem extends ToastInput {
  id: number
}

interface ToastApi {
  show: (t: ToastInput) => number
  dismiss: (id: number) => void
  /** Сокращения */
  success: (message: string, title?: string) => number
  error: (message: string, title?: string) => number
}

const ToastContext = createContext<ToastApi | null>(null)

/** Уведомления для действий пользователя: «Приглашение отправлено», «Не удалось сохранить». */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast нужно вызывать внутри <ToastProvider>')
  return ctx
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const seq = useRef(0)
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>())

  const dismiss = useCallback((id: number) => {
    const t = timers.current.get(id)
    if (t) clearTimeout(t)
    timers.current.delete(id)
    setItems((list) => list.filter((x) => x.id !== id))
  }, [])

  const show = useCallback(
    (input: ToastInput) => {
      const id = ++seq.current
      setItems((list) => [...list.slice(-3), { ...input, id }])
      const ms = input.durationMs ?? (input.tone === 'danger' ? 8000 : 5000)
      if (ms > 0) timers.current.set(id, setTimeout(() => dismiss(id), ms))
      return id
    },
    [dismiss],
  )

  const api = useMemo<ToastApi>(
    () => ({
      show,
      dismiss,
      success: (message, title) => show({ message, title, tone: 'success' }),
      error: (message, title) => show({ message, title, tone: 'danger' }),
    }),
    [show, dismiss],
  )

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className="pointer-events-none fixed inset-x-0 bottom-0 z-50 flex flex-col items-center gap-2 p-4 sm:items-end sm:p-6"
        aria-live="polite"
      >
        {items.map((t) => (
          <ToastView key={t.id} toast={t} onClose={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  )
}

function ToastView({ toast, onClose }: { toast: ToastItem; onClose: () => void }) {
  const tone = toast.tone ?? 'default'
  return (
    <div
      role={tone === 'danger' ? 'alert' : 'status'}
      className="pointer-events-auto flex w-full max-w-[380px] animate-toast-in items-start gap-3 rounded-panel border border-hairline bg-white p-4 shadow-float"
    >
      {/* узел-индикатор на коротком отрезке трассы */}
      <span aria-hidden="true" className="mt-2 flex shrink-0 items-center">
        <span className={cn('h-[1.5px] w-3', tone === 'danger' ? 'bg-danger' : 'bg-brand')} />
        <span className={cn('h-2 w-2 rounded-full', tone === 'danger' ? 'bg-danger' : 'bg-brand', tone === 'success' && 'ring-2 ring-heat-2')} />
      </span>
      <div className="min-w-0 flex-1">
        {toast.title && <p className="t-body-strong text-ink">{toast.title}</p>}
        <p className={cn('t-body', toast.title ? 'text-ink-2' : 'text-ink')}>{toast.message}</p>
      </div>
      <button
        type="button"
        onClick={onClose}
        aria-label="Закрыть уведомление"
        className="-mr-1 -mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-ink-3 hover:bg-panel hover:text-ink"
      >
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
          <path d="M3.5 3.5l7 7M10.5 3.5l-7 7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  )
}
