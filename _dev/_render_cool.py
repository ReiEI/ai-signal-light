# -*- coding: utf-8 -*-
"""离屏渲染散热条：三种版式 × 有/无会话，直接出 PNG 供肉眼校验。"""
import os, sys, tempfile, time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="aisl-render-"))
os.environ["AISIGNAL_HOME"] = str(_TMP / "AIStatusLight")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

import main as M
import state as st
import cooling as cool

app = QApplication(sys.argv)
w = M.SignalLight(demo=False)
w.setAttribute(Qt.WA_DontShowOnScreen, True)
w.show()
w._cool.stop()   # 关掉会话轮询，否则定时器会把 w.cool 清回 None
app.processEvents()

st.write_state("running", "生成 Q3 运营复盘", "WorkBuddy · 正在处理", 60)
w._poll_state()
w.metrics.sample(); w.thermal.temp = 53.0; w.thermal.label = "GPU"

def shot(name):
    app.processEvents()
    pm = QPixmap(w.size())
    pm.fill(Qt.transparent)
    w.render(pm)
    out = ROOT / "_dev" / name
    pm.save(str(out), "PNG")
    print(out, pm.width(), pm.height())

for style in ("standard", "bar", "stack"):
    w.set_style(style)
    w.cool = None
    w._apply_size()
    app.processEvents()
    shot(f"_cool_{style}_off.png")
    w.cool = cool.Session("ec", 1234, time.time() - 134, time.time() + 1666, time.time(), 15, 0, "EC 接口可读写")
    w._apply_size()
    app.processEvents()
    shot(f"_cool_{style}_on.png")
