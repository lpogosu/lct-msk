import { Outlet } from 'react-router-dom'
import { Illustration, Logo, Panel, Trace } from '@/ui'

/**
 * Рамка экранов входа: слева форма, справа на широких экранах — мягкая hero-панель с 3D и обещанием продукта.
 * На телефоне — только форма с логотипом сверху.
 */
export function AuthLayout() {
  return (
    <div className="min-h-dvh bg-white">
      <div className="mx-auto grid min-h-dvh max-w-page lg:grid-cols-[minmax(0,1fr)_minmax(0,560px)]">
        <div className="flex flex-col px-6 py-6 sm:px-12 lg:px-16">
          <Logo />
          <main id="main" className="flex flex-1 items-center py-10">
            <div className="mx-auto w-full max-w-[440px]">
              <Outlet />
            </div>
          </main>
        </div>
        <aside className="hidden p-6 lg:sticky lg:top-0 lg:block lg:h-dvh" aria-hidden="true">
          <Panel variant="hero" radius="hero" padding="none" className="flex h-full flex-col justify-between overflow-hidden">
            <div className="p-10 pb-0">
              <Trace className="w-24" nodes={[{ at: 0, variant: 'open' }, { at: 1 }]} height={12} inset={6} />
              <p className="t-title mt-6 text-ink">Контакты откроются только после взаимного «да».</p>
              <p className="t-lead mt-3 max-w-[400px] text-ink-2">
                Работодатель видит слепую карточку: категорию, подтверждённые навыки и зарплатные ожидания. Имя и
                связь — когда вы оба согласились.
              </p>
            </div>
            <Illustration name="mutual" className="mx-auto w-[360px]" eager />
          </Panel>
        </aside>
      </div>
    </div>
  )
}
