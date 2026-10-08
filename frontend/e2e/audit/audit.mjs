// Аудит адаптивности и доступности: скриншоты 390/768/1440, горизонтальный скролл страницы и прогон axe-core.
// Запуск: node audit/audit.mjs <метка: before|after> [baseURL]. Нужен стенд (бэкенд DEMO_MODE + Vite), см. docs/ACCESSIBILITY.md.
// Скриншоты — в frontend/.screens/responsive/<метка>/ (в .gitignore), JSON-отчёт — рядом.
import { chromium } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const label = process.argv[2] ?? 'after'
const base = process.argv[3] ?? 'http://localhost:5188'
const out = resolve(here, '../../.screens/responsive', label)
mkdirSync(out, { recursive: true })

const creds = readFileSync(resolve(here, '../../../backend/var/demo_accounts.md'), 'utf-8')
const pass = (email) => creds.split('\n').find((l) => l.includes(`\`${email}\``))?.split('|')[2]?.trim().replace(/`/g, '')
const VIEWPORTS = { 390: { width: 390, height: 844 }, 768: { width: 768, height: 1024 }, 1440: { width: 1440, height: 900 } }

async function login(page, email) {
  await page.goto(base + '/auth/login')
  await page.getByLabel(/почта|email/i).first().fill(email)
  await page.getByLabel(/пароль/i).first().fill(pass(email))
  await page.locator('button[type=submit]').first().click()
  await page.waitForURL((u) => !u.pathname.startsWith('/auth/login'))
}
const firstHref = async (page, re) =>
  page.evaluate((src) => {
    const r = new RegExp(src)
    return [...document.querySelectorAll('a[href]')].map((a) => a.getAttribute('href')).find((h) => r.test(h)) ?? null
  }, re.source)

const browser = await chromium.launch()
const report = []

async function visit(ctx, name, path, { axe = true } = {}) {
  for (const [w, vp] of Object.entries(VIEWPORTS)) {
    const page = await ctx.newPage()
    await page.setViewportSize(vp)
    try {
      await page.goto(base + path, { waitUntil: 'networkidle', timeout: 30000 })
      await page.waitForFunction(() => !document.querySelector('[aria-busy="true"], [class*="animate-shimmer"]'), null, { timeout: 15000 }).catch(() => {})
      await page.waitForTimeout(800)
      const overflow = await page.evaluate(() => ({
        sw: document.documentElement.scrollWidth,
        iw: window.innerWidth,
      }))
      await page.screenshot({ path: `${out}/${name}-${w}.png`, fullPage: true })
      const row = { name, path, width: Number(w), overflowPx: Math.max(0, overflow.sw - overflow.iw) }
      if (axe && w === '1440') {
        const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
        row.axe = r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, sample: v.nodes[0]?.target?.join(' '), help: v.help }))
      }
      report.push(row)
      console.log(name, w, 'overflow', row.overflowPx, (row.axe ?? []).map((v) => `${v.impact}:${v.id}(${v.nodes})`).join(' '))
    } catch (e) {
      report.push({ name, path, width: Number(w), error: String(e).slice(0, 200) })
      console.log(name, w, 'ERR', String(e).slice(0, 120))
    }
    await page.close()
  }
}

// публичные
{
  const ctx = await browser.newContext({ locale: 'ru-RU' })
  await visit(ctx, 'landing', '/')
  await visit(ctx, 'login', '/auth/login')
  await visit(ctx, 'jury', '/jury')
  await ctx.close()
}
// кандидат
{
  const ctx = await browser.newContext({ locale: 'ru-RU' })
  const p = await ctx.newPage()
  await login(p, 'stage3-midtest@demo.example.com')
  await p.goto(base + '/candidate'); await p.waitForTimeout(1500)
  let test = p.url().includes('/candidate/test/') ? new URL(p.url()).pathname : await firstHref(p, /\/candidate\/test\//)
  await p.close()
  if (test) await visit(ctx, 'test-player', test)
  await ctx.close()
  const c2 = await browser.newContext({ locale: 'ru-RU' })
  const q = await c2.newPage()
  await login(q, 'stage4-category@demo.example.com')
  let res = null
  for (const path of ['/candidate', '/candidate/qualification']) {
    await q.goto(base + path); await q.waitForTimeout(2000)
    res = await firstHref(q, /\/candidate\/result\/[^/]+$/)
    if (res) break
  }
  await q.close()
  if (res) await visit(c2, 'result', res)
  else console.log('result link not found')
  await c2.close()
}
// работодатель
{
  const ctx = await browser.newContext({ locale: 'ru-RU' })
  const p = await ctx.newPage()
  await login(p, 'hr@severny-kod.example')
  await p.goto(base + '/employer/needs'); await p.waitForTimeout(1500)
  const need = await firstHref(p, /\/employer\/needs\/[0-9a-f-]{8,}$/)
  await p.close()
  await visit(ctx, 'emp-needs', '/employer/needs')
  if (need) await visit(ctx, 'emp-selection', need)
  await visit(ctx, 'emp-talent-map', '/employer/bank')
  await visit(ctx, 'emp-bank', '/employer/bank/list')
  await visit(ctx, 'emp-invitations', '/employer/invitations')
  await visit(ctx, 'emp-vacancies', '/employer/vacancies')
  await ctx.close()
}
// лаборатория
{
  const ctx = await browser.newContext({ locale: 'ru-RU' })
  const p = await ctx.newPage()
  await login(p, 'jury@demo.example.com')
  await p.goto(base + '/lab/reconciliations'); await p.waitForTimeout(1500)
  const act = await firstHref(p, /\/lab\/reconciliations\/[^/]+$/)
  await p.close()
  if (act) await visit(ctx, 'lab-act', act)
  await ctx.close()
}
await browser.close()
writeFileSync(`${out}/report.json`, JSON.stringify(report, null, 2))
