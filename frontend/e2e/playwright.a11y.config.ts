import { defineConfig } from '@playwright/test'
import base from './playwright.config'

// Автопроверка доступности не меняет данные, поэтому без globalSetup (он сбрасывает демо-мир).
export default defineConfig({ ...base, globalSetup: undefined, testMatch: '06-a11y.spec.ts' })
