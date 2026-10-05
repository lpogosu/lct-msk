"""Минимальный декодер QR для тестов (без сторонних библиотек распознавания): матрица модулей → байты.

Поддерживает то, что рисует PDF-профиль: версии 1–9, любой уровень коррекции и маска, байтовый режим,
неповреждённое изображение (коррекция ошибок не нужна). Матрица берётся из векторных квадратов PDF.
Это настоящее декодирование: формат-инфо → маска → обход зигзагом → разведение блоков → поток бит.
"""

from __future__ import annotations

from collections.abc import Callable

from qrcode.base import rs_blocks
from qrcode.util import PATTERN_POSITION_TABLE

_MASKS: dict[int, Callable[[int, int], bool]] = {
    0: lambda i, j: (i + j) % 2 == 0,
    1: lambda i, j: i % 2 == 0,
    2: lambda i, j: j % 3 == 0,
    3: lambda i, j: (i + j) % 3 == 0,
    4: lambda i, j: (i // 2 + j // 3) % 2 == 0,
    5: lambda i, j: (i * j) % 2 + (i * j) % 3 == 0,
    6: lambda i, j: ((i * j) % 2 + (i * j) % 3) % 2 == 0,
    7: lambda i, j: ((i * j) % 3 + (i + j) % 2) % 2 == 0,
}
_FORMAT_MASK = 0b101010000010010


def _function_modules(n: int, version: int) -> list[list[bool]]:
    func = [[False] * n for _ in range(n)]

    def mark(r0: int, c0: int, h: int, w: int) -> None:
        for r in range(r0, r0 + h):
            for c in range(c0, c0 + w):
                if 0 <= r < n and 0 <= c < n:
                    func[r][c] = True

    # Поисковые узоры с разделителями и полями формата
    mark(0, 0, 9, 9)
    mark(0, n - 8, 9, 8)
    mark(n - 8, 0, 8, 9)
    finder = [row[:] for row in func]
    pos = PATTERN_POSITION_TABLE[version - 1]
    for r in pos:
        for c in pos:
            if finder[r][c]:
                continue
            mark(r - 2, c - 2, 5, 5)
    for i in range(n):
        func[6][i] = True
        func[i][6] = True
    if version >= 7:
        mark(0, n - 11, 6, 3)
        mark(n - 11, 0, 3, 6)
    return func


def decode_matrix(m: list[list[bool]]) -> bytes:
    n = len(m)
    version = (n - 17) // 4
    if not 1 <= version <= 9 or 17 + 4 * version != n:
        raise ValueError(f"неподдерживаемый размер QR: {n}")
    fmt = 0
    for i in range(15):
        if i < 6:
            v = m[i][8]
        elif i < 8:
            v = m[i + 1][8]
        else:
            v = m[n - 15 + i][8]
        fmt |= int(bool(v)) << i
    fmt ^= _FORMAT_MASK
    ec = (fmt >> 13) & 0b11
    mask = _MASKS[(fmt >> 10) & 0b111]
    func = _function_modules(n, version)

    bits: list[int] = []
    row, inc = n - 1, -1
    for col in range(n - 1, 0, -2):
        c0 = col - 1 if col <= 6 else col
        while True:
            for c in (c0, c0 - 1):
                if not func[row][c]:
                    bits.append(int(bool(m[row][c]) ^ mask(row, c)))
            row += inc
            if row < 0 or row >= n:
                row -= inc
                inc = -inc
                break
    blocks = rs_blocks(version, ec)
    total = sum(b.total_count for b in blocks)
    codewords = [int("".join(map(str, bits[i * 8 : i * 8 + 8])), 2) for i in range(total)]
    data_blocks: list[list[int]] = [[] for _ in blocks]
    idx = 0
    for i in range(max(b.data_count for b in blocks)):
        for r, b in enumerate(blocks):
            if i < b.data_count:
                data_blocks[r].append(codewords[idx])
                idx += 1
    stream = "".join(f"{x:08b}" for blk in data_blocks for x in blk)
    out = bytearray()
    p = 0
    while p + 4 <= len(stream):
        mode = int(stream[p : p + 4], 2)
        p += 4
        if mode == 0:
            break
        if mode != 0b0100:
            raise ValueError(f"режим {mode:04b} не поддерживается тестовым декодером")
        length = int(stream[p : p + 8], 2)
        p += 8
        for _ in range(length):
            out.append(int(stream[p : p + 8], 2))
            p += 8
    return bytes(out)


def matrix_from_rects(rects: list[dict], *, bbox: tuple[float, float, float, float]) -> list[list[bool]]:
    """Матрица из прямоугольников pdfplumber внутри bbox (x0, top, x1, bottom). Модуль = минимальная высота."""
    inside = [
        r
        for r in rects
        if r["x0"] >= bbox[0] - 0.5 and r["x1"] <= bbox[2] + 0.5 and r["top"] >= bbox[1] - 0.5 and r["bottom"] <= bbox[3] + 0.5
    ]
    if not inside:
        raise ValueError("в области QR нет модулей")
    module = min(r["bottom"] - r["top"] for r in inside)
    x0 = min(r["x0"] for r in inside)
    top = min(r["top"] for r in inside)
    size = int(round((max(r["x1"] for r in inside) - x0) / module))
    size_v = int(round((max(r["bottom"] for r in inside) - top) / module))
    n = max(size, size_v)
    m = [[False] * n for _ in range(n)]
    for r in inside:
        row = int(round((r["top"] - top) / module))
        c_start = int(round((r["x0"] - x0) / module))
        c_end = int(round((r["x1"] - x0) / module))
        for c in range(c_start, c_end):
            m[row][c] = True
    return m
