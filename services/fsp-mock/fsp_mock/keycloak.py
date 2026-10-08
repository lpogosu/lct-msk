"""Демо-пользователи ФСП ID и realm «fsp» для Keycloak, согласованные с демо-реестром.

`sub` в id_token Keycloak = id пользователя. При импорте realm id берётся из файла, поэтому у каждого
демо-пользователя id = sub спортсмена из реестра: войдя как fsp-demo-07, кандидат получает ровно
достижения спортсмена с этим sub. Отбор 30 пользователей покрывает все сценарии концепции §6.4.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .contract import (
    Level,
    RankCode,
    RankStatus,
    RegistrySnapshot,
    ResultStatus,
    RoleKind,
    Stage,
    StatusType,
)
from .generator import DATA_AS_OF, DISCIPLINES

REALM = "fsp"
# client id — как VZ_FSP_OIDC_CLIENT_ID по умолчанию в app/core/settings.py, .env.example и docker-compose.yml.
CLIENT_ID = "vzaimno"
CLIENT_SECRET = "vzaimno-demo-secret"  # только демо; в проде — из секрета окружения
# Куда Keycloak может вернуть код. Основной адрес — страница фронтенда /candidate/fsp/callback
# (VZ_FSP_OIDC_REDIRECT_URI по умолчанию): она вызывает GET /api/fsp/callback с code и state. Адрес бэкенда
# /api/fsp/callback тоже разрешён: при переходе браузера (а не запросе страницы) он перенаправляет на страницу
# фронтенда с теми же параметрами. Локально и на домене стенда; шаблонов (*) нет — open redirect не нужен.
_PUBLIC = "https://${VZ_PUBLIC_DOMAIN:vzaimno.example}"
REDIRECT_URIS = [
    "http://localhost/candidate/fsp/callback",  # за reverse-proxy compose на :80
    "http://localhost:5173/candidate/fsp/callback",
    "http://localhost:8000/api/fsp/callback",
    f"{_PUBLIC}/candidate/fsp/callback",
    f"{_PUBLIC}/api/fsp/callback",
]
DEMO_PASSWORD = "Demo-FSP-2026"
EMAIL_DOMAIN = "demo.example.org"  # зарезервированный домен (RFC 2606): письма никуда не уходят
N_DEMO_USERS = 30

_DISC_LABEL = {
    "algo": "алгоритмическое",
    "product": "продуктовое",
    "bas": "БАС",
    "robotics": "робототехника",
    "infosec": "ИБ",
}
_DISC_BY_CODE = {code: short for short, code in DISCIPLINES.items()}
_LEVEL_LABEL = {
    Level.international: "международные",
    Level.all_russian: "всероссийские",
    Level.interregional: "межрегиональные",
    Level.regional: "региональные",
    Level.municipal: "муниципальные",
}
_RANK_LABEL = {RankCode.MS: "МС", RankCode.KMS: "КМС", RankCode.I: "I р.", RankCode.II: "II р.", RankCode.III: "III р."}


@dataclass
class _Facts:
    sub: str
    age: int
    confirmed: list = field(default_factory=list)  # [(Result, Competition)]
    pending: list = field(default_factory=list)
    ranks: list = field(default_factory=list)
    roles: list = field(default_factory=list)

    def any_result(self, pred: Callable[[Any, Any], bool]) -> bool:
        return any(pred(r, c) for r, c in self.confirmed)


@dataclass(frozen=True)
class DemoUser:
    username: str
    sub: str
    first_name: str
    last_name: str
    middle_name: str | None
    email: str
    scenario: str


def _facts(snapshot: RegistrySnapshot) -> dict[str, _Facts]:
    comps = {c.ekp: c for c in snapshot.competitions}
    out: dict[str, _Facts] = {}
    for a in snapshot.athletes:
        b = a.birth_date
        age = DATA_AS_OF.year - b.year - ((DATA_AS_OF.month, DATA_AS_OF.day) < (b.month, b.day))
        out[a.sub] = _Facts(sub=a.sub, age=age)
    for r in snapshot.results:
        bucket = out[r.athlete_sub].confirmed if r.status == ResultStatus.confirmed else out[r.athlete_sub].pending
        bucket.append((r, comps[r.ekp]))
    for rank in snapshot.ranks:
        out[rank.athlete_sub].ranks.append(rank)
    for role in snapshot.roles:
        out[role.athlete_sub].roles.append(role)
    return out


def _is_ar_final(c: Any) -> bool:
    return c.level == Level.all_russian and c.stage != Stage.qualifier


# Сценарии в порядке приоритета: (имя, сколько нужно, предикат).
_COVERAGE: list[tuple[str, int, Callable[[_Facts], bool]]] = [
    ("ms", 1, lambda f: any(r.code == RankCode.MS for r in f.ranks)),
    ("champion_product", 1, lambda f: f.any_result(
        lambda r, c: r.place == 1 and c.status_type == StatusType.championship and _is_ar_final(c)
        and _DISC_BY_CODE.get(c.discipline_code) == "product")),
    ("kms", 1, lambda f: bool(f.confirmed) and any(r.code == RankCode.KMS for r in f.ranks)),
    ("cup_final_podium", 1, lambda f: f.any_result(
        lambda r, c: c.status_type == StatusType.cup and c.stage == Stage.final and (r.place_to or r.place or 99) <= 3)),
    ("international", 1, lambda f: f.any_result(lambda r, c: c.level == Level.international)),
    ("qualifier_and_final", 1, lambda f: f.any_result(lambda r, c: c.stage == Stage.qualifier)
     and f.any_result(lambda r, c: c.stage == Stage.final and c.status_type == StatusType.cup)),
    ("qualifier_only", 1, lambda f: bool(f.confirmed) and all(c.stage == Stage.qualifier for _, c in f.confirmed)),
    ("place_range", 1, lambda f: f.any_result(lambda r, c: r.place_to is not None)),
    ("confirmed_and_pending", 1, lambda f: bool(f.confirmed) and bool(f.pending)),
    ("awaiting_only", 2, lambda f: not f.confirmed and bool(f.pending) and not f.ranks),
    ("rank_only", 2, lambda f: not f.confirmed and not f.pending and bool(f.ranks)),
    ("no_results", 3, lambda f: not f.confirmed and not f.pending and not f.ranks and not f.roles),
    ("coach_no_results", 1, lambda f: not f.confirmed and any(r.kind == RoleKind.coach for r in f.roles)),
    ("judge", 2, lambda f: any(r.kind == RoleKind.judge for r in f.roles)),
    ("coach", 1, lambda f: bool(f.confirmed) and any(r.kind == RoleKind.coach for r in f.roles)),
    ("expired_rank", 1, lambda f: any(r.status == RankStatus.expired for r in f.ranks)),
    ("minor", 2, lambda f: f.age < 18 and bool(f.confirmed)),
    ("product_backend", 1, lambda f: f.any_result(lambda r, c: r.team_function == "backend")),
    ("product_frontend", 1, lambda f: f.any_result(lambda r, c: r.team_function == "frontend")),
    ("captain", 1, lambda f: f.any_result(lambda r, c: r.role.value == "captain")),
    ("algo_personal", 1, lambda f: f.any_result(
        lambda r, c: _DISC_BY_CODE.get(c.discipline_code) == "algo" and r.role.value == "personal")),
    ("bas", 1, lambda f: f.any_result(lambda r, c: _DISC_BY_CODE.get(c.discipline_code) == "bas")),
    ("robotics", 1, lambda f: f.any_result(lambda r, c: _DISC_BY_CODE.get(c.discipline_code) == "robotics")),
    ("infosec", 1, lambda f: f.any_result(lambda r, c: _DISC_BY_CODE.get(c.discipline_code) == "infosec")),
]


def _best(f: _Facts) -> tuple[Any, Any] | None:
    level_order = {Level.international: 4, Level.all_russian: 3, Level.interregional: 2, Level.regional: 1,
                   Level.municipal: 0}

    def key(rc: tuple[Any, Any]) -> tuple:
        r, c = rc
        return (-(level_order[c.level] - (c.stage == Stage.qualifier)), (r.place_to or r.place or 10**6) / c.field_size)

    return min(f.confirmed, key=key) if f.confirmed else None


def describe(f: _Facts) -> str:
    """Короткое описание сценария для таблицы соответствия в README."""
    parts: list[str] = []
    best = _best(f)
    if best is not None:
        r, c = best
        place = f"{r.place}–{r.place_to}" if r.place_to else str(r.place)
        stage = ", отбор" if c.stage == Stage.qualifier else ""
        parts.append(
            f"{place} из {r.field_size}, {_LEVEL_LABEL[c.level]}{stage}, "
            f"{_DISC_LABEL[_DISC_BY_CODE[c.discipline_code]]} {c.end_date.year}"
        )
        if len(f.confirmed) > 1:
            parts.append(f"всего результатов: {len(f.confirmed)}")
    elif f.pending:
        parts.append("только результаты, ждущие протокола")
    elif f.ranks:
        parts.append("только разряд, результатов в реестре нет")
    else:
        parts.append("в реестре нет результатов")
    if f.confirmed and f.pending:
        parts.append(f"ждёт протокола: {len(f.pending)}")
    for rank in sorted(f.ranks, key=lambda x: x.assigned_at):
        label = _RANK_LABEL.get(rank.code, rank.code.value)
        parts.append(f"{label} (истёк)" if rank.status == RankStatus.expired else label)
    for role in f.roles:
        parts.append(role.position or role.kind.value)
    if f.age < 18:
        parts.append(f"несовершеннолетний, {f.age} лет")
    functions = sorted({r.team_function for r, _ in f.confirmed if r.team_function})
    if functions:
        parts.append("роль " + ", ".join(functions))
    return "; ".join(parts)


def demo_users(snapshot: RegistrySnapshot, n: int = N_DEMO_USERS) -> list[DemoUser]:
    facts = _facts(snapshot)
    order = [a.sub for a in snapshot.athletes]
    chosen: list[str] = []
    for _, need, pred in _COVERAGE:
        got = sum(1 for s in chosen if pred(facts[s]))
        for sub in order:
            if got >= need or len(chosen) >= n:
                break
            if sub not in chosen and pred(facts[sub]):
                chosen.append(sub)
                got += 1
    for sub in order:  # добираем до n в порядке реестра
        if len(chosen) >= n:
            break
        if sub not in chosen:
            chosen.append(sub)
    athletes = {a.sub: a for a in snapshot.athletes}
    users = []
    for i, sub in enumerate(chosen, start=1):
        a = athletes[sub]
        username = f"fsp-demo-{i:02d}"
        users.append(
            DemoUser(
                username=username, sub=sub, first_name=a.first_name, last_name=a.last_name,
                middle_name=a.middle_name, email=f"{username}@{EMAIL_DOMAIN}", scenario=describe(facts[sub]),
            )
        )
    return users


def build_realm(snapshot: RegistrySnapshot, users: list[DemoUser] | None = None) -> dict[str, Any]:
    """Realm «fsp» для `kc.sh start-dev --import-realm`: конфиденциальный клиент платформы с PKCE S256
    и сервисным аккаунтом (client credentials к реестру), русская локаль, демо-пользователи с id = sub."""
    users = users if users is not None else demo_users(snapshot)
    return {
        "realm": REALM,
        "displayName": "ФСП ID (демо)",
        "displayNameHtml": "<b>ФСП ID</b> · демо-стенд",
        "enabled": True,
        "sslRequired": "external",
        "registrationAllowed": False,
        "loginWithEmailAllowed": True,
        "duplicateEmailsAllowed": False,
        "resetPasswordAllowed": False,
        "editUsernameAllowed": False,
        "rememberMe": False,
        "bruteForceProtected": True,
        "internationalizationEnabled": True,
        "supportedLocales": ["ru", "en"],
        "defaultLocale": "ru",
        "accessTokenLifespan": 300,
        "ssoSessionIdleTimeout": 1800,
        "ssoSessionMaxLifespan": 36000,
        "roles": {"realm": [{"name": "athlete", "description": "Спортсмен ФСП (демо)"}]},
        "clients": [
            {
                "clientId": CLIENT_ID,
                "name": "Взаимно — подбор ИТ-специалистов",
                "description": "Платформа «Взаимно»: вход через ФСП ID и чтение реестра достижений",
                "enabled": True,
                "protocol": "openid-connect",
                "publicClient": False,
                "clientAuthenticatorType": "client-secret",
                "secret": CLIENT_SECRET,
                "standardFlowEnabled": True,
                "implicitFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "serviceAccountsEnabled": True,
                "frontchannelLogout": True,
                "consentRequired": False,
                "fullScopeAllowed": True,
                "rootUrl": "http://localhost:8000",
                "baseUrl": "/",
                "redirectUris": REDIRECT_URIS,
                "webOrigins": ["+"],
                "attributes": {
                    "pkce.code.challenge.method": "S256",
                    "post.logout.redirect.uris": f"http://localhost:8000/*##http://localhost/*##http://localhost:5173/*##{_PUBLIC}/*",
                    "oauth2.device.authorization.grant.enabled": "false",
                    "backchannel.logout.session.required": "true",
                },
            }
        ],
        "users": [
            {
                "id": u.sub,
                "username": u.username,
                "enabled": True,
                "emailVerified": True,
                "firstName": u.first_name,
                "lastName": u.last_name,
                "email": u.email,
                "credentials": [{"type": "password", "value": DEMO_PASSWORD, "temporary": False}],
                "realmRoles": ["default-roles-fsp", "athlete"],
            }
            for u in users
        ],
    }


def mapping_table(users: list[DemoUser]) -> str:
    """Markdown-таблица «логин → sub → ФИО → сценарий» для deploy/keycloak/README.md."""
    rows = [
        "| Логин | sub (= id в реестре) | ФИО (вымышленные) | Что в реестре |",
        "|---|---|---|---|",
    ]
    for u in users:
        fio = " ".join(p for p in (u.last_name, u.first_name, u.middle_name) if p)
        rows.append(f"| `{u.username}` | `{u.sub}` | {fio} | {u.scenario} |")
    return "\n".join(rows)
