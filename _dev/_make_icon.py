# -*- coding: utf-8 -*-
"""SignalLight 图标生成器。

用法：
    .venv\\Scripts\\python.exe _dev\\_make_icon.py preview   # 出 A/B/C 对比图 + 选定方案展示图
    .venv\\Scripts\\python.exe _dev\\_make_icon.py build A   # 出 png + ico（A 可换 B/C）

设计约束：16~32px 下两个第三方 logo 必然糊，所以主形必须是红绿灯，
品牌标记退化成底部两枚小色块 —— 小尺寸靠"三灯 + 两个点"传达信息。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QPixmap,
)

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
OUT = ROOT / "_dev"

S = 1024  # 主画布尺寸，所有坐标按 1024 设计再等比缩放

# 取色自挂件本体，保证图标和卡片是同一套视觉
BG = QColor("#0F1217")
HOUSING = QColor("#1E242D")
HOUSING_EDGE = QColor("#39414E")
RED = QColor("#FF5C5C")
AMBER = QColor("#FFB020")
GREEN = QColor("#2BD97C")
INSET = QColor("#0A0D11")

# Codex 官方色：#B1A7FF -> #7A9DFF -> #3941FF
CX_TOP = QColor("#B1A7FF")
CX_MID = QColor("#7A9DFF")
CX_BOT = QColor("#3941FF")


def _wb_pixmap() -> QPixmap:
    pm = QPixmap(str(ASSETS / "workbuddy.png"))
    return pm.scaled(1024, 1024, Qt.KeepAspectRatio, Qt.SmoothTransformation)


WB = None


def rounded(p: QPainter, rect: QRectF, radius: float, fill: QColor, edge: QColor | None = None, edge_w: float = 0.0):
    p.setBrush(fill)
    if edge is not None and edge_w > 0:
        p.setPen(QPen(edge, edge_w))
    else:
        p.setPen(Qt.NoPen)
    p.drawRoundedRect(rect, radius, radius)


def lamp(p: QPainter, cx: float, cy: float, r: float, color: QColor, lit: bool) -> None:
    """未点亮的灯保留色相与环，别压成黑圆；点亮的灯用径向渐变做光晕。

    早先版本光晕半径给到 1.55~2.0r，直接把灯罩吃掉、红灯黄灯糊成暗块 ——
    "红绿灯"这个识别点就是靠三盏灯各自可辨，宁可光晕克制。
    """
    if lit:
        grad = QRadialGradient(QPointF(cx, cy), r * 1.62)
        for stop, a in ((0.0, 84), (0.60, 46), (0.85, 16), (1.0, 0)):
            c = QColor(color)
            c.setAlpha(a)
            grad.setColorAt(stop, c)
        p.setBrush(grad)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), r * 1.62, r * 1.62)

        p.setBrush(color)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), r, r)

        hi = QColor(255, 255, 255, 52)
        p.setBrush(hi)
        p.drawEllipse(QPointF(cx - r * 0.24, cy - r * 0.28), r * 0.4, r * 0.3)
    else:
        dark = QColor(color)
        dark.setAlpha(52)
        p.setBrush(dark)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), r, r)

        rim = QColor(color)
        rim.setAlpha(150)
        p.setPen(QPen(rim, r * 0.075))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r * 0.97, r * 0.97)


def wb_chip(p: QPainter, rect: QRectF) -> None:
    """WorkBuddy 官方 logo 原样贴入，不重画。"""
    p.save()
    clip = QPainterPath()
    clip.addRoundedRect(rect, rect.width() * 0.24, rect.width() * 0.24)
    p.setClipPath(clip)
    p.drawPixmap(rect, WB, QRectF(0, 0, WB.width(), WB.height()))
    p.restore()
    p.setPen(QPen(QColor(255, 255, 255, 30), 3))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(rect, rect.width() * 0.24, rect.width() * 0.24)


def codex_chip(p: QPainter, rect: QRectF) -> None:
    """Codex 标记：白底圆角块 + 蓝紫渐变团块 + 白色 >_ 。"""
    rounded(p, rect, rect.width() * 0.24, QColor("#F6F7FA"))

    inner = rect.adjusted(rect.width() * 0.14, rect.width() * 0.14, -rect.width() * 0.14, -rect.width() * 0.14)
    g = QLinearGradient(inner.topLeft(), inner.bottomLeft())
    g.setColorAt(0.0, CX_TOP)
    g.setColorAt(0.45, CX_MID)
    g.setColorAt(1.0, CX_BOT)

    # 官方轮廓是六瓣云团，此处用大圆角方块近似：小尺寸下二者无法区分
    blob = QPainterPath()
    blob.addRoundedRect(inner, inner.width() * 0.34, inner.width() * 0.34)
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawPath(blob)

    p.save()
    p.setClipPath(blob)
    pen = QPen(QColor(255, 255, 255), inner.width() * 0.105)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)

    w, h = inner.width(), inner.height()
    x0, y0 = inner.x(), inner.y()
    chev = QPainterPath()
    chev.moveTo(x0 + w * 0.30, y0 + h * 0.33)
    chev.lineTo(x0 + w * 0.42, y0 + h * 0.50)
    chev.lineTo(x0 + w * 0.30, y0 + h * 0.67)
    p.drawPath(chev)

    under = QPainterPath()
    under.moveTo(x0 + w * 0.52, y0 + h * 0.67)
    under.lineTo(x0 + w * 0.72, y0 + h * 0.67)
    p.drawPath(under)
    p.restore()


def lamps_in(p: QPainter, clip: QRectF, radius: float, items) -> None:
    """把灯画在灯罩内部：光晕必须在罩子里衰减，溢到外面就是渲染事故。

    灯罩外沿重新描一遍，避免被灯的渐变盖糊。
    """
    path = QPainterPath()
    path.addRoundedRect(clip, radius, radius)
    p.save()
    p.setClipPath(path)
    for cx, cy, r, color, lit in items:
        lamp(p, cx, cy, r, color, lit)
    p.restore()


def variant_a(p: QPainter) -> None:
    """横排信号头 + 下方品牌底座。正方形画布最吃得下这个构图。"""
    rounded(p, QRectF(0, 0, S, S), S * 0.22, BG, QColor(255, 255, 255, 14), 4)
    housing = QRectF(72, 187, 880, 320)
    rounded(p, housing, 160, HOUSING)
    cy = 347
    lamps_in(
        p,
        housing,
        160,
        [
            (280, cy, 104, RED, False),
            (512, cy, 104, AMBER, False),
            (744, cy, 104, GREEN, True),
        ],
    )
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(HOUSING_EDGE, 5))
    p.drawRoundedRect(housing, 160, 160)

    w = 180
    y = 657
    gap = 56
    left = (S - (w * 2 + gap)) / 2
    wb_chip(p, QRectF(left, y, w, w))
    codex_chip(p, QRectF(left + w + gap, y, w, w))


def variant_b(p: QPainter) -> None:
    """竖排窄灯箱在左，品牌徽标竖排在右。像一块真的信号机 + 两个接入方。"""
    rounded(p, QRectF(0, 0, S, S), S * 0.22, BG, QColor(255, 255, 255, 14), 4)
    housing = QRectF(170, 80, 368, 864)
    rounded(p, housing, 176, HOUSING)
    cx = 354
    lamps_in(
        p,
        housing,
        176,
        [
            (cx, 250, 112, RED, False),
            (cx, 512, 112, AMBER, False),
            (cx, 774, 112, GREEN, True),
        ],
    )
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(HOUSING_EDGE, 5))
    p.drawRoundedRect(housing, 176, 176)

    w = 220
    x = 634
    y1 = 270
    wb_chip(p, QRectF(x, y1, w, w))
    codex_chip(p, QRectF(x, y1 + w + 44, w, w))


def variant_c(p: QPainter) -> None:
    """一体化：三灯 + 底部内嵌品牌条，整体是一个物件，没有漂浮的小方块。"""
    rounded(p, QRectF(0, 0, S, S), S * 0.22, BG, QColor(255, 255, 255, 14), 4)
    housing = QRectF(96, 96, 832, 832)
    rounded(p, housing, 180, HOUSING)

    cy = 300
    lamps_in(
        p,
        QRectF(96, 96, 832, 400),
        180,
        [
            (280, cy, 104, RED, False),
            (512, cy, 104, AMBER, False),
            (744, cy, 104, GREEN, True),
        ],
    )

    plate = QRectF(196, 500, 632, 320)
    rounded(p, plate, 76, INSET, QColor(255, 255, 255, 12), 3)

    w = 190
    y = 565
    gap = 60
    left = (S - (w * 2 + gap)) / 2
    wb_chip(p, QRectF(left, y, w, w))
    codex_chip(p, QRectF(left + w + gap, y, w, w))

    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(HOUSING_EDGE, 5))
    p.drawRoundedRect(housing, 180, 180)


VARIANTS = {"A": variant_a, "B": variant_b, "C": variant_c}


def render(name: str, size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.scale(size / S, size / S)
    VARIANTS[name](p)
    p.end()
    return img


def contact_sheet() -> QImage:
    """三方案 × 六尺寸，深底浅底各一行，判断小尺寸是否还认得出。"""
    pad = 26
    cols = [256, 128, 64, 48, 32, 16]
    col_w = 296
    sheet_w = pad * 2 + col_w * len(cols)
    row_h = 320
    head_h = 54
    header = 46
    sheet_h = pad + header + head_h + row_h * len(VARIANTS) + 120
    img = QImage(sheet_w, sheet_h, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor("#15171C"))

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)

    p.setPen(QColor("#8A93A3"))
    f = p.font()
    f.setFamily("Segoe UI")
    f.setPixelSize(22)
    p.setFont(f)
    for i, s in enumerate(cols):
        p.drawText(QRectF(pad + i * col_w, pad, col_w, 30), Qt.AlignHCenter | Qt.AlignVCenter, f"{s}px")

    y = pad + header
    for name in "ABC":
        p.setPen(QColor("#E6EAF2"))
        f2 = p.font()
        f2.setPixelSize(26)
        f2.setBold(True)
        p.setFont(f2)
        p.drawText(QRectF(pad, y + 4, 60, 30), Qt.AlignLeft | Qt.AlignVCenter, name)
        p.setFont(f)

        for i, s in enumerate(cols):
            icon = render(name, s)
            x = pad + i * col_w + (col_w - s) / 2
            yy = y + head_h + (row_h - head_h - s) / 2
            p.drawImage(QPointF(x, yy), icon)
        y += row_h

    # 小尺寸在浅色背景下的可读性
    light_y = y + 6
    p.setPen(QColor("#8A93A3"))
    p.drawText(QRectF(pad, light_y - 4, 400, 28), Qt.AlignLeft | Qt.AlignVCenter, "light bg / 48-16px")
    lx = pad
    band = QRectF(pad, light_y + 26, sheet_w - pad * 2, 74)
    rounded(p, band, 14, QColor("#F2F3F5"))
    lx = band.x() + 24
    for name in "ABC":
        for s in (48, 32, 16):
            p.drawImage(QPointF(lx, band.center().y() - s / 2), render(name, s))
            lx += s + 14
        lx += 34
    p.end()
    return img


def final_sheet(name: str = "A") -> QImage:
    """选定方案的单独展示图：大图 + 尺寸阶梯 + 深浅底各一遍。"""
    w, h = 900, 476
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor("#15171C"))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    f = p.font()
    f.setFamily("Segoe UI")
    f.setPixelSize(18)
    p.setFont(f)

    p.setPen(QColor("#E6EAF2"))
    p.drawText(QRectF(40, 24, 300, 30), Qt.AlignLeft | Qt.AlignVCenter, f"方案 {name} · 深色底")
    p.drawImage(QPointF(40, 66), render(name, 256))

    p.setPen(QColor("#8A93A3"))
    p.drawText(QRectF(40, 340, 300, 26), Qt.AlignLeft | Qt.AlignVCenter, "256")
    x = 360
    for s in (128, 64, 48, 32, 16):
        p.drawImage(QPointF(x, 66 + (256 - s) / 2), render(name, s))
        p.setPen(QColor("#8A93A3"))
        p.drawText(QRectF(x - 10, 340, s + 20, 26), Qt.AlignHCenter | Qt.AlignVCenter, str(s))
        x += s + 26

    band = QRectF(40, 374, w - 80, 82)
    rounded(p, band, 14, QColor("#F2F3F5"))
    p.setPen(QColor("#59616F"))
    p.drawText(QRectF(58, band.y(), 210, band.height()), Qt.AlignLeft | Qt.AlignVCenter, "浅色底 / 48-16px")
    lx = 300
    for s in (48, 32, 16):
        p.drawImage(QPointF(lx, band.center().y() - s / 2), render(name, s))
        lx += s + 26
    p.end()
    return img


def main() -> None:
    global WB
    app = QGuiApplication(sys.argv[:1])
    WB = _wb_pixmap()

    mode = sys.argv[1] if len(sys.argv) > 1 else "preview"

    if mode == "preview":
        OUT.mkdir(exist_ok=True)
        img = contact_sheet()
        path = OUT / "icon_preview.png"
        img.save(str(path))
        for name in "ABC":
            render(name, 256).save(str(OUT / f"icon_{name}.png"))
        final = OUT / "icon_final.png"
        final_sheet().save(str(final))
        print("saved", path)
        print("saved", final)
        return

    if mode == "build":
        from PIL import Image

        name = (sys.argv[2] if len(sys.argv) > 2 else "A").upper()
        if name not in VARIANTS:
            raise SystemExit(f"unknown variant {name}; choose A/B/C")

        ASSETS.mkdir(exist_ok=True)
        png = ASSETS / "signallight.png"
        ico = ASSETS / "signallight.ico"
        render(name, 1024).save(str(png))

        # Windows 图标从任务栏 16px 到资源管理器大图标 256px 都要有独立帧；
        # 只给一张 256 让系统缩，16px 必糊。
        sizes = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]
        with Image.open(png) as base:
            base.convert("RGBA").save(str(ico), format="ICO", sizes=sizes)

        with Image.open(ico) as check:
            got = sorted(check.ico.sizes())
        assert tuple(got[-1]) == (256, 256), got
        assert min(got) == (16, 16), got
        print("saved", png, "variant", name)
        print("saved", ico, "frames", got)
        return

    raise SystemExit("mode must be preview|build")


if __name__ == "__main__":
    main()
