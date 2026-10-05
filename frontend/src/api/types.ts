import type { components } from './schema'

/** Все схемы бэкенда: `Schemas['UserOut']`. Сгенерировано `npm run gen:api`. */
export type Schemas = components['schemas']

export type User = Schemas['UserOut']
export type TokenResponse = Schemas['TokenResponse']
export type RegisterRequest = Schemas['RegisterRequest']
export type RegisterResponse = Schemas['RegisterResponse']
export type ConsentDocument = Schemas['ConsentDocumentOut']

/** Роли аккаунта: candidate «Ищу работу», employer «Нанимаю», methodist/admin — лаборатория. */
export type Role = 'candidate' | 'employer' | 'methodist' | 'admin'
