import { demoAccount } from './tests/helpers'

// Перед прогоном возвращаем демо-мир к состоянию seed (персоны, приглашения, лимиты), чтобы тесты были повторяемыми.
// Вне prod сброс доступен любому вошедшему (входим как жюри из demo_accounts.md); на стенде с ограничением — ключ
// E2E_DEMO_KEY = VZ_DEMO_CONTROL_KEY. Если аккаунтов нет (и ключа тоже), сброс пропускается: чистый seed в нём не нуждается.
export default async function globalSetup(): Promise<void> {
  const base = process.env.E2E_BASE_URL ?? 'http://localhost:5185'
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (process.env.E2E_DEMO_KEY) headers['X-Demo-Key'] = process.env.E2E_DEMO_KEY
  else {
    let jury
    try {
      jury = demoAccount('jury@demo.example.com')
    } catch {
      return
    }
    const r = await fetch(`${base}/api/auth/login`, { method: 'POST', headers, body: JSON.stringify(jury) })
    if (!r.ok) throw new Error(`Вход жюри для сброса не удался: HTTP ${r.status}`)
    headers.Authorization = `Bearer ${((await r.json()) as { access_token: string }).access_token}`
  }
  const res = await fetch(`${base}/api/demo/reset`, { method: 'POST', headers })
  if (!res.ok) throw new Error(`Сброс демо-мира не удался: HTTP ${res.status}`)
}
