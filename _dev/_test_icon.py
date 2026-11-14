# -*- coding: utf-8 -*-
"""图标资产与接线自检。

防止三类静默回归：
  1. 打包脚本被改回去，exe 又变回 PyInstaller 默认图标
  2. ICO 少帧或某帧空白，任务栏 16px 变成白板
  3. 只剩 exe 资源图标，Qt 窗口仍然落回默认图标
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from PIL import Image  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from _dev import _make_icon as mk  # noqa: E402

ok = 0
bad = 0


def check(name: str, cond: bool, detail: object = "") -> None:
    global ok, bad
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        bad += 1
        print(f"  FAIL  {name}  -> {detail!r}")


app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
mk.WB = mk._wb_outline()

print("1) 资产文件")
png = ROOT / "assets" / "signallight.png"
ico = ROOT / "assets" / "signallight.ico"
wb = ROOT / "assets" / "workbuddy.png"
check("png 存在", png.exists(), png)
check("ico 存在", ico.exists(), ico)
check("品牌原图存在", wb.exists(), wb)

with Image.open(png) as im:
    check("母版 1024 方图", im.size == (1024, 1024), im.size)
    check("母版带 alpha", im.mode == "RGBA", im.mode)

with Image.open(ico) as im:
    sizes = sorted(im.ico.sizes())
expected = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]
check("9 档尺寸齐全", sizes == expected, sizes)

print("2) 小尺寸帧不是空白")
with Image.open(ico) as im:
    for size in (16, 32, 48, 256):
        frame = im.ico.getimage((size, size)).convert("RGBA")
        # 用直方图统计非透明像素，避免 Pillow 的 getdata 弃用告警
        colored = sum(frame.getchannel("A").histogram()[17:])
        check(f"{size}px 有可见像素", colored > size * size * 0.5, colored)

print("3) 三灯配色可分辨")
img = mk.render("A", 64)


def px(x1024: float, y1024: float):
    c = img.pixelColor(int(x1024 / 1024 * 64), int(y1024 / 1024 * 64))
    return c.red(), c.green(), c.blue()


r, g, b = px(280, 347)
check("红灯偏红", r > g and r > b, (r, g, b))
r, g, b = px(512, 347)
check("黄灯偏黄", r > b and g > b, (r, g, b))
r, g, b = px(744, 347)
check("绿灯偏绿", g > r and g > b, (r, g, b))

print("4) 三方案都能渲染")
for name in "ABC":
    im = mk.render(name, 128)
    check(f"方案 {name} 非空白", not im.isNull() and im.width() == 128, im.size())

print("5) 打包与运行时接线")
cmd = (ROOT / "打包.cmd").read_text(encoding="utf-8")
check("三个 exe 都带 --icon", cmd.count("--icon") == 3, cmd.count("--icon"))
check("主程序随包携带 ico", "--add-data" in cmd and "signallight.ico;assets" in cmd)

main_src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
check("源码定义 app_icon()", "def app_icon() -> QIcon:" in main_src)
check("冻结态查 _MEIPASS", "_MEIPASS" in main_src)
check("应用级窗口图标", "app.setWindowIcon(app_icon())" in main_src)
check("窗口级图标", "w.setWindowIcon(app_icon())" in main_src)

import main as main_mod  # noqa: E402

icon = main_mod.app_icon()
check("源码态能拿到非空 QIcon", not icon.isNull())
check("QIcon 覆盖 16/32/256", all(not icon.pixmap(s, s).isNull() for s in (16, 32, 256)))

print("6) 已打包 exe 的内嵌图标（dist 存在才跑）")
dist = ROOT / "dist"
exes = ["SignalLight.exe", "signal.exe", "fan_agent.exe"]
if all((dist / e).exists() for e in exes):
    import hashlib  # noqa: E402
    import struct  # noqa: E402

    import pefile  # noqa: E402

    raw = ico.read_bytes()
    count = struct.unpack_from("<H", raw, 4)[0]
    frame_hashes = set()
    for i in range(count):
        off = 6 + i * 16
        _, _, _, _, _, _, size, start = struct.unpack_from("<BBBBHHII", raw, off)
        frame_hashes.add(hashlib.sha256(raw[start : start + size]).hexdigest())

    for e in exes:
        pe = pefile.PE(str(dist / e), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        blobs = [
            pe.get_data(l.data.struct.OffsetToData, l.data.struct.Size)
            for top in pe.DIRECTORY_ENTRY_RESOURCE.entries
            if top.id == 3
            for entry in top.directory.entries
            for l in entry.directory.entries
        ]
        matched = sum(hashlib.sha256(b).hexdigest() in frame_hashes for b in blobs)
        check(f"{e} 内嵌 9 帧且逐帧匹配", len(blobs) == count == matched == 9, (len(blobs), matched))
else:
    print("  SKIP  dist 里没有完整的三个 exe（源码态自检不做二进制检查）")

print("7) Codex 标记：必须按参考图的六瓣结，别再退回渐变团块 + >_")
from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

svg_file = ROOT / "assets" / "codex-knot.svg"
ref_file = ROOT / "assets" / "codex-knot-ref.png"
check("结形矢量资产存在", svg_file.exists(), svg_file)
check("参考掩膜资产存在", ref_file.exists(), ref_file)
import xml.etree.ElementTree as ET  # noqa: E402

svg_text = svg_file.read_text(encoding="utf-8")
svg_root = ET.fromstring(svg_text)
attr_values = [v for el in svg_root.iter() for v in el.attrib.values()]
check(
    "矢量显式白色（不依赖动态色值）",
    svg_root.attrib.get("fill") == "#FFFFFF" and not any("currentColor" in v for v in attr_values),
    svg_root.attrib.get("fill"),
)
check("矢量保持 24×24 viewBox", svg_root.attrib.get("viewBox") == "0 0 24 24", svg_root.attrib.get("viewBox"))
check("SVG 可解析", QSvgRenderer(str(svg_file)).isValid())

gen_src = (ROOT / "_dev" / "_make_icon.py").read_text(encoding="utf-8")
chip_src = gen_src.split("def codex_chip", 1)[1].split("\ndef ", 1)[0]
check("生成器不再引用蓝紫渐变", "CX_TOP" not in gen_src and "QLinearGradient" not in gen_src)
check("codex_chip 改由矢量描边渲染", "_cx_renderer(stroke=True).render" in chip_src and "QSvgRenderer" in gen_src)
check("codex_chip 不再自绘团块/提示符路径", "QPainterPath" not in chip_src and "chev" not in chip_src)
check("两枚徽标共用线框底板", "def chip_frame(" in gen_src and gen_src.count("chip_frame(p, rect)") == 2)


def _white_mask(qimg: QImage) -> Image.Image:
    buf = qimg.bits()
    w, h = qimg.width(), qimg.height()
    out = Image.new("L", (w, h), 0)
    op = out.load()
    for y in range(h):
        row = y * w * 4
        for x in range(w):
            i = row + x * 4
            b, g, r, a = buf[i], buf[i + 1], buf[i + 2], buf[i + 3]
            op[x, y] = 255 if (a > 110 and min(b, g, r) > 190) else 0
    return out


def _norm(m: Image.Image, n: int = 256) -> Image.Image:
    c = m.crop(m.getbbox())
    s = min(n / c.width, n / c.height)
    c = c.resize((round(c.width * s), round(c.height * s)), Image.LANCZOS)
    out = Image.new("L", (n, n), 0)
    out.paste(c, ((n - c.width) // 2, (n - c.height) // 2))
    return out.point(lambda v: 255 if v > 110 else 0)


def _iou(a: Image.Image, b: Image.Image) -> float:
    aa, bb = a.tobytes(), b.tobytes()
    inter = sum(1 for u, v in zip(aa, bb) if u and v)
    union = sum(1 for u, v in zip(aa, bb) if u or v)
    return inter / union if union else 0.0


def render_chip(fn) -> QImage:
    """渲染真正出货的 chip，而不是单独测素材文件。"""
    q = QImage(512, 512, QImage.Format_ARGB32_Premultiplied)
    q.fill(QColor(0, 0, 0, 0))
    painter = QPainter(q)
    painter.setRenderHint(QPainter.Antialiasing, True)
    fn(painter, QRectF(0, 0, 512, 512))
    painter.end()
    return q


def white_ratio(q: QImage) -> float:
    return sum(_white_mask(q).histogram()[1:]) / (512 * 512)


def colored_samples(q: QImage) -> int:
    """品牌彩色块留下的像素：白线框 + 深灰底不该有任何高饱和颜色。"""
    n = 0
    for y in range(0, 512, 3):
        for x in range(0, 512, 3):
            c = q.pixelColor(x, y)
            if c.alpha() > 110 and max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) > 40:
                n += 1
    return n


chip = render_chip(mk.codex_chip)
wb_chip = render_chip(mk.wb_chip)

tile = chip.pixelColor(256, 8)
check(
    "徽标底色是深灰，不是白底或蓝紫渐变",
    tile.alpha() > 200 and max(tile.red(), tile.green(), tile.blue()) < 100,
    (tile.red(), tile.green(), tile.blue(), tile.alpha()),
)
hole = chip.pixelColor(256, 256)
check("六瓣结中央镂空", max(hole.red(), hole.green(), hole.blue()) < 100, (hole.red(), hole.green(), hole.blue()))

r_cx, r_wb = white_ratio(chip), white_ratio(wb_chip)
check("Codex 徽标是白色线框而非实心填充", 0.05 < r_cx < 0.22, round(r_cx, 4))
check("WorkBuddy 徽标是白色线框而非原生 icon", 0.05 < r_wb < 0.22, round(r_wb, 4))
check("两枚徽标都不含品牌彩色块", colored_samples(chip) == 0 and colored_samples(wb_chip) == 0,
      (colored_samples(chip), colored_samples(wb_chip)))

mask = _norm(_white_mask(chip))
rot60 = mask.rotate(60, resample=Image.BICUBIC).point(lambda v: 255 if v > 110 else 0)
check("线框结形具备 60° 旋转对称", _iou(mask, rot60) > 0.85, round(_iou(mask, rot60), 4))

with Image.open(ref_file) as im:
    check("参考掩膜是 256 灰度图", im.size == (256, 256) and im.mode == "L", (im.size, im.mode))
    ref_mask = im.convert("L")
# 线框版与实心参考图直接算 IoU 会偏低（描边只覆盖边缘），
# 所以用同一矢量的实心掩膜对形状，另用白色面积确认出货的是线框。
fill_mask = _norm(mk.codex_fill_mask())
score = _iou(ref_mask, fill_mask)
check("结形轮廓与参考图形状一致（IoU > 0.70）", score > 0.70, round(score, 4))
print(f"      形状 IoU = {score:.4f}  线框白色占比 Codex {r_cx:.3f} / WorkBuddy {r_wb:.3f}")

print("8) 三盏灯取软件状态灯实际颜色")
sys.path.insert(0, str(ROOT / "app"))
import theme  # noqa: E402

for label, got, key in (
    ("红灯 = FAILED 状态色", mk.LAMP_RED, "failed"),
    ("黄灯 = WAITING 状态色", mk.LAMP_AMBER, "waiting"),
    ("绿灯 = DONE 状态色", mk.LAMP_GREEN, "done"),
):
    check(label, got.name().lower() == theme.STATE_HEX[key].lower(), (got.name(), theme.STATE_HEX[key]))

icon = mk.render("A", 128)


def lamp_px(x1024: float, y1024: float):
    c = icon.pixelColor(int(x1024 / 1024 * 128), int(y1024 / 1024 * 128))
    return c.red(), c.green(), c.blue(), c.alpha()


r, g, b, a = lamp_px(280, 347)
check("未点亮红灯仍是不透明饱和红", a == 255 and r > 150 and r > g * 2 and r > b * 2, (r, g, b, a))
r, g, b, a = lamp_px(512, 347)
check("未点亮黄灯仍是不透明饱和黄", a == 255 and r > 150 and g > 100 and b < 60, (r, g, b, a))
r, g, b, a = lamp_px(744, 347)
check("点亮绿灯是状态绿且更亮", a == 255 and g > 180 and g > r and g > b, (r, g, b, a))
check("三灯不再有 alpha 52 的暗块", "setAlpha(52)" not in gen_src)

print(f"\n结果: {ok} 通过 / {bad} 失败")
sys.exit(1 if bad else 0)
