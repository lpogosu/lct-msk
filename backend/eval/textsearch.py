"""Лексические бейзлайны подбора: BM25 и косинус TF-IDF «бриф ↔ самоописание» (концепция §7.2, B1 и B3).

Токенизация общая для обоих: нормализация текста как у парсера брифа (`skills.normalize_text`: нижний
регистр, «ё» → «е»), слова из букв, цифр и «+#», кириллические слова длиннее 5 букв обрезаются до 5
(грубый стемминг вместо морфологии). B3 — ЗАМЕНИТЕЛЬ эмбеддингов: модель эмбеддингов в прогоне не
используется, поэтому честно называем его TF-IDF.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence

from app.matching.skills import normalize_text

_TOKEN = re.compile(r"[a-zа-я0-9+#]+")
_CYR = re.compile(r"[а-я]")
STEM_LEN = 5


def tokenize(text: str) -> list[str]:
    norm = normalize_text(text).norm
    out = []
    for t in _TOKEN.findall(norm):
        if len(t) > STEM_LEN and _CYR.match(t):
            t = t[:STEM_LEN]
        out.append(t)
    return out


class BM25:
    """Okapi BM25 (k1 = 1,5, b = 0,75) по коллекции документов {id: текст}."""

    def __init__(self, docs: Mapping[str, str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.ids = list(docs)
        self.tf = {d: Counter(tokenize(docs[d])) for d in self.ids}
        self.len = {d: sum(c.values()) for d, c in self.tf.items()}
        n = len(self.ids)
        self.avgdl = (sum(self.len.values()) / n) if n else 0.0
        df: Counter[str] = Counter()
        for c in self.tf.values():
            df.update(c.keys())
        self.idf = {t: math.log(1.0 + (n - k + 0.5) / (k + 0.5)) for t, k in df.items()}

    def score(self, query: str) -> dict[str, float]:
        q = Counter(tokenize(query))
        out = {}
        for d in self.ids:
            tf, dl, s = self.tf[d], self.len[d], 0.0
            for t in q:
                f = tf.get(t, 0)
                if not f:
                    continue
                s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1.0)))
            out[d] = s
        return out

    def rank(self, query: str) -> list[str]:
        sc = self.score(query)
        return sorted(self.ids, key=lambda d: (-sc[d], d))


class TfIdf:
    """Косинус TF-IDF (сглаженный idf, L2-нормировка) — заменитель эмбеддингов в B3."""

    def __init__(self, docs: Mapping[str, str]) -> None:
        self.ids = list(docs)
        tfs = {d: Counter(tokenize(docs[d])) for d in self.ids}
        n = len(self.ids)
        df: Counter[str] = Counter()
        for c in tfs.values():
            df.update(c.keys())
        self.idf = {t: math.log((1 + n) / (1 + k)) + 1.0 for t, k in df.items()}
        self.vecs = {d: self._vec(c) for d, c in tfs.items()}

    def _vec(self, c: Mapping[str, int]) -> dict[str, float]:
        v = {t: f * self.idf.get(t, 0.0) for t, f in c.items() if t in self.idf}
        norm = math.sqrt(sum(x * x for x in v.values()))
        return {t: x / norm for t, x in v.items()} if norm > 0 else {}

    def score(self, query: str) -> dict[str, float]:
        q = self._vec(Counter(tokenize(query)))
        return {d: sum(w * self.vecs[d].get(t, 0.0) for t, w in q.items()) for d in self.ids}

    def rank(self, query: str) -> list[str]:
        sc = self.score(query)
        return sorted(self.ids, key=lambda d: (-sc[d], d))


def rank_positions(ranking: Sequence[str]) -> dict[str, int]:
    """id → место (с 1)."""
    return {cid: i + 1 for i, cid in enumerate(ranking)}
