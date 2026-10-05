# ФСП ID на демо-стенде: Keycloak, realm `fsp`

ФСП ID — это Keycloak с OIDC (issuer `https://sso.fsp-russia.ru/realms/fsp`; scopes `openid profile email`,
claims `sub`, `name`, `given_name`, `family_name`, `email`). Пока ФСП не выдала нам client, на стенде
работает **свой Keycloak с тем же realm `fsp`**. Код платформы от этого не меняется: переход на настоящий
ФСП ID — это смена переменных окружения (см. раздел «Переход на sso.fsp-russia.ru»).

Своя аутентификация «Взаимно» (email + пароль) живёт в FastAPI. Keycloak здесь — только внешний
провайдер «Войти через ФСП ID», концепция §6.5.

## Что в `realm-fsp.json`

| Что | Значение |
|---|---|
| Realm | `fsp`, русская локаль, регистрация закрыта |
| Клиент | `vzaimno` (= `VZ_FSP_OIDC_CLIENT_ID` по умолчанию), **конфиденциальный** (client secret), authorization code + **PKCE S256** |
| Redirect URI | бэкенд `http://localhost:8000/api/auth/fsp/callback`, фронтенд `http://localhost:5173/auth/fsp/callback` (= `VZ_FSP_OIDC_REDIRECT_URI` по умолчанию) и те же пути на `https://${VZ_PUBLIC_DOMAIN}` |
| Сервисный аккаунт | включён: client credentials для чтения реестра достижений (`VZ_FSP_TOKEN_URL`) |
| Scopes | стандартные `openid profile email` → claims `sub`, `name`, `given_name`, `family_name`, `email`, `preferred_username` |
| Пользователи | 30 демо-спортсменов, `id` пользователя = `sub` = ключ спортсмена в демо-реестре |
| Пароль у всех | `Demo-FSP-2026` |
| Client secret | `vzaimno-demo-secret` — **только для демо**, в проде задаётся заново. Бэкенду его нужно передать в `VZ_FSP_OIDC_CLIENT_SECRET` (в `.env.example` поле пустое) |

`${VZ_PUBLIC_DOMAIN:vzaimno.example}` — плейсхолдер Keycloak: при `--import-realm` он подставляет
переменную окружения `VZ_PUBLIC_DOMAIN` (по умолчанию `vzaimno.example`). Если стенд поднят иначе,
замените домен в админке: Clients → vzaimno → Valid redirect URIs.

Файл генерируется вместе с демо-реестром, руками его не правим:

```bash
cd services/fsp-mock
python -m fsp_mock export   # realm-fsp.json, снимок реестра для бэкенда и таблица ниже
```

## Запуск

```bash
docker run --rm -p 8080:8080 \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
  -e VZ_PUBLIC_DOMAIN=vzaimno.example \
  -v "$(pwd)/deploy/keycloak:/opt/keycloak/data/import:ro" \
  quay.io/keycloak/keycloak:26.0 start-dev --import-realm
```

В `docker-compose.yml` это сервис `keycloak` в профиле `fsp` (≈ 512–768 МБ памяти), он монтирует
этот каталог в `/opt/keycloak/data/import`:

```bash
docker compose --profile fsp up -d keycloak
```

Чтобы плейсхолдер домена в redirect URI подставился, сервису нужна переменная
`VZ_PUBLIC_DOMAIN: ${VZ_PUBLIC_DOMAIN:-vzaimno.example}` в `environment`; без неё Keycloak берёт
значение по умолчанию `vzaimno.example`, а локальные `localhost` URI работают в любом случае.

Проверка: `http://localhost:8080/realms/fsp/.well-known/openid-configuration` отдаёт discovery,
`/realms/fsp/protocol/openid-connect/certs` — JWKS для проверки подписи id_token.

Если Keycloak не поднялся, демо не ломается: на платформе есть кнопка «демо-вход ФСП» (подписанный
мок-токен), а данные реестра бэкенд берёт из встроенного снимка (`VZ_FSP_PROVIDER=auto`).

## Переменные бэкенда

ФСП ID — те же `VZ_FSP_OIDC_*`, что в `app/core/settings.py` и `.env.example`; `app/fsp/settings.py`
читает их же, поэтому значения не расходятся.

| Переменная | Демо-стенд | Настоящая ФСП |
|---|---|---|
| `VZ_FSP_OIDC_ISSUER` | `http://localhost:8080/realms/fsp` | `https://sso.fsp-russia.ru/realms/fsp` |
| `VZ_FSP_OIDC_CLIENT_ID` | `vzaimno` | client, выданный ФСП |
| `VZ_FSP_OIDC_CLIENT_SECRET` | `vzaimno-demo-secret` | секрет от ФСП (только из секретов окружения) |
| `VZ_FSP_OIDC_REDIRECT_URI` | `http://localhost:5173/auth/fsp/callback` (или `http://localhost:8000/api/auth/fsp/callback`) | `https://<домен>/auth/fsp/callback` |
| `VZ_FSP_REGISTRY_URL` | `http://localhost:8001`, в compose `http://fsp-mock:8001` | адрес API реестра ФСП |
| `VZ_FSP_REGISTRY_TOKEN` | `demo-fsp-registry-token` | не задаётся |
| `VZ_FSP_TOKEN_URL` | не задаётся (или `…/realms/fsp/protocol/openid-connect/token`) | `https://sso.fsp-russia.ru/realms/fsp/protocol/openid-connect/token` |
| `VZ_FSP_PROVIDER` | `auto` (сеть, при недоступности — встроенный снимок) | `http` (в `auto` сбой реестра для `sub` не из демо-снимка всё равно даёт ошибку «недоступно», а не «результатов нет») |

## Переход на sso.fsp-russia.ru

1. **Зарегистрировать client в realm `fsp` ФСП**: конфиденциальный, standard flow + PKCE S256, redirect URI
   тот же, что в `VZ_FSP_OIDC_REDIRECT_URI` (`https://<домен>/auth/fsp/callback` или
   `https://<домен>/api/auth/fsp/callback`), scopes `openid profile email`. Claims `sub`, имя и email
   в кабинете ФСП уже есть (клиент `lk-sportsman-prod` их получает).
2. **Сменить issuer** на `https://sso.fsp-russia.ru/realms/fsp` и client id/secret. Discovery и JWKS
   берутся из `/.well-known/openid-configuration`, поэтому код и проверка подписи не меняются.
3. **Предложить ФСП контракт реестра** — OpenAPI демо-реестра (`services/fsp-mock`, `/docs`), его модели
   лежат в `backend/app/fsp/models.py`. Доступ — client credentials сервисного аккаунта (`VZ_FSP_TOKEN_URL`).
4. **Переключить `FSPProvider`** на настоящий адрес: `VZ_FSP_REGISTRY_URL`, `VZ_FSP_PROVIDER=http`.
5. **Учитывать задержку протоколов**: результаты до сдачи протокола приходят как `awaiting_protocol`
   и в оценку не идут (у кандидата — «Ожидает протокол»).
6. `sub` у настоящих спортсменов другие: демо-пользователей этого realm в проде нет, привязка `fsp_id`
   создаётся заново при первом входе. Сопоставление исторических данных по ФИО и дате рождения — только
   с согласия человека и вне MVP.

## Демо-пользователи ↔ спортсмены демо-реестра

Все ФИО вымышлены (фамилии из ИТ-терминов), совпадения с реальными людьми случайны. Логин и пароль —
на странице входа Keycloak; `sub` из id_token — это `id` спортсмена в `GET /v1/athletes/{sub}` мока.

<!-- demo-users:begin -->
| Логин | sub (= id в реестре) | ФИО (вымышленные) | Что в реестре |
|---|---|---|---|
| `fsp-demo-01` | `54ac9bcc-d3ac-4244-a71b-656ddbaa7a8f` | Дебагов Роман Евгеньевич | 1 из 103, всероссийские, продуктовое 2025; всего результатов: 3; МС; роль frontend |
| `fsp-demo-02` | `f65bb71f-8ac3-4af0-8ead-29daf95d439a` | Кэшев Лев Сергеевич | 3 из 44, всероссийские, продуктовое 2026; всего результатов: 2; ждёт протокола: 1; КМС; несовершеннолетний, 17 лет; роль ml |
| `fsp-demo-03` | `b05a3443-6e8e-4cba-9efb-383bfcb06b04` | Релизов Тимофей Игоревич | 2 из 35, международные, робототехника 2024; всего результатов: 4; КМС |
| `fsp-demo-04` | `d98cba91-ae78-4d5a-bfb0-130ca2c6b02f` | Патчев Всеволод Сергеевич | 2 из 181, международные, алгоритмическое 2025; всего результатов: 5; КМС |
| `fsp-demo-05` | `c7d48424-d766-41dd-a146-bb846cbf12c9` | Байтов Всеволод Дмитриевич | 25 из 272, всероссийские, отбор, ИБ 2026 |
| `fsp-demo-06` | `800a9cc8-5bcb-4dac-9828-accc71a830e4` | Драйверов Павел Дмитриевич | 10 из 67, всероссийские, алгоритмическое 2025; всего результатов: 6; I р. |
| `fsp-demo-07` | `ced232ac-5bea-42b9-81bf-2756c325b433` | Деплоев Никита Романович | только результаты, ждущие протокола |
| `fsp-demo-08` | `0473a37f-91fd-4447-9360-f35c2e0260db` | Сервлетов Арсений Олегович | только результаты, ждущие протокола |
| `fsp-demo-09` | `c3323cfc-bcfe-42ab-a237-3dc7ee0a9b36` | Коммитов Олег Андреевич | только разряд, результатов в реестре нет; КМС |
| `fsp-demo-10` | `c65e176b-ba86-4203-a194-bf6ce57859cf` | Компиляторов Максим Романович | только разряд, результатов в реестре нет; I р. |
| `fsp-demo-11` | `4b9d66eb-d468-464d-b17e-396e6af1749d` | Бэкапова Злата Михайловна | в реестре нет результатов |
| `fsp-demo-12` | `8339bf64-8948-449b-81bb-2e0eb68d246c` | Мержев Арсений Евгеньевич | в реестре нет результатов |
| `fsp-demo-13` | `52e12b87-a95a-4b9a-8c10-32b5d13a0c04` | Коммитова София Николаевна | в реестре нет результатов |
| `fsp-demo-14` | `87648a7a-c05b-48fd-b515-c6d9db523c6b` | Тензоров Кирилл Игоревич | в реестре нет результатов; тренер |
| `fsp-demo-15` | `2a5d3fd6-154e-41fb-b10d-f301bb9387bf` | Рекурсина Злата Алексеевна | 771 из 915, всероссийские, отбор, алгоритмическое 2026; всего результатов: 2; главный судья |
| `fsp-demo-16` | `eeee6dce-b472-444b-8ff6-f4a60f297241` | Курсоров Артём Алексеевич | 5–8 из 38, всероссийские, робототехника 2025; всего результатов: 3; I р.; судья-эксперт |
| `fsp-demo-17` | `8c2bb7b8-7eea-4a32-8cf3-414a368dbfe8` | Кэшев Артём Павлович | 84 из 235, всероссийские, продуктовое 2026; всего результатов: 2; тренер; роль frontend |
| `fsp-demo-18` | `43d2def2-48bb-4deb-ba70-5ffa4d4f7369` | Логгеров Егор Павлович | 31 из 235, всероссийские, продуктовое 2026; всего результатов: 2; I р. (истёк); роль backend |
| `fsp-demo-19` | `562fb333-ea35-48b4-b157-96542b932cd4` | Массивов Лев Евгеньевич | 84 из 235, всероссийские, продуктовое 2026; всего результатов: 3; несовершеннолетний, 17 лет; роль ml |
| `fsp-demo-20` | `34a8406c-f18f-49ef-8769-a34a4161e778` | Компиляторов Даниил Олегович | 6 из 72, всероссийские, БАС 2025; всего результатов: 2; ждёт протокола: 1; I р. |
| `fsp-demo-21` | `5c9b7091-cfec-4f88-9fef-8875cda40c98` | Стеков Фёдор Дмитриевич | 14 из 44, региональные, алгоритмическое 2024 |
| `fsp-demo-22` | `54e32dfe-4b66-45d4-b002-e451dbddbf71` | Триггеров Даниил Сергеевич | 44 из 235, всероссийские, продуктовое 2026; всего результатов: 2; II р.; роль frontend |
| `fsp-demo-23` | `e0ca0b59-2788-4ebc-b6d5-4f2607c2006b` | Лямбдин Артём Олегович | 7 из 72, всероссийские, БАС 2025; всего результатов: 2; ждёт протокола: 1; I р. |
| `fsp-demo-24` | `8b56020c-ba6f-4c6d-a230-d03ec06c4ba0` | Токенов Матвей Сергеевич | 20 из 181, международные, алгоритмическое 2025; всего результатов: 5; I р. |
| `fsp-demo-25` | `72e54fca-351b-4127-98ac-faacc93bcf6a` | Битмапов Даниил Олегович | 1 из 72, всероссийские, БАС 2025; всего результатов: 3; ждёт протокола: 1; МС |
| `fsp-demo-26` | `2410a7bf-ec13-4147-be40-1452ea8d8ab1` | Нейронова Полина Андреевна | 5 из 44, всероссийские, продуктовое 2026; всего результатов: 4; I р.; роль pm |
| `fsp-demo-27` | `5a0b204f-e2b0-4503-a624-984ce8c4fbb0` | Сокетов Павел Романович | 1 из 103, всероссийские, продуктовое 2025; всего результатов: 4; МС; роль frontend |
| `fsp-demo-28` | `d8a63131-49c6-4f92-93ae-503e55ca2c7e` | Индексов Кирилл Сергеевич | 6 из 72, всероссийские, алгоритмическое 2026; всего результатов: 3; I р. |
| `fsp-demo-29` | `d2e37133-4824-4c62-8e83-476037118762` | Векторов Богдан Викторович | 7 из 35, региональные, продуктовое 2025; роль frontend |
| `fsp-demo-30` | `990dfcc5-287b-4c20-8221-a826e184799a` | Пикселев Семён Андреевич | 3 из 38, всероссийские, робототехника 2025; всего результатов: 2; КМС |
<!-- demo-users:end -->
