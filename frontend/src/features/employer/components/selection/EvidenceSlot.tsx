import { useSyncExternalStore, type ReactNode } from 'react'
import { Panel } from '@/ui'
import { Drawer } from '@/ui/Drawer'

const XL = '(min-width: 1280px)'

function useIsXl(): boolean {
  return useSyncExternalStore(
    (cb) => {
      const mq = window.matchMedia(XL)
      mq.addEventListener('change', cb)
      return () => mq.removeEventListener('change', cb)
    },
    () => window.matchMedia(XL).matches,
    () => true,
  )
}

/**
 * Место карточки кандидата. От xl (1280 px) — липкая панель справа от таблицы; ниже — выдвижная панель на весь экран:
 * на телефоне и планшете карточка не прячется под длинной таблицей. Рендерится ровно одна из двух веток,
 * чтобы не дублировать запросы и регионы для скринридера. `suspended` скрывает панель, пока поверх открыто другое
 * окно (приглашение), иначе два модальных окна спорят за фокус.
 */
export function EvidenceSlot({
  open,
  onClose,
  suspended = false,
  title = 'Карточка кандидата',
  empty,
  children,
}: {
  open: boolean
  onClose: () => void
  suspended?: boolean
  title?: string
  empty: ReactNode
  children: ReactNode
}) {
  const xl = useIsXl()
  if (xl) {
    return (
      <Panel variant="panel" radius="md" padding="lg" aria-label={title}>
        {open ? children : empty}
      </Panel>
    )
  }
  return (
    <Drawer open={open && !suspended} onClose={onClose} title={title} width={520}>
      {children}
    </Drawer>
  )
}
