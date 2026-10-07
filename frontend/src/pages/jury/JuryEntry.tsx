import { useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ApiException, errorMessage } from '@/api/errors'
import { enterJuryPersona, startJuryTest, useJuryPaths, type JuryMode } from '@/features/demo/api'
import { Alert, Button, EmptyState, Panel, Segmented, Skeleton } from '@/ui'
import { Select } from '@/ui/Select'

type Busy = JuryMode | 'employer' | 'lab' | null

/**
 * Вход для жюри (публичный, только DEMO_MODE): без регистрации и писем. Одна кнопка — одноразовый кандидат с готовым
 * опросом и начатым тестом; остальные пути — песочница «Попробуй списать», роль работодателя, лаборатория методиста и
 * проверка на собственных профилях. Ничего из этого не работает на боевом стенде (бэкенд отвечает 404).
 */
export default function JuryEntry() {
  const navigate = useNavigate()
  const paths = useJuryPaths()
  const [spec, setSpec] = useState<string | null>(null)
  const [grade, setGrade] = useState<string | null>(null)
  const [busy, setBusy] = useState<Busy>(null)
  const [error, setError] = useState<string | null>(null)

  if (paths.isPending) {
    return (
      <div className="mx-auto max-w-page px-4 py-8 sm:px-6 lg:px-12">
        <Skeleton className="h-40 w-full rounded-panel-xl" />
      </div>
    )
  }
  if (paths.error instanceof ApiException && paths.error.status === 404) {
    return (
      <div className="mx-auto max-w-page px-4 py-10 sm:px-6 lg:px-12">
        <EmptyState
          title="Страница для жюри недоступна"
          description="Она включается только в демонстрационном режиме (DEMO_MODE). На этом стенде он выключен."
          action={<Button to="/">На главную</Button>}
        />
      </div>
    )
  }
  if (paths.error || !paths.data) {
    return (
      <div className="mx-auto max-w-page px-4 py-10 sm:px-6 lg:px-12">
        <Alert tone="danger" title="Не удалось загрузить страницу">
          {errorMessage(paths.error)}
        </Alert>
      </div>
    )
  }

  const p = paths.data
  const specValue = spec ?? p.default_specialization
  const gradeValue = grade ?? p.default_grade

  async function begin(mode: JuryMode) {
    setError(null)
    setBusy(mode)
    try {
      const t = await startJuryTest(mode, specValue, gradeValue)
      navigate(t.path)
    } catch (e) {
      setError(errorMessage(e))
      setBusy(null)
    }
  }

  async function enter(persona: 'employer' | 'lab') {
    setError(null)
    setBusy(persona)
    try {
      const t = await enterJuryPersona(persona)
      navigate(t.path)
    } catch (e) {
      setError(errorMessage(e))
      setBusy(null)
    }
  }

  return (
    <div className="mx-auto flex max-w-page flex-col gap-10 px-4 py-6 sm:px-6 sm:py-8 lg:gap-12 lg:px-12 lg:py-10">
      <header className="flex max-w-[760px] flex-col gap-4">
        <p className="t-small text-ink-3">Демонстрационный стенд · для жюри</p>
        <h1 className="t-display">
          <span className="text-ink">Проверьте сами.</span>
          <br />
          <span className="text-brand">Регистрация не нужна.</span>
        </h1>
        <p className="t-lead text-ink-2">
          Пройдите тест как кандидат, попробуйте списать, загляните в подборку глазами работодателя и в лабораторию
          методиста. Каждое задание — одноразовый клон: два прохождения не повторят друг друга, а ответ нельзя узнать
          заранее.
        </p>
      </header>

      {error && <Alert tone="danger">{error}</Alert>}

      <section aria-labelledby="take" className="flex flex-col gap-4">
        <h2 id="take" className="t-title text-brand">
          Пройти тест самому
        </h2>
        <Panel variant="panel" radius="xl" padding="lg" className="flex flex-col gap-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <Select label="Специализация" value={specValue} onChange={(e) => setSpec(e.target.value)}>
              {p.specializations.map((s) => (
                <option key={s.code} value={s.code}>
                  {s.name}
                </option>
              ))}
            </Select>
            <div className="flex flex-col gap-1.5">
              <span className="t-label text-ink">Заявленный грейд</span>
              <Segmented
                aria-label="Заявленный грейд"
                options={p.grades.map((g) => ({ value: g.code, label: g.name }))}
                value={gradeValue}
                onChange={setGrade}
                block
              />
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Panel variant="tint" radius="lg" padding="md" className="flex flex-col gap-3" data-testid="card-express">
              <h3 className="t-name text-ink">Пройти экспресс-тест самому</h3>
              <p className="t-body text-ink-2">
                Около {p.express_minutes} минут, не больше {p.express_items_max} заданий. Тот же движок, тот же банк и та
                же защита от подсказчика. Категорию экспресс не присваивает; если точности не хватит, мы так и скажем.
              </p>
              <Button size="lg" block loading={busy === 'express'} disabled={busy !== null} onClick={() => void begin('express')}>
                Начать экспресс-тест
              </Button>
            </Panel>
            <Panel variant="outline" radius="lg" padding="md" className="flex flex-col gap-3" data-testid="card-full">
              <h3 className="t-name text-ink">Пройти полный тест</h3>
              <p className="t-body text-ink-2">
                12–20 заданий, до 25 минут. Присваивает категорию «специализация × грейд», показывает подтверждение
                навыков и предлагает добор вверх или вниз.
              </p>
              <Button size="lg" variant="outline" block loading={busy === 'full'} disabled={busy !== null} onClick={() => void begin('full')}>
                Начать полный тест
              </Button>
            </Panel>
          </div>
          <p className="t-small text-ink-3">
            Мы создаём одноразового кандидата: письмо с кодом и пароль не нужны. Сессии помечены как демонстрационные и в
            калибровку банка и метрики не попадают.
          </p>
        </Panel>
      </section>

      <section aria-labelledby="more" className="flex flex-col gap-4">
        <h2 id="more" className="t-title text-brand">
          Что ещё посмотреть
        </h2>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          <PathCard
            title="Попробовать списать"
            text="Мини-тест из 4 заданий с двойниками в буфере: списывайте как угодно, затем смотрите, что заметила система."
          >
            <Button variant="outline" block to="/try">
              Открыть песочницу
            </Button>
          </PathCard>
          <PathCard
            title="Посмотреть глазами работодателя"
            text="Вход в демо-компанию: потребности, слепая подборка с объяснением, приглашения и «Взаимно»."
          >
            <Button
              variant="outline"
              block
              loading={busy === 'employer'}
              disabled={busy !== null || !p.employer_available}
              onClick={() => void enter('employer')}
            >
              Войти как работодатель
            </Button>
            {!p.employer_available && <p className="t-small text-ink-3">Демо-мир не собран: выполните make seed.</p>}
          </PathCard>
          <PathCard
            title="Лаборатория методиста"
            text="Трасса каждой сессии, банк заданий и «3 клона рядом», сверки и акты, валидация."
          >
            <Button
              variant="outline"
              block
              loading={busy === 'lab'}
              disabled={busy !== null || !p.lab_available}
              onClick={() => void enter('lab')}
            >
              Открыть лабораторию
            </Button>
            {!p.lab_available && <p className="t-small text-ink-3">Демо-мир не собран: выполните make seed.</p>}
          </PathCard>
        </div>
      </section>

      <section aria-labelledby="own" className="flex flex-col gap-3 pb-4">
        <h2 id="own" className="t-title text-brand">
          Проверить на своих профилях
        </h2>
        <Panel variant="panel" radius="xl" padding="lg" className="flex max-w-[760px] flex-col gap-3">
          <p className="t-body text-ink-2">
            Загрузите свой набор «бриф × кандидат» (строки как в <code className="font-mono">backend/eval/pairs_test.jsonl</code>) и
            получите те же метрики, что у нас. Нужен вход администратора, пароль — в{' '}
            <code className="font-mono">backend/var/demo_accounts.md</code>.
          </p>
          <ol className="list-decimal pl-5 t-body text-ink-2">
            <li>
              Откройте{' '}
              <a className="text-brand underline underline-offset-4" href="/api/docs" target="_blank" rel="noreferrer">
                документацию API
              </a>
              , войдите через <code className="font-mono">/api/auth/login</code> и нажмите Authorize.
            </li>
            <li>
              Отправьте набор в <code className="font-mono">POST /api/admin/eval/pairs</code>, затем запустите оценку тем же маршрутом с пустым
              телом.
            </li>
            <li>
              Итог смотрите в лаборатории:{' '}
              <Link className="text-brand underline underline-offset-4" to="/lab/validation">
                Валидация
              </Link>
              .
            </li>
          </ol>
        </Panel>
      </section>
    </div>
  )
}

function PathCard({ title, text, children }: { title: string; text: string; children: ReactNode }) {
  return (
    <Panel variant="panel" radius="xl" padding="md" className="flex flex-col gap-3">
      <h3 className="t-name text-ink">{title}</h3>
      <p className="t-body text-ink-2">{text}</p>
      <div className="mt-auto flex flex-col gap-2">{children}</div>
    </Panel>
  )
}
