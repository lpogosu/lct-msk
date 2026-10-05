"""Конвейер разбора резюме (концепция §3.1.1):

pdfplumber → разделы по заголовкам → regex: email, телефон, Telegram, даты → ФИО (словари + окончания)
→ навыки по словарю app.matching.skills (Aho-Corasick + rapidfuzz ≥ 90) с цитатами-спанами
→ роли и софт-скиллы по словарям → стаж в месяцах по интервалам дат (пересечения не удваиваются)
→ [необязательно] LLM через app/llm/gateway.py: только добавляет поля, каждое с проверенной цитатой.

Результат — черновик: кандидат подтверждает или правит каждое поле. Всё распознанное — «заявлено»:
в категорию и Score не идёт никогда (DECISIONS п. 1). Детерминированно: один и тот же PDF и те же часы →
тот же результат.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import datetime

from app.llm.gateway import LLMGateway
from app.llm.masking import URL_RE, pii_spans
from app.matching.skills import Match, SkillCatalog, get_catalog
from app.services.resume.contacts import find_email, find_phone, find_telegram
from app.services.resume.dates import Period, find_periods, fmt_month, merged_months
from app.services.resume.extract import ExtractedText, extract_text
from app.services.resume.names import NameMatch, find_full_name
from app.services.resume.schemas import (
    ExperienceField,
    ExperiencePeriod,
    FullNameField,
    ListItem,
    LLMInfo,
    NameParts,
    Quote,
    ResumeParse,
    ScalarField,
    SectionOut,
)
from app.services.resume.sections import Section, iter_lines, section_spans, split_sections
from app.services.resume.settings import ResumeSettings

PARSER_VERSION = "rules-v1"

_EDU_WORDS = ("университет", "институт", "академи", "колледж", "техникум", "училищ", "школа", "university", "institute")
_COMPANY_MARKERS = ("ооо", "ао ", "пао", "зао", "ип ", "«", '"', "компания", "llc", "inc", "ltd", "гк ", "группа")
_TITLE_WORDS = (
    "разработчик", "программист", "инженер", "developer", "engineer", "аналитик", "стажер", "стажёр",
    "тимлид", "lead", "архитектор", "тестировщик", "devops", "специалист", "intern",
)


class _Ctx:
    def __init__(self, ext: ExtractedText) -> None:
        self.ext = ext
        self.text = ext.text

    def quote(self, start: int, end: int) -> Quote:
        return Quote(text=self.text[start:end], start=start, end=end, page=self.ext.page_of(start))

    def line_at(self, pos: int) -> tuple[int, int]:
        s = self.text.rfind("\n", 0, pos) + 1
        e = self.text.find("\n", pos)
        return s, (len(self.text) if e < 0 else e)


_LINK_LABEL_RE = re.compile(r"([\w\-]{2,20})\s*:\s*$")


def dictionary_text(text: str) -> str:
    """Текст для поиска по словарям навыков и ролей: контакты, ссылки и строка с датой рождения
    заменены пробелами (та же длина — цитаты остаются на своих местах). Иначе «….net» в адресе почты
    стал бы навыком .NET, а подпись ссылки «GitHub: github.com/…» — навыком GitHub."""
    spans = list(pii_spans(text))
    for m in URL_RE.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        label = _LINK_LABEL_RE.search(text[line_start : m.start()])
        if label:
            spans.append((line_start + label.start(1), line_start + label.end(1)))
    chars = list(text)
    for s, e in spans:
        for i in range(s, e):
            if not chars[i].isspace():
                chars[i] = " "
    return "".join(chars)


def _region_text_spans(sections: list[Section], *keys: str) -> list[tuple[int, int]]:
    spans = [(s.start, s.end) for s in sections if s.key in keys]
    return spans


def _items(
    ctx: _Ctx,
    matches: Iterable[Match],
    name_of: Callable[[str], str],
    *,
    prefer: list[tuple[int, int]] | None = None,
) -> list[ListItem]:
    """Уникальные по id совпадения; цитата — первое вхождение (в предпочтительных разделах, если есть)."""
    chosen: dict[str, Match] = {}
    for m in matches:
        cur = chosen.get(m.id)
        if cur is None:
            chosen[m.id] = m
            continue
        if prefer and any(s <= m.start < e for s, e in prefer) and not any(s <= cur.start < e for s, e in prefer):
            chosen[m.id] = m
    ordered = sorted(chosen.values(), key=lambda m: m.start)
    return [ListItem(id=m.id, name=name_of(m.id), quote=ctx.quote(m.start, m.end)) for m in ordered]


def _first_match(matches: list[Match], spans: list[tuple[int, int]]) -> Match | None:
    for m in sorted(matches, key=lambda m: m.start):
        if not spans or any(s <= m.start < e for s, e in spans):
            return m
    return None


def _education(ctx: _Ctx, sections: list[Section]) -> ScalarField | None:
    for s, e in section_spans(sections, "education"):
        lines = [(a + s, b + s, ln) for a, b, ln in iter_lines(ctx.text[s:e]) if ln.strip()]
        if not lines:
            continue
        pick = next((x for x in lines if any(w in x[2].lower() for w in _EDU_WORDS)), lines[0])
        a, b, ln = pick
        lead = len(ln) - len(ln.lstrip())
        trail = len(ln.rstrip())
        value = ln.strip()[:300]
        return ScalarField(value=value, quote=ctx.quote(a + lead, a + trail), confidence=0.8)
    return None


def _job_context(ctx: _Ctx, p: Period) -> tuple[str | None, str | None]:
    """Должность и компания рядом со строкой периода: та же строка и до трёх следующих."""
    ls, le = ctx.line_at(p.span[0])
    candidates = [ctx.text[ls:le].replace(ctx.text[p.span[0] : p.span[1]], " ")]
    pos = le + 1
    for _ in range(3):
        if pos >= len(ctx.text):
            break
        s, e = ctx.line_at(pos)
        if ctx.text[s:e].strip():
            candidates.append(ctx.text[s:e])
        pos = e + 1
    title = company = None
    for raw in candidates:
        line = raw.strip(" ,—–-|\t")
        low = line.lower()
        if not line:
            continue
        if company is None and any(mk in low + " " for mk in _COMPANY_MARKERS):
            company = line[:200]
        elif title is None and any(w in low for w in _TITLE_WORDS):
            title = line[:200]
    return title, company


def parse_resume(
    data: bytes,
    *,
    now: datetime,
    settings: ResumeSettings | None = None,
    catalog: SkillCatalog | None = None,
    gateway: LLMGateway | None = None,
) -> tuple[ExtractedText, ResumeParse]:
    """PDF → (извлечённый текст, черновик полей с цитатами). ResumeError — понятная ошибка для человека."""
    s = settings or ResumeSettings()
    ext = extract_text(data, max_pages=s.max_pages, min_text_chars=s.min_text_chars)
    return ext, parse_text(ext, now=now, catalog=catalog, gateway=gateway)


def parse_text(
    ext: ExtractedText,
    *,
    now: datetime,
    catalog: SkillCatalog | None = None,
    gateway: LLMGateway | None = None,
) -> ResumeParse:
    cat = catalog or get_catalog()
    ctx = _Ctx(ext)
    text = ctx.text
    sections = split_sections(text)
    header_end = next((sec.end for sec in sections if sec.key == "header"), 0)
    head_spans = _region_text_spans(sections, "header", "contacts", "position")
    warnings: list[str] = []

    # --- ФИО
    full_name: FullNameField | None = None
    nm = find_full_name(text, header_end)
    if nm is not None:
        full_name = FullNameField(
            value=nm.value,
            quote=ctx.quote(nm.start, nm.end),
            confidence=min(1.0, nm.score / 7.5),
            parts=NameParts(last=nm.last, first=nm.first, middle=nm.middle),
        )
    else:
        warnings.append("ФИО не распознано — впишите вручную")

    # --- Контакты: сначала шапка и раздел «Контакты», затем весь текст
    def contact(finder: Callable[[str], object]) -> ScalarField | None:
        for s, e in [*head_spans, (0, len(text))]:
            f = finder(text[s:e])
            if f is not None:
                fs, fe = s + f.start, s + f.end  # type: ignore[attr-defined]
                return ScalarField(value=f.value, quote=ctx.quote(fs, fe))  # type: ignore[attr-defined]
        return None

    email = contact(find_email)
    phone = contact(find_phone)
    telegram = contact(find_telegram)
    if email is None and phone is None and telegram is None:
        warnings.append("Контакты не найдены — укажите хотя бы один способ связи")

    # --- Город: шапка, контакты, строка «Проживает: …»
    city: ScalarField | None = None
    cm = _first_match(cat.find_cities(text), head_spans) or next(
        (m for m in cat.find_cities(text) if "прожива" in text[ctx.line_at(m.start)[0] : m.start].lower()), None
    )
    if cm is not None:
        c = cat.cities[cm.id]
        city = ScalarField(value=cm.id, label=c.name, quote=ctx.quote(cm.start, cm.end), confidence=0.9)

    # --- Заявленный грейд: шапка, желаемая должность, «о себе»
    grade_spans = _region_text_spans(sections, "header", "position", "summary")
    gm = _first_match(cat.find_grade_markers(text), grade_spans) if grade_spans else None
    declared_grade = None
    if gm is not None:
        names = {g["code"]: g["name"] for g in cat.taxonomy["grades"]}
        declared_grade = ScalarField(value=gm.id, label=names.get(gm.id, gm.id), quote=ctx.quote(gm.start, gm.end), confidence=0.8)

    # --- Навыки, роли, софт-скиллы (словари подбора — те же, что в брифе); контакты и ссылки не ищем
    dict_text = dictionary_text(text)
    skills_pref = section_spans(sections, "skills")
    skills = _items(ctx, cat.find_skills(dict_text, fuzzy=True), cat.name, prefer=skills_pref)
    role_spans = _region_text_spans(sections, "header", "position", "experience", "summary")
    roles = _items(
        ctx,
        [m for m in cat.find_roles(dict_text) if not role_spans or any(s <= m.start < e for s, e in role_spans)],
        lambda rid: cat.roles[rid].name,
    )
    soft = _items(
        ctx,
        cat.find_soft_skills(dict_text),
        lambda sid: cat.soft_skills[sid],
        prefer=section_spans(sections, "soft_skills", "summary"),
    )

    # --- Стаж: периоды в разделе «Опыт работы»; без него — всё, кроме образования и курсов
    exp_spans = section_spans(sections, "experience")
    if exp_spans:
        periods = find_periods(text, now, exp_spans)
    else:
        excluded = section_spans(sections, "education", "courses")
        periods = [p for p in find_periods(text, now) if not any(s <= p.span[0] < e for s, e in excluded)]
    exp_periods = []
    for p in periods:
        title, company = _job_context(ctx, p)
        exp_periods.append(
            ExperiencePeriod(
                start=fmt_month(p.start),
                end=None if p.present else fmt_month(p.end),
                months=p.months,
                title=title,
                company=company,
                quote=ctx.quote(*p.span),
            )
        )
    months, overlap = merged_months(periods)
    experience = ExperienceField(value=months if periods else None, periods=exp_periods, overlap_months=overlap)
    if not periods:
        warnings.append("Периоды работы не найдены — укажите стаж вручную")

    parse = ResumeParse(
        parser_version=PARSER_VERSION,
        pages=ext.pages,
        text_chars=len(text),
        llm=LLMInfo(used=False),
        sections=[SectionOut(key=sec.key, title=sec.title, start=sec.start, end=sec.end) for sec in sections],
        full_name=full_name,
        email=email,
        phone=phone,
        telegram=telegram,
        city=city,
        declared_grade=declared_grade,
        education=_education(ctx, sections),
        experience=experience,
        skills=skills,
        roles=roles,
        soft_skills=soft,
        warnings=warnings,
    )
    if gateway is not None and gateway.enabled:
        _llm_enrich(parse, ctx, cat, gateway, name_spans=name_occurrences(text, nm))
    return parse


def name_occurrences(text: str, nm: NameMatch | None) -> list[tuple[int, int]]:
    """Все упоминания частей ФИО в тексте (подпись, «Отзыв об Иванове», email-ник уже скрыт регуляркой),
    включая падежные формы (+ до 3 букв окончания), — чтобы до LLM не дошло ни одно из них."""
    if nm is None:
        return []
    spans = [(nm.start, nm.end)]
    for part in (nm.last, nm.first, nm.middle):
        if not part or len(part) < 2:
            continue
        stems = {part}
        if len(part) >= 5:
            stems.add(part[:-1])  # Иванова → Иванов-, Мария → Мари- (Марии, Марию)
        for stem in stems:
            tail = r"[а-яёa-z]{0,3}" if len(stem) >= 3 else ""
            for m in re.finditer(rf"(?<![\w-]){re.escape(stem)}{tail}(?![\w])", text, re.IGNORECASE):
                spans.append((m.start(), m.end()))
    return spans


_LLM_INSTRUCTIONS = (
    "Найди в резюме: field=skill — технологии и инструменты; field=role — должности (например, "
    "«Python-разработчик»); field=soft_skill — личные качества; field=declared_grade — заявленный уровень "
    "(стажёр, junior, middle, senior). value — короткое название, quote — дословная цитата."
)


def _llm_enrich(
    parse: ResumeParse, ctx: _Ctx, cat: SkillCatalog, gateway: LLMGateway, name_spans: list[tuple[int, int]]
) -> None:
    """LLM только ДОБАВЛЯЕТ элементы справочника, которых не нашли правила, и только с проверенной цитатой."""
    res = gateway.extract(
        "resume",
        ctx.text,
        _LLM_INSTRUCTIONS,
        allowed_fields=("skill", "role", "soft_skill", "declared_grade"),
        extra_pii_spans=name_spans,
    )
    parse.llm = LLMInfo(used=res.used, provider=res.provider, rejected=res.rejected)
    if not res.used:
        return
    added = 0
    finders: dict[str, tuple[Callable[[str], list[Match]], list[ListItem], Callable[[str], str]]] = {
        "skill": (lambda v: cat.find_skills(v, fuzzy=True), parse.skills, cat.name),
        "role": (cat.find_roles, parse.roles, lambda rid: cat.roles[rid].name),
        "soft_skill": (cat.find_soft_skills, parse.soft_skills, lambda sid: cat.soft_skills[sid]),
    }
    for f in res.fields:
        quote = Quote(text=f.quote, start=f.start, end=f.end, page=ctx.ext.page_of(f.start))
        if f.field == "declared_grade":
            hits = cat.find_grade_markers(f.value)
            if parse.declared_grade is None and hits:
                names = {g["code"]: g["name"] for g in cat.taxonomy["grades"]}
                parse.declared_grade = ScalarField(value=hits[0].id, label=names.get(hits[0].id), quote=quote, source="llm", confidence=0.6)
                added += 1
            continue
        finder, target, name_of = finders[f.field]
        hits = finder(f.value)
        if not hits:  # значение не из справочника — не добавляем (заявленные навыки — только справочные)
            parse.llm.rejected += 1
            continue
        sid = hits[0].id
        if any(item.id == sid for item in target):
            continue
        target.append(ListItem(id=sid, name=name_of(sid), quote=quote, source="llm"))
        added += 1
    parse.llm.added = added
