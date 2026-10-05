import { forwardRef, useState } from 'react'
import { Input, type InputProps } from './Input'

/** Поле пароля с переключателем «Показать / Скрыть». */
export const PasswordInput = forwardRef<HTMLInputElement, Omit<InputProps, 'type' | 'adornment'>>(
  function PasswordInput(props, ref) {
    const [shown, setShown] = useState(false)
    return (
      <Input
        ref={ref}
        {...props}
        type={shown ? 'text' : 'password'}
        adornment={
          <button
            type="button"
            onClick={() => setShown((v) => !v)}
            aria-pressed={shown}
            className="rounded-md px-2 py-1 t-label text-brand hover:underline"
          >
            {shown ? 'Скрыть' : 'Показать'}
          </button>
        }
      />
    )
  },
)
