// Локальный запуск e2e одной командой (`make e2e`): поднимает бэкенд в DEMO_MODE и Vite, ждёт готовности, гоняет Playwright,
// гасит процессы. Нужна БД с миграциями и демо-миром: make up-infra migrate gen-pool seed (или своя через VZ_DATABASE_URL).
import { spawn, spawnSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const backendPort = process.env.E2E_BACKEND_PORT ?? '8128'
const webPort = process.env.E2E_WEB_PORT ?? '5185'
const isWin = process.platform === 'win32'
const children = []

function start(cmd, args, cwd, env) {
  const child = spawn(cmd, args, { cwd, env: { ...process.env, ...env }, stdio: 'inherit', shell: isWin })
  children.push(child)
  return child
}

function stopAll() {
  for (const c of children) {
    if (c.exitCode !== null || !c.pid) continue
    if (isWin) spawnSync('taskkill', ['/PID', String(c.pid), '/T', '/F'])
    else c.kill('SIGTERM')
  }
}

async function waitFor(url, label, timeoutMs = 90_000) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    try {
      if ((await fetch(url)).ok) return
    } catch {
      /* ещё не поднялся */
    }
    await new Promise((r) => setTimeout(r, 1000))
  }
  throw new Error(`${label} не ответил за ${timeoutMs / 1000} с: ${url}`)
}

let code = 1
try {
  const origin = `http://localhost:${webPort}`
  start('uv', ['run', 'uvicorn', 'app.main:app', '--port', backendPort], resolve(root, 'backend'), {
    VZ_DEMO_MODE: 'true',
    VZ_PUBLIC_BASE_URL: origin,
    VZ_CORS_ORIGINS: origin,
    PYTHONUTF8: '1',
  })
  start('npx', ['vite', '--port', webPort, '--strictPort'], resolve(root, 'frontend'), {
    VITE_API_PROXY_TARGET: `http://localhost:${backendPort}`,
  })
  await waitFor(`http://localhost:${backendPort}/api/health`, 'бэкенд')
  await waitFor(`${origin}/api/health`, 'интерфейс (прокси /api)')
  const r = spawnSync('npx', ['playwright', 'test', ...process.argv.slice(2)], {
    cwd: dirname(fileURLToPath(import.meta.url)),
    env: { ...process.env, E2E_BASE_URL: origin },
    stdio: 'inherit',
    shell: isWin,
  })
  code = r.status ?? 1
} catch (e) {
  console.error(String(e))
} finally {
  stopAll()
}
process.exit(code)
