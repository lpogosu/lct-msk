import { useState, type FormEvent } from 'react'
import { errorMessage } from '@/api/errors'
import { formatInt } from '@/lib/format'
import { Alert, Button, EmptyState, Input, Panel, Segmented, TextArea } from '@/ui'
import { useRankPlatform } from '../api'
import { CandidateTable } from './CandidateTable'
import { formatsText, salaryText } from '../format'
import { Pill } from './common'

const EXAMPLE =
  'Ищем Middle Python-разработчика в команду сервиса доставки. Обязательно: Python, Django, PostgreSQL. Будет плюсом: Celery, Redis, Docker. Гибрид, офис в Москве.'
const TOPS = ['10', '20', '50'] as const

function toInt(v: string): number | null {
  const n = Number(v.replace(/[\s\u00a0]/g, '').replace(',', '.'))
  return v.trim() && Number.isFinite(n) && n > 0 ? Math.round(n) : null
}

/** «Своя вакансия против банка платформы»: текст и вилка → топ-N слепого банка тем же движком, что у работодателя. */
export function VacancyTab() {
  const rank = useRankPlatform()
  const [text, setText] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [net, setNet] = useState<'net' | 'gross'>('net')
  const [top, setTop] = useState<(typeof TOPS)[number]>('10')
  const [formError, setFormError] = useState<string | null>(null)

  function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    if (!text.trim()) return setFormError('Вставьте текст вакансии.')
    const lo = toInt(from)
    const hi = toInt(to)
    if ((lo == null) !== (hi == null)) return setFormError('Укажите обе границы вилки или оставьте обе пустыми, если вилка есть в тексте.')
    if (lo != null && hi != null && lo > hi) return setFormError('«До» меньше «от».')
    rank.mutate({
      text: text.trim(),
      salary_from: lo,
      salary_to: hi,
      salary_net: lo != null ? net === 'net' : null,
      top: Number(top),
    })
  }

  const r = rank.data
  return (
    <div className="flex flex-col gap-8">
      <Panel variant="panel" radius="xl" padding="lg">
        <form className="flex flex-col gap-5" onSubmit={submit} noValidate>
          <TextArea
            label="Текст вакансии"
            hint="Как написали бы рекрутеру: стек, уровень, формат. Мы разберём его так же, как бриф работодателя."
            rows={5}
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={5000}
            placeholder={EXAMPLE}
          />
          <div className="grid gap-4 sm:grid-cols-[1fr_1fr_auto]">
            <Input label="Вилка от, ₽ в месяц" inputMode="numeric" value={from} onChange={(e) => setFrom(e.target.value)} placeholder="220 000" />
            <Input label="до, ₽ в месяц" inputMode="numeric" value={to} onChange={(e) => setTo(e.target.value)} placeholder="280 000" />
            <div className="flex flex-col gap-1.5">
              <span className="t-label text-ink">Сумма</span>
              <Segmented
                aria-label="На руки или до вычета НДФЛ"
                options={[
                  { value: 'net', label: 'на руки' },
                  { value: 'gross', label: 'до НДФЛ' },
                ]}
                value={net}
                onChange={setNet}
              />
            </div>
          </div>
          <p className="t-small text-ink-3">Вилка обязательна: в полях или в тексте («220–280 тыс. на руки»).</p>
          <div className="flex flex-wrap items-center gap-4">
            <Button type="submit" loading={rank.isPending}>
              Подобрать из банка платформы
            </Button>
            <div className="flex items-center gap-2">
              <span className="t-small text-ink-2">Показать</span>
              <Segmented aria-label="Сколько кандидатов показать" options={TOPS.map((t) => ({ value: t, label: `топ-${t}` }))} value={top} onChange={setTop} />
            </div>
            {!text && (
              <Button variant="text" onClick={() => setText(EXAMPLE)}>
                Вставить пример
              </Button>
            )}
          </div>
          {formError && <Alert tone="danger">{formError}</Alert>}
          {rank.isError && (
            <Alert tone="danger" title="Не получилось подобрать">
              {errorMessage(rank.error)}
            </Alert>
          )}
        </form>
      </Panel>

      {r && (
        <section aria-labelledby="vac-res" className="flex flex-col gap-5">
          <h2 id="vac-res" className="t-title text-ink">
            Как разобрана вакансия
          </h2>
          <Panel variant="outline" radius="lg" padding="md">
            <dl className="m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1.5 t-small">
              <dt className="text-ink-3">Категория</dt>
              <dd className="m-0 text-ink">{r.brief.category}</dd>
              <dt className="text-ink-3">Обязательно</dt>
              <dd className="m-0 text-ink">{r.brief.must_names.join(', ') || '—'}</dd>
              <dt className="text-ink-3">Желательно</dt>
              <dd className="m-0 text-ink">{r.brief.nice_names.join(', ') || '—'}</dd>
              <dt className="text-ink-3">Вилка</dt>
              <dd className="m-0 text-ink">{salaryText(r.brief.salary_from, r.brief.salary_to, r.brief.salary_net)}</dd>
              <dt className="text-ink-3">Формат</dt>
              <dd className="m-0 text-ink">{formatsText(r.brief.formats)}</dd>
              <dt className="text-ink-3">Где ищем</dt>
              <dd className="m-0 flex flex-wrap gap-1.5">
                {r.categories.length === 0 && <span className="text-ink-3">рекомендованных категорий нет</span>}
                {r.categories.map((c) => (
                  <Pill key={`${c.spec}-${c.grade}`}>
                    {c.name} · {formatInt(c.n)}
                  </Pill>
                ))}
              </dd>
            </dl>
          </Panel>
          <p className="t-small text-ink-2">
            Банк платформы: {formatInt(r.pool_size)} кандидатов с подтверждённой категорией; в рекомендованных категориях —{' '}
            {formatInt(r.total)}. Выдача слепая: вместо имени — псевдоним, контактов нет.
          </p>
          {r.items.length === 0 ? (
            <Panel variant="panel" radius="lg">
              <EmptyState
                title="В банке платформы нет подходящих кандидатов"
                description="В рекомендованных категориях пока никого. На демо-стенде банк наполняет make seed; можно уточнить специализацию и грейд в тексте вакансии."
                size="compact"
              />
            </Panel>
          ) : (
            <CandidateTable items={r.items} showRel={false} caption="Топ кандидатов банка платформы по вакансии" />
          )}
        </section>
      )}
    </div>
  )
}
