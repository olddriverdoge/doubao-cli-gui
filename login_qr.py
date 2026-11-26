"""把 CLI 在终端里画的字符二维码还原成可扫的 PNG。

CLI 用半块字符（▀ ▄ █ 空格）画二维码：一个字符宽 = 1 个模块，一行字符 = 2 行模块。
它按"深色背景"渲染——实际浅色模块打印成块字符、实际深色模块打印成空格，
所以拿到的矩阵要按 1 - value 翻一次才是一次正常的二维码。
另外终端会带一圈 1 个模块的外框（浅色 quiet zone 被画成块），裁剪时按候选搜索。
只依赖标准库。
"""

import base64
import struct
import zlib

BLOCKS = {
    " ": (0, 0),
    "\u2588": (1, 1),  # █ 上下都实
    "\u2580": (1, 0),  # ▀ 只有上半
    "\u2584": (0, 1),  # ▄ 只有下半
}

VALID_SIZES = tuple(21 + 4 * i for i in range(40))


def _block_lines(text):
    out = []
    for line in text.splitlines():
        line = line.rstrip("\r")
        if len(line) < 21:
            continue
        if all(ch in BLOCKS for ch in line):
            out.append(line)
    if not out:
        return None
    width = max(len(line) for line in out)
    return [line.ljust(width) for line in out]


def _grid(lines):
    grid = []
    for line in lines:
        top, bottom = [], []
        for ch in line:
            up, down = BLOCKS[ch]
            top.append(up)
            bottom.append(down)
        grid.append(top)
        grid.append(bottom)
    return grid


def _finder_ok(grid):
    n = len(grid)
    if n not in VALID_SIZES or any(len(row) != n for row in grid):
        return False

    def finder(oy, ox):
        for y in range(7):
            for x in range(7):
                edge = y in (0, 6) or x in (0, 6)
                inner = 2 <= y <= 4 and 2 <= x <= 4
                expected = 1 if (edge or inner) else 0
                if grid[oy + y][ox + x] != expected:
                    return False
        return True

    return finder(0, 0) and finder(0, n - 7) and finder(n - 7, 0)


def _crop(grid, top, left, bottom, right):
    if top or bottom:
        grid = grid[top:len(grid) - bottom]
    if left or right:
        grid = [row[left:len(row) - right] for row in grid]
    return grid


def extract(text):
    """从 CLI 输出里取出二维码矩阵（深色模块 = 1）；识别不到返回 None。"""
    lines = _block_lines(text)
    if not lines:
        return None
    base = _grid(lines)
    variants = [[[1 - v for v in row] for row in base], base]
    for grid in variants:
        for top in range(3):
            for left in range(3):
                for bottom in range(3):
                    for right in range(3):
                        candidate = _crop(grid, top, left, bottom, right)
                        if _finder_ok(candidate):
                            return candidate
    return None


def png_bytes(grid, module=8, quiet=4):
    n = len(grid)
    side = (n + quiet * 2) * module
    rows = []
    for y in range(side):
        gy = y // module - quiet
        row = bytearray()
        for x in range(side):
            gx = x // module - quiet
            dark = 0 <= gy < n and 0 <= gx < n and grid[gy][gx]
            row.extend(b"\x00\x00\x00" if dark else b"\xff\xff\xff")
        rows.append(b"\x00" + bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


def data_url(text):
    """CLI 输出 -> data:image/png;base64,...；识别失败返回空串。"""
    grid = extract(text)
    if not grid:
        return ""
    return "data:image/png;base64," + base64.b64encode(png_bytes(grid)).decode("ascii")
