import { expect, test } from '@playwright/test'
import { answerUntilResult } from './helpers'

// (a) /jury → экспресс-тест → ответы → результат. Без регистрации; только DEMO_MODE.
test('жюри: экспресс-тест от старта до результата', async ({ page }) => {
  await page.goto('/jury')
  await expect(page.getByRole('heading', { name: /Проверьте сами/ })).toBeVisible()
  await page.getByRole('button', { name: 'Начать экспресс-тест' }).click()
  await expect(page).toHaveURL(/\/candidate\/test\//)
  await expect(page.getByTestId('express-strip')).toBeVisible()

  const n = await answerUntilResult(page, /\/candidate\/result\//, 12)
  expect(n).toBeGreaterThan(0)
  await expect(page.getByTestId('express-result')).toBeVisible()
  await expect(page.getByTestId('express-headline')).not.toBeEmpty()
})
