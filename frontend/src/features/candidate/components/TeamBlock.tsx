import { SectionTitle } from './Bits'

interface Team {
  size?: number | null
  role?: string | null
  stack_context?: string | null
  about?: string | null
}

/** «О команде» из брифа работодателя (E-02): в приглашении и вакансии. На Score не влияет. */
export function TeamBlock({ team }: { team?: Record<string, unknown> | null }) {
  const t = (team ?? {}) as Team
  const rows: [string, string][] = [
    ['Размер команды', t.size ? `${t.size} чел.` : ''],
    ['Ваша роль', t.role ?? ''],
    ['Стек проекта', t.stack_context ?? ''],
  ].filter((r): r is [string, string] => Boolean(r[1]))
  if (rows.length === 0 && !t.about) return null
  return (
    <section aria-labelledby="team-h">
      <SectionTitle as="h3">
        <span id="team-h">О команде</span>
      </SectionTitle>
      <dl className="m-0 mt-2 flex flex-col gap-1">
        {rows.map(([k, v]) => (
          <div key={k} className="flex flex-wrap gap-x-2 t-body">
            <dt className="text-ink-3">{k}:</dt>
            <dd className="m-0 text-ink">{v}</dd>
          </div>
        ))}
      </dl>
      {t.about && <p className="mt-2 whitespace-pre-line t-body text-ink-2">{t.about}</p>}
    </section>
  )
}
