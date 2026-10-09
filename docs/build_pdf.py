#!/usr/bin/env python3
"""Сборка PDF документации «Взаимно» в docs/pdf/.

Что получается:
  docs/pdf/Взаимно — документация.pdf   сводный: титул, содержание с номерами страниц, разделы в порядке перечня организаторов
  docs/pdf/NN Название.pdf              по одному PDF на документ
  docs/pdf/diagrams/*.svg               диаграммы Mermaid, отрисованные в SVG

Конвейер: Markdown (markdown-it-py, якоря как на GitHub) -> HTML -> headless Chrome/Edge (Playwright) с отрисовкой Mermaid -> PDF.
Номера страниц в содержании ставятся вторым проходом: первый PDF читается pypdf, у каждого заголовка находится страница.

Зависимости (все с открытой лицензией):
  pip install markdown-it-py pypdf          (MIT, BSD)
  cd docs/pdf-tools && npm install          (mermaid — MIT, playwright-core — Apache-2.0)
  установленный Google Chrome или Microsoft Edge (иначе Playwright возьмёт свой Chromium)

Запуск: python docs/build_pdf.py [--only API] [--no-separate] [--check-links]   (или `make docs-pdf`)
Ссылки между документами проверяются всегда; битая ссылка или нерисующаяся диаграмма завершают сборку с ошибкой.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from urllib.parse import unquote
from dataclasses import dataclass, field
from pathlib import Path

from markdown_it import MarkdownIt

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
OUT = DOCS / "pdf"
TOOLS = DOCS / "pdf-tools"
COMBINED_NAME = "Взаимно — документация.pdf"


@dataclass(frozen=True)
class Doc:
    stem: str  # имя файла без .md
    title: str  # короткое русское название для содержания и имён файлов
    part: str  # метка раздела в сводном PDF
    requirement: str = ""  # пункт перечня организаторов
    no: str = ""  # префикс имени отдельного PDF (совпадает с номером пункта перечня)


# Порядок = порядок перечня организаторов, затем дополнительные материалы.
DOCS_ORDER: list[Doc] = [
    Doc("README", "Карта документации", "Начало", "Навигация", no="00"),
    Doc("REQUIREMENTS", "Требования задачи и где они реализованы", "Начало", "Навигация", no="00.1"),
    Doc("ARCHITECTURE", "Архитектура", "Раздел 1", "1. Архитектура решения", no="01"),
    Doc("TESTING_MECHANISM", "Механика тестирования", "Раздел 2", "2. Механика тестирования", no="02"),
    Doc("MATCHING", "Механика подбора и категоризации", "Раздел 3", "3. Механика подбора и логика категоризации", no="03"),
    Doc("VALIDATION_PLAN", "Валидация — протокол", "Раздел 4а", "4. Процедура валидации", no="04.1"),
    Doc("VALIDATION", "Валидация — результаты", "Раздел 4б", "4. Результаты валидации", no="04.2"),
    Doc("FSP_INTEGRATION", "Интеграция с ФСП и ФСП ID", "Раздел 5", "5. Схемы интеграции с ФСП / ФСП ID", no="05"),
    Doc("API", "Описание API", "Раздел 6", "6. Описание API", no="06"),
    Doc("THIRD_PARTY", "Библиотеки и версии", "Раздел 7", "7. Перечень библиотек с версиями", no="07"),
    Doc("DEPLOY", "Сборка и запуск", "Раздел 8", "8. Инструкция по сборке и запуску", no="08"),
    Doc("ATS_INTEGRATION", "Интеграция с ATS", "Дополнительно", "Дополнительно", no="09"),
    Doc("TRUST_AND_SAFETY", "Доверие и безопасность платформы", "Дополнительно", "Дополнительно", no="10"),
    Doc("SECURITY", "Безопасность и персональные данные (152-ФЗ)", "Дополнительно", "Дополнительно", no="11"),
    Doc("LLM", "Использование LLM", "Дополнительно", "Дополнительно", no="12"),
    Doc("LOAD_TEST", "Нагрузочное тестирование", "Дополнительно", "Дополнительно", no="13"),
    Doc("CREDENTIALS", "Цифровой паспорт навыков", "Дополнительно", "Дополнительно", no="14"),
    Doc("ENGINEERING", "Инженерные практики", "Дополнительно", "Дополнительно", no="15"),
    Doc("ACCESSIBILITY", "Доступность и адаптивность", "Дополнительно", "Дополнительно", no="16"),
]
BY_STEM = {d.stem: d for d in DOCS_ORDER}

CSS = """
@page { size: A4; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-family: "Segoe UI", "DejaVu Sans", "Liberation Sans", Arial, sans-serif; font-size: 9.6pt; line-height: 1.45;
       color: #1b2433; margin: 0; overflow-wrap: anywhere; }
h1, h2, h3, h4 { color: #0f2a55; line-height: 1.25; break-after: avoid; }
h1 { font-size: 21pt; margin: 0 0 6mm; padding-bottom: 2mm; border-bottom: 2px solid #2f5aa8; }
h2 { font-size: 14.5pt; margin: 8mm 0 3mm; }
h3 { font-size: 11.5pt; margin: 6mm 0 2mm; }
h4 { font-size: 10pt; margin: 4mm 0 1.5mm; }
p { margin: 0 0 2.6mm; }
ul, ol { margin: 0 0 3mm; padding-left: 6mm; }
li { margin: 0.6mm 0; }
a { color: #1f4f9c; text-decoration: none; }
.xref { color: #1f4f9c; }
code { font-family: "Cascadia Mono", Consolas, "DejaVu Sans Mono", "Liberation Mono", monospace; font-size: 0.88em;
       background: #eef2f8; padding: 0 1.2mm; border-radius: 1mm; overflow-wrap: anywhere; word-break: break-word; }
pre { background: #f4f6fa; border: 1px solid #d7deea; border-radius: 1.5mm; padding: 2.5mm 3mm; margin: 0 0 3.5mm;
      white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; font-size: 7.9pt; line-height: 1.35; }
pre code { background: none; padding: 0; font-size: inherit; }
pre.mermaid { background: #fff; border: 1px solid #d7deea; text-align: center; white-space: normal; padding: 3mm; break-inside: avoid; }
pre.mermaid.rendered svg { width: 100% !important; max-width: 100% !important; height: auto !important; max-height: 225mm; }
pre.mermaid-failed { color: #a00; text-align: left; white-space: pre-wrap; font-size: 7pt; }
table { width: 100%; border-collapse: collapse; margin: 1mm 0 4mm; font-size: 8.1pt; line-height: 1.35; }
th, td { border: 1px solid #ccd5e3; padding: 1.2mm 1.8mm; vertical-align: top; text-align: left; overflow-wrap: break-word; }
th { background: #e8eef8; color: #0f2a55; }
tr { break-inside: avoid; }
thead { display: table-header-group; }
blockquote { margin: 0 0 3mm; padding: 1.5mm 3.5mm; border-left: 3px solid #2f5aa8; background: #f3f6fb; color: #33415c; }
blockquote p:last-child { margin-bottom: 0; }
img { max-width: 100%; height: auto; }
hr { border: 0; border-top: 1px solid #ccd5e3; margin: 5mm 0; }
.doc { break-before: page; }
.doc:first-of-type { break-before: auto; }
.part-label { font-size: 8.5pt; letter-spacing: 0.08em; text-transform: uppercase; color: #5a6b86; margin: 0 0 1.5mm; }
h2::after { content: attr(data-m); font-size: 1px; color: #fff; }
.docstart { font-size: 1px; line-height: 1px; color: #fff; height: 1px; overflow: hidden; }
.title-page { height: 255mm; display: flex; flex-direction: column; justify-content: center; padding: 0 6mm; break-after: page; }
.title-page .brand { font-size: 40pt; font-weight: 700; color: #0f2a55; margin: 0; }
.title-page .sub { font-size: 17pt; color: #2f5aa8; margin: 3mm 0 12mm; }
.title-page .meta { font-size: 11pt; color: #33415c; line-height: 1.7; }
.title-page .bar { width: 38mm; height: 3px; background: #2f5aa8; margin: 0 0 8mm; }
.toc { break-after: page; }
.toc h1 { border: 0; }
.toc ul { list-style: none; padding: 0; margin: 0; }
.toc li { display: flex; align-items: baseline; gap: 2mm; margin: 0.4mm 0; font-size: 9pt; }
.toc li .t { flex: 0 1 auto; }
.toc li .dots { flex: 1 1 auto; border-bottom: 1px dotted #9aa8c0; transform: translateY(-1.2mm); min-width: 4mm; }
.toc li .n { flex: 0 0 auto; font-variant-numeric: tabular-nums; color: #33415c; }
.toc li.doc-l { margin-top: 2.2mm; font-weight: 600; font-size: 10pt; color: #0f2a55; }
.toc li.h2-l { padding-left: 6mm; }
.toc .part { font-size: 8pt; color: #5a6b86; text-transform: uppercase; letter-spacing: 0.06em; margin: 4mm 0 0; }
"""


def gh_slug(text: str) -> str:
    """Якорь заголовка как на GitHub: нижний регистр, пунктуация выброшена, пробелы -> дефисы."""
    s = unicodedata.normalize("NFKC", text).strip().lower()
    s = re.sub(r"[^\w\- ]", "", s, flags=re.UNICODE)
    return s.replace(" ", "-")


def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


@dataclass
class Rendered:
    doc: Doc
    html: str
    ids: set[str]
    headings: list[tuple[int, str, str]] = field(default_factory=list)  # (level, id, text)
    links: list[tuple[str, str]] = field(default_factory=list)  # (href, где)


def render_md(doc: Doc) -> Rendered:
    src = (DOCS / f"{doc.stem}.md").read_text(encoding="utf-8")
    md = MarkdownIt("commonmark", {"html": True, "breaks": False}).enable("table")
    body = md.render(src)
    seen: dict[str, int] = {}
    ids: set[str] = set()
    headings: list[tuple[int, str, str]] = []

    def head(m: re.Match[str]) -> str:
        lvl, attrs, inner = int(m.group(1)), m.group(2), m.group(3)
        text = strip_tags(inner)
        slug = gh_slug(text) or "section"
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        hid = slug if n == 0 else f"{slug}-{n}"
        ids.add(hid)
        headings.append((lvl, hid, text))
        return f'<h{lvl}{attrs} id="{doc.stem}--{hid}" data-m="§§{doc.stem}--{hid}§§">{inner}</h{lvl}>'

    body = re.sub(r"<h([1-4])([^>]*)>(.*?)</h\1>", head, body, flags=re.S)
    # диаграммы Mermaid: <pre><code class="language-mermaid"> -> <pre class="mermaid">
    body = re.sub(r'<pre><code class="language-mermaid">(.*?)</code></pre>', r'<pre class="mermaid">\1</pre>', body, flags=re.S)
    return Rendered(doc, body, ids, headings)


def rewrite_links(r: Rendered, all_r: dict[str, Rendered], combined: bool, problems: list[str]) -> str:
    """Относительные ссылки -> внутренние якоря (сводный PDF) или текст со ссылкой-подписью (отдельный PDF)."""
    stem = r.doc.stem

    def fix(m: re.Match[str]) -> str:
        href, rest, inner = m.group(1), m.group(2), m.group(3)
        h = unquote(html.unescape(href))
        if re.match(r"^(https?:|mailto:)", h):
            return m.group(0)
        if h.startswith("#"):
            frag = h[1:]
            if frag not in r.ids:
                problems.append(f"{stem}.md: нет якоря #{frag}")
                return inner
            return f'<a href="#{stem}--{frag}"{rest}>{inner}</a>'
        path, _, frag = h.partition("#")
        if path.endswith(".md") and "/" not in path:
            tstem = path[:-3]
            if tstem not in all_r:
                if not (DOCS / path).exists():
                    problems.append(f"{stem}.md: нет файла {path}")
                return f'<span class="xref">{inner}</span>'
            if frag and frag not in all_r[tstem].ids:
                problems.append(f"{stem}.md: в {path} нет якоря #{frag}")
            if combined:
                target = f"{tstem}--{frag}" if frag else f"doc-{tstem}"
                return f'<a href="#{target}"{rest}>{inner}</a>'
            return f'<span class="xref">{inner}</span>'
        # прочие относительные пути: проверяем существование и убираем гиперссылку (в PDF она бесполезна)
        target = (DOCS / path).resolve() if path else None
        if target is not None and not target.exists() and not path.startswith(("http", "pdf/")):
            problems.append(f"{stem}.md: нет файла {path}")
        return f'<span class="xref">{inner}</span>'

    return re.sub(r'<a href="([^"]*)"([^>]*)>(.*?)</a>', fix, r.html, flags=re.S)


def fix_images(body: str) -> str:
    def img(m: re.Match[str]) -> str:
        src = html.unescape(m.group(1))
        if re.match(r"^(https?:|data:)", src):
            return m.group(0)
        p = (DOCS / src).resolve()
        return m.group(0).replace(m.group(1), p.as_uri()) if p.exists() else m.group(0)

    return re.sub(r'<img [^>]*?src="([^"]*)"', img, body)


def git_rev() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001 — репозиторий может быть без git
        return "n/a"


def page(title: str, body: str) -> str:
    return f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>{html.escape(title)}</title><style>{CSS}</style></head><body>{body}</body></html>'


def doc_block(r: Rendered, all_r: dict[str, Rendered], combined: bool, problems: list[str]) -> str:
    label = f'<p class="part-label">{html.escape(r.doc.part)} · {html.escape(r.doc.requirement)}</p>' if combined and r.doc.requirement else ""
    body = fix_images(rewrite_links(r, all_r, combined, problems))
    return f'<section class="doc" id="doc-{r.doc.stem}"><div class="docstart">§§{r.doc.stem}§§</div>{label}{body}</section>'


def toc_html(rs: list[Rendered], pages: dict[str, str]) -> str:
    out = ['<section class="toc"><h1>Содержание</h1><ul>']
    last_part = None
    for r in rs:
        group = r.doc.requirement if r.doc.requirement == "Дополнительно" else "Перечень организаторов"
        if r.doc.stem == "README":
            group = "Навигация"
        if group != last_part:
            out.append(f'</ul><p class="part">{html.escape(group)}</p><ul>')
            last_part = group
        out.append(
            f'<li class="doc-l"><a class="t" href="#doc-{r.doc.stem}">{html.escape(r.doc.requirement.split(".")[0] + ". " if r.doc.requirement[:1].isdigit() else "")}{html.escape(r.doc.title)}</a>'
            f'<span class="dots"></span><span class="n">{pages.get("doc:" + r.doc.stem, "00")}</span></li>'
        )
        for lvl, hid, text in r.headings:
            if lvl == 2:
                out.append(
                    f'<li class="h2-l"><a class="t" href="#{r.doc.stem}--{hid}">{html.escape(text)}</a>'
                    f'<span class="dots"></span><span class="n">{pages.get(f"{r.doc.stem}--{hid}", "00")}</span></li>'
                )
    out.append("</ul></section>")
    return "".join(out)


def title_html(rs: list[Rendered]) -> str:
    items = "".join(f"<li>{html.escape(r.doc.requirement)}</li>" for r in rs if r.doc.requirement[:1].isdigit() and r.doc.stem != "VALIDATION")
    return (
        '<section class="title-page"><div class="bar"></div><p class="brand">«Взаимно»</p>'
        '<p class="sub">Платформа обратного подбора ИТ-специалистов<br>Сопроводительная документация решения</p>'
        '<div class="meta">Хакатон ЛЦТ-2026, спецтрек ФСП<br>'
        f"Состояние репозитория: коммит {git_rev()}, 9 октября 2026<br>"
        "Все числа параметров берутся из <code>config/*.yaml</code>; результаты валидации получены на синтетической популяции<br><br>"
        f"<b>Состав:</b><ul>{items}<li>Дополнительно: ATS, доверие и безопасность, 152-ФЗ, LLM, нагрузка</li></ul></div></section>"
    )


def node_render(html_path: Path, pdf_path: Path, footer: str, svg_dir: Path | None = None, svg_prefix: str = "") -> None:
    node = shutil.which("node")
    if not node:
        sys.exit("нужен Node.js (node в PATH)")
    if not (TOOLS / "node_modules" / "mermaid").exists():
        sys.exit("выполните: cd docs/pdf-tools && npm install")
    cmd = [node, str(TOOLS / "render.mjs"), str(html_path), str(pdf_path), footer]
    if svg_dir:
        cmd += [str(svg_dir), svg_prefix]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    sys.stdout.write(res.stdout)
    if res.returncode != 0:
        sys.stderr.write(res.stderr)
        sys.exit(f"сборка {pdf_path.name} не удалась (код {res.returncode})")


def locate_pages(pdf_path: Path, rs: list[Rendered]) -> dict[str, str]:
    """Для каждого документа и заголовка h2 находит страницу по невидимым маркерам §§id§§ в тексте PDF (pypdf)."""
    from pypdf import PdfReader

    norm = lambda s: re.sub(r"[\s_\-]+", "", s).lower()  # noqa: E731
    texts = [norm(p.extract_text() or "") for p in PdfReader(str(pdf_path)).pages]
    res: dict[str, str] = {}
    for r in rs:
        start = next((i for i, t in enumerate(texts) if norm(f"§§{r.doc.stem}§§") in t), None)
        if start is None:
            continue
        res["doc:" + r.doc.stem] = str(start + 1)
        cur = start
        for lvl, hid, _ in r.headings:
            if lvl != 2:
                continue
            key = f"{r.doc.stem}--{hid}"
            needle = norm(f"§§{key}§§")
            found = next((i for i in range(cur, len(texts)) if needle in texts[i]), None)
            if found is not None:
                cur = found
            res[key] = str((cur if found is None else found) + 1)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="собрать отдельный PDF только для одного документа (например API) и выйти")
    ap.add_argument("--no-separate", action="store_true", help="только сводный PDF")
    ap.add_argument("--no-combined", action="store_true", help="только отдельные PDF")
    ap.add_argument("--check-links", action="store_true", help="только проверить ссылки и якоря")
    args = ap.parse_args()

    rs = [render_md(d) for d in DOCS_ORDER]
    all_r = {r.doc.stem: r for r in rs}
    problems: list[str] = []
    for r in rs:  # проверка ссылок во всех документах
        rewrite_links(r, all_r, True, problems)
    if problems:
        print("Битые ссылки:\n  " + "\n  ".join(sorted(set(problems))), file=sys.stderr)
        sys.exit(2)
    print(f"ссылки между документами: ошибок нет ({len(rs)} документов)")
    if args.check_links:
        return

    OUT.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="vz_docs_"))
    try:
        if not args.only and not args.no_combined:
            blocks = "".join(doc_block(r, all_r, True, []) for r in rs)
            p1 = tmp / "combined.html"
            pdf1 = tmp / "pass1.pdf"
            p1.write_text(page("Взаимно — документация", title_html(rs) + toc_html(rs, {}) + blocks), encoding="utf-8")
            node_render(p1, pdf1, "Взаимно — документация")
            pages = locate_pages(pdf1, rs)
            p2 = tmp / "combined2.html"
            p2.write_text(page("Взаимно — документация", title_html(rs) + toc_html(rs, pages) + blocks), encoding="utf-8")
            node_render(p2, OUT / COMBINED_NAME, "Взаимно — документация")
        if not args.no_separate:
            svg_dir = OUT / "diagrams"
            if svg_dir.exists() and not args.only:
                shutil.rmtree(svg_dir)
            n = 0
            for idx, r in enumerate(rs):
                if args.only and r.doc.stem != args.only:
                    continue
                name = f"{r.doc.no} {r.doc.title}.pdf"
                h = tmp / f"{r.doc.stem}.html"
                h.write_text(page(r.doc.title, doc_block(r, all_r, False, [])), encoding="utf-8")
                node_render(h, OUT / name, f"Взаимно · {r.doc.title}", svg_dir, f"{r.doc.stem}-")
                n += 1
            print(f"отдельных PDF: {n}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
