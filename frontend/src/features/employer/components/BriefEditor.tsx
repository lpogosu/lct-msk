import { useId, type ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { segmentText, type Mark } from '../brief'

/**
 * Поле брифа (Figma 40:32): серая панель, текст крупным шрифтом, распознанные фразы подчёркнуты фиолетовым.
 * Подсветка — слой под прозрачным <textarea> с тем же шрифтом и переносами: он же задаёт высоту поля,
 * поэтому поле растёт вместе с текстом, а курсор и выделение остаются нативными.
 * Наведение на строку «Как мы поняли» (activeKey) закрашивает соответствующие фразы.
 */
export function BriefEditor({
  value,
  onChange,
  marks,
  activeKey,
  maxLength,
  footerLeft,
  footerRight,
  error,
  placeholder,
  autoFocus,
}: {
  value: string
  onChange: (v: string) => void
  marks: Mark[]
  activeKey: string | null
  maxLength: number
  footerLeft?: ReactNode
  footerRight?: ReactNode
  error?: ReactNode
  placeholder?: string
  autoFocus?: boolean
}) {
  const id = useId()
  const errId = `${id}-err`
  const segments = segmentText(value, marks, activeKey)

  return (
    <div className="flex flex-col gap-2">
      <div
        className={cn(
          'flex flex-col gap-3 rounded-panel-lg bg-panel px-5 pb-4 pt-5 sm:px-6 sm:pb-[18px] sm:pt-[22px]',
          'focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-brand',
          error && 'outline outline-[1.5px] outline-danger',
        )}
      >
        <div className="relative min-h-[104px]">
          {/* слой подсветки: текст прозрачный, видны только подчёркивания и заливка */}
          <div
            aria-hidden="true"
            className="pointer-events-none min-h-[104px] whitespace-pre-wrap break-words t-lead text-transparent"
          >
            {segments.map((s, i) =>
              s.mark ? (
                <span
                  key={i}
                  className={cn(
                    'underline decoration-brand decoration-[1.5px] underline-offset-[5px] transition-colors',
                    s.active && 'rounded-[3px] bg-heat-2',
                  )}
                >
                  {s.text}
                </span>
              ) : (
                <span key={i}>{s.text}</span>
              ),
            )}
            {'​'}
          </div>
          <textarea
            id={id}
            value={value}
            maxLength={maxLength}
            autoFocus={autoFocus}
            spellCheck
            placeholder={placeholder}
            aria-label="Описание потребности"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errId : undefined}
            onChange={(e) => onChange(e.target.value)}
            className="absolute inset-0 block h-full w-full resize-none overflow-hidden whitespace-pre-wrap break-words bg-transparent t-lead text-ink placeholder:text-ink-3 focus-visible:outline-none"
          />
        </div>
        {(footerLeft || footerRight) && (
          <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1 t-small text-ink-3">
            <span>{footerLeft}</span>
            <span>{footerRight}</span>
          </div>
        )}
      </div>
      {error && (
        <p id={errId} role="alert" className="t-small text-danger-text">
          {error}
        </p>
      )}
    </div>
  )
}
