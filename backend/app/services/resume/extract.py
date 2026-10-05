"""Извлечение текста из PDF (pdfplumber, MIT). Скан без текстового слоя → понятная ошибка, а не пустой профиль."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import pdfplumber
from pdfminer.pdfparser import PDFSyntaxError

log = logging.getLogger("vzaimno.resume")

PAGE_SEPARATOR = "\n\n"


class ResumeError(Exception):
    """Ошибка разбора резюме с машинным кодом и русским сообщением для человека."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ExtractedText:
    text: str
    pages: int
    page_starts: tuple[int, ...]  # смещение начала каждой страницы в text

    def page_of(self, pos: int) -> int:
        page = 1
        for i, start in enumerate(self.page_starts):
            if pos >= start:
                page = i + 1
        return page


def looks_like_pdf(data: bytes) -> bool:
    # Заголовок %PDF- может стоять не в самом начале (до 1 КБ мусора допускается спецификацией)
    return b"%PDF-" in data[:1024]


def extract_text(data: bytes, *, max_pages: int, min_text_chars: int) -> ExtractedText:
    if not looks_like_pdf(data):
        raise ResumeError("resume_not_pdf", "Это не PDF. Загрузите резюме в формате PDF")
    parts: list[str] = []
    starts: list[int] = []
    pos = 0
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if getattr(pdf.doc, "is_extractable", True) is False:
                raise ResumeError("resume_protected", "PDF защищён от копирования текста. Снимите защиту или заполните профиль вручную")
            for page in pdf.pages[:max_pages]:
                txt = page.extract_text(x_tolerance=1.5, y_tolerance=3) or ""
                txt = txt.replace("\r\n", "\n").replace("\r", "\n").replace("­", "")
                starts.append(pos)
                parts.append(txt)
                pos += len(txt) + len(PAGE_SEPARATOR)
            n_pages = len(pdf.pages)
    except ResumeError:
        raise
    except (PDFSyntaxError, ValueError, KeyError, TypeError, AttributeError) as e:
        log.info("Битый PDF резюме: %s", e)
        raise ResumeError("resume_broken", "Не удалось открыть PDF: файл повреждён. Сохраните резюме заново и загрузите ещё раз") from e
    except Exception as e:  # pdfminer бросает много собственных исключений
        log.info("PDF резюме не читается: %s", e)
        raise ResumeError("resume_broken", "Не удалось открыть PDF: файл повреждён. Сохраните резюме заново и загрузите ещё раз") from e
    text = PAGE_SEPARATOR.join(parts)
    if sum(1 for ch in text if ch.isalnum()) < min_text_chars:
        raise ResumeError(
            "resume_no_text",
            "Не удалось распознать текст: похоже, это скан или картинка. Загрузите PDF с текстовым слоем "
            "(«Сохранить как PDF» из редактора или с hh.ru) или заполните профиль вручную",
        )
    return ExtractedText(text=text, pages=n_pages, page_starts=tuple(starts))
