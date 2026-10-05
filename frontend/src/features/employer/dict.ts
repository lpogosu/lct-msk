// Справочники и подписи кабинета работодателя. Коды — из config/taxonomy.yaml и config/contact.yaml бэкенда,
// тексты — для интерфейса (всё по-русски). Числа и пороги здесь не живут: их считает бэкенд.

export const GRADE_LABEL: Record<string, string> = {
  intern: 'Стажёр',
  junior: 'Junior',
  middle: 'Middle',
  senior: 'Senior',
}

export const GRADE_ORDER = ['intern', 'junior', 'middle', 'senior'] as const

export const SPEC_LABEL: Record<string, string> = {
  backend_python: 'Backend (Python)',
  frontend_js: 'Frontend (JS/TS)',
  fullstack: 'Fullstack',
  data_ml: 'Data Science / ML',
  devops: 'DevOps / SRE',
  qa: 'QA / тестирование',
  mobile: 'Mobile',
  infosec: 'Информационная безопасность',
  backend_go: 'Backend (Go)',
  backend_java: 'Backend (Java/Kotlin)',
}

/** Порядок строк карты талантов: сначала направления с тестом, затем «ждут теста». */
export const MAP_SPECS = [
  'backend_python',
  'frontend_js',
  'fullstack',
  'devops',
  'qa',
  'data_ml',
  'mobile',
  'infosec',
  'backend_go',
  'backend_java',
] as const

export const FORMAT_LABEL: Record<string, string> = {
  office: 'Офис',
  hybrid: 'Гибрид',
  remote: 'Удалённо',
}

export const FORMAT_LABEL_LOWER: Record<string, string> = {
  office: 'офис',
  hybrid: 'гибрид',
  remote: 'удалённо',
}

export const INDUSTRY_LABEL: Record<string, string> = {
  fintech: 'Финтех',
  ecommerce: 'E-commerce и ритейл',
  gov: 'Госсектор',
  industry: 'Промышленность',
  telecom: 'Телеком',
  media_games: 'Медиа и игры',
  edtech: 'Образование',
  medtech: 'Медицина',
  logistics: 'Логистика',
  other: 'Другое',
}

export const SEARCH_MODE_LABEL: Record<string, string> = {
  active: 'ищет активно',
  open: 'открыт к предложениям',
}

/** Тексты для фильтров подборки (коды — FilterCode бэкенда). Короткие, как переключатели в Figma 27:2. */
export const FILTER_TOGGLE_LABEL = {
  must_confirmed: 'Только подтверждённые навыки',
  has_fsp: 'С ФСП',
  salary_fit: 'Ожидания в вилке',
  format: 'Подходящий формат',
  city: 'Город или релокация',
  active: 'Активные за 30 дней',
  confirmed_only: 'Только подтверждённые тестом',
} as const

export type ToggleFilterCode = keyof typeof FILTER_TOGGLE_LABEL

/** Компоненты Score: порядок и названия на «вы» (Figma 28:5, декомпозиция). */
export const SCORE_PARTS = [
  { key: 'T', label: 'Тест', hint: 'Уровень по адаптивному тесту' },
  { key: 'K', label: 'Стек под вашу потребность', hint: 'Обязательные и желательные навыки брифа' },
  { key: 'F', label: 'Достижения ФСП', hint: 'Если истории нет — 0, это не штраф' },
  { key: 'A', label: 'Свежесть', hint: 'Недавняя активность и задачи' },
  { key: 'U', label: 'Условия', hint: 'Вилка, формат, город' },
] as const

/** Балл банка: 0,55·T + 0,25·F + 0,20·A (config/scoring.yaml: bank.weights) — без навыков брифа и условий. */
export const BANK_PARTS = [
  { key: 'T', label: 'Тест', hint: 'Уровень по адаптивному тесту' },
  { key: 'F', label: 'Достижения ФСП', hint: 'Если истории нет — 0, это не штраф' },
  { key: 'A', label: 'Свежесть', hint: 'Недавняя активность и задачи' },
] as const

export const INVITATION_STATUS_TONE: Record<string, 'brand' | 'ink' | 'danger' | 'muted'> = {
  sent: 'ink',
  viewed: 'ink',
  countered: 'brand',
  accepted: 'brand',
  declined: 'muted',
  expired: 'muted',
  withdrawn: 'muted',
}

/** Причины отказа работодателя на встречное условие (POST /invitations/{id}/counter/decline). */
export const COUNTER_DECLINE_REASONS = [
  { code: 'salary', label: 'Зарплата выше бюджета' },
  { code: 'format', label: 'Формат не подходит' },
  { code: 'start_date', label: 'Дата выхода не подходит' },
  { code: 'other', label: 'Другая причина' },
] as const

/** Причины отзыва приглашения (POST /invitations/{id}/withdraw). */
export const WITHDRAW_REASONS = [
  { code: 'position_closed', label: 'Позиция закрыта' },
  { code: 'chose_other', label: 'Выбрали другого' },
  { code: 'sent_by_mistake', label: 'Отправлено по ошибке' },
  { code: 'other', label: 'Другая причина' },
] as const

/** Итог «не сложилось» (POST /invitations/{id}/outcome). */
export const NOT_MATCHED_REASONS = [
  { code: 'skills_missing', label: 'Не хватило навыков' },
  { code: 'position_closed', label: 'Позиция закрыта' },
  { code: 'chose_other', label: 'Выбрали другого' },
] as const

/** Причины отказа отклику (POST /applications/{id}/reject). */
export const REJECT_REASONS = [
  { code: 'skills_missing', label: 'Не хватило навыков' },
  { code: 'salary', label: 'Ожидания по зарплате' },
  { code: 'position_closed', label: 'Позиция закрыта' },
  { code: 'chose_other', label: 'Выбрали другого' },
  { code: 'other', label: 'Другая причина' },
] as const

export const RESPONSE_DAYS = [3, 5, 7] as const

export const NEED_STATUS_LABEL: Record<string, string> = {
  draft: 'Черновик',
  parsed: 'Черновик',
  active: 'Ищем',
  paused: 'На паузе',
  closed: 'Закрыта',
}

export const CATEGORY_KIND_LABEL: Record<string, string> = {
  primary: 'основная',
  suggested: 'предложена',
  adjacent: 'смежная',
  manual: 'вручную',
}

export function specLabel(code: string | null | undefined): string {
  return code ? (SPEC_LABEL[code] ?? code) : '—'
}

export function gradeLabel(code: string | null | undefined): string {
  return code ? (GRADE_LABEL[code] ?? code) : '—'
}

/** Код категории «spec:grade» → «Backend (Python) · Middle» */
export function categoryName(spec: string, grade: string): string {
  return `${specLabel(spec)} · ${gradeLabel(grade)}`
}

/** Навыки для фильтра «стек» в банке: только те, что подтверждает тест (config/skills.yaml — у них есть модули). */
export const STACK_OPTIONS: { id: string; name: string }[] = [
  { id: 'python', name: 'Python' },
  { id: 'fastapi', name: 'FastAPI' },
  { id: 'django', name: 'Django' },
  { id: 'flask', name: 'Flask' },
  { id: 'asyncio', name: 'asyncio' },
  { id: 'sqlalchemy', name: 'SQLAlchemy' },
  { id: 'pytest', name: 'pytest' },
  { id: 'sql', name: 'SQL' },
  { id: 'postgresql', name: 'PostgreSQL' },
  { id: 'rest', name: 'REST API' },
  { id: 'graphql', name: 'GraphQL' },
  { id: 'javascript', name: 'JavaScript' },
  { id: 'typescript', name: 'TypeScript' },
  { id: 'react', name: 'React' },
  { id: 'nextjs', name: 'Next.js' },
  { id: 'vue', name: 'Vue' },
  { id: 'nodejs', name: 'Node.js' },
  { id: 'html', name: 'HTML' },
  { id: 'css', name: 'CSS' },
  { id: 'git', name: 'Git' },
  { id: 'linux', name: 'Linux' },
  { id: 'algorithms', name: 'Алгоритмы' },
]

/** Города из справочника брифа (config/skills.yaml → cities): код → название. Остальные показываем как есть. */
export const CITY_LABEL: Record<string, string> = {
  moscow: 'Москва',
  spb: 'Санкт-Петербург',
  novosibirsk: 'Новосибирск',
  ekaterinburg: 'Екатеринбург',
  kazan: 'Казань',
  nizhny_novgorod: 'Нижний Новгород',
  samara: 'Самара',
  rostov: 'Ростов-на-Дону',
  krasnodar: 'Краснодар',
  innopolis: 'Иннополис',
}

export function cityLabel(code: string | null | undefined): string {
  return code ? (CITY_LABEL[code] ?? code) : ''
}
