// Генерирует типы API из OpenAPI бэкенда.
//   npm run gen:api                      — берёт http://localhost:8000/api/openapi.json (бэкенд должен быть запущен)
//   OPENAPI_URL=http://host/api/openapi.json npm run gen:api
//   OPENAPI_FILE=../backend/openapi.json npm run gen:api   — из готового файла
// Результат: src/api/openapi.json (снимок схемы) и src/api/schema.d.ts (типы; коммитятся, чтобы сборка шла без бэкенда).
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import openapiTS, { astToString } from 'openapi-typescript'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const outDir = resolve(root, 'src/api')
const snapshot = resolve(outDir, 'openapi.json')
const types = resolve(outDir, 'schema.d.ts')

const base = process.env.OPENAPI_BASE ?? 'http://localhost:8000'
const candidates = process.env.OPENAPI_URL
  ? [process.env.OPENAPI_URL]
  : [`${base}/api/openapi.json`, `${base}/openapi.json`]

async function load() {
  if (process.env.OPENAPI_FILE) {
    return await readFile(resolve(root, process.env.OPENAPI_FILE), 'utf8')
  }
  const errors = []
  for (const url of candidates) {
    try {
      const res = await fetch(url)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const text = await res.text()
      JSON.parse(text)
      console.log(`Схема получена: ${url}`)
      return text
    } catch (e) {
      errors.push(`${url}: ${e instanceof Error ? e.message : e}`)
    }
  }
  throw new Error(
    `Не удалось получить OpenAPI. Запустите бэкенд (cd backend; uv run uvicorn app.main:app --port 8000) ` +
      `или задайте OPENAPI_URL / OPENAPI_FILE.\n${errors.join('\n')}`,
  )
}

await mkdir(outDir, { recursive: true })
const text = await load()
// Нормализуем форматирование снимка, чтобы диффы были читаемыми
await writeFile(snapshot, JSON.stringify(JSON.parse(text), null, 2) + '\n', 'utf8')

// JS-API вместо CLI: CLI ломается на путях с кириллицей (репозиторий лежит в «москва2»)
const ast = await openapiTS(JSON.parse(text), { alphabetize: false })
const banner = '/* Файл сгенерирован `npm run gen:api` из OpenAPI бэкенда. Не редактировать вручную. */\n\n'
await writeFile(types, banner + astToString(ast), 'utf8')
console.log(`Типы записаны: ${types}`)
