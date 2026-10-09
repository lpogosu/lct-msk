"""http.ratelimit.token_bucket.js — JS-версия арифметики ограничителя частоты «ведро токенов» (token bucket).

Та же трассировка, что у http.ratelimit.token_bucket: дробное пополнение, потолок capacity, отказ без списания.
Отбор экземпляров — по Python-двойнику (шаблон и мутанты оригинала), ключ и дистракторы — исполнением JS-класса и его
мутантов в Node (shared/_js.py). Скорости 0.25 и 0.5 точно представимы в двоичной арифметике: ни в Python, ни в JS нет
ошибок округления.
"""

from __future__ import annotations

import random

from app.testing.bank.base import Instance, Rendered, register
from app.testing.bank.families.frontend_js._common import JsSpec, fill
from app.testing.bank.families.shared import http_ratelimit as py
from app.testing.bank.families.shared._common import exec_capture, explain, norm_int_seq, text_distractors
from app.testing.bank.families.shared._js import JS_SPECS, JsTextIntsFamily, js_list, require_same

_TEMPLATE = """class TokenBucket {
  constructor({ capacity, rate, now }) {
    this.capacity = capacity; // максимум токенов в ведре
    this.rate = rate;         // сколько токенов добавляется за секунду
    this.tokens = capacity;   // в начале ведро полное
    this.last = now;          // момент последнего пересчёта, с
  }

  allow(now) {
    const elapsed = now - this.last;
    this.tokens = Math.min(this.capacity, this.tokens + elapsed * this.rate);
    this.last = now;
    if (this.tokens >= 1) {
      this.tokens -= 1;
      return true;
    }
    return false;
  }
}

const <<var>> = new TokenBucket({ capacity: <<capacity>>, rate: <<rate>>, now: <<start>> });
const rejected = [];
for (const t of <<times>>) {
  if (!<<var>>.allow(t)) {
    rejected.push(t);
  }
}
console.log(rejected.join(" "));"""

_REFILL = "Math.min(this.capacity, this.tokens + elapsed * this.rate)"
_DECIDE = (
    "    if (this.tokens >= 1) {\n"
    "      this.tokens -= 1;\n"
    "      return true;\n"
    "    }\n"
    "    return false;\n"
)
# Мутанты = заблуждения о token bucket (те же, что у Python-версии); ключ дистрактора — исполнение мутанта.
_MUTANTS = {
    "no_cap": lambda c: c.replace(_REFILL, "this.tokens + elapsed * this.rate"),
    "integer_refill": lambda c: c.replace(_REFILL, "Math.min(this.capacity, this.tokens + Math.floor(elapsed * this.rate))"),
    "rejected_consume": lambda c: c.replace(_DECIDE, "    this.tokens -= 1;\n    return this.tokens >= 0;\n"),
    "last_on_success": lambda c: c.replace("    this.last = now;\n", "").replace(
        "      this.tokens -= 1;\n", "      this.tokens -= 1;\n      this.last = now;\n"
    ),
}


@register
class TokenBucketRateLimitJs(JsTextIntsFamily):
    id = "http.ratelimit.token_bucket.js"
    title = "Rate limiting на JS: какие запросы отклонит token bucket"
    specs = JS_SPECS
    module = "http_api"
    format = "predict_output"
    # Шкала frontend_js: грейд, pos и якорь перенесены с Python-версии http.ratelimit.token_bucket — пересчитать по данным.
    target_grade = "senior"
    position = 0.0
    time_limit_s = 180
    is_anchor = True

    def build(self, rng: random.Random) -> JsSpec:
        var = rng.choice(("limiter", "bucket", "throttle", "rl"))
        while True:  # те же розыгрыши и тот же отбор, что у Python-версии
            # только скорости, точно представимые в двоичной арифметике: без ошибок округления float
            rate = rng.choice((0.25, 0.5))
            capacity = rng.choice((3, 4))
            times, t = [], rng.randint(0, 600)  # ведро создано в момент первого запроса
            for i in range(10):
                if i:
                    t += 1 if rng.random() < 0.65 else rng.randint(2, int(3 / rate))
                times.append(t)
            py_code = py._TEMPLATE.format(var="b", capacity=capacity, rate=rate, times=times, start=times[0])
            py_key = norm_int_seq(exec_capture(py_code))
            n_rej = len(py_key.split()) if py_key else 0
            if not 2 <= n_rej <= 4:
                continue
            alt = {tag: norm_int_seq(exec_capture(mut(py_code))) for tag, mut in py._MUTANTS.items()}
            if sum(v != py_key and v != "" for v in alt.values()) >= 2:
                break
        code = fill(_TEMPLATE, var=var, capacity=capacity, rate=rate, start=times[0], times=js_list(times))
        progs = {"key": code, **{tag: mut(code) for tag, mut in _MUTANTS.items()}}
        return JsSpec(code, progs, {"capacity": capacity, "rate": rate, "times": times, "py_key": py_key})

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        d = spec.data
        key = norm_int_seq(outs["key"])
        require_same(self.id, seed, "ключ", key, d["py_key"])
        alt = {tag: norm_int_seq(outs[tag]) for tag in _MUTANTS}
        # пустой вывод («отказов не будет») как дистрактор не храним
        distractors = text_distractors(key, [(v, tag) for tag, v in alt.items() if v], norm_int_seq)
        prompt = (
            "Перед публичным API стоит ограничитель частоты запросов «ведро токенов» (token bucket). "
            "Моменты прихода запросов (в секундах) перечислены в цикле. Что выведет программа — "
            "моменты запросов, которые получат 429 Too Many Requests?"
        )
        trace = py._trace(d["capacity"], d["rate"], d["times"])
        body = "Токенов в ведре перед решением:\n" + "\n".join(trace) + f"\nОтвет: {key}."
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt=prompt,
                code=spec.code,
                language="javascript",
                answer_mode="text",
                answer_hint="Числа через пробел — так, как их напечатает console.log(rejected.join(\" \")).",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, py.HINTS, distractors.values()),
        )
