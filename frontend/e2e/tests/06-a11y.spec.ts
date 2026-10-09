import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { demoAccount, login, submitSandboxWithTwinTrace } from './helpers'

// Автопроверка доступности (axe-core, WCAG 2.1 A/AA): на ключевых экранах не должно быть нарушений
// уровня serious/critical. Отчёт и список остаточного — docs/ACCESSIBILITY.md.
async function expectNoSeriousViolations(page: Page, where: string) {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
  const bad = r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
  expect(bad.map((v) => `${where}: ${v.id} (${v.nodes.length}) ${v.nodes[0]?.target.join(' ')}`)).toEqual([])
}

// Для песочницы (акт сверки): чтение буфера обмена
test.use({ permissions: ['clipboard-read', 'clipboard-write'] })

const settle = async (page: Page) => {
  await page.waitForLoadState('networkidle', { timeout: 8_000 }).catch(() => {})
  await page.waitForFunction(() => !document.querySelector('[class*="animate-shimmer"]'), null, { timeout: 15_000 }).catch(() => {})
}

test('a11y: публичные экраны', async ({ page }) => {
  for (const path of ['/', '/auth/login', '/jury']) {
    await page.goto(path)
    await settle(page)
    await expectNoSeriousViolations(page, path)
  }
})

test('a11y: кандидат — плеер теста', async ({ page }) => {
  const mid = demoAccount('anna@demo.example.com')
  await login(page, mid.email, mid.password)
  // «Главная» ведёт на выбор грейда; если тест уже идёт — сразу в него
  await page.goto('/candidate')
  await page.waitForURL(/\/candidate\/test\//, { timeout: 5_000 }).catch(() => {})
  if (!page.url().includes('/candidate/test/')) {
    // В свежем демо-мире идущего теста нет: проверяем экран выбора грейда, а плеер — в audit/audit.mjs, когда сессия есть
    test.info().annotations.push({ type: 'note', description: 'идущего теста нет — проверен экран выбора грейда' })
    await page.goto('/candidate/grade?spec=backend_python')
  }
  await settle(page)
  await expectNoSeriousViolations(page, 'test player / grade')
})

test('a11y: кандидат — результат', async ({ page }) => {
  const done = demoAccount('stage4-category@demo.example.com')
  await login(page, done.email, done.password)
  await page.goto('/candidate/qualification')
  await settle(page)
  const res = await page.locator('a[href*="/candidate/result/"]').first().getAttribute('href')
  expect(res).toBeTruthy()
  await page.goto(res!)
  await settle(page)
  await expectNoSeriousViolations(page, 'result')
})

test('a11y: работодатель и лаборатория', async ({ page }) => {
  const hr = demoAccount('hr@severny-kod.example')
  await login(page, hr.email, hr.password)
  await page.goto('/employer/needs')
  await settle(page)
  const need = await page.locator('a[href^="/employer/needs/"]').evaluateAll((as) =>
    as.map((a) => a.getAttribute('href')).find((h) => /needs\/[0-9a-f-]{8,}$/.test(h ?? '')),
  )
  expect(need).toBeTruthy()
  for (const path of [need!, '/employer/bank', '/employer/bank/list', '/employer/invitations']) {
    await page.goto(path)
    await settle(page)
    await expectNoSeriousViolations(page, path)
  }
})

test('a11y: лаборатория — акт сверки', async ({ page }) => {
  // На чистой БД (CI) сверок нет: порождаем акт сами, а не рассчитываем на данные чужого прогона
  await submitSandboxWithTwinTrace(page)
  const jury = demoAccount('jury@demo.example.com')
  await login(page, jury.email, jury.password)
  await page.goto('/lab/reconciliations')
  await settle(page)
  const link = page.locator('a[href^="/lab/reconciliations/"]').first()
  await expect(link).toBeVisible({ timeout: 15_000 })
  const act = await link.getAttribute('href')
  await page.goto(act!)
  await settle(page)
  await expectNoSeriousViolations(page, 'lab act')
})
