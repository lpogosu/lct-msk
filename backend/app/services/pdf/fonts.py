"""Шрифты PDF: Inter (текст) и JetBrains Mono (цифры, ЕКП, выписка) — OFL 1.1, лежат в app/assets/fonts
вместе с текстами лицензий (Inter-OFL.txt, JetBrainsMono-OFL.txt). Шрифты отдаём со своего сервера
(концепция §9.5) — в PDF они встраиваются подмножеством.

Если файлов нет (урезанный образ), берём DejaVu Sans из системы; если и его нет — Helvetica без
кириллицы, с предупреждением в лог.
TODO: при отсутствии шрифтов в образе кириллица в PDF не отрисуется — шрифты обязательны в app/assets.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

log = logging.getLogger("vzaimno.pdf")

FONTS_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"

_FILES = {
    "VZ-Text": "Inter-Regular.ttf",
    "VZ-TextSemi": "Inter-SemiBold.ttf",
    "VZ-TextBold": "Inter-Bold.ttf",
    "VZ-Mono": "JetBrainsMono-Regular.ttf",
    "VZ-MonoBold": "JetBrainsMono-Bold.ttf",
}
_DEJAVU = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("C:/Windows/Fonts/DejaVuSans.ttf"),
)


@dataclass(frozen=True)
class FontSet:
    text: str
    semi: str
    bold: str
    mono: str
    mono_bold: str
    embedded: bool  # True — Inter/JetBrains Mono из app/assets


_lock = threading.Lock()
_fonts: FontSet | None = None


def register_fonts() -> FontSet:
    """Зарегистрировать шрифты в reportlab (один раз на процесс)."""
    global _fonts
    with _lock:
        if _fonts is not None:
            return _fonts
        if all((FONTS_DIR / f).is_file() for f in _FILES.values()):
            for name, file in _FILES.items():
                pdfmetrics.registerFont(TTFont(name, str(FONTS_DIR / file)))
            _fonts = FontSet("VZ-Text", "VZ-TextSemi", "VZ-TextBold", "VZ-Mono", "VZ-MonoBold", True)
            return _fonts
        dejavu = next((p for p in _DEJAVU if p.is_file()), None)
        if dejavu is not None:
            log.warning("Шрифты Inter/JetBrains Mono не найдены в %s — PDF в DejaVu Sans", FONTS_DIR)
            pdfmetrics.registerFont(TTFont("VZ-Fallback", str(dejavu)))
            _fonts = FontSet(*(["VZ-Fallback"] * 5), embedded=False)
            return _fonts
        log.error("Нет ни Inter, ни DejaVu Sans: кириллица в PDF не отрисуется")
        _fonts = FontSet("Helvetica", "Helvetica-Bold", "Helvetica-Bold", "Courier", "Courier-Bold", False)
        return _fonts
