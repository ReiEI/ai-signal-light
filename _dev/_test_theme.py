# -*- coding: utf-8 -*-
"""浅色/深色主题自检。

重点不是"有两个调色板"这种静态事实，而是：
  1. 对比度够不够（浅色底上浅蓝状态灯会糊）
  2. 系统深浅色变化后，卡片、菜单、托盘、散热面板是否都跟着换
  3. 手改配置改坏了能不能回到 auto
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TEST_HOME = Path(tempfile.gettempdir()) / "aisignal-themetest"
shutil.rmtree(_TEST_HOME, ignore_errors=True)
os.environ["AISIGNAL_HOME"] = str(_TEST_HOME)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import cooling_ui  # noqa: E402
import main as M  # noqa: E402
import state as st  # noqa: E402
import theme  # noqa: E402

ok = bad = 0


def check(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        bad += 1
        print(f"  FAIL  {name}  -> {extra!r}")


app = QApplication(sys.argv)

print("1) 配置字段与容错")
check("默认主题是 auto", st.DEFAULT_SETTINGS.get("theme") == "auto")
for raw, want in (("light", "light"), ("dark", "dark"), ("auto", "auto"),
                  ("solarized", "auto"), ("", "auto"), (None, "auto")):
    s = dict(st.DEFAULT_SETTINGS)
    s["theme"] = raw
    # 走 load_settings 的容错分支：写进临时 settings.json 再读回来
    st.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    import json
    st.SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    check(f"配置值 {raw!r} -> {want}", st.load_settings()["theme"] == want, st.load_settings()["theme"])

print("2) 解析与 Windows 联动")
check("resolve(light/dark) 不被系统覆盖", theme.resolve("light") == "light" and theme.resolve("dark") == "dark")
check("sanitize 只认三档", all(theme.sanitize(v) == "auto" for v in ("", None, "sepia", 3)))
orig_reg = theme._registry_light
try:
    theme._registry_light = lambda: True
    check("注册表 AppsUseLightTheme=1 -> 浅色", theme.windows_scheme() == "light")
    check("auto 解析跟随浅色系统", theme.resolve("auto") == "light")
    theme._registry_light = lambda: False
    check("注册表 AppsUseLightTheme=0 -> 深色", theme.windows_scheme() == "dark")
    theme._registry_light = lambda: None
    check("注册表读不到时回退不抛异常", theme.windows_scheme() in ("light", "dark"))
finally:
    theme._registry_light = orig_reg

print("3) 两套调色板完整性与对比度")
fields = ("card_bg", "card_border", "txt", "txt_dim", "txt_faint", "divider", "track",
          "bar_blue", "amber", "menu_bg", "menu_fg", "menu_sel", "tray_rim", "chip_bg",
          "metric_value", "metric_label", "metric_bar",
          "panel_btn", "panel_primary_bg", "panel_danger_bg")
for tid in ("dark", "light"):
    pal = theme.Palette(tid)
    missing = [f for f in fields if not isinstance(getattr(pal, f, None), QColor)]
    check(f"{tid} 调色板字段齐全", not missing, missing)
    check(f"{tid} 五态颜色齐全", all(isinstance(pal.state(k), QColor) for k in theme.STATE_HEX))
    ratio = theme.contrast(pal.txt, pal.card_bg)
    check(f"{tid} 主文字对比度 >= 4.5", ratio >= 4.5, round(ratio, 2))
    dim = theme.contrast(pal.txt_dim, pal.card_bg)
    check(f"{tid} 次文字对比度 >= 3.0", dim >= 3.0, round(dim, 2))
    faint = theme.contrast(pal.txt_faint, pal.card_bg)
    check(f"{tid} 三级文字对比度 >= 2.4", faint >= 2.4, round(faint, 2))
    if tid == "light":
        check("浅色三级文字对比度 >= 3.5", faint >= 3.5, round(faint, 2))
    for k in ("running", "done", "waiting", "failed"):
        cr = theme.contrast(pal.state(k), pal.card_bg)
        check(f"{tid} 状态灯 {k} 对比度 >= 2.2", cr >= 2.2, round(cr, 2))
    # 硬件指标是"相对弱化"而不是"看不清"：比主文字暗，但仍守住可读性下限。
    mv = theme.contrast(pal.metric_value, pal.card_bg)
    ml = theme.contrast(pal.metric_label, pal.card_bg)
    mt = theme.contrast(pal.txt, pal.card_bg)
    check(f"{tid} 指标数值弱化但仍 >= 3.5", 3.5 <= mv < mt, round(mv, 2))
    check(f"{tid} 指标标签弱化但仍 >= 2.8", 2.8 <= ml < mt, round(ml, 2))
    check(f"{tid} 指标色与主文字不同", pal.metric_value.name() != pal.txt.name())
check("浅深主色不同", theme.Palette("dark").txt.name() != theme.Palette("light").txt.name())
check("浅深菜单样式不同", theme.menu_stylesheet(theme.Palette("dark")) != theme.menu_stylesheet(theme.Palette("light")))
check("浅深面板样式不同", theme.panel_stylesheet(theme.Palette("dark")) != theme.panel_stylesheet(theme.Palette("light")))
check("菜单样式无残留硬编码深色", "#12161C" not in theme.menu_stylesheet(theme.Palette("light")))

print("4) 源码接线")
main_src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
panel_src = (ROOT / "app" / "cooling_ui.py").read_text(encoding="utf-8")
check("main 引用共享调色板", "PAL = theme_mod.PAL" in main_src)
check("main 无旧硬编码颜色常量", "CARD_BG = QColor(" not in main_src and "STATE_COLORS = {" not in main_src)
check("main 有主题菜单", 'm.addMenu("主题")' in main_src)
check("main 有系统信号与轮询兜底", "colorSchemeChanged" in main_src and 'theme_mod.resolve("auto")' in main_src)
check("面板 QSS 由主题生成", "theme.panel_stylesheet(PAL)" in panel_src)
check("温度命令输入框也走主题", "dlg.setStyleSheet(theme_mod.panel_stylesheet(PAL))" in main_src)
check("面板有 apply_theme", "def apply_theme(self)" in panel_src)
check("面板无旧硬编码颜色", 'BG = "#12161C"' not in panel_src)

theme_src = (ROOT / "app" / "theme.py").read_text(encoding="utf-8")
check("两套调色板都有指标弱化色",
      all(theme_src.count(f'"{k}"') == 2 for k in ("metric_value", "metric_label", "metric_bar")))
check("多任务行改用大状态灯", "def _row_lamp(" in main_src and "lamp=True" in main_src)
check("执行中任务有强调与提亮", "ROW_ACCENT_W" in main_src
      and "emphasis=running and lamp" in main_src)
check("单任务大灯放大", "LAMP_BASE = 8.0" in main_src)
check("行高亮按版式行高计算", "row_h - 1" in main_src and "row_h=22.0" in main_src)

print("5) 运行时联动")
w = M.SignalLight(demo=False)
w.setAttribute(Qt.WA_DontShowOnScreen, True)
w.show()
app.processEvents()
check("启动解析出具体主题", w._theme_id in ("light", "dark"), w._theme_id)
check("启动即跟随系统档位", w._theme_id == theme.windows_scheme(), (w._theme_id, theme.windows_scheme()))
check("启动后 PAL 与窗口一致", M.PAL.name == w._theme_id, (M.PAL.name, w._theme_id))
# 先强制暗色，别让测试结果依赖"此刻系统正好是深色还是浅色"
w.set_theme("dark")
app.processEvents()

def snapshot():
    """直接 render 到 QPixmap：grab() 在 WA_DontShowOnScreen 窗口上会偶发空图。"""
    app.processEvents()
    pm = QPixmap(w.size())
    pm.fill(Qt.transparent)
    w.render(pm)
    return pm.toImage()


def diff_count(a, b) -> int:
    if a.size() != b.size():
        return 10**6
    n = 0
    for y in range(0, a.height(), 3):
        for x in range(0, a.width(), 3):
            if a.pixelColor(x, y) != b.pixelColor(x, y):
                n += 1
    return n


dark_img = snapshot()
w.set_theme("light")
app.processEvents()
check("手动切浅色生效", M.PAL.name == "light" and w._theme_id == "light")
check("设置已持久化", st.load_settings()["theme"] == "light", st.load_settings().get("theme"))
light_img = snapshot()
check("浅深渲染确实不同", diff_count(dark_img, light_img) > 100, diff_count(dark_img, light_img))
menu = w._menu()
thm = next((a for a in menu.actions() if a.text() == "主题"), None)
check("主题菜单存在", thm is not None and thm.menu() is not None)
if thm is not None and thm.menu() is not None:
    checked = [a.text() for a in thm.menu().actions() if a.isCheckable() and a.isChecked()]
    check("菜单勾选与当前一致", checked == ["浅色"], checked)

theme._registry_light = lambda: True
w.set_theme("auto")
app.processEvents()
check("auto 跟随浅色系统", M.PAL.name == "light", M.PAL.name)
theme._registry_light = lambda: False
w._on_system_scheme()
app.processEvents()
check("系统切深色后自动跟随", M.PAL.name == "dark", M.PAL.name)
theme._registry_light = orig_reg

# 散热面板也要换色，不能留一块黑面板
panel = cooling_ui.CoolingPanel(w.cooling, w.metrics, w.thermal)
qss_dark = panel.styleSheet()
M.PAL.set("light")
panel.apply_theme()
check("面板 QSS 随主题变化", panel.styleSheet() != qss_dark)
panel.close()

# 主题色应改变托盘圆点
M.PAL.set("dark")
i_dark = w._make_icon(M.PAL.state("running")).pixmap(32, 32).toImage()
M.PAL.set("light")
i_light = w._make_icon(M.PAL.state("running")).pixmap(32, 32).toImage()
check("托盘状态灯随主题换色", i_dark != i_light)

print("6) 执行中任务的强调在浅深两套下都成立")
from PySide6.QtGui import QImage, QPainter  # noqa: E402


def focus_alpha(tid: str) -> int:
    M.PAL.set(tid)
    q = QImage(400, 40, QImage.Format_ARGB32_Premultiplied)
    q.fill(Qt.transparent)
    pp = QPainter(q)
    pp.setRenderHint(QPainter.Antialiasing, True)
    it = st.Slot("workbuddy", "focus", st.RUNNING, "对话", "指令", 1, 999, 1, "工作")
    w._task_line(pp, 20.0, 5.0, 360.0, it, 9, 40.0, lamp=True)
    pp.end()
    return q.pixelColor(200, 25).alpha()


for tid in ("dark", "light"):
    check(f"{tid} 下执行中任务仍有行高亮", focus_alpha(tid) > 0)

print(f"\n结果: {ok} 通过 / {bad} 失败")
sys.exit(1 if bad else 0)
