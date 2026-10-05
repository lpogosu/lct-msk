import { useEffect, useState } from 'react'

/** Сколько секунд осталось до момента `targetMs` (Date.now()-время). Тикает раз в секунду, останавливается на нуле. */
export function useCountdown(targetMs: number): number {
  const calc = () => Math.max(0, Math.ceil((targetMs - Date.now()) / 1000))
  const [left, setLeft] = useState(calc)

  useEffect(() => {
    setLeft(Math.max(0, Math.ceil((targetMs - Date.now()) / 1000)))
    const id = setInterval(() => {
      const next = Math.max(0, Math.ceil((targetMs - Date.now()) / 1000))
      setLeft(next)
      if (next === 0) clearInterval(id)
    }, 1000)
    return () => clearInterval(id)
  }, [targetMs])

  return left
}
