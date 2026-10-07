import { useDemoMode } from '@/features/demo/api'
import { Button, Illustration, NodeLegend, Panel, TraceDiagram, type TraceDiagramRow } from '@/ui'

const CANDIDATE_STEPS: TraceDiagramRow[] = [
  { label: 'Опрос', evidence: 'Отрасли, действия за год, технологии и стаж — пять минут.', end: 'claimed' },
  { label: 'Тест', evidence: 'Адаптивный: задания подбираются под ваши ответы, у каждого свой вариант.', end: 'test' },
  { label: 'Категория', evidence: '«Специализация × грейд» — по тесту, а не по тексту резюме.', end: 'fsp' },
]

const EMPLOYER_STEPS: TraceDiagramRow[] = [
  { label: 'Потребность', evidence: 'Опишите текстом, как коллеге, и обязательно укажите вилку.', end: 'open' },
  { label: 'Подборка', evidence: 'Слепые карточки с объяснением: что подтверждено и чем.', end: 'test' },
  { label: 'Приглашение', evidence: 'С зарплатой. Ответ «Да», «Да, если…» или «Нет».', end: 'fsp' },
]

export default function Landing() {
  const demo = useDemoMode()
  return (
    <div className="mx-auto flex max-w-page flex-col gap-12 px-4 py-6 sm:px-6 sm:py-8 lg:gap-16 lg:px-12 lg:py-10">
      <Panel
        variant="hero"
        radius="hero"
        padding="none"
        className="grid items-center gap-6 overflow-hidden px-6 pt-8 sm:px-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,460px)] lg:gap-10 lg:pl-14 lg:pr-8 lg:pt-12"
      >
        <div className="flex flex-col gap-6 pb-2 lg:pb-12">
          <p className="t-small text-ink-3">Обратный подбор ИТ-специалистов</p>
          <h1 className="t-display">
            <span className="text-ink">Не резюме, а</span>
            <br />
            <span className="text-brand">подтверждённый уровень.</span>
          </h1>
          <p className="t-lead max-w-[560px] text-ink-2">
            Кандидат проходит тест и получает категорию. Работодатель описывает потребность и видит подборку с
            объяснением. Имя и контакты откроются только после взаимного «да».
          </p>
          <div className="flex flex-col gap-3 sm:flex-row">
            <Button size="lg" to="/auth/register?role=candidate" className="sm:min-w-[200px]">
              Ищу работу
            </Button>
            <Button size="lg" variant="outline" to="/auth/register?role=employer" className="sm:min-w-[200px]">
              Нанимаю
            </Button>
          </div>
          {demo && (
            <p className="t-body text-ink-2" data-testid="jury-link">
              Вы из жюри?{' '}
              <Button variant="text" to="/jury">
                Пройти тест самому без регистрации
              </Button>
            </p>
          )}
        </div>
        <Illustration
          name="hero_vzaimno"
          eager
          className="mx-auto -mb-2 w-full max-w-[420px] lg:max-w-none"
          alt="Две сферы, соединённые дугой: кандидат и работодатель"
        />
      </Panel>

      <section aria-labelledby="how" className="flex flex-col gap-6">
        <h2 id="how" className="t-title text-brand">
          Как это работает
        </h2>
        <div className="grid gap-6 lg:grid-cols-2">
          <Panel variant="panel" radius="xl" padding="lg" className="flex flex-col gap-5">
            <h3 className="t-body-strong text-ink">Кандидат</h3>
            <TraceDiagram rows={CANDIDATE_STEPS} labelWidth={96} rowHeight={52} aria-label="Путь кандидата" />
          </Panel>
          <Panel variant="panel" radius="xl" padding="lg" className="flex flex-col gap-5">
            <h3 className="t-body-strong text-ink">Работодатель</h3>
            <TraceDiagram rows={EMPLOYER_STEPS} labelWidth={96} rowHeight={52} aria-label="Путь работодателя" />
          </Panel>
        </div>
      </section>

      <section aria-labelledby="proof" className="flex flex-col gap-4 pb-4">
        <h2 id="proof" className="t-title text-brand">
          Доказательства видно по форме узла
        </h2>
        <p className="t-lead max-w-[640px] text-ink-2">
          У каждого навыка в подборке есть трасса к подтверждению. Резюме и самоописание показываются серыми «заявлено»
          — на категорию и рейтинг они не влияют.
        </p>
        <NodeLegend />
      </section>
    </div>
  )
}
