"""Общие помощники пакета frontend_js.

1. Исполнение JS для ключей (spec-testing §3.8: «ключ = исполнение шаблона офлайн»).
   Запуск Node на каждый экземпляр стоит ~70 мс, а setTimeout(0) на Windows — ~15 мс, поэтому программы
   соседних seed'ов (блок BLOCK) исполняются ОДНИМ вызовом run_js: каждая в своём vm-контексте со своим
   console и учётом таймеров. Программы стартуют подряд и выполняются конкурентно: очередь микрозадач и
   список таймеров FIFO, поэтому относительный порядок событий ВНУТРИ программы тот же, что при отдельном
   запуске (все таймеры в банке — с задержкой 0). Результаты кешируются по (family_id, seed).
2. Сборка вариантов ответа (choice) с тегами заблуждений.
3. Нормализация текстовых ответов.
"""

from __future__ import annotations

import json
import os
import random
import re
import tempfile
from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.testing.bank.base import Family, Instance, Option, canonical
from app.testing.bank.execution import ExecutionError, run_js

BLOCK = 50
ERR_PREFIX = "\u0000ERR "

_RUNNER = r"""
const fs = require("fs");
const vm = require("vm");
const util = require("util");
const progs = JSON.parse(fs.readFileSync(__PATH__, "utf8"));
let unhandled = null;
process.on("unhandledRejection", (e) => { unhandled = String((e && e.stack) || e); });
function start(src) {
  return new Promise((resolve) => {
    const out = [];
    const live = new Set();
    let done = false;
    const fmt = (a) => (typeof a === "string" ? a : util.inspect(a));
    const finish = (err) => {
      if (done) return;
      done = true;
      resolve(err ? "\u0000ERR " + err : out.join("\n"));
    };
    const check = () => setImmediate(() => { if (live.size === 0) finish(); });
    const sandbox = {
      console: { log: (...a) => { out.push(a.map(fmt).join(" ")); } },
      setTimeout: (fn, ms, ...args) => {
        const h = setTimeout(() => {
          live.delete(h);
          try { fn(...args); } catch (e) { finish(String((e && e.stack) || e)); }
          check();
        }, ms);
        live.add(h);
        return h;
      },
      clearTimeout: (h) => { if (live.delete(h)) clearTimeout(h); check(); },
      queueMicrotask: (fn) => queueMicrotask(fn),
    };
    vm.createContext(sandbox);
    try {
      vm.runInContext(src, sandbox, { timeout: 2000 });
    } catch (e) {
      finish(String((e && e.stack) || e));
      return;
    }
    check();
  });
}
Promise.all(progs.map(start)).then((res) => {
  if (unhandled) res = res.map(() => "\u0000ERR unhandled rejection: " + unhandled);
  process.stdout.write(JSON.stringify(res));
});
"""


def run_js_many(programs: list[str]) -> list[str]:
    """Исполнить список JS-программ одним процессом Node; вернуть stdout каждой (или ERR_PREFIX + ошибка)."""
    if not programs:
        return []
    fd, path = tempfile.mkstemp(suffix=".json", prefix="vz_js_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(programs, f, ensure_ascii=False)
        raw = run_js(_RUNNER.replace("__PATH__", json.dumps(path)), timeout=60.0)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    res = json.loads(raw)
    if not isinstance(res, list) or len(res) != len(programs):
        raise ExecutionError("JS-раннер вернул неожиданный результат")
    return [str(x) for x in res]


@dataclass
class JsSpec:
    """Результат детерминированного построения экземпляра до исполнения.

    code — то, что видит кандидат; programs — что исполняется (имя → код; ключ берётся из вывода нужных программ в assemble);
    data — всё, что нужно для сборки экземпляра после исполнения.
    """

    code: str
    programs: dict[str, str]
    data: dict[str, Any] = field(default_factory=dict)


_JS_CACHE: dict[tuple[str, int], dict[str, str]] = {}


class JsExecFamily(Family):
    """Семейство, ключ которого получается исполнением JS в Node (пачками, с кешем)."""

    @abstractmethod
    def build(self, rng: random.Random) -> JsSpec:
        """Детерминированно построить код и программы по rng (без исполнения)."""

    @abstractmethod
    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        """Собрать экземпляр по результатам исполнения программ."""

    def generate(self, seed: int) -> Instance:
        spec = self.build(random.Random(seed))
        outs = js_outputs(self, seed)
        for name, out in outs.items():
            if out.startswith(ERR_PREFIX):
                raise ExecutionError(f"{self.id} seed={seed} программа {name}: {out[len(ERR_PREFIX):][:300]}")
        return self.assemble(seed, spec, outs)


def js_outputs(fam: JsExecFamily, seed: int) -> dict[str, str]:
    key = (fam.id, seed)
    if key in _JS_CACHE:
        return _JS_CACHE[key]
    base = seed - seed % BLOCK
    seeds = [s for s in range(base, base + BLOCK) if (fam.id, s) not in _JS_CACHE]
    if seed not in seeds:
        seeds.append(seed)
    names: list[tuple[int, str]] = []
    codes: list[str] = []
    for s in seeds:
        try:
            progs = fam.build(random.Random(s)).programs
        except Exception:  # noqa: BLE001 — ошибка соседнего seed проявится при его собственной генерации
            if s == seed:
                raise
            continue
        for n, c in progs.items():
            names.append((s, n))
            codes.append(c)
    outs = run_js_many(codes)
    fresh: dict[int, dict[str, str]] = {}
    for (s, n), o in zip(names, outs, strict=True):
        fresh.setdefault(s, {})[n] = o
    for s, d in fresh.items():
        _JS_CACHE[(fam.id, s)] = d
    return _JS_CACHE[key]


# ---------------------------------------------------------------- нормализация

_QUOTES = re.compile(r"[\"'`«»“”]")
_SEP = re.compile(r"[,;]")
_WS = re.compile(r"\s+")
# Типографские тире и минус → «-»: автозамена в браузере/ОС не должна делать верный ответ неверным.
_DASHES = str.maketrans({"—": "-", "–": "-", "−": "-", "‒": "-"})


def norm_output(answer: Any) -> str:
    """Вывод console.log: без кавычек, запятые = пробелы, пробелы схлопнуты, регистр и вид тире не важны."""
    s = _QUOTES.sub("", str(answer).translate(_DASHES))
    s = _SEP.sub(" ", s)
    return _WS.sub(" ", s).strip().lower()


def norm_exact(answer: Any) -> str:
    """Значение поля ввода: регистр важен; снимаем внешние пробелы и обрамляющие кавычки."""
    s = _WS.sub(" ", str(answer)).strip()
    for q in ('"', "'", "«", "`"):
        close = "»" if q == "«" else q
        if len(s) >= 2 and s[0] == q and s[-1] == close:
            s = s[1:-1].strip()
    return s


def norm_choice(answer: Any) -> Any:
    if isinstance(answer, str):
        return answer.strip().lower()
    return answer


def norm_line_choice(answer: Any) -> Any:
    if isinstance(answer, dict) and "line" in answer and "fix" in answer:
        try:
            line = int(answer["line"])
        except (TypeError, ValueError):
            return answer
        return {"line": line, "fix": str(answer["fix"]).strip().lower()}
    return answer


# ---------------------------------------------------------------- варианты ответа

OPTION_IDS = "abcdefgh"


@dataclass(frozen=True)
class Choice:
    options: tuple[Option, ...]
    key: str
    distractors: dict[str, str]  # id варианта → тег заблуждения


def make_choice(rng: random.Random, correct: str, wrong: list[tuple[str, str]], k: int = 4) -> Choice:
    """Правильный текст + k−1 различных неверных (по приоритету списка), перемешать по rng."""
    seen = {correct}
    picked: list[tuple[str, str]] = []
    for text, tag in wrong:
        if text in seen:
            continue
        seen.add(text)
        picked.append((text, tag))
        if len(picked) == k - 1:
            break
    if len(picked) < k - 1:
        raise ValueError(f"недостаточно различных дистракторов: {len(picked)} < {k - 1}")
    items: list[tuple[str, str | None]] = [(correct, None), *picked]
    rng.shuffle(items)
    options = tuple(Option(OPTION_IDS[i], t) for i, (t, _) in enumerate(items))
    key = next(OPTION_IDS[i] for i, (_, tag) in enumerate(items) if tag is None)
    distractors = {OPTION_IDS[i]: tag for i, (_, tag) in enumerate(items) if tag is not None}
    return Choice(options, key, distractors)


def text_distractors(key: str, wrong: list[tuple[str, str]]) -> dict[str, str]:
    """Нормализованные неверные ответы → тег; совпадающие с ключом и повторы отбрасываются."""
    res: dict[str, str] = {}
    for ans, tag in wrong:
        a = canonical(ans)
        if a != canonical(key) and a not in res and a != "":
            res[a] = tag
    return res


def explain(main: str, wrong: dict[str, str], hints: dict[str, str], show: dict[str, str] | None = None) -> str:
    """Разбор: основное объяснение + «если ответили X — ...» по каждому заблуждению."""
    parts = [main.strip()]
    lines = []
    for ans, tag in wrong.items():
        hint = hints.get(tag)
        if not hint:
            continue
        shown = show.get(ans, ans) if show else ans
        lines.append(f"• «{shown}» — {hint}")
    if lines:
        parts.append("Типичные ошибки:\n" + "\n".join(lines))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- мелочи

_PH = re.compile(r"<<(\w+)>>")


def fill(template: str, **kw: Any) -> str:
    """Подстановка <<name>> в шаблон кода (в JS слишком много фигурных скобок для str.format)."""

    def rep(m: re.Match[str]) -> str:
        return str(kw[m.group(1)])

    return _PH.sub(rep, template)


def plural_ru(n: int, forms: tuple[str, str, str]) -> str:
    """plural_ru(3, ("раз", "раза", "раз")) → «раза»."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def times_ru(n: int) -> str:
    return f"{n} {plural_ru(n, ('раз', 'раза', 'раз'))}"


def pick(rng: random.Random, pool: list[Any] | tuple[Any, ...]) -> Any:
    return pool[rng.randrange(len(pool))]


def fmt_num(x: float) -> str:
    """Число для ответа: целое без точки, иначе до 2 знаков без хвостовых нулей."""
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.2f}".rstrip("0").rstrip(".")
