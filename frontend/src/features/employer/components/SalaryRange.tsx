import { useId } from 'react'
import { cn } from '@/lib/cn'
import { formatInt } from '@/lib/format'
import { Input, Segmented } from '@/ui'
import { parseMoney } from '../brief'

const THUMB =
  'pointer-events-none absolute inset-x-0 top-0 m-0 h-5 w-full appearance-none bg-transparent focus-visible:outline-none ' +
  '[&::-webkit-slider-runnable-track]:appearance-none [&::-webkit-slider-runnable-track]:bg-transparent ' +
  '[&::-webkit-slider-thumb]:pointer-events-auto [&::-webkit-slider-thumb]:h-[18px] [&::-webkit-slider-thumb]:w-[18px] ' +
  '[&::-webkit-slider-thumb]:cursor-grab [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full ' +
  '[&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-solid [&::-webkit-slider-thumb]:border-brand [&::-webkit-slider-thumb]:bg-white ' +
  '[&::-moz-range-thumb]:pointer-events-auto [&::-moz-range-thumb]:h-[14px] [&::-moz-range-thumb]:w-[14px] ' +
  '[&::-moz-range-thumb]:cursor-grab [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-2 ' +
  '[&::-moz-range-thumb]:border-solid [&::-moz-range-thumb]:border-brand [&::-moz-range-thumb]:bg-white ' +
  'focus-visible:[&::-webkit-slider-thumb]:outline focus-visible:[&::-webkit-slider-thumb]:outline-2 ' +
  'focus-visible:[&::-webkit-slider-thumb]:outline-offset-2 focus-visible:[&::-webkit-slider-thumb]:outline-brand'

/** Двойной ползунок вилки (Figma 40:2): два нативных range поверх одной дорожки — работают с клавиатурой и тачем. */
export function SalaryRangeSlider({
  min,
  max,
  step,
  from,
  to,
  onChange,
  label,
}: {
  min: number
  max: number
  step: number
  from: number
  to: number
  onChange: (from: number, to: number) => void
  label: string
}) {
  const span = Math.max(1, max - min)
  const pos = (v: number) => `${((Math.min(max, Math.max(min, v)) - min) / span) * 100}%`
  const f = Math.min(Math.max(from, min), max)
  const t = Math.min(Math.max(to, min), max)

  return (
    <div className="relative mx-[9px] h-5" role="group" aria-label={label}>
      <span className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-steel" aria-hidden="true" />
      <span
        className="absolute top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-brand"
        style={{ left: pos(f), width: `calc(${pos(t)} - ${pos(f)})` }}
        aria-hidden="true"
      />
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={f}
        aria-label={`${label}: от`}
        aria-valuetext={`от ${formatInt(f)} рублей`}
        onChange={(e) => onChange(Math.min(Number(e.target.value), t), t)}
        className={cn(THUMB, 'z-10')}
        style={{ width: 'calc(100% + 18px)', left: -9 }}
      />
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={t}
        aria-label={`${label}: до`}
        aria-valuetext={`до ${formatInt(t)} рублей`}
        onChange={(e) => onChange(f, Math.max(Number(e.target.value), f))}
        className={cn(THUMB, 'z-20')}
        style={{ width: 'calc(100% + 18px)', left: -9 }}
      />
    </div>
  )
}

/** Поля «от», «до» и переключатель «на руки / до НДФЛ» — вилка обязательна (концепция §3.2, шаг 3). */
export function SalaryFields({
  from,
  to,
  net,
  onFrom,
  onTo,
  onNet,
  error,
  warning,
}: {
  from: number | null
  to: number | null
  net: boolean
  onFrom: (v: number | null) => void
  onTo: (v: number | null) => void
  onNet: (v: boolean) => void
  error?: string | null
  warning?: string | null
}) {
  const msgId = useId()
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
        <Input
          label="Вилка от, ₽ в месяц"
          mono
          inputMode="numeric"
          value={from != null ? formatInt(from) : ''}
          onChange={(e) => onFrom(parseMoney(e.target.value))}
          placeholder="220 000"
          aria-describedby={error || warning ? msgId : undefined}
          aria-invalid={error ? true : undefined}
          wrapperClassName="w-[190px]"
        />
        <Input
          label="до, ₽ в месяц"
          mono
          inputMode="numeric"
          value={to != null ? formatInt(to) : ''}
          onChange={(e) => onTo(parseMoney(e.target.value))}
          placeholder="280 000"
          aria-describedby={error || warning ? msgId : undefined}
          aria-invalid={error ? true : undefined}
          wrapperClassName="w-[190px]"
        />
        <Segmented
          aria-label="Тип вилки"
          options={[
            { value: 'net', label: 'на руки' },
            { value: 'gross', label: 'до НДФЛ' },
          ]}
          value={net ? 'net' : 'gross'}
          onChange={(v) => onNet(v === 'net')}
          className="pb-0.5"
        />
      </div>
      {error ? (
        <p id={msgId} role="alert" className="t-small text-danger-text">
          {error}
        </p>
      ) : warning ? (
        <p id={msgId} role="status" className="t-small text-ink-2">
          {warning}
        </p>
      ) : null}
    </div>
  )
}
