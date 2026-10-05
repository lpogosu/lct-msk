"""http.cache.* — HTTP-кэш браузера: свежесть max-age, ревалидация по ETag (RFC 9111)."""

from __future__ import annotations

import random
import re
from typing import Any

from app.testing.bank.base import Family, Instance, Rendered, register
from app.testing.bank.families.shared._common import SPECS, explain, text_distractors

_PATHS = (
    "/api/v1/currencies", "/api/v1/catalog/categories", "/api/v1/settings", "/api/v1/regions",
    "/api/v1/feature-flags", "/static/config.json", "/api/v1/delivery/zones", "/api/v1/tariffs",
)
_MAX_AGES = (60, 90, 120, 180, 240, 300)
_PAIR = re.compile(r"(\d+)\s*[:=\-–—]\s*(\d{3})")

HINTS = {
    "no_refresh_after_304": "ответ 304 обновляет сохранённый ответ (RFC 9111 §4.3.4): срок свежести "
    "max-age снова отсчитывается от момента ревалидации, а не от последнего 200.",
    "no_conditional": "устаревший ответ с ETag браузер не выбрасывает, а ревалидирует условным запросом "
    "If-None-Match; если ресурс не менялся, сервер отвечает 304 без тела.",
    "always_revalidate": "пока возраст ответа меньше max-age, браузер отдаёт его из кэша, не обращаясь "
    "к серверу (так ведёт себя no-cache, а не max-age).",
    "change_immediate": "браузер не узнаёт об изменении на сервере, пока ответ свежий: до истечения max-age "
    "показывается старая версия.",
}


def _simulate(times: list[int], max_age: int, change_at: int, mode: str) -> list[tuple[int, int]]:
    """Обращения к серверу (момент, код). mode: correct | no_refresh_after_304 | no_conditional |
    always_revalidate | change_immediate."""
    out = [(0, 200)]
    validated_at = 0  # от какого момента считается возраст сохранённого ответа
    stored_ver = 0  # версия ресурса в кэше (0 — до изменения, 1 — после)
    for t in times[1:]:
        server_ver = 0 if t < change_at else 1
        fresh = t - validated_at < max_age  # RFC 9111 §4.2: свеж, пока возраст < freshness lifetime
        if mode == "always_revalidate":
            fresh = False
        if mode == "change_immediate" and server_ver != stored_ver:
            fresh = False
        if fresh:
            continue
        if mode == "no_conditional" or server_ver != stored_ver:
            out.append((t, 200))
            stored_ver = server_ver
            validated_at = t
        else:
            out.append((t, 304))
            if mode != "no_refresh_after_304":
                validated_at = t
    return out


def _fmt(pairs: list[tuple[int, int]]) -> str:
    return " ".join(f"{t}:{c}" for t, c in pairs)


def _hex(rng: random.Random) -> str:
    return f"{rng.getrandbits(16):04x}"


@register
class CacheRevalidationTimeline(Family):
    id = "http.cache.revalidation"
    title = "HTTP-кэш: max-age, ETag и ревалидация по шкале времени"
    specs = SPECS
    module = "http_api"
    format = "predict_output"
    target_grade = "middle"
    position = 0.0
    c = 0.0
    time_limit_s = 150
    is_anchor = True
    enemies = ("http.status.scenario",)

    def normalize(self, answer: Any) -> Any:
        if not isinstance(answer, str):
            return answer
        found = _PAIR.findall(answer)
        if not found:  # «0 200 130 304» — пары без двоеточий
            nums = re.findall(r"\d+", answer)
            found = list(zip(nums[::2], nums[1::2], strict=False)) if len(nums) % 2 == 0 else []
        pairs = {(int(t), int(c)) for t, c in found}
        if not pairs:
            return answer.strip()
        # первую пару 0:200 подсказка называет сама — её пропуск не делает ответ неверным
        pairs.add((0, 200))
        return _fmt(sorted(pairs))

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        path = rng.choice(_PATHS)
        modes = ("no_refresh_after_304", "no_conditional", "always_revalidate", "change_immediate")
        while True:
            max_age = rng.choice(_MAX_AGES)
            step = 5
            times = [0]
            for _ in range(5):
                times.append(times[-1] + step * rng.randint(max_age // 15, (3 * max_age) // 10))
            # ни одна пара моментов не попадает ровно на границу свежести — без «ловушек на равенство»
            if any(b - a == max_age for i, a in enumerate(times) for b in times[i + 1:]):
                continue
            change_at = rng.randint(times[2] + 1, times[-1] - 1)
            if change_at in times:
                continue
            key_pairs = _simulate(times, max_age, change_at, "correct")
            codes = [c for _, c in key_pairs[1:]]
            if 304 not in codes or 200 not in codes:
                continue
            alt = {m: _fmt(_simulate(times, max_age, change_at, m)) for m in modes}
            key = _fmt(key_pairs)
            if alt["no_refresh_after_304"] == key or sum(v != key for v in alt.values()) < 3:
                continue
            break

        distractors = text_distractors(key, [(v, m) for m, v in alt.items()], self.normalize)
        etag1, etag2 = _hex(rng), _hex(rng)
        while etag2 == etag1:
            etag2 = _hex(rng)
        code = (
            "t = 0 с — первый ответ сервера:\n"
            "HTTP/1.1 200 OK\n"
            "Content-Type: application/json\n"
            f"Cache-Control: max-age={max_age}\n"
            f'ETag: "{etag1}"\n'
            "\n"
            f't = {change_at} с — данные на сервере обновились, новый ETag: "{etag2}"\n'
            "\n"
            f'fetch("{path}") вызывается в моменты (с): {", ".join(map(str, times))}'
        )
        prompt = (
            "Страница периодически запрашивает справочник через fetch() с настройками по умолчанию. "
            "Браузер кэширует ответы по RFC 9111; задержкой сети пренебрегаем, часы браузера и сервера "
            f"совпадают. Каждый ответ сервера (и 200, и 304) приходит с заголовками Cache-Control: "
            f"max-age={max_age} и текущим ETag.\n\n"
            "В какие моменты браузер обратится к серверу и какой код ответа получит? Вызовы, которые "
            "браузер обслужит из кэша без обращения к серверу, не указывайте."
        )
        steps = ["t=0: кэш пуст → запрос, 200."]
        validated_at, ver = 0, 0
        for t in times[1:]:
            age = t - validated_at
            if age < max_age:
                steps.append(f"t={t}: возраст {age} < {max_age} → из кэша, без запроса.")
                continue
            server_ver = 0 if t < change_at else 1
            if server_ver == ver:
                steps.append(f"t={t}: возраст {age} ≥ {max_age} → If-None-Match: \"{etag1 if ver == 0 else etag2}\" "
                             "→ 304, свежесть отсчитывается заново.")
            else:
                steps.append(f"t={t}: возраст {age} ≥ {max_age} → If-None-Match: \"{etag1}\", но ресурс изменился "
                             "→ 200 с новым ETag.")
                ver = server_ver
            validated_at = t
        body = "\n".join(steps) + f"\nОтвет: {key}."
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt=prompt,
                code=code,
                language="text",
                answer_mode="text",
                answer_hint="Пары «момент:код» через пробел по возрастанию времени; первая пара — 0:200.",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
