/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Базовый URL API, если фронтенд и бэкенд на разных origin. По умолчанию — тот же origin (пути /api/...). */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
