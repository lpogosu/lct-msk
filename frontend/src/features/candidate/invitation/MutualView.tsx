import { Illustration, Button, Panel } from '@/ui'
import type { InvitationDetail } from '@/features/candidate/queries'
import { salaryLine } from '@/features/candidate/format'

/** Ссылка для связи по способу: Telegram → t.me, email → mailto, телефон → tel. */
function contactHref(method: string, value: string): string | null {
  const v = value.trim()
  if (method === 'telegram') {
    const handle = v.replace(/^https?:\/\/(t\.me|telegram\.me)\//i, '').replace(/^@/, '')
    return handle ? `https://t.me/${handle}` : null
  }
  if (method === 'email') return `mailto:${v}`
  if (method === 'phone') return `tel:${v.replace(/[^\d+]/g, '')}`
  return null
}

/**
 * «Взаимно» (Figma 29:48): после взаимного «да» — 3D-композиция, обещание компании и её контакт.
 * Контакты компании кандидат видел и раньше (они в её профиле); новое здесь — что компания теперь видит его данные.
 */
export function MutualView({
  d,
  onDispute,
  onRevoke,
}: {
  d: InvitationDetail
  onDispute: () => void
  onRevoke: () => void
}) {
  const t = d.terms
  const name = d.company?.name ?? 'компания'
  const href = contactHref(t.contact_method, t.contact_value)
  const channel = t.contact_method_label ?? t.contact_method
  const due = d.timers.contact_due_text

  return (
    <div className="flex flex-col gap-5">
      <Illustration name="mutual" eager className="mx-auto -mt-2 w-full max-w-[300px]" alt="" />
      <div>
        <p className="t-small text-ink-3">Вы и {name}</p>
        <h1 className="mt-2 font-mono text-[40px] font-medium leading-[48px] tracking-[-1px] text-brand">Взаимно</h1>
      </div>

      {d.contacted ? (
        <p className="t-lead text-ink">Компания отметила, что связалась с вами. Если это не так, сообщите нам.</p>
      ) : (
        <p className="t-lead text-ink">
          Вы приняли условия, компания увидела выбранные вами данные. Она обещала связаться в течение трёх рабочих дней
          {due ? ` — ${due}` : ''}.
        </p>
      )}

      <Panel variant="panel" radius="lg" padding="md" className="flex flex-col gap-1.5">
        <p className="t-body-strong text-ink">{name}</p>
        <p className="t-body text-brand">
          {channel} <span className="font-mono">{t.contact_value}</span>
        </p>
        <p className="t-small text-ink-2">
          {salaryLine(t.salary_from, t.salary_to, t.salary_type_label)}
          {t.format_label ? `, ${t.format_label.toLowerCase()}` : ''}
        </p>
      </Panel>

      <div className="flex flex-col items-center gap-3">
        {href && (
          <Button block size="lg" href={href} target={t.contact_method === 'telegram' ? '_blank' : undefined} rel="noreferrer noopener">
            {t.contact_method === 'telegram' ? 'Написать в Telegram' : t.contact_method === 'email' ? 'Написать на email' : 'Позвонить'}
          </Button>
        )}
        {d.can_dispute ? (
          <Button variant="text" onClick={onDispute}>
            Со мной не связались
          </Button>
        ) : (
          !d.contacted && <p className="text-center t-small text-ink-3">Кнопка «Со мной не связались» появится после срока первого контакта.</p>
        )}
        <Button variant="text" className="text-ink-3" onClick={onRevoke}>
          Отозвать согласие на передачу контактов
        </Button>
      </div>
    </div>
  )
}
