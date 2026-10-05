"""YAML и docker compose: якоря и merge-ключи (<<), приоритеты переменных окружения.

Ключ merge-семейства — настоящий парсер PyYAML (yaml.safe_load): он реализует спецификацию merge key. Дистракторы —
собственный вычислитель по дереву узлов (yaml.compose) с заблуждениями: явный ключ проигрывает слиянию, в списке слияний
побеждает последний, слияние глубокое. Compose-семейство — точный решатель: интерполяция `${VAR:-default}`
(shell > .env > значение по умолчанию), `environment` поверх `env_file`, следующий env_file поверх предыдущего.
"""

from __future__ import annotations

import random
import re
from typing import Any

import yaml
from yaml.constructor import SafeConstructor

from app.testing.bank.base import register
from app.testing.bank.families.devops._common import (
    MODULE,
    SPECS,
    GenerationRetry,
    OpsFamily,
    Scenario,
    ScenarioError,
)

# ============================================================== якоря и merge

_MERGE_TAG = "tag:yaml.org,2002:merge"
_SCALAR_KEYS = ["mem_limit", "cpu_shares", "pids_limit"]
_MAPPINGS = ["environment", "labels"]
_VARS = ["LOG_LEVEL", "CACHE_TTL", "MAX_CONN", "RETRIES", "FEATURE_FLAGS", "QUEUE_NAME", "REGION", "SENTRY_DSN",
         "WORKERS", "PAGE_SIZE"]
_VALUES = ["debug", "info", "warn", "eu", "us", "prod", "stage", "fast", "slow", "strict", "lazy"]
_SERVICES = ["web", "api", "worker", "cron", "bot", "admin", "gateway", "queue", "mailer", "search"]
_ANCHORS = [("base", "net", "mid"), ("defaults", "limits", "tuned"), ("common", "tight", "derived")]
_CONSTRUCTOR = SafeConstructor()


def _scalar(node: yaml.Node) -> Any:
    return _CONSTRUCTOR.construct_object(node, deep=True)


def evaluate(node: yaml.Node, *, merge_wins: bool = False, later_wins: bool = False, deep: bool = False) -> Any:
    """Собственный вычислитель дерева YAML. Без флагов совпадает с yaml.safe_load (проверяет тест), флаги — заблуждения."""
    if isinstance(node, yaml.ScalarNode):
        return _scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return [evaluate(n, merge_wins=merge_wins, later_wins=later_wins, deep=deep) for n in node.value]
    if not isinstance(node, yaml.MappingNode):
        raise ScenarioError("неожиданный узел YAML")
    merged: dict[Any, Any] = {}
    explicit: dict[Any, Any] = {}
    for k_node, v_node in node.value:
        if k_node.tag == _MERGE_TAG:
            sources = list(v_node.value) if isinstance(v_node, yaml.SequenceNode) else [v_node]
            maps = [evaluate(m, merge_wins=merge_wins, later_wins=later_wins, deep=deep) for m in sources]
            for m in maps if later_wins else list(reversed(maps)):
                merged.update(m)
        else:
            explicit[_scalar(k_node)] = evaluate(v_node, merge_wins=merge_wins, later_wins=later_wins, deep=deep)
    if deep:
        for key, val in explicit.items():
            if isinstance(val, dict) and isinstance(merged.get(key), dict):
                explicit[key] = {**merged[key], **val}
    return {**explicit, **merged} if merge_wins else {**merged, **explicit}


def _service(doc: dict[str, Any], name: str) -> dict[str, Any]:
    services = doc["services"]
    if name not in services or not isinstance(services[name], dict):
        raise ScenarioError(f"нет сервиса {name}")
    return services[name]


def answer_question(doc: dict[str, Any], q: dict[str, str]) -> str:
    svc = _service(doc, q["service"])
    if q["kind"] == "count":
        return str(len(svc[q["key"]]))
    return str(svc[q["key"]])


_Q_TEXT = {
    "value": "значение {key} у сервиса {service}",
    "count": "сколько ключей в {key} у сервиса {service}",
}


@register
class YamlAnchors(OpsFamily):
    id = "ops.yaml.anchors"
    title = "YAML: якоря, алиасы и merge-ключи"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "junior"
    position = 0.4
    time_limit_s = 120
    enemies = ("ops.compose.resolve",)
    answer_kind = "ints"
    language = "text"
    measures = (
        "Слияние через <<: явный ключ сервиса побеждает унаследованный; слияние поверхностное (вложенный "
        "словарь заменяется целиком); в списке слияний выигрывает первый словарь; цепочка алиасов."
    )
    misconceptions = {
        "explicit_loses": "явно записанный ключ сервиса сильнее унаследованного через <<: он переопределяет значение из "
        "якоря",
        "deep_merge": "слияние <<  поверхностное: если сервис задаёт свой словарь (environment), он заменяет словарь из "
        "якоря целиком, ключи не объединяются",
        "later_wins": "в списке <<: [*a, *b] при совпадении ключей побеждает первый словарь, а не последний",
    }
    answer_hint = "Введите три числа через пробел — ответы на вопросы по порядку."

    def make(self, rng: random.Random) -> Scenario:  # noqa: C901
        base, alt, mid = rng.choice(_ANCHORS)
        k1, k2 = rng.sample(_SCALAR_KEYS, 2)
        mapname = rng.choice(_MAPPINGS)
        svc = rng.sample(_SERVICES, 4)
        env = rng.sample(_VARS, 6)
        vals = rng.sample(_VALUES, 6)
        v1_base, v2_base = rng.randrange(128, 2048, 64), rng.randrange(256, 4096, 32)
        v1_alt = rng.randrange(64, 1024, 16)
        v1_s1 = rng.randrange(256, 3072, 64)
        v1_mid = rng.randrange(96, 1536, 32)
        if len({v1_base, v1_alt, v1_s1, v1_mid}) < 4:
            raise GenerationRetry
        base_vars = env[:3]
        over_vars = [env[1], env[3], *([env[4]] if rng.random() < 0.5 else [])]
        lines = [
            f"x-{base}: &{base}",
            "  restart: unless-stopped",
            f"  {k1}: {v1_base}",
            f"  {k2}: {v2_base}",
            f"  {mapname}:",
            *[f"    {v}: {vals[i]}" for i, v in enumerate(base_vars)],
            f"x-{alt}: &{alt}",
            f"  {k1}: {v1_alt}",
            "  init: true",
            f"x-{mid}: &{mid}",
            f"  <<: *{base}",
            f"  {k1}: {v1_mid}",
            "",
            "services:",
            f"  {svc[0]}:",
            f"    <<: *{base}",
            f"    image: shop/{svc[0]}",
            f"    {k1}: {v1_s1}",
            f"  {svc[1]}:",
            f"    <<: *{base}",
            f"    {mapname}:",
            *[f"      {v}: {vals[3 + i % 3]}" for i, v in enumerate(over_vars)],
            f"  {svc[2]}:",
            f"    <<: [*{alt}, *{base}]",
            f"    image: shop/{svc[2]}",
            f"  {svc[3]}:",
            f"    <<: *{mid}",
            f"    image: shop/{svc[3]}",
        ]
        kinds = rng.sample(["t1", "t2", "t3", "t4"], 3)
        questions: list[dict[str, str]] = []
        for kind in kinds:
            if kind == "t1":
                questions.append({"kind": "value", "service": svc[0], "key": k1})
            elif kind == "t2":
                questions.append({"kind": "count", "service": svc[1], "key": mapname})
            elif kind == "t3":
                questions.append({"kind": "value", "service": svc[2], "key": k1})
            else:
                questions.append({"kind": "value", "service": svc[3], "key": rng.choice([k1, k2])})
        return Scenario("\n".join(lines), {"questions": questions})

    def prompt(self, sc: Scenario) -> str:
        parts = [_Q_TEXT[q["kind"]].format(**q) for q in sc.data["questions"]]
        return (
            "Файл docker-compose с якорями разобран YAML-парсером (docker compose config). Ответьте по порядку: "
            + "; ".join(f"{i}) {p}" for i, p in enumerate(parts, 1))
            + "?"
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        if mutant is None:
            doc = yaml.safe_load(code)
        else:
            flags = {"explicit_loses": {"merge_wins": True}, "deep_merge": {"deep": True},
                     "later_wins": {"later_wins": True}}[mutant]
            doc = evaluate(yaml.compose(code), **flags)
        return " ".join(answer_question(doc, q) for q in sc.data["questions"])

    def why(self, sc: Scenario, raw_key: str) -> str:
        return (
            "<<: *якорь подмешивает ключи словаря, но явные ключи сервиса сильнее унаследованных. Слияние "
            "поверхностное: заданный в сервисе словарь (environment/labels) заменяет такой же словарь из якоря целиком. "
            "В списке <<: [*a, *b] при совпадении ключей выигрывает первый словарь. Через промежуточный якорь значения "
            "наследуются по цепочке, и свои ключи якоря сильнее его собственных слияний."
        )


# ============================================================== docker compose

_SECTION = re.compile(r"^# (shell|\.env|docker-compose\.yml|\S+\.env)\s*$")
_INTERP = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")
_HOSTS = ["db-prod", "db-stage", "pg-main", "cache-1x", "mq-core", "store-eu", "redis-fast"]
_WORDS = ["info", "debug", "warn", "quiet", "trace", "verbose"]


def parse_compose_case(code: str) -> dict[str, Any]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in code.splitlines():
        m = _SECTION.match(line)
        if m:
            current = m.group(1)
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    if "docker-compose.yml" not in sections:
        raise ScenarioError("нет docker-compose.yml")

    def kv(lines: list[str], prefix: str = "") -> dict[str, str]:
        out: dict[str, str] = {}
        for ln in lines:
            ln = ln.strip()
            if prefix:
                if not ln.startswith(prefix):
                    continue
                ln = ln[len(prefix) :].strip()
            if ln and "=" in ln and not ln.startswith("#"):
                key, val = ln.split("=", 1)
                out[key.strip()] = val.strip()
        return out

    return {
        "shell": kv(sections.get("shell", []), "$ export "),
        "dotenv": kv(sections.get(".env", [])),
        "files": {name: kv(lines) for name, lines in sections.items() if name.endswith(".env") and name != ".env"},
        "compose": "\n".join(sections["docker-compose.yml"]),
    }


def resolve_compose(
    case: dict[str, Any],
    names: dict[str, str],
    *,
    dotenv_first: bool = False,
    file_wins: bool = False,
    defaults_always: bool = False,
    first_file_wins: bool = False,
) -> dict[str, str]:
    """Значения для вопросов: port (хост-порт), port_container и переменные окружения контейнера web."""

    def sub(m: re.Match[str]) -> str:
        name, default = m.group(1), m.group(2)
        order = [case["dotenv"], case["shell"]] if dotenv_first else [case["shell"], case["dotenv"]]
        if defaults_always and default is not None:
            return default
        for src in order:
            if src.get(name):
                return src[name]
        return default if default is not None else ""

    doc = yaml.safe_load(_INTERP.sub(sub, case["compose"]))
    web = doc["services"][names["service"]]
    parts = str(web["ports"][0]).split(":")
    host = parts[-2]
    container = parts[-1]
    env: dict[str, str] = {}
    files = list(web.get("env_file", []))
    for f in reversed(files) if first_file_wins else files:
        if f not in case["files"]:
            raise ScenarioError(f"нет файла {f}")
        env.update(case["files"][f])
    inline = {str(k): str(v) for k, v in (web.get("environment") or {}).items()}
    if file_wins:
        env = {**inline, **env}
    else:
        env.update(inline)
    return {"port": host, "port_container": container, **env}


@register
class ComposeResolve(OpsFamily):
    id = "ops.compose.resolve"
    title = "docker compose: итоговые порт и переменные окружения"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "middle"
    position = 0.2
    time_limit_s = 150
    enemies = ("ops.yaml.anchors",)
    answer_kind = "latin"
    language = "text"
    measures = (
        "Приоритет значений в compose: переменные shell сильнее .env, .env сильнее значения по умолчанию "
        "(${VAR:-…}); environment сильнее env_file; следующий env_file сильнее предыдущего; ports «хост:контейнер»."
    )
    misconceptions = {
        "dotenv_beats_shell": "переменная, экспортированная в shell, сильнее файла .env при подстановке в compose-файл",
        "env_file_beats_environment": "ключи environment сильнее значений из env_file: они переопределяют файл",
        "defaults_always": "значение после :- берётся только если переменной нет ни в shell, ни в .env",
        "container_port": "в записи ports первым стоит порт хоста, вторым — порт контейнера",
        "first_file_wins": "из нескольких env_file позже перечисленный перекрывает ранее перечисленный",
    }
    answer_hint = "Введите через пробел три значения в порядке вопроса (порт числом, остальные — как в файле)."

    def make(self, rng: random.Random) -> Scenario:
        mode_port = rng.choice(["shell", "shell", "dotenv", "default"])
        mode_env = rng.choice(["shell", "dotenv", "dotenv", "default"])
        service = rng.choice(["web", "api", "app", "site"])
        a_name = rng.choice(["DB_HOST", "TIMEOUT"])
        b_name = rng.choice(["LOG_LEVEL", "CACHE_TTL"])

        def val(name: str) -> str:
            if name == "DB_HOST":
                return rng.choice(_HOSTS)
            if name == "LOG_LEVEL":
                return rng.choice(_WORDS)
            return str(rng.randint(20, 900))

        shell: list[str] = []
        dotenv: list[str] = []
        p_default, a_default = str(rng.randint(1100, 9900)), val(a_name)
        port_shell, port_env = str(rng.randint(1100, 9900)), str(rng.randint(1100, 9900))
        a_shell, a_env = val(a_name), val(a_name)
        if mode_port == "shell":
            shell.append(f"$ export WEB_PORT={port_shell}")
            dotenv.append(f"WEB_PORT={port_env}")
        elif mode_port == "dotenv":
            dotenv.append(f"WEB_PORT={port_env}")
        if mode_env == "shell":
            shell.append(f"$ export {a_name}={a_shell}")
            dotenv.append(f"{a_name}={a_env}")
        elif mode_env == "dotenv":
            dotenv.append(f"{a_name}={a_env}")
        if len({port_shell, port_env, p_default}) < 3 or len({a_shell, a_env, a_default}) < 3:
            raise GenerationRetry
        f1, f2 = "cfg/base.env", f"cfg/{service}.env"
        file_a = val(a_name)
        b1, b2 = val(b_name), val(b_name)
        if b1 == b2 or file_a in (a_shell, a_env, a_default):
            raise GenerationRetry
        port_fmt = rng.choice(['"${WEB_PORT:-%s}:80"', '"127.0.0.1:${WEB_PORT:-%s}:80"'])
        lines: list[str] = []
        if shell:
            lines += ["# shell", *shell]
        if dotenv:
            lines += ["# .env", *dotenv]
        lines += [f"# {f1}", f"{b_name}={b1}", f"{a_name}={val(a_name)}", f"# {f2}", f"{b_name}={b2}", f"{a_name}={file_a}",
                  "# docker-compose.yml", "services:", f"  {service}:", f"    image: shop/{service}", "    ports:",
                  f"      - {port_fmt % p_default}", "    env_file:", f"      - {f1}", f"      - {f2}", "    environment:",
                  f"      {a_name}: ${{{a_name}:-{a_default}}}"]
        order = ["port", a_name, b_name]
        rng.shuffle(order)
        return Scenario("\n".join(lines), {"service": service, "order": order})

    def prompt(self, sc: Scenario) -> str:
        names = {"port": "порт хоста, на который опубликован контейнер"}
        parts = [names.get(k, f"значение переменной {k} внутри контейнера") for k in sc.data["order"]]
        return (
            f"Выполняется docker compose up в каталоге, где лежат показанные файлы. Для сервиса {sc.data['service']} "
            "укажите по порядку: " + "; ".join(f"{i}) {p}" for i, p in enumerate(parts, 1)) + "?"
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        flags = {
            None: {}, "dotenv_beats_shell": {"dotenv_first": True}, "env_file_beats_environment": {"file_wins": True},
            "defaults_always": {"defaults_always": True}, "container_port": {}, "first_file_wins": {"first_file_wins": True},
        }[mutant]
        values = resolve_compose(parse_compose_case(code), {"service": sc.data["service"]}, **flags)
        out = []
        for k in sc.data["order"]:
            key = "port_container" if (k == "port" and mutant == "container_port") else k
            out.append(values[key])
        return " ".join(out)

    def why(self, sc: Scenario, raw_key: str) -> str:
        return (
            "Подстановка ${VAR:-по умолчанию} в compose-файле берёт значение из окружения shell, если его там нет — из "
            ".env, иначе значение по умолчанию. Внутри контейнера переменные из env_file (по порядку: следующий файл "
            "перекрывает предыдущий) переопределяются ключами environment. В ports первым идёт порт хоста; если перед "
            "ним указан IP, хост-порт — средний."
        )
