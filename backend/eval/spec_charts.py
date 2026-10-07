"""PNG-графики test-retest и разделяющей способности (matplotlib, необязателен: нет пакета — графики пропускаются).

    python eval/spec_charts.py eval/results/summary.json ../docs/img

Самостоятельный скрипт: читает только JSON (раздел spec_validation), приложение не импортирует.
Палитра — безопасная для дальтоников (Okabe–Ito), подписи значений прямо на столбцах.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SPEC_RU = {"backend_python": "Backend", "frontend_js": "Frontend", "qa": "QA", "devops": "DevOps", "all": "Все"}
BLUE, ORANGE, GREEN, INK = "#0072B2", "#E69F00", "#009E73", "#333333"


def _plt() -> Any:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": "#888888",
                         "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK, "font.family": "DejaVu Sans"})
    return plt


def _bars(ax: Any, xs: list[float], vals: list[float | None], width: float, color: str, label: str, values: bool = True) -> None:
    ys = [v or 0.0 for v in vals]
    ax.bar(xs, ys, width, color=color, label=label)
    for x, v in zip(xs, vals, strict=True):
        if v is not None and values:
            ax.text(x, v + 0.012, f"{v:.2f}".replace(".", ","), ha="center", va="bottom", fontsize=8, color=INK)


def make_charts(sv: dict[str, Any], out_dir: Path) -> list[Path]:
    plt = _plt()
    if plt is None:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    keys = [k for k in ("backend_python", "frontend_js", "qa", "devops") if k in sv["by_spec"]] + ["all"]
    blocks = {k: (sv["by_spec"][k] if k != "all" else sv["overall"]) for k in keys}
    labels = [SPEC_RU[k] for k in keys]
    xs = list(range(len(keys)))
    paths = []

    # 1. Test-retest: точное совпадение грейда и QWK
    fig, ax = plt.subplots(figsize=(8.2, 4.2), dpi=140)
    w = 0.26
    _bars(ax, [x - w for x in xs], [blocks[k]["retest_parallel"].get("grade_exact") for k in keys], w, BLUE, "точное совпадение грейда, параллельная сессия")
    _bars(ax, xs, [blocks[k]["retest_real_rules"].get("grade_exact") for k in keys], w, ORANGE, "то же, пересдача по правилам (другие семейства)")
    _bars(ax, [x + w for x in xs], [blocks[k]["retest_parallel"].get("grade_qwk") for k in keys], w, GREEN, "взвешенный κ грейда, параллельная сессия")
    ax.set_xticks(xs, labels)
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("доля / κ")
    ax.set_title("Test-retest: один латентный профиль, две независимые сессии", loc="left", fontsize=11)
    ax.legend(frameon=False, fontsize=8, loc="lower center", ncol=1, bbox_to_anchor=(0.5, -0.46))
    fig.tight_layout()
    p = out_dir / "validation_retest.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    paths.append(p)

    # 2. Матрица ошибок (все специализации): строки — истина
    conf = sv["overall"]["separation"]["confusion"]
    rows = ["intern", "junior", "middle", "senior"]
    cols = ["none", "intern", "junior", "middle", "senior"]
    ru = {"none": "не подтв.", "intern": "Стажёр", "junior": "Junior", "middle": "Middle", "senior": "Senior"}
    mat = [[conf.get(r, {}).get(c, 0) for c in cols] for r in rows]
    fig, ax = plt.subplots(figsize=(6.2, 4.2), dpi=140)
    norm = [[v / max(1, sum(r)) for v in r] for r in mat]
    ax.imshow(norm, cmap="Blues", vmin=0, vmax=1)
    for i, r in enumerate(mat):
        for j, v in enumerate(r):
            ax.text(j, i, f"{v}\n{norm[i][j] * 100:.0f}%", ha="center", va="center", fontsize=8,
                    color="white" if norm[i][j] > 0.55 else INK)
    ax.set_xticks(range(len(cols)), [ru[c] for c in cols])
    ax.set_yticks(range(len(rows)), [ru[r] for r in rows])
    ax.set_xlabel("грейд по итогам теста")
    ax.set_ylabel("латентный (истинный) грейд")
    ax.set_title("Матрица ошибок, все специализации", loc="left", fontsize=11)
    for s in ax.spines.values():
        s.set_visible(False)
    fig.tight_layout()
    p = out_dir / "validation_confusion.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    paths.append(p)

    # 3. Точность решения у каждого порога и рядом с ним
    cuts = ["intern|junior", "junior|middle", "middle|senior"]
    fig, axes = plt.subplots(1, 2, figsize=(10.0, 4.2), dpi=140, sharey=True)
    pal = [BLUE, ORANGE, GREEN, "#CC79A7", "#555555"]
    for ax, field, title in ((axes[0], "decision_accuracy", "по всем профилям"), (axes[1], "near_cut_accuracy", "рядом с порогом (|θ − порог| ≤ 0,5)")):
        w = 0.8 / len(keys)
        for i, k in enumerate(keys):
            vals = [blocks[k]["separation"].get("per_cut", {}).get(c, {}).get(field) for c in cuts]
            _bars(ax, [j - 0.4 + w * (i + 0.5) for j in range(3)], vals, w * 0.92, pal[i], SPEC_RU[k], values=k == "all")
        ax.set_xticks(range(3), cuts)
        ax.set_title(title, loc="left", fontsize=10)
        ax.axhline(0.85, color="#999999", lw=0.8, ls="--")
    axes[0].set_ylim(0, 1.12)
    axes[0].set_ylabel("точность решения «выше/ниже порога»")
    h, lab = axes[0].get_legend_handles_labels()
    fig.legend(h, lab, frameon=False, fontsize=8, ncol=5, loc="lower center")
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    p = out_dir / "validation_cuts.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    paths.append(p)
    return paths


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    data = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    made = make_charts(data["spec_validation"], Path(argv[1]))
    print("\n".join(str(p) for p in made) or "matplotlib не установлен: графики пропущены")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
