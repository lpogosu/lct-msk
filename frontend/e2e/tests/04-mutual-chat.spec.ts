import { expect, test } from '@playwright/test'
import { demoAccount, login } from './helpers'

// (d) Кандидат принимает приглашение → контакты по согласию → «Взаимно» → сообщение в чате.
// Приглашение создаёт предыдущий тест (03-employer-flow): топ подборки «Backend Middle» — демо-персоны (Илья и ступенчатые
// аккаунты). Здесь ищем среди них того, у кого есть приглашение в статусе «ожидает ответа».
const PERSONAS = ['ilya@demo.example.com', 'stage4-category@demo.example.com', 'anna@demo.example.com']

test('кандидат: «да» → «Взаимно» → чат', async ({ page }) => {
  const accept = page.getByRole('button', { name: 'Да, интересно' })
  let found = false
  for (const email of PERSONAS) {
    const cand = demoAccount(email)
    await page.goto('/')
    await login(page, cand.email, cand.password)
    await page.goto('/candidate/invitations')
    await page.waitForLoadState('networkidle')
    const hrefs = await page
      .locator('a[href^="/candidate/invitations/"]')
      .evaluateAll((els) => els.map((e) => e.getAttribute('href') as string))
    for (const href of hrefs) {
      await page.goto(href)
      await expect(page.getByRole('heading').first()).toBeVisible()
      if (await accept.isVisible()) {
        found = true
        break
      }
    }
    if (found) break
    await page.evaluate(() => window.localStorage.clear())
    await page.context().clearCookies()
  }
  expect(found, 'нет приглашения в статусе «ожидает ответа»: сначала должен пройти 03-employer-flow').toBe(true)

  await accept.click()
  const sheet = page.getByRole('dialog')
  await expect(sheet).toBeVisible()
  await sheet.getByRole('checkbox', { name: /Согласен передать/ }).check({ force: true })
  await sheet.getByRole('button', { name: 'Да, интересно' }).click()

  // Принятие условий и согласие на раскрытие дают «Взаимно»: контакт открыт, чат доступен
  await expect(page.getByRole('heading', { name: 'Взаимно' })).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('hr@severny-kod.example')).toBeVisible()

  const message = `Здравствуйте! Когда удобно созвониться? (e2e ${Date.now()})`
  await page.getByPlaceholder(/Сообщение \(Ctrl\+Enter/).fill(message)
  await page.getByRole('button', { name: 'Отправить', exact: true }).click()
  await expect(page.getByRole('log', { name: 'Сообщения' }).or(page.getByLabel('Сообщения')).getByText(message)).toBeVisible()
})
