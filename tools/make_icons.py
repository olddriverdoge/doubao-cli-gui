#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 PWA 图标（纯标准库，4x 超采样抗锯齿）。"""

import os
import struct
import zlib

SIZE = 512
SS = 4


def inside_round_rect(x, y, x0, y0, x1, y1, radius):
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = min(max(x, x0 + radius), x1 - radius)
    cy = min(max(y, y0 + radius), y1 - radius)
    dx = x - cx
    dy = y - cy
    return dx * dx + dy * dy <= radius * radius


def inside_ellipse(x, y, cx, cy, rx, ry):
    dx = (x - cx) / rx
    dy = (y - cy) / ry
    return dx * dx + dy * dy <= 1.0


def color_at(x, y):
    """返回 (r,g,b,alpha)，坐标按 0..512 的设计空间。"""
    radius = 112
    if not inside_round_rect(x, y, 0, 0, 512, 512, radius):
        return (0, 0, 0, 0)
    t = (x + y) / 1024.0
    r = int(0x5b + (0x32 - 0x5b) * t)
    g = int(0x7c + (0x51 - 0x7c) * t)
    b = int(0xff + (0xe6 - 0xff) * t)
    if inside_ellipse(x, y, 256, 244, 118, 96):
        return (255, 255, 255, 255)
    if inside_round_rect(x, y, 150, 300, 362, 326, 13):
        return (255, 255, 255, 255)
    if inside_ellipse(x, y, 212, 222, 13, 13) or inside_ellipse(x, y, 300, 222, 13, 13):
        return (r, g, b, 255)
    return (r, g, b, 255)


def render(size):
    scale = 512.0 / size
    pixels = bytearray()
    for row in range(size):
        for col in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SS):
                for sx in range(SS):
                    x = (col + (sx + 0.5) / SS) * scale
                    y = (row + (sy + 0.5) / SS) * scale
                    sample = color_at(x, y)
                    weight = sample[3]
                    acc[0] += sample[0] * weight
                    acc[1] += sample[1] * weight
                    acc[2] += sample[2] * weight
                    acc[3] += weight
            total = SS * SS
            alpha = acc[3] / total
            if acc[3] > 0:
                pixels.extend(
                    (
                        int(acc[0] / acc[3]),
                        int(acc[1] / acc[3]),
                        int(acc[2] / acc[3]),
                        int(round(alpha)),
                    )
                )
            else:
                pixels.extend((0, 0, 0, 0))
    return bytes(pixels)


def write_png(path, size, rgba):
    stride = size * 4
    raw = bytearray()
    for row in range(size):
        raw.append(0)
        raw.extend(rgba[row * stride : (row + 1) * stride])

    def chunk(tag, payload):
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    body = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as handle:
        handle.write(body)


def main():
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
    for size in (192, 512):
        target = os.path.abspath(os.path.join(here, f"icon-{size}.png"))
        write_png(target, size, render(size))
        print(f"写入 {target} ({os.path.getsize(target)} 字节)")


if __name__ == "__main__":
    main()
