"""Разделы отчёта: test-retest и разделяющая способность по специализациям (2.11) и «Набор жюри» (7).

Текст генерируется из `result["spec_validation"]` (eval.spec_validation.run); выводы считаются по числам,
а не пишутся вручную, чтобы отчёт не расходился с прогоном.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from eval.report import num, pct, table

SPEC_RU = {"backend_python": "Backend (Python)", "frontend_js": "Frontend (JS/TS)", "qa": "QA", "devops": "DevOps", "all": "Все вместе"}
GRADE_RU = {"none": "не подтв.", "intern": "Стажёр", "junior": "Junior", "middle": "Middle", "senior": "Senior"}
CUT_RU = {"intern|junior": "Стажёр/Junior", "junior|middle": "Junior/Middle", "middle|senior": "Middle/Senior"}
PNG = (("validation_retest.png", "Test-retest по специализациям"), ("validation_confusion.png", "Матрица ошибок"),
       ("validation_cuts.png", "Точность решения у порогов"))


def _blocks(sv: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    return [(k, v) for k, v in sv["by_spec"].items()] + [("all", sv["overall"])]


def spec_section(r: Mapping[str, Any], doc_dir: Path) -> list[str]:
    sv = r.get("spec_validation")
    if not sv:
        return []
    cfg = sv["config"]
    bl = _blocks(sv)
    L = ["### 2.11. Test-retest и разделяющая способность по специализациям", "",
         f"Отдельный эксперимент на тех же движках (`eval/spec_validation.py`): по {num(cfg['n_per_spec'])} латентных профилей на "
         "каждую из четырёх специализаций, поровну по грейдам Стажёр / Junior / Middle / Senior, заявка 60% верно / 30% на "
         "ступень выше / 10% ниже. Один и тот же профиль проходит три сессии: **A** — основная; **B** — независимая "
         "параллельная (другой сид → другие клоны и другая траектория CAT, семейства могут повториться); **C** — пересдача "
         "по реальным правилам (spec-testing §7.1: семейства сессии A исключены, то есть другая экспозиция заданий). "
         "Истина — латентный θ профиля; в расчёт точности не входят кандидаты, заявившие грейд ниже истинного: тест на "
         "заявленный грейд не может выдать больше, повышение — отдельный тест, это правило продукта, а не ошибка теста.", ""]
    for f, alt in PNG:
        if (doc_dir / "img" / f).exists():
            L += [f"![{alt}](img/{f})", ""]
    # --- test-retest
    L += ["**Test-retest** (A↔B — параллельные формы; A↔C — по правилам пересдачи).", ""]
    rows = []
    for k, b in bl:
        p, c = b["retest_parallel"], b["retest_real_rules"]
        rows.append((SPEC_RU[k], b["n"], pct(p["grade_exact"]), pct(p["grade_within1"]), num(p["grade_qwk"]), num(p["theta_r"]),
                     pct(p["decision_agreement"]), num(p["decision_kappa"]), pct(c["grade_exact"]), num(c["grade_qwk"]),
                     num(c["theta_r"]), num(b["mean_shared_families_real_rules"], 1)))
    L += table(["Специализация", "n", "A↔B точно", "±1", "QWK", "r(θ)", "решение сдал/не сдал совпало", "κ решения",
                "A↔C точно", "A↔C QWK", "A↔C r(θ)", "общих семейств в C"], rows)
    L += ["", "Доля профилей, у которых решение «перевернулось» через порог между сессиями A и B (весь профиль; в скобках — только "
          "рядом с порогом, |θ − порог| ≤ 0,5, где ошибки ожидаемы):", ""]
    rows = []
    for k, b in bl:
        p = b["retest_parallel"]
        fl = p["flip_across_cut"]
        rows.append((SPEC_RU[k], pct(fl["intern|junior"]), pct(fl["junior|middle"]), pct(fl["middle|senior"]), pct(p["flip_any_cut"]),
                     f"{pct(1 - p['near_cut_exact'])} (n = {p['near_cut_n']})" if p.get("near_cut_exact") is not None else "—",
                     num(p["theta_sd_diff"])))
    L += table(["Специализация", "Стажёр/Junior", "Junior/Middle", "Middle/Senior", "любой порог", "рядом с порогом: грейд разошёлся",
                "SEM по паре сессий"], rows)
    # --- separation
    L += ["", "**Разделяющая способность: итог теста против истинного грейда.**", ""]
    rows = []
    for k, b in bl:
        s = b["separation"]
        rows.append((SPEC_RU[k], s["n"], pct(s["grade_exact"]), pct(s["grade_within1"]), num(s["grade_qwk"]), num(s["reliability"]),
                     num(s["sem_posterior_mean"]), num(s["sem_posterior_near_cut"]), num(s["length_mean"], 1), num(s["length_p95"], 0)))
    L += table(["Специализация", "n", "Точный грейд", "±1", "QWK", "Надёжность", "SEM (апост.)", "SEM у порогов", "Заданий", "p95"], rows)
    conf = sv["overall"]["separation"]["confusion"]
    L += ["", "Матрица ошибок, все специализации (строки — истинный грейд, столбцы — итог теста):", ""]
    cols = ["none", "intern", "junior", "middle", "senior"]
    L += table(["Истина \\ итог", *(GRADE_RU[c] for c in cols)],
               [(GRADE_RU[g], *(conf.get(g, {}).get(c, 0) for c in cols)) for g in ("intern", "junior", "middle", "senior")])
    L += ["", "Точность решения «выше порога / ниже» у каждого порога (всего / рядом с порогом) и AUC оценки уровня между соседними грейдами (ровно «ниже/выше» порога):", ""]
    cuts = ["intern|junior", "junior|middle", "middle|senior"]
    rows = []
    for k, b in bl:
        pc = b["separation"]["per_cut"]
        rows.append((SPEC_RU[k], *(f"{pct(pc[c]['decision_accuracy'])} / {pct(pc[c]['near_cut_accuracy'])} / {num(pc[c]['auc_theta'])}"
                                   for c in cuts)))
    L += table(["Специализация", "Стажёр/Junior", "Junior/Middle", "Middle/Senior"], rows)
    L += ["", "Информация Фишера, набранная заданиями сессии у порога, и SEM = 1/√I (среднее по профилям):", ""]
    rows = []
    for k, b in bl:
        inf = b["separation"]["information"]
        rows.append((SPEC_RU[k], *(f"{num(inf[c]['mean_information'], 1)} / {num(inf[c]['sem_from_information'])}" for c in cuts)))
    L += table(["Специализация", "Стажёр/Junior: I / SEM", "Junior/Middle: I / SEM", "Middle/Senior: I / SEM"], rows)
    # --- дискриминация заданий
    L += ["", "**Дискриминация заданий** (зачётные задания сессий A; r_pb — ответ против оценки уровня без этого ответа; "
          "A_eff — крутизна IRT-кривой из банка; ΔP — прирост доли верных между соседними грейдами):", ""]
    rows = []
    for k, b in bl:
        d = b.get("discrimination")
        if d:
            rows.append((SPEC_RU[k], d["families"], num(d["r_pb_median"]), num(d["r_pb_p10"]), pct(d["share_r_pb_ge_020"]),
                         num(d["a_eff_median"]), num(d["dp_adjacent_median"]), ", ".join(d["weak_families"]) or "нет"))
    L += table(["Специализация", "Семейств (n ≥ 25 ответов)", "Медиана r_pb", "10-й перцентиль", "Доля r_pb ≥ 0,20",
                "Медиана A_eff", "Медиана ΔP", "Флаг r_pb < 0,10"], rows)
    # --- устойчивость
    L += ["", "**Эквивалентность клонов.** Живых клонов у нас нет, поэтому разброс сложности клона σ_inst "
          f"(в конфиге {num(sv['config']['sigma_inst'])}) — допущение. Проверяем чувствительность: как меняются точность и "
          "ретест, если клоны на самом деле разнятся меньше или больше (по каждой специализации, доля точного грейда против "
          "истины / доля совпадения при ретесте):", ""]
    sig = [x["sigma_inst"] for x in sv["by_spec"][next(iter(sv["by_spec"]))]["clone_sensitivity"]["rows"]]
    rows = []
    for k, b in sv["by_spec"].items():
        rows.append((SPEC_RU[k], *(f"{pct(x['grade_exact_vs_truth'])} / {pct(x['retest_grade_exact'])}" for x in b["clone_sensitivity"]["rows"])))
    L += table(["Специализация", *(f"σ_inst = {num(s)}" for s in sig)], rows)
    L += ["", "**Устойчивость к утечке ответов.** Коалиция честных участников выкладывает всё, что видела; слабый кандидат "
          "(Стажёр/Junior, заявка на ступень выше) вводит слитый ключ чужого клона. Сравнение парное: та же сессия честно и со "
          "слитыми ключами. «Осторожный» списывающий, видя другой код, отвечает честно — его прирост равен нулю по построению, "
          "поэтому в таблице наивный.", ""]
    rows = []
    for k, b in sv["by_spec"].items():
        lk = b["leak"]
        ci_ = lk["theta_gain_ci95"]
        rows.append((SPEC_RU[k], lk["n"], lk["coalition"], pct(lk["leaked_share_of_spec_bank"]),
                     f"{num(lk['theta_gain_mean'])} [{num(ci_[0])}; {num(ci_[1])}]" if ci_ else num(lk["theta_gain_mean"]),
                     pct(lk["false_pass_honest"]), pct(lk["false_pass_with_leak"]), pct(lk["share_flagged_foreign_key"]),
                     f"{lk['detector_active_families']} из {lk['bank_families']}"))
    L += table(["Специализация", "n", "Коалиция", "Банк слит", "Прирост оценки уровня [95% ДИ]", "Ложный «сдал» честно", "со слитыми ключами",
                "Пойман «чужим ключом»", "Детектор активен для семейств"], rows)
    L += ["", "**«Двойник в буфере»: покрытие банка.** Доля экземпляров, для которых строится двойник (подменённый литерал, ключ "
          "двойника ≠ ключа), — детерминированно, без людей и без LLM:", ""]
    rows = [(SPEC_RU[k], b["twin"]["families"], b["twin"]["families_with_twin"], pct(b["twin"]["twin_built_share"]),
             pct(b["twin"]["twin_key_differs_share_of_built"]),
             ", ".join(f"{a}: {n}" for a, n in sorted(b["twin"]["no_twin_reasons"].items(), key=lambda x: -x[1])) or "—")
            for k, b in sv["by_spec"].items()]
    L += table(["Специализация", "Семейств", "С двойником", "Экземпляров с двойником", "Ключ двойника ≠ ключа", "Почему нет двойника (экземпляров)"], rows)
    pi = sv["pilot_antiassist"]
    L += ["", "**Пилот анти-ассиста** (сводка без персональных данных). Пилот 1: "
          f"{pi['pilot1']['models']} модели одного вендора × 2 условия × {pi['pilot1']['clean_items']} обычных задания = "
          f"{pi['pilot1']['answers']} ответов, верных {pi['pilot1']['correct']} — правильный ответ ИИ по одному ответу не отличить "
          f"от сильного человека. Пилот 2: построено {pi['pilot2']['items_built']} заданий с двойником (predict_output — "
          f"{pi['pilot2']['formats']['predict_output']}, sql_result — {pi['pilot2']['formats']['sql_result']}); ключ двойника ≠ ключа "
          f"и все гейты пройдены у {pi['pilot2']['gates_passed']} из {pi['pilot2']['items_built']}. **Прогон моделей на этих "
          "ловушках не выполнен, поэтому recall двойника не измерен.** " + pi["honest_status"], ""]
    L += _conclusions(sv)
    return L


def _conclusions(sv: Mapping[str, Any]) -> list[str]:
    by = sv["by_spec"]
    ov = sv["overall"]
    ex = {k: b["separation"]["grade_exact"] for k, b in by.items()}
    worst = min(ex, key=lambda k: ex[k])
    best = max(ex, key=lambda k: ex[k])
    rr = {k: b["retest_real_rules"]["grade_exact"] for k, b in by.items()}
    worst_rr = min(rr, key=lambda k: rr[k])
    rt = {k: b["retest_parallel"]["grade_exact"] for k, b in by.items()}
    worst_rt = min(rt, key=lambda k: rt[k])
    twin_low = [SPEC_RU[k] for k, b in by.items() if (b["twin"]["twin_built_share"] or 0) < 0.5]
    weak = sorted({f for b in by.values() for f in b["discrimination"]["weak_families"]})
    gains = [b["leak"]["theta_gain_ci95"][1] for b in by.values() if b["leak"]["theta_gain_ci95"]]
    L = ["**Выводы по 2.11 (считаются по числам выше).**", "",
         f"- Разделяющая способность: точный грейд против истины — {pct(ov['separation']['grade_exact'])}, ±1 — "
         f"{pct(ov['separation']['grade_within1'])}, QWK {num(ov['separation']['grade_qwk'])}. Лучше всего {SPEC_RU[best]} "
         f"({pct(ex[best])}), слабее всего {SPEC_RU[worst]} ({pct(ex[worst])}). Грубые ошибки (через ступень) редки; ошибаются "
         "соседние грейды — у порога.",
         f"- Test-retest: точное совпадение грейда между независимыми сессиями {pct(ov['retest_parallel']['grade_exact'])}, "
         f"между сессией и пересдачей по правилам {pct(ov['retest_real_rules']['grade_exact'])}; слабее всего {SPEC_RU[worst_rt]} "
         f"({pct(rt[worst_rt])}). Решение «выше/ниже порога» повторяется в {pct(ov['retest_parallel']['decision_agreement'])} случаев. "
         f"При пересдаче по правилам слабее всего {SPEC_RU[worst_rr]} ({pct(rr[worst_rr])}): в малых банках (QA, DevOps) после "
         "исключения семейств первой сессии остаётся меньше подходящих по сложности заданий. κ решения около 0,5 — умеренная. "
         "Точное совпадение при 4–5 уровнях и ~16 заданиях не может быть близким к 100%; расхождения сосредоточены у порогов.",
         f"- Утечка: у наивного списывающего слитые ключи не дают прироста (верхняя граница 95% ДИ прироста оценки уровня по специализациям: "
         f"{num(max(gains)) if gains else '—'} ≤ 0). Это модельный результат при допущении σ_inst и уникальных клонов; утечку **идеи** "
         "семейства он не покрывает (см. 2.6, CUSUM).",
         ("- Двойник строится не везде: доля экземпляров с двойником ниже 50% у " + ", ".join(twin_low)
          + " — остальные семейства закрыты только клонами и временем ответа.") if twin_low else
         "- Двойник покрывает не менее половины экземпляров во всех специализациях.",
         ("- Семейства с r_pb < 0,10 (кандидаты на пересмотр): " + ", ".join(weak) + ".") if weak else
         "- Семейств с r_pb < 0,10 нет.",
         "- Всё это симуляция: модель ответа — Раш с экспертными сложностями, а не живые люди; точность против истины отражает "
         "процедуру, а не реальную валидность заданий. Живой ретест на людях не проводился.", ""]
    return L


def jury_section() -> list[str]:
    return [
        "## 7. Набор жюри: как проверить вручную на своих профилях", "",
        "Организаторы проверяют воспроизводимость и разделяющую способность на своём закрытом наборе синтетических профилей. "
        "Есть два пути.", "",
        "**A. Test-retest и разделяющая способность (тест) — `eval/jury_check.py`.** Принимает JSONL или CSV с профилями "
        "и прогоняет их через боевой CAT-движок (три сессии на профиль: основная, параллельная, пересдача по правилам).", "",
        "```", "cd backend",
        "uv run python -m eval.jury_check eval/jury_profiles_example.jsonl --seed 1 --out eval/out/jury_check.json", "```", "",
        "Формат строки (CSV — те же имена столбцов, заголовок обязателен):", "",
        *table(["Поле", "Значение", "Обязательно"],
               [("`id`", "уникальная строка", "да"),
                ("`spec`", "`backend_python` · `frontend_js` · `qa` · `devops`", "да"),
                ("`true_grade`", "ваш эталон: `intern` · `junior` · `middle` · `senior`", "да"),
                ("`theta`", "латентный уровень на шкале движка (пороги: Стажёр/Junior −1, Junior/Middle 0, Middle/Senior +1); "
                            "нет — берётся из полосы грейда по сиду", "нет"),
                ("`claimed_grade`", "грейд, на который профиль проходит тест; нет — равен `true_grade`", "нет")]),
        "", "Пример: `backend/eval/jury_profiles_example.jsonl` (16 профилей). Вывод — таблица в консоль и JSON "
        "(`eval/out/jury_check.json`): согласие грейдов, κ, r(θ), доли переворотов, матрица ошибок против `true_grade`, "
        "точность и AUC у каждого порога, информация и SEM у порогов, дискриминация заданий и грейды по каждому профилю. "
        "Фиксированный `--seed` даёт тот же результат. Неверная строка → код 2 и сообщение с номером строки и полем.", "",
        "**B. Подбор: свои пары «бриф × кандидат» — админ-API.** Формат строк — `backend/eval/pairs_test.jsonl` (схема "
        "`pairs_test.schema.json`, раздел 5): бриф, слепые признаки кандидата (или `features: null` — без теста), `rel` 0–3.", "",
        "1. `POST /api/admin/import` — загрузить пары жюри (данные в банк платформы не попадают); `GET /api/admin/import` — список загрузок.",
        "2. `POST /api/admin/eval/pairs` — P@5, P@10, nDCG@10 с 95% бутстреп-ДИ по брифам (тот же код, что в `make validate`); "
        "без `pairs` берётся последний импорт.",
        "3. `POST /api/admin/eval/rank` — ранжирование пула по брифу нашей формулой Score с объяснениями (CSV — `?format=csv`).", "",
        "Требуется роль администратора; ключи доступа — в `docs/API.md`.", ""]
