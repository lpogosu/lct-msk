"""Отрисовка стандартизированного PDF-профиля (reportlab, BSD) в фирменном стиле ФСП (концепция §3.1.2, §9.5).

Цвета брендбука ФСП: #402FFF (акцент: категория, интервал на линейке, ✓-чипы), #1B1C21 (текст, пороги),
#EDEDED / #C8C9CA (фон блоков и серые «заявлено»). Красный #EC1D35 здесь не нужен — он только для
дедлайнов и «против». Текст — Inter, числа, код сверки и выписка ФСП — JetBrains Mono. Без градиентов,
теней и CAPS-подписей.

«Линейка» — фирменный элемент: шкала 300–600 с порогами Стажёр / Junior / Middle / Senior и полосой
80%-интервала кандидата. В полной версии — ещё точный балл, в слепой — только интервал.

QR рисуется векторными квадратами (чёткий при печати и на экране) и ведёт на страницу сверки /v/{код};
тот же адрес — кликабельная ссылка под QR.
"""

from __future__ import annotations

import io
from collections.abc import Sequence

import qrcode
from qrcode.constants import ERROR_CORRECT_M
from qrcode.util import MODE_8BIT_BYTE, QRData
from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from app.services.pdf.fonts import FontSet, register_fonts
from app.services.pdf.sheet import ProfileSheet, RulerData

VIOLET = HexColor("#402FFF")
INK = HexColor("#1B1C21")
BG = HexColor("#EDEDED")
LINE = HexColor("#C8C9CA")
MUTED = HexColor("#5E5F66")
VIOLET_SOFT = HexColor("#ECEAFF")

W, H = A4
M = 40.0
HEADER_H = 64.0
FOOTER_H = 104.0
QR_SIZE = 78.0

VARIANT_LABEL = {"blind": "Слепая версия", "full": "Полная версия"}


def qr_matrix(data: str) -> list[list[bool]]:
    """Матрица QR (без «тихой зоны»): байтовый режим, уровень коррекции M — читается и с печати."""
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, border=0, box_size=1)
    qr.add_data(QRData(data.encode("utf-8"), mode=MODE_8BIT_BYTE))
    qr.make(fit=True)
    return [list(row) for row in qr.get_matrix()]


def draw_qr(c: canvas.Canvas, data: str, x: float, y: float, size: float) -> None:
    """QR в квадрате (x, y) — левый нижний угол. Тёмные модули строки склеиваются в один прямоугольник."""
    matrix = qr_matrix(data)
    n = len(matrix)
    quiet = 2  # тихая зона 2 модуля — внутри квадрата, фон страницы белый
    module = size / (n + 2 * quiet)
    c.saveState()
    c.setFillColor(INK)
    for r, row in enumerate(matrix):
        col = 0
        while col < n:
            if not row[col]:
                col += 1
                continue
            start = col
            while col < n and row[col]:
                col += 1
            c.rect(
                x + (quiet + start) * module,
                y + size - (quiet + r + 1) * module,
                (col - start) * module,
                module,
                stroke=0,
                fill=1,
            )
    c.restoreState()


class _Pen:
    """Курсор потока: переносы строк, разрывы страниц, общий футер со сверкой."""

    def __init__(self, c: canvas.Canvas, sheet: ProfileSheet, fonts: FontSet, url: str, code: str) -> None:
        self.c = c
        self.s = sheet
        self.f = fonts
        self.url = url
        self.code = code
        self.page = 1
        self.x0 = M
        self.x1 = W - M
        self.y = 0.0
        self.y_min = 0.0
        self._start_page(first=True)

    # --- страницы ---

    def _start_page(self, *, first: bool) -> None:
        c, f = self.c, self.f
        c.setFillColor(INK)
        c.rect(0, H - HEADER_H, W, HEADER_H, stroke=0, fill=1)
        c.setFillColor(VIOLET)
        c.rect(0, H - HEADER_H - 3, W, 3, stroke=0, fill=1)
        c.setFillColor(white)
        c.setFont(f.bold, 17)
        c.drawString(M, H - 38, "Взаимно")
        c.setFont(f.text, 8.5)
        c.setFillColor(LINE)
        c.drawString(M, H - 52, "Найм по доказательствам. Контакт — по взаимному да")
        c.setFont(f.semi, 9.5)
        c.setFillColor(white)
        c.drawRightString(W - M, H - 34, f"Стандартизированный профиль · {VARIANT_LABEL[self.s.variant]}")
        c.setFont(f.mono, 8)
        c.setFillColor(LINE)
        c.drawRightString(W - M, H - 50, f"Выдан {self.s.issued_on.strftime('%d.%m.%Y')} · стр. {self.page}")
        self.y = H - HEADER_H - 30
        if first:
            self.y_min = M + FOOTER_H + 8
            self._footer_verify()
        else:
            self.y_min = M + 26
            self._footer_small()

    def new_page(self) -> None:
        self.c.showPage()
        self.page += 1
        self._start_page(first=False)

    def ensure(self, height: float) -> None:
        if self.y - height < self.y_min:
            self.new_page()

    def _footer_verify(self) -> None:
        c, f = self.c, self.f
        top = M + FOOTER_H
        c.setStrokeColor(LINE)
        c.setLineWidth(0.8)
        c.line(M, top, W - M, top)
        qx, qy = W - M - QR_SIZE, M + 4
        draw_qr(c, self.url, qx, qy, QR_SIZE)
        tx = M
        ty = top - 18
        c.setFillColor(INK)
        c.setFont(f.semi, 10)
        c.drawString(tx, ty, "Проверить подлинность профиля")
        c.setFont(f.mono, 8.5)
        c.setFillColor(VIOLET)
        c.drawString(tx, ty - 15, self.url)
        url_w = c.stringWidth(self.url, f.mono, 8.5)
        c.linkURL(self.url, (tx, ty - 18, tx + url_w, ty - 6), relative=0)
        c.setFillColor(INK)
        c.setFont(f.mono, 8)
        c.drawString(tx, ty - 30, f"Код сверки: {self.code}")
        if self.s.session_ref:
            c.drawString(tx, ty - 42, f"Сессия теста: {self.s.session_ref}")
        c.setFont(f.text, 7.2)
        c.setFillColor(MUTED)
        note = (
            "Категория подтверждена адаптивным тестом платформы «Взаимно»: у каждого кандидата свой вариант заданий. "
            "Заявленные навыки и опыт тестом не проверялись. Отсканируйте QR или откройте ссылку: изменённый документ "
            "сверку не пройдёт."
        )
        lines = simpleSplit(note, f.text, 7.2, W - 2 * M - QR_SIZE - 20)
        yy = ty - 58
        for ln in lines:
            c.drawString(tx, yy, ln)
            yy -= 9

    def _footer_small(self) -> None:
        c, f = self.c, self.f
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(M, M + 18, W - M, M + 18)
        c.setFont(f.mono, 7.5)
        c.setFillColor(MUTED)
        c.drawString(M, M + 6, f"Код сверки {self.code} · {self.url}")

    # --- примитивы ---

    def text(self, s: str, *, font: str | None = None, size: float = 9.5, color: Color = INK, leading: float | None = None, indent: float = 0.0) -> None:
        font = font or self.f.text
        leading = leading or size * 1.38
        width = self.x1 - self.x0 - indent
        for ln in simpleSplit(s, font, size, width) or [""]:
            self.ensure(leading)
            self.c.setFont(font, size)
            self.c.setFillColor(color)
            self.c.drawString(self.x0 + indent, self.y - size, ln)
            self.y -= leading

    def gap(self, h: float) -> None:
        self.y -= h

    def section(self, title: str) -> None:
        self.ensure(34)
        self.gap(10)
        c = self.c
        c.setFillColor(VIOLET)
        c.rect(self.x0, self.y - 9, 4, 9, stroke=0, fill=1)
        c.setFillColor(INK)
        c.setFont(self.f.semi, 11)
        c.drawString(self.x0 + 10, self.y - 9, title)
        self.y -= 18

    def chips(self, items: Sequence[str], *, style: str = "confirmed", size: float = 8.5) -> None:
        if not items:
            return
        c, f = self.c, self.f
        pad_x, h, gap = 6.0, 15.0, 5.0
        x = self.x0
        self.ensure(h + 4)
        for label in items:
            w = c.stringWidth(label, f.text, size) + 2 * pad_x
            if x + w > self.x1:
                x = self.x0
                self.y -= h + gap
                self.ensure(h + 4)
            y = self.y - h
            if style == "confirmed":
                c.setFillColor(VIOLET_SOFT)
                c.setStrokeColor(VIOLET)
                text_color = INK
            elif style == "weak":
                c.setFillColor(white)
                c.setStrokeColor(LINE)
                text_color = MUTED
            else:  # declared — серые «заявлено»
                c.setFillColor(BG)
                c.setStrokeColor(BG)
                text_color = MUTED
            c.setLineWidth(0.7)
            c.roundRect(x, y, w, h, 4, stroke=1, fill=1)
            c.setFillColor(text_color)
            c.setFont(f.text, size)
            c.drawString(x + pad_x, y + 4.3, label)
            x += w + gap
        self.y -= h + 6

    def mono_box(self, text: str, *, size: float = 7.6) -> None:
        c, f = self.c, self.f
        leading = size * 1.35
        lines: list[str] = []
        for raw in text.splitlines():
            lines.extend(simpleSplit(raw, f.mono, size, self.x1 - self.x0 - 16) or [""])
        i = 0
        while i < len(lines):
            self.ensure(leading * 2 + 10)
            room = int((self.y - self.y_min - 10) // leading)
            chunk = lines[i : i + max(1, room)]
            box_h = len(chunk) * leading + 10
            c.setFillColor(BG)
            c.roundRect(self.x0, self.y - box_h, self.x1 - self.x0, box_h, 5, stroke=0, fill=1)
            c.setFillColor(INK)
            c.setFont(f.mono, size)
            yy = self.y - 5 - size
            for ln in chunk:
                c.drawString(self.x0 + 8, yy, ln)
                yy -= leading
            self.y -= box_h + 4
            i += len(chunk)

    # --- линейка ---

    def ruler(self, r: RulerData) -> None:
        c, f = self.c, self.f
        self.ensure(62)
        x0, x1 = self.x0 + 8, self.x1 - 8
        span = max(1, r.hi - r.lo)

        def sx(score: float) -> float:
            return x0 + (min(max(score, r.lo), r.hi) - r.lo) / span * (x1 - x0)

        base = self.y - 26
        c.setStrokeColor(LINE)
        c.setLineWidth(1.2)
        c.line(x0, base, x1, base)
        if r.interval is not None:
            a, b = sx(r.interval[0]), sx(r.interval[1])
            c.setFillColor(VIOLET)
            c.roundRect(a, base - 3.5, max(b - a, 3), 7, 3, stroke=0, fill=1)
            c.setFont(f.mono, 7.5)
            c.setFillColor(VIOLET)
            label = f"80%: {r.interval[0]}–{r.interval[1]}"
            c.drawCentredString(min(max((a + b) / 2, x0 + 30), x1 - 30), base + 9, label)
        c.setStrokeColor(INK)
        c.setLineWidth(0.8)
        for cut in r.cuts:
            x = sx(cut.score)
            c.line(x, base - 6, x, base + 6)
            c.setFillColor(INK)
            c.setFont(f.mono, 7.5)
            c.drawCentredString(x, base - 16, str(cut.score))
            c.setFont(f.text, 7.5)
            c.setFillColor(MUTED)
            c.drawCentredString(x, base - 26, cut.name)
        if r.score is not None:
            x = sx(r.score)
            c.setFillColor(INK)
            c.circle(x, base, 3.2, stroke=0, fill=1)
            c.setFont(f.mono_bold, 9)
            c.drawCentredString(x, base + 19, str(r.score))
        self.y = base - 34


def render_pdf(sheet: ProfileSheet, url: str, code: str) -> bytes:
    """PDF-байты листа. invariant=1 — без случайного /ID и даты создания: один лист → одни байты."""
    fonts = register_fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1, pageCompression=1)
    c.setTitle(f"Профиль кандидата — {VARIANT_LABEL[sheet.variant].lower()}")
    c.setAuthor("Взаимно")
    c.setSubject(f"Код сверки {code}")
    c.setCreator("Взаимно — стандартизированный PDF-профиль")
    p = _Pen(c, sheet, fonts, url, code)

    # --- кто это
    p.text(sheet.title, font=fonts.bold, size=21, leading=26)
    if sheet.subtitle:
        p.text(sheet.subtitle, size=10.5, color=MUTED)
    if sheet.contacts:
        p.text("  ·  ".join(sheet.contacts), font=fonts.text, size=9.5)
    if sheet.location:
        p.text(sheet.location, size=9.5, color=MUTED)
    if sheet.variant == "blind":
        p.text(
            "Имя и контакты скрыты: кандидат раскрывает их компании только после взаимного «да».",
            size=8.5,
            color=MUTED,
        )

    # --- категория и линейка
    p.section("Категория и уровень")
    if sheet.category_name:
        p.text(sheet.category_name, font=fonts.bold, size=15, color=VIOLET, leading=19)
        if sheet.category_note:
            p.text(sheet.category_note, size=9)
        if sheet.borderline:
            p.text("Тест сдан на границе грейда.", size=8.5, color=MUTED)
        if sheet.ruler is not None:
            p.gap(4)
            p.ruler(sheet.ruler)
    else:
        p.text("Категория пока не подтверждена тестом.", size=9.5, color=MUTED)
    if sheet.modules:
        p.text("Модули теста: ✓✓ — сильный, ✓ — подтверждён, ~ — мало данных", size=8, color=MUTED)
        p.chips([f"{m.name} {m.symbol}".strip() for m in sheet.modules if m.state in ("strong", "confirmed")])
        weak = [f"{m.name} ~" for m in sheet.modules if m.state not in ("strong", "confirmed")]
        p.chips(weak, style="weak")
    if sheet.freshness:
        p.text(sheet.freshness, size=9)

    # --- ФСП
    p.section("Достижения ФСП")
    fsp = sheet.fsp
    if fsp.linked:
        src = " · ".join(x for x in (fsp.source_label, f"данные на {fsp.registry_on}" if fsp.registry_on else None) if x)
        if src:
            p.text(src, font=fonts.mono, size=8, color=MUTED)
        for ln in fsp.lines:
            p.text(f"— {ln}", size=9.5)
        if fsp.badges:
            p.chips(list(fsp.badges))
        if fsp.extract:
            p.gap(2)
            p.mono_box(fsp.extract)
    else:
        p.text(fsp.neutral or "Достижения ФСП не привязаны — оценка по тесту и задачам", size=9.5, color=MUTED)

    # --- задачи
    if sheet.tasks:
        p.section("Последние задачи")
        for t in sheet.tasks:
            parts = [f"«{t.title}»", t.result, t.when]
            p.text(" · ".join(x for x in parts if x), size=9.5)

    # --- заявлено
    p.section("Заявлено кандидатом")
    p.text("Не проверялось тестом и не влияет на категорию и место в подборке.", size=8, color=MUTED)
    if sheet.declared_skills:
        p.chips(list(sheet.declared_skills), style="declared")
    if sheet.roles:
        p.text("Роли: " + ", ".join(sheet.roles), size=9.5)
    if sheet.experience_label:
        p.text(f"Стаж: {sheet.experience_label}", size=9.5)
    for ln in sheet.experience:
        p.text(ln, size=9, indent=8)
    if sheet.education:
        p.text(f"Образование: {sheet.education}", size=9.5)
    if sheet.soft_skills:
        p.text("Личные качества: " + ", ".join(sheet.soft_skills), size=9.5)
    if not (sheet.declared_skills or sheet.roles or sheet.experience_label or sheet.experience or sheet.education or sheet.soft_skills):
        p.text("Кандидат пока ничего не заявил о себе.", size=9, color=MUTED)

    c.showPage()
    c.save()
    return buf.getvalue()
