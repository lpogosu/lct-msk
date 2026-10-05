import { clsx, type ClassValue } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

// tailwind-merge не знает наших токенов (rounded-pill, t-label…) — подсказываем, иначе конфликты не схлопнутся.
const twMerge = extendTailwindMerge<'typography'>({
  extend: {
    theme: { radius: ['pill', 'panel', 'panel-lg', 'panel-xl', 'hero', 'cell'] },
    classGroups: {
      typography: [
        {
          t: ['display', 'title', 'num-l', 'num-m', 'lead', 'name', 'body', 'body-strong', 'label', 'small', 'code'],
        },
      ],
    },
    conflictingClassGroups: {
      typography: ['font-size', 'font-weight', 'leading', 'tracking', 'font-family'],
    },
  },
})

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
