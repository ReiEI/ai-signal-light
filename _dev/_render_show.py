# -*- coding: utf-8 -*-
"""离屏渲染「显示元素」开关矩阵：三套版式 × 各种区块组合，出 PNG 供肉眼校验。

检查点：
  1. 关掉任务区后，指标区要顶到卡片顶部且不贴圆角
  2. 关掉指标区后，卡片要缩到任务区高度，底部不留一条黑边
  3. 只留 2~3 列指标时，列宽要重新均分，不能留空位
  4. 有散热条时，散热条要紧贴内容下沿，不能被卡片边缘切掉
"""
import os
import sys
import tempfile
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="aisl-show-"))
os.environ["AISIGNAL_HOME"] = str(_TMP / "AIStatusLight")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

import cooling as cool
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
SINGLE = [st.Slot("workbuddy", "r1", st.RUNNING, "优化信号灯任务名称",
                  "改成工作空间 + 对话名两行显示", NOW - 95, 999, NOW, "AI学习")]
MULTI = [
    st.Slot("workbuddy", "r1", st.RUNNING, "优化信号灯任务名称",
            "改成工作空间 + 对话名两行显示", NOW - 95, 999, NOW, "AI学习"),
    st.Slot("workbuddy", "r2", st.WAITING, "抓取竞品价格",
            "等待你确认是否覆盖同名文件", NOW - 260, 999, NOW, "消费理财"),
]

# (标签, 设置覆盖, 任务集合, 是否开散热)
CASES = [
    ("all", {}, SINGLE, False),
    ("task_off", {"show_task": False}, SINGLE, False),
    ("metrics_off", {"show_metrics": False}, SINGLE, False),
    ("two_cols", {"show_cpu": False, "show_net": False}, SINGLE, False),
    ("one_col", {"show_cpu": False, "show_mem": False, "show_net": False}, SINGLE, False),
    ("all_cool", {}, SINGLE, True),
    ("task_off_cool", {"show_task": False}, SINGLE, True),
    ("metrics_off_cool", {"show_metrics": False}, SINGLE, True),
    ("multi3", {}, MULTI, False),
    ("multi3_metrics_off", {"show_metrics": False}, MULTI, False),
]


def shot(name: str) -> None:
    app.processEvents()
    pm = QPixmap(w.size())
    pm.fill(Qt.transparent)
    w.render(pm)
    out = ROOT / "_dev" / f"_show_{name}.png"
    pm.save(str(out), "PNG")
    print(f"{out.name:30s} {pm.width():>4d}x{pm.height():<4d}")


for style in ("standard", "bar", "stack"):
    for tag, over, rows, with_cool in CASES:
        w.settings.update(dict(st.DEFAULT_SETTINGS))
        w.settings.update(over)
        w.set_style(style)
        w.rows = list(rows)
        w._row_keys = [M._row_key(s) for s in rows]
        w.status = st.compose(rows, st.Status())
        w.cool = (cool.Session("ec", 1234, NOW - 134, NOW + 1666, NOW, 15, 0, "EC 接口可读写")
                  if with_cool else None)
        w._apply_size()
        shot(f"{style}_{tag}")
