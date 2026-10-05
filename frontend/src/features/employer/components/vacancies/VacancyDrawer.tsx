import { useState } from 'react'
import { errorMessage } from '@/api/errors'
import { Alert, Button, Input, TextArea, useToast } from '@/ui'
import { Drawer } from '@/ui/Drawer'
import { Select } from '@/ui/Select'
import { useVacancyMutations, type NeedSummary, type VacancyEmployer } from '../../api'
import { salaryState } from '../../brief'
import { cityLabel, FORMAT_LABEL } from '../../dict'
import { SalaryFields } from '../SalaryRange'

/**
 * «Опубликовать бриф как вакансию» и правка вакансии (Р10): тот же текст и вилка, что в брифе. Вилка проверяется
 * как в приглашении: шире 1,6× — предупреждение, шире 2× — отказ. Один бриф — одна опубликованная вакансия.
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
    if (salaryError || sal.state === 'empty' || (!vacancy && !needId)) return
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
      title={vacancy ? 'Правка вакансии' : 'Опубликовать бриф'}
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
        {!vacancy && needs.length === 0 && (
          <Alert tone="info" title="Нет брифов без вакансии">
            Все ваши брифы уже опубликованы. Опишите новую потребность — и её можно будет опубликовать.
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
