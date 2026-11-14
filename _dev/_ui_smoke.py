# -*- coding: utf-8 -*-
"""开发期工具：UI 冒烟测试。不显示窗口，但把交互代码路径都走一遍。"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# 隔离数据目录：写真实 state.json 会让挂件显示测试残留的假信号
_TEST_HOME = Path(tempfile.gettempdir()) / "aisignal-uitest"
shutil.rmtree(_TEST_HOME, ignore_errors=True)
os.environ["AISIGNAL_HOME"] = str(_TEST_HOME)

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

import main as M  # noqa: E402
import state as st  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {extra}")


app = QApplication(sys.argv)
w = M.SignalLight(demo=False)
w.setAttribute(Qt.WA_DontShowOnScreen, True)
w.show()
app.processEvents()

print("1) 右键菜单构建")
m = w._menu()
acts = [a.text() for a in m.actions() if a.text()]
check("菜单非空", len(acts) >= 5, str(acts))
check("含置顶项", "窗口置顶" in acts, str(acts))
check("含退出项", "退出" in acts, str(acts))
check("含样式/尺寸/温度子菜单", {"样式", "尺寸", "温度"} <= set(acts), str(acts))
check("置顶默认为勾选", any(a.text() == "窗口置顶" and a.isChecked() for a in m.actions()))

sub_style = next(a.menu() for a in m.actions() if a.text() == "样式")
check("样式三选一", len(sub_style.actions()) == 3, len(sub_style.actions()))
sub_scale = next(a.menu() for a in m.actions() if a.text() == "尺寸")
check("尺寸四档", len(sub_scale.actions()) == 4, len(sub_scale.actions()))
sub_temp = next(a.menu() for a in m.actions() if a.text() == "温度")
check("温度菜单列出当前来源", "当前：" in sub_temp.actions()[0].text(), sub_temp.actions()[0].text())
check("来源项不可点击", sub_temp.actions()[0].isEnabled() is False)

print("2) 三套版式：尺寸符合基准定义，且都能画出来")
for sid in M.STYLE_ORDER:
    w.set_style(sid)
    app.processEvents()
    bw, bh = M.BASE_SIZE[sid]
    check(f"{sid} 尺寸 = 基准×{w.scale}", (w.width(), w.height()) == (round(bw * w.scale), round(bh * w.scale)),
          (w.width(), w.height()))
    pm = QPixmap(w.width(), w.height())
    pm.fill(Qt.transparent)
    w.render(pm)
    check(f"{sid} 渲染非空", not pm.isNull() and pm.width() == w.width(), pm.size())
check("当前样式已持久化", st.load_settings()["style"] == M.STYLE_ORDER[-1], st.load_settings()["style"])

print("3) 四档尺寸：等比放大，窗口不越界")
w.set_style(M.STYLE_STANDARD)
for val, _name in M.SCALE_STEPS:
    w.set_scale(val)
    app.processEvents()
    bw, bh = M.BASE_SIZE[M.STYLE_STANDARD]
    check(f"{int(val * 100)}% 尺寸正确",
          (w.width(), w.height()) == (round(bw * val), round(bh * val)), (w.width(), w.height()))
    check(f"{int(val * 100)}% 仍在屏内", w.x() > -1000 and w.y() > -1000, (w.x(), w.y()))
w.set_scale(1.0)

print("4) 双击折叠：标准卡 ↔ 横条")
w.set_style(M.STYLE_STANDARD)
app.processEvents()
h0 = w.height()
w.toggle_collapse()
app.processEvents()
check("折叠成横条", w.style == M.STYLE_BAR, w.style)
check("高度变小", w.height() < h0, f"{h0} -> {w.height()}")
w.toggle_collapse()
app.processEvents()
check("再双击恢复标准卡", w.style == M.STYLE_STANDARD, w.style)
check("高度复原", w.height() == h0, f"{w.height()} vs {h0}")

print("5) 温度文案")
w.thermal.stop()
w.thermal.temp = 51.4
w.thermal.label = "GPU"
w.thermal.probed = True
check("温度取整显示", w._temp_text() == "51°", w._temp_text())
check("注释带传感器名", w._temp_note() == "GPU", w._temp_note())
w.thermal.temp = None
w.thermal.label = ""
check("无读数显示破折号", w._temp_text() == "—", w._temp_text())
check("无传感器提示", w._temp_note() == "无传感器", w._temp_note())
w.thermal.probed = False
check("探测中不误报无传感器", w._temp_note() == "", w._temp_note())
check("横条指标串含温度", "°" in w._micro_metrics() or "—" in w._micro_metrics(), w._micro_metrics())

print("6) 置顶开关（切换窗口标志后窗口仍然有效）")
w.set_on_top(False)
app.processEvents()
w.set_on_top(True)
app.processEvents()
check("置顶切换未崩溃", w.isVisible())

print("7) 托盘图标生成")
ic = w._make_icon(M.PAL.state(st.FAILED))
check("图标非空", not ic.isNull())

print("8) 状态更新驱动托盘提示")
st.write_state(st.WAITING, "等待确认", "请处理", 60)
time.sleep(0.02)
w._demo = False
w._poll_state()
app.processEvents()
check("状态已采纳", w.status.state == st.WAITING, w.status.state)
check("托盘提示含任务名", "等待确认" in (w.tray.toolTip() or ""), w.tray.toolTip())

print("9) 异常位置恢复到屏内")
w.move(-9000, -9000)
w._ensure_on_screen()
app.processEvents()
check("已移回屏幕内", w.x() > -1000, f"x={w.x()}")

print("10) 多任务并行：逐行显示、高度自适应、上限收敛")
now = time.time()

# 停掉所有定时器：_anim 里的 demo 循环会每 3 秒重建一次 rows，
# 把本节手工布置的数据冲掉，测出来的高度就对不上了。
for _t in (w._anim, w._res, w._poll, w._cool):
    _t.stop()


def mk(i, state, work, name, detail):
    return st.Slot("workbuddy", f"m{i:02d}", state, name, detail,
                   now - 60 * i, 999, now, work)


w._demo = True          # 阻止 _poll_state 被真实文件覆盖
w.set_style(M.STYLE_STANDARD)
w.rows = []
w._row_keys = []
w._on_state_change()
app.processEvents()
h1 = w.height()

w.rows = [mk(1, st.RUNNING, "AI学习", "完善信号灯", "改任务名称"),
          mk(2, st.WAITING, "消费理财", "抓竞品价格", "等待确认")]
w._row_keys = [M._row_key(s) for s in w.rows]
w.status = st.compose(w.rows, st.Status())
w._on_state_change()
app.processEvents()
h2 = w.height()
check("两任务时卡片变高", h2 > h1, f"{h1} -> {h2}")
check("两任务高度 = 基准 + 20", h2 - h1 == 20, h2 - h1)
rows, more = w._visible_rows()
check("两个任务都列出", len(rows) == 2 and more == 0, (len(rows), more))
check("托盘提示列出两个任务", "2 个任务并行" in (w.tray.toolTip() or ""), w.tray.toolTip())

w.rows = [mk(i, st.RUNNING, f"工作{i}", f"对话{i}", f"指令{i}") for i in range(1, 6)]
w._row_keys = [M._row_key(s) for s in w.rows]
w.status = st.compose(w.rows, st.Status())
w._on_state_change()
app.processEvents()
rows, more = w._visible_rows()
check("超过上限时收敛到 3 行", len(rows) + (1 if more else 0) == M.MAX_ROWS, (len(rows), more))
check("余下任务折算成汇总数", more == 3, more)
h3 = w.height()
check("三行高度不超过上限行数", h3 - h1 == 50, h3 - h1)

pm = QPixmap(w.width(), w.height())
pm.fill(Qt.transparent)
w.render(pm)
check("多任务渲染不崩", not pm.isNull() and pm.width() == w.width(), pm.size())

for sid, extra in ((M.STYLE_BAR, 22), (M.STYLE_STACK, 30)):
    w.set_style(sid)
    app.processEvents()
    w.rows = [mk(1, st.RUNNING, "AI学习", "完善信号灯", "改任务名称"),
              mk(2, st.WAITING, "消费理财", "抓竞品价格", "等待确认")]
    w._row_keys = [M._row_key(s) for s in w.rows]
    w.status = st.compose(w.rows, st.Status())
    w._on_state_change()
    app.processEvents()
    base = round(M.BASE_SIZE[sid][1] * w.scale)
    check(f"{sid} 多任务高度 = 基准 + {extra}", w.height() - base == round(extra * w.scale),
          (w.height(), base))
    pm = QPixmap(w.width(), w.height())
    pm.fill(Qt.transparent)
    w.render(pm)
    check(f"{sid} 多任务渲染不崩", not pm.isNull(), pm.size())

w.set_style(M.STYLE_STANDARD)
w.rows = []
w._row_keys = []
w.status = st.Status()
w._on_state_change()
app.processEvents()
check("清空后高度复原", w.height() == h1, (w.height(), h1))

print("11) 多槽位文件驱动多行显示")
w._demo = False
for i in range(3):
    st.write_slot("workbuddy", f"live{i}", st.RUNNING, f"对话{i}", f"指令{i}",
                  ttl=120, workspace=f"工作空间{i}")
w._poll_state()
app.processEvents()
check("读到 3 个槽位", len(w.rows) == 3, len(w.rows))
base_std = round(M.BASE_SIZE[M.STYLE_STANDARD][1] * w.scale)
check("窗口按 3 行加高 50px", w.height() - base_std == round(50 * w.scale), (w.height(), base_std))
check("最新任务排在第一行", w.rows[0].title == "对话2", w.rows[0].title if w.rows else None)
check("槽位带到工作空间名", w.rows[0].workspace == "工作空间2", w.rows[0].workspace if w.rows else None)
for i in range(3):
    st.write_slot("workbuddy", f"live{i}", st.IDLE)
w._poll_state()
app.processEvents()
check("槽位清空后回到单行高度", w.height() == base_std, (w.height(), base_std))

print("12) 显示元素开关：两区块各自可关，高度跟着缩")
for sid in M.STYLE_ORDER:
    w.set_style(sid)
    w.settings.update(dict(st.DEFAULT_SETTINGS))
    w._apply_size()
    app.processEvents()
    full = w.height()

    w.set_show("show_task", False)
    app.processEvents()
    check(f"{sid} 关任务区后变矮", w.height() < full, (w.height(), full))
    check(f"{sid} 关任务区后仍在屏内合理范围", 30 <= w.height() <= full, w.height())

    w.set_show("show_metrics", False)
    app.processEvents()
    check(f"{sid} 两区块都关被拒绝", w.height() > 30 and w._task_on() is False,
          (w.height(), w._task_on()))
    check(f"{sid} 被拒绝后设置回滚", w.settings["show_metrics"] is True,
          w.settings.get("show_metrics"))

    w.set_show("show_task", True)
    app.processEvents()
    w.settings.update(dict(st.DEFAULT_SETTINGS))
    w._apply_size()
    app.processEvents()

# 单个指标列：列数减少但高度不变（标准卡指标区高度是固定的）
w.set_style(M.STYLE_STANDARD)
w.settings.update(dict(st.DEFAULT_SETTINGS))
w._apply_size()
h_all = w.height()
w.set_show("show_cpu", False)
w.set_show("show_mem", False)
w.set_show("show_temp", False)
app.processEvents()
check("只剩网络一列时高度不变", w.height() == h_all, (w.height(), h_all))
check("生效指标只有 net", w._metric_keys() == ("net",), w._metric_keys())
check("指标串只剩下行", "↓" in w._micro_metrics() and "CPU" not in w._micro_metrics(),
      w._micro_metrics())

w.settings.update(dict(st.DEFAULT_SETTINGS))
w.set_show("show_metrics", False)
app.processEvents()
check("关硬件指标后指标串为空", w._micro_metrics() == "", repr(w._micro_metrics()))
check("关硬件指标后卡片变矮", w.height() < h_all, (w.height(), h_all))

w.settings.update(dict(st.DEFAULT_SETTINGS))
w._apply_size()
app.processEvents()

print("13) 显示元素菜单项齐全")
m = w._menu()
tops = [a.text() for a in m.actions() if a.text()]
check("菜单有显示元素入口", "显示元素" in tops, tops)
vm = next((a.menu() for a in m.actions() if a.text() == "显示元素"), None)
check("显示元素子菜单存在", vm is not None)
if vm is not None:
    items = [a.text() for a in vm.actions() if a.text()]
    check("含 AI 任务项", any("AI 任务" in t for t in items), items)
    mm = next((a.menu() for a in vm.actions() if "硬件指标" in a.text()), None)
    check("硬件指标二级菜单存在", mm is not None, items)
    if mm is not None:
        subs = [a.text() for a in mm.actions() if a.text()]
        for name in ("CPU", "内存", "温度", "网络"):
            check(f"含 {name} 开关", name in subs, subs)

print("14) 视觉层级：执行中任务突出、多任务灯放大、指标弱化")
from PySide6.QtGui import QImage, QPainter  # noqa: E402


def draw_task_line(state):
    """只画一行到透明画布：排除卡片底色与指标区干扰，像素结果可判定。"""
    q = QImage(400, 40, QImage.Format_ARGB32_Premultiplied)
    q.fill(Qt.transparent)
    painter = QPainter(q)
    painter.setRenderHint(QPainter.Antialiasing, True)
    it = st.Slot("workbuddy", "focus", state, "对话甲", "指令甲",
                 time.time() - 60, 999, time.time(), "工作甲")
    w._task_line(painter, 20.0, 5.0, 360.0, it, 9, 40.0, lamp=True)
    painter.end()
    return q


run_q = draw_task_line(st.RUNNING)
wait_q = draw_task_line(st.WAITING)
check("执行中任务有整行高亮", run_q.pixelColor(200, 25).alpha() > 0,
      run_q.pixelColor(200, 25).alpha())
check("非执行中任务没有行高亮", wait_q.pixelColor(200, 25).alpha() == 0,
      wait_q.pixelColor(200, 25).alpha())
check("执行中任务有左侧强调条", run_q.pixelColor(14, 10).alpha() > 150,
      run_q.pixelColor(14, 10).alpha())


def core_width(q, cy=12):
    """灯芯水平直径：光晕 alpha < 100、强调环 < 200，只数不透明的芯。"""
    xs = [xx for xx in range(10, 60) if q.pixelColor(xx, cy).alpha() > 200]
    return (max(xs) - min(xs) + 1) if xs else 0


check("执行中状态灯直径 >= 13px（旧色点只有 6px）", core_width(run_q) >= 13,
      core_width(run_q))
check("非执行中状态灯同样够大", core_width(wait_q) >= 12, core_width(wait_q))

ui_src = (Path(__file__).parent.parent / "app" / "main.py").read_text(encoding="utf-8")
check("指标数值降为 9.5pt 常规字重", "_f(9.5, QFont.Normal, FONT_MONO)" in ui_src)
check("指标不再用 10.5pt 粗体", "_f(10.5, QFont.DemiBold, FONT_MONO)" not in ui_src)
check("指标走主题里的弱化色", "PAL.metric_value" in ui_src and "PAL.metric_label" in ui_src)
check("告警色不被弱化吞掉", "vcol if alert else PAL.metric_value" in ui_src)
check("行高亮跟着版式行高走", "row_h - 1" in ui_src and "row_h=22.0" in ui_src)

# 开机自启：winreg 键对象只有 Close()/__exit__，没有小写 close()
check("注册表键不用小写 .close()", ".close()" not in ui_src.split("def _set_autostart")[1].split("def _single_instance")[0])
check("CreateKeyEx 用 with 管理句柄", "with winreg.CreateKeyEx" in ui_src)
check("开机自启用 toggled 信号（非 triggered）", "a_boot.toggled.connect" in ui_src)

print(f"\n结果: {ok} 通过 / {fail} 失败")
st.write_state("idle")
sys.exit(0 if fail == 0 else 1)
