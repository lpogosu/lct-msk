import { expect, test } from '@playwright/test'
import { demoAccount, login } from './helpers'

const BRIEF =
  'Бэкенд в команду платёжного шлюза. Уверенный Python, FastAPI, PostgreSQL, опыт от 2 лет. Kafka и Docker будут плюсом. Гибрид, Москва. 220–280 тыс. на руки.'

test('работодатель: бриф → подборка → панель доказательств → приглашение', async ({ page }) => {
  const hr = demoAccount('hr@severny-kod.example')
  await login(page, hr.email, hr.password)
  await expect(page).toHaveURL(/\/employer/)

  await page.goto('/employer/needs/new')
  await page.getByRole('textbox').first().fill(BRIEF)
  await expect(page.getByText('разобрано правилами, без нейросети')).toBeVisible()
  await expect(page.getByLabel('Вилка от, ₽ в месяц')).not.toHaveValue('')
  await page.getByRole('button', { name: 'Показать подборку' }).click()
  await expect(page).toHaveURL(/\/employer\/needs\/[0-9a-f-]+$/)

  const rows = page.locator('tbody tr')
  await expect(rows.first()).toBeVisible()
  await page.screenshot({ path: 'test-results/sel.png' })
  const withCon = rows.filter({ hasText: 'Против:' }).first()
  await withCon.click()
  const why = page.getByRole('region', { name: 'Почему в подборке' })
  await expect(why).toBeVisible()
  // Панель доказательств: ровно 3 «за» и 1 «против» (остальные — в разборе ниже)
  await expect(why.locator('li')).toHaveCount(4)
  await expect(why.locator('li', { hasText: 'Против:' })).toHaveCount(1)
  await expect(page.getByText('Имя и контакты откроются, только когда кандидат ответит «да»')).toBeVisible()

  // Приглашаем первого, кому ещё не отправлено (тест можно гонять повторно на том же стенде)
  const inviteBtn = page.getByRole('button', { name: 'Пригласить', exact: true })
  const total = await rows.count()
  for (let i = 0; i < total && !(await inviteBtn.isVisible()); i++) await rows.nth(i).click()
  await inviteBtn.click()
  const drawer = page.getByRole('dialog')
  await expect(drawer).toBeVisible()
  await drawer.getByRole('button', { name: 'Отправить приглашение' }).click()
  // Окно закрывается, в карточке появляется статус приглашения
  await expect(drawer).toBeHidden()
  await expect(page.getByText('Приглашён: Отправлено')).toBeVisible()
})
