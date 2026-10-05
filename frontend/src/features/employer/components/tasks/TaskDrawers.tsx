import { useMemo, useState } from 'react'
import { errorMessage } from '@/api/errors'
import { Alert, Button, Checkbox, Input, Segmented, TextArea, useToast } from '@/ui'
import { Drawer } from '@/ui/Drawer'
import { Select } from '@/ui/Select'
import { useTaskCatalog, useTaskMutations, type Submission, type TaskOut } from '../../api'
import { GRADE_LABEL, GRADE_ORDER, SPEC_LABEL } from '../../dict'
import { Label } from '../common'

const TESTED = ['backend_python', 'frontend_js'] as const // направления, где есть откалиброванные семейства (config/taxonomy.yaml)
const PROMPT_MIN = 50
const TIME_MAX = 15

/**
 * Новая регулярная задача (Р11). Тип A — откалиброванное семейство из каталога: каждому кандидату свой одноразовый
 * экземпляр и автопроверка. Тип B — «предложи подход» с рубрикой из 4 критериев по 0–2. Аудитория — специализация × грейд ±1.
 */
export function NewTaskDrawer({ onClose }: { onClose: () => void }) {
  const toast = useToast()
  const { create } = useTaskMutations()
  const [kind, setKind] = useState<'catalog' | 'approach'>('catalog')
  const [spec, setSpec] = useState<string>('backend_python')
  const [grade, setGrade] = useState<string>('middle')
  const [familyId, setFamilyId] = useState('')
  const [title, setTitle] = useState('')
  const [prompt, setPrompt] = useState('')
  const [minutes, setMinutes] = useState('12')
  const [ack, setAck] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState(false)
  const catalog = useTaskCatalog(spec)

  const families = useMemo(() => (catalog.data ?? []).filter((f) => f.calibrated), [catalog.data])
  const mins = Number(minutes)
  const problems = {
    family: kind === 'catalog' && !familyId ? 'Выберите семейство заданий' : null,
    title: kind === 'approach' && !title.trim() ? 'Назовите задачу' : null,
    prompt: kind === 'approach' && prompt.trim().length < PROMPT_MIN ? `Текст задачи — не короче ${PROMPT_MIN} знаков` : null,
    minutes: kind === 'approach' && !(mins >= 1 && mins <= TIME_MAX) ? `Время — от 1 до ${TIME_MAX} минут` : null,
    ack: !ack ? 'Подтвердите: это не рабочая задача компании' : null,
  }
  const hasProblems = Object.values(problems).some(Boolean)

  async function submit() {
    setTouched(true)
    setError(null)
    if (hasProblems) return
    try {
      await create.mutateAsync({
        kind,
        specialization: spec,
        grade: grade as 'intern' | 'junior' | 'middle' | 'senior',
        not_real_work_ack: ack,
        title: title.trim() || null,
        family_id: kind === 'catalog' ? familyId : null,
        prompt: kind === 'approach' ? prompt.trim() : null,
        time_limit_min: kind === 'approach' ? mins : null,
        tags: [],
      })
      toast.success('Задача создана и начнёт предлагаться кандидатам')
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
      eyebrow="Регулярные задачи"
      title="Новая задача"
      footer={
        <div className="flex flex-wrap gap-4">
          <Button loading={create.isPending} onClick={() => void submit()}>
            Создать задачу
          </Button>
          <Button variant="outline" onClick={onClose}>
            Отмена
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-2">
          <Label>Тип задачи</Label>
          <Segmented
            aria-label="Тип задачи"
            options={[
              { value: 'catalog', label: 'Из каталога' },
              { value: 'approach', label: 'Предложи подход' },
            ]}
            value={kind}
            onChange={setKind}
          />
          <p className="t-small text-ink-3">
            {kind === 'catalog'
              ? 'Откалиброванное семейство заданий: у каждого кандидата свой одноразовый экземпляр, решение проверяется автоматически.'
              : 'Открытая задача: кандидат описывает подход, вы оцениваете по рубрике из четырёх критериев (0–2).'}
          </p>
        </div>
        <div className="grid gap-5 sm:grid-cols-2">
          <Select label="Специализация" value={spec} onChange={(e) => { setSpec(e.target.value); setFamilyId('') }}>
            {TESTED.map((s) => (
              <option key={s} value={s}>
                {SPEC_LABEL[s]}
              </option>
            ))}
          </Select>
          <Select label="Грейд (аудитория ±1)" value={grade} onChange={(e) => setGrade(e.target.value)}>
            {GRADE_ORDER.map((g) => (
              <option key={g} value={g}>
                {GRADE_LABEL[g]}
              </option>
            ))}
          </Select>
        </div>
        {kind === 'catalog' ? (
          <Select
            label="Семейство заданий"
            value={familyId}
            onChange={(e) => setFamilyId(e.target.value)}
            error={touched ? problems.family : null}
            hint={catalog.isPending ? 'Загружаем каталог…' : `В каталоге: ${families.length}`}
          >
            <option value="">Выберите…</option>
            {families.map((f) => (
              <option key={f.family_id} value={f.family_id}>
                {f.title} · {f.module_name ?? f.module} · {f.grade_name ?? GRADE_LABEL[f.grade]} · {f.minutes} мин
              </option>
            ))}
          </Select>
        ) : (
          <>
            <Input label="Название" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} error={touched ? problems.title : null} />
            <TextArea
              label="Текст задачи"
              rows={6}
              value={prompt}
              maxLength={5000}
              onChange={(e) => setPrompt(e.target.value)}
              error={touched ? problems.prompt : null}
              hint="Без контактов и просьб об оплате. Описывайте учебную ситуацию, а не реальную рабочую задачу."
            />
            <Input
              label="Время на решение, минут"
              mono
              inputMode="numeric"
              value={minutes}
              onChange={(e) => setMinutes(e.target.value.replace(/\D/g, ''))}
              error={touched ? problems.minutes : null}
              hint={`Не больше ${TIME_MAX} минут`}
              wrapperClassName="w-[200px]"
            />
          </>
        )}
        <Checkbox
          checked={ack}
          onChange={(e) => setAck(e.target.checked)}
          label="Это не рабочая задача компании"
          description="Решения кандидатов не используются в работе. Это условие платформы."
          error={touched ? problems.ack : null}
        />
        {error && (
          <Alert tone="danger" title="Не получилось создать задачу">
            {error}
          </Alert>
        )}
      </div>
    </Drawer>
  )
}

/** Оценка решения типа B по рубрике: все критерии по 0–2 и короткий комментарий. */
export function ReviewDrawer({ task, submission, onClose }: { task: TaskOut; submission: Submission; onClose: () => void }) {
  const toast = useToast()
  const { review } = useTaskMutations()
  const criteria = Object.entries((task.rubric as { criteria?: Record<string, string> } | null)?.criteria ?? {})
  const [scores, setScores] = useState<Record<string, number>>({})
  const [comment, setComment] = useState('')
  const [error, setError] = useState<string | null>(null)
  const complete = criteria.length > 0 && criteria.every(([k]) => scores[k] != null)

  async function submit() {
    setError(null)
    try {
      await review.mutateAsync({ id: submission.assignment_id, scores, comment: comment.trim() })
      toast.success('Оценка сохранена. Кандидат получил уведомление.')
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
      eyebrow={task.title}
      title={`Решение ${submission.pseudonym}`}
      footer={
        <div className="flex gap-4">
          <Button loading={review.isPending} disabled={!complete} onClick={() => void submit()}>
            Сохранить оценку
          </Button>
          <Button variant="outline" onClick={onClose}>
            Отмена
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-6">
        <div>
          <Label>Решение кандидата</Label>
          <p className="whitespace-pre-line t-body text-ink [overflow-wrap:anywhere]">{String(submission.answer ?? '—')}</p>
        </div>
        {criteria.map(([key, label]) => (
          <div key={key} className="flex flex-col gap-2">
            <p className="t-label text-ink">{label}</p>
            <Segmented
              aria-label={label}
              options={[
                { value: '0', label: '0 — нет' },
                { value: '1', label: '1 — частично' },
                { value: '2', label: '2 — хорошо' },
              ]}
              value={scores[key] != null ? String(scores[key]) : null}
              onChange={(v) => setScores((s) => ({ ...s, [key]: Number(v) }))}
            />
          </div>
        ))}
        <TextArea label="Комментарий (необязательно)" rows={3} maxLength={300} value={comment} onChange={(e) => setComment(e.target.value)} hint={`${comment.length} из 300`} />
        {error && (
          <Alert tone="danger" title="Не получилось">
            {error}
          </Alert>
        )}
      </div>
    </Drawer>
  )
}
