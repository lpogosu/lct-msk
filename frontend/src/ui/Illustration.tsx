import { cn } from '@/lib/cn'

/** 3D-рендеры из public/3d (WebP с прозрачностью, ≤ 300 КБ; исходники — _internal/design/assets3d). */
export type IllustrationName = 'hero_vzaimno' | 'medal_fsp' | 'detector' | 'podium' | 'empty_state' | 'mutual'

// Реальные размеры файлов — чтобы браузер резервировал место и не было прыжков вёрстки (CLS)
const SIZE: Record<IllustrationName, [number, number]> = {
  hero_vzaimno: [960, 540],
  medal_fsp: [960, 960],
  detector: [960, 960],
  podium: [960, 720],
  empty_state: [960, 960],
  mutual: [960, 960],
}

export interface IllustrationProps {
  name: IllustrationName
  className?: string
  /** Без alt картинка считается декоративной */
  alt?: string
  eager?: boolean
}

export function Illustration({ name, className, alt = '', eager }: IllustrationProps) {
  const [w, h] = SIZE[name]
  return (
    <img
      src={`/3d/${name}.webp`}
      width={w}
      height={h}
      alt={alt}
      loading={eager ? 'eager' : 'lazy'}
      decoding="async"
      draggable={false}
      className={cn('block h-auto max-w-full select-none object-contain', className)}
    />
  )
}
