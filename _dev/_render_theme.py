# -*- coding: utf-8 -*-
"""离屏渲染浅/深主题对照图：三套版式各一张 + 散热面板各一张。"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["AISIGNAL_HOME"] = str(Path(tempfile.mkdtemp(prefix="aisl-theme-")) / "AIStatusLight")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import cooling as cool  # noqa: E402
import cooling_ui  # noqa: E402
import main as M  # noqa: E402
import state as st  # noqa: E402

app = QApplication(sys.argv)
w = M.SignalLight(demo=False)
w.setAttribute(Qt.WA_DontShowOnScreen, True)
w.show()
for t in (w._anim, w._res, w._poll, w._cool):
    t.stop()
app.processEvents()
w.metrics.sample()
w.thermal.temp = 53.0
w.thermal.label = "GPU"

NOW = time.time()
ROWS = [
    st.Slot("workbuddy", "r1", st.RUNNING, "优化信号灯任务名称",
            "改成工作空间 - 对话名两行显示", NOW - 95, 999, NOW, "AI学习"),
    st.Slot("codex", "r2", st.WAITING, "抓取竞品价格",
            "等待确认是否覆盖同名文件", NOW - 260, 999, NOW, "消费理财"),
]


def shot() -> QPixmap:
    app.processEvents()
    pm = QPixmap(w.size())
    pm.fill(Qt.transparent)
    w.render(pm)
    return pm


THEMES = (("dark", "深色 / Windows dark"), ("light", "浅色 / Windows light"))
STYLES = ("standard", "bar", "stack")

# 先渲染一遍拿尺寸，避免硬编码宽高
grab = {}
for tid, _ in THEMES:
    w.set_theme(tid)
    for s in STYLES:
        w.set_style(s)
        w.rows = list(ROWS)
        w._row_keys = [M._row_key(x) for x in ROWS]
        w.status = st.compose(ROWS, st.Status())
        w.cool = None
        w.settings.update({k: True for k in st.SHOW_KEYS})
        w._apply_size()
        grab[(tid, s)] = shot()

# 散热条单独一组：它用琥珀半透明底 + 虚线边，浅色主题最容易在这里翻车
strip = {}
for tid, _ in THEMES:
    w.set_theme(tid)
    for s in ("standard", "stack"):
        w.set_style(s)
        w.rows = list(ROWS)
        w._row_keys = [M._row_key(x) for x in ROWS]
        w.status = st.compose(ROWS, st.Status())
        w.cool = cool.Session("ec", 1234, NOW - 134, NOW + 1666, NOW, 15, 0, "EC 接口可读写")
        w._apply_size()
        strip[(tid, s)] = shot()
w.cool = None

col_w = max(pm.width() for pm in grab.values()) + 60
row_h = max(pm.height() for pm in grab.values()) + 86
W, H = col_w * 3 + 40, row_h * 2 + 60
sheet = QPixmap(W, H)
sheet.fill(QColor("#8A909B"))
p = QPainter(sheet)
p.setRenderHint(QPainter.Antialiasing, True)
f = QFont("Microsoft YaHei UI", 10)
p.setFont(f)
for ri, (tid, label) in enumerate(THEMES):
    y = 30 + ri * row_h
    p.setPen(QColor("#10141A"))
    p.drawText(QRectF(24, y, col_w * 3, 26), Qt.AlignLeft | Qt.AlignVCenter, label)
    for ci, s in enumerate(STYLES):
        x = 24 + ci * col_w
        p.setPen(QColor("#2A3038"))
        p.drawText(QRectF(x, y + 26, col_w - 40, 22), Qt.AlignLeft | Qt.AlignVCenter, s)
        pm = grab[(tid, s)]
        p.drawPixmap(QPointF(x + 10, y + 52), pm)
p.end()
out = ROOT / "_dev" / "_theme_cards.png"
sheet.save(str(out), "PNG")
print("saved", out, sheet.size())

# 散热面板：QSS 是否跟着主题走
panel = cooling_ui.CoolingPanel(w.cooling, w.metrics, w.thermal)
panel.resize(492, 528)
panel.setAttribute(Qt.WA_DontShowOnScreen, True)
panel.show()
app.processEvents()
pw, ph = 492, 528
psheet = QPixmap(pw * 2 + 60, ph + 60)
psheet.fill(QColor("#8A909B"))
pp = QPainter(psheet)
pp.setRenderHint(QPainter.Antialiasing, True)
for i, (tid, label) in enumerate(THEMES):
    M.PAL.set(tid)
    panel.apply_theme()
    app.processEvents()
    pm = panel.grab()
    pp.drawPixmap(QPointF(20 + i * (pw + 20), 40), pm)
    pp.setPen(QColor("#10141A"))
    pp.drawText(QRectF(20 + i * (pw + 20), 8, pw, 26), Qt.AlignLeft | Qt.AlignVCenter, label)
pp.end()
pout = ROOT / "_dev" / "_theme_panel.png"
psheet.save(str(pout), "PNG")
print("saved", pout, psheet.size())
panel.close()

# 散热条对照
sw = max(pm.width() for pm in strip.values()) + 60
sh = max(pm.height() for pm in strip.values()) + 86
ssheet = QPixmap(sw * 2 + 40, sh * 2 + 50)
ssheet.fill(QColor("#8A909B"))
sp = QPainter(ssheet)
sp.setRenderHint(QPainter.Antialiasing, True)
sp.setFont(f)
for ri, (tid, label) in enumerate(THEMES):
    y = 24 + ri * sh
    sp.setPen(QColor("#10141A"))
    sp.drawText(QRectF(20, y, sw * 2, 24), Qt.AlignLeft | Qt.AlignVCenter, label + " · 散热条")
    for ci, s in enumerate(("standard", "stack")):
        x = 20 + ci * sw
        sp.setPen(QColor("#2A3038"))
        sp.drawText(QRectF(x, y + 24, sw - 40, 20), Qt.AlignLeft | Qt.AlignVCenter, s)
        sp.drawPixmap(QPointF(x + 10, y + 48), strip[(tid, s)])
sp.end()
sout = ROOT / "_dev" / "_theme_strip.png"
ssheet.save(str(sout), "PNG")
print("saved", sout, ssheet.size())
