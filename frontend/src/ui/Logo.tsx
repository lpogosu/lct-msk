import { Link } from 'react-router-dom'
import { cn } from '@/lib/cn'

/** Знак: дуга-трасса между двумя узлами («взаимно»). Figma 27:5. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg
      width="34"
      height="22"
      viewBox="0 0 34 22"
      fill="none"
      aria-hidden="true"
      className={cn('shrink-0', className)}
      focusable="false"
    >
      <path d="M5 17C5 7.5 29 7.5 29 17" stroke="#402FFF" strokeWidth="2" strokeLinecap="round" />
      <circle cx="5" cy="17" r="4" fill="#402FFF" />
      <circle cx="29" cy="17" r="4" fill="#402FFF" />
    </svg>
  )
}

/** Знак + слово «взаимно» (JetBrains Mono Medium 20/24, −0,3). Ссылка на главную. */
export function Logo({ to = '/', className, word = true }: { to?: string; className?: string; word?: boolean }) {
  return (
    <Link
      to={to}
      aria-label="Взаимно — на главную"
      className={cn('inline-flex items-center gap-2.5 text-ink no-underline', className)}
    >
      <LogoMark />
      {word && <span className="font-mono text-[20px] font-medium leading-6 tracking-[-0.3px]">взаимно</span>}
    </Link>
  )
}
