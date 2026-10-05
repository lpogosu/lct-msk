import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { ApiException, errorMessage } from '@/api/errors'
import type { Schemas } from '@/api/types'
import { SectionTitle } from '@/features/candidate/components/Bits'
import { Sheet } from '@/features/candidate/components/Sheet'
import { fileSize, monthsLabel } from '@/features/candidate/format'
import { qk, type ProfileOut } from '@/features/candidate/queries'
import { cn } from '@/lib/cn'
import { Alert, Button, Checkbox, Input, Panel, Segmented, Skeleton, TrustNode, useToast } from '@/ui'

type Draft = Schemas['ResumeDraftOut']
type Scalar = Schemas['ScalarField']
type Quote = Schemas['Quote']
type ListItem = Schemas['ListItem']

const MAX_BYTES = 5 * 1024 * 1024

const GRADE_OPTIONS = [
  { value: 'intern', label: 'Стажёр' },
  { value: 'junior', label: 'Junior' },
  { value: 'middle', label: 'Middle' },
  { value: 'senior', label: 'Senior' },
] as const

/** Цитата в окружении: подсветка того места резюме, откуда взято поле (смещения start/end приходят с сервера). */
function Excerpt({ text, quote }: { text: string; quote?: Quote | null }) {
  if (!quote || quote.end <= quote.start || quote.end > text.length) return null
  const from = Math.max(0, quote.start - 45)
  const to = Math.min(text.length, quote.end + 45)
  return (
    <p className="rounded-md bg-panel px-2.5 py-1.5 t-small text-ink-3">
      <span className="text-ink-3">В резюме: </span>
      {from > 0 ? '…' : ''}
      {text.slice(from, quote.start).replace(/\s+/g, ' ')}
      <mark className="rounded-sm bg-heat-2 px-0.5 text-ink">{text.slice(quote.start, quote.end).replace(/\s+/g, ' ')}</mark>
      {text.slice(quote.end, to).replace(/\s+/g, ' ')}
      {to < text.length ? '…' : ''}
    </p>
  )
}

/**
 * Загрузка PDF-резюме и подтверждение распознанных полей (К7). Распознанное — «заявлено»: серые чипы, подсказка стажа;
 * в категорию и Score не идёт. Каждое поле приходит с цитатой, кандидат принимает или правит.
 */
export function ResumeSection({ profile }: { profile: ProfileOut }) {
  const qc = useQueryClient()
  const toast = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const status = profile.resume.status

  const draftQ = useQuery({
    queryKey: qk.resumeDraft,
    queryFn: () => unwrap(api.GET('/api/candidates/me/resume')),
    enabled: status === 'draft',
    retry: false,
  })

  const upload = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData()
      fd.append('file', file)
      return unwrap(api.POST('/api/candidates/me/resume', { body: { file: '' }, bodySerializer: () => fd }))
    },
    onSuccess: (d) => {
      qc.setQueryData(qk.resumeDraft, d)
      void qc.invalidateQueries({ queryKey: qk.profile })
      setError(null)
    },
    onError: (e) => setError(errorMessage(e)),
  })

  const remove = useMutation({
    mutationFn: () => unwrap(api.DELETE('/api/candidates/me/resume')),
    onSuccess: (p) => {
      qc.setQueryData(qk.profile, p)
      qc.removeQueries({ queryKey: qk.resumeDraft })
      toast.success('Резюме удалено. Подтверждённые поля профиля остались.')
      setConfirmDelete(false)
    },
    onError: (e) => toast.error(errorMessage(e)),
  })

  function pick(file: File | undefined) {
    if (!file) return
    setError(null)
    if (file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) return setError('Нужен файл в формате PDF.')
    if (file.size > MAX_BYTES) return setError(`Файл ${fileSize(file.size)} — больше 5 МБ. Сожмите PDF или заполните профиль вручную.`)
    upload.mutate(file)
  }

  const draft = status === 'draft' ? (upload.data ?? draftQ.data) : null

  return (
    <section className="flex flex-col gap-4" aria-labelledby="resume-h">
      <SectionTitle>
        <span id="resume-h">Резюме (PDF)</span>
      </SectionTitle>

      <input
        ref={fileRef}
        type="file"
        accept="application/pdf,.pdf"
        className="sr-only"
        aria-label="Выбрать PDF с резюме"
        onChange={(e) => {
          pick(e.target.files?.[0])
          e.target.value = ''
        }}
      />

      {status !== 'draft' && (
        <Panel variant="panel" radius="lg" padding="sm" className="flex flex-col gap-3">
          {status === 'confirmed' ? (
            <p className="t-body text-ink">
              Резюме загружено{profile.resume.uploaded_ago ? ` ${profile.resume.uploaded_ago}` : ''}
              {profile.resume.pages ? `, страниц: ${profile.resume.pages}` : ''}. Поля подтверждены.
            </p>
          ) : (
            <p className="t-body text-ink-2">
              Загрузите PDF с текстовым слоем (до 5 МБ): мы распознаем ФИО, контакты, навыки и стаж, а вы подтвердите. Можно и без резюме — заполните форму ниже.
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant={status === 'confirmed' ? 'outline' : 'primary'} loading={upload.isPending} onClick={() => fileRef.current?.click()}>
              {status === 'confirmed' ? 'Загрузить другое' : 'Загрузить PDF'}
            </Button>
            {status === 'confirmed' && (
              <Button size="sm" variant="text" onClick={() => setConfirmDelete(true)}>
                Удалить резюме
              </Button>
            )}
          </div>
          {upload.isPending && <p className="t-small text-ink-3">Разбираем резюме: обычно несколько секунд…</p>}
        </Panel>
      )}

      {error && (
        <Alert tone="danger" title="Резюме не загрузилось">
          {error}
        </Alert>
      )}

      {status === 'draft' && draftQ.isPending && !upload.data && <Skeleton className="h-48 w-full rounded-panel-lg" />}
      {status === 'draft' && draftQ.isError && !upload.data && (
        <Alert tone="danger">
          {draftQ.error instanceof ApiException && draftQ.error.code === 'resume_draft_not_found'
            ? 'Черновик устарел. Загрузите резюме заново.'
            : errorMessage(draftQ.error)}
          <div className="mt-2">
            <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()}>
              Загрузить PDF
            </Button>
          </div>
        </Alert>
      )}
      {draft && <DraftReview draft={draft} onReupload={() => fileRef.current?.click()} />}

      <Sheet
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="Удалить резюме?"
        description="Удалим текст резюме, черновик и цитаты. Поля профиля, которые вы уже подтвердили, останутся."
      >
        <div className="flex flex-col gap-3">
          <Button block variant="outline" loading={remove.isPending} onClick={() => remove.mutate()}>
            Удалить резюме
          </Button>
          <Button block variant="text" onClick={() => setConfirmDelete(false)}>
            Оставить
          </Button>
        </div>
      </Sheet>
    </section>
  )
}

// --- Проверка распознанного ---------------------------------------------------------------------------------------------

function scalarText(f?: Scalar | null): string {
  if (!f || f.value == null) return ''
  return String(f.label ?? f.value)
}

function DraftReview({ draft, onReupload }: { draft: Draft; onReupload: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const p = draft.parse
  const text = draft.text

  const [vals, setVals] = useState({
    full_name: scalarText(p.full_name),
    email: scalarText(p.email),
    phone: scalarText(p.phone),
    telegram: scalarText(p.telegram),
    city: scalarText(p.city),
    education: scalarText(p.education),
    declared_grade: p.declared_grade?.value != null ? String(p.declared_grade.value) : '',
    years: p.experience.value != null ? String(Math.floor(p.experience.value / 12)) : '',
    months: p.experience.value != null ? String(p.experience.value % 12) : '',
  })
  const [skills, setSkills] = useState<string[]>((p.skills ?? []).map((s) => s.id))
  const [roles, setRoles] = useState<string[]>((p.roles ?? []).map((s) => s.id))
  const [soft, setSoft] = useState<string[]>((p.soft_skills ?? []).map((s) => s.id))
  const [keepText, setKeepText] = useState(true)
  const set = (k: keyof typeof vals, v: string) => setVals((x) => ({ ...x, [k]: v }))

  const confirm = useMutation({
    mutationFn: () => {
      const months = (Number.parseInt(vals.years || '0', 10) || 0) * 12 + (Number.parseInt(vals.months || '0', 10) || 0)
      return unwrap(
        api.POST('/api/candidates/me/resume/confirm', {
          body: {
            ...(vals.full_name.trim() ? { full_name: vals.full_name.trim() } : {}),
            ...(vals.email.trim() ? { email: vals.email.trim() } : {}),
            ...(vals.phone.trim() ? { phone: vals.phone.trim() } : {}),
            ...(vals.telegram.trim() ? { telegram: vals.telegram.trim() } : {}),
            ...(vals.city.trim() ? { city: vals.city.trim() } : {}),
            ...(vals.education.trim() ? { education: vals.education.trim() } : {}),
            ...(vals.declared_grade ? { declared_grade: vals.declared_grade as 'intern' | 'junior' | 'middle' | 'senior' } : {}),
            ...(vals.years !== '' || vals.months !== '' ? { experience_months: months } : {}),
            use_experience_periods: true,
            skills,
            roles,
            soft_skills: soft,
            keep_resume_text: keepText,
          },
        }),
      )
    },
    onSuccess: (profile) => {
      qc.setQueryData(qk.profile, profile)
      qc.removeQueries({ queryKey: qk.resumeDraft })
      toast.success('Профиль обновлён. Всё из резюме — в «заявлено».')
    },
  })

  const toggle = (list: string[], set_: (l: string[]) => void, id: string) =>
    set_(list.includes(id) ? list.filter((x) => x !== id) : [...list, id])

  const lists: { title: string; items: ListItem[]; sel: string[]; setSel: (l: string[]) => void }[] = [
    { title: 'Навыки', items: p.skills ?? [], sel: skills, setSel: setSkills },
    { title: 'Роли', items: p.roles ?? [], sel: roles, setSel: setRoles },
    { title: 'Софт-скиллы', items: p.soft_skills ?? [], sel: soft, setSel: setSoft },
  ]

  return (
    <div className="flex flex-col gap-6">
      <Alert title="Проверьте, что распознали">
        Поле можно принять или исправить. Всё это — «заявлено»: работодатель увидит серые чипы, а категорию определит только тест.
      </Alert>
      {(p.warnings ?? []).map((w) => (
        <Alert key={w}>{w}</Alert>
      ))}

      <div className="flex flex-col gap-5">
        <Field label="ФИО" quote={p.full_name?.quote} text={text}>
          <Input aria-label="ФИО" value={vals.full_name} onChange={(e) => set('full_name', e.target.value)} />
        </Field>
        <Field label="Email" quote={p.email?.quote} text={text}>
          <Input aria-label="Email" type="email" value={vals.email} onChange={(e) => set('email', e.target.value)} />
        </Field>
        <Field label="Телефон" quote={p.phone?.quote} text={text}>
          <Input aria-label="Телефон" type="tel" value={vals.phone} onChange={(e) => set('phone', e.target.value)} />
        </Field>
        <Field label="Telegram" quote={p.telegram?.quote} text={text}>
          <Input aria-label="Telegram" value={vals.telegram} onChange={(e) => set('telegram', e.target.value)} />
        </Field>
        <Field label="Город" quote={p.city?.quote} text={text}>
          <Input aria-label="Город" value={vals.city} onChange={(e) => set('city', e.target.value)} />
        </Field>
        <Field label="Образование" quote={p.education?.quote} text={text}>
          <Input aria-label="Образование" value={vals.education} onChange={(e) => set('education', e.target.value)} />
        </Field>
        <Field label="Грейд как заявлен" quote={p.declared_grade?.quote} text={text} hint="Это подсказка. Категорию определит тест.">
          <Segmented
            aria-label="Заявленный грейд"
            options={GRADE_OPTIONS}
            value={vals.declared_grade || null}
            onChange={(v) => set('declared_grade', v)}
            block
            className="gap-2 [&_button]:px-2"
          />
        </Field>
        <div className="flex flex-col gap-2">
          <p className="t-label text-ink">Стаж</p>
          <div className="grid grid-cols-2 gap-3">
            <Input label="Лет" type="number" min={0} max={60} value={vals.years} onChange={(e) => set('years', e.target.value)} />
            <Input label="Месяцев" type="number" min={0} max={11} value={vals.months} onChange={(e) => set('months', e.target.value)} />
          </div>
          {(p.experience.periods ?? []).length > 0 && (
            <ul className="flex flex-col gap-1.5">
              {(p.experience.periods ?? []).map((per, i) => (
                <li key={i} className="t-small text-ink-2">
                  {per.start} — {per.end ?? 'по настоящее время'} · {monthsLabel(per.months)}
                  {per.title ? ` · ${per.title}` : ''}
                  {per.company ? ` · ${per.company}` : ''}
                </li>
              ))}
              {(p.experience.overlap_months ?? 0) > 0 && (
                <li className="t-small text-ink-3">Пересечение периодов ({p.experience.overlap_months} мес.) посчитано один раз.</li>
              )}
            </ul>
          )}
        </div>
      </div>

      {lists.map(
        (l) =>
          l.items.length > 0 && (
            <section key={l.title} aria-label={l.title}>
              <p className="mb-2 t-label text-ink">{l.title}</p>
              <ul className="flex flex-wrap gap-2">
                {l.items.map((it) => {
                  const on = l.sel.includes(it.id)
                  return (
                    <li key={it.id}>
                      <button
                        type="button"
                        role="checkbox"
                        aria-checked={on}
                        title={`В резюме: «${it.quote.text}»`}
                        onClick={() => toggle(l.sel, l.setSel, it.id)}
                        className={cn(
                          'inline-flex items-center gap-1.5 rounded-pill border px-3 py-1.5 t-small transition-colors',
                          on ? 'border-dashed border-ink-3 bg-white text-ink' : 'border-hairline bg-panel text-ink-3 line-through',
                        )}
                      >
                        {on && <TrustNode kind="claimed" />}
                        {it.name}
                      </button>
                    </li>
                  )
                })}
              </ul>
              <p className="mt-1.5 t-small text-ink-3">Нажмите на чип, чтобы убрать лишнее. Цитата — в подсказке при наведении.</p>
            </section>
          ),
      )}

      <Checkbox
        checked={keepText}
        onChange={(e) => setKeepText(e.target.checked)}
        label={<span className="t-body">Сохранить текст резюме</span>}
        description="Компания увидит его только после взаимного «да» и только если вы разрешите поле «Резюме» в приватности."
      />

      {confirm.error ? (
        <Alert tone="danger" title="Не удалось подтвердить">
          {errorMessage(confirm.error)}
        </Alert>
      ) : null}
      <div className="flex flex-col gap-2">
        <Button block size="lg" loading={confirm.isPending} onClick={() => confirm.mutate()}>
          Подтвердить поля
        </Button>
        <Button variant="text" className="self-center" onClick={onReupload}>
          Загрузить другой файл
        </Button>
      </div>
    </div>
  )
}

function Field({
  label,
  quote,
  text,
  hint,
  children,
}: {
  label: string
  quote?: Quote | null
  text: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <p className="t-label text-ink">{label}</p>
      {children}
      {hint && <p className="t-small text-ink-3">{hint}</p>}
      <Excerpt text={text} quote={quote} />
    </div>
  )
}
