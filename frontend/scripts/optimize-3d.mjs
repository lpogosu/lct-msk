// Оптимизирует прозрачные 3D-рендеры из _internal/design/assets3d в WebP (≤ 300 КБ) → public/3d.
// _internal не в git, поэтому готовые WebP лежат в репозитории; скрипт нужен, только чтобы пересобрать.
import { mkdir, readdir, stat } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import sharp from 'sharp'

const here = dirname(fileURLToPath(import.meta.url))
const SRC = resolve(here, '../../_internal/design/assets3d')
const OUT = resolve(here, '../public/3d')
const NAMES = ['hero_vzaimno', 'medal_fsp', 'detector', 'podium', 'empty_state', 'mutual']
const MAX_BYTES = 300 * 1024
const MAX_WIDTH = 960

await mkdir(OUT, { recursive: true })
for (const name of NAMES) {
  const input = join(SRC, `${name}_transparent.png`)
  const output = join(OUT, `${name}.webp`)
  let quality = 86
  for (;;) {
    await sharp(input)
      .resize({ width: MAX_WIDTH, withoutEnlargement: true })
      .webp({ quality, alphaQuality: 90, effort: 6 })
      .toFile(output)
    const { size } = await stat(output)
    if (size <= MAX_BYTES || quality <= 40) {
      const meta = await sharp(output).metadata()
      console.log(`${name}.webp  ${meta.width}x${meta.height}  ${(size / 1024).toFixed(0)} КБ  q=${quality}`)
      break
    }
    quality -= 6
  }
}
console.log('Готово:', (await readdir(OUT)).join(', '))
