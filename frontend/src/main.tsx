import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import { queryClient } from '@/api/queryClient'
import { router } from '@/app/routes'
import { AuthBootstrap } from '@/auth/guards'
import { ToastProvider } from '@/ui'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <AuthBootstrap>
          <RouterProvider router={router} />
        </AuthBootstrap>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
)
