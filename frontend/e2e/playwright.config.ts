import { defineConfig, devices } from '@playwright/test'

// Сквозные смоук-тесты против запущенного стека (бэкенд в DEMO_MODE + интерфейс).
// Адрес — E2E_BASE_URL (по умолчанию Vite на :5185; в CI — тот же Vite, проксирующий /api в бэкенд).
// Стек поднимают `make e2e` (локально) или job e2e в .github/workflows/ci.yml; сами тесты ничего не запускают.
const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost:5185'

export default defineConfig({
  testDir: './tests',
  globalSetup: './global-setup.ts',
  // Тесты делят одну БД и одни демо-часы; последовательный прогон надёжнее параллельного.
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL,
    locale: 'ru-RU',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
