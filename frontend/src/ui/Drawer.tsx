import { useEffect, useId, useRef, type CSSProperties, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '@/lib/cn'

export interface DrawerProps {
  open: boolean
  onClose: () => void
  /** Заголовок диалога (читается скринридером) */
  title: string
  /** Маленькая строка над заголовком: «Приглашение · К-4821» */
  eyebrow?: string
  children: ReactNode
  /** Закреплённая нижняя панель действий */
  footer?: ReactNode
  /** Ширина панели на десктопе, px */
  width?: number
  className?: string
}

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'

/**
 * Боковая панель-диалог (Figma Р7 «Приглашение», панель справа). Модальная: фокус заперт внутри,
 * Esc и клик по затемнению закрывают, после закрытия фокус возвращается на кнопку, открывшую панель.
 * На узких экранах занимает всю ширину.
 */
export function Drawer({ open, onClose, title, eyebrow, children, footer, width = 560, className }: DrawerProps) {
  const titleId = useId()
  const panelRef = useRef<HTMLDivElement>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    if (!open) return
    const opener = document.activeElement as HTMLElement | null
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const panel = panelRef.current
    // фокус на первое поле формы (не на «Закрыть»), иначе на сам диалог
    const first = panel?.querySelector<HTMLElement>('[data-autofocus],input,textarea,select')
    ;(first ?? panel)?.focus({ preventScroll: true })

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onCloseRef.current()
        return
      }
      if (e.key !== 'Tab' || !panel) return
      const nodes = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((n) => n.offsetParent !== null)
      if (nodes.length === 0) {
        e.preventDefault()
        return
      }
      const firstNode = nodes[0]
      const lastNode = nodes[nodes.length - 1]
      if (e.shiftKey && document.activeElement === firstNode) {
        e.preventDefault()
        lastNode.focus()
      } else if (!e.shiftKey && document.activeElement === lastNode) {
        e.preventDefault()
        firstNode.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
      opener?.focus?.({ preventScroll: true })
    }
  }, [open])

  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink/35" onClick={onClose} aria-hidden="true" />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        style={{ '--dw': `${width}px` } as CSSProperties}
        className={cn(
          'relative flex h-full w-full max-w-full flex-col bg-white shadow-float outline-none sm:w-[min(var(--dw),100vw)]',
          className,
        )}
      >
        <header className="flex items-start justify-between gap-4 border-b border-hairline px-5 py-4 sm:px-7 sm:py-5">
          <div className="min-w-0">
            {eyebrow && <p className="t-small text-ink-3">{eyebrow}</p>}
            <h2 id={titleId} className="t-title text-brand">
              {title}
            </h2>
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
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-7">{children}</div>
        {footer && <footer className="border-t border-hairline bg-white px-5 py-4 sm:px-7">{footer}</footer>}
      </div>
    </div>,
    document.body,
  )
}
