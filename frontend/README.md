# «Взаимно» — фронтенд

React 18 + Vite 7 + TypeScript + Tailwind 3 + React Router 7 + TanStack Query 5. Типизированный клиент API
(`openapi-fetch` + `openapi-typescript`). Дизайн — стиль «Трасса» (Figma, файл `rzq7Hlpmq8hTkGJQsJ7AoF`,
страница «v2 · Трасса», id `26:14`).

## Запуск

```powershell
# 1. БД и почта (из корня репозитория)
docker compose up -d db mailpit

# 2. Бэкенд
cd backend; uv run uvicorn app.main:app --port 8000

# 3. Фронтенд
cd frontend
npm install
npm run dev        # http://localhost:5173 (порт строго 5173: так настроен CORS бэкенда)
```

Vite проксирует `/api` → `http://localhost:8000` (другой адрес: `VITE_API_PROXY_TARGET`, см. `.env.example`),
поэтому в браузере всё идёт с одного origin и refresh-cookie `vz_refresh` работает без CORS.
Письма с кодами в dev — в Mailpit: http://localhost:8025.

| Команда | Что делает |
|---|---|
| `npm run dev` | dev-сервер |
| `npm run build` | `tsc -b` + продакшн-сборка в `dist/` |
| `npm run typecheck` | проверка типов |
| `npm run lint` | ESLint (0 ошибок и предупреждений — норма) |
| `npm run gen:api` | заново сгенерировать типы из OpenAPI бэкенда (бэкенд должен быть запущен) |
| `npm run optimize:3d` | пересобрать `public/3d/*.webp` из `_internal/design/assets3d` (нужен только если рендеры изменились) |

**`npm run gen:api`** берёт `http://localhost:8000/api/openapi.json` (схема лежит по `/api/openapi.json`, не `/openapi.json`),
пишет `src/api/openapi.json` (снимок) и `src/api/schema.d.ts` (типы). Оба файла в git: сборка не требует бэкенда.
Другие источники: `OPENAPI_URL=…` или `OPENAPI_FILE=путь.json`. **Перегенерируйте типы, когда бэкенд-агент
дописал свой раздел** — пока многие разделы бэкенда это заглушки `501 not_implemented`, и в схеме для них нет
типизированных путей.

## Структура

```
frontend/
  public/
    fonts/            Onest + JetBrains Mono (woff2, OFL, лицензии рядом); CDN шрифтов не используется
    3d/               6 WebP-рендеров ≤ 300 КБ: hero_vzaimno, medal_fsp, detector, podium, empty_state, mutual
  scripts/            gen-api.mjs, optimize-3d.mjs
  src/
    api/              клиент и сессия (см. ниже)
    app/              routes.tsx (КАРТА МАРШРУТОВ), nav.ts (пункты меню по ролям)
    auth/             useAuth, guards (RequireRole, RedirectIfAuthed), signupFlow
    hooks/            useCountdown
    layouts/          RootLayout, PublicLayout, CandidateLayout (мобильный), EmployerLayout, LabLayout
    lib/              cn, format (числа/даты по-русски), grades (шкала 300–600), hash
    pages/            Landing, auth/*, candidate/*, dev/Kit, NotFound
    ui/               дизайн-система «Трасса» (реэкспорт: `@/ui`)
    index.css, styles/fonts.css
  tailwind.config.ts  токены и типографическая шкала
```

Алиас `@` → `src`. Витрина всех компонентов — **http://localhost:5173/dev/kit** (сверяйте с Figma).

## Токены

Заданы в `tailwind.config.ts`, цвета в коде не пишем — только токены.

| Токен | Значение | Где |
|---|---|---|
| `brand` | `#402FFF` | единственный яркий цвет; заголовки-display, кнопки, трассы |
| `danger` / `danger-text` | `#EC1D35` / `#C8102E` | **только** сроки и «против» (текст — `danger-text`) |
| `ink`, `ink-2`, `ink-3` | `#1B1C21`, `#4A4B52`, `#7A7B82` | текст |
| `hairline`, `panel`, `tint`, `steel` | `#E4E4E7`, `#F6F6F8`, `#F3F1FF`, `#C8C9CA` | разделители, плашки, неактивное |
| `heat-1…5` | `#F1EFFF … #402FFF` | теплокарта |

Типографика — классы `t-*` (шкала Figma `v2/*`): `t-display` 40/48, `t-title` 28/34 (оба JetBrains Mono, фиолетовые
на экране), `t-num-l` 32, `t-num-m` 20 (числа, mono), `t-lead` 17, `t-name` 17 semibold, `t-body` 15,
`t-body-strong`, `t-label` 14 medium, `t-small` 13, `t-code` 14 mono. На экранах < 640 px `display` и `title` уменьшаются.
Правила: белые страницы, границы заменяем hairline, теней нет (кроме плавающих — `shadow-float`: тосты, поповеры),
градиент один — hero-панель (`<Panel variant="hero">`).

## Компоненты (`import { … } from '@/ui'`)

| Компонент | Назначение / Figma |
|---|---|
| `Button` (`primary`/`outline`/`text`, `sm`/`md`/`lg`, `block`, `loading`, `to`/`href`) | пилюли; `to` — ссылка роутера |
| `Trace`, `TraceProgress` | трасса 1,5 px с узлами (подчёркивание меню, версии подборки `27:39`, прогресс теста `34:9`) |
| `TrustNode`, `NodeLegend` | узел доверия: залитый = ФСП, кольцо с точкой = тест, пустое = резюме, пунктир = заявлено |
| `Ruler` (`compact`/`full`) | линейка 300–600 с интервалом и баллом (`27:83`, `34:42`) |
| `ScoreRing` | кольцо Score (`32:91`) |
| `TraceDiagram` | «требование → доказательство» (`28:51`) и «Как мы поняли» (`40:83`, `monoLabels`) |
| `Identicon` | узор 5×5 из строки (`К-4821`) |
| `Counters` | числа с hairline-разделителями (`28:238`) |
| `HeatCell`, `HeatLegend`, `heatLevel` | карта талантов (`33:46`) |
| `Panel` (`panel`/`tint`/`hero`/`outline`, радиусы 12/16/20/24) | поверхности |
| `TopNav` | шапка работодателя/лаборатории (`27:3`), активный пункт с трассой, на < 1024 — меню |
| `MobileShell`, `MobileHeader`, `MobileTabBar` | каркас экрана кандидата 360–430 px (`29:3`, `34:3`) |
| `Input`, `PasswordInput`, `TextArea` (`field`/`panel`), `Checkbox`, `Segmented` | формы; ошибки текстом под полем |
| `Toast` (`useToast`), `Alert`, `Spinner`, `Skeleton`, `EmptyState`, `Illustration`, `WorkInProgress` | состояния |
| `Logo`, `TextLink` | бренд, ссылки |

Новые компоненты кладите в `src/ui/`, экспортируйте из `index.ts` и **добавляйте в `/dev/kit`**.

## API и авторизация

```ts
import { useQuery, useMutation } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'

// чтение: unwrap бросает ApiException (русское message, code, details) — TanStack Query её подхватит
const consents = useQuery({
  queryKey: ['consents'],
  queryFn: () => unwrap(api.GET('/api/consents')),
})

// запись
const grant = useMutation({
  mutationFn: (purpose: 'blind_card') => unwrap(api.POST('/api/consents', { body: { purpose, text_version: null } })),
})
```

* Пути в типах начинаются с `/api/…`. Типы схем: `import type { Schemas } from '@/api/types'` → `Schemas['UserOut']`.
* Ошибка бэкенда `{code, message, details}` → `ApiException`: `err.message` (готово к показу), `err.code`,
  `err.fieldIssues` (422 по полям), `err.retryAfterS`, `err.attemptsLeft`, `err.isNotImplemented` (501 —
  покажите `WorkInProgress`, а не ошибку). `errorMessage(e)` — текст из любой ошибки.
* **Сессия**: access-JWT лежит только в памяти модуля (`src/api/session.ts`), refresh — httpOnly-cookie
  `vz_refresh` (путь `/api/auth`, ротация при каждом обновлении). `api` сам подставляет `Authorization: Bearer`,
  на `401` один раз вызывает `/api/auth/refresh` и повторяет запрос; параллельные обновления делят один запрос;
  `409 refresh_superseded` (другая вкладка уже обновила) повторяется. Токен обновляется заранее за 30 с до
  истечения. При старте приложения `AuthBootstrap` пробует refresh (если раньше входили — флаг-подсказка в
  localStorage, не токен).
* **Поток**: `register` → письмо с 6-значным кодом → `verify-email` (код **и пароль** из регистрации; пароль
  хранится только в памяти вкладки, при перезагрузке/ссылке из письма форма спросит его заново) → сессия;
  `login` → сессия; `logout`. Методы — `authApi` в `src/api/auth.ts`; состояние — `useAuth()`
  (`status`, `user`, `role`).
* Ссылки из писем бэкенда: `/verify-email?email=…&code=…`, `/reset-password?email=…&code=…`, QR-сверка PDF —
  `/v/{code}`. Эти адреса зашиты в `backend/app/mail/templates.py` — не переименовывать.
* Роли и кабинеты: `candidate` → `/candidate` (mobile-first), `employer` → `/employer` (десктоп),
  `methodist`/`admin` → `/lab`. `RequireRole` отправит гостя на вход, чужую роль — в её кабинет.

## Как добавить экран (для экран-агентов)

1. Найдите маршрут-заглушку в `src/app/routes.tsx` (`wip('…', …, '27:2 …')`) — рядом указан узел Figma.
2. Создайте `src/pages/<роль>/<Экран>.tsx` (default export), подключите `lazy: async () => ({ Component: (await import('…')).default })`,
   сохранив `handle: { title }` (заголовок вкладки; `hideNav: true` скрывает нижние вкладки кандидата).
3. Откройте узел через `get_design_context` (Figma MCP), соберите из компонентов `@/ui`, а не из сырой разметки.
4. Верстка: кандидат — внутри `CandidateLayout` (экран рисует свой `<MobileHeader title backTo />`), работодатель/лаборатория —
   контент в контейнере `EmployerLayout`/`LabLayout` (до 1440 px, поля 48 px на десктопе).
5. Данные — через `unwrap(api…)` и TanStack Query; пустое/загрузка/ошибка — `EmptyState`, `Skeleton`, `Alert`.
6. Весь текст интерфейса — по-русски, без lorem; недоделанное не оставляйте «мёртвыми» кнопками — скрывайте или ведите в
   `WorkInProgress`.
7. Резюме и самоописание — только серые «заявлено»-узлы (`TrustNode kind="claimed"`), на Score они не влияют; отсутствие истории ФСП — не «против»
   (красный — только сроки и «против»).
8. Контакты кандидата работодателю — только после взаимного «да» (DTO `CandidateBlind` vs `CandidateDisclosed`).

### Карта «экран → маршрут → узел Figma»

| Экран | Маршрут | Figma |
|---|---|---|
| Р5 «Подборка» (таблица, фильтры, панель кандидата) | `/employer/needs/:id` | `27:2` (таблица `27:70`, панель `28:5`, версии `27:37`, фильтры `28:238`) |
| Карта талантов | `/employer/bank` | `33:2` (герой `46:142`, карта `33:35`, панель категории `33:305`) |
| Р3 «Опишите, кого ищете» (бриф) | `/employer/needs/new` | `40:2` («Как мы поняли» `40:81`) |
| Р8 «Приглашения» | `/employer/invitations` | `42:2` |
| Акт сверки (Антиподсказчик) | `/lab/reconciliations` | `39:2` |
| К4 Тест | `/candidate/test` | `34:2` |
| К5 Результат теста | `/candidate/result` | `34:42` |
| К10 Приглашение | `/candidate/invitations/:id` | `29:2` |
| К10 «Взаимно» (после взаимного «да») | то же, состояние после ответа | `29:48` |
| 3D-ассеты | `public/3d/*.webp` | `43:2`, `43:142–145`, `46:2` |

Навигация (`src/app/nav.ts`) повторяет шапки Figma: работодатель — «Потребности · Банк кандидатов · Приглашения · Вакансии · Задачи»,
лаборатория — «Сессии · Банк заданий · Сверки · Валидация · ИИ-стенд». На кадре `33:2` второй пункт назван «Карта талантов», на
`39:2`/`40:2`/`42:2` — «Банк кандидатов»: принят второй вариант, а карта — главный экран этого раздела.

## Качество

* `npm run build`, `npm run lint`, `npm run typecheck` проходят.
* Адаптив: проверено от 360 px (без горизонтальной прокрутки); у `TraceDiagram` на телефоне подпись переносится над трассой.
* Фокус с клавиатуры виден везде (фиолетовый контур 2 px, глобально в `index.css`); есть ссылка «К содержимому».
* `prefers-reduced-motion`: анимации и плавная прокрутка отключаются глобально в `index.css`.
* Шрифты самохостятся; `<link rel="preload">` для кириллических начертаний.

## Отступления от задачи и ограничения

* shadcn/ui из `CLAUDE.md` не подключён: дизайн-система «Трасса» самодостаточна, на нативных элементах (a11y из коробки).
  Для сложных виджетов (диалог, поповер) можно добавить Radix — лицензия MIT.
* Версии: Vite 7, ESLint 9, TypeScript 5.9, Tailwind 3.4 (не 4) — ради стабильности экосистемы.
* Названия компании и квоты приглашений («Северный код · 57 из 60 на неделе») в шапке работодателя пока заглушены email-ом:
  подключит экран «Потребности» через `/api/companies`.
* Размер чекбокса 14 px как в Figma; зона нажатия — вся строка с подписью.
