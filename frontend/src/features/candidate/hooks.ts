import { useEffect, useState } from 'react'

/** Значение с задержкой: живое превью не должно дёргать сервер на каждую букву. */
export function useDebounced<T>(value: T, ms = 450): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms)
    return () => window.clearTimeout(id)
  }, [value, ms])
  return v
}
