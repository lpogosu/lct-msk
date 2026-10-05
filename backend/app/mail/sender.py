"""Отправка писем: SMTP (stdlib smtplib, STARTTLS/SSL по настройкам) и консольный вариант.

В разработке SMTP смотрит в Mailpit (docker compose: smtp mailpit:1025, веб-интерфейс :8025).
Тесты подменяют отправителя на MemoryMailer (tests/conftest.py).
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import threading
from dataclasses import dataclass, field
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import Protocol

from app.core.settings import Settings
from app.mail.templates import RenderedMail

log = logging.getLogger("vzaimno.mail")


class MailError(Exception):
    """Письмо не отправлено (сеть, авторизация SMTP, отказ сервера)."""


@dataclass(frozen=True)
class OutgoingMail:
    to: str
    subject: str
    text: str
    html: str | None = None
    tags: dict[str, str] = field(default_factory=dict)  # служебные метки (например, purpose) — в заголовки не идут

    @classmethod
    def from_rendered(cls, to: str, rendered: RenderedMail, **tags: str) -> OutgoingMail:
        return cls(to=to, subject=rendered.subject, text=rendered.text, html=rendered.html, tags=dict(tags))


class Mailer(Protocol):
    def send(self, mail: OutgoingMail) -> None: ...


def build_message(mail: OutgoingMail, sender: str) -> EmailMessage:
    msg = EmailMessage()
    name, addr = parseaddr(sender)
    msg["From"] = str(Address(display_name=name, addr_spec=addr)) if name else addr
    msg["To"] = mail.to
    msg["Subject"] = mail.subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=addr.split("@")[-1] if "@" in addr else None)
    msg.set_content(mail.text, charset="utf-8")
    if mail.html:
        msg.add_alternative(mail.html, subtype="html", charset="utf-8")
    return msg


class SmtpMailer:
    """SMTP-отправитель. Соединение на каждое письмо: писем мало (коды, приглашения), так проще и надёжнее."""

    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def send(self, mail: OutgoingMail) -> None:
        s = self.s
        msg = build_message(mail, s.mail_from)
        try:
            if s.smtp_security == "ssl":
                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_s, context=ctx) as smtp:
                    self._login_and_send(smtp, msg)
            else:
                with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_s) as smtp:
                    if s.smtp_security == "starttls":
                        smtp.starttls(context=ssl.create_default_context())
                    self._login_and_send(smtp, msg)
        except (OSError, smtplib.SMTPException) as e:
            log.warning("SMTP: письмо на %s не отправлено: %s", mail.to, e)
            raise MailError(str(e)) from e

    def _login_and_send(self, smtp: smtplib.SMTP, msg: EmailMessage) -> None:
        if self.s.smtp_username:
            password = self.s.smtp_password.get_secret_value() if self.s.smtp_password else ""
            smtp.login(self.s.smtp_username, password)
        smtp.send_message(msg)


class ConsoleMailer:
    """Пишет письмо в лог (без SMTP). Код виден в логе бэкенда — только для локальной отладки."""

    def send(self, mail: OutgoingMail) -> None:
        log.info("Письмо для %s: %s\n%s", mail.to, mail.subject, mail.text)


class MemoryMailer:
    """Копит письма в памяти — для тестов и демо-сценариев."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.outbox: list[OutgoingMail] = []
        self.fail_next = False  # смоделировать сбой SMTP

    def send(self, mail: OutgoingMail) -> None:
        with self._lock:
            if self.fail_next:
                self.fail_next = False
                raise MailError("Смоделированный сбой SMTP")
            self.outbox.append(mail)

    def last_to(self, email: str) -> OutgoingMail | None:
        with self._lock:
            for m in reversed(self.outbox):
                if m.to == email:
                    return m
        return None

    def clear(self) -> None:
        with self._lock:
            self.outbox.clear()


def make_mailer(settings: Settings) -> Mailer:
    if settings.mail_backend == "console":
        return ConsoleMailer()
    return SmtpMailer(settings)
