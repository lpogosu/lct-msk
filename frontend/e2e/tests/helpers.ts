import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, type Page } from '@playwright/test'

const here = dirname(fileURLToPath(import.meta.url))

/**
 * Логин и пароль демо-аккаунта из backend/var/demo_accounts.md (создаёт `make seed`; пароли выводятся из VZ_APP_SECRET и в
 * репозиторий не попадают). Путь можно переопределить E2E_CREDENTIALS.
 */
export function demoAccount(email: string): { email: string; password: string } {
  const file = process.env.E2E_CREDENTIALS ?? resolve(here, '../../../backend/var/demo_accounts.md')
  const text = readFileSync(file, 'utf-8')
  const row = text.split('\n').find((l) => l.includes(`\`${email}\``))
  const password = row?.split('|')[2]?.trim().replace(/`/g, '')
  if (!password) throw new Error(`В ${file} нет аккаунта ${email}: выполните make seed`)
  return { email, password }
}

export async function login(page: Page, email: string, password: string): Promise<void> {
  await page.goto('/auth/login', { waitUntil: 'networkidle' })
  await page.getByLabel(/почта|email/i).first().fill(email)
  await page.getByLabel(/пароль/i).first().fill(password)
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await expect(page).not.toHaveURL(/\/auth\/login/)
}

/**
 * Отвечает на задания теста, пока не откроется результат. Тип задания не важен: выбираем первый вариант / вводим «1»
 * / нажимаем первую строку кода; если ответить нечем — «Не знаю — пропустить». Качество ответов смоуку не нужно,
 * проверяется, что сквозной путь «задание → ответ → следующее → результат» работает.
 */
export async function answerUntilResult(page: Page, resultUrl: RegExp, maxItems = 30): Promise<number> {
  let answered = 0
  while (answered < maxItems) {
    const answer = page.getByRole('button', { name: 'Ответить', exact: true })
    const skip = page.getByRole('button', { name: /Не знаю/ })
    // Либо следующее задание, либо переход на экран результата
    await expect
      .poll(async () => resultUrl.test(page.url()) || (await skip.isVisible()), { timeout: 30_000 })
      .toBe(true)
    if (resultUrl.test(page.url())) break
    const firstRadio = page.getByRole('radio').first()
    if (await firstRadio.isVisible()) await firstRadio.click()
    else if (await page.getByLabel('Ваш ответ').isVisible()) await page.getByLabel('Ваш ответ').fill('1')
    else {
      const line = page.getByRole('button', { name: /^Строка \d+:/ }).first()
      if (await line.isVisible()) await line.click()
    }
    const before = answered
    try {
      // Кнопка может исчезнуть, если тест завершился между проверкой и кликом (экран результата уже открыт)
      if (await answer.isEnabled()) await answer.click({ timeout: 10_000 })
      else await skip.click({ timeout: 10_000 })
    } catch (e) {
      if (resultUrl.test(page.url())) break
      throw e
    }
    answered = before + 1
    await page.waitForTimeout(150)
  }
  return answered
}
