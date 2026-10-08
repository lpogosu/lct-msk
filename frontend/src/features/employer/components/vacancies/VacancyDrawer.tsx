import { useState } from 'react'
import { errorMessage } from '@/api/errors'
import { Alert, Button, Input, Segmented, TextArea, useToast } from '@/ui'
import { Drawer } from '@/ui/Drawer'
import { Select } from '@/ui/Select'
import { useSkills, useVacancyMutations, type NeedSummary, type VacancyEmployer } from '../../api'
import { salaryState } from '../../brief'
import { cityLabel, FORMAT_LABEL, GRADE_LABEL, SPEC_LABEL } from '../../dict'
import { SalaryFields } from '../SalaryRange'

/**
 * «Опубликовать бриф как вакансию» и правка вакансии (Р10): тот же текст и вилка, что в брифе. Вилка проверяется
 * как в приглашении: шире 1,6× — предупреждение, шире 2× — отказ. Один бриф — одна опубликованная вакансия.
 * X-04: вакансию можно создать и без готового брифа — тогда бриф создаётся автоматически из полей формы.
 */
export function VacancyDrawer({
  vacancy,
  needs,
  onClose,
}: {
  /** Есть вакансия — правка, нет — публикация брифа */
  vacancy: VacancyEmployer | null
  /** Брифы без опубликованной вакансии */
  needs: NeedSummary[]
  onClose: () => void
}) {
  const toast = useToast()
  const { publish, patch } = useVacancyMutations()
  const [mode, setMode] = useState<'need' | 'direct'>(needs.length > 0 ? 'need' : 'direct')
  const direct = !vacancy && mode === 'direct'
  const skills = useSkills()
  const [spec, setSpec] = useState('')
  const [grade, setGrade] = useState('')
  const [must, setMust] = useState<string[]>([])
  const [nice, setNice] = useState<string[]>([])
  const [team, setTeam] = useState({ size: '', role: '', stack_context: '', about: '' })
  const [needId, setNeedId] = useState(needs[0]?.id ?? '')
  const [title, setTitle] = useState(vacancy?.title ?? needs[0]?.title ?? '')
  const [description, setDescription] = useState(vacancy?.description ?? '')
  const [from, setFrom] = useState<number | null>(vacancy?.salary.salary_from ?? needs[0]?.salary_from ?? null)
  const [to, setTo] = useState<number | null>(vacancy?.salary.salary_to ?? needs[0]?.salary_to ?? null)
  const [net, setNet] = useState((vacancy?.salary.salary_type ?? (needs[0]?.salary_net === false ? 'gross' : 'net')) === 'net')
  const [format, setFormat] = useState(vacancy?.format ?? '')
  const [city, setCity] = useState(cityLabel(vacancy?.city))
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState(false)

  const sal = salaryState(from, to)
  const salaryError =
    sal.state === 'blocked'
      ? 'Вилка шире чем в 2 раза. Сузьте её: кандидат должен видеть реальные условия.'
      : sal.state === 'invalid'
        ? 'Проверьте вилку: «от» — не меньше 30 000 ₽, «до» — не меньше «от».'
        : sal.state === 'empty' && touched
          ? 'Вилка обязательна.'
          : null
  const salaryWarning = sal.state === 'warn' ? 'Вилка шире чем в 1,6 раза — кандидат увидит предупреждение.' : null
  const busy = publish.isPending || patch.isPending

  async function submit() {
    setTouched(true)
    setError(null)
    if (salaryError || sal.state === 'empty' || (!vacancy && !direct && !needId)) return
    if (direct && (!title.trim() || !spec || !grade)) {
      setError('Для вакансии без брифа укажите название, направление и грейд.')
      return
    }
    const common = {
      title: title.trim() || null,
      description: description.trim() || null,
      salary_from: from,
      salary_to: to,
      salary_type: (net ? 'net' : 'gross') as 'net' | 'gross',
      format: format || null,
      city: city.trim() || null,
    }
    try {
      const res = vacancy
        ? await patch.mutateAsync({ id: vacancy.id, body: common })
        : direct
          ? await publish.mutateAsync({
              ...common,
              need_id: null,
              specialization: spec,
              grade: grade as 'intern' | 'junior' | 'middle' | 'senior',
              must,
              nice,
              team: {
                size: Number.parseInt(team.size, 10) || null,
                role: team.role.trim() || null,
                stack_context: team.stack_context.trim() || null,
                about: team.about.trim() || null,
              },
            })
          : await publish.mutateAsync({ need_id: needId, ...common })
      res.warnings.forEach((w) => toast.show({ title: 'Обратите внимание', message: w }))
      toast.success(vacancy ? 'Вакансия обновлена' : 'Вакансия опубликована: кандидаты по вашим категориям увидят её в списке')
      onClose()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <Drawer
      open
      onClose={onClose}
      width={600}
      eyebrow={vacancy ? 'Вакансия' : 'Новая вакансия'}
      title={vacancy ? 'Правка вакансии' : direct ? 'Новая вакансия' : 'Опубликовать бриф'}
      footer={
        <div className="flex flex-wrap gap-4">
          <Button loading={busy} onClick={() => void submit()}>
            {vacancy ? 'Сохранить' : 'Опубликовать вакансию'}
          </Button>
          <Button variant="outline" onClick={onClose}>
            Отмена
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-6">
        {!vacancy && (
          <Segmented
            aria-label="Откуда вакансия"
            options={[
              { value: 'need', label: 'Из брифа' },
              { value: 'direct', label: 'Без брифа' },
            ]}
            value={mode}
            onChange={setMode}
            block
          />
        )}
        {!vacancy && !direct && (
          <Select
            label="Бриф"
            value={needId}
            onChange={(e) => {
              setNeedId(e.target.value)
              const n = needs.find((x) => x.id === e.target.value)
              if (n) {
                setTitle(n.title ?? '')
                setFrom(n.salary_from)
                setTo(n.salary_to)
                setNet(n.salary_net)
              }
            }}
            hint="Вакансия берёт текст и вилку брифа; их можно уточнить ниже."
          >
            {needs.map((n) => (
              <option key={n.id} value={n.id}>
                {n.title || n.category}
              </option>
            ))}
          </Select>
        )}
        <Input label="Заголовок" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} placeholder="Если пусто — возьмём название брифа" />
        <TextArea
          label="Описание"
          rows={5}
          value={description}
          maxLength={10000}
          onChange={(e) => setDescription(e.target.value)}
          hint="Если пусто — возьмём текст брифа."
        />
        <SalaryFields from={from} to={to} net={net} onFrom={setFrom} onTo={setTo} onNet={setNet} error={salaryError} warning={salaryWarning} />
        <div className="grid gap-5 sm:grid-cols-2">
          <Select label="Формат работы" value={format} onChange={(e) => setFormat(e.target.value)}>
            <option value="">Как в брифе</option>
            {Object.entries(FORMAT_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </Select>
          <Input label="Город" value={city} onChange={(e) => setCity(e.target.value)} placeholder="Москва" />
        </div>
        {direct && (
          <>
            <div className="grid gap-5 sm:grid-cols-2">
              <Select label="Направление" value={spec} onChange={(e) => setSpec(e.target.value)}>
                <option value="">Выберите</option>
                {Object.entries(SPEC_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </Select>
              <Select label="Грейд" value={grade} onChange={(e) => setGrade(e.target.value)}>
                <option value="">Выберите</option>
                {Object.entries(GRADE_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </Select>
            </div>
            <SkillPick label="Обязательные навыки (до 5)" all={skills.data ?? []} value={must} onChange={setMust} max={5} exclude={nice} />
            <SkillPick label="Желательные навыки" all={skills.data ?? []} value={nice} onChange={setNice} exclude={must} />
            <div className="grid gap-5 sm:grid-cols-2">
              <Input label="Размер команды" type="number" min={1} max={500} value={team.size} onChange={(e) => setTeam({ ...team, size: e.target.value })} />
              <Input label="Роль в команде" value={team.role} maxLength={120} onChange={(e) => setTeam({ ...team, role: e.target.value })} />
            </div>
            <Input label="Контекст стека" value={team.stack_context} maxLength={300} onChange={(e) => setTeam({ ...team, stack_context: e.target.value })} />
            <TextArea label="О команде" rows={3} value={team.about} maxLength={1000} onChange={(e) => setTeam({ ...team, about: e.target.value })} hint="Кандидат увидит это в вакансии. На Score не влияет." />
          </>
        )}
        {!vacancy && !direct && needs.length === 0 && (
          <Alert tone="info" title="Нет брифов без вакансии">
            Все ваши брифы уже опубликованы. Создайте вакансию «Без брифа» или опишите новую потребность.
          </Alert>
        )}
        {error && (
          <Alert tone="danger" title="Не получилось">
            {error}
          </Alert>
        )}
      </div>
    </Drawer>
  )
}

/** Выбор навыков из справочника чипами (X-04: вакансия без брифа). */
function SkillPick({
  label,
  all,
  value,
  onChange,
  max,
  exclude,
}: {
  label: string
  all: { id: string; name: string }[]
  value: string[]
  onChange: (v: string[]) => void
  max?: number
  exclude: string[]
}) {
  const toggle = (id: string) =>
    onChange(value.includes(id) ? value.filter((x) => x !== id) : max && value.length >= max ? value : [...value, id])
  return (
    <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
      <legend className="t-label text-ink">{label}</legend>
      <div className="flex max-h-40 flex-wrap gap-2 overflow-y-auto">
        {all
          .filter((s) => !exclude.includes(s.id))
          .map((s) => {
            const on = value.includes(s.id)
            return (
              <button
                key={s.id}
                type="button"
                aria-pressed={on}
                onClick={() => toggle(s.id)}
                className={`rounded-pill border px-3 py-1 t-small ${on ? 'border-brand bg-tint text-brand' : 'border-hairline text-ink-2 hover:border-brand'}`}
              >
                {s.name}
              </button>
            )
          })}
      </div>
    </fieldset>
  )
}
