"""http.rest.* — ревью таблицы маршрутов REST API: найти нарушение и выбрать исправление (bug_line)."""

from __future__ import annotations

import random

from app.testing.bank.base import Instance, Rendered, canonical, register
from app.testing.bank.families.shared._common import SPECS, LineChoiceFamily, build_choice, explain

# (коллекция, вложенная коллекция, имя id, имя id вложенного, «список …», «… (вин.)»,
#  «вложенный список», «добавить …», «удалить …»)
_RESOURCES = (
    ("orders", "items", "orderId", "itemId", "список заказов", "заказ", "позиции заказа",
     "добавить позицию", "удалить позицию"),
    ("projects", "members", "projectId", "userId", "список проектов", "проект", "участники проекта",
     "добавить участника", "исключить участника"),
    ("tickets", "comments", "ticketId", "commentId", "список обращений", "обращение", "комментарии к обращению",
     "добавить комментарий", "удалить комментарий"),
    ("articles", "tags", "articleId", "tag", "список статей", "статью", "теги статьи", "добавить тег", "снять тег"),
    ("invoices", "lines", "invoiceId", "lineId", "список счетов", "счёт", "строки счёта", "добавить строку",
     "удалить строку"),
    ("courses", "lessons", "courseId", "lessonId", "список курсов", "курс", "уроки курса", "добавить урок",
     "удалить урок"),
    ("playlists", "tracks", "playlistId", "trackId", "список плейлистов", "плейлист", "треки плейлиста",
     "добавить трек", "убрать трек"),
    ("warehouses", "shipments", "warehouseId", "shipmentId", "список складов", "склад", "поставки склада",
     "оформить поставку", "отменить поставку"),
)
_PREFIXES = ("/api/v1", "/api/v2", "/v1", "/api")

HINTS = {
    "get_mutation": "GET обязан быть безопасным: его повторяют краулеры, предзагрузка и кэши, поэтому "
    "изменение данных через GET приводит к случайным изменениям.",
    "verb_in_path": "глагол в пути — это RPC-стиль: действие в REST выражает метод HTTP, а путь называет ресурс.",
    "put_replace": "PUT заменяет ресурс целиком переданным представлением — он не удаляет и не дополняет.",
    "item_vs_collection": "путь с {id} адресует один ресурс; список отдаёт коллекция.",
    "created_for_read": "201 Created означает, что запрос создал ресурс; успешное чтение — 200 OK.",
    "put_for_create": "PUT кладёт ресурс по URI, который знает клиент; если id присваивает сервер, "
    "создают через POST в коллекцию.",
    "patch_for_create": "PATCH изменяет существующий ресурс, а не создаёт новые элементы коллекции.",
    "no_content_with_body": "204 No Content — ответ без тела, а клиенту нужен сам ресурс.",
    "accepted_async": "202 Accepted — запрос принят к асинхронной обработке; для чтения готового ресурса не подходит.",
    "redirect": "302 Found — перенаправление на другой URI, ресурс в ответе не возвращается.",
    "put_non_idempotent": "PUT по определению идемпотентен: повтор не должен добавлять ещё один элемент.",
    "replace_parent": "PUT на родительский ресурс ради одного вложенного элемента заменит его целиком — "
    "это гонки и потеря чужих изменений.",
    "wrong_method": "DELETE удаляет ресурс, а не изменяет его поля.",
}


def _route(method: str, path: str, status: int) -> str:
    return f"{method} {path} → {status}"


@register
class RestRouteReview(LineChoiceFamily):
    id = "http.rest.route_review"
    title = "Ревью REST-маршрутов: найти нарушение и исправить"
    specs = SPECS
    module = "http_api"
    format = "bug_line"
    target_grade = "junior"
    position = 0.5
    # spec-testing §3.1: c — вероятность угадать. Все 4 исправления — варианты одного маршрута, поэтому
    # строку они фактически выдают; угадывание сводится к выбору 1 из 4 исправлений (как в frontend_js).
    c = 0.25
    time_limit_s = 120

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        p, s, pid, sid, list_d, acc, sub_list_d, sub_add_d, sub_del_d = rng.choice(_RESOURCES)
        pre = rng.choice(_PREFIXES)
        coll = f"{pre}/{p}"
        item = f"{coll}/{{{pid}}}"
        sub = f"{item}/{s}"
        sub_item = f"{sub}/{{{sid}}}"
        cap = p[0].upper() + p[1:]

        # Эталонная таблица маршрутов: (метод, путь, статус, комментарий)
        routes = {
            "list": ("GET", coll, 200, f"{list_d} (?limit=&offset=)"),
            "create": ("POST", coll, 201, f"создать {acc}"),
            "get": ("GET", item, 200, f"получить {acc}"),
            "patch": ("PATCH", item, 200, "изменить отдельные поля"),
            "delete": ("DELETE", item, 204, f"удалить {acc}"),
            "sublist": ("GET", sub, 200, sub_list_d),
            "subadd": ("POST", sub, 201, sub_add_d),
            "subdel": ("DELETE", sub_item, 204, sub_del_d),
        }
        # Дефекты: цель, дефектная строка, почему, неверные исправления (метод, путь, статус, тег)
        defects = {
            "delete_via_post": (
                "delete", ("POST", f"{item}/delete", 200, f"удалить {acc}"),
                "удаление выражено глаголом в пути и методом POST; в REST это DELETE на сам ресурс, "
                "успешный ответ без тела — 204",
                [("GET", f"{item}/delete", 200, "get_mutation"),
                 ("POST", f"{coll}/delete?id={{{pid}}}", 200, "verb_in_path"),
                 ("PUT", item, 204, "put_replace")],
            ),
            "list_verb": (
                "list", ("GET", f"{pre}/get{cap}", 200, f"{list_d} (?limit=&offset=)"),
                "глагол get в пути дублирует метод GET; коллекция называется существительным во "
                "множественном числе",
                [("POST", f"{pre}/get{cap}", 200, "verb_in_path"),
                 ("GET", item, 200, "item_vs_collection"),
                 ("GET", coll, 201, "created_for_read")],
            ),
            "create_via_put": (
                "create", ("PUT", coll, 201, f"создать {acc} (id присваивает сервер)"),
                "PUT на коллекцию означал бы «заменить всю коллекцию»; создание элемента с серверным id — "
                "POST в коллекцию с ответом 201",
                [("PUT", f"{coll}/new", 201, "put_for_create"),
                 ("GET", f"{coll}/create", 201, "get_mutation"),
                 ("PATCH", coll, 201, "patch_for_create")],
            ),
            "get_201": (
                "get", ("GET", item, 201, f"получить {acc}"),
                "201 Created возвращают, когда запрос создал ресурс; успешное чтение — 200 OK",
                [("GET", item, 204, "no_content_with_body"),
                 ("GET", item, 202, "accepted_async"),
                 ("GET", item, 302, "redirect")],
            ),
            "patch_via_post": (
                "patch", ("POST", f"{item}/update", 200, "изменить отдельные поля"),
                "частичное изменение — метод PATCH на URI самого ресурса, а не POST на «глагольный» путь",
                [("GET", f"{item}/update", 200, "get_mutation"),
                 ("POST", f"{coll}/update/{{{pid}}}", 200, "verb_in_path"),
                 ("DELETE", item, 200, "wrong_method")],
            ),
            "subadd_via_get": (
                "subadd", ("GET", f"{sub}/add?{sid}=…", 200, sub_add_d),
                "GET должен быть безопасным, без побочных эффектов; добавление во вложенную коллекцию — "
                "POST с ответом 201",
                [("GET", f"{sub}?add=…", 200, "get_mutation"),
                 ("POST", f"{sub}/add", 201, "verb_in_path"),
                 ("PUT", item, 200, "replace_parent")],
            ),
            "subadd_via_put": (
                "subadd", ("PUT", sub, 201, f"{sub_add_d} (каждый вызов добавляет новый элемент)"),
                "PUT идемпотентен по определению: повтор запроса не должен добавлять ещё один элемент; "
                "«добавить ещё один» — это POST в коллекцию",
                [("PUT", f"{sub}/new", 201, "put_non_idempotent"),
                 ("GET", f"{sub}/add", 201, "get_mutation"),
                 ("PUT", item, 201, "replace_parent")],
            ),
        }
        defect_name = rng.choice(sorted(defects))
        target, bad, why, wrong = defects[defect_name]

        # 7 строк из 8: выкидываем одну НЕ целевую — набор строк тоже варьируется
        order = list(routes)
        dropped = rng.choice([r for r in order if r != target])
        shown = [r for r in order if r != dropped]
        rows = [bad if r == target else routes[r] for r in shown]
        width = max(len(path) for _, path, _, _ in rows)
        lines = tuple(f"{m:<6} {path:<{width}}  → {st}   # {comment}" for m, path, st, comment in rows)
        line_no = shown.index(target) + 1

        good_m, good_path, good_st, _ = routes[target]
        correct_fix = _route(good_m, good_path, good_st)
        options, fix_id, opt_tags = build_choice(
            rng, correct_fix, [(_route(m, path, st), tag) for m, path, st, tag in wrong], 4
        )
        key = {"line": line_no, "fix": fix_id}
        distractors = {canonical({"line": line_no, "fix": oid}): tag for oid, tag in opt_tags.items()}

        prompt = (
            "Ревью контракта API перед релизом. В таблице маршрутов ровно одна строка нарушает соглашения "
            "REST и семантику HTTP (RFC 9110). Остальные строки корректны.\n\n"
            "Укажите эту строку и выберите, на что её заменить."
        )
        body = f"Строка {line_no}: «{' '.join(lines[line_no - 1].split('#')[0].split())}» — {why}. Исправление: {correct_fix}."
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt=prompt,
                code="\n".join(lines),
                language="text",
                options=options,
                lines=lines,
                answer_mode="line_choice",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, opt_tags.values()),
        )
