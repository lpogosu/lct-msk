import { expect, test } from '@playwright/test'

// (e) /try: песочница «Попробуй списать» — копирование условия даёт «двойника» (в буфере другое значение), итог сверки.
test.use({ permissions: ['clipboard-read', 'clipboard-write'] })

test('песочница: копия задания подменяется двойником, итог сверки показан', async ({ page }) => {
  await page.goto('/try')
  await page.getByRole('button', { name: 'Начать' }).click()
  await expect(page.getByRole('heading', { name: /Задание 1 из/ })).toBeVisible()

  // Копируем условие целиком, как делает человек, отправляющий его чат-боту
  const block = page.locator('[data-task-block]')
  await block.evaluate((el) => {
    const sel = window.getSelection()!
    sel.removeAllRanges()
    const range = document.createRange()
    range.selectNodeContents(el)
    sel.addRange(range)
  })
  await page.keyboard.press('Control+C')
  await expect(page.getByText(/копировали: 1/)).toBeVisible()

  const shown = (await block.innerText()).replace(/\s+/g, ' ').trim()
  const copied = (await page.evaluate(() => navigator.clipboard.readText())).replace(/\s+/g, ' ').trim()
  expect(copied.length).toBeGreaterThan(0)
  expect(copied, 'в буфере не двойник: текст совпал с экранным').not.toBe(shown)

  // Доходим до итога: ответы произвольные, проверяется сама сверка
  const total = Number((await page.getByRole('heading', { name: /Задание 1 из/ }).innerText()).match(/из (\d+)/)![1])
  for (let i = 0; i < total; i++) {
    await page.getByLabel('Ваш ответ').fill('1')
    if (i < total - 1) await page.getByRole('button', { name: 'Дальше' }).click()
  }
  await page.getByRole('button', { name: 'Отправить ответы' }).click()
  await expect(page.getByRole('heading', { level: 1, name: /Двойник вас не поймал|Вас поймал двойник/ })).toBeVisible({ timeout: 30_000 })
})
