"""Почта: SMTP-отправитель и русские шаблоны писем."""

from app.mail.sender import (
    ConsoleMailer,
    Mailer,
    MailError,
    MemoryMailer,
    OutgoingMail,
    SmtpMailer,
    make_mailer,
)
from app.mail.templates import (
    RenderedMail,
    password_reset_link,
    render_password_reset_code,
    render_verification_code,
    verification_link,
)

__all__ = [
    "ConsoleMailer",
    "MailError",
    "Mailer",
    "MemoryMailer",
    "OutgoingMail",
    "RenderedMail",
    "SmtpMailer",
    "make_mailer",
    "password_reset_link",
    "render_password_reset_code",
    "render_verification_code",
    "verification_link",
]
