Локальные ассеты документации API (/api/docs, /api/redoc): страницы не обращаются к внешним CDN и шрифтам.

swagger-ui-bundle.js, swagger-ui.css, favicon-32x32.png — swagger-ui-dist 5.33.1 (Apache-2.0, LICENSE.swagger-ui-dist.txt, NOTICE.swagger-ui-dist.txt).
redoc.standalone.js — redoc 2.5.4 (MIT, LICENSE.redoc.txt; сторонние лицензии внутри бандла — redoc.standalone.js.LICENSE.txt).

Обновление: npm pack swagger-ui-dist@<версия> redoc@<версия>, скопировать файлы из пакетов (bundles/redoc.standalone.js у redoc),
обновить номера версий здесь. Источники (*.map) не включаем: размер.
