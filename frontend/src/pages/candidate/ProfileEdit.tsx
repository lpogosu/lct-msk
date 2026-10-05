import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { ApiException, errorMessage } from '@/api/errors'
import { SectionTitle, SkillChip } from '@/features/candidate/components/Bits'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { ResumeSection } from '@/features/candidate/profile/ResumeSection'
import { qk, useProfile, type ProfileOut } from '@/features/candidate/queries'
import { Alert, Button, Input, MobileHeader, Panel, Segmented, useToast } from '@/ui'

const GRADES = [
  { value: 'intern', label: 'Стажёр' },
  { value: 'junior', label: 'Junior' },
  { value: 'middle', label: 'Middle' },
  { value: 'senior', label: 'Senior' },
] as const

interface Form {
  full_name: string
  contact_email: string
  phone: string
  telegram: string
  city: string
  current_employer: string
  education: string
  declared_grade: string
  years: string
  months: string
  skills: string[]
}

function formFrom(p: ProfileOut): Form {
  return {
    full_name: p.full_name ?? '',
    contact_email: p.contact_email ?? '',
    phone: p.phone ?? '',
    telegram: p.telegram ?? '',
    city: p.city?.name ?? p.city_text ?? '',
    current_employer: p.current_employer ?? '',
    education: p.education ?? '',
    declared_grade: p.declared_grade?.code ?? '',
    years: p.experience_months != null ? String(Math.floor(p.experience_months / 12)) : '',
    months: p.experience_months != null ? String(p.experience_months % 12) : '',
    skills: (p.skills ?? []).map((s) => s.id),
  }
}

/** К7 «Профиль и резюме»: загрузка PDF с распознаванием и ручная форма. Всё, что здесь, — «заявлено». */
export default function CandidateProfileEdit() {
  const q = useProfile()
  return (
    <>
      <MobileHeader title="Профиль и резюме" backTo="/candidate/profile" />
      <QueryBoundary query={q}>
        {(p) => (
          <div className="flex flex-col gap-9">
            <header>
              <h1 className="t-title text-brand">Профиль и резюме</h1>
              <p className="mt-2 t-body text-ink-2">{p.note}</p>
            </header>
            <ResumeSection profile={p} />
            {p.resume.status !== 'draft' && (
              <ProfileForm key={JSON.stringify([p.full_name, p.skills, p.experience_months, p.contact_email, p.phone, p.telegram, p.city, p.declared_grade, p.education, p.current_employer])} profile={p} />
            )}
            <Link to="/candidate/profile/pdf" className="self-center t-label text-brand underline-offset-4 hover:underline">
              Скачать PDF-профиль
            </Link>
          </div>
        )}
      </QueryBoundary>
    </>
  )
}

function ProfileForm({ profile }: { profile: ProfileOut }) {
  const qc = useQueryClient()
  const toast = useToast()
  const initial = useMemo(() => formFrom(profile), [profile])
  const [f, setF] = useState<Form>(initial)
  const [skillInput, setSkillInput] = useState('')
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((x) => ({ ...x, [k]: v }))
  const dirty = JSON.stringify(f) !== JSON.stringify(initial)

  const nameOf = (id: string) => (profile.skills ?? []).find((s) => s.id === id)?.name ?? id

  const save = useMutation({
    mutationFn: () => {
      const months = f.years !== '' || f.months !== '' ? (Number.parseInt(f.years || '0', 10) || 0) * 12 + (Number.parseInt(f.months || '0', 10) || 0) : null
      const orNull = (s: string) => (s.trim() === '' ? null : s.trim())
      return unwrap(
        api.PUT('/api/candidates/me', {
          body: {
            full_name: orNull(f.full_name),
            contact_email: orNull(f.contact_email),
            phone: orNull(f.phone),
            telegram: orNull(f.telegram),
            city: orNull(f.city),
            current_employer: orNull(f.current_employer),
            education: orNull(f.education),
            declared_grade: (f.declared_grade || null) as 'intern' | 'junior' | 'middle' | 'senior' | null,
            experience_months: months,
            skills: f.skills,
          },
        }),
      )
    },
    onSuccess: (p) => {
      qc.setQueryData(qk.profile, p)
      toast.success('Профиль сохранён.')
    },
  })

  const issues = save.error instanceof ApiException ? save.error.fieldIssues : []

  function addSkill() {
    const v = skillInput.trim()
    if (!v || f.skills.some((s) => s.toLowerCase() === v.toLowerCase())) return
    set('skills', [...f.skills, v])
    setSkillInput('')
  }

  return (
    <section className="flex flex-col gap-5" aria-labelledby="form-h">
      <SectionTitle>
        <span id="form-h">Данные профиля</span>
      </SectionTitle>
      <Input label="ФИО" value={f.full_name} onChange={(e) => set('full_name', e.target.value)} autoComplete="name" hint="Компания увидит его только после взаимного «да»." />
      <Input label="Email для связи" type="email" value={f.contact_email} onChange={(e) => set('contact_email', e.target.value)} hint={`По умолчанию — email аккаунта: ${profile.account_email}`} />
      <Input label="Телефон" type="tel" value={f.phone} onChange={(e) => set('phone', e.target.value)} placeholder="+7 915 204-18-73" />
      <Input label="Telegram" value={f.telegram} onChange={(e) => set('telegram', e.target.value)} placeholder="@ник" />
      <Input label="Город" value={f.city} onChange={(e) => set('city', e.target.value)} placeholder="Москва" hint={profile.region ? `Субъект РФ, который видит работодатель: ${profile.region.name}` : undefined} />
      <Input label="Текущий работодатель" value={f.current_employer} onChange={(e) => set('current_employer', e.target.value)} hint="Скрыт до «да»." />
      <Input label="Образование" value={f.education} onChange={(e) => set('education', e.target.value)} hint="Необязательно. Скрыто до «да»." />

      <div className="flex flex-col gap-2">
        <p className="t-label text-ink-2">Грейд как заявлен</p>
        <Segmented aria-label="Заявленный грейд" options={GRADES} value={f.declared_grade || null} onChange={(v) => set('declared_grade', v)} block className="gap-2 [&_button]:px-2" />
        <p className="t-small text-ink-3">Подсказка при выборе грейда. Категорию определяет только тест.</p>
      </div>

      <div className="flex flex-col gap-2">
        <p className="t-label text-ink-2">Стаж в ИТ</p>
        <div className="grid grid-cols-2 gap-3">
          <Input aria-label="Стаж, лет" type="number" min={0} max={60} value={f.years} onChange={(e) => set('years', e.target.value)} placeholder="Лет" />
          <Input aria-label="Стаж, месяцев" type="number" min={0} max={11} value={f.months} onChange={(e) => set('months', e.target.value)} placeholder="Месяцев" />
        </div>
        {profile.experience_label && <p className="t-small text-ink-3">Сейчас в профиле: {profile.experience_label}</p>}
      </div>

      {(profile.experience ?? []).length > 0 && (
        <div>
          <p className="mb-1.5 t-label text-ink-2">Периоды работы</p>
          <ul className="flex flex-col gap-1">
            {profile.experience!.map((e, i) => (
              <li key={i} className="t-small text-ink-2">
                {e.start} — {e.end ?? 'по настоящее время'}
                {e.title ? ` · ${e.title}` : ''}
                {e.company ? ` · ${e.company}` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-col gap-2.5">
        <p className="t-label text-ink-2">Навыки — «заявлено»</p>
        {f.skills.length > 0 ? (
          <ul className="flex flex-wrap gap-2">
            {f.skills.map((id) => (
              <li key={id}>
                <button
                  type="button"
                  onClick={() => set('skills', f.skills.filter((x) => x !== id))}
                  aria-label={`Убрать навык «${nameOf(id)}»`}
                  className="inline-flex items-center gap-1"
                >
                  <SkillChip name={nameOf(id)} kind="claimed" />
                  <span aria-hidden="true" className="text-ink-3">
                    ×
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="t-small text-ink-3">Пока нет. Добавьте то, с чем работали, — работодатель увидит серые чипы.</p>
        )}
        <div className="flex gap-2">
          <Input
            aria-label="Добавить навык"
            wrapperClassName="flex-1"
            value={skillInput}
            onChange={(e) => setSkillInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                addSkill()
              }
            }}
            placeholder="Например, FastAPI"
          />
          <Button variant="outline" className="h-12 shrink-0" onClick={addSkill} disabled={!skillInput.trim()}>
            Добавить
          </Button>
        </div>
        <p className="t-small text-ink-3">Навыки сверяются со справочником платформы; неизвестные сервер вернёт списком.</p>
      </div>

      {((profile.roles ?? []).length > 0 || (profile.soft_skills ?? []).length > 0) && (
        <Panel variant="panel" radius="lg" padding="sm" className="flex flex-col gap-2">
          {(profile.roles ?? []).length > 0 && (
            <p className="t-small text-ink-2">
              <span className="text-ink-3">Роли: </span>
              {profile.roles!.map((r) => r.name).join(', ')}
            </p>
          )}
          {(profile.soft_skills ?? []).length > 0 && (
            <p className="t-small text-ink-2">
              <span className="text-ink-3">Софт-скиллы: </span>
              {profile.soft_skills!.map((r) => r.name).join(', ')}
            </p>
          )}
          <p className="t-small text-ink-3">Роли и софт-скиллы берутся из резюме: загрузите его заново, чтобы обновить.</p>
        </Panel>
      )}

      {(profile.missing ?? []).length > 0 && (
        <Alert title="Что ещё стоит заполнить">
          <ul className="mt-1 list-disc pl-4">
            {profile.missing!.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </Alert>
      )}

      {save.error ? (
        <Alert tone="danger" title="Не удалось сохранить">
          {issues.length > 0 ? (
            <ul className="list-disc pl-4">
              {issues.map((i, k) => (
                <li key={k}>{i.message}</li>
              ))}
            </ul>
          ) : (
            errorMessage(save.error)
          )}
        </Alert>
      ) : null}

      <div className="sticky bottom-[72px] z-20 -mx-6 border-t border-hairline bg-white/95 px-6 py-3 backdrop-blur">
        <Button block size="lg" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate()}>
          {dirty ? 'Сохранить профиль' : 'Профиль сохранён'}
        </Button>
      </div>
    </section>
  )
}
