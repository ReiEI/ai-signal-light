# -*- coding: utf-8 -*-
"""开发期工具：离屏渲染三套版式与四档尺寸，拼对照图。不参与运行。"""
import os
import sys
import tempfile
import time
from pathlib import Path

# 隔离数据目录：渲染脚本不该污染真实状态，也不该被真实信号干扰
os.environ["AISIGNAL_HOME"] = str(Path(tempfile.gettempdir()) / "aisignal-render")

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

import main as M  # noqa: E402
import state as st  # noqa: E402

BG = QColor(30, 36, 48)

app = QApplication(sys.argv)
w = M.SignalLight(demo=False)
w.setAttribute(Qt.WA_DontShowOnScreen, True)
w.show()
app.processEvents()
for _ in range(3):
    w.metrics.sample()
    time.sleep(0.22)

# 固定一组读数，截图才能对比
w.thermal.stop()
w._res.stop()  # 暂停实时采样，否则覆盖下面的固定值
w.thermal.temp = 51.0
w.thermal.label = "GPU"
w.thermal.source = "adl"
w.thermal.probed = True
w.metrics.cpu = 23.0
w.metrics.mem = 58.0
w.metrics.mem_used = int(9.4 * 1024**3)
w.metrics.mem_total = int(16 * 1024**3)
w.metrics.cores = 16
w.metrics.down = 1.2 * 1024 * 1024
w.metrics.up = 165 * 1024


def shot(style, scale, state, title, detail, peers=0):
    w.style = style
    w.scale = scale
    w._apply_size()
    w._demo = True
    w.reader._last = st.Status(state, title, detail, time.time(), 9999, False, 0.0, peers)
    w.reader._last_mtime = 1e18
    w.status = w.reader._last
    w._on_state_change()
    w.update()
    app.processEvents()
    pm = QPixmap(w.width(), w.height())
    pm.fill(BG)
    w.render(pm)
    return pm


def sheet(items, cols, gap=12, label_h=16, name="_styles.png"):
    rows = (len(items) + cols - 1) // cols
    cell_w = max(pm.width() for pm in items) + gap * 2
    cell_h = max(pm.height() for pm in items) + gap * 2 + label_h
    out = QPixmap(cell_w * cols, cell_h * rows)
    out.fill(QColor(22, 26, 34))
    p = QPainter(out)
    p.setFont(M._f(9))
    for i, pm in enumerate(items):
        cx = (i % cols) * cell_w + gap
        cy = (i // cols) * cell_h + gap
        p.setPen(QColor(150, 160, 176))
        p.drawText(cx, cy, f"[{i + 1}]")
        p.drawPixmap(cx, cy + label_h - 4, pm)
    p.end()
    path = Path(__file__).parent / name
    out.save(str(path), "PNG")
    print(path, out.width(), out.height())


cases = [
    (M.STYLE_STANDARD, 1.0, st.IDLE, "", ""),
    (M.STYLE_STANDARD, 1.0, st.RUNNING, "生成 Q3 运营报告", "WorkBuddy · 正在处理", 1),
    (M.STYLE_STANDARD, 1.0, st.FAILED, "抓取竞品价格", "接口返回 429"),
    (M.STYLE_BAR, 1.0, st.RUNNING, "生成 Q3 运营报告", "正在读取数据表"),
    (M.STYLE_BAR, 1.0, st.WAITING, "批量重命名 128 个文件", "等待你确认操作"),
    (M.STYLE_BAR, 1.0, st.IDLE, "", ""),
    (M.STYLE_STACK, 1.0, st.RUNNING, "重构 state.py 槽位逻辑并补测试", "Codex · 正在执行", 1),
    (M.STYLE_STACK, 1.0, st.DONE, "生成 Q3 运营报告", "用时 42s"),
    (M.STYLE_STACK, 1.0, st.IDLE, "", ""),
]
sheet([shot(*c) for c in cases], 3, name="_styles.png")

scales = [shot(M.STYLE_STANDARD, k, st.RUNNING, "生成 Q3 运营报告", "WorkBuddy · 正在处理")
          for k, _n in M.SCALE_STEPS]
sheet(scales, 2, name="_scales.png")

st.write_state(st.IDLE)
