import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import type { Schemas } from '@/api/types'
import { BlindCard, type BlindCardData } from '@/features/candidate/components/BlindCard'
import { Chip, SectionTitle } from '@/features/candidate/components/Bits'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { CONTACT_FIELDS } from '@/features/candidate/components/disclosureUtil'
import { useDebounced } from '@/features/candidate/hooks'
import { qk, usePrivacy, type PrivacyOut } from '@/features/candidate/queries'
import { cn } from '@/lib/cn'
import { formatInt } from '@/lib/format'
import { Alert, Button, Checkbox, Input, MobileHeader, Panel, Segmented, Skeleton, useToast, fieldBorderClasses } from '@/ui'

type Update = Schemas['PrivacyUpdateIn']
type Format = 'office' | 'hybrid' | 'remote'

interface Draft {
  search_mode: 'active' | 'open' | 'hidden'
  salary_min: string
  salary_type: 'net' | 'gross'
  salary_visibility: 'signal' | 'exact'
  weekly_invite_limit: number | null
  formats: Format[]
  region_code: string
  city: string
  relocation: boolean
  available_from: string
  blocked: { id: string; name: string }[]
  field_visibility: Record<string, boolean>
}

function draftFrom(p: PrivacyOut): Draft {
  return {
    search_mode: p.search_mode as Draft['search_mode'],
    salary_min: p.salary_min != null ? String(p.salary_min) : '',
    salary_type: p.salary_type as Draft['salary_type'],
    salary_visibility: p.salary_visibility as Draft['salary_visibility'],
    weekly_invite_limit: p.weekly_invite_limit ?? null,
    formats: (p.formats ?? []) as Format[],
    region_code: p.region?.code ?? '',
    city: p.city?.name ?? '',
    relocation: p.relocation,
    available_from: p.available_from ?? '',
    blocked: (p.blocked_companies ?? []).map((b) => ({ id: b.id, name: b.name })),
    field_visibility: Object.fromEntries((p.field_visibility ?? []).map((f) => [f.field, f.allowed])),
  }
}

function toUpdate(d: Draft): Update {
  const min = d.salary_min.trim() === '' ? null : Math.max(0, Math.round(Number(d.salary_min) / 1000) * 1000)
  return {
    search_mode: d.search_mode,
    salary_min: min != null && Number.isFinite(min) ? min : null,
    salary_type: d.salary_type,
    salary_visibility: d.salary_visibility,
    weekly_invite_limit: d.weekly_invite_limit,
    formats: d.formats,
    ...(d.region_code ? { region_code: d.region_code } : {}),
    ...(d.city.trim() ? { city: d.city.trim() } : {}),
    relocation: d.relocation,
    ...(d.available_from ? { available_from: d.available_from } : {}),
    blocked_company_ids: d.blocked.map((b) => b.id),
    field_visibility: d.field_visibility,
  }
}

/**
 * К13 «Приватность и поиск»: режим поиска, минимум зарплаты и его видимость, лимит приглашений, формат и регион,
 * блок-лист, поля после «да» и живое превью «так вас видят» — оно строится по несохранённым настройкам.
 */
export default function CandidatePrivacy() {
  const q = usePrivacy()
  return (
    <>
      <MobileHeader title="Приватность и поиск" backTo="/candidate/profile" />
      <QueryBoundary query={q}>{(p) => <PrivacyForm key={JSON.stringify(p)} initial={p} />}</QueryBoundary>
    </>
  )
}

function PrivacyForm({ initial }: { initial: PrivacyOut }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [draft, setDraft] = useState<Draft>(() => draftFrom(initial))
  const patch = (p: Partial<Draft>) => setDraft((d) => ({ ...d, ...p }))
  const update = useMemo(() => toUpdate(draft), [draft])
  const initialUpdate = useMemo(() => toUpdate(draftFrom(initial)), [initial])
  const dirty = JSON.stringify(update) !== JSON.stringify(initialUpdate)

  const contactOk = CONTACT_FIELDS.some((k) => draft.field_visibility[k])

  const save = useMutation({
    mutationFn: () => unwrap(api.PUT('/api/candidates/me/privacy', { body: update })),
    onSuccess: (data) => {
      qc.setQueryData(qk.privacy, data)
      void qc.invalidateQueries({ queryKey: ['candidate', 'preview'] })
      void qc.invalidateQueries({ queryKey: qk.profile })
      toast.success('Настройки сохранены.')
    },
  })

  const opts = initial.options

  return (
    <div className="flex flex-col gap-9">
      <header>
        <h1 className="t-title text-brand">Приватность и поиск</h1>
        <p className="mt-2 t-body text-ink-2">Вы решаете, кто и что о вас видит. До вашего «да» компания не видит ни имени, ни контактов.</p>
      </header>

      <BankStatus bank={initial.bank} />

      <section className="flex flex-col gap-3" aria-labelledby="mode-h">
        <SectionTitle>
          <span id="mode-h">Режим поиска</span>
        </SectionTitle>
        <div role="radiogroup" aria-labelledby="mode-h" className="flex flex-col gap-2">
          {opts.search_modes.map((m) => (
            <button
              key={m.code}
              type="button"
              role="radio"
              aria-checked={draft.search_mode === m.code}
              onClick={() => patch({ search_mode: m.code as Draft['search_mode'] })}
              className={cn(
                'rounded-panel-lg border px-4 py-3 text-left t-label transition-colors',
                draft.search_mode === m.code ? 'border-brand bg-tint text-ink' : 'border-hairline bg-white text-ink hover:border-steel',
              )}
            >
              {m.name}
            </button>
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-4" aria-labelledby="sal-h">
        <SectionTitle>
          <span id="sal-h">Минимум зарплаты</span>
        </SectionTitle>
        <Input
          label="Минимум, ₽ в месяц"
          type="number"
          inputMode="numeric"
          min={0}
          step={1000}
          value={draft.salary_min}
          onChange={(e) => patch({ salary_min: e.target.value })}
          placeholder="Например, 250000"
          hint="С шагом 1 000 ₽. Можно не указывать."
          mono
        />
        <Segmented
          aria-label="На руки или до НДФЛ"
          options={[
            { value: 'net', label: 'На руки' },
            { value: 'gross', label: 'До НДФЛ' },
          ]}
          value={draft.salary_type}
          onChange={(v) => patch({ salary_type: v })}
        />
        <div className="flex flex-col gap-2">
          <p className="t-label text-ink-2">Что увидит работодатель</p>
          <Segmented
            aria-label="Видимость минимума"
            options={[
              { value: 'signal', label: 'Только значок' },
              { value: 'exact', label: 'Число' },
            ]}
            value={draft.salary_visibility}
            onChange={(v) => patch({ salary_visibility: v })}
          />
          <p className="t-small text-ink-3">
            «Только значок» — работодатель видит «в вилке ✓» или «выше вилки», но не сумму. Так рекомендуем: компании не подгоняют вилку под ваш минимум.
          </p>
        </div>
      </section>

      <section className="flex flex-col gap-3" aria-labelledby="lim-h">
        <SectionTitle>
          <span id="lim-h">Сколько новых приглашений в неделю</span>
        </SectionTitle>
        <Segmented
          aria-label="Лимит приглашений в неделю"
          options={opts.weekly_limits.map((n) => ({ value: n == null ? 'none' : String(n), label: n == null ? '∞' : String(n) }))}
          value={draft.weekly_invite_limit == null ? 'none' : String(draft.weekly_invite_limit)}
          onChange={(v) => patch({ weekly_invite_limit: v === 'none' ? null : Number(v) })}
        />
        <p className="t-small text-ink-3">По умолчанию 5. Сверх лимита компании не смогут вам написать до следующей недели.</p>
      </section>

      <section className="flex flex-col gap-4" aria-labelledby="fmt-h">
        <SectionTitle>
          <span id="fmt-h">Формат и место</span>
        </SectionTitle>
        <div className="flex flex-wrap gap-2.5" role="group" aria-label="Подходящие форматы">
          {opts.formats.map((f) => {
            const on = draft.formats.includes(f.code as Format)
            return (
              <button
                key={f.code}
                type="button"
                role="checkbox"
                aria-checked={on}
                onClick={() =>
                  patch({ formats: on ? draft.formats.filter((x) => x !== f.code) : [...draft.formats, f.code as Format] })
                }
                className={cn(
                  'rounded-pill border px-[18px] py-2.5 t-label transition-colors',
                  on ? 'border-brand bg-brand text-white' : 'border-steel bg-white text-ink hover:border-brand hover:text-brand',
                )}
              >
                {f.name}
              </button>
            )
          })}
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor="region" className="t-label text-ink-2">
            Регион (виден компаниям до «да»)
          </label>
          <select
            id="region"
            value={draft.region_code}
            onChange={(e) => patch({ region_code: e.target.value })}
            className={cn(fieldBorderClasses(false), 'h-12 px-3.5 t-body')}
          >
            <option value="">Не указан</option>
            {opts.regions.map((r) => (
              <option key={r.code} value={r.code}>
                {r.name}
              </option>
            ))}
          </select>
        </div>
        <Input label="Город (виден только после «да»)" value={draft.city} onChange={(e) => patch({ city: e.target.value })} placeholder="Например, Москва" />
        <Checkbox
          checked={draft.relocation}
          onChange={(e) => patch({ relocation: e.target.checked })}
          label={<span className="t-body">Готов к переезду</span>}
        />
        <Input
          label="Готов выйти с"
          type="date"
          value={draft.available_from}
          onChange={(e) => patch({ available_from: e.target.value })}
        />
      </section>

      <Blocklist blocked={draft.blocked} onChange={(blocked) => patch({ blocked })} />

      <section className="flex flex-col gap-3" aria-labelledby="vis-h">
        <SectionTitle>
          <span id="vis-h">Что увидит компания после «да»</span>
        </SectionTitle>
        <div className="flex flex-col gap-0.5">
          {(initial.field_visibility ?? []).map((f) => (
            <Checkbox
              key={f.field}
              checked={!!draft.field_visibility[f.field]}
              onChange={(e) => patch({ field_visibility: { ...draft.field_visibility, [f.field]: e.target.checked } })}
              label={<span className="t-body">{f.label}</span>}
            />
          ))}
        </div>
        {!contactOk && <Alert tone="danger">Оставьте хотя бы один способ связи: email, телефон или Telegram.</Alert>}
        <p className="t-small text-ink-3">Раскрытие всегда отдельное согласие на конкретную компанию. Его можно отозвать в журнале.</p>
      </section>

      <LivePreview update={update} />

      <div className="sticky bottom-[72px] z-20 -mx-6 flex flex-col gap-2 border-t border-hairline bg-white/95 px-6 py-3 backdrop-blur">
        {save.error ? <Alert tone="danger">{errorMessage(save.error)}</Alert> : null}
        <Button block size="lg" disabled={!dirty || !contactOk} loading={save.isPending} onClick={() => save.mutate()}>
          {dirty ? 'Сохранить настройки' : 'Настройки сохранены'}
        </Button>
        <Link to="/candidate/consents" className="self-center t-small text-ink-3 underline-offset-4 hover:underline">
          Журнал согласий
        </Link>
      </div>
    </div>
  )
}

function BankStatus({ bank }: { bank: PrivacyOut['bank'] }) {
  return (
    <Panel variant={bank.visible ? 'tint' : 'panel'} radius="lg" padding="sm" className="flex items-start gap-3">
      <span aria-hidden="true" className={cn('mt-[7px] h-2 w-2 shrink-0 rounded-full', bank.visible ? 'bg-brand' : 'bg-steel')} />
      <div className="min-w-0">
        <p className="t-label text-ink">{bank.visible ? 'Работодатели вас видят' : 'Работодатели вас пока не видят'}</p>
        {(bank.reasons ?? []).map((r) => (
          <p key={r} className="mt-0.5 t-small text-ink-2">
            {r}
          </p>
        ))}
      </div>
    </Panel>
  )
}

// --- блок-лист ----------------------------------------------------------------------------------------------------------

function Blocklist({ blocked, onChange }: { blocked: { id: string; name: string }[]; onChange: (b: { id: string; name: string }[]) => void }) {
  const [term, setTerm] = useState('')
  const debounced = useDebounced(term.trim(), 350)
  const search = useQuery({
    queryKey: ['candidate', 'blocklist-search', debounced],
    queryFn: () => unwrap(api.GET('/api/candidates/me/blocklist/search', { params: { query: { q: debounced } } })),
    enabled: debounced.length >= 2,
    staleTime: 30_000,
  })
  const ids = new Set(blocked.map((b) => b.id))
  return (
    <section className="flex flex-col gap-3" aria-labelledby="blk-h">
      <SectionTitle>
        <span id="blk-h">Блок-лист компаний</span>
      </SectionTitle>
      <p className="t-small text-ink-3">Эти компании не видят вас в подборках и не могут пригласить.</p>
      {blocked.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {blocked.map((b) => (
            <li key={b.id}>
              <button
                type="button"
                onClick={() => onChange(blocked.filter((x) => x.id !== b.id))}
                aria-label={`Убрать «${b.name}» из блок-листа`}
                className="inline-flex items-center gap-1.5 rounded-pill bg-panel px-3 py-1.5 t-small text-ink hover:bg-hairline"
              >
                {b.name}
                <span aria-hidden="true" className="text-ink-3">
                  ×
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <Input aria-label="Найти компанию" value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Название компании, от 2 букв" />
      {search.isFetching && <Skeleton className="h-10 w-full" />}
      {search.data && search.data.length === 0 && debounced.length >= 2 && <p className="t-small text-ink-3">Такой компании нет.</p>}
      {search.data && search.data.length > 0 && (
        <ul className="flex flex-col">
          {search.data.map((c) => (
            <li key={c.id} className="flex items-center justify-between gap-3 border-b border-hairline py-2">
              <span className="min-w-0 t-body text-ink">
                {c.name}
                {c.field && <span className="block t-small text-ink-3">{c.field}</span>}
              </span>
              {ids.has(c.id) ? (
                <Chip tone="panel">в блок-листе</Chip>
              ) : (
                <Button size="sm" variant="outline" onClick={() => onChange([...blocked, { id: c.id, name: c.name }])}>
                  Скрыться
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

// --- живое превью ---------------------------------------------------------------------------------------------------------

type Blind = Record<string, unknown>

const asArray = (v: unknown): unknown[] => (Array.isArray(v) ? v : [])
const asRecord = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {})
const asString = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)

function blindToCard(b: Blind): BlindCardData {
  const category = asRecord(b.category)
  const test = asRecord(b.test)
  const interval = asArray(test.interval).map(Number)
  const salary = asRecord(b.salary_min)
  const fresh = asRecord(b.freshness)
  return {
    pseudonym: asString(b.pseudonym) ?? 'К-····',
    category: asString(category.name),
    badge: [asString(test.band) ? `Тест: ${asString(test.band)}` : null, asString(b.test_date) ? `подтверждён ${asString(b.test_date)}` : null]
      .filter(Boolean)
      .join(' · ') || null,
    interval: interval.length === 2 && interval.every(Number.isFinite) ? [interval[0], interval[1]] : null,
    modules: asArray(b.skills).map((s) => ({ name: String(asRecord(s).name ?? ''), symbol: asString(asRecord(s).symbol) ?? undefined })),
    declared: asArray(b.declared_skills).map(String),
    freshness: asString(fresh.band) ? `актуальность: ${fresh.band}` : null,
    fsp: asArray(b.fsp).map((f) => {
      const r = asRecord(f)
      return [r.discipline, r.level, r.year, r.place_band].filter(Boolean).join(' · ')
    }),
    region: asString(b.region),
    salarySignal: asString(salary.signal) ?? (typeof salary.value === 'number' ? `минимум ${formatInt(salary.value)} ₽` : null),
    hidden: 'Скрыто до «да»: имя, контакты, город, текущий работодатель, вуз, точный балл.',
  }
}

const DISCLOSED_ORDER: [string, string][] = [
  ['full_name', 'ФИО'],
  ['email', 'Email'],
  ['phone', 'Телефон'],
  ['telegram', 'Telegram'],
  ['city', 'Город'],
  ['current_employer', 'Текущий работодатель'],
  ['university', 'Вуз и образование'],
  ['resume', 'Резюме'],
  ['photo_url', 'Фото'],
]

function LivePreview({ update }: { update: Update }) {
  const [offer, setOffer] = useState('280000')
  const [view, setView] = useState<'blind' | 'disclosed'>('blind')
  const debounced = useDebounced({ update, offer }, 500)
  const offerTo = Number(debounced.offer)
  const q = useQuery({
    queryKey: ['candidate', 'preview', JSON.stringify(debounced)],
    queryFn: () =>
      unwrap(
        api.POST('/api/candidates/me/preview', {
          body: { privacy: debounced.update, ...(Number.isFinite(offerTo) && offerTo > 0 ? { offer_salary_to: offerTo } : {}) },
        }),
      ),
    placeholderData: (prev) => prev,
    retry: false,
  })

  return (
    <section className="flex flex-col gap-3" aria-labelledby="prev-h">
      <SectionTitle>
        <span id="prev-h">Так вас видят</span>
      </SectionTitle>
      <Segmented
        aria-label="Какую карточку показать"
        options={[
          { value: 'blind', label: 'До «да»' },
          { value: 'disclosed', label: 'После «да»' },
        ]}
        value={view}
        onChange={setView}
      />
      {q.isPending ? (
        <Skeleton className="h-40 w-full rounded-panel-lg" />
      ) : q.isError ? (
        <Alert tone="danger">{errorMessage(q.error)}</Alert>
      ) : view === 'blind' ? (
        <>
          <BlindCard data={blindToCard(q.data.blind)} />
          {q.data.pii_in_blind.length > 0 && <Alert tone="danger">В слепой карточке нашлись персональные поля: {q.data.pii_in_blind.join(', ')}.</Alert>}
        </>
      ) : (
        <Panel variant="outline" radius="lg" padding="sm" className="flex flex-col gap-2">
          <dl className="flex flex-col gap-2">
            {DISCLOSED_ORDER.map(([k, label]) => {
              const v = q.data.disclosed[k]
              if (v == null || v === '') return null
              return (
                <div key={k}>
                  <dt className="t-small text-ink-3">{label}</dt>
                  <dd className="break-words t-label text-ink">{String(v)}</dd>
                </div>
              )
            })}
          </dl>
          <p className="t-small text-ink-3">Поля, которые вы отключили выше, компании не передаются. Контакты, которых нет в профиле, не показываются.</p>
        </Panel>
      )}
      <Input
        label="Проверить значок для вилки (верх, ₽)"
        type="number"
        inputMode="numeric"
        step={10000}
        value={offer}
        onChange={(e) => setOffer(e.target.value)}
        hint="Так работодатель увидит ваш минимум относительно вилки приглашения."
        mono
      />
      {q.data && (q.data.notes ?? []).length > 0 && (
        <ul className="flex flex-col gap-1 t-small text-ink-3">
          {(q.data.notes ?? []).map((n) => (
            <li key={n}>· {n}</li>
          ))}
        </ul>
      )}
      {q.data && !q.data.in_bank.visible && (
        <Alert>
          При этих настройках вас не видят: {(q.data.in_bank.reasons ?? []).join('; ') || 'проверьте режим поиска'}.
        </Alert>
      )}
    </section>
  )
}
