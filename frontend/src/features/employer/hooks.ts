import { useEffect, useState } from 'react'

/** Значение с задержкой: запрос превью и разбора не уходит на каждое нажатие клавиши. */
export function useDebounced<T>(value: T, ms = 400): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}
