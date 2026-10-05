import { Checkbox } from '@/ui'
import type { FieldOption } from './disclosureUtil'

/** Какие данные открыть компании после «да» (К13 «видимость полей»): флажки и пояснение про способ связи. */
export function DisclosureFields({
  options,
  value,
  onChange,
}: {
  options: FieldOption[]
  value: Record<string, boolean>
  onChange: (next: Record<string, boolean>) => void
}) {
  return (
    <fieldset className="m-0 flex flex-col gap-0.5 border-0 p-0">
      <legend className="mb-1 t-label text-ink">Что увидит компания</legend>
      {options.map((o) => (
        <Checkbox
          key={o.field}
          checked={!!value[o.field]}
          onChange={(e) => onChange({ ...value, [o.field]: e.target.checked })}
          label={<span className="t-body">{o.label}</span>}
        />
      ))}
      <p className="mt-1 t-small text-ink-3">Нужен хотя бы один способ связи: email, телефон или Telegram.</p>
    </fieldset>
  )
}
