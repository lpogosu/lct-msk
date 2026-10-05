"""Версии подборки: неизменяемые снимки, неразрушающие фильтры, diff-бейджи, шортлист, откат.

Концепция §3.2 шаги 5–6, §4.4 п.5; spec-contact §2.3, §3.6:
- версия хранит снимок брифа, категории, фильтры, пресет, версию конфига и полный ранжированный список
  (≤ 500 строк с components, reasons, hidden_by[]); фильтр никого не удаляет — он заполняет hidden_by;
- любая правка брифа / фильтра / пресета — новая версия; откат — перенос указателя без пересчёта,
  на любую из последних 10 версий;
- шортлист живёт отдельно от версий; закреплённый кандидат, не прошедший фильтр, — с жёлтой меткой;
- вкладки «По категориям» (≤ 50 на категорию, страницы по 10) и «Общий топ» (без дублей Fullstack).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import MappingProxyType
from typing import Any

from app.matching.explain import Explanation, NeighborDelta, Reason, explain, neighbor_delta, version_medians
from app.matching.scoring import (
    BriefSpec,
    CandidateFeatures,
    CategoryStats,
    ScoreResult,
    ScoringConfig,
    ScoringContext,
    SkillEvidence,
    round1,
    score_candidate,
    sort_key,
)
from app.matching.skills import SkillCatalog, get_catalog


class SelectionError(ValueError):
    """Ошибка операции с подборкой (сообщение — для пользователя)."""


# --------------------------------------------------------------------------- фильтры

FILTER_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "has_fsp": "Есть ФСП",
        "salary_fit": "Ожидания в вилке",
        "format": "Формат подходит",
        "city": "Город или релокация",
        "must_confirmed": "Все обязательные подтверждены",
        "skill": "Навык подтверждён",
        "min_score": "Score не ниже",
        "active": "Активен за 30 дней",
        "industry": "Отрасль интереса",
        "grade": "Грейд",
        "confirmed_only": "Только подтверждённые тестом",
        "exclude": "Скрыть вручную",
    }
)
# Фильтры, которым нужно значение (навык, порог, список отраслей / грейдов / кандидатов).
VALUE_FILTERS = frozenset({"skill", "min_score", "industry", "grade", "exclude"})


@dataclass(frozen=True)
class Filter:
    """Неразрушающий фильтр. value — str / float / tuple (хешируемый, чтобы версия оставалась неизменяемой)."""

    code: str
    value: Any = None

    def __post_init__(self) -> None:
        if self.code not in FILTER_LABELS:
            raise SelectionError(f"Неизвестный фильтр: {self.code}")
        if isinstance(self.value, list):
            object.__setattr__(self, "value", tuple(self.value))
        elif isinstance(self.value, (set, frozenset)):  # множество → кортеж в детерминированном порядке
            object.__setattr__(self, "value", tuple(sorted(self.value)))
        if self.code in VALUE_FILTERS and (self.value is None or self.value == () or self.value == ""):
            raise SelectionError(f"Для фильтра «{FILTER_LABELS[self.code]}» нужно значение")
        if self.code == "min_score":
            try:
                float(self.value)
            except (TypeError, ValueError):
                raise SelectionError("Порог Score должен быть числом") from None

    @property
    def label(self) -> str:
        return FILTER_LABELS[self.code]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "value": list(self.value) if isinstance(self.value, tuple) else self.value,
            "label": self.label,
        }


def filter_reason(f: Filter, r: ScoreResult, cfg: ScoringConfig, cat: SkillCatalog) -> str | None:
    """Причина, по которой кандидат скрыт фильтром (None — проходит)."""
    feat = r.features
    if f.code == "has_fsp":
        return None if feat.has_fsp else "Нет достижений ФСП"
    if f.code == "salary_fit":
        return None if r.u.salary_above_pct is None else "Ожидания выше вилки"
    if f.code == "format":
        return None if r.u.format_match else "Формат не подходит"
    if f.code == "city":
        return None if r.u.city_ok else "Другой город, без релокации"
    if f.code == "must_confirmed":
        miss = [ch.name for ch in r.skills if ch.kind == "must" and ch.state != "confirmed"]
        return None if not miss else "Не подтверждены обязательные: " + ", ".join(miss)
    if f.code == "skill":
        ids = f.value if isinstance(f.value, tuple) else (f.value,)
        bad = []
        for sid in ids:
            ch = next((c for c in r.skills if c.skill_id == sid), None)
            if ch is None or ch.state != "confirmed":
                bad.append(cat.name(sid))
        return None if not bad else "Не подтверждён навык " + ", ".join(bad)
    if f.code == "min_score":
        return None if round1(r.score) >= float(f.value) else f"Score ниже {float(f.value):g}"
    if f.code == "active":
        days = float(cfg.section("A")["active_days"])
        return None if any(t.days_ago <= days for t in feat.tasks) else f"Нет задач за {int(days)} дней"
    if f.code == "industry":
        codes = set(f.value if isinstance(f.value, tuple) else (f.value,))
        return None if codes & set(feat.industries) else "Другая отрасль интереса"
    if f.code == "grade":
        grades = set(f.value if isinstance(f.value, tuple) else (f.value,))
        return None if r.grade in grades else "Грейд не входит в фильтр"
    if f.code == "confirmed_only":
        return None if feat.confirmed else "Категория по опросу, тестом не подтверждена"
    if f.code == "exclude":
        ids = set(f.value if isinstance(f.value, tuple) else (f.value,))
        return "Скрыт вручную" if r.candidate_id in ids else None
    raise SelectionError(f"Неизвестный фильтр: {f.code}")


# --------------------------------------------------------------------------- строки и версии


@dataclass(frozen=True)
class SelectionItem:
    candidate_id: str
    spec: str
    grade: str
    category: str
    score: float
    score_display: float
    parts: Mapping[str, float]
    components: Mapping[str, float]
    contributions: Mapping[str, float]
    G: float
    Cov: float
    rank: int | None  # место в «Общем топе» среди видимых (None — скрыт или дубль Fullstack)
    category_rank: int | None  # место во вкладке категории среди видимых
    reasons: tuple[Reason, ...]
    why_you: tuple[str, ...]
    chips: tuple[SkillEvidence, ...]
    hidden_by: tuple[str, ...]
    hidden_reasons: tuple[str, ...]
    has_fsp: bool
    confirmed: bool
    flags: frozenset[str]

    @property
    def visible(self) -> bool:
        return not self.hidden_by

    @property
    def pros(self) -> tuple[Reason, ...]:
        return tuple(r for r in self.reasons if r.kind == "pro")

    @property
    def con(self) -> Reason | None:
        return next((r for r in self.reasons if r.kind == "con"), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "spec": self.spec,
            "grade": self.grade,
            "category": self.category,
            "score": self.score_display,
            "parts": dict(self.parts),
            # 2 знака: точный F (= m·VSS/100) — почти уникальный «отпечаток» истории ФСП, а протоколы публичны (§4.5).
            "components": {k: round(v, 2) for k, v in self.components.items()},
            "G": round(self.G, 4),
            "Cov": self.Cov,
            "rank": self.rank,
            "category_rank": self.category_rank,
            "reasons": [
                {
                    "kind": r.kind,
                    "code": r.code,
                    "component": r.component,
                    "text": r.text,
                    "evidence": [{"type": e.type, "id": e.id, "label": e.label} for e in r.evidence],
                }
                for r in self.reasons
            ],
            "why_you": list(self.why_you),
            "chips": [
                {
                    "skill_id": c.skill_id,
                    "name": c.name,
                    "kind": c.kind,
                    "state": c.state,
                    "source": c.source,
                    "label": c.label,
                }
                for c in self.chips
            ],
            "hidden_by": list(self.hidden_by),
            "hidden_reasons": list(self.hidden_reasons),
            "has_fsp": self.has_fsp,
            "confirmed": self.confirmed,
            "flags": sorted(self.flags),
        }


@dataclass(frozen=True)
class SelectionVersion:
    """Неизменяемая версия подборки."""

    need_id: str
    n: int
    parent_n: int | None
    brief: BriefSpec
    categories: tuple[tuple[str, str], ...]
    filters: tuple[Filter, ...]
    preset: str
    weights: Mapping[str, float]
    scoring_config_version: str
    created_at: str | None
    items: tuple[SelectionItem, ...]
    neighbors: tuple[NeighborDelta, ...]
    # Счётчики ДО обрезки списка до max_items (≤ 500 строк): иначе «Скрыто фильтрами (N)» занижалось бы,
    # ведь скрытые стоят в конце списка и первыми попадают под обрезку.
    total_n: int = 0  # строк в выбранных категориях (кандидат × категория)
    hidden_n: int = 0  # кандидатов, скрытых фильтрами во всех своих категориях
    hidden_counts: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))

    # ---- представления
    def visible(self) -> tuple[SelectionItem, ...]:
        return tuple(sorted((i for i in self.items if i.rank is not None), key=lambda i: i.rank))

    def overall_top(self, limit: int | None = None) -> tuple[SelectionItem, ...]:
        v = self.visible()
        return v if limit is None else v[:limit]

    def hidden(self) -> tuple[SelectionItem, ...]:
        """Группа «Скрыто фильтрами (N)» — по одному элементу на кандидата."""
        seen: set[str] = set()
        out = []
        for i in self.items:
            if i.hidden_by and i.candidate_id not in seen and not self._visible_elsewhere(i.candidate_id):
                seen.add(i.candidate_id)
                out.append(i)
        return tuple(out)

    def _visible_elsewhere(self, cid: str) -> bool:
        return any(i.candidate_id == cid and i.visible for i in self.items)

    def hidden_summary(self) -> dict[str, int]:
        """{код фильтра: сколько кандидатов он скрыл} — по всем кандидатам версии, а не только по ≤ 500 строкам."""
        return dict(self.hidden_counts)

    def by_category(self, limit: int | None = None) -> dict[tuple[str, str], tuple[SelectionItem, ...]]:
        """Вкладки «По категориям»: ≤ 50 на категорию (selection.max_per_category); limit=0 — без ограничения."""
        if limit is None:
            limit = int(ScoringConfig.load().section("selection")["max_per_category"])
        out: dict[tuple[str, str], list[SelectionItem]] = {c: [] for c in self.categories}
        for i in self.items:
            if i.category_rank is not None:
                out.setdefault((i.spec, i.grade), []).append(i)
        return {
            k: tuple(
                sorted(v, key=lambda i: i.category_rank)[:limit]
                if limit
                else sorted(v, key=lambda i: i.category_rank)
            )
            for k, v in out.items()
        }

    def item(self, candidate_id: str) -> SelectionItem | None:
        """Лучшая строка кандидата (видимая, иначе с наибольшим Score)."""
        rows = [i for i in self.items if i.candidate_id == candidate_id]
        if not rows:
            return None
        vis = [i for i in rows if i.rank is not None]
        return vis[0] if vis else rows[0]

    def neighbor(self, upper_rank: int) -> NeighborDelta | None:
        return next((d for d in self.neighbors if d.upper_rank == upper_rank), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "need_id": self.need_id,
            "n": self.n,
            "parent_n": self.parent_n,
            "brief": self.brief.to_dict(),
            "categories": [list(c) for c in self.categories],
            "filters": [f.to_dict() for f in self.filters],
            "preset": self.preset,
            "weights": dict(self.weights),
            "scoring_config_version": self.scoring_config_version,
            "created_at": self.created_at,
            "items": [i.to_dict() for i in self.items],
            "neighbors": [
                {
                    "upper_rank": d.upper_rank,
                    "lower_rank": d.lower_rank,
                    "delta": round1(d.delta),
                    "text": d.text,
                }
                for d in self.neighbors
            ],
            "hidden_summary": self.hidden_summary(),
            "hidden_n": self.hidden_n,
            "total_n": self.total_n,
        }


def page(items: Sequence[SelectionItem], page_no: int, size: int | None = None) -> tuple[SelectionItem, ...]:
    """Страница (с 1) по 10 строк."""
    size = size or int(ScoringConfig.load().section("selection")["page_size"])
    if page_no < 1:
        raise SelectionError("Номер страницы начинается с 1")
    return tuple(items[(page_no - 1) * size : page_no * size])


def _iso(dt: datetime | str | None) -> str | None:
    if dt is None or isinstance(dt, str):
        return dt
    return dt.isoformat()


def build_version(
    *,
    brief: BriefSpec,
    pool: Iterable[CandidateFeatures],
    n: int = 1,
    parent_n: int | None = None,
    categories: Sequence[tuple[str, str]] | None = None,
    filters: Sequence[Filter] = (),
    preset: str | None = None,
    created_at: datetime | str | None = None,
    category_stats: CategoryStats | None = None,
    cfg: ScoringConfig | None = None,
    catalog: SkillCatalog | None = None,
    keep: Iterable[str] = (),
) -> SelectionVersion:
    """Собрать версию: Score + объяснения по всем кандидатам выбранных категорий, фильтры → hidden_by, ранги.

    keep — id кандидатов (шортлист), чьи строки остаются в версии даже за пределами max_items: закреплённый
    кандидат, не прошедший фильтр, должен остаться с жёлтой меткой и причиной (концепция §3.2 шаг 6)."""
    cfg = cfg or ScoringConfig.load()
    cat = catalog or get_catalog()
    sc = cfg.section("selection")
    pool = list(pool)
    if categories is None:
        categories = sorted({(c.spec, c.grade) for c in pool})
    cats = tuple(dict.fromkeys(tuple(c) for c in categories))
    in_scope = [c for c in pool if (c.spec, c.grade) in cats]
    ctx = ScoringContext.build(pool, preset=preset, cfg=cfg, catalog=cat, category_stats=category_stats)
    results = [score_candidate(c, brief, ctx) for c in in_scope]
    # Медианы — по всей версии (до фильтров), чтобы «за» не прыгали при включении фильтра.
    medians = version_medians(results)
    filters = tuple(filters)
    hidden: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for r in results:
        hits = []
        for f in filters:
            why = filter_reason(f, r, cfg, cat)
            if why is not None:
                hits.append((f.code, why))
        hidden[(r.candidate_id, r.spec, r.grade)] = hits
    ordered = sorted(
        results, key=lambda r: (bool(hidden[(r.candidate_id, r.spec, r.grade)]),) + sort_key(r, cfg)
    )
    # «Скрыто фильтрами (N)» считаем до обрезки: по одному разу на кандидата, если он скрыт во всех категориях.
    visible_ids = {r.candidate_id for r in ordered if not hidden[(r.candidate_id, r.spec, r.grade)]}
    hidden_counts: dict[str, int] = {}
    hidden_seen: set[str] = set()
    for r in ordered:
        hits = hidden[(r.candidate_id, r.spec, r.grade)]
        if hits and r.candidate_id not in visible_ids and r.candidate_id not in hidden_seen:
            hidden_seen.add(r.candidate_id)
            for code, _ in hits:
                hidden_counts[code] = hidden_counts.get(code, 0) + 1
    total_n = len(ordered)
    max_items = int(sc["max_items"])
    if len(ordered) > max_items:
        keep_ids = set(keep)
        ordered = ordered[:max_items] + [r for r in ordered[max_items:] if r.candidate_id in keep_ids]
    expl: dict[tuple[str, str, str], Explanation] = {
        (r.candidate_id, r.spec, r.grade): explain(r, medians, brief=brief, cfg=cfg, catalog=cat)
        for r in ordered
    }
    overall_rank: dict[tuple[str, str, str], int] = {}
    cat_rank: dict[tuple[str, str, str], int] = {}
    per_cat: dict[tuple[str, str], int] = {}
    seen: set[str] = set()
    visible_results: list[ScoreResult] = []
    for r in ordered:
        key = (r.candidate_id, r.spec, r.grade)
        if hidden[key]:
            continue
        per_cat[(r.spec, r.grade)] = per_cat.get((r.spec, r.grade), 0) + 1
        cat_rank[key] = per_cat[(r.spec, r.grade)]
        if r.candidate_id not in seen:  # Fullstack виден в обеих категориях, в общем топе — один раз
            seen.add(r.candidate_id)
            overall_rank[key] = len(seen)
            visible_results.append(r)
    items = []
    for r in ordered:
        key = (r.candidate_id, r.spec, r.grade)
        e = expl[key]
        items.append(
            SelectionItem(
                candidate_id=r.candidate_id,
                spec=r.spec,
                grade=r.grade,
                category=cfg.category_name(r.spec, r.grade),
                score=r.score,
                score_display=r.score_display,
                parts=MappingProxyType(dict(e.parts)),
                components=MappingProxyType(dict(r.x)),
                contributions=MappingProxyType(dict(r.c)),
                G=r.G,
                Cov=r.Cov,
                rank=overall_rank.get(key),
                category_rank=cat_rank.get(key),
                reasons=e.reasons,
                why_you=e.why_you,
                chips=r.skills,
                hidden_by=tuple(code for code, _ in hidden[key]),
                hidden_reasons=tuple(why for _, why in hidden[key]),
                has_fsp=r.features.has_fsp,
                confirmed=r.confirmed,
                flags=r.flags,
            )
        )
    neighbors = tuple(
        neighbor_delta(visible_results[i], visible_results[i + 1], i + 1, i + 2, cfg)
        for i in range(len(visible_results) - 1)
    )
    return SelectionVersion(
        need_id=brief.need_id,
        n=n,
        parent_n=parent_n,
        brief=brief,
        categories=cats,
        filters=filters,
        preset=ctx.preset,
        weights=ctx.weights,
        scoring_config_version=cfg.version,
        created_at=_iso(created_at),
        items=tuple(items),
        neighbors=neighbors,
        total_n=total_n,
        hidden_n=len(hidden_seen),
        hidden_counts=MappingProxyType(dict(sorted(hidden_counts.items()))),
    )


def derive_version(
    parent: SelectionVersion,
    pool: Iterable[CandidateFeatures],
    *,
    n: int,
    brief: BriefSpec | None = None,
    categories: Sequence[tuple[str, str]] | None = None,
    filters: Sequence[Filter] | None = None,
    preset: str | None = None,
    created_at: datetime | str | None = None,
    **kw: Any,
) -> SelectionVersion:
    """Новая версия с параметрами родителя и указанными изменениями. Без изменений — «пересчитать на свежих данных»."""
    return build_version(
        brief=brief or parent.brief,
        pool=pool,
        n=n,
        parent_n=parent.n,
        categories=categories if categories is not None else parent.categories,
        filters=tuple(filters) if filters is not None else parent.filters,
        preset=preset or parent.preset,
        created_at=created_at,
        **kw,
    )


# --------------------------------------------------------------------------- diff


@dataclass(frozen=True)
class Badge:
    kind: str  # new | up | down | same | hidden
    delta: int = 0

    @property
    def label(self) -> str:
        return {"new": "новый", "up": f"▲{self.delta}", "down": f"▼{self.delta}", "hidden": "скрыт"}.get(
            self.kind, ""
        )


def diff_badges(prev: SelectionVersion | None, cur: SelectionVersion) -> dict[str, Badge]:
    """Бейджи `новый`, `▲3/▼2`, `скрыт` — по месту в «Общем топе» относительно родительской (или просмотренной) версии."""
    if prev is None:
        return {}
    before = {i.candidate_id: i.rank for i in prev.items if i.rank is not None}
    after = {i.candidate_id: i.rank for i in cur.items if i.rank is not None}
    out: dict[str, Badge] = {}
    for cid, rank in after.items():
        if cid not in before:
            out[cid] = Badge("new")
        elif before[cid] > rank:
            out[cid] = Badge("up", before[cid] - rank)
        elif before[cid] < rank:
            out[cid] = Badge("down", rank - before[cid])
        else:
            out[cid] = Badge("same")
    for cid in before:
        if cid not in after and any(i.candidate_id == cid for i in cur.items):
            out[cid] = Badge("hidden")
    return out


# --------------------------------------------------------------------------- история и откат


@dataclass(frozen=True)
class SelectionHistory:
    """Цепочка версий брифа v1 → v2 → v3 и указатель текущей. Откат — перенос указателя без пересчёта."""

    need_id: str
    versions: tuple[SelectionVersion, ...] = ()
    current_n: int | None = None

    @property
    def current(self) -> SelectionVersion | None:
        return None if self.current_n is None else self.get(self.current_n)

    def get(self, n: int) -> SelectionVersion:
        for v in self.versions:
            if v.n == n:
                return v
        raise SelectionError(f"Версии v{n} нет")

    def next_n(self) -> int:
        return max((v.n for v in self.versions), default=0) + 1

    def add(self, version: SelectionVersion) -> SelectionHistory:
        if version.need_id != self.need_id:
            raise SelectionError("Версия относится к другому брифу")
        if any(v.n == version.n for v in self.versions):
            raise SelectionError(f"Версия v{version.n} уже есть — версии неизменяемы")
        return replace(self, versions=self.versions + (version,), current_n=version.n)

    def rollback_targets(self) -> tuple[int, ...]:
        depth = int(ScoringConfig.load().section("selection")["rollback_depth"])
        return tuple(v.n for v in sorted(self.versions, key=lambda v: v.n)[-depth:])

    def rollback(self, n: int) -> SelectionHistory:
        if n not in self.rollback_targets():
            raise SelectionError("Откатиться можно на одну из последних 10 версий")
        return replace(self, current_n=n)

    def derive(self, pool: Iterable[CandidateFeatures], **changes: Any) -> SelectionHistory:
        """Уточнение от текущей версии: новая неизменяемая версия становится текущей."""
        cur = self.current
        if cur is None:
            raise SelectionError("Сначала соберите первую подборку")
        return self.add(derive_version(cur, pool, n=self.next_n(), **changes))


# --------------------------------------------------------------------------- шортлист


@dataclass(frozen=True)
class Pin:
    candidate_id: str
    pinned_at: str | None = None
    note: str = ""


@dataclass(frozen=True)
class Shortlist:
    """Шортлист брифа: живёт отдельно от версий, ≤ 30 кандидатов (spec-contact §2.1)."""

    need_id: str
    pins: tuple[Pin, ...] = ()

    def __contains__(self, candidate_id: object) -> bool:
        return any(p.candidate_id == candidate_id for p in self.pins)

    def pin(self, candidate_id: str, pinned_at: datetime | str | None = None, note: str = "") -> Shortlist:
        if candidate_id in self:
            return self
        limit = int(ScoringConfig.load().section("selection")["shortlist_max"])
        if len(self.pins) >= limit:
            raise SelectionError(f"В шортлисте не больше {limit} кандидатов")
        return replace(self, pins=self.pins + (Pin(candidate_id, _iso(pinned_at), note),))

    def unpin(self, candidate_id: str) -> Shortlist:
        return replace(self, pins=tuple(p for p in self.pins if p.candidate_id != candidate_id))


@dataclass(frozen=True)
class ShortlistEntry:
    pin: Pin
    mark: str  # ok | yellow
    reason: str | None
    item: SelectionItem | None

    @property
    def candidate_id(self) -> str:
        return self.pin.candidate_id


def shortlist_view(shortlist: Shortlist, version: SelectionVersion) -> tuple[ShortlistEntry, ...]:
    """Закреплённые всегда видны; не прошедший фильтры или выпавший из версии — с жёлтой меткой и причиной."""
    out = []
    for p in shortlist.pins:
        item = version.item(p.candidate_id)
        if item is None:
            out.append(ShortlistEntry(p, "yellow", "Нет в текущей версии подборки", None))
        elif item.rank is None:
            out.append(ShortlistEntry(p, "yellow", "; ".join(item.hidden_reasons) or "Скрыт фильтром", item))
        else:
            out.append(ShortlistEntry(p, "ok", None, item))
    return tuple(out)
