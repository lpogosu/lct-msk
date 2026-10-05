"""git_linux: права файлов, конвейер grep/cut/sort/uniq, перенаправления потоков bash, сценарии git
(упорядочивание команд) и диапазоны коммитов A..B / A...B.

Ключи считают точные решатели: битовая модель chmod, модель конвейера с семантикой GNU coreutils
(LC_ALL=C), таблица файловых дескрипторов bash, граф коммитов с достижимостью. Тест пакета сверяет
модели конвейера и перенаправлений с настоящим bash, если он установлен.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence

from app.testing.bank.base import Instance, register
from app.testing.bank.families.backend_python._common import SPECS, BankFamily, GenerationRetry

# ============================================================== права файлов

_BITS = {"r": 4, "w": 2, "x": 1}
_WHO = "ugo"


def perms_from_octal(octal: str) -> list[set[str]]:
    return [{b for b, v in _BITS.items() if int(d) & v} for d in octal]


def to_octal(perms: Sequence[set[str]], weights: dict[str, int] | None = None) -> str:
    w = weights or _BITS
    return "".join(str(sum(w[b] for b in p)) for p in perms)


def to_symbolic(perms: Sequence[set[str]]) -> str:
    return "-" + "".join("".join(b if b in p else "-" for b in "rwx") for p in perms)


def apply_chmod(perms: Sequence[set[str]], clauses: Sequence[tuple[str, str, str]], *,
                who_map: dict[str, str] | None = None, plus_is_assign: bool = False) -> list[set[str]]:
    """Точная семантика chmod u+x,g-w,o=r (параметры — для моделей заблуждений)."""
    out = [set(p) for p in perms]
    for who, op, bits in clauses:
        idx = _WHO.index((who_map or {}).get(who, who))
        if op == "+" and not plus_is_assign:
            out[idx] |= set(bits)
        elif op == "-":
            out[idx] -= set(bits)
        else:
            out[idx] = set(bits)
    return out


@register
class ChmodOctal(BankFamily):
    id = "sh.chmod.octal"
    title = "chmod: символьная запись → восьмеричная"
    specs = SPECS
    module = "git_linux"
    format = "predict_output"
    target_grade = "intern"
    position = 0.3
    c = 0.25
    time_limit_s = 90
    is_anchor = True
    answer_kind = "choice"
    measures = "Права rwx для владельца/группы/остальных, символьный chmod (+, -, =), перевод в восьмеричную запись."
    misconceptions = {
        "reversed_weights": "веса битов: r = 4, w = 2, x = 1",
        "o_is_owner": "o в chmod — это «остальные» (others), владелец — u (user)",
        "plus_is_assign": "+ добавляет права к имеющимся, а = заменяет их целиком",
        "digits_reversed": "восьмеричные цифры идут в порядке владелец, группа, остальные",
    }

    _START = ["644", "640", "600", "755", "750", "700", "664", "660", "444", "604", "754", "711"]
    _FILES = ["deploy.sh", "backup.sh", "config.yml", "report.csv", "id_ed25519", "app.log", "run_tests.sh",
              "secrets.env", "cleanup.py", "nginx.conf"]

    def build(self, rng: random.Random, seed: int) -> Instance:
        start = rng.choice(self._START)
        fname = rng.choice(self._FILES)
        perms = perms_from_octal(start)
        whos = rng.sample(_WHO, 2)
        clauses: list[tuple[str, str, str]] = []
        for who in whos:
            op = rng.choices(["+", "-", "="], weights=[45, 40, 15])[0]
            bits = "".join(b for b in "rwx" if rng.random() < 0.45) or rng.choice("rwx")
            clauses.append((who, op, bits))
        final = apply_chmod(perms, clauses)
        # Каждая часть команды должна что-то менять, иначе задание вырождается.
        for i in range(len(clauses)):
            if apply_chmod(perms, clauses[:i] + clauses[i + 1:]) == final:
                raise GenerationRetry
        key = to_octal(final)
        cmd = ",".join(f"{w}{op}{b}" for w, op, b in clauses)
        wrongs: list[tuple[str, str]] = [(to_octal(final, {"r": 1, "w": 2, "x": 4}), "reversed_weights")]
        if "o" in whos:
            wrongs.append((to_octal(apply_chmod(perms, clauses, who_map={"o": "u"})), "o_is_owner"))
        if any(op == "+" for _, op, _ in clauses):
            wrongs.append((to_octal(apply_chmod(perms, clauses, plus_is_assign=True)), "plus_is_assign"))
        wrongs.append((key[::-1], "digits_reversed"))
        rng.shuffle(wrongs)
        # Спрашиваем запись режима из трёх цифр (как в `chmod 640`), а не вывод `stat -c '%a'`: stat печатает
        # число без ведущих нулей (режим 056 → «56»), и ключ «056» был бы неверен, а варианты вида «047» — заведомо
        # «не вывод stat» (подсказка). В записи для chmod ведущий ноль законен.
        code = f"$ stat -c '%A' {fname}\n{to_symbolic(perms)}\n$ chmod {cmd} {fname}"
        return self.choice_instance(
            seed,
            rng,
            prompt=(
                f"Какими станут права файла {fname} после команды chmod? Выберите их запись в восьмеричном виде "
                "(три цифры, как в команде chmod 640)."
            ),
            correct=key,
            wrongs=wrongs,
            code=code,
            language="text",
            why=(
                f"Было {to_symbolic(perms)} ({start}). chmod {cmd}: u — владелец, g — группа, o — остальные; "
                f"+ добавляет, - убирает, = задаёт права целиком. Стало {to_symbolic(final)}; каждая тройка "
                "переводится в цифру как r = 4, w = 2, x = 1."
            ),
        )


# ============================================================== конвейер grep | cut | sort | uniq


def sh_grep(lines: list[str], pattern: str, invert: bool = False) -> list[str]:
    return [ln for ln in lines if (pattern in ln) != invert]  # шаблоны без метасимволов: подстрока


def sh_cut(lines: list[str], field: int) -> list[str]:
    return [ln.split(" ")[field - 1] if len(ln.split(" ")) >= field else "" for ln in lines]


def sh_sort(lines: list[str]) -> list[str]:
    return sorted(lines)  # LC_ALL=C: побайтовое сравнение, для ASCII совпадает с порядком str


def sh_uniq_c(lines: list[str]) -> list[str]:
    out: list[str] = []
    prev, cnt = None, 0
    for ln in lines + [None]:  # type: ignore[list-item]
        if ln == prev:
            cnt += 1
            continue
        if prev is not None:
            out.append(f"{cnt:7d} {prev}")  # формат GNU uniq -c
        prev, cnt = ln, 1
    return out


def sh_sort_n(lines: list[str], reverse: bool) -> list[str]:
    """sort -n / -rn: числовой ключ, при равенстве — сравнение всей строки (тоже развёрнутое при -r)."""
    return sorted(lines, key=lambda ln: (int(ln.split()[0]), ln), reverse=reverse)


@register
class PipelineUniq(BankFamily):
    id = "sh.pipeline.uniq"
    title = "Конвейер grep | cut | sort | uniq -c по логу"
    specs = SPECS
    module = "git_linux"
    format = "predict_output"
    target_grade = "junior"
    position = -0.3
    time_limit_s = 150
    is_anchor = True
    measures = "Чтение конвейера: фильтр grep, поля cut, зачем sort перед uniq, sort -rn и head."
    misconceptions = {
        "uniq_without_sort": "uniq схлопывает только СОСЕДНИЕ одинаковые строки — без sort перед ним счётчики дробятся",
        "sort_ascending": "sort -rn сортирует по числу по убыванию, поэтому head берёт самые частые",
        "grep_ignored": "grep оставляет только строки с шаблоном — остальные в подсчёт не попадают",
        "fields_zero_based": "поля в cut -f нумеруются с 1",
    }

    _RES = ["orders", "items", "users", "cart", "invoices", "files"]

    def build(self, rng: random.Random, seed: int) -> Instance:
        ips = [f"10.0.{rng.randint(0, 9)}.{rng.randint(2, 254)}" for _ in range(5)]
        if len(set(ips)) < 5:
            raise GenerationRetry
        # dict.fromkeys, а не set: порядок не должен зависеть от PYTHONHASHSEED (детерминизм по seed).
        paths = list(dict.fromkeys(f"/api/{rng.choice(self._RES)}/{rng.randint(1, 999)}" for _ in range(6)))
        if len(paths) < 5:
            raise GenerationRetry
        variant = rng.randrange(3)
        c1 = rng.randint(3, 4)
        c2 = rng.randint(2, c1 - 1)
        counts = [c1, c2, 1, 1][: rng.randint(3, 4)]

        def line(ip: str, method: str, path: str, status: int) -> str:
            return f"{ip} {method} {path} {status}"

        lines: list[str] = []
        if variant == 0:  # 404 по путям
            pattern, invert, field = " 404", False, 3
            vals = rng.sample(paths, len(counts))
            for v, cnt in zip(vals, counts, strict=True):
                lines += [line(rng.choice(ips), rng.choice(["GET", "GET", "POST"]), v, 404) for _ in range(cnt)]
            lines += [line(rng.choice(ips), rng.choice(["GET", "POST"]), rng.choice(paths), rng.choice([200, 201, 302]))
                      for _ in range(rng.randint(4, 5))]
            cmd = "grep ' 404' access.log | cut -d' ' -f3 | sort | uniq -c | sort -rn | head -n 2"
            what = "какие адреса чаще всего отдают 404"
        elif variant == 1:  # POST по IP
            pattern, invert, field = "POST", False, 1
            vals = rng.sample(ips, len(counts))
            for v, cnt in zip(vals, counts, strict=True):
                lines += [line(v, "POST", rng.choice(paths), rng.choice([200, 201, 400])) for _ in range(cnt)]
            lines += [line(rng.choice(ips), "GET", rng.choice(paths), rng.choice([200, 304, 404]))
                      for _ in range(rng.randint(4, 5))]
            cmd = "grep 'POST' access.log | cut -d' ' -f1 | sort | uniq -c | sort -rn | head -n 2"
            what = "с каких IP приходит больше всего POST-запросов"
        else:  # всё, кроме 200, по путям
            pattern, invert, field = " 200", True, 3
            vals = rng.sample(paths, len(counts))
            for v, cnt in zip(vals, counts, strict=True):
                lines += [line(rng.choice(ips), rng.choice(["GET", "POST"]), v, rng.choice([404, 500, 502]))
                          for _ in range(cnt)]
            lines += [line(rng.choice(ips), rng.choice(["GET", "POST"]), rng.choice(paths), 200)
                      for _ in range(rng.randint(4, 5))]
            cmd = "grep -v ' 200' access.log | cut -d' ' -f3 | sort | uniq -c | sort -rn | head -n 2"
            what = "какие адреса чаще всего отвечают ошибкой"
        rng.shuffle(lines)

        def run(*, grep: bool = True, field_: int = field, pre_sort: bool = True, rev: bool = True) -> str:
            cur = sh_grep(lines, pattern, invert) if grep else list(lines)
            cur = sh_cut(cur, field_)
            if pre_sort:
                cur = sh_sort(cur)
            return "\n".join(sh_sort_n(sh_uniq_c(cur), reverse=rev)[:2])

        filtered = sh_cut(sh_grep(lines, pattern, invert), field)
        top = sorted((filtered.count(v) for v in set(filtered)), reverse=True)
        if len(top) < 3 or not top[0] > top[1] > top[2]:
            raise GenerationRetry  # первые две строки вывода должны определяться однозначно
        key = run()
        wrongs = [
            (run(pre_sort=False), "uniq_without_sort"),
            (run(rev=False), "sort_ascending"),
            (run(grep=False), "grep_ignored"),
            (run(field_=field + 1), "fields_zero_based"),
        ]
        if run(pre_sort=False) == key:
            raise GenerationRetry  # иначе роль sort перед uniq не проверяется
        code = "$ cat access.log\n" + "\n".join(lines) + f"\n$ {cmd}"
        return self.text_instance(
            seed,
            prompt=f"Администратор выясняет, {what}. Что выведет последняя команда (LC_ALL=C)?",
            code=code,
            language="text",
            raw_key=key,
            wrongs=wrongs,
            why=(
                "grep отбирает строки, cut -d' ' -fN берёт N-е поле (с 1), sort группирует одинаковые значения "
                "рядом, uniq -c схлопывает соседние повторы и пишет их число, sort -rn упорядочивает по числу "
                "по убыванию, head -n 2 берёт две первые строки."
            ),
            answer_hint=(
                "Введите вывод команды: число и значение в каждой строке; строки можно разделять пробелом "
                "или переводом строки, отступы не важны."
            ),
        )


# ============================================================== перенаправления bash

# Вид перенаправления → список операций слева направо: ("out"|"err", "trunc"|"append") или ("dup",) для 2>&1.
_REDIRS: dict[str, tuple[str, list[tuple[str, ...]]]] = {
    "trunc_out": ("> {f}", [("out", "trunc")]),
    "trunc_both": ("> {f} 2>&1", [("out", "trunc"), ("dup",)]),
    "append_both": (">> {f} 2>&1", [("out", "append"), ("dup",)]),
    "dup_then_append": ("2>&1 >> {f}", [("dup",), ("out", "append")]),
    "append_err": ("2>> {f}", [("err", "append")]),
}


def simulate_redirects(cmds: Sequence[tuple[str, str]], labels: tuple[str, str], *, dup_order_ignored: bool = False,
                       append_truncates: bool = False, out_takes_err: bool = False) -> list[str]:
    """Таблица дескрипторов bash: операции применяются слева направо, 2>&1 копирует ТЕКУЩУЮ цель stdout,
    открытие с > обрезает файл сразу. Возвращает строки файла. Флаги — модели заблуждений."""
    content: list[str] = []
    for word, kind in cmds:
        ops = list(_REDIRS[kind][1])
        if dup_order_ignored:
            ops = [o for o in ops if o != ("dup",)] + [o for o in ops if o == ("dup",)]
        fd = {1: "tty", 2: "tty"}
        for op in ops:
            if op == ("dup",):
                fd[2] = fd[1]
                continue
            stream, mode = op
            if append_truncates:
                mode = "trunc"
            if mode == "trunc":
                content.clear()
            fd[1 if stream == "out" else 2] = "file"
            if out_takes_err and stream == "out":
                fd[2] = "file"
        for num, text in ((1, f"{labels[0]}:{word}"), (2, f"{labels[1]}:{word}")):
            if fd[num] == "file":
                content.append(text)
    return content


@register
class RedirectOrder(BankFamily):
    id = "sh.redirect.order"
    title = "Перенаправления stdout/stderr в bash"
    specs = SPECS
    module = "git_linux"
    format = "predict_output"
    target_grade = "middle"
    position = 0.0
    time_limit_s = 150
    is_anchor = True
    measures = "> и >> (обрезка и дозапись), 2> и 2>>, порядок 2>&1 относительно перенаправления stdout."
    misconceptions = {
        "dup_order_ignored": "перенаправления применяются слева направо: 2>&1 до >> копирует старый stdout "
        "(терминал), поэтому stderr в файл не попадает",
        "append_truncates": ">> дописывает в конец файла, а обрезает его только >",
        "out_takes_err": "> и >> перенаправляют только stdout; stderr попадает в файл только через 2> или 2>&1",
    }

    _WORDS = ["build", "test", "lint", "pack", "deploy", "migrate", "seed", "scan", "sync", "check", "bench", "docs"]
    _LABELS = [("out", "err"), ("ok", "warn"), ("info", "error"), ("log", "fail")]
    _FILES = ["app.log", "ci.log", "build.log", "run.log"]

    def build(self, rng: random.Random, seed: int) -> Instance:
        words = rng.sample(self._WORDS, 4)
        labels = rng.choice(self._LABELS)
        fname = rng.choice(self._FILES)
        rest = ["append_both", "dup_then_append", "append_err"]
        rng.shuffle(rest)
        kinds = [rng.choice(["trunc_out", "trunc_both"]), *rest]
        cmds = list(zip(words, kinds, strict=True))
        script = "\n".join(
            [f'emit() {{ echo "{labels[0]}:$1"; echo "{labels[1]}:$1" >&2; }}', ""]
            + [f"emit {w} {_REDIRS[k][0].format(f=fname)}" for w, k in cmds]
        )
        key = "\n".join(simulate_redirects(cmds, labels))
        wrongs = [
            ("\n".join(simulate_redirects(cmds, labels, dup_order_ignored=True)), "dup_order_ignored"),
            ("\n".join(simulate_redirects(cmds, labels, append_truncates=True)), "append_truncates"),
            ("\n".join(simulate_redirects(cmds, labels, out_takes_err=True)), "out_takes_err"),
        ]
        return self.text_instance(
            seed,
            prompt=(
                f"Скрипт выполняется в bash в пустом каталоге. Функция emit пишет одну строку в stdout и одну в "
                f"stderr. Что окажется в файле {fname} после выполнения?"
            ),
            code=script,
            language="text",
            raw_key=key,
            wrongs=wrongs,
            why=(
                "> открывает файл с обрезкой, >> — на дозапись. Перенаправления читаются слева направо: "
                "«>> f 2>&1» — stdout в файл, затем stderr туда же; «2>&1 >> f» — stderr копирует ещё не "
                "перенаправленный stdout (терминал), и только потом stdout уходит в файл. 2>> f отправляет в файл "
                "только stderr."
            ),
            answer_hint="Введите содержимое файла: строки по порядку, через пробел или с новой строки.",
        )


# ============================================================== сценарии git (упорядочивание)

_SLUGS = ["payments", "search", "auth", "export", "billing", "profile", "reports", "webhooks", "cart"]
_SRC = ["api/views.py", "app/models.py", "core/settings.py", "services/billing.py", "README.md", "db/queries.sql",
        "api/schemas.py", "utils/dates.py"]


def _hash(rng: random.Random) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(7))


@register
class GitWorkflowOrder(BankFamily):
    id = "git.workflow.order"
    title = "Сценарии git: расставьте команды по порядку"
    specs = SPECS
    module = "git_linux"
    format = "parsons"
    target_grade = "junior"
    position = 0.5
    time_limit_s = 150
    answer_kind = "order"
    measures = "Рабочие процессы git: rebase с конфликтом, cherry-pick в релиз, разбиение коммита, stash и хотфикс."
    misconceptions = {
        "stale_base": "сначала git fetch: иначе rebase идёт на устаревший origin/main",
        "add_before_conflict": "git add отмечает конфликт решённым — до rebase/cherry-pick конфликта ещё нет",
        "continue_before_add": "--continue сработает только после git add исправленных файлов",
        "tests_mid_rebase": "тесты имеет смысл запускать, когда rebase завершён",
        "push_untested": "публиковать ветку — после проверки тестами",
        "pull_wrong_branch": "git pull обновляет текущую ветку: сначала переключитесь на релизную",
        "pick_on_stale_branch": "cherry-pick делают на обновлённую ветку, иначе push будет отклонён или "
        "придётся сливать заново",
        "push_mid_operation": "push имеет смысл после завершения cherry-pick/коммита — до этого публиковать нечего",
        "add_before_reset": "git reset HEAD~1 возвращает изменения в рабочую копию; add до него ничего не разбивает",
        "commit_before_add": "commit фиксирует только подготовленные через git add изменения",
        "both_files_in_first": "если добавить второй файл до первого коммита, оба изменения попадут в него",
        "push_before_commit": "push отправляет только закоммиченные изменения",
        "switch_with_dirty_tree": "сначала спрячьте правки в stash, иначе они переедут в ветку хотфикса",
        "commit_on_wrong_branch": "коммит хотфикса делают уже в новой ветке, а не в рабочей",
        "push_wrong_branch": "git push -u origin HEAD публикует текущую ветку — её нужно отправить до переключения",
        "pop_on_wrong_branch": "stash pop возвращает правки в текущую ветку — сначала вернитесь в рабочую",
    }

    def build(self, rng: random.Random, seed: int) -> Instance:
        feat = f"feature/{rng.choice(_SLUGS)}"
        scenario = rng.randrange(4)
        if scenario == 0:
            f = rng.choice(_SRC)
            steps = ["git fetch origin", "git rebase origin/main", f"git add {f}", "git rebase --continue",
                     "pytest -q", "git push --force-with-lease"]
            tags = {0: "stale_base", 1: "add_before_conflict", 2: "continue_before_add", 3: "tests_mid_rebase",
                    4: "push_untested"}
            prompt = (f"Вы на ветке {feat}. Перенесите её на свежий main из origin. При rebase будет конфликт в "
                      f"{f} (вы исправите его в редакторе). Затем прогоните тесты и обновите ветку на сервере.")
        elif scenario == 1:
            f, h = rng.choice(_SRC), _hash(rng)
            rel = f"release/{rng.randint(1, 3)}.{rng.randint(0, 12)}"
            steps = [f"git switch {rel}", "git pull --ff-only", f"git cherry-pick {h}", f"git add {f}",
                     "git cherry-pick --continue", f"git push origin {rel}"]
            tags = {0: "pull_wrong_branch", 1: "pick_on_stale_branch", 2: "add_before_conflict",
                    3: "continue_before_add", 4: "push_mid_operation"}
            prompt = (f"Исправление {h} уже в main. Перенесите его в ветку {rel}, предварительно обновив её с "
                      f"сервера. При переносе будет конфликт в {f} (вы исправите его в редакторе).")
        elif scenario == 2:
            f1, f2 = rng.sample(_SRC, 2)
            m1, m2 = f"fix {f1.split('/')[-1]}", f"update {f2.split('/')[-1]}"
            steps = ["git reset HEAD~1", f"git add {f1}", f'git commit -m "{m1}"', f"git add {f2}",
                     f'git commit -m "{m2}"', "git push --force-with-lease"]
            tags = {0: "add_before_reset", 1: "commit_before_add", 2: "both_files_in_first", 3: "commit_before_add",
                    4: "push_before_commit"}
            prompt = (f"Последний коммит ветки {feat} (уже на сервере) смешал два изменения: {f1} и {f2}. "
                      f"Разбейте его на два коммита — сначала «{m1}» с {f1}, затем «{m2}» с {f2} — "
                      "и обновите ветку на сервере.")
        else:
            x = f"hotfix/{rng.choice(_SLUGS)}-{rng.randint(100, 999)}"
            msg = f"fix {rng.choice(['timeout', 'null check', 'rounding', 'encoding', 'retry'])}"
            steps = ['git stash push -m "wip"', f"git switch -c {x} main", f'git commit -am "{msg}"',
                     "git push -u origin HEAD", f"git switch {feat}", "git stash pop"]
            tags = {0: "switch_with_dirty_tree", 1: "commit_on_wrong_branch", 2: "push_before_commit",
                    3: "push_wrong_branch", 4: "pop_on_wrong_branch"}
            prompt = (f"Вы в ветке {feat} с незакоммиченными правками. Нужно срочно сделать хотфикс от main в новой "
                      f"ветке {x} (правку внесёте в редакторе перед коммитом), опубликовать её и вернуться к "
                      "своей работе с сохранёнными правками.")
        return self.order_instance(
            seed,
            rng,
            prompt=prompt + " Расставьте команды в порядке выполнения.",
            steps=steps,
            swap_tags=tags,
            why=(
                "Каждая команда опирается на результат предыдущей: актуальная база до rebase/cherry-pick, git add "
                "после ручного исправления конфликта и до --continue, коммит до публикации, stash до переключения "
                "веток и stash pop после возвращения. После переписывания истории опубликованной ветки нужен "
                "push --force-with-lease."
            ),
        )


# ============================================================== диапазоны коммитов


class _Repo:
    """Граф коммитов: точная модель switch -c / commit / merge (без fast-forward — main всегда ушёл вперёд)."""

    def __init__(self) -> None:
        self.msgs: list[str] = ["init"]
        self.parents: list[list[int]] = [[]]
        self.branches: dict[str, int] = {"main": 0}
        self.head = "main"

    def commit(self, msg: str, extra_parent: int | None = None) -> None:
        self.msgs.append(msg)
        self.parents.append([self.branches[self.head]] + ([extra_parent] if extra_parent is not None else []))
        self.branches[self.head] = len(self.msgs) - 1

    def reach(self, branch: str) -> set[int]:
        seen, stack = set(), [self.branches[branch]]
        while stack:
            c = stack.pop()
            if c not in seen:
                seen.add(c)
                stack += self.parents[c]
        return seen


@register
class GitRangeDots(BankFamily):
    id = "git.range.dots"
    title = "git log A..B и A...B после слияния"
    specs = SPECS
    module = "git_linux"
    format = "predict_output"
    target_grade = "senior"
    position = 0.0
    c = 0.25
    time_limit_s = 180
    answer_kind = "choice"
    measures = "Достижимость в графе коммитов: A..B (есть в B, нет в A), A...B (симметричная разность) после merge."
    misconceptions = {
        "merged_commits_included": "коммит main, влитый в ветку через merge, достижим из обеих веток, поэтому не "
        "попадает ни в main..ветка, ни в ветка..main, ни в main...ветка",
        "range_reversed": "A..B — коммиты, достижимые из B и недостижимые из A, а не наоборот",
        "two_vs_three_dots": "две точки — только «есть в B, нет в A»; три точки — коммиты обеих сторон",
        "merge_commit_hidden": "merge-коммит — обычный коммит ветки, git log его показывает",
    }

    _FEAT_MSGS = ["add payment model", "add refund endpoint", "add webhooks", "validate amounts", "add retries",
                  "cache rates", "add export job", "paginate results"]
    _MAIN_MSGS = ["fix login timeout", "bump deps", "update CI", "fix typo in docs", "speed up tests",
                  "rotate logs", "fix migration"]

    def build(self, rng: random.Random, seed: int) -> Instance:
        feat = f"feature/{rng.choice(_SLUGS)}"
        fm = rng.sample(self._FEAT_MSGS, 3)
        mm = rng.sample(self._MAIN_MSGS, 2)
        two_before = rng.random() < 0.5
        repo = _Repo()
        script = [f"git switch -c {feat}"]
        repo.branches[feat] = 0
        repo.head = feat
        before = fm[:2] if two_before else fm[:1]
        for msg in before:
            repo.commit(msg)
            script.append(f'git commit -m "{msg}"')
        repo.head = "main"
        script.append("git switch main")
        repo.commit(mm[0])
        script.append(f'git commit -m "{mm[0]}"')
        repo.head = feat
        script.append(f"git switch {feat}")
        merge_msg = f"Merge branch 'main' into {feat}"
        repo.commit(merge_msg, extra_parent=repo.branches["main"])
        script.append("git merge main")
        repo.commit(fm[2])
        script.append(f'git commit -m "{fm[2]}"')
        repo.head = "main"
        script.append("git switch main")
        repo.commit(mm[1])
        script.append(f'git commit -m "{mm[1]}"')

        r_main, r_feat = repo.reach("main"), repo.reach(feat)
        merge_id = repo.msgs.index(merge_msg)

        def fmt(ids: set[int]) -> str:
            return ", ".join(repo.msgs[i] for i in sorted(ids, reverse=True)) if ids else "(пустой вывод)"

        sets: dict[str, Callable[[], set[int]]] = {
            "main..feat": lambda: r_feat - r_main,
            "feat..main": lambda: r_main - r_feat,
            "sym": lambda: r_feat ^ r_main,
        }
        variant = rng.choice(["main..feat", "feat..main", "sym"])
        if variant == "main..feat":
            rng_expr, correct = f"main..{feat}", sets["main..feat"]()
            wrongs = [(fmt(r_feat - {0}), "merged_commits_included"), (fmt(sets["feat..main"]()), "range_reversed"),
                      (fmt(sets["sym"]()), "two_vs_three_dots"), (fmt(correct - {merge_id}), "merge_commit_hidden")]
        elif variant == "feat..main":
            rng_expr, correct = f"{feat}..main", sets["feat..main"]()
            wrongs = [(fmt(r_main - {0}), "merged_commits_included"), (fmt(sets["main..feat"]()), "range_reversed"),
                      (fmt(sets["sym"]()), "two_vs_three_dots"),
                      (fmt(correct | {repo.msgs.index(mm[0])}), "merged_commits_included")]
        else:
            rng_expr, correct = f"main...{feat}", sets["sym"]()
            wrongs = [(fmt(sets["main..feat"]()), "two_vs_three_dots"), (fmt(sets["feat..main"]()), "range_reversed"),
                      (fmt((r_feat | r_main) - {0}), "merged_commits_included"),
                      (fmt(correct - {merge_id}), "merge_commit_hidden")]
        rng.shuffle(wrongs)
        code = "\n".join(["# в main уже есть коммит «init»; правки файлов перед коммитами опущены", *script,
                          f"git log --oneline {rng_expr}"])
        return self.choice_instance(
            seed,
            rng,
            prompt=(
                f"Команды выполнены по порядку; git merge main создаёт коммит «{merge_msg}». Какие коммиты "
                "(по сообщениям, от новых к старым) выведет последняя команда?"
            ),
            correct=fmt(correct),
            wrongs=wrongs,
            code=code,
            language="text",
            why=(
                "A..B — коммиты, достижимые из B и недостижимые из A. Коммит main, влитый в ветку через merge, "
                "достижим из обеих веток (из ветки — через merge-коммит), поэтому не попадает ни в один из этих "
                "диапазонов. A...B — симметричная разность: коммиты, достижимые ровно из одной из веток."
            ),
        )
