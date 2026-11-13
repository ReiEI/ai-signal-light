# -*- coding: utf-8 -*-
"""离屏渲染多任务布局：三种版式 × 1/2/3/5 个任务，出 PNG 供肉眼校验。"""
import os, sys, tempfile, time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="aisl-multi-"))
os.environ["AISIGNAL_HOME"] = str(_TMP / "AIStatusLight")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

import main as M
import state as st

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


def row(i, state, work, name, detail, age):
    return st.Slot("workbuddy", f"r{i}", state, name, detail,
                   NOW - age, 999, NOW, work)


SETS = {
    1: [row(1, st.RUNNING, "示例项目", "优化信号灯任务名称", "改成工作空间 + 对话名两行显示", 95)],
    2: [row(1, st.RUNNING, "示例项目", "优化信号灯任务名称", "改成工作空间 + 对话名两行显示", 95),
        row(2, st.WAITING, "数据分析", "抓取竞品价格", "等待你确认是否覆盖同名文件", 260)],
    3: [row(1, st.RUNNING, "示例项目", "优化信号灯任务名称", "改成工作空间 + 对话名两行显示", 95),
        row(2, st.WAITING, "数据分析", "抓取竞品价格", "等待你确认是否覆盖同名文件", 260),
        row(3, st.RUNNING, "工作-202602-竞品调研", "采集竞品评论", "正在解析第 38 页", 20)],
    5: [row(i, st.RUNNING, f"工作{i}", f"对话{i}", f"正在处理第 {i} 项", 30 * i) for i in range(1, 6)],
}


def shot(name):
    app.processEvents()
    pm = QPixmap(w.size())
    pm.fill(Qt.transparent)
    w.render(pm)
    out = ROOT / "_dev" / name
    pm.save(str(out), "PNG")
    print(f"{out.name:34s} {pm.width()}x{pm.height()}")


for style in ("standard", "bar", "stack"):
    for n, rows in SETS.items():
        w.set_style(style)
        w.rows = list(rows)
        w._row_keys = [M._row_key(s) for s in rows]
        w.status = st.compose(w.rows, st.Status())
        w._apply_size()
        shot(f"_multi_{style}_{n}.png")
