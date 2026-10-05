import { useEffect, useRef, type ReactNode } from 'react'

/**
 * Нижний лист («Да, если…», «Нет», подтверждение согласия). Нативный <dialog>: фокус остаётся внутри,
 * Esc закрывает, фон недоступен. Клик по затемнению — закрыть. На широком экране лист остаётся колонкой 430 px.
 */
export function Sheet({
  open,
  onClose,
  title,
  description,
  children,
}: {
  open: boolean
  onClose: () => void
  title: string
  description?: ReactNode
  children: ReactNode
}) {
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) d.showModal()
    if (!open && d.open) d.close()
  }, [open])

  // прокрутка страницы под листом не нужна
  useEffect(() => {
    if (!open) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prev
    }
  }, [open])

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose()
      }}
      aria-label={title}
      className="fixed inset-x-0 bottom-0 top-auto m-0 mx-auto mt-auto max-h-[92dvh] w-full max-w-[430px] overflow-y-auto rounded-t-hero border-0 bg-white p-0 text-ink backdrop:bg-ink/40"
    >
      {open && (
        <div className="animate-toast-in px-6 pb-[max(2rem,env(safe-area-inset-bottom))] pt-3">
          <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-hairline" aria-hidden="true" />
          <div className="mb-4 flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <h2 className="t-name text-ink">{title}</h2>
              {description && <p className="mt-1 t-small text-ink-2">{description}</p>}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Закрыть"
              className="-mr-2 flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-ink-3 hover:bg-panel hover:text-ink"
            >
              <svg width="16" height="16" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                <path d="M3.5 3.5l7 7M10.5 3.5l-7 7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            </button>
          </div>
          {children}
        </div>
      )}
    </dialog>
  )
}
