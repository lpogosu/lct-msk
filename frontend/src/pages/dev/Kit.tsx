import { useState, type ReactNode } from 'react'
import { EMPLOYER_NAV } from '@/app/nav'
import {
  Alert,
  Button,
  Checkbox,
  Counters,
  EmptyState,
  HeatCell,
  HeatLegend,
  heatLevel,
  Identicon,
  Illustration,
  Input,
  Logo,
  MobileHeader,
  MobileTabBar,
  NodeLegend,
  Panel,
  PasswordInput,
  Ruler,
  ScoreRing,
  Segmented,
  Skeleton,
  SkeletonText,
  Spinner,
  TextArea,
  TextLink,
  TopNav,
  Trace,
  TraceDiagram,
  TraceProgress,
  TrustNode,
  useToast,
  type IllustrationName,
  type NodeKind,
  type TraceDiagramRow,
} from '@/ui'
import { CANDIDATE_NAV } from '@/app/nav'

/** Витрина дизайн-системы «Трасса»: все компоненты в одном месте. Сверять с Figma «v2 · Трасса» (26:14). */

function Section({ id, title, note, children }: { id: string; title: string; note?: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-h`} className="flex flex-col gap-5 border-t border-hairline pt-8">
      <div className="flex flex-col gap-1">
        <h2 id={`${id}-h`} className="t-title text-brand">
          {title}
        </h2>
        {note && <p className="t-small max-w-[760px] text-ink-3">{note}</p>}
      </div>
      {children}
    </section>
  )
}

function Swatch({ name, hex, className }: { name: string; hex: string; className: string }) {
  return (
    <div className="flex items-center gap-3">
      <span className={`h-10 w-10 shrink-0 rounded-panel ${className}`} />
      <span className="flex flex-col">
        <span className="t-label text-ink">{name}</span>
        <span className="t-small font-mono text-ink-3">{hex}</span>
      </span>
    </div>
  )
}

const KINDS: NodeKind[] = ['fsp', 'test', 'resume', 'claimed']

const REQUIREMENT_ROWS: TraceDiagramRow[] = [
  { label: 'Python', evidence: 'Python — сильный уровень', end: 'test' },
  { label: 'FastAPI', evidence: 'модуль «HTTP и API»', end: 'test' },
  { label: 'PostgreSQL', evidence: 'модуль SQL', end: 'test' },
  { label: 'Kafka', evidence: 'не подтверждена', end: 'claimed', tone: 'danger' },
  { label: 'Docker', evidence: 'нет данных', end: 'none', tone: 'muted' },
]

const MEANING_ROWS: TraceDiagramRow[] = [
  { label: '«уверенный Python»', evidence: 'Обязательно: Python', end: 'open' },
  { label: '«FastAPI, PostgreSQL»', evidence: 'Обязательно: FastAPI, PostgreSQL', end: 'open' },
  { label: '«опыт от 2 лет»', evidence: 'Грейд: Middle 0,74 или Senior 0,21', end: 'open' },
  { label: '«будут плюсом»', evidence: 'Желательно: Kafka, Docker', end: 'none' },
  { label: '«220–280 тыс. на руки»', evidence: '220 000 – 280 000 ₽ на руки', end: 'open' },
]

const HEAT: { spec: string; sub: string; cells: (number | null)[] }[] = [
  { spec: 'Backend (Python)', sub: 'тест работает', cells: [41, 96, 38, 9] },
  { spec: 'Frontend (JS/TS)', sub: 'тест работает', cells: [35, 88, 31, 7] },
  { spec: 'Fullstack', sub: 'обе квалификации', cells: [null, 12, 14, 3] },
]
const GRADE_NAMES = ['Стажёр', 'Junior', 'Middle', 'Senior']

const ILLUSTRATIONS: IllustrationName[] = ['hero_vzaimno', 'medal_fsp', 'detector', 'podium', 'empty_state', 'mutual']

export default function Kit() {
  const toast = useToast()
  const [role, setRole] = useState<'middle' | 'senior' | 'both' | null>('middle')
  const [selectedCell, setSelectedCell] = useState('0-2')
  const [checks, setChecks] = useState({ a: true, b: false, c: true })
  const [answer, setAnswer] = useState('[5, 3]')
  const [brief, setBrief] = useState('Бэкенд в команду платёжного шлюза. Уверенный Python, FastAPI, PostgreSQL, опыт от 2 лет.')
  const maxCount = 96

  return (
    <div className="mx-auto flex max-w-page flex-col gap-12 px-4 py-8 sm:px-6 lg:px-12">
      <header className="flex flex-col gap-3">
        <Logo />
        <h1 className="t-display text-brand">Витрина «Трасса»</h1>
        <p className="t-lead max-w-[720px] text-ink-2">
          Дизайн-система «Взаимно»: токены, типографика и компоненты из <code className="font-mono">src/ui</code>.
          Источник правды — Figma «v2 · Трасса» (файл rzq7Hlpmq8hTkGJQsJ7AoF, страница 26:14).
        </p>
        <nav aria-label="Разделы витрины" className="flex flex-wrap gap-x-4 gap-y-1">
          {[
            ['tokens', 'Токены'],
            ['type', 'Типографика'],
            ['buttons', 'Кнопки'],
            ['trace', 'Трасса и узлы'],
            ['ruler', 'Линейка и Score'],
            ['diagram', 'Диаграммы'],
            ['data', 'Данные'],
            ['forms', 'Формы'],
            ['nav', 'Навигация'],
            ['feedback', 'Обратная связь'],
            ['3d', '3D'],
          ].map(([id, label]) => (
            <TextLink key={id} to={`#${id}`}>
              {label}
            </TextLink>
          ))}
        </nav>
      </header>

      <Section id="tokens" title="Токены" note="Один яркий цвет — фиолетовый. Красный — только для сроков и «против». Границ нет: разделяет hairline.">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Swatch name="brand" hex="#402FFF" className="bg-brand" />
          <Swatch name="danger (сроки, «против»)" hex="#EC1D35" className="bg-danger" />
          <Swatch name="danger-text" hex="#C8102E" className="bg-danger-text" />
          <Swatch name="ink" hex="#1B1C21" className="bg-ink" />
          <Swatch name="ink-2" hex="#4A4B52" className="bg-ink-2" />
          <Swatch name="ink-3" hex="#686970" className="bg-ink-3" />
          <Swatch name="hairline" hex="#E4E4E7" className="bg-hairline" />
          <Swatch name="panel" hex="#F6F6F8" className="bg-panel" />
          <Swatch name="tint" hex="#F3F1FF" className="bg-tint" />
          <Swatch name="steel" hex="#C8C9CA" className="bg-steel" />
          <Swatch name="identicon" hex="#E6E2FF" className="bg-identicon" />
          <Swatch name="bar (гистограмма)" hex="#DCDCE2" className="bg-bar" />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="t-small text-ink-3">Теплокарта:</span>
          {[1, 2, 3, 4, 5].map((l) => (
            <span key={l} className={`h-8 w-14 rounded-cell bg-heat-${l}`} />
          ))}
        </div>
      </Section>

      <Section id="type" title="Типографика" note="JetBrains Mono — заголовки (фиолетовые) и числа; Onest — весь остальной текст. Шрифты самохостятся.">
        <div className="grid gap-6 lg:grid-cols-2">
          <div className="flex flex-col gap-4">
            <p className="t-display text-brand">Display 40/48</p>
            <p className="t-title text-ink">Title 28/34</p>
            <p className="t-num-l">83 · 36,4 · 1 214</p>
            <p className="t-num-m">542 · 97% · 13</p>
            <p className="t-code text-ink-2">print(xs[-4:1:step])</p>
          </div>
          <div className="flex flex-col gap-3">
            <p className="t-lead text-ink-2">Lead 17/26. Категории собраны из опроса и адаптивного теста, а не из текста резюме.</p>
            <p className="t-name">Name 17/24 semibold — К-4821</p>
            <p className="t-body">Body 15/22. Пишите как коллеге. Мы покажем, как поняли каждое слово.</p>
            <p className="t-body-strong">Body Strong 15/22</p>
            <p className="t-label">Label 14/20 medium</p>
            <p className="t-small text-ink-3">Small 13/18 — подписи и пояснения</p>
          </div>
        </div>
      </Section>

      <Section id="buttons" title="Кнопки" note="Пилюли (radius 999). Primary — заливка, Outline — контур 1,5 px, Text — второстепенное действие.">
        <div className="flex flex-wrap items-center gap-4">
          <Button>Пригласить</Button>
          <Button variant="outline">В шортлист</Button>
          <Button variant="text">Сохранить черновик</Button>
          <Button size="sm">Middle</Button>
          <Button size="lg">Показать подборку</Button>
          <Button loading>Отправляем</Button>
          <Button disabled>Недоступно</Button>
          <Button variant="outline" disabled>
            Недоступно
          </Button>
        </div>
        <div className="max-w-[360px]">
          <Button size="lg" block>
            Ответить
          </Button>
        </div>
      </Section>

      <Section id="trace" title="Трасса и узлы доверия" note="Форма узла — уровень доверия: залитый — ФСП, кольцо с точкой — тест, пустое — резюме, пунктир — заявлено.">
        <div className="grid gap-6 lg:grid-cols-2">
          <div className="flex flex-col gap-4">
            <p className="t-label">Узлы (14 px и 20 px)</p>
            <div className="flex flex-wrap items-center gap-6">
              {KINDS.map((k) => (
                <span key={k} className="flex items-center gap-2 t-small text-ink-2">
                  <TrustNode kind={k} /> <TrustNode kind={k} size={20} /> {k}
                </span>
              ))}
            </div>
            <NodeLegend />
          </div>
          <div className="flex flex-col gap-5">
            <div>
              <p className="mb-2 t-label">Подчёркивание пункта меню (40 × 6)</p>
              <Trace className="w-10" height={6} />
            </div>
            <div>
              <p className="mb-2 t-label">Версии подборки: первая → текущая</p>
              <Trace className="w-24" height={12} inset={6} nodes={[{ at: 0, variant: 'open' }, { at: 1 }]} />
            </div>
            <div>
              <p className="mb-2 t-label">Прогресс теста (7-е задание из ~15)</p>
              <TraceProgress done={6} estimatedTotal={15} className="max-w-[312px]" />
              <p className="mt-1 t-small text-ink-2">Задание 7 из примерно 12–15</p>
            </div>
            <div>
              <p className="mb-2 t-label">Пунктир — «ещё впереди»</p>
              <Trace
                height={14}
                inset={6}
                nodes={[{ at: 0, variant: 'muted' }, { at: 1, variant: 'muted' }]}
                segments={[{ from: 0, to: 1, dashed: true }]}
                className="max-w-[312px]"
              />
            </div>
          </div>
        </div>
      </Section>

      <Section id="ruler" title="Линейка грейдов и Score" note="Шкала 300–600: S = 500 + 100·θ; узлы — границы грейдов (300 / 400 / 500 / 600). Отрезок — интервал, точка — оценка.">
        <div className="grid gap-8 lg:grid-cols-2">
          <div className="flex flex-col gap-5">
            <p className="t-label">Compact (строка таблицы, 176 px)</p>
            {[
              { v: 542, i: [500, 584] as const, c: 'К-4821' },
              { v: 561, i: [530, 590] as const, c: 'К-1290' },
              { v: 612, i: [585, 600] as const, c: 'К-7735' },
            ].map((r) => (
              <div key={r.c} className="flex items-center gap-3">
                <span className="w-16 t-name">{r.c}</span>
                <div className="w-44">
                  <Ruler value={r.v} interval={r.i} />
                  <span className="t-small text-ink-3">{r.v}</span>
                </div>
              </div>
            ))}
          </div>
          <div className="flex max-w-[312px] flex-col gap-5">
            <p className="t-label">Full (результат теста)</p>
            <Ruler size="full" value={542} interval={[500, 584]} />
            <Ruler size="full" value={390} interval={[340, 440]} showValue />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-8">
          <div className="flex flex-col items-center gap-2">
            <ScoreRing value={83} />
            <span className="t-small text-ink-3">ScoreRing 88</span>
          </div>
          <div className="flex flex-col items-center gap-2">
            <ScoreRing value={47} size={64} />
            <span className="t-small text-ink-3">64</span>
          </div>
          <div className="flex flex-col items-center gap-2">
            <ScoreRing value={100} size={120} />
            <span className="t-small text-ink-3">120</span>
          </div>
        </div>
      </Section>

      <Section id="diagram" title="Диаграммы «требование → доказательство»" note="Слева подписи, справа свидетельства, между ними трасса к узлу доверия. Красный текст — «против», серый — «нет данных».">
        <div className="grid gap-6 lg:grid-cols-2">
          <Panel radius="md">
            <p className="mb-4 t-body-strong">Что вы просили и чем это подтверждено</p>
            <TraceDiagram rows={REQUIREMENT_ROWS} />
            <NodeLegend className="mt-5" surface="#F6F6F8" />
          </Panel>
          <Panel radius="xl">
            <p className="mb-4 t-body-strong">Как мы поняли</p>
            <TraceDiagram rows={MEANING_ROWS} monoLabels labelWidth={200} rowHeight={44} />
          </Panel>
        </div>
      </Section>

      <Section id="data" title="Данные" note="Идентикон, счётчики, теплокарта талантов (число — размер категории, мини-трасса — доля ФСП).">
        <div className="flex flex-wrap items-center gap-4">
          {['К-4821', 'К-1290', 'К-5512', 'К-7735', 'К-3058', 'К-6105'].map((s) => (
            <span key={s} className="flex items-center gap-2 t-label">
              <Identicon seed={s} />
              {s}
            </span>
          ))}
          <Identicon seed="К-4821" size={56} />
        </div>
        <Counters
          items={[
            { value: 38, label: 'в категории Backend Middle' },
            { value: 29, label: 'подходят по вашей вилке' },
            { value: 11, label: 'с достижениями ФСП' },
            { value: 2, label: 'новых с прошлой версии', tone: 'brand' },
          ]}
        />
        <div className="flex max-w-[808px] flex-col gap-1.5">
          <div className="grid grid-cols-[110px_repeat(4,minmax(0,1fr))] gap-1.5 sm:grid-cols-[220px_repeat(4,minmax(0,1fr))] t-small text-ink-3">
            <span />
            {GRADE_NAMES.map((g) => (
              <span key={g}>{g}</span>
            ))}
          </div>
          {HEAT.map((row, r) => (
            <div key={row.spec} className="grid grid-cols-[110px_repeat(4,minmax(0,1fr))] items-center gap-1.5 sm:grid-cols-[220px_repeat(4,minmax(0,1fr))]">
              <div className="min-w-0">
                <p className="t-body-strong truncate">{row.spec}</p>
                <p className="t-small text-ink-3">{row.sub}</p>
              </div>
              {row.cells.map((n, c) => (
                <HeatCell
                  key={c}
                  count={n ?? 0}
                  level={n == null ? 0 : heatLevel(n, maxCount)}
                  fspShare={n == null ? undefined : 0.2 + ((r + c) % 4) * 0.2}
                  selected={selectedCell === `${r}-${c}`}
                  onClick={() => setSelectedCell(`${r}-${c}`)}
                  label={`${row.spec}, ${GRADE_NAMES[c]}: ${n ?? 0} кандидатов`}
                />
              ))}
            </div>
          ))}
          <HeatLegend className="pt-2" />
        </div>
      </Section>

      <Section id="forms" title="Формы" note="Рамка 1 px steel, в фокусе 1,5 px brand. Ошибки — текстом под полем, не только цветом.">
        <div className="grid gap-8 lg:grid-cols-2">
          <div className="flex max-w-[440px] flex-col gap-5">
            <Input label="Email" type="email" placeholder="you@company.example" hint="На этот адрес придёт 6-значный код." />
            <PasswordInput label="Пароль" defaultValue="Kod-2026-ok" />
            <Input label="Ваш ответ" mono value={answer} onChange={(e) => setAnswer(e.target.value)} hint="Вывод программы одной строкой, пробелы не важны." />
            <Input label="Код" mono error="Неверный код. Осталось попыток: 4." defaultValue="042917" />
            <Input label="Недоступно" disabled defaultValue="Только чтение" />
            <TextArea label="Комментарий" placeholder="Коротко опишите, что важно" />
          </div>
          <div className="flex flex-col gap-6">
            <TextArea
              variant="panel"
              aria-label="Описание потребности"
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
              footerLeft={`${brief.length} из 2000 знаков`}
              footerRight="разобрано правилами, без нейросети"
            />
            <div className="flex flex-col gap-2">
              <p className="t-body-strong">Уточните одну вещь</p>
              <Segmented
                aria-label="Кого показывать первыми"
                value={role}
                onChange={setRole}
                options={[
                  { value: 'middle', label: 'Middle' },
                  { value: 'senior', label: 'Senior' },
                  { value: 'both', label: 'Обоих' },
                ]}
              />
            </div>
            <div className="flex flex-wrap gap-x-6 gap-y-1">
              <Checkbox label="Middle 38" checked={checks.a} onChange={(e) => setChecks({ ...checks, a: e.target.checked })} />
              <Checkbox label="Junior 41" muted checked={checks.b} onChange={(e) => setChecks({ ...checks, b: e.target.checked })} />
              <Checkbox label="Только подтверждённые навыки" checked={checks.c} onChange={(e) => setChecks({ ...checks, c: e.target.checked })} />
              <Checkbox label="С ошибкой" error="Нужно согласие" />
            </div>
          </div>
        </div>
      </Section>

      <Section id="nav" title="Навигация" note="Десктопная шапка (работодатель, лаборатория) и мобильный каркас кандидата. Активный пункт здесь — «Приглашения» (ведёт на эту же страницу).">
        <div className="overflow-hidden rounded-panel border border-hairline">
          <TopNav
            items={EMPLOYER_NAV.map((i) => (i.to === '/employer/invitations' ? { ...i, to: '/dev/kit', badge: 2 } : i))}
            account={{ name: 'Северный код', caption: '57 из 60 приглашений на неделе' }}
          />
        </div>
        <div className="flex flex-wrap gap-6">
          <div className="flex w-[360px] max-w-full flex-col overflow-hidden rounded-panel-xl border border-hairline bg-white">
            <div className="px-6 pt-5">
              <MobileHeader title="Приглашение" backTo="/candidate" />
              <p className="t-small text-ink-3">Вы и Северный код</p>
              <p className="t-display text-brand">Взаимно</p>
              <p className="mt-3 mb-5 t-lead">Вы приняли условия, компания увидела ваше имя и контакты.</p>
            </div>
            <MobileTabBar items={CANDIDATE_NAV.map((i) => (i.to === '/candidate/invitations' ? { ...i, to: '/dev/kit' } : i))} />
          </div>
          <div className="flex w-[360px] max-w-full flex-col gap-3 rounded-panel-xl border border-hairline bg-white px-6 pb-6 pt-5">
            <MobileHeader title="Тест · Backend (Python)" backTo="/candidate" right={<span className="t-num-m">01:12</span>} className="mb-2" />
            <TraceProgress done={6} />
            <div className="flex justify-between t-small">
              <span className="text-ink-2">Задание 7 из примерно 12–15</span>
              <span className="text-ink-3">вариант 7F3A</span>
            </div>
            <p className="t-name">Что выведет программа?</p>
            <div className="rounded-panel bg-code-bg px-4 py-3.5 t-code text-white">
              {['xs = list(range(9))', 'step = -2', 'print(xs[-4:1:step])'].map((l, i) => (
                <div key={i} className="flex gap-4">
                  <span className="text-code-dim">{i + 1}</span>
                  <span>{l}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </Section>

      <Section id="feedback" title="Обратная связь и состояния" note="Тосты — единственные элементы с тенью (плавающие). Пустое состояние всегда с 3D и одним действием.">
        <div className="flex flex-wrap items-center gap-3">
          <Button size="sm" variant="outline" onClick={() => toast.success('Приглашение отправлено. Ответ придёт в течение 4 дней.', 'Готово')}>
            Тост: успех
          </Button>
          <Button size="sm" variant="outline" onClick={() => toast.error('Не удалось сохранить. Повторите через минуту.')}>
            Тост: ошибка
          </Button>
          <Button size="sm" variant="outline" onClick={() => toast.show({ message: 'Подборка обновлена: двое новых, четверо скрыты.' })}>
            Тост: обычный
          </Button>
          <Spinner label="Загрузка" />
        </div>
        <div className="grid max-w-[760px] gap-3">
          <Alert>Контакты кандидатов откроются, только когда они ответят «да».</Alert>
          <Alert tone="danger" title="Ответить в течение 2 дней">
            Если не ответить в срок, приглашение истечёт.
          </Alert>
        </div>
        <div className="grid gap-6 lg:grid-cols-2">
          <Panel variant="outline" radius="xl" padding="md">
            <EmptyState
              title="Пока никого нет"
              description="Опишите потребность текстом — мы предложим категории и построим подборку."
              action={<Button>Описать потребность</Button>}
            />
          </Panel>
          <Panel variant="panel" radius="xl" padding="md" className="flex flex-col gap-4">
            <p className="t-label text-ink-3">Skeleton</p>
            <div className="flex items-center gap-3">
              <Skeleton circle className="h-7 w-7" />
              <SkeletonText lines={2} className="flex-1" />
              <Skeleton className="h-6 w-12" />
            </div>
            <SkeletonText lines={3} />
          </Panel>
        </div>
        <div className="grid gap-6 lg:grid-cols-2">
          <Panel variant="hero" radius="hero" padding="lg">
            <p className="t-small text-ink-3">Hero-панель (единственный градиент)</p>
            <p className="t-display mt-2 text-ink">1 214 специалистов.</p>
            <p className="t-display text-brand">612 подтвердили уровень тестом.</p>
          </Panel>
          <Panel variant="tint" radius="xl" padding="md">
            <p className="t-body-strong">Tint-панель: «Согласиться и открыть контакты»</p>
            <p className="mt-1 t-body text-ink-2">Условия зафиксируются новой версией, а имя и контакты откроются сразу.</p>
            <p className="mt-2 t-small text-danger-text">Ответить в течение 2 дней</p>
          </Panel>
        </div>
      </Section>

      <Section id="3d" title="3D-иллюстрации" note="WebP с прозрачностью, ≤ 300 КБ, лежат в public/3d.">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
          {ILLUSTRATIONS.map((n) => (
            <figure key={n} className="m-0 flex flex-col items-center gap-2 rounded-panel bg-panel p-3">
              <Illustration name={n} className="h-28 w-full" />
              <figcaption className="t-small font-mono text-ink-3">{n}</figcaption>
            </figure>
          ))}
        </div>
      </Section>
    </div>
  )
}
