"""Шаблоны писем (русский текст + HTML). Цвета — из брендбука ФСП (концепция §0)."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from urllib.parse import urlencode

# Брендбук ФСП: основной #402FFF, текст #1B1C21, фон #EDEDED, разделители #C8C9CA
_PRIMARY = "#402FFF"
_TEXT = "#1B1C21"
_BG = "#EDEDED"
_MUTED = "#6B6C70"


@dataclass(frozen=True)
class RenderedMail:
    subject: str
    text: str
    html: str


def _minutes_ru(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} минуту"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} минуты"
    return f"{n} минут"


def verification_link(public_base_url: str, email: str, code: str) -> str:
    """Ссылка из письма ведёт на экран О3 фронтенда, который сам вызывает /api/auth/verify-email
    (пароль из регистрации экран спрашивает, если не держит его в памяти)."""
    return f"{public_base_url.rstrip('/')}/verify-email?{urlencode({'email': email, 'code': code})}"


def password_reset_link(public_base_url: str, email: str, code: str) -> str:
    """Ссылка из письма восстановления ведёт на экран О4 «новый пароль» (/api/auth/password-reset/confirm)."""
    return f"{public_base_url.rstrip('/')}/reset-password?{urlencode({'email': email, 'code': code})}"


def render_verification_code(code: str, link: str, ttl_s: int, app_name: str = "Взаимно") -> RenderedMail:
    """Письмо с 6-значным кодом подтверждения email (концепция §3.1 шаг 2, экран О3)."""
    return _render_code_mail(
        code,
        link,
        ttl_s,
        app_name,
        subject=f"Код подтверждения: {code} — {app_name}",
        intro_text=f"Ваш код подтверждения для «{app_name}»: {code}",
        intro_html="Здравствуйте! Ваш код подтверждения email:",
        where="на странице регистрации",
        button="Подтвердить email",
        ignore_note="Если вы не регистрировались, просто проигнорируйте это письмо — аккаунт не будет активирован.",
    )


def render_password_reset_code(code: str, link: str, ttl_s: int, app_name: str = "Взаимно") -> RenderedMail:
    """Письмо с 6-значным кодом восстановления пароля (экран О4 «Вход и восстановление»)."""
    return _render_code_mail(
        code,
        link,
        ttl_s,
        app_name,
        subject=f"Код для восстановления пароля: {code} — {app_name}",
        intro_text=f"Код для восстановления пароля в «{app_name}»: {code}",
        intro_html="Здравствуйте! Код для восстановления пароля:",
        where="на странице восстановления пароля",
        button="Задать новый пароль",
        ignore_note=(
            "Если вы не запрашивали восстановление, просто проигнорируйте это письмо — пароль останется прежним."
        ),
    )


def _render_code_mail(
    code: str,
    link: str,
    ttl_s: int,
    app_name: str,
    *,
    subject: str,
    intro_text: str,
    intro_html: str,
    where: str,
    button: str,
    ignore_note: str,
) -> RenderedMail:
    ttl = _minutes_ru(max(1, ttl_s // 60))
    text = (
        "Здравствуйте!\n\n"
        f"{intro_text}\n\n"
        f"Код действует {ttl}. Введите его {where} или перейдите по ссылке:\n"
        f"{link}\n\n"
        f"{ignore_note}\n\n"
        f"— Команда «{app_name}»\n"
    )
    code_cells = "".join(
        f'<td style="padding:0 4px"><div style="width:40px;height:52px;line-height:52px;border:1px solid #C8C9CA;'
        f'border-radius:8px;font:600 28px \'JetBrains Mono\',Consolas,monospace;color:{_TEXT};text-align:center;'
        f'background:#FFFFFF">{escape(d)}</div></td>'
        for d in code
    )
    html = f"""<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(subject)}</title></head>
<body style="margin:0;padding:0;background:{_BG};font-family:Inter,Arial,sans-serif;color:{_TEXT}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};padding:24px 0">
<tr><td align="center">
<table role="presentation" width="480" cellpadding="0" cellspacing="0"
       style="max-width:480px;width:100%;background:#FFFFFF;border-radius:12px;padding:32px">
<tr><td style="font:700 20px Inter,Arial,sans-serif;color:{_PRIMARY};padding-bottom:16px">{escape(app_name)}</td></tr>
<tr><td style="font:400 16px/24px Inter,Arial,sans-serif;padding-bottom:16px">
{escape(intro_html)}</td></tr>
<tr><td align="center" style="padding:8px 0 20px">
<table role="presentation" cellpadding="0" cellspacing="0"><tr>{code_cells}</tr></table></td></tr>
<tr><td style="font:400 15px/22px Inter,Arial,sans-serif;padding-bottom:20px">
Код действует {escape(ttl)}. Введите его {escape(where)} или нажмите кнопку:</td></tr>
<tr><td align="center" style="padding-bottom:24px">
<a href="{escape(link, quote=True)}"
   style="display:inline-block;background:{_PRIMARY};color:#FFFFFF;text-decoration:none;
          font:600 16px Inter,Arial,sans-serif;padding:12px 24px;border-radius:8px">{escape(button)}</a></td></tr>
<tr><td style="font:400 13px/20px Inter,Arial,sans-serif;color:{_MUTED}">
{escape(ignore_note)}</td></tr>
</table>
</td></tr></table>
</body></html>"""
    return RenderedMail(subject=subject, text=text, html=html)


def guardian_confirm_link(public_base_url: str, token: str) -> str:
    """Ссылка из письма законному представителю ведёт на публичный экран фронтенда /guardian-confirm."""
    return f"{public_base_url.rstrip('/')}/guardian-confirm?{urlencode({'token': token})}"


def render_guardian_consent(
    guardian_name: str, candidate_hint: str, link: str, ttl_hours: int, app_name: str = "Взаимно"
) -> RenderedMail:
    """Письмо законному представителю кандидата 15–17 лет с одноразовой ссылкой подтверждения согласия."""
    days = max(1, ttl_hours // 24)
    subject = f"Согласие законного представителя — {app_name}"
    body = (
        f"Здравствуйте, {guardian_name}!\n\n"
        f"Пользователь {candidate_hint} указал вас законным представителем (родителем, опекуном) на платформе «{app_name}» — "
        "сервисе подбора ИТ-специалистов. Пользователю от 15 до 17 лет, поэтому до вашего согласия его профиль не виден работодателям.\n\n"
        "Что именно вы разрешаете: обработку персональных данных несовершеннолетнего в рамках платформы и показ его слепой карточки "
        "работодателям. Работодатели смогут предлагать ему только стажировку или лёгкий труд на неполный день. Контакты раскрываются "
        "работодателю только после взаимного согласия самого пользователя.\n\n"
        f"Чтобы подтвердить согласие, перейдите по ссылке (действует {days} дн.):\n{link}\n\n"
        "Если вы не знаете этого пользователя или не согласны, ничего не делайте — профиль останется скрытым.\n\n"
        f"— Команда «{app_name}»\n"
    )
    html = f"""<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(subject)}</title></head>
<body style="margin:0;padding:0;background:{_BG};font-family:Inter,Arial,sans-serif;color:{_TEXT}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};padding:24px 0">
<tr><td align="center">
<table role="presentation" width="480" cellpadding="0" cellspacing="0"
       style="max-width:480px;width:100%;background:#FFFFFF;border-radius:12px;padding:32px">
<tr><td style="font:700 20px Inter,Arial,sans-serif;color:{_PRIMARY};padding-bottom:16px">{escape(app_name)}</td></tr>
<tr><td style="font:400 16px/24px Inter,Arial,sans-serif;padding-bottom:12px">Здравствуйте, {escape(guardian_name)}!</td></tr>
<tr><td style="font:400 15px/22px Inter,Arial,sans-serif;padding-bottom:12px">
Пользователь {escape(candidate_hint)} указал вас законным представителем на платформе «{escape(app_name)}».
Ему от 15 до 17 лет, поэтому до вашего согласия его профиль не виден работодателям.</td></tr>
<tr><td style="font:400 15px/22px Inter,Arial,sans-serif;padding-bottom:20px">
Вы разрешаете обработку персональных данных несовершеннолетнего на платформе и показ его слепой карточки работодателям.
Предложить ему можно только стажировку или лёгкий труд на неполный день. Контакты раскрываются только после взаимного
согласия самого пользователя.</td></tr>
<tr><td align="center" style="padding-bottom:24px">
<a href="{escape(link, quote=True)}"
   style="display:inline-block;background:{_PRIMARY};color:#FFFFFF;text-decoration:none;
          font:600 16px Inter,Arial,sans-serif;padding:12px 24px;border-radius:8px">Подтвердить согласие</a></td></tr>
<tr><td style="font:400 13px/20px Inter,Arial,sans-serif;color:{_MUTED}">
Ссылка действует {days} дн. Если вы не знаете этого пользователя или не согласны, ничего не делайте — профиль останется скрытым.</td></tr>
</table>
</td></tr></table>
</body></html>"""
    return RenderedMail(subject=subject, text=body, html=html)
