# -*- coding: utf-8 -*-
"""散热 UI 冒烟测试：菜单、散热条尺寸联动、停止热区、面板。

这里测的都是"看起来能跑但一用就露馅"的地方：
  * 会话出现/消失时卡片高度没跟着变，散热条被裁掉一半；
  * "停止"画出来了但点不到，或者点了反而把窗口拖走；
  * 面板按钮在能力不足时仍然可点，点下去只弹一句报错。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="aisl-coolui-"))
os.environ["AISIGNAL_HOME"] = str(_TMP / "AIStatusLight")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication

import cooling as cool  # noqa: E402
import main as M  # noqa: E402
import state as st  # noqa: E402

ok = 0
bad = 0


def check(name: str, cond: bool, detail: object = "") -> None:
    global ok, bad
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        bad += 1
        print(f"  FAIL  {name}  {detail}")


app = QApplication(sys.argv)
w = M.SignalLight(demo=False)
w.setAttribute(Qt.WA_DontShowOnScreen, True)
w.show()
w._cool.stop()          # 冻结定时器，让测试自己控制状态
w._anim.stop()
app.processEvents()


def write_session(mode: str = "ec", beat_ago: float = 0.0, minutes: float = 30.0) -> None:
    cool.ensure_dir()
    now = time.time()
    cool.SESSION_FILE.write_text(json.dumps({
        "mode": mode, "pid": 999, "started": now - 120,
        "deadline": now + minutes * 60, "beat": now - beat_ago,
        "cycles": 14, "workers": 0, "note": "EC 接口可读写", "error": "",
    }), encoding="utf-8")


print("1) 右键菜单里有散热入口，且不可用项带原因")
m = w._menu()
texts = [a.text() for a in m.actions() if a.text()]
check("菜单含散热", "散热" in texts, texts)
sub = next(a.menu() for a in m.actions() if a.text() == "散热")
items = [a.text() for a in sub.actions() if a.text()]
check("散热含面板入口", "散热面板…" in items, items)
check("散热含三种模式", any("风扇全速" in t for t in items) and any("持续负载" in t for t in items), items)
check("散热含停止项", "停止并恢复" in items, items)
stop_act = next(a for a in sub.actions() if a.text() == "停止并恢复")
check("无会话时停止项禁用", not stop_act.isEnabled())
ec_act = next(a for a in sub.actions() if "风扇全速" in a.text())
check("EC 可用时不禁用", ec_act.isEnabled() or "不可用" in ec_act.text(), ec_act.text())

print("2) 会话出现时卡片变高，消失后还原")
w.set_style("standard")
w.cool = None
w._apply_size()
h_off = w.height()
write_session()
w._poll_cooling()
app.processEvents()
h_on = w.height()
check("标准卡 +30px", h_on - h_off == 30, f"{h_off} -> {h_on}")
check("会话被识别", w.cool is not None and w.cool.mode == "ec")

print("3) 散热条画得出来，停止热区落在卡片内")
pm = QPixmap(w.size())
pm.fill(Qt.transparent)
w.render(pm)
pm.save(str(ROOT / "_dev" / "_ui_strip.png"), "PNG")
check("散热条热区有效", w._stop_rect.isValid() and w._stop_rect.width() > 20, w._stop_rect)

print("4) 点停止热区 = 停止，而不是拖窗口")
fired = {"n": 0}
_orig_stop = w.stop_cooling


def _fake_stop() -> None:
    fired["n"] += 1


w.stop_cooling = _fake_stop  # type: ignore[method-assign]
cx = (w._stop_rect.center().x()) * w.scale
cy = (w._stop_rect.center().y()) * w.scale
ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(cx, cy), QPointF(cx, cy),
                 Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
w.mousePressEvent(ev)
check("命中热区触发停止", fired["n"] == 1, fired)
check("命中热区不启动拖拽", w._drag_from is None, w._drag_from)

ev2 = QMouseEvent(QEvent.MouseButtonPress, QPointF(20, 20), QPointF(20, 20),
                  Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
w.mousePressEvent(ev2)
check("点空白处仍然可以拖", w._drag_from is not None)
w._drag_from = None
w.stop_cooling = _orig_stop  # type: ignore[method-assign]

print("5) 三种版式都能在会话态下绘制，横条高度不变")
for style in ("standard", "bar", "stack"):
    w.set_style(style)
    w.cool = None
    w._apply_size()
    h0 = w.height()
    write_session()
    w._poll_cooling()
    app.processEvents()
    h1 = w.height()
    pm2 = QPixmap(w.size())
    pm2.fill(Qt.transparent)
    w.render(pm2)
    pm2.save(str(ROOT / "_dev" / f"_ui_strip_{style}.png"), "PNG")
    expect = {"standard": 30, "bar": 0, "stack": 34}[style]
    check(f"{style} 增量 {expect}px", h1 - h0 == expect, f"{h0} -> {h1}")
    check(f"{style} 散热中都有停止热区", w._stop_rect.isValid(), w._stop_rect)

print("6) 心跳过期的会话不算活着")
write_session(beat_ago=30)
w._poll_cooling()
check("过期会话被丢弃", w.cool is None)
check("过期会话使卡片回落到原高度", w.height() == 248, w.height())
check("无会话时不残留停止热区", not w._stop_rect.isValid(), w._stop_rect)

print("7) 面板：能力不足时按钮禁用，有会话时不能再启动")
from cooling_ui import CoolingPanel  # noqa: E402

panel = CoolingPanel(w.cooling, w.metrics, w.thermal)
panel.setAttribute(Qt.WA_DontShowOnScreen, True)
panel.show()
app.processEvents()
panel._tick()
pm3 = QPixmap(panel.size())
pm3.fill(Qt.transparent)
panel.render(pm3)
pm3.save(str(ROOT / "_dev" / "_ui_panel.png"), "PNG")
check("面板能构建并绘制", pm3.width() > 400 and pm3.height() > 300, f"{pm3.width()}x{pm3.height()}")
check("面板显示 EC 状态", "EC" in panel.lb_cap.text(), panel.lb_cap.text())

write_session()
w._poll_cooling()
panel._sync_controls()
check("有会话时 EC 按钮禁用", not panel.btn_ec.isEnabled())
check("有会话时停止按钮可用", panel.btn_stop.isEnabled())

cool.SESSION_FILE.unlink(missing_ok=True)
w._poll_cooling()
panel._tick()
check("无会话时停止按钮禁用", not panel.btn_stop.isEnabled())

print("8) 关掉面板不会顺手停掉会话")
write_session()
w._poll_cooling()
panel.close()
app.processEvents()
check("面板关闭后会话仍在", w.cool is not None)

print()
print(f"结果: {ok} 通过 / {bad} 失败")
cool.SESSION_FILE.unlink(missing_ok=True)
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(0 if bad == 0 else 1)
