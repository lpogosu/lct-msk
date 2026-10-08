// Скриншот карточки кандидата как выдвижной панели на 390 и 768 px. node audit/drawer-shot.mjs <метка> [baseURL]
import { chromium } from '@playwright/test'
import { readFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
const here = dirname(fileURLToPath(import.meta.url))
const label = process.argv[2] ?? 'after'
const base = process.argv[3] ?? 'http://localhost:5188'
const out = resolve(here, '../../.screens/responsive', label)
mkdirSync(out, { recursive: true })
const creds = readFileSync(resolve(here, '../../../backend/var/demo_accounts.md'), 'utf-8')
const email = 'hr@severny-kod.example'
const pw = creds.split('\n').find((l) => l.includes(`\`${email}\``)).split('|')[2].trim().replace(/`/g, '')
const b = await chromium.launch()
for (const w of [390, 768]) {
  const ctx = await b.newContext({ viewport: { width: w, height: 900 }, locale: 'ru-RU' })
  const p = await ctx.newPage()
  await p.goto(base + '/auth/login', { waitUntil: 'networkidle' })
  await p.getByLabel(/почта|email/i).first().fill(email)
  await p.getByLabel(/пароль/i).first().fill(pw)
  await p.locator('button[type=submit]').first().click()
  await p.waitForURL((u) => !u.pathname.startsWith('/auth'))
  await p.goto(base + '/employer/needs'); await p.waitForTimeout(1500)
  const href = await p.evaluate(() => [...document.querySelectorAll('a[href]')].map((a) => a.getAttribute('href')).find((h) => /needs\/[0-9a-f-]{8,}$/.test(h)))
  await p.goto(base + href, { waitUntil: 'networkidle' })
  await p.waitForTimeout(1500)
  await p.locator('tbody tr').first().click()
  await p.waitForTimeout(1500)
  await p.screenshot({ path: `${out}/emp-selection-drawer-${w}.png` })
  const ov = await p.evaluate(() => document.documentElement.scrollWidth - innerWidth)
  console.log(w, 'overflow', ov, 'dialog', await p.getByRole('dialog').count())
  await ctx.close()
}
await b.close()
