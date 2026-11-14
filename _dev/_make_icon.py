# -*- coding: utf-8 -*-
"""SignalLight 图标生成器。

用法：
    .venv\\Scripts\\python.exe _dev\\_make_icon.py preview   # 出 A/B/C 对比图 + 选定方案展示图
    .venv\\Scripts\\python.exe _dev\\_make_icon.py build A   # 出 png + ico（A 可换 B/C）

设计约束：16~32px 下两个第三方 logo 必然糊，所以主形必须是红绿灯，
品牌标记退化成底部两枚徽章 —— 小尺寸靠"三灯 + 两个点"传达信息。
Codex 侧必须是 assets/codex-knot.svg 的白色六瓣结，画法错了 _test_icon.py 会拦住。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
OUT = ROOT / "_dev"
sys.path.insert(0, str(ROOT / "app"))

import theme  # noqa: E402  —— 状态灯颜色只认这一处，别在图标里另编一套

S = 1024  # 主画布尺寸，所有坐标按 1024 设计再等比缩放

BG = QColor("#0F1217")
# 灯箱压成近黑、外沿提亮：彩色灯珠才跳得出来。
# 之前灯箱与卡片明度太接近，三盏灯看着像贴在灰板上。
HOUSING = QColor("#0B0E13")
HOUSING_EDGE = QColor("#4B5563")
INSET = QColor("#0A0D11")

# 三盏灯取软件状态灯的真实颜色：failed / waiting / done。
# 不是图标自己编的 #FF5C5C 那套 —— _test_icon.py 会逐项比对 theme.STATE_HEX。
LAMP_RED = QColor(theme.STATE_HEX["failed"])
LAMP_AMBER = QColor(theme.STATE_HEX["waiting"])
LAMP_GREEN = QColor(theme.STATE_HEX["done"])

# 两枚徽标统一底：只画白色线框，不再贴原生彩色 icon。
CHIP_BG = QColor("#373737")
OUTLINE_W = QColor("#FFFFFF")
OUTLINE_K = 25          # 512 画布上的形态学核，约 12px 线宽
CX_STROKE_W = 0.65      # SVG viewBox 24 单位下的描边宽度


def _wb_outline() -> QPixmap:
    """从 WorkBuddy 原图里只取白色图形，再提成线框。

    官方图是绿色渐变圆角块 + 白色猫形实心图案。用户要的是「白色线框部分」，
    所以按亮度抽出白色区域（绿色块亮度约 158，白色约 255，阈值 205 能干净分开），
    再用膨胀减腐蚀取内外轮廓。不重画几何，避免像上次 Codex 那样画错品牌。
    """
    global WB
    if WB is None:
        src = Image.open(ASSETS / "workbuddy.png").convert("RGBA")
        mask = Image.new("L", src.size, 0)
        sp, mp = src.load(), mask.load()
        for y in range(src.height):
            for x in range(src.width):
                r, g, b, a = sp[x, y]
                lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
                mp[x, y] = 255 if (a > 128 and lum > 205) else 0
        WB = _outline_pixmap(mask, OUTLINE_K)
    return WB


def _outline_pixmap(mask: Image.Image, k: int) -> QPixmap:
    m = mask.resize((512, 512), Image.LANCZOS).point(lambda v: 255 if v > 110 else 0)
    dil = m.filter(ImageFilter.MaxFilter(k))
    ero = m.filter(ImageFilter.MinFilter(k))
    edge = ImageChops.lighter(ImageChops.subtract(dil, m), ImageChops.subtract(m, ero))
    rgba = Image.new("RGBA", (512, 512), (255, 255, 255, 0))
    rgba.putalpha(edge.point(lambda v: min(255, int(v * 1.3))))
    buf = io.BytesIO()
    rgba.save(buf, format="PNG")
    pm = QPixmap()
    pm.loadFromData(buf.getvalue())
    return pm


def _cx_renderer(stroke: bool = True) -> QSvgRenderer:
    """Codex 结形：默认描边（白色线框），fill=True 给自检用。"""
    global CX, CX_FILL
    if stroke:
        if CX is None:
            src = (ASSETS / "codex-knot.svg").read_text(encoding="utf-8")
            src = src.replace(
                'fill="#FFFFFF"',
                f'fill="none" stroke="#FFFFFF" stroke-width="{CX_STROKE_W}" stroke-linejoin="round"',
            )
            CX = QSvgRenderer(src.encode("utf-8"))
        if not CX.isValid():
            raise RuntimeError("codex-knot.svg 描边版无法解析")
        return CX
    if CX_FILL is None:
        CX_FILL = QSvgRenderer(str(ASSETS / "codex-knot.svg"))
    return CX_FILL


def codex_fill_mask() -> Image.Image:
    """给自检用的实心掩膜：线框形状要跟它对齐才有意义。"""
    img = QImage(512, 512, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    _cx_renderer(stroke=False).render(p, QRectF(0, 0, 512, 512))
    p.end()
    buf = img.bits()
    out = Image.new("L", (512, 512), 0)
    op = out.load()
    for y in range(512):
        row = y * 512 * 4
        for x in range(512):
            i = row + x * 4
            b, g, r, a = buf[i], buf[i + 1], buf[i + 2], buf[i + 3]
            op[x, y] = 255 if (a > 110 and min(b, g, r) > 110) else 0
    return out


WB = None
CX = None
CX_FILL = None


def rounded(p: QPainter, rect: QRectF, radius: float, fill: QColor, edge: QColor | None = None, edge_w: float = 0.0):
    p.setBrush(fill)
    if edge is not None and edge_w > 0:
        p.setPen(QPen(edge, edge_w))
    else:
        p.setPen(Qt.NoPen)
    p.drawRoundedRect(rect, radius, radius)


def _shade(c: QColor, f: float) -> QColor:
    return QColor(
        min(255, int(c.red() * f)),
        min(255, int(c.green() * f)),
        min(255, int(c.blue() * f)),
    )


def lamp(p: QPainter, cx: float, cy: float, r: float, color: QColor, lit: bool) -> None:
    """三盏灯都用状态灯实色，不再把未点亮的压成 alpha 52 的暗块。

    用户反馈「红绿信号灯不够明显」的根因就在这：两盏没亮的灯几乎看不见，
    结果整张图标只剩一个绿点。现在未点亮的也是饱和实色（只压 18% 明度），
    亮的那盏额外加光晕和白色高光来区分"当前状态"。
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
        p.setBrush(_shade(color, 0.82))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), r, r)

        rim = _shade(color, 1.18)
        rim.setAlpha(190)
        p.setPen(QPen(rim, r * 0.07))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r * 0.95, r * 0.95)

    # 灯泡外圈：三盏灯在深色灯箱里各自有个边界，不会被光晕连成一片
    p.setPen(QPen(QColor(255, 255, 255, 42), r * 0.05))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(cx, cy), r * 1.06, r * 1.06)


def chip_frame(p: QPainter, rect: QRectF) -> QRectF:
    """两枚徽标共用同一块深灰底，只承载白色线框。"""
    r = rect.width() * 0.24
    rounded(p, rect, r, CHIP_BG)
    p.setPen(QPen(QColor(255, 255, 255, 34), 3))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(rect, r, r)
    pad = rect.width() * 0.15
    return rect.adjusted(pad, pad, -pad, -pad)


def wb_chip(p: QPainter, rect: QRectF) -> None:
    """WorkBuddy：只留白色线框，不贴绿色原生 icon。"""
    inner = chip_frame(p, rect)
    pm = _wb_outline()
    p.drawPixmap(inner, pm, QRectF(0, 0, pm.width(), pm.height()))


def codex_chip(p: QPainter, rect: QRectF) -> None:
    """Codex：现行六瓣结的白色描边线稿（同一矢量，fill 改成 none）。"""
    inner = chip_frame(p, rect)
    p.save()
    _cx_renderer(stroke=True).render(p, inner)
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
            (280, cy, 110, LAMP_RED, False),
            (512, cy, 110, LAMP_AMBER, False),
            (744, cy, 110, LAMP_GREEN, True),
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
            (cx, 250, 116, LAMP_RED, False),
            (cx, 512, 116, LAMP_AMBER, False),
            (cx, 774, 116, LAMP_GREEN, True),
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
            (280, cy, 110, LAMP_RED, False),
            (512, cy, 110, LAMP_AMBER, False),
            (744, cy, 110, LAMP_GREEN, True),
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
    WB = _wb_outline()

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
