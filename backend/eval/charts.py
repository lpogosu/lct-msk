"""Простые SVG-графики для docs/VALIDATION.md (без matplotlib).

Палитра — проверенная категориальная (6 слотов, порядок фиксирован, светлая и тёмная тема через
prefers-color-scheme). Цвет несёт только идентичность серии; подписи — цветом текста. Рядом с каждым
графиком в отчёте есть таблица с теми же числами (доступность и контраст светлых слотов).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from html import escape

W, H = 720, 340
PAD_L, PAD_R, PAD_T, PAD_B = 64, 24, 64, 48
SLOTS_LIGHT = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300")
SLOTS_DARK = ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300")
MUTED = "#898781"


def _style() -> str:
    light = "".join(f"--s{i + 1}:{c};" for i, c in enumerate(SLOTS_LIGHT))
    dark = "".join(f"--s{i + 1}:{c};" for i, c in enumerate(SLOTS_DARK))
    return (
        "<style>"
        f"svg{{{light}--bg:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;}}"
        f"@media (prefers-color-scheme: dark){{svg{{{dark}--bg:#1a1a19;--ink:#ffffff;--ink2:#c3c2b7;"
        "--grid:#2c2c2a;--axis:#383835;}}}}"
        'text{font-family:"Segoe UI",system-ui,-apple-system,Roboto,Arial,sans-serif;fill:var(--ink2);font-size:12px}'
        ".t{fill:var(--ink);font-size:14px;font-weight:600}.ax{stroke:var(--axis);stroke-width:1}"
        ".g{stroke:var(--grid);stroke-width:1}.thr{stroke:var(--muted);stroke-width:1.5;stroke-dasharray:5 4}"
        ".tick{font-variant-numeric:tabular-nums}"
        "</style>"
    )


def _fmt(x: float) -> str:
    s = f"{x:.2f}".rstrip("0").rstrip(".") if abs(x) < 10 else f"{x:.0f}"
    return s.replace(".", ",")


LEGEND_ROW = 18
CHAR_W = 7.8  # оценка ширины символа 12px (кириллица) для переноса легенды


def _frame(title: str, body: str, desc: str, height: int = H) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" width="{W}" height="{height}" role="img" '
        f'font-family="Segoe UI, Arial, sans-serif" aria-label="{escape(title)}">{_style()}<title>{escape(title)}</title>'
        f'<desc>{escape(desc)}</desc><rect width="{W}" height="{height}" rx="8" fill="var(--bg)"/>'
        f'<text class="t" x="{PAD_L}" y="24">{escape(title)}</text>{body}</svg>'
    )


def _legend(labels: Sequence[str], y: int = 44) -> tuple[str, int]:
    """Легенда с переносом строк; возвращает (svg, число строк)."""
    out, x, row = [], PAD_L, 0
    for i, lab in enumerate(labels):
        w = 40 + int(len(lab) * CHAR_W)
        if x > PAD_L and x + w > W - PAD_R:
            x, row = PAD_L, row + 1
        yy = y + row * LEGEND_ROW
        c = f"var(--s{i % 6 + 1})"
        out.append(f'<line x1="{x}" y1="{yy - 4}" x2="{x + 18}" y2="{yy - 4}" stroke="{c}" stroke-width="2"/>'
                   f'<circle cx="{x + 9}" cy="{yy - 4}" r="4" fill="{c}" stroke="var(--bg)" stroke-width="2"/>'
                   f'<text x="{x + 24}" y="{yy}">{escape(lab)}</text>')
        x += w
    return "".join(out), row + 1


def _with_legend(title: str, plot: str, labels: Sequence[str], desc: str) -> str:
    """Легенда сверху; область графика сдвигается вниз на лишние строки легенды. Одной серии легенда не нужна."""
    if len(labels) <= 1:
        return _frame(title, plot, desc, H)
    legend, rows = _legend(labels)
    dy = LEGEND_ROW * (rows - 1)
    return _frame(title, legend + f'<g transform="translate(0,{dy})">{plot}</g>', desc, H + dy)


def nice_top(y_max: float, ticks: int = 5) -> tuple[float, int]:
    """Верх оси и число делений с «круглым» шагом 1, 2, 2,5 или 5 · 10^k."""
    if y_max <= 0:
        return 1.0, ticks
    raw = y_max / ticks
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw - 1e-12)
    n = max(1, math.ceil(y_max / step - 1e-9))
    return step * n, n


def _y_axis(y_min: float, y_max: float, ticks: int, label: str) -> tuple[str, Callable[[float], float]]:
    if y_min == 0:
        y_max, ticks = nice_top(y_max, ticks)
    ph = H - PAD_T - PAD_B

    def sy(v: float) -> float:
        return PAD_T + ph * (1 - (v - y_min) / ((y_max - y_min) or 1.0))

    parts = []
    for k in range(ticks + 1):
        v = y_min + (y_max - y_min) * k / ticks
        y = sy(v)
        parts.append(f'<line class="g" x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}"/>'
                     f'<text class="tick" x="{PAD_L - 8}" y="{y + 4:.1f}" text-anchor="end">{_fmt(v)}</text>')
    parts.append(f'<text x="14" y="{PAD_T + ph / 2:.0f}" transform="rotate(-90 14 {PAD_T + ph / 2:.0f})" '
                 f'text-anchor="middle">{escape(label)}</text>')
    return "".join(parts), sy


def line_chart(title: str, x_labels: Sequence[str], series: Sequence[tuple[str, Sequence[float | None]]], *,
               y_label: str, x_title: str, y_max: float = 1.0, y_min: float = 0.0,
               threshold: tuple[float, str] | None = None) -> str:
    """Линии по категориальной оси X (например, размер коалиции)."""
    grid, sy = _y_axis(y_min, y_max, 5, y_label)
    pw = W - PAD_L - PAD_R
    n = len(x_labels)

    def sx(i: int) -> float:
        return PAD_L + pw * (i + 0.5) / n

    body = [grid, f'<line class="ax" x1="{PAD_L}" y1="{H - PAD_B}" x2="{W - PAD_R}" y2="{H - PAD_B}"/>']
    for i, lab in enumerate(x_labels):
        body.append(f'<text x="{sx(i):.1f}" y="{H - PAD_B + 18}" text-anchor="middle">{escape(lab)}</text>')
    body.append(f'<text x="{PAD_L + pw / 2:.0f}" y="{H - 8}" text-anchor="middle">{escape(x_title)}</text>')
    if threshold is not None:
        y = sy(threshold[0])
        body.append(f'<line class="thr" x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}"/>'
                    f'<text x="{W - PAD_R}" y="{y - 6:.1f}" text-anchor="end">{escape(threshold[1])}</text>')
    for si, (_, ys) in enumerate(series):
        c = f"var(--s{si % 6 + 1})"
        pts = [(sx(i), sy(v)) for i, v in enumerate(ys) if v is not None]
        if len(pts) > 1:
            d = " ".join(f"{'M' if k == 0 else 'L'}{x:.1f},{y:.1f}" for k, (x, y) in enumerate(pts))
            body.append(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="2" stroke-linejoin="round"/>')
        for x, y in pts:
            body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{c}" stroke="var(--bg)" stroke-width="2"/>')
    desc = "; ".join(f"{lab}: " + ", ".join("—" if v is None else _fmt(v) for v in ys) for lab, ys in series)
    return _with_legend(title, "".join(body), [s[0] for s in series], desc)


def path_chart(title: str, series: Sequence[tuple[str, Sequence[float]]], *, y_label: str, x_title: str,
               threshold: tuple[float, str] | None = None, marker_x: tuple[int, str] | None = None) -> str:
    """Траектории (например, статистика CUSUM по номеру ответа)."""
    n = max((len(s) for _, s in series), default=1)
    y_max = max([max(s, default=0.0) for _, s in series] + [threshold[0] * 1.2 if threshold else 1.0])
    grid, sy = _y_axis(0.0, y_max, 4, y_label)
    pw = W - PAD_L - PAD_R

    def sx(i: float) -> float:
        return PAD_L + pw * i / max(1, n - 1)

    body = [grid, f'<line class="ax" x1="{PAD_L}" y1="{H - PAD_B}" x2="{W - PAD_R}" y2="{H - PAD_B}"/>']
    for k in range(5):
        i = (n - 1) * k / 4
        body.append(f'<text class="tick" x="{sx(i):.1f}" y="{H - PAD_B + 18}" text-anchor="middle">{int(round(i))}</text>')
    body.append(f'<text x="{PAD_L + pw / 2:.0f}" y="{H - 8}" text-anchor="middle">{escape(x_title)}</text>')
    if threshold is not None:
        y = sy(threshold[0])
        body.append(f'<line class="thr" x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}"/>'
                    f'<text x="{W - PAD_R}" y="{y - 6:.1f}" text-anchor="end">{escape(threshold[1])}</text>')
    if marker_x is not None:
        x = sx(marker_x[0])
        body.append(f'<line class="thr" x1="{x:.1f}" y1="{PAD_T}" x2="{x:.1f}" y2="{H - PAD_B}"/>'
                    f'<text x="{x + 6:.1f}" y="{PAD_T + 12}">{escape(marker_x[1])}</text>')
    for si, (_, ys) in enumerate(series):
        if not ys:
            continue
        # Прореживаем до ≤ 400 точек: форма линии сохраняется, файл остаётся маленьким.
        step = max(1, len(ys) // 400)
        pts = [(sx(i), sy(v)) for i, v in enumerate(ys) if i % step == 0 or i == len(ys) - 1]
        d = " ".join(f"{'M' if k == 0 else 'L'}{x:.1f},{y:.1f}" for k, (x, y) in enumerate(pts))
        body.append(f'<path d="{d}" fill="none" stroke="var(--s{si % 6 + 1})" stroke-width="2" stroke-linejoin="round"/>')
    desc = "; ".join(f"{lab}: {len(ys)} точек, максимум {_fmt(max(ys) if ys else 0)}" for lab, ys in series)
    return _with_legend(title, "".join(body), [s[0] for s in series], desc)


def ci_chart(title: str, rows: Sequence[tuple[str, float, float | None, float | None, bool]], *, x_label: str,
             x_max: float = 1.0, threshold: tuple[float, str] | None = None) -> str:
    """Точки с 95% ДИ (горизонтально). rows: (подпись, среднее, ДИ низ, ДИ верх, выделить)."""
    left = 230
    row_h = 30
    h = PAD_T + row_h * len(rows) + PAD_B
    pw = W - left - PAD_R

    def sx(v: float) -> float:
        return left + pw * max(0.0, min(1.0, v / x_max))

    body = []
    for k in range(6):
        v = x_max * k / 5
        x = sx(v)
        body.append(f'<line class="g" x1="{x:.1f}" y1="{PAD_T - 8}" x2="{x:.1f}" y2="{h - PAD_B}"/>'
                    f'<text class="tick" x="{x:.1f}" y="{h - PAD_B + 18}" text-anchor="middle">{_fmt(v)}</text>')
    body.append(f'<text x="{left + pw / 2:.0f}" y="{h - 10}" text-anchor="middle">{escape(x_label)}</text>')
    if threshold is not None:
        x = sx(threshold[0])
        body.append(f'<line class="thr" x1="{x:.1f}" y1="{PAD_T - 14}" x2="{x:.1f}" y2="{h - PAD_B}"/>'
                    f'<text x="{x + 4:.1f}" y="{PAD_T - 18}">{escape(threshold[1])}</text>')
    for i, (lab, m, lo, hi, hl) in enumerate(rows):
        y = PAD_T + row_h * i + row_h / 2
        c = "var(--s1)" if hl else "var(--muted)"
        emph = ' style="fill:var(--ink);font-weight:600"' if hl else ""
        body.append(f'<text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end"{emph}>{escape(lab)}</text>')
        if lo is not None and hi is not None:
            body.append(f'<line x1="{sx(lo):.1f}" y1="{y:.1f}" x2="{sx(hi):.1f}" y2="{y:.1f}" stroke="{c}" stroke-width="2"/>'
                        f'<line x1="{sx(lo):.1f}" y1="{y - 5:.1f}" x2="{sx(lo):.1f}" y2="{y + 5:.1f}" stroke="{c}" stroke-width="2"/>'
                        f'<line x1="{sx(hi):.1f}" y1="{y - 5:.1f}" x2="{sx(hi):.1f}" y2="{y + 5:.1f}" stroke="{c}" stroke-width="2"/>')
        body.append(f'<circle cx="{sx(m):.1f}" cy="{y:.1f}" r="5" fill="{c}" stroke="var(--bg)" stroke-width="2"/>'
                    f'<text class="tick" x="{sx(hi if hi is not None else m) + 10:.1f}" y="{y + 4:.1f}">{_fmt(m)}</text>')
    svg = _frame(title, "".join(body), "; ".join(f"{r[0]}: {_fmt(r[1])}" for r in rows))
    return svg.replace(f'viewBox="0 0 {W} {H}" width="{W}" height="{H}"', f'viewBox="0 0 {W} {h}" width="{W}" height="{h}"') \
              .replace(f'<rect width="{W}" height="{H}"', f'<rect width="{W}" height="{h}"')
