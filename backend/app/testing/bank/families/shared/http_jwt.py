"""http.jwt.* — жизненный цикл JWT: exp и leeway, ротация refresh-токенов, выход при stateless access."""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.testing.bank.base import Instance, Rendered, register
from app.testing.bank.families.shared._common import SPECS, ChoiceFamily, build_choice, explain

_API_CALLS = (
    "GET /api/v1/orders", "GET /api/v1/profile", "POST /api/v1/cart/items", "GET /api/v1/notifications",
    "PATCH /api/v1/settings", "GET /api/v1/invoices",
)
_ACCESS_KINDS = ("old_access_valid", "old_access_leeway", "old_access_expired", "new_access_before_logout",
                 "new_access_after_logout")
_REFRESH_KINDS = ("old_refresh_reuse", "refresh_after_logout")
_TRAPS = ("old_access_valid", "old_access_leeway", "new_access_after_logout")

HINTS = {
    "refresh_revokes_access": "обновление токенов не отзывает прежний access-токен: stateless JWT действует "
    "до своего exp, сервер его не «помнит».",
    "logout_revokes_access": "logout удаляет refresh-токен в БД, но выданные access-токены проверяются без "
    "обращения к БД и работают до exp — поэтому их срок жизни делают коротким.",
    "no_leeway": "leeway расширяет окно проверки: токен ещё принимается, пока now < exp + leeway.",
    "rotation_ignored": "при ротации использованный refresh-токен становится недействительным — повторное "
    "предъявление отклоняется (и по OAuth Security BCP это признак кражи).",
    "logout_client_only": "logout — не только очистка на клиенте: сервер удалил refresh-токен, обновиться им "
    "больше нельзя.",
}
_MODES = tuple(HINTS)


_LABELS = "ABCDE"


def _hms(t: int) -> str:
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


@dataclass(frozen=True)
class _Timeline:
    t1: int  # обновление токенов
    t2: int  # logout
    exp1: int
    exp2: int
    leeway: int


def _accepted(t: int, kind: str, token: str, mode: str, ctx: _Timeline) -> bool:
    """Выполнит ли сервер запрос; mode — модель кандидата с заблуждением (или correct)."""
    if token.startswith("access"):
        exp = ctx.exp1 if token == "access₁" else ctx.exp2
        if mode == "refresh_revokes_access" and token == "access₁" and t > ctx.t1:
            return False
        if mode == "logout_revokes_access" and t > ctx.t2:
            return False
        limit = exp if mode == "no_leeway" else exp + ctx.leeway
        return t < limit  # RFC 7519 §4.1.4: принимаем строго до exp (+ leeway, как PyJWT)
    if kind == "old_refresh_reuse":
        return mode == "rotation_ignored"
    return mode == "logout_client_only"


def _answer(reqs: list[tuple[int, str, str]], mode: str, ctx: _Timeline) -> str:
    ok = [_LABELS[i] for i, (t, k, tok) in enumerate(reqs) if _accepted(t, k, tok, mode, ctx)]
    return ", ".join(ok) if ok else "ни один"


@register
class JwtLifecycle(ChoiceFamily):
    id = "http.jwt.lifecycle"
    title = "JWT: exp и leeway, ротация refresh, выход из системы"
    specs = (*SPECS, "qa")
    module = "http_api"
    format = "predict_output"
    target_grade = "senior"
    position = -0.5
    c = 0.25
    time_limit_s = 150

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        while True:
            ttl = rng.choice((300, 600, 900))
            leeway = rng.choice((15, 30, 60))
            t0 = rng.randint(9, 18) * 3600 + rng.randint(0, 59) * 60
            t1 = t0 + rng.randint(int(0.35 * ttl), int(0.7 * ttl))  # refresh: access₂ + refresh₂
            t2 = t1 + rng.randint(int(0.15 * ttl), int(0.35 * ttl))  # logout: refresh₂ удалён
            exp1, exp2 = t0 + ttl, t1 + ttl
            windows = {  # вид запроса → (токен, окно времени); границы отступают от exp/exp+leeway
                "old_access_valid": ("access₁", t1 + 5, exp1 - 5),
                "old_access_leeway": ("access₁", exp1 + 3, exp1 + leeway - 3),
                "old_access_expired": ("access₁", exp1 + leeway + 5, exp1 + leeway + int(0.3 * ttl)),
                "new_access_before_logout": ("access₂", t1 + 5, t2 - 5),
                "new_access_after_logout": ("access₂", t2 + 5, exp2 - 5),
                "old_refresh_reuse": ("refresh₁", t1 + 5, t2 + int(0.3 * ttl)),
                "refresh_after_logout": ("refresh₂", t2 + 5, t2 + int(0.5 * ttl)),
            }
            kinds = rng.sample(_ACCESS_KINDS, 3) + [rng.choice(_REFRESH_KINDS)]
            kinds.append(rng.choice([k for k in windows if k not in kinds]))
            if sum(k in _TRAPS for k in kinds) < 2:
                continue
            reqs = []
            for k in kinds:
                token, lo, hi = windows[k]
                reqs.append((rng.randint(lo, hi), k, token))
            reqs.sort()
            times = [t for t, _, _ in reqs]
            if len(set(times) | {t1, t2}) != len(times) + 2:
                continue
            ctx = _Timeline(t1=t1, t2=t2, exp1=exp1, exp2=exp2, leeway=leeway)
            key_text = _answer(reqs, "correct", ctx)
            alt = {m: _answer(reqs, m, ctx) for m in _MODES}
            if len({v for v in alt.values() if v != key_text}) >= 3:
                break

        modes = list(_MODES)
        rng.shuffle(modes)
        options, key, distractors = build_choice(rng, key_text, [(alt[m], m) for m in modes], 4)

        lines = [
            f"Access-токен живёт {ttl // 60} мин (exp = iat + {ttl} с), leeway = {leeway} с.",
            "",
            f"{_hms(t0)}  вход → выданы access₁ и refresh₁",
            f"{_hms(t1)}  POST /auth/refresh с refresh₁ → выданы access₂ и refresh₂",
            f"{_hms(t2)}  POST /auth/logout с refresh₂",
            "",
            "Запросы:",
        ]
        calls = rng.sample(_API_CALLS, len(reqs))
        for i, (t, _, token) in enumerate(reqs):
            if token.startswith("access"):
                lines.append(f"{_LABELS[i]}  {_hms(t)}  {calls[i]:<26} Authorization: Bearer {token}")
            else:
                lines.append(f"{_LABELS[i]}  {_hms(t)}  {'POST /auth/refresh':<26} refresh_token={token}")
        code = "\n".join(lines)
        prompt = (
            "Сервис авторизации выдаёт пару токенов. Access-токен — JWT; API проверяет его подпись, exp и nbf "
            "по RFC 7519 с допуском на рассинхрон часов (leeway) и при этом не обращается к базе данных. "
            "Refresh-токен хранится в БД; при каждом обновлении выдаётся новая пара, а использованный "
            "refresh-токен отзывается (ротация). Выход (logout) удаляет из БД текущий refresh-токен.\n\n"
            "Какие из запросов A–E сервер выполнит успешно?"
        )
        reasons = {
            "old_access_valid": "access₁ ещё не истёк — обновление токенов его не отзывает",
            "old_access_leeway": f"access₁ истёк в {_hms(exp1)}, но leeway {leeway} с ещё не вышел",
            "old_access_expired": f"access₁ истёк в {_hms(exp1)}, и leeway тоже вышел → 401",
            "new_access_before_logout": "access₂ действителен",
            "new_access_after_logout": f"access₂ действует до {_hms(exp2)}: logout удалил только refresh-токен, "
            "а access проверяется без БД",
            "old_refresh_reuse": "refresh₁ уже использован при ротации → отклонён",
            "refresh_after_logout": "refresh₂ удалён при logout → отклонён",
        }
        if any(t > t2 for t, k, _ in reqs if k == "old_access_valid"):
            reasons["old_access_valid"] = "access₁ ещё не истёк — ни обновление токенов, ни logout его не отзывают"
        body = "\n".join(f"{_LABELS[i]}: {reasons[k]}." for i, (_, k, _) in enumerate(reqs))
        body += f"\nУспешны: {key_text}."
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(prompt=prompt, code=code, language="text", options=options, answer_mode="choice"),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
