import { expect, test } from '@playwright/test'

// (b) Кандидат: регистрация (DEMO_MODE — аккаунт активен без письма) → опрос → выбор грейда → старт теста.
test('кандидат: регистрация → опрос → грейд → тест начат', async ({ page }) => {
  const email = `e2e-${Date.now()}@demo.example.com`
  await page.goto('/auth/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Пароль', { exact: true }).fill('Playwright-2026')
  await page.getByLabel('Год рождения').fill('1998')
  await page.getByRole('checkbox', { name: /согласие на обработку/i }).check({ force: true })
  await page.getByRole('button', { name: 'Получить код' }).click()
  await expect(page).toHaveURL(/\/candidate/)

  await page.goto('/candidate/survey')
  await expect(page.getByText('Шаг 1 из 4')).toBeVisible()
  for (const step of [2, 3, 4]) {
    await page.getByRole('button', { name: 'Далее' }).click()
    await expect(page.getByText(`Шаг ${step} из 4`)).toBeVisible()
  }
  await page.getByRole('radio', { name: /Backend/ }).first().click()
  await page.getByRole('button', { name: 'Завершить опрос' }).click()

  await page.getByRole('link', { name: 'Выбрать грейд' }).click()
  await expect(page).toHaveURL(/\/candidate\/grade/)
  await page.getByRole('radio', { name: /Middle/ }).click()
  await page.getByRole('checkbox').first().check({ force: true })
  await page.getByRole('button', { name: 'Начать тест' }).click()
  await expect(page).toHaveURL(/\/candidate\/test\//)
  await expect(page.getByRole('button', { name: 'Ответить', exact: true })).toBeVisible()
})
