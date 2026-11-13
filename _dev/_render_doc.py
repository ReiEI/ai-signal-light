# -*- coding: utf-8 -*-
"""渲染 README 用效果图（2 倍像素密度，避免在高分屏/网页里发虚）。

输出到 docs/screenshot.png：标准卡 + 3 个并行任务 + 散热条，
一张图同时说明多任务、任务命名、指标区、散热条四件事。
"""
import os
import sys
import tempfile
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="aisl-doc-"))
os.environ["AISIGNAL_HOME"] = str(_TMP / "AIStatusLight")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

import cooling as cool
import main as M
import state as st

DPR = 2

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

now = time.time()
rows = [
    st.Slot("workbuddy", "r1", st.RUNNING, "优化信号灯任务名称",
            "改成工作空间 + 对话名两行显示", now - 95, 999, now, "AI学习"),
    st.Slot("workbuddy", "r2", st.WAITING, "抓取竞品价格",
            "等待你确认是否覆盖同名文件", now - 260, 999, now, "消费理财"),
    st.Slot("workbuddy", "r3", st.RUNNING, "采集竞品评论",
            "正在解析第 38 页", now - 20, 999, now, "Alexa提问采集"),
]

w.set_style("standard")
w.rows = list(rows)
w._row_keys = [M._row_key(s) for s in rows]
w.status = st.compose(rows, st.Status())
w.cool = cool.Session("ec", 1234, now - 134, now + 1666, now, 15, 0, "EC 接口可读写")
w._apply_size()
app.processEvents()

size = w.size()
pm = QPixmap(size.width() * DPR, size.height() * DPR)
pm.setDevicePixelRatio(DPR)
pm.fill(Qt.transparent)
w.render(pm)

out_dir = ROOT / "docs"
out_dir.mkdir(exist_ok=True)
out = out_dir / "screenshot.png"
pm.save(str(out), "PNG")
print(f"{out}  {pm.width()}x{pm.height()} (逻辑 {size.width()}x{size.height()})")
