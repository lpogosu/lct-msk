# Каталог семейств: Frontend (JS/TS)

Пакет `app/testing/bank/families/frontend_js`, специализация `frontend_js`. 25 семейств: js_core 12, react 8, html_css 5.
Грейды: стажёр 4 (16%), junior 8 (32%), middle 7 (28%), senior 6 (24%). Доля `mcq_concept` — 2 из 25 (8%).
Якоря (⚓, b не перекалибровывается) стоят у каждого порога в js_core и react и у трёх порогов в html_css.
Приор сложности: `b = cut(грейд) + 0.35·pos` (spec-testing §3.2), где cut = −2 / −1 / 0 / +1.

Откуда берётся ключ:
- **Node** — ключ равен stdout сгенерированного кода в Node.js (`execution.run_js`; программы соседних seed'ов исполняются одним вызовом, у каждой свой vm-контекст). Дистракторы — stdout мутантов того же кода, по одному мутанту на заблуждение.
- **Node-проверка** — bug_line: исходный код и каждое исправление исполняются со скрытыми моками API. Верное исправление обязано давать ожидаемый вывод, остальные — нет, иначе генерация падает.
- **Модель** — точная модель сценария на Python: React 18 (createRoot, production, без StrictMode), сужение типов TS, CSS-решатели, accname. Сценарии ограничены случаями, где поведение однозначно и описано в документации.

Формат ответа: `text` — кандидат вводит вывод (c = 0); `choice` — 4 варианта (c = 0.25); `line_choice` — номер строки (с 1, как в редакторе) плюс одно исправление из 4.
Нормализация `text`: кавычки, запятые, лишние пробелы, регистр и вид тире (— – − → -) не важны (кроме задания про контролируемое поле ввода, где регистр важен); ключи состоят только из символов, которые набираются с клавиатуры (проверяется тестом).

| id | Название | Модуль | Формат / ответ | Грейд, pos (b) | Ключ | Что измеряет |
|---|---|---|---|---|---|---|
| `js.array_methods` ⚓ | filter → map → reduce по списку заказов | js_core | predict_output / text | intern, −0.3 (−2.1) | Node | Чтение цепочки filter/map/reduce на данных с сервера |
| `js.equality_coercion` | Строки из формы: +, *, == и === | js_core | predict_output / text | intern, +0.5 (−1.8) | Node | Приведение типов значений из input: склейка и арифметика, «+» слева направо, == и === |
| `js.optional_nullish` ⚓ | ?? против \|\| и опциональная цепочка ?. | js_core | predict_output / text | junior, −0.5 (−1.2) | Node | Значения по умолчанию для 0, "", false и null; поведение `?.` |
| `js.scope_tdz` | var, let, const: подъём и TDZ | js_core | predict_output / text | junior, 0 (−1.0) | Node | Подъём var в функции, блочная область let, временная мёртвая зона const |
| `js.destructuring_spread` | Слияние настроек: spread и значения по умолчанию | js_core | predict_output / text | junior, +0.3 (−0.9) | Node | Порядок spread, undefined перезаписывает, default только для undefined, пропуски в массиве |
| `js.closure_loop` | Замыкания в цикле: var, let и общая переменная | js_core | predict_output / text | junior, +0.7 (−0.8) | Node | Замыкание хранит переменную, а не значение; привязка let на каждой итерации |
| `js.this_binding` | this: метод, стрелочное поле, bind и call | js_core | predict_output / text | middle, −0.5 (−0.2) | Node | Динамический this, стрелочное поле класса, bind сильнее call, потерянный this в строгом режиме |
| `js.promise_chain` ⚓ | Цепочка промисов: throw, catch, finally | js_core | predict_output / text | middle, 0 (0.0) | Node | Пропуск then при ошибке, восстановление после catch, then без return, finally не меняет значение |
| `js.ts_narrowing` | TypeScript: сужение типов | js_core | mcq_concept / choice | middle, +0.3 (+0.1) | Модель | typeof (с typeof null), истинность, == и === null, оператор in, дискриминант в switch |
| `js.event_loop_order` ⚓ | Порядок вывода: синхронный код, микрозадачи, таймеры | js_core | predict_output / text (метки) | senior, −0.5 (+0.8) | Node | Event loop: синхронный исполнитель Promise, await, вложенные микрозадачи, setTimeout |
| `js.async_await_errors` | Ошибки async/await: найти строку и исправление | js_core | bug_line / line_choice | senior, 0 (+1.0) | Node-проверка | forEach с async, return без await в try, промис в условии |
| `js.prototype_chain` | Прототипы: общее состояние и собственные свойства | js_core | predict_output / text | senior, +0.3 (+1.1) | Node | Чтение по цепочке, запись создаёт своё свойство, общий изменяемый объект на прототипе |
| `react.render_count` ⚓ | Сколько раз отрендерится компонент | react | predict_output / choice | intern, +0.5 (−1.8) | Модель | Рендер родителя перерисовывает детей; локальное состояние — только свой компонент |
| `react.controlled_input` | Контролируемое поле ввода: что увидит пользователь | react | predict_output / text | junior, −0.3 (−1.1) | Модель | Контролируемый input: фильтры, ограничение длины, trim на каждом нажатии |
| `react.batched_updates` ⚓ | Очередь обновлений состояния и batching | react | predict_output / text | junior, +0.3 (−0.9) | Модель | setX(значение) и setX(prev => …) в одной очереди, один рендер на клик |
| `react.effect_deps` | useEffect: зависимости, очистка и порядок запуска | react | predict_output / choice | middle, −0.3 (−0.1) | Модель | Какие эффекты перезапускаются ([], [dep], без массива, объект в зависимостях); очистки идут раньше тел |
| `react.list_keys` ⚓ | Ключи в списках: чьё состояние у строки | react | predict_output / choice | middle, 0 (0.0) | Модель | key={index} и key={id}: состояние строки при вставке, удалении, перевороте списка |
| `react.stale_closure` | Устаревшее замыкание в useEffect | react | predict_output / text | middle, +0.5 (+0.2) | Модель | Подписка в useEffect([]) видит состояние первого рендера; функциональное обновление это исправляет |
| `react.memo_callback` ⚓ | memo, useCallback, useMemo: когда строка перерисуется | react | predict_output / choice | senior, −0.3 (+0.9) | Модель | Сравнение пропсов через Object.is, зависимости useCallback/useMemo, проп-флаг active |
| `react.derived_state` | Производное состояние: найти строку и исправление | react | bug_line / line_choice | senior, 0 (+1.0) | Модель | Состояние, скопированное из пропсов, «застывает»; лечение: вычислять при рендере или ставить key |
| `css.box_model` ⚓ | Блочная модель: ширина содержимого и offsetWidth | html_css | predict_output / text | intern, +0.5 (−1.8) | Модель | content-box и border-box, сокращённые записи padding, margin не входит в offsetWidth |
| `css.specificity` ⚓ | Каскад: специфичность, !important и порядок правил | html_css | predict_output / choice | junior, 0 (−1.0) | Модель | Совпадение селектора (включая «>»), специфичность (a,b,c), !important, порядок правил |
| `css.a11y_name` | Доступное имя кнопки и поля | html_css | mcq_concept / choice | junior, +0.5 (−0.8) | Модель | Приоритет labelledby → aria-label → label/содержимое → title → placeholder; alt, aria-hidden, describedby |
| `css.flex_distribution` ⚓ | Flexbox: как делится свободное место | html_css | predict_output / text | middle, 0 (0.0) | Модель | flex-grow и flex-basis, `flex: N` даёт базу 0, gap, flex: none и auto |
| `css.stacking_context` | z-index и контексты наложения | html_css | predict_output / choice | senior, −0.3 (+0.9) | Модель | Кто создаёт контекст (z-index, opacity, transform, fixed); z-index потомка заперт в контексте родителя |

Взаимоисключающие пары (enemies): `js.scope_tdz` ↔ `js.closure_loop`, `react.batched_updates` ↔ `react.stale_closure`,
`react.render_count` ↔ `react.memo_callback`, `react.effect_deps` ↔ `react.derived_state` — в каждой паре одно семейство раскрывает механизм, на котором стоит другое.

Каждое семейство проходит CI-гейты (`tests/test_bank_gates.py`): 200 экземпляров, детерминизм, не меньше 95% различных
ключей для `text`. Собственные тесты — `tests/test_bank_frontend_js.py`: ключ проходит проверку, дистракторы — нет, плюс контрольные примеры всех решателей.
