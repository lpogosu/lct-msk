"""Семейства пакета devops (module devops_core, префикс ops.).

Что проверяем:
- гейты банка, проверку ключа и каждого дистрактора через check(), детерминизм, состав модуля (грейды, якоря, враги);
- ключ воспроизводим по ПОКАЗАННОМУ тексту (решатель разбирает код экземпляра);
- двойник в буфере: доля ≥ 90%, один изменённый токен, ключ двойника пересчитывается решателем, не равен ключу и дистракторам;
- независимые оракулы: расписание cron перебором минут/дней, контрольный пример из документации nginx, битовая формула
  umask, таблица правил ENTRYPOINT/CMD, критический путь CI итерацией до неподвижной точки, YAML — PyYAML, настоящий bash.
"""

from __future__ import annotations

import random
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

from app.testing.bank.base import GRADES, REGISTRY, canonical
from app.testing.bank.families.devops import docker, k8s, nginx, perm, schedule, shell, yaml_compose
from app.testing.bank.gates import check_family

assert docker and k8s and nginx and perm and schedule and shell and yaml_compose  # модули импортированы ради регистрации
FAMS = {fid: f for fid, f in sorted(REGISTRY.items()) if fid.startswith("ops.")}
IDS = list(FAMS)
BACKEND_DIR = Path(__file__).resolve().parents[1]
CATALOG = BACKEND_DIR / "app" / "testing" / "bank" / "families" / "devops" / "CATALOG.md"
SEEDS = range(40)
GIT = shutil.which("git")


def _find_bash() -> str | None:
    """Настоящий bash: системный или из Git for Windows; WSL-обёртку System32\\bash.exe не берём."""
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    if GIT:
        root = Path(GIT).resolve().parent.parent
        for cand in (root / "usr" / "bin" / "bash.exe", root / "bin" / "bash.exe"):
            if cand.exists():
                return str(cand)
    return None


BASH = _find_bash()


# ---------------------------------------------------------------- состав модуля


def test_module_composition():
    assert len(FAMS) == 11
    grades = [f.target_grade for f in FAMS.values()]
    assert tuple(grades.count(g) for g in GRADES) == (1, 4, 3, 3)  # ≈ 15/30/30/25 на 11 семействах
    assert {f.target_grade for f in FAMS.values() if f.is_anchor} == set(GRADES)  # по якорю на каждый порог
    for fid, f in FAMS.items():
        assert f.module == "devops_core" and f.specs == ("devops",), fid
        assert f.format == "predict_output" and f.c == 0, fid
        assert -1 <= f.position <= 1 and 60 <= f.time_limit_s <= 180, fid
        assert f.measures and len(f.misconceptions) >= 3, fid
        assert f.generate(0).rendered.answer_mode == "text", fid
        for e in f.enemies:
            assert e in FAMS and fid in FAMS[e].enemies, (fid, e)
    assert sum(f.format == "mcq_concept" for f in FAMS.values()) / len(FAMS) <= 0.30


def test_catalog_lists_every_family():
    text = CATALOG.read_text(encoding="utf-8")
    for fid in IDS:
        assert f"`{fid}`" in text, fid


@pytest.mark.parametrize("fid", IDS)
def test_family_passes_gates(fid):
    errors = check_family(FAMS[fid])
    assert not errors, "; ".join(errors)


# ---------------------------------------------------------------- ключ, дистракторы, детерминизм


@pytest.mark.parametrize("fid", IDS)
def test_key_and_distractors_pass_check(fid):
    fam = FAMS[fid]
    for seed in SEEDS:
        inst = fam.generate(seed)
        assert fam.check(inst, inst.key) == (True, None), (fid, seed)
        assert fam.check(inst, "wrong answer")[0] is False
        assert len(inst.distractors) >= 2, (fid, seed)
        for wrong, tag in inst.distractors.items():
            assert tag in fam.misconceptions, (fid, tag)
            assert fam.check(inst, wrong) == (False, tag), (fid, seed, wrong)
        assert "Верный ответ" in inst.explanation and any("а" <= c <= "я" for c in inst.explanation.lower())


@pytest.mark.parametrize("fid", IDS)
def test_generation_is_deterministic_and_does_not_use_global_random(fid):
    fam = FAMS[fid]
    random.seed(1)
    first = [fam.generate(s) for s in (0, 5, 17)]
    random.seed(2)
    second = [fam.generate(s) for s in (0, 5, 17)]
    for a, b in zip(first, second, strict=True):
        assert a.key == b.key and a.rendered == b.rendered and a.distractors == b.distractors


@pytest.mark.parametrize("fid", IDS)
def test_instances_are_distinct(fid):
    fam = FAMS[fid]
    insts = [fam.generate(s) for s in range(100)]
    assert len({i.rendered.code for i in insts}) >= 98
    assert len({canonical(i.key) for i in insts}) >= 95


@pytest.mark.parametrize("fid", IDS)
def test_key_is_reproducible_from_shown_text(fid):
    fam = FAMS[fid]
    for seed in SEEDS:
        inst = fam.generate(seed)
        sc = fam.scenario_for(seed)
        assert sc.code == inst.rendered.code
        assert canonical(fam.normalize(fam.solve(sc, inst.rendered.code))) == canonical(inst.key)
        assert fam.check(inst, fam.solve(sc, inst.rendered.code)) == (True, None)


# ---------------------------------------------------------------- двойник в буфере


@pytest.mark.parametrize("fid", IDS)
def test_twin_coverage_and_properties(fid):
    fam = FAMS[fid]
    made = 0
    for seed in SEEDS:
        inst = fam.generate(seed)
        twin = fam.twin(inst)
        if twin is None:
            continue
        made += 1
        c = twin.changed_literal
        shown, clip = twin.shown_text, twin.clipboard_text
        off = c.offset
        assert shown[off : off + len(c.from_)] == c.from_ and clip[off : off + len(c.to)] == c.to
        assert shown[:off] == clip[:off] and shown[off + len(c.from_) :] == clip[off + len(c.to) :]  # один токен
        assert abs(len(c.to) - len(c.from_)) <= 1 and c.from_ != c.to
        assert twin.twin_key != canonical(inst.key) and twin.twin_key not in inst.distractors
        assert twin.key == canonical(inst.key)
        assert set(twin.client_clip()) == {"render_id", "variant_code", "text", "map", "version"}  # ключей на клиенте нет
        # ключ двойника пересчитывается решателем по тексту из буфера
        sc = fam.scenario_for(seed)
        assert canonical(fam.normalize(fam.solve(sc, twin.twin_rendered.code))) == twin.twin_key
        # детерминизм
        again = fam.twin(inst)
        assert again is not None and again.twin_key == twin.twin_key and again.clipboard_text == twin.clipboard_text
        assert all(twin.gates.values())
    assert made / len(SEEDS) >= 0.9, (fid, made)


# ---------------------------------------------------------------- umask


def _expected_mode(base: int, umask: int) -> int:
    return base & ~umask & 0o777


def test_umask_control_examples():
    fam = FAMS["ops.perm.umask"]
    sc = perm.Scenario("", {"asked": ["f", "d", "x", "y"]})
    code = "\n".join(
        ["$ umask 027", "$ touch f", "$ mkdir d", "$ touch x && chmod +x x", "$ touch y && chmod a+x y"]
    )
    assert fam.solve(sc, code) == "640 750 750 751"
    assert (_expected_mode(0o666, 0o027), _expected_mode(0o777, 0o027)) == (0o640, 0o750)
    # chmod +x без «кого» подчиняется umask 077, явное a+x — нет
    code2 = "\n".join(["$ umask 077", "$ touch x", "$ chmod +x x", "$ touch y", "$ chmod a+x y"])
    assert fam.solve(perm.Scenario("", {"asked": ["x", "y"]}), code2) == "700 711"
    # umask меняется между командами
    code3 = "\n".join(["$ umask 022", "$ touch a", "$ umask 077", "$ touch b"])
    assert fam.solve(perm.Scenario("", {"asked": ["a", "b"]}), code3) == "644 600"
    # заблуждение «вычитание цифр»: umask 013 → 666 − 013 цифра за цифрой = 653, верно 664
    code4 = "$ umask 013\n$ touch a"
    s4 = perm.Scenario("", {"asked": ["a"]})
    assert fam.solve(s4, code4) == "664" and fam.solve(s4, code4, "subtract_digits") == "653"
    assert fam.normalize("20 0") == "020 000" and fam.normalize("0640") == "640"


# ---------------------------------------------------------------- ENTRYPOINT / CMD


@pytest.mark.parametrize(
    ("dockerfile", "run", "argv"),
    [
        ('ENTRYPOINT ["python", "-m", "app"]\nCMD ["--port", "8080"]', "img", "python -m app --port 8080"),
        ('ENTRYPOINT ["python", "-m", "app"]\nCMD ["--port", "8080"]', "img --port 9090", "python -m app --port 9090"),
        ('CMD ["python", "app.py"]', "img python -m pytest", "python -m pytest"),
        ('CMD ["python", "app.py"]', "img", "python app.py"),
        ('ENTRYPOINT python app.py\nCMD ["--x"]', "img --y", "/bin/sh -c python app.py"),
        ('ENTRYPOINT ["node", "s.js"]\nCMD --x 1', "img", "node s.js /bin/sh -c --x 1"),
        ('ENTRYPOINT ["node", "s.js"]\nCMD ["--x", "1"]', "--entrypoint /bin/sh img", "/bin/sh"),
        ('ENTRYPOINT ["node", "s.js"]\nCMD ["--x", "1"]', "--entrypoint /bin/sh img -c ls", "/bin/sh -c ls"),
        ('CMD python app.py', "img", "/bin/sh -c python app.py"),
        ('ENTRYPOINT ["a"]\nENTRYPOINT ["b"]\nCMD ["c"]\nCMD ["d"]', "img", "b d"),
    ],
)
def test_entrypoint_truth_table(dockerfile, run, argv):
    fam = FAMS["ops.docker.entrypoint"]
    code = f"FROM python:3.12\n{dockerfile}\n\n$ docker run --rm {run}"
    assert fam.solve(docker.Scenario(code), code) == argv


def test_entrypoint_mutants_differ_on_truth_table():
    fam = FAMS["ops.docker.entrypoint"]
    code = 'ENTRYPOINT ["python", "-m", "app"]\nCMD ["--port", "8080"]\n\n$ docker run img --port 9090'
    sc = docker.Scenario(code)
    assert fam.solve(sc, code, "args_replace_all") == "--port 9090"
    assert fam.solve(sc, code, "args_appended_to_cmd") == "python -m app --port 8080 --port 9090"
    code_b = 'ENTRYPOINT ["python", "-m", "app"]\nCMD ["--port", "8080"]\n\n$ docker run --entrypoint sh img'
    assert fam.solve(sc, code_b, "entrypoint_flag_keeps_cmd") == "sh --port 8080"
    assert fam.normalize('"python", "-m", "app"') == "python -m app"
    code_c = 'ENTRYPOINT ["python", "-m", "app"]\nCMD ["--port", "8080"]\n\n$ docker run img'
    assert fam.solve(sc, code_c, "reversed_order") == "--port 8080 python -m app"
    code_d = 'ENTRYPOINT python app.py\nCMD ["--x"]\n\n$ docker run img'
    assert fam.solve(sc, code_d, "no_sh_wrap") == "python app.py"
    code_e = 'ENTRYPOINT ["python", "-m", "app"]\nCMD ["--port", "8080"]\n\n$ docker run --entrypoint sh img -c ls'
    assert fam.solve(sc, code_e, "flag_ignored") == "python -m app -c ls"
    assert fam.solve(sc, code, "run_args_ignored") == "python -m app --port 8080"


# ---------------------------------------------------------------- кеш слоёв


def test_cache_control_examples():
    fam = FAMS["ops.docker.cache"]
    steps = (
        "1: FROM python:3.12   # 3 с\n2: WORKDIR /app   # 1 с\n3: COPY requirements.txt .   # 2 с\n"
        "4: RUN pip install -r requirements.txt   # 50 с\n5: COPY . .   # 4 с\n6: RUN python -m build   # 10 с\n"
        "7: CMD [\"python\", \"main.py\"]   # 1 с\n# .dockerignore\ntests/\n*.md"
    )

    def solve(header, mutant=None):
        code = f"{header}\n# Dockerfile\n{steps}"
        return fam.solve(docker.Scenario(code), code, mutant)

    assert solve("# Изменение: изменён файл requirements.txt") == "3 4 5 6 7 67 4"  # с первого COPY и ниже
    assert solve("# Изменение: изменён файл src/app.py") == "5 6 7 15 56"  # только COPY . .
    assert solve("# Изменение: изменён файл tests/test_a.py") == "0 71"  # .dockerignore: контекст не менялся
    assert solve("# Изменение: изменён файл README.md") == "0 71"  # *.md в корне
    assert solve("# Изменение: изменён файл docs/guide.md") == "5 6 7 15 56"  # *.md не охватывает подкаталоги
    assert solve("# Изменение: правка инструкции в строке 4") == "4 5 6 7 65 6"
    assert solve("# Изменение: изменён файл tests/test_a.py", "dockerignore_ignored") == "5 6 7 15 56"
    assert solve("# Изменение: правка инструкции в строке 4", "after_only") == "5 6 7 15 56"


# ---------------------------------------------------------------- YAML


def test_yaml_merge_semantics_match_pyyaml():
    fam = FAMS["ops.yaml.anchors"]
    for seed in SEEDS:
        code = fam.generate(seed).rendered.code
        assert yaml_compose.evaluate(yaml.compose(code)) == yaml.safe_load(code), seed
    text = (
        "a: &a {x: 1, y: 2}\nb: &b {x: 10, z: 30}\n"
        "s1: {<<: [*a, *b]}\ns2: {<<: [*b, *a], y: 5}\ns3: {<<: *a, x: 7}\n"
    )
    doc = yaml.safe_load(text)
    assert doc["s1"] == {"x": 1, "y": 2, "z": 30}  # первый словарь списка побеждает
    assert doc["s2"] == {"x": 10, "y": 5, "z": 30}
    assert doc["s3"] == {"x": 7, "y": 2}
    later = yaml_compose.evaluate(yaml.compose(text), later_wins=True)
    assert later["s1"] == {"x": 10, "y": 2, "z": 30}
    assert yaml_compose.evaluate(yaml.compose(text), merge_wins=True)["s3"]["x"] == 1
    deep = yaml_compose.evaluate(yaml.compose("a: &a {m: {p: 1, q: 2}}\ns: {<<: *a, m: {p: 9}}\n"), deep=True)
    assert deep["s"]["m"] == {"p": 9, "q": 2}
    shallow = yaml.safe_load("a: &a {m: {p: 1, q: 2}}\ns: {<<: *a, m: {p: 9}}\n")
    assert shallow["s"]["m"] == {"p": 9}


# ---------------------------------------------------------------- compose


def _compose_text(shell_lines, dotenv, files, compose):
    parts = []
    if shell_lines:
        parts += ["# shell", *shell_lines]
    if dotenv:
        parts += ["# .env", *dotenv]
    for name, lines in files.items():
        parts += [f"# {name}", *lines]
    return "\n".join([*parts, "# docker-compose.yml", compose])


_COMPOSE = (
    "services:\n  web:\n    image: shop/web\n    ports:\n      - \"${WEB_PORT:-3000}:80\"\n    env_file:\n"
    "      - cfg/base.env\n      - cfg/web.env\n    environment:\n      TIMEOUT: ${TIMEOUT:-10}"
)


def test_compose_precedence_control_examples():
    fam = FAMS["ops.compose.resolve"]
    files = {"cfg/base.env": ["TIMEOUT=1", "LOG=a", "ONLY=base"], "cfg/web.env": ["TIMEOUT=2", "LOG=b"]}
    sc = yaml_compose.Scenario("", {"service": "web", "order": ["port", "TIMEOUT", "LOG"]})

    def solve(shell_lines, dotenv, mutant=None):
        return fam.solve(sc, _compose_text(shell_lines, dotenv, files, _COMPOSE), mutant)

    assert solve(["$ export WEB_PORT=9090"], ["WEB_PORT=8080", "TIMEOUT=45"]) == "9090 45 b"  # shell > .env
    assert solve([], ["WEB_PORT=8080"]) == "8080 10 b"  # .env, затем значение по умолчанию; environment > env_file
    assert solve([], []) == "3000 10 b"
    assert solve(["$ export WEB_PORT=9090"], ["WEB_PORT=8080"], "dotenv_beats_shell") == "8080 10 b"
    assert solve([], [], "env_file_beats_environment") == "3000 2 b"
    assert solve(["$ export WEB_PORT=9090"], [], "defaults_always") == "3000 10 b"
    assert solve([], [], "container_port") == "80 10 b"
    assert solve([], [], "first_file_wins") == "3000 10 a"
    ip_compose = _COMPOSE.replace('"${WEB_PORT:-3000}:80"', '"127.0.0.1:${WEB_PORT:-3000}:80"')
    assert fam.solve(sc, _compose_text([], ["WEB_PORT=7000"], files, ip_compose)) == "7000 10 b"


# ---------------------------------------------------------------- cron


def _fits(text: str, value: int, lo: int, hi: int) -> bool:
    """Независимая проверка членства значения в поле cron (без построения множеств)."""
    for item in text.split(","):
        step = 1
        if "/" in item:
            item, s = item.split("/")
            step = int(s)
        if item == "*":
            a, b = lo, hi
        elif "-" in item:
            a, b = (int(x) for x in item.split("-"))
        else:
            a = b = int(item)
        if a <= value <= b and (value - a) % step == 0:
            return True
    return False


def _oracle_runs(fields, now: datetime, n: int, horizon_days: int = 800) -> list[datetime]:
    """Перебор: день за днём, внутри подходящего дня — минута за минутой (без наборов и сортировок)."""
    out: list[datetime] = []
    day0 = datetime(now.year, now.month, now.day)
    for d in range(horizon_days):
        day = day0 + timedelta(days=d)
        if not _fits(fields[3], day.month, 1, 12):
            continue
        dom_ok = _fits(fields[2], day.day, 1, 31)
        cron_dow = (day.weekday() + 1) % 7
        dow_ok = _fits(fields[4], cron_dow, 0, 7) or (cron_dow == 0 and _fits(fields[4], 7, 0, 7))
        both_restricted = not fields[2].startswith("*") and not fields[4].startswith("*")
        if not ((dom_ok or dow_ok) if both_restricted else (dom_ok and dow_ok)):
            continue
        for minute_of_day in range(1440):
            t = day + timedelta(minutes=minute_of_day)
            if t > now and _fits(fields[1], t.hour, 0, 23) and _fits(fields[0], t.minute, 0, 59):
                out.append(t)
                if len(out) == n:
                    return out
    return out


def _cron_code(line: str, now: datetime) -> str:
    ru = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][now.weekday()]
    return f"# crontab -l\n{line}  /bin/job\n# сейчас: {now:%Y-%m-%d} ({ru}) {now:%H:%M}"


@pytest.mark.parametrize(
    ("line", "now", "expected"),
    [
        ("*/20 9-17 * * 1-5", datetime(2026, 3, 5, 16, 41), ["2026-03-05 17:00", "2026-03-05 17:20", "2026-03-05 17:40"]),
        ("*/7 * * * *", datetime(2026, 3, 5, 10, 55), ["2026-03-05 10:56", "2026-03-05 11:00", "2026-03-05 11:07"]),
        ("0 3 1 * 1", datetime(2026, 3, 2, 4, 0), ["2026-03-09 03:00", "2026-03-16 03:00", "2026-03-23 03:00"]),
        ("0 3 1 * 1", datetime(2026, 3, 23, 4, 0), ["2026-03-30 03:00", "2026-04-01 03:00", "2026-04-06 03:00"]),
        ("30 8 * * 0", datetime(2026, 3, 5, 9, 0), ["2026-03-08 08:30", "2026-03-15 08:30", "2026-03-22 08:30"]),
        ("30 8 * * 7", datetime(2026, 3, 5, 9, 0), ["2026-03-08 08:30", "2026-03-15 08:30", "2026-03-22 08:30"]),
        ("15 10 * * *", datetime(2026, 3, 5, 10, 15), ["2026-03-06 10:15", "2026-03-07 10:15", "2026-03-08 10:15"]),
        ("0 0 31 * *", datetime(2026, 1, 31, 0, 0), ["2026-03-31 00:00", "2026-05-31 00:00", "2026-07-31 00:00"]),
    ],
)
def test_cron_control_examples(line, now, expected):
    fam = FAMS["ops.cron.next_runs"]
    code = _cron_code(line, now)
    assert fam.solve(schedule.Scenario(code), code) == " ; ".join(expected)
    fields = line.split()
    assert [t.strftime("%Y-%m-%d %H:%M") for t in _oracle_runs(fields, now, 3)] == expected


def test_cron_leap_day_and_mutants():
    fam = FAMS["ops.cron.next_runs"]
    now = datetime(2026, 3, 1, 0, 0)
    case = schedule.parse_cron_case(_cron_code("0 0 29 2 *", now))
    assert schedule.next_runs(case, 1) == [datetime(2028, 2, 29, 0, 0)]
    code = _cron_code("0 3 1 * 1", datetime(2026, 3, 2, 4, 0))
    sc = schedule.Scenario(code)
    firsts = [datetime(2026 + (m - 1) // 12, (m - 1) % 12 + 1, 1, 3, 0) for m in range(3, 80)]
    monday_firsts = [t for t in firsts if t.weekday() == 0 and t > datetime(2026, 3, 2, 4, 0)][:3]  # 1-е число, которое при этом понедельник
    assert fam.solve(sc, code, "dom_dow_and") == " ; ".join(t.strftime("%Y-%m-%d %H:%M") for t in monday_firsts)
    # «сейчас» — момент запуска: строго позже
    code2 = _cron_code("*/15 * * * *", datetime(2026, 3, 5, 10, 15))
    assert fam.solve(schedule.Scenario(code2), code2).startswith("2026-03-05 10:30")
    assert fam.solve(schedule.Scenario(code2), code2, "include_now").startswith("2026-03-05 10:15")
    assert fam.normalize("02.03.2026 9:30 ; 2026-03-02 09:45") == "2026-03-02 09:30 ; 2026-03-02 09:45"


@pytest.mark.parametrize("seed", range(0, 60, 5))
def test_cron_generated_matches_bruteforce(seed):
    fam = FAMS["ops.cron.next_runs"]
    inst = fam.generate(seed)
    case = schedule.parse_cron_case(inst.rendered.code)
    want = [t.strftime("%Y-%m-%d %H:%M") for t in _oracle_runs(case["fields"], case["now"], 3)]
    assert inst.key == " ; ".join(want)


# ---------------------------------------------------------------- CI


def _fixpoint_schedule(doc):
    """Независимый расчёт: итерации до неподвижной точки (без топологической сортировки)."""
    stages = doc["stages"]
    jobs = {k: v for k, v in doc.items() if k != "stages"}
    finish = dict.fromkeys(jobs, 0)
    start = dict.fromkeys(jobs, 0)
    for _ in range(len(jobs) + 2):
        for name, job in jobs.items():
            if "needs" in job:
                deps = job["needs"]
            else:
                deps = [j for j, jv in jobs.items() if stages.index(jv["stage"]) < stages.index(job["stage"])]
            start[name] = max((finish[d] for d in deps), default=0)
            finish[name] = start[name] + job["time"]
    return start, finish


def test_ci_control_examples_and_independent_longest_path():
    fam = FAMS["ops.ci.stages"]
    code = (
        "stages: [build, test]\na:\n  stage: build\n  time: 3\nb:\n  stage: build\n  time: 5\n"
        "c:\n  stage: test\n  time: 2\n  needs: [a]\nd:\n  stage: test\n  time: 4\ne:\n  stage: test\n  time: 6\n  needs: []\n"
    )
    sc = schedule.Scenario(code, {"asked": ["c", "d"]})
    assert fam.solve(sc, code) == "3 5 9"  # c ждёт только a; d — всю стадию; пайплайн: d кончается на 9-й минуте
    assert fam.solve(sc, code, "needs_ignored") == "5 5 11"
    assert fam.solve(sc, code, "needs_plus_stage") == "5 5 11"
    assert fam.solve(sc, code, "empty_needs_as_none") == "3 5 11"
    for seed in SEEDS:
        inst = fam.generate(seed)
        doc = yaml.safe_load(inst.rendered.code)
        start, finish = _fixpoint_schedule(doc)
        a, b = fam.scenario_for(seed).data["asked"]
        assert inst.key == f"{start[a]} {start[b]} {max(finish.values())}", seed


# ---------------------------------------------------------------- Kubernetes


def _deployment(replicas, surge, unavailable, capacity):
    return (
        f"# В кластере помещается не более {capacity} подов\napiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: x\n"
        f"spec:\n  replicas: {replicas}\n  strategy:\n    type: RollingUpdate\n    rollingUpdate:\n"
        f"      maxSurge: {surge}\n      maxUnavailable: {unavailable}\n"
    )


@pytest.mark.parametrize(
    ("replicas", "surge", "unavail", "capacity", "expected"),
    [
        (10, "25%", "25%", 14, (3, 2, 13, 8)),  # 2,5 → вверх 3 / вниз 2
        (10, "25%", "25%", 12, (3, 2, 12, 8)),  # ёмкость кластера ограничивает максимум
        (3, "25%", "25%", 5, (1, 0, 4, 3)),  # 0,75 → вверх 1 / вниз 0
        (4, 0, "10%", 6, (0, 1, 4, 3)),  # оба нуля → maxUnavailable = 1
        (20, 4, 0, 30, (4, 0, 24, 20)),
        (7, "50%", 2, 20, (4, 2, 11, 5)),
    ],
)
def test_rolling_update_control_examples(replicas, surge, unavail, capacity, expected):
    fam = FAMS["ops.k8s.rolling"]
    code = _deployment(replicas, surge, unavail, capacity)
    assert fam.solve(k8s.Scenario(code), code) == " ".join(map(str, expected))


def test_rolling_update_mutants():
    fam = FAMS["ops.k8s.rolling"]
    code = _deployment(10, "25%", "25%", 12)
    sc = k8s.Scenario(code)
    assert fam.solve(sc, code, "round_wrong") == "2 3 12 7"
    assert fam.solve(sc, code, "no_cluster_limit") == "3 2 13 8"
    assert fam.solve(sc, code, "ready_minus_surge") == "3 2 12 7"
    code0 = _deployment(4, 0, "10%", 6)
    assert fam.solve(k8s.Scenario(code0), code0, "skip_zero_rule") == "0 0 4 4"


def _bytes(value: str) -> int:
    units = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "K": 10**3, "M": 10**6, "G": 10**9}
    for suffix in sorted(units, key=len, reverse=True):
        if value.endswith(suffix):
            return int(float(value[: -len(suffix)]) * units[suffix])
    return int(value)


def test_resource_placement_control_examples():
    fam = FAMS["ops.res.limits"]
    code = (
        "nodes:\n  - {count: 2, cpu: 4, memory: 16Gi}\nreserved_per_node: {cpu: 500m, memory: 1Gi}\n"
        "daemonset_per_node: {cpu: 100m, memory: 256Mi}\npod_requests: {cpu: 500m, memory: 2Gi}\n"
    )
    sc = k8s.Scenario(code)
    # узел: 3400m CPU, 14,75 Gi памяти → min(6, 7) = 6 подов; два узла → 12; остаток CPU 2 × 400m
    assert fam.solve(sc, code) == "12 800"
    # десятичные единицы: 3G ≠ 3Gi
    code_g = code.replace("memory: 2Gi}\n", "memory: 3G}\n")
    mem_free = _bytes("16Gi") - _bytes("1Gi") - _bytes("256Mi")
    per_node = min(3400 // 500, mem_free // _bytes("3G"))
    assert fam.solve(sc, code_g) == f"{2 * per_node} {2 * (3400 - per_node * 500)}"
    per_node_bin = min(3400 // 500, mem_free // _bytes("3Gi"))
    assert per_node == 5 and per_node_bin == 4  # 3G = 3·10⁹ байт, 3Gi = 3,22·10⁹: поды разной «толщины»
    # группы узлов считаются отдельно, остатки не складываются
    code_two = code.replace("  - {count: 2, cpu: 4, memory: 16Gi}", "  - {count: 1, cpu: 2, memory: 8Gi}\n  - {count: 1, cpu: 2, memory: 8Gi}")
    pods, _ = k8s.placement(code_two)
    pods_pooled, _ = k8s.placement(code_two, pooled=True)
    assert pods == 2 * min((2000 - 600) // 500, (_bytes("8Gi") - _bytes("1Gi") - _bytes("256Mi")) // _bytes("2Gi"))
    assert pods_pooled >= pods
    assert k8s.cpu_millis(0.5) == 500 and k8s.cpu_millis("250m") == 250 and k8s.cpu_millis(2) == 2000
    assert k8s.mem_bytes("1G") == 10**9 and k8s.mem_bytes("1Gi") == 2**30 and k8s.mem_bytes("1G", binary_only=True) == 2**30


# ---------------------------------------------------------------- nginx

_DOC_CONFIG = (
    "server {\n    location = / { return 200; }  # 1\n    location / { proxy_pass http://a; }  # 2\n"
    "    location /documents/ { proxy_pass http://b; }  # 3\n    location ^~ /images/ { root /d; }  # 4\n"
    "    location ~* \\.(gif|jpg|jpeg)$ { expires 7d; }  # 5\n}\n"
)


def test_nginx_documentation_example():
    """Пример из документации nginx (ngx_http_core_module, location): пять запросов и их блоки."""
    blocks = nginx.parse_blocks(_DOC_CONFIG)
    assert blocks == [("=", "/"), ("", "/"), ("", "/documents/"), ("^~", "/images/"), ("~*", r"\.(gif|jpg|jpeg)$")]
    expected = {"/": 1, "/index.html": 2, "/documents/document.html": 3, "/images/1.gif": 4, "/documents/1.jpg": 5}
    for uri, block in expected.items():
        assert nginx.match_location(uri, blocks) == block, uri
    fam = FAMS["ops.nginx.location"]
    code = _DOC_CONFIG + "\n" + "\n".join(f"GET {u}?x=1" for u in expected)
    assert fam.solve(nginx.Scenario(code), code) == "1 2 3 4 5"
    # регистр: ~ различает, ~* — нет
    blocks2 = [("", "/"), ("~", r"\.png$"), ("~*", r"\.jpg$")]
    assert nginx.match_location("/a/B.PNG", blocks2) == 1 and nginx.match_location("/a/B.JPG", blocks2) == 3
    assert nginx.match_location("/a/B.PNG", blocks2, tilde_insensitive=True) == 2
    # заблуждения дают другой ответ на тех же запросах
    assert nginx.match_location("/documents/1.jpg", blocks, prefix_beats_regex=True) == 3
    assert nginx.match_location("/images/1.gif", blocks, regex_beats_caret=True) == 5
    blocks3 = [("", "/"), ("", "/a/"), ("", "/a/b/")]
    assert nginx.match_location("/a/b/c", blocks3) == 3 and nginx.match_location("/a/b/c", blocks3, first_prefix=True) == 1
    blocks4 = [("~", r"\.php$"), ("~", r"^/admin/.*\.php$")]
    assert nginx.match_location("/admin/x.php", blocks4) == 1
    assert nginx.match_location("/admin/x.php", blocks4, longest_regex=True) == 2


# ---------------------------------------------------------------- bash


def test_shell_control_examples():
    run = shell.run_script
    assert run('f() { return 3; }\nf || echo no\necho "rc=$?"') == ["no", "rc=0"]
    assert run("true || echo X && echo Y") == ["Y"]  # && и || равноприоритетны: (true || X) && Y
    assert run("true || echo X && echo Y", c_precedence=True) == []
    assert run('false | true\necho "rc=$?"') == ["rc=0"]
    assert run('set -o pipefail\nfalse | true\necho "rc=$?"') == ["rc=1"]
    assert run('set -o pipefail\nfalse | true\necho "rc=$?"', pipefail_ignored=True) == ["rc=0"]
    assert run('! false && echo A\necho "rc=$?"') == ["A", "rc=0"]
    assert run("set -e\nfalse && echo A\necho B") == ["B"]  # упавшая команда не последняя в списке — set -e молчит
    assert run("set -e\ntrue && false\necho B") == []  # false после последнего && — скрипт прерван
    assert run("set -e\nif false; then echo A; else echo B; fi\necho C") == ["B", "C"]
    assert run("set -e\n! false\necho B") == ["B"]
    assert run("set -e\n(exit 3)\necho B") == []


@pytest.mark.skipif(BASH is None, reason="bash не установлен")
@pytest.mark.parametrize("seed", range(0, 80, 2))
def test_shell_model_matches_real_bash(seed):
    fam = FAMS["ops.sh.exitcodes"]
    inst = fam.generate(seed)
    code = inst.rendered.code
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "script.sh"
        script.write_text(code + "\n", encoding="utf-8", newline="\n")
        proc = subprocess.run([BASH, "script.sh"], cwd=tmp, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert fam.normalize(proc.stdout) == inst.key, code
    # дистракторы ответом bash не являются
    assert fam.normalize(proc.stdout) not in inst.distractors


# ---------------------------------------------------------------- детерминизм между процессами


def test_generation_does_not_depend_on_hash_seed():
    """Ключи и двойники одинаковы при разном PYTHONHASHSEED (порядок множеств не должен влиять на генерацию)."""
    import hashlib
    import os
    import sys

    def digest(families):
        h = hashlib.sha256()
        for fid in sorted(families):
            for seed in (0, 3, 11):
                inst = FAMS[fid].generate(seed)
                tw = FAMS[fid].twin(inst)
                h.update(canonical([inst.rendered.code, inst.key, sorted(inst.distractors.items())]).encode())
                h.update((tw.twin_key + tw.clipboard_text if tw else "-").encode())
        return h.hexdigest()

    here = digest(IDS)
    code = (
        "import hashlib\n"
        "from app.testing.bank.base import REGISTRY, canonical\n"
        "from app.testing.bank.families.devops import docker, k8s, nginx, perm, schedule, shell, yaml_compose\n"
        "F = {k: v for k, v in sorted(REGISTRY.items()) if k.startswith('ops.')}\n"
        "h = hashlib.sha256()\n"
        "for fid in sorted(F):\n"
        "    for seed in (0, 3, 11):\n"
        "        inst = F[fid].generate(seed); tw = F[fid].twin(inst)\n"
        "        h.update(canonical([inst.rendered.code, inst.key, sorted(inst.distractors.items())]).encode())\n"
        "        h.update((tw.twin_key + tw.clipboard_text if tw else '-').encode())\n"
        "print(h.hexdigest())\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": "4242", "PYTHONPATH": str(BACKEND_DIR), "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=BACKEND_DIR, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    assert out.stdout.strip() == here
