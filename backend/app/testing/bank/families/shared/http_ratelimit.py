"""http.ratelimit.* — арифметика ограничителя частоты «ведро токенов» (token bucket)."""

from __future__ import annotations

import random

from app.testing.bank.base import Instance, Rendered, register
from app.testing.bank.families.shared._common import (
    PY_SPECS,
    TextIntsFamily,
    exec_capture,
    explain,
    norm_int_seq,
    text_distractors,
)

_TEMPLATE = '''class TokenBucket:
    def __init__(self, capacity, rate, now):
        self.capacity = capacity  # максимум токенов в ведре
        self.rate = rate          # сколько токенов добавляется за секунду
        self.tokens = capacity    # в начале ведро полное
        self.last = now           # момент последнего пересчёта, с

    def allow(self, now):
        elapsed = now - self.last
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


{var} = TokenBucket(capacity={capacity}, rate={rate}, now={start})
rejected = []
for t in {times}:
    if not {var}.allow(t):
        rejected.append(t)
print(*rejected)
'''

_REFILL = "min(self.capacity, self.tokens + elapsed * self.rate)"
_DECIDE = (
    "        if self.tokens >= 1:\n"
    "            self.tokens -= 1\n"
    "            return True\n"
    "        return False\n"
)
# Мутанты = заблуждения о token bucket; ключ дистрактора получаем исполнением мутанта.
_MUTANTS = {
    "no_cap": lambda c: c.replace(_REFILL, "self.tokens + elapsed * self.rate"),
    "integer_refill": lambda c: c.replace(_REFILL, "min(self.capacity, self.tokens + int(elapsed * self.rate))"),
    "rejected_consume": lambda c: c.replace(_DECIDE, "        self.tokens -= 1\n        return self.tokens >= 0\n"),
    "last_on_success": lambda c: c.replace("        self.last = now\n", "").replace(
        "            self.tokens -= 1\n", "            self.tokens -= 1\n            self.last = now\n"
    ),
}

HINTS = {
    "no_cap": "ведро не копит больше capacity токенов: долгий простой даёт максимум capacity запросов подряд.",
    "integer_refill": "пополнение непрерывное: дробные доли токена сохраняются и складываются между запросами.",
    "rejected_consume": "отклонённый запрос токен не тратит — в этой реализации списание только при успехе.",
    "last_on_success": "момент пересчёта last обновляется при каждом вызове, иначе одно и то же время "
    "простоя засчитывается в пополнение несколько раз.",
}


def _trace(capacity: int, rate: float, times: list[int]) -> list[str]:
    tokens, last, out = float(capacity), times[0], []
    for t in times:
        tokens = min(capacity, tokens + (t - last) * rate)
        last = t
        if tokens >= 1:
            out.append(f"t={t}: {tokens:g} → {tokens - 1:g}, пропущен")
            tokens -= 1
        else:
            out.append(f"t={t}: {tokens:g} < 1 → 429")
    return out


@register
class TokenBucketRateLimit(TextIntsFamily):
    id = "http.ratelimit.token_bucket"
    title = "Rate limiting: какие запросы отклонит token bucket"
    specs = (*PY_SPECS, "qa", "devops")  # фронтендеру — JS-версия «<id>.js» (shared/_js.py)
    module = "http_api"
    format = "predict_output"
    target_grade = "senior"
    position = 0.0
    time_limit_s = 180
    is_anchor = True

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        var = rng.choice(("limiter", "bucket", "throttle", "rl"))
        while True:
            # только скорости, точно представимые в двоичной арифметике: без ошибок округления float
            rate = rng.choice((0.25, 0.5))
            capacity = rng.choice((3, 4))
            times, t = [], rng.randint(0, 600)  # ведро создано в момент первого запроса
            for i in range(10):
                if i:
                    t += 1 if rng.random() < 0.65 else rng.randint(2, int(3 / rate))
                times.append(t)
            code = _TEMPLATE.format(var=var, capacity=capacity, rate=rate, times=times, start=times[0])
            key = norm_int_seq(exec_capture(code))
            n_rej = len(key.split()) if key else 0
            if not 2 <= n_rej <= 4:
                continue
            alt = {tag: norm_int_seq(exec_capture(mut(code))) for tag, mut in _MUTANTS.items()}
            if sum(v != key and v != "" for v in alt.values()) >= 2:
                break

        # пустой вывод («отказов не будет») как дистрактор не храним
        distractors = text_distractors(key, [(v, tag) for tag, v in alt.items() if v], norm_int_seq)
        prompt = (
            "Перед публичным API стоит ограничитель частоты запросов «ведро токенов» (token bucket). "
            "Моменты прихода запросов (в секундах) перечислены в цикле. Что выведет программа — "
            "моменты запросов, которые получат 429 Too Many Requests?"
        )
        body = "Токенов в ведре перед решением:\n" + "\n".join(_trace(capacity, rate, times)) + f"\nОтвет: {key}."
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt=prompt,
                code=code.rstrip("\n"),
                language="python",
                answer_mode="text",
                answer_hint="Числа через пробел — так, как их напечатает print(*rejected).",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
