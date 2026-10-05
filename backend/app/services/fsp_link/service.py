"""Привязка ФСП ID и данные реестра у кандидата (концепция §3.1 шаг 9, §6.2, §6.4–6.5; DECISIONS п. 12).

Сценарии:
- **Привязка** (кандидат вошёл): ФСП ID → FspLink(fsp_id = sub, UNIQUE). Предпросмотр достижений идёт из
  реестра «на лету» и НЕ сохраняется, пока нет согласия fsp_enrichment. Кандидат может скрыть отдельные
  результаты (hidden_result_ids) ещё до согласия. Согласие → синхронизация: сырой ответ реестра в FspLink,
  нормализованные записи в fsp_achievements (контракт подбора: app/services/matching/sources.py).
- **Вход с лендинга**: ФСП ID уже привязан → обычная сессия; не привязан → новый аккаунт в pending_email
  без пароля и письмо с кодом (DECISIONS п. 12); пароль задаётся при подтверждении email.
  Email из ФСП ID не даёт права на чужой аккаунт: если такой email уже зарегистрирован — 409, войти по паролю
  и привязать ФСП ID в профиле.
- **Защита от «предзахвата» аккаунта.** Аккаунт, созданный входом через ФСП ID, ждёт подтверждения email;
  если владелец этого email зарегистрируется по паролю сам (регистрация перезаписывает пароль неподтверждённого
  аккаунта), привязка чужого ФСП ID недействительна: она удаляется при первом обращении, и вход через этот ФСП ID
  в аккаунт невозможен. Согласие на обогащение, отмеченное при таком входе, применяется только после
  подтверждения email (при следующем входе через ФСП ID или кнопкой на К14) — до этого данные реестра
  к неподтверждённому аккаунту не попадают.
- **«Демо-вход ФСП»** открывает только аккаунты, созданные входом через ФСП ID: аккаунт с паролем, к которому
  привязали демо-спортсмена, входит по паролю (иначе любой на демо-стенде вошёл бы в него по выбору sub).
- **Отвязка**: удаляем привязку и все данные ФСП, отзываем согласие fsp_enrichment (запись в журнале).
- **Отзыв согласия** fsp_enrichment из журнала: данные реестра удаляются, привязка остаётся.

ФСП не влияет на грейд и категорию; отсутствие ФСП — не штраф (F = 0). Скрытый результат не участвует в F.
"""

from __future__ import annotations

import hmac
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import auth_service
from app.core.access import ensure_role
from app.core.audit import audit
from app.core.auth_service import IssuedSession, RequestMeta, SendStatus
from app.core.clock import relative_ru
from app.core.consents import (
    active_consents,
    current_version,
    grant_consent,
    has_active_consent,
    on_consent_revoked,
    revoke_consent,
)
from app.core.domains import email_domain, normalize_email
from app.core.errors import ApiError, bad_request, conflict, forbidden, not_found, unauthorized
from app.core.settings import Settings
from app.fsp import vss
from app.fsp.models import Achievements, Athlete
from app.fsp.provider import (
    FallbackFSPProvider,
    FSPAuthError,
    FSPError,
    FSPProvider,
    FSPUnavailable,
    InProcessFSPProvider,
    build_provider,
    normalize_achievements,
)
from app.fsp.schema import FSPProfile, FSPState, NormalizedResult
from app.fsp.settings import FSPSettings
from app.mail.sender import Mailer
from app.models.candidate import CandidateProfile
from app.models.enums import AchievementStatus, ConsentPurpose, FspSyncStatus, UserRole, UserStatus
from app.models.fsp import FspAchievementCache, FspAuthState, FspLink
from app.models.users import Consent, User
from app.services.fsp_link.oidc import (
    OIDCClient,
    OIDCError,
    OIDCUnavailable,
    mint_demo_token,
    new_nonce,
    new_state,
    pkce_pair,
    sha256_hex,
    verify_demo_token,
)
from app.services.fsp_link.schemas import FspResultOut, FspStatusOut, FspStrengthOut

log = logging.getLogger("vzaimno.fsp.link")

# Привязка начатого входа к браузеру (защита от login CSRF и подмены привязки): state дублируется
# в httpOnly-cookie, callback принимается только из того же браузера.
FLOW_COOKIE = "vz_fsp_flow"
FLOW_COOKIE_PATH = "/api/fsp"

NEUTRAL_NOTE = (
    "Данные ФСП усиливают профиль, но не влияют на грейд и категорию. Без ФСП вы проходите тот же путь — "
    "это не штраф. Скрытый результат не участвует в оценке и не виден работодателям."
)
_SAFE_CLAIMS = ("iss", "sub", "aud", "azp", "auth_time", "preferred_username", "email_verified", "demo")
# Служебные пометки платформы в FspLink.claims (не из токена): откуда привязка и отложенное согласие
ORIGIN_SIGNUP = "fsp_signup"  # аккаунт создан входом через ФСП ID (DECISIONS п. 12)
_PLATFORM_MARKS = ("origin", "enrichment_requested")

Mode = Literal["link", "login"]


# --------------------------------------------------------------------------- зависимости процесса


@lru_cache(maxsize=1)
def get_fsp_settings() -> FSPSettings:
    return FSPSettings()


@lru_cache(maxsize=1)
def get_oidc_client() -> OIDCClient:
    return OIDCClient(get_fsp_settings())


class BreakerProvider:
    """Предохранитель для режима auto: если сеть реестра не ответила и сработал встроенный снимок, следующие
    `cooldown_s` секунд ходим сразу в снимок. Иначе «чёрная дыра» вместо отказа соединения задерживала бы
    каждую загрузку профиля на таймаут (build-reviews: fsp, «нет circuit breaker»)."""

    def __init__(self, inner: FallbackFSPProvider, cooldown_s: float = 60.0, monotonic: Any = time.monotonic) -> None:
        self.inner = inner
        self.cooldown_s = cooldown_s
        self._monotonic = monotonic
        self._open_until = 0.0

    def _call(self, name: str, arg: str) -> Any:
        if self._monotonic() < self._open_until:
            result = getattr(self.inner.fallback, name)(arg)
            if result is not None:
                return result
        result = getattr(self.inner, name)(arg)
        if self.inner.last_used == "fallback":
            self._open_until = self._monotonic() + self.cooldown_s
        return result

    def get_athlete(self, sub: str) -> Athlete | None:
        return self._call("get_athlete", sub)

    def get_achievements(self, sub: str) -> Achievements | None:
        return self._call("get_achievements", sub)

    def get_competition(self, ekp: str) -> Any:
        return self._call("get_competition", ekp)


@lru_cache(maxsize=1)
def get_provider() -> FSPProvider:
    """Реестр по настройкам VZ_FSP_PROVIDER (auto: сеть, при недоступности — встроенный демо-снимок)."""
    provider = build_provider(get_fsp_settings())
    if isinstance(provider, FallbackFSPProvider):
        return BreakerProvider(provider)
    return provider


@lru_cache(maxsize=1)
def demo_registry() -> InProcessFSPProvider:
    return InProcessFSPProvider.from_file(get_fsp_settings().snapshot_path)


def fsp_error(e: OIDCError) -> ApiError:
    if isinstance(e, OIDCUnavailable):
        s = get_fsp_settings()
        return ApiError(503, e.code, e.message, {"demo_login": s.demo_login_enabled})
    if e.code == "fsp_id_misconfigured":
        return ApiError(503, e.code, e.message, {"demo_login": get_fsp_settings().demo_login_enabled})
    return bad_request(e.code, e.message)


def _registry_error(e: FSPError) -> str:
    if isinstance(e, FSPUnavailable):
        return "Реестр ФСП сейчас недоступен — данные обновятся позже"
    if isinstance(e, FSPAuthError):
        return "Реестр ФСП отказал платформе в доступе — сообщите в поддержку"
    return "Реестр ФСП ответил с ошибкой — попробуйте обновить позже"


def _invalidate(db: Session, user_id: uuid.UUID) -> None:
    """Хук подбора «данные кандидата изменились» (модуль подбора может быть ещё не собран — не падаем)."""
    try:
        from app.services.matching import invalidate_features
    except Exception:  # noqa: BLE001
        return
    invalidate_features(db, user_id)


def _raw_link_of(db: Session, user_id: uuid.UUID) -> FspLink | None:
    return db.scalar(select(FspLink).where(FspLink.user_id == user_id))


def signup_overridden(db: Session, link: FspLink) -> bool:
    """Аккаунт создан входом через ФСП ID, но email затем зарегистрировали по паролю: регистрация перезаписывает
    пароль неподтверждённого аккаунта (app/core/auth_service.register) и пишет согласие на обработку ПДн
    с контекстом «registration». Значит, email подтвердил его владелец, а ФСП ID принадлежит тому, кто начал
    вход, — возможно, другому человеку. Такая привязка недействительна (предзахват аккаунта)."""
    if (link.claims or {}).get("origin") != ORIGIN_SIGNUP:
        return False
    row = db.scalar(
        select(Consent.id)
        .where(
            Consent.user_id == link.user_id,
            Consent.purpose == ConsentPurpose.processing.value,
            Consent.scope["context"].astext == "registration",
        )
        .limit(1)
    )
    return row is not None


def _drop_link(db: Session, link: FspLink, reason: str) -> None:
    """Удалить недействительную привязку со всеми данными ФСП и согласием на обогащение (запись в журнале)."""
    user_id, link_id = link.user_id, link.id
    db.execute(delete(FspAchievementCache).where(FspAchievementCache.user_id == user_id))
    db.delete(link)
    db.flush()
    consents: list[Consent] = active_consents(db, user_id, ConsentPurpose.fsp_enrichment)
    if consents:
        revoke_consent(db, consents[0])
    log.warning("Привязка ФСП ID у %s удалена: %s", user_id, reason)
    audit(db, "fsp.link_dropped", entity_type="fsp_link", entity_id=link_id, user_id=user_id, reason=reason)
    _invalidate(db, user_id)
    db.commit()


def _valid(db: Session, link: FspLink | None) -> FspLink | None:
    """Привязка, если она действительна; недействительная удаляется (см. signup_overridden)."""
    if link is None:
        return None
    owner = db.get(User, link.user_id)
    if owner is None:  # pragma: no cover — каскад удаляет привязку вместе с аккаунтом
        return None
    if owner.role != UserRole.candidate.value:
        _drop_link(db, link, "аккаунт больше не кандидат")
        return None
    if signup_overridden(db, link):
        _drop_link(db, link, "email аккаунта зарегистрирован заново по паролю")
        return None
    return link


def _link_of(db: Session, user_id: uuid.UUID) -> FspLink | None:
    return _valid(db, _raw_link_of(db, user_id))


def _link_by_sub(db: Session, sub: str) -> FspLink | None:
    return _valid(db, db.scalar(select(FspLink).where(FspLink.fsp_id == sub)))


def _safe_claims(claims: dict[str, Any], keep: dict[str, Any] | None = None) -> dict[str, Any]:
    """В привязке храним только служебные claims: имя и email из ФСП ID платформе для подбора не нужны.
    keep — прежние claims привязки: служебные пометки платформы (origin, отложенное согласие) сохраняются."""
    out = {k: claims[k] for k in _SAFE_CLAIMS if k in claims}
    out.update({k: v for k, v in (keep or {}).items() if k in _PLATFORM_MARKS})
    return out


def _set_mark(link: FspLink, key: str, value: Any | None) -> None:
    claims = dict(link.claims or {})
    if value is None:
        claims.pop(key, None)
    else:
        claims[key] = value
    link.claims = claims  # новый dict — иначе JSONB-изменение не попадёт в UPDATE


# --------------------------------------------------------------------------- начало входа


@dataclass(frozen=True)
class FlowStart:
    authorization_url: str
    state: str
    expires_in: int
    mode: Mode
    redirect_uri: str


def start_flow(
    db: Session,
    oidc: OIDCClient,
    *,
    mode: Mode,
    user: User | None,
    consent_personal_data: bool,
    consent_fsp_enrichment: bool,
    now: datetime,
) -> FlowStart:
    s = oidc.settings
    if mode == "link":
        if user is None:
            raise unauthorized("not_authenticated", "Чтобы привязать ФСП ID, войдите в аккаунт")
        ensure_role(user, UserRole.candidate.value)
    elif not consent_personal_data:
        raise bad_request(
            "consent_required",
            "Отметьте согласие на обработку персональных данных: без него нельзя создать аккаунт через ФСП ID",
        )
    verifier, challenge = pkce_pair()
    state, nonce = new_state(), new_nonce()
    try:
        url = oidc.authorization_url(state=state, nonce=nonce, code_challenge=challenge, redirect_uri=s.redirect_uri)
    except OIDCError as e:
        raise fsp_error(e) from e
    # Уборка: начатые и брошенные входы старше суток не нужны
    db.execute(delete(FspAuthState).where(FspAuthState.expires_at < now - timedelta(days=1)))
    db.add(
        FspAuthState(
            state_hash=sha256_hex(state),
            nonce=nonce,
            code_verifier=verifier,
            user_id=user.id if mode == "link" and user is not None else None,
            redirect_uri=s.redirect_uri,
            consent_personal_data=bool(consent_personal_data),
            consent_fsp_enrichment=bool(consent_fsp_enrichment),
            expires_at=now + timedelta(seconds=s.state_ttl_s),
        )
    )
    db.commit()
    return FlowStart(url, state, s.state_ttl_s, mode, s.redirect_uri)


def consume_state(
    db: Session, *, state: str, browser_state: str | None, current_user: User | None, now: datetime
) -> FspAuthState:
    """Одноразовый state: найден, не истёк, не использован, из того же браузера, тот же пользователь."""
    row = db.scalar(select(FspAuthState).where(FspAuthState.state_hash == sha256_hex(state)).with_for_update())
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise bad_request("fsp_state_invalid", "Вход через ФСП ID устарел или уже завершён. Начните заново")
    if not browser_state or not hmac.compare_digest(browser_state, state):
        raise bad_request(
            "fsp_state_mismatch", "Вход через ФСП ID начат в другом браузере или вкладке. Начните заново здесь"
        )
    if row.user_id is not None and (current_user is None or current_user.id != row.user_id):
        # Привязку завершает тот же аккаунт, что её начал: иначе чужой ФСП ID попал бы к атакующему
        raise forbidden("fsp_state_foreign", "Привязку начал другой аккаунт. Войдите в свой аккаунт и начните заново")
    row.used_at = now
    db.commit()  # state сгорает даже при последующей ошибке обмена кода
    return row


# --------------------------------------------------------------------------- синхронизация с реестром


def _status_of(r: NormalizedResult) -> str:
    return AchievementStatus.confirmed.value if r.confirmed else AchievementStatus.awaiting_protocol.value


def _write_cache(db: Session, user_id: uuid.UUID, link: FspLink, profile: FSPProfile, now: datetime) -> None:
    """Нормализованные записи в fsp_achievements (их читает подбор: kind + payload по контракту app/fsp)."""
    db.execute(delete(FspAchievementCache).where(FspAchievementCache.user_id == user_id))
    for r in profile.results:
        db.add(
            FspAchievementCache(
                user_id=user_id,
                fsp_id=link.fsp_id,
                external_id=r.result_id,
                kind="result",
                discipline=r.discipline,
                level=r.level.value,
                year=r.end_date.year,
                ekp_number=r.ekp[:16],
                place_from=r.place,
                place_to=r.place_to,
                field_size=r.field_size,
                role=r.role.value,
                status=_status_of(r),
                hidden=r.hidden,
                payload=r.model_dump(mode="json"),
                fetched_at=now,
            )
        )
    for rk in profile.ranks:
        db.add(
            FspAchievementCache(
                user_id=user_id,
                fsp_id=link.fsp_id,
                external_id=rk.id,
                kind="rank",
                year=rk.assigned_at.year,
                status=AchievementStatus.confirmed.value,
                payload=rk.model_dump(mode="json"),
                fetched_at=now,
            )
        )
    for ro in profile.roles:
        db.add(
            FspAchievementCache(
                user_id=user_id,
                fsp_id=link.fsp_id,
                external_id=ro.id,
                kind="role",
                role=ro.kind.value,
                status=AchievementStatus.confirmed.value,
                payload=ro.model_dump(mode="json"),
                fetched_at=now,
            )
        )


def _clear_registry_data(db: Session, link: FspLink) -> None:
    link.achievements = None
    link.athlete = None
    link.registry_as_of = None
    link.data_source = None
    link.fsp_state = None
    link.sync_status = FspSyncStatus.pending.value
    link.sync_error = None
    db.execute(delete(FspAchievementCache).where(FspAchievementCache.user_id == link.user_id))


def _apply_minor_policy(db: Session, user_id: uuid.UUID, athlete: Athlete | None) -> None:
    """Год рождения из реестра — только для политики по несовершеннолетним (никогда не показывается).
    Реестр важнее заявленного: иначе несовершеннолетний обошёл бы защиту, указав год рождения до привязки."""
    if athlete is None:
        return
    prof = db.get(CandidateProfile, user_id)
    if prof is not None:
        prof.birth_year = athlete.birth_date.year


def registry_birth_year(db: Session, user_id: uuid.UUID) -> int | None:
    """Год рождения из сохранённого ответа реестра (есть только при согласии на обогащение)."""
    link = _raw_link_of(db, user_id)
    if link is None or not link.athlete:
        return None
    try:
        return Athlete.model_validate(link.athlete).birth_date.year
    except ValueError:
        return None


def sync(db: Session, link: FspLink, provider: FSPProvider, now: datetime) -> FSPProfile:
    """Забрать достижения из реестра и сохранить (только при действующем согласии — проверяет вызывающий).

    Недоступность реестра не обнуляет прежние данные: статус error и текст причины, данные остаются.
    """
    hidden = set(link.hidden_result_ids or ())
    try:
        ach = provider.get_achievements(link.fsp_id)
        athlete = provider.get_athlete(link.fsp_id)
        if ach is not None and ach.athlete_sub != link.fsp_id:
            raise FSPError("Реестр ФСП вернул достижения другого спортсмена")
        if athlete is not None and athlete.sub != link.fsp_id:
            raise FSPError("Реестр ФСП вернул другого спортсмена")
    except FSPError as e:
        log.warning("Синхронизация ФСП для %s не удалась: %s", link.user_id, e)
        link.sync_status = FspSyncStatus.error.value
        link.sync_error = _registry_error(e)
        raise
    if ach is None:
        profile = FSPProfile(sub=link.fsp_id, state=FSPState.linked_no_results, athlete=athlete, fetched_at=now)
        link.achievements = None
        link.data_source = None
        link.registry_as_of = now
    else:
        profile = normalize_achievements(ach, hidden_result_ids=hidden, fetched_at=now, athlete=athlete)
        link.achievements = ach.model_dump(mode="json")
        link.data_source = ach.source.value
        link.registry_as_of = ach.as_of
    link.athlete = athlete.model_dump(mode="json") if athlete is not None else None
    link.fsp_state = profile.state.value
    link.last_sync_at = now
    link.sync_status = FspSyncStatus.ok.value
    link.sync_error = None
    _write_cache(db, link.user_id, link, profile, now)
    _apply_minor_policy(db, link.user_id, athlete)
    _invalidate(db, link.user_id)
    db.flush()
    return profile


def _try_sync(db: Session, link: FspLink, provider: FSPProvider, now: datetime) -> None:
    try:
        sync(db, link, provider, now)
    except FSPError:
        pass  # причина уже в link.sync_error; привязка и согласие остаются


def stored_profile(link: FspLink, now: datetime) -> FSPProfile | None:
    """Профиль из сохранённого ответа реестра (без сети) с учётом скрытых результатов."""
    if link.achievements is None:
        if link.last_sync_at is None:
            return None
        athlete = Athlete.model_validate(link.athlete) if link.athlete else None
        return FSPProfile(sub=link.fsp_id, state=FSPState.linked_no_results, athlete=athlete, fetched_at=link.last_sync_at)
    ach = Achievements.model_validate(link.achievements)
    athlete = Athlete.model_validate(link.athlete) if link.athlete else None
    return normalize_achievements(
        ach, hidden_result_ids=link.hidden_result_ids or (), fetched_at=link.last_sync_at or now, athlete=athlete
    )


# --------------------------------------------------------------------------- привязка и вход


def _grant_enrichment(db: Session, user_id: uuid.UUID, meta: RequestMeta, context: str) -> None:
    if not has_active_consent(db, user_id, ConsentPurpose.fsp_enrichment):
        grant_consent(
            db, user_id, ConsentPurpose.fsp_enrichment, ip=meta.ip, user_agent=meta.user_agent, scope={"context": context}
        )


def link_account(
    db: Session,
    user: User,
    claims: dict[str, Any],
    *,
    is_demo: bool,
    consent_fsp_enrichment: bool,
    provider: FSPProvider,
    meta: RequestMeta,
    now: datetime,
) -> FspLink:
    ensure_role(user, UserRole.candidate.value)
    sub = str(claims["sub"])
    taken = _link_by_sub(db, sub)
    if taken is not None and taken.user_id != user.id:
        raise conflict("fsp_id_taken", "Этот ФСП ID уже привязан к другому аккаунту «Взаимно»")
    link = _link_of(db, user.id)
    if link is not None and link.fsp_id != sub:
        raise conflict("fsp_already_linked", "К аккаунту уже привязан другой ФСП ID. Сначала отвяжите его")
    if link is None:
        link = FspLink(
            user_id=user.id,
            fsp_id=sub,
            claims=_safe_claims(claims),
            is_demo=is_demo,
            linked_at=now,
            sync_status=FspSyncStatus.pending.value,
            hidden_result_ids=[],
        )
        try:
            with db.begin_nested():
                db.add(link)
                db.flush()
        except IntegrityError:
            raise conflict("fsp_id_taken", "Этот ФСП ID уже привязан к другому аккаунту «Взаимно»") from None
        audit(db, "fsp.linked", actor_id=user.id, actor_role=user.role, entity_type="fsp_link", entity_id=link.id, ip=meta.ip, demo=is_demo)
    else:
        link.claims = _safe_claims(claims, keep=link.claims)
        link.is_demo = is_demo
    if consent_fsp_enrichment:
        _grant_enrichment(db, user.id, meta, "fsp_link")
        _set_mark(link, "enrichment_requested", None)
    if has_active_consent(db, user.id, ConsentPurpose.fsp_enrichment):
        _try_sync(db, link, provider, now)
    _invalidate(db, user.id)
    db.commit()
    return link


@dataclass
class LoginOutcome:
    kind: Literal["session", "verify_email"]
    user: User
    session: IssuedSession | None = None
    send: SendStatus | None = None


def login_with_fsp(
    db: Session,
    settings: Settings,
    mailer: Mailer,
    claims: dict[str, Any],
    *,
    is_demo: bool,
    consent_personal_data: bool,
    consent_fsp_enrichment: bool,
    provider: FSPProvider,
    meta: RequestMeta,
    now: datetime,
) -> LoginOutcome:
    sub = str(claims["sub"])
    link = _link_by_sub(db, sub)
    if link is not None:
        user = db.get(User, link.user_id)
        if user is None:  # pragma: no cover — каскад удаляет привязку вместе с аккаунтом
            raise not_found("user_not_found", "Аккаунт не найден")
        if user.status == UserStatus.blocked.value:
            raise forbidden("account_blocked", "Аккаунт заблокирован. Напишите в поддержку")
        if is_demo and (link.claims or {}).get("origin") != ORIGIN_SIGNUP:
            # Демо-токен выпускает сама платформа по выбранному sub: он не доказывает, что входит владелец
            # аккаунта с паролем. Такие аккаунты входят по паролю (или через настоящий ФСП ID).
            raise conflict(
                "fsp_demo_password_account",
                "Этот ФСП ID привязан к аккаунту с паролем. Войдите по email и паролю — демо-вход ФСП открывает "
                "только аккаунты, созданные через ФСП ID",
            )
        if user.is_active and user.email_verified:
            if consent_fsp_enrichment or (link.claims or {}).get("enrichment_requested"):
                # Отложенное согласие из первого входа применяется только к подтверждённому аккаунту
                _grant_enrichment(db, user.id, meta, "fsp_login")
                _set_mark(link, "enrichment_requested", None)
            if has_active_consent(db, user.id, ConsentPurpose.fsp_enrichment):
                _try_sync(db, link, provider, now)  # концепция §6.5: синхронизация при входе
            user.last_login_at = now
            audit(db, "fsp.login", actor_id=user.id, actor_role=user.role, ip=meta.ip, demo=is_demo)
            session = auth_service.start_session(db, settings, user, meta)
            return LoginOutcome("session", user, session=session)
        if consent_fsp_enrichment:
            _set_mark(link, "enrichment_requested", True)
        db.commit()
        return LoginOutcome("verify_email", user, send=_send_code(db, settings, mailer, user))

    if not consent_personal_data:
        raise bad_request(
            "consent_required",
            "Отметьте согласие на обработку персональных данных: без него нельзя создать аккаунт через ФСП ID",
        )
    raw_email = claims.get("email")
    if not raw_email:
        raise bad_request(
            "fsp_email_missing",
            "В профиле ФСП ID нет email. Зарегистрируйтесь по email и привяжите ФСП ID в профиле",
        )
    email = normalize_email(str(raw_email))
    existing = auth_service.get_user_by_email(db, email)
    if existing is not None:
        if existing.email_verified:
            raise conflict(
                "email_taken",
                "Аккаунт с этим email уже есть. Войдите по паролю и привяжите ФСП ID в разделе «ФСП»",
                {"email": email},
            )
        raise conflict(
            "email_pending",
            "Этот email ждёт подтверждения: введите код из письма или восстановите пароль, затем привяжите ФСП ID",
            {"email": email},
        )
    user = User(
        email=email,
        email_domain=email_domain(email),
        role=UserRole.candidate.value,
        status=UserStatus.pending_email.value,
        email_verified=False,
        password_hash=None,  # пароль задаётся при подтверждении email (DECISIONS п. 12)
    )
    try:
        with db.begin_nested():
            db.add(user)
            db.flush()
    except IntegrityError:
        raise conflict("email_taken", "Аккаунт с этим email уже есть. Войдите по паролю", {"email": email}) from None
    grant_consent(
        db,
        user.id,
        ConsentPurpose.processing,
        ip=meta.ip,
        user_agent=meta.user_agent,
        scope={"context": "fsp_id_registration"},
    )
    marks: dict[str, Any] = {"origin": ORIGIN_SIGNUP}
    if consent_fsp_enrichment:
        # Согласие на обогащение — после подтверждения email: до этого аккаунт может оказаться чужим
        # (email из ФСП ID не проверен платформой), и данные реестра к нему не привязываем.
        marks["enrichment_requested"] = True
    link = FspLink(
        user_id=user.id,
        fsp_id=sub,
        claims={**_safe_claims(claims), **marks},
        is_demo=is_demo,
        linked_at=now,
        sync_status=FspSyncStatus.pending.value,
        hidden_result_ids=[],
    )
    try:
        with db.begin_nested():
            db.add(link)
            db.flush()
    except IntegrityError:
        raise conflict("fsp_id_taken", "Этот ФСП ID уже привязан к другому аккаунту «Взаимно»") from None
    audit(db, "fsp.account_created", actor_id=user.id, actor_role=user.role, entity_type="user", entity_id=user.id, ip=meta.ip, demo=is_demo)
    db.commit()
    return LoginOutcome("verify_email", user, send=_send_code(db, settings, mailer, user, first=True))


def _send_code(db: Session, settings: Settings, mailer: Mailer, user: User, *, first: bool = False) -> SendStatus:
    """Код подтверждения. Повторный вход раньше минуты не шлёт письмо, а сообщает, когда можно."""
    try:
        return auth_service.issue_verification_code(db, user, settings, mailer, enforce_limits=not first)
    except ApiError as e:
        if e.status_code != 429:
            raise
        wait = int((e.details or {}).get("retry_after_s", 60)) if isinstance(e.details, dict) else 60
        return SendStatus(False, wait, 0)


# --------------------------------------------------------------------------- завершение входа


@dataclass
class CallbackResult:
    mode: Mode
    demo: bool
    link: FspLink | None = None
    login: LoginOutcome | None = None
    user: User | None = None


def finish(
    db: Session,
    *,
    mode: Mode,
    claims: dict[str, Any],
    demo: bool,
    current_user: User | None,
    consent_personal_data: bool,
    consent_fsp_enrichment: bool,
    settings: Settings,
    mailer: Mailer,
    provider: FSPProvider,
    meta: RequestMeta,
    now: datetime,
) -> CallbackResult:
    if mode == "link":
        if current_user is None:
            raise unauthorized("not_authenticated", "Чтобы привязать ФСП ID, войдите в аккаунт")
        link = link_account(
            db, current_user, claims, is_demo=demo, consent_fsp_enrichment=consent_fsp_enrichment,
            provider=provider, meta=meta, now=now,
        )
        return CallbackResult("link", demo, link=link, user=current_user)
    outcome = login_with_fsp(
        db, settings, mailer, claims, is_demo=demo, consent_personal_data=consent_personal_data,
        consent_fsp_enrichment=consent_fsp_enrichment, provider=provider, meta=meta, now=now,
    )
    return CallbackResult("login", demo, login=outcome, user=outcome.user)


def complete_callback(
    db: Session,
    oidc: OIDCClient,
    *,
    code: str,
    state: str,
    browser_state: str | None,
    current_user: User | None,
    settings: Settings,
    mailer: Mailer,
    provider: FSPProvider,
    meta: RequestMeta,
    now: datetime,
) -> CallbackResult:
    row = consume_state(db, state=state, browser_state=browser_state, current_user=current_user, now=now)
    try:
        tokens = oidc.exchange_code(code, code_verifier=row.code_verifier, redirect_uri=row.redirect_uri)
        claims = oidc.verify_id_token(str(tokens["id_token"]), nonce=row.nonce)
    except OIDCError as e:
        raise fsp_error(e) from e
    return finish(
        db,
        mode="link" if row.user_id is not None else "login",
        claims=claims,
        demo=False,
        current_user=current_user,
        consent_personal_data=row.consent_personal_data,
        consent_fsp_enrichment=row.consent_fsp_enrichment,
        settings=settings,
        mailer=mailer,
        provider=provider,
        meta=meta,
        now=now,
    )


def demo_login(
    db: Session,
    *,
    sub: str,
    mode: Mode,
    email: str | None,
    current_user: User | None,
    consent_personal_data: bool,
    consent_fsp_enrichment: bool,
    settings: Settings,
    mailer: Mailer,
    provider: FSPProvider,
    meta: RequestMeta,
    now: datetime,
) -> CallbackResult:
    """«Демо-вход ФСП»: подписанный мок-токен вместо Keycloak, дальше — тот же путь, что после OIDC."""
    fs = get_fsp_settings()
    if not demo_allowed(settings):
        raise forbidden("fsp_demo_disabled", "Демо-вход ФСП выключен на этом стенде")
    athlete = demo_registry().get_athlete(sub)
    if athlete is None:
        raise not_found("fsp_demo_unknown", "Такого спортсмена нет в демо-реестре ФСП")
    nonce = new_nonce()
    token = mint_demo_token(
        app_secret=settings.app_secret.get_secret_value(),
        client_id=fs.client_id,
        sub=athlete.sub,
        nonce=nonce,
        now=now,
        ttl_s=fs.demo_token_ttl_s,
        email=email or f"fsp-{athlete.sub[:8]}@{fs.demo_email_domain}",
        given_name=athlete.first_name,
        family_name=athlete.last_name,
    )
    try:
        claims = verify_demo_token(
            token, app_secret=settings.app_secret.get_secret_value(), client_id=fs.client_id, nonce=nonce, now=now
        )
    except OIDCError as e:  # pragma: no cover — свой же токен
        raise fsp_error(e) from e
    return finish(
        db,
        mode=mode,
        claims=claims,
        demo=True,
        current_user=current_user,
        consent_personal_data=consent_personal_data,
        consent_fsp_enrichment=consent_fsp_enrichment,
        settings=settings,
        mailer=mailer,
        provider=provider,
        meta=meta,
        now=now,
    )


def demo_allowed(settings: Settings) -> bool:
    """Демо-вход — только на демо-стенде: флаг VZ_FSP_DEMO_LOGIN_ENABLED и никогда в VZ_ENV=prod."""
    return get_fsp_settings().demo_login_enabled and settings.env != "prod"


def demo_athletes(now: datetime) -> list[dict[str, Any]]:
    """Демо-спортсмены встроенного реестра (вымышленные) — для кнопки «демо-вход ФСП»."""
    from app.fsp.provider import load_profile

    reg = demo_registry()
    out = []
    for a in reg.snapshot.athletes:
        prof = load_profile(reg, a.sub, now=now)
        initials = "".join(f"{p[0]}." for p in (a.first_name, a.middle_name) if p)
        out.append(
            {
                "sub": a.sub,
                "label": f"{a.last_name} {initials}",
                "summary": vss.employer_line(prof, now),
                "state": prof.state.value,
            }
        )
    order = {"linked": 0, "awaiting_protocol": 1, "linked_no_results": 2, "not_linked": 3}
    out.sort(key=lambda x: (order.get(x["state"], 9), x["label"]))
    return out


# --------------------------------------------------------------------------- К14: состояние, скрытие, отвязка


def _level_label(r: NormalizedResult) -> str:
    return {
        "international": "международные",
        "all_russian": "всероссийские",
        "interregional": "межрегиональные",
        "regional": "региональные",
        "municipal": "муниципальные",
    }.get(r.level.value, r.level.value)


def _place_text(r: NormalizedResult) -> str:
    if r.place is None:
        return f"участие без места, участников: {r.field_size}"
    if r.place_to is not None and r.place_to > r.place:
        return f"{r.place}–{r.place_to} из {r.field_size}"
    return f"{r.place} из {r.field_size}"


def _result_out(r: NormalizedResult, counted_ids: set[str]) -> FspResultOut:
    pub = vss.result_public_item(r)
    if r.hidden:
        label = "Скрыт"
    elif r.confirmed:
        label = "Подтверждён"
    else:
        label = "Ожидает протокол"
    return FspResultOut(
        id=r.result_id,
        title=r.title,
        discipline=vss.discipline_label(r.discipline),
        level=_level_label(r),
        year=r.end_date.year,
        place=_place_text(r),
        role=vss.role_text(r),
        outcome=pub.outcome,
        confirmed=r.confirmed,
        hidden=r.hidden,
        counted=r.result_id in counted_ids,
        employer_text=None if r.hidden or not r.confirmed else pub.text,
        status_label=label,
    )


def source_label(profile: FSPProfile) -> str | None:
    if profile.source is None:
        return None
    return {"fsp_api": "Реестр ФСП", "fsp_mock": "Демо-реестр ФСП", "diploma": "Загруженный диплом"}.get(
        profile.source.value, "Реестр ФСП"
    )


def status_view(db: Session, user: User, provider: FSPProvider, now: datetime) -> FspStatusOut:
    """Экран К14. Без согласия — предпросмотр из реестра «на лету» (ничего не сохраняется)."""
    link = _link_of(db, user.id)  # недействительная привязка удаляется здесь же (вместе с согласием)
    consent = has_active_consent(db, user.id, ConsentPurpose.fsp_enrichment)
    version = current_version(ConsentPurpose.fsp_enrichment)
    if link is None:
        return FspStatusOut(
            state="not_linked",
            status_line=vss.candidate_status_line(FSPProfile.not_linked()),
            linked=False,
            consent_fsp_enrichment=consent,
            consent_text_version=version,
            preview_only=False,
            employer_line=vss.NOT_LINKED_LINE,
            note=NEUTRAL_NOTE,
        )
    sync_error = link.sync_error
    requested = not consent and bool((link.claims or {}).get("enrichment_requested"))
    profile: FSPProfile | None
    preview = not consent
    if consent:
        profile = stored_profile(link, now)
    else:
        try:
            from app.fsp.provider import load_profile

            profile = load_profile(provider, link.fsp_id, now=now, hidden_result_ids=link.hidden_result_ids or ())
        except FSPError as e:
            profile, sync_error = None, _registry_error(e)
    if profile is None:
        return FspStatusOut(
            state="linked_no_results",
            status_line="ФСП ID привязан. Данные реестра ещё не получены" if consent else "ФСП ID привязан",
            linked=True,
            is_demo=link.is_demo,
            linked_ago=relative_ru(link.linked_at, now),
            consent_fsp_enrichment=consent,
            consent_text_version=version,
            enrichment_requested=requested,
            preview_only=preview,
            sync_status=link.sync_status,
            sync_error=sync_error,
            employer_line=vss.NOT_LINKED_LINE if not consent else vss.NO_RESULTS_LINE,
            note=NEUTRAL_NOTE,
        )
    counted = {r.result_id for r in profile.counted_results}
    strength = [
        FspStrengthOut(discipline=vss.discipline_label(d), strength=round(v, 1))
        for d, v in sorted(vss.vss_by_discipline(profile, now).items(), key=lambda kv: -kv[1])
    ]
    extract = vss.protocol_extract(profile, now) or None
    results = sorted(profile.results, key=lambda r: (-r.end_date.toordinal(), r.result_id))
    return FspStatusOut(
        state=profile.state.value,  # type: ignore[arg-type]
        status_line=vss.candidate_status_line(profile),
        linked=True,
        is_demo=link.is_demo,
        linked_ago=relative_ru(link.linked_at, now),
        consent_fsp_enrichment=consent,
        consent_text_version=version,
        enrichment_requested=requested,
        preview_only=preview,
        source_label=source_label(profile),
        data_ago=vss.fetched_ago(profile.fetched_at, now) if profile.fetched_at else None,
        sync_status=link.sync_status if consent else None,
        sync_error=sync_error,
        results=[_result_out(r, counted) for r in results],
        badges=vss.badges(profile, now),
        # Без согласия работодатель видит нейтральную строку: данные ФСП в подбор не идут
        employer_line=vss.employer_line(profile, now) if consent else vss.NOT_LINKED_LINE,
        strength=strength,
        extract=extract,
        pending_count=profile.pending_count,
        note=NEUTRAL_NOTE,
    )


def require_link(db: Session, user: User) -> FspLink:
    link = _link_of(db, user.id)
    if link is None:
        raise not_found("fsp_not_linked", "ФСП ID не привязан")
    return link


def set_hidden(
    db: Session, user: User, result_id: str, hidden: bool, provider: FSPProvider, meta: RequestMeta, now: datetime
) -> None:
    link = require_link(db, user)
    known: set[str] = set()
    stored = stored_profile(link, now)
    if stored is not None:
        known = {r.result_id for r in stored.results}
    if result_id not in known:
        try:
            from app.fsp.provider import load_profile

            live = load_profile(provider, link.fsp_id, now=now)
            known |= {r.result_id for r in live.results}
        except FSPError:
            pass
    if result_id not in known:
        raise not_found("fsp_result_not_found", "Такого результата нет в ваших данных ФСП")
    ids = set(link.hidden_result_ids or ())
    ids.add(result_id) if hidden else ids.discard(result_id)
    link.hidden_result_ids = sorted(ids)
    for row in db.scalars(
        select(FspAchievementCache).where(
            FspAchievementCache.user_id == user.id, FspAchievementCache.external_id == result_id
        )
    ):
        row.hidden = hidden
        payload = dict(row.payload or {})
        if "hidden" in payload:
            payload["hidden"] = hidden
            row.payload = payload
    audit(db, "fsp.result_hidden" if hidden else "fsp.result_shown", actor_id=user.id, actor_role=user.role, entity_type="fsp_result", entity_id=result_id, ip=meta.ip)
    _invalidate(db, user.id)
    db.commit()


def refresh(db: Session, user: User, provider: FSPProvider, now: datetime) -> None:
    link = require_link(db, user)
    if not has_active_consent(db, user.id, ConsentPurpose.fsp_enrichment):
        raise conflict(
            "fsp_consent_required",
            "Чтобы сохранять и обновлять данные ФСП, дайте согласие на обогащение профиля",
        )
    try:
        sync(db, link, provider, now)
    except FSPError as e:
        db.commit()  # статус ошибки синхронизации сохраняем
        raise ApiError(503, "fsp_registry_unavailable", _registry_error(e)) from e
    db.commit()


def grant_enrichment(
    db: Session, user: User, text_version: str | None, provider: FSPProvider, meta: RequestMeta, now: datetime
) -> None:
    ensure_role(user, UserRole.candidate.value)
    link = require_link(db, user)
    if not has_active_consent(db, user.id, ConsentPurpose.fsp_enrichment):
        grant_consent(
            db,
            user.id,
            ConsentPurpose.fsp_enrichment,
            text_version=text_version,
            ip=meta.ip,
            user_agent=meta.user_agent,
            scope={"context": "fsp_screen"},
        )
        audit(db, "consent.granted", actor_id=user.id, actor_role=user.role, entity_type="consent", ip=meta.ip, purpose="fsp_enrichment")
    _set_mark(link, "enrichment_requested", None)
    _try_sync(db, link, provider, now)
    db.commit()


def unlink(db: Session, user: User, meta: RequestMeta) -> None:
    """Отвязка: удаляем привязку и все данные ФСП, отзываем согласие (запись в журнале согласий)."""
    link = require_link(db, user)
    fsp_link_id = link.id
    db.execute(delete(FspAchievementCache).where(FspAchievementCache.user_id == user.id))
    db.delete(link)
    db.flush()
    consents: list[Consent] = active_consents(db, user.id, ConsentPurpose.fsp_enrichment)
    if consents:
        revoke_consent(db, consents[0])  # гасит все записи цели и вызывает хуки (подбор пересчитает F)
    audit(db, "fsp.unlinked", actor_id=user.id, actor_role=user.role, entity_type="fsp_link", entity_id=fsp_link_id, ip=meta.ip)
    _invalidate(db, user.id)
    db.commit()


@on_consent_revoked
def _on_enrichment_revoked(db: Session, consent: Consent) -> None:
    """Отзыв согласия на обогащение: данные реестра больше не храним и не используем (привязка остаётся)."""
    if consent.purpose != ConsentPurpose.fsp_enrichment.value:
        return
    link = _raw_link_of(db, consent.user_id)
    if link is not None:
        _clear_registry_data(db, link)
    else:
        db.execute(delete(FspAchievementCache).where(FspAchievementCache.user_id == consent.user_id))
    _invalidate(db, consent.user_id)
