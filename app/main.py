# -*- coding: utf-8 -*-
"""AI 工作信号灯 —— 桌面常驻状态灯

无边框、常驻顶层、可拖拽的小挂件。
上半区显示 AI 任务状态（五态信号灯），下半区显示 CPU / 内存 / 温度 / 网速。

三套既定版式（右键 →样式）：
  standard  标准卡：灯 + 状态 + 四列指标，信息最全
  bar       横条：两行贴边，占屏最少
  stack     竖版：窄而高，适合贴屏幕侧边

四档尺寸（右键 →尺寸）：85% / 100% / 115% / 135%。
尺寸不做自由拖拽——文字换行和列宽在非整数比例下必然错位，
改为"整张卡片等比缩放"：所有坐标和字号都乘同一个系数，布局永远不变形。

启动：python app/main.py
演示：python app/main.py --demo     （循环切换五种状态，用于预览）
"""

from __future__ import annotations

import math
import os
import sys
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QInputDialog,
    QMenu,
    QSystemTrayIcon,
    QWidget,
)

sys.path.insert(0, str(Path(__file__).parent))

import codex as codex_mod  # noqa: E402
import cooling as cooling_mod  # noqa: E402
import metrics as metrics_mod  # noqa: E402
import state as st  # noqa: E402
import theme as theme_mod  # noqa: E402
from cooling_ui import CoolingPanel, fmt_dur, fmt_short  # noqa: E402

# ---------------- 版式 ----------------

STYLE_STANDARD = "standard"
STYLE_BAR = "bar"
STYLE_STACK = "stack"

STYLE_ORDER = (STYLE_STANDARD, STYLE_BAR, STYLE_STACK)
STYLE_NAMES = {STYLE_STANDARD: "标准卡", STYLE_BAR: "横条", STYLE_STACK: "竖版"}
STYLE_HINTS = {STYLE_STANDARD: "灯 + 指标列", STYLE_BAR: "两行贴边", STYLE_STACK: "窄竖排"}


def app_icon() -> QIcon:
    """窗口 / 任务栏图标。

    exe 内嵌图标只决定文件管理器里看到的那张图；Qt 窗口得自己 setWindowIcon，
    否则任务栏会落回 Qt 默认图标。冻结后资源在 _MEIPASS，源码跑时在仓库根。
    资源缺失时返回空 QIcon —— 图标不该让程序起不来。
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent
    ico = base / "assets" / "signallight.ico"
    return QIcon(str(ico)) if ico.exists() else QIcon()

# 基准尺寸（不含缩放）。实际窗口 = 基准 × scale。
BASE_SIZE = {
    STYLE_STANDARD: (400.0, 138.0),
    STYLE_BAR: (320.0, 64.0),
    STYLE_STACK: (200.0, 248.0),
}

# 尺寸档位：只给既有档位，不给连续缩放
SCALE_STEPS = ((0.85, "小"), (1.0, "标准"), (1.15, "大"), (1.35, "超大"))

# 散热条：只有存在散热会话时才出现在卡片底部。多出来的高度不算进基准尺寸，
# 避免"有没有在散热"影响版式比例 —— 那会让每次开关风扇卡片都轻微变形。
STRIP_STANDARD = 30.0
STRIP_STACK = 34.0

# 指标区的高度常量集中在这里：_base_size 和 paint 必须用同一套数字，
# 各写各的迟早出现"高度算出来 118，实际内容画到 122"这种裁切。
STD_METRICS_H = 62.0     # 标准卡：分隔线 + 8px 间隙 + 54px 内容
STACK_GAP_TASK = 11.0    # 竖版：任务区下沿到第一条指标（分隔线到内容）
STACK_GAP_HEAD = 8.0     # 竖版：没有任务区时，卡片顶部到第一条指标
STACK_ROW_H = 36.0
BAR_ONLY_METRICS_H = 40.0  # 横条只剩指标串时的总高

METRIC_NAMES = {"cpu": "CPU", "mem": "内存", "temp": "温度", "net": "网络"}

# 多任务并行：每个任务占一行（横条）或两行（标准卡/竖版：名称 + 指令）。
# 上限 3 个 —— 常驻挂件不是任务管理器，再多就该去做窗口而不是贴屏幕。
MAX_ROWS = 3

# --demo 的步骤数：5 个单任务状态 + 1 个三任务并行
DEMO_STEPS = 6

MARGIN = 8  # 阴影留白

# 任务名称里工作空间与对话名的分隔符
TASK_SEP = " - "

# 视觉层级：执行中的任务是主角，硬件指标退到背景。
# 多任务原来每行只有一个 6×6 的小色点，三行并排时几乎看不见 ——
# 现在行首统一给一枚带光晕的状态灯，执行中的再大一档并加左侧强调条。
TASK_LAMP_W = 20.0          # 行首灯位宽度：灯直径 14 + 6px 呼吸
TASK_LAMP_R = 6.5          # 非执行中的灯也要看得清，只是不抢主角
TASK_LAMP_R_RUNNING = 7.0
ROW_ACCENT_W = 3.0          # 执行中任务左侧强调条宽度
LAMP_BASE = 8.0             # 单任务大灯半径，原 6.5

# 所有颜色集中到 app/theme.py（浅/深两套 + 状态灯权威色）。
# PAL 是 theme_mod.PAL 的别名而不是新实例：切换主题时原地换色，
# 任何一处绘制代码拿到的都是当前主题，不会残留旧 QColor。
PAL = theme_mod.PAL

SRC_NAMES = {
    "adl": "AMD ADL",
    "nvidia": "nvidia-smi",
    "lhm": "LibreHardwareMonitor",
    "psutil": "psutil",
    "cmd": "自定义命令",
}

FONT_UI = "Microsoft YaHei UI"
FONT_MONO = "Consolas"

# 单实例互斥句柄，必须活到进程结束
_MUTEX = None

# 温度告警线：笔记本 CPU/APU 常年在 45~60，75 以上才算真热
TEMP_WARN = 62.0
TEMP_HOT = 78.0


def _f(size: float, weight: int = QFont.Normal, family: str = FONT_UI) -> QFont:
    """必须用 setPointSizeF：QFont(family, size) 只接受整数，会把 9.5 截成 9。"""
    f = QFont(family)
    f.setPointSizeF(float(size))
    f.setWeight(QFont.Weight(weight))
    return f


def _row_key(s) -> tuple:
    """判断"可见任务清单是否变化"用的指纹。

    心跳会不断刷新 beat，但 beat 不影响显示内容 —— 把它算进来的话，
    每次工具调用都会触发一次全量重绘与尺寸检查。
    """
    return (s.source, s.sid, s.state, s.title, s.workspace, s.detail, round(s.ts, 2))


def fmt_elapsed(sec: float) -> str:
    if sec < 1:
        return "刚刚"
    if sec < 60:
        return f"{int(sec)}s"
    m, s = divmod(int(sec), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


class SignalLight(QWidget):
    def __init__(self, demo: bool = False) -> None:
        super().__init__()
        self.settings = st.load_settings()
        self._theme_pref = theme_mod.sanitize(self.settings.get("theme"))
        self._theme_id = ""
        self._theme_check_t = 0.0
        PAL.set(theme_mod.resolve(self._theme_pref))
        self._theme_id = PAL.name
        self.reader = st.StateReader()
        self.codex = codex_mod.CodexWatcher()
        self.metrics = metrics_mod.Metrics()
        self.thermal = metrics_mod.Thermal(cmd=str(self.settings.get("temp_cmd") or ""))
        self.cooling = cooling_mod.Cooling()
        self.cool = None                  # 当前散热会话（None = 没在散热）
        self.panel: CoolingPanel | None = None
        self._stop_rect = QRectF()        # 散热条上的"停止"热区（基准坐标）
        self.status = st.Status()
        # 多任务并行：槽位可能同时有多个（不同工作空间 / 不同对话）。
        # 之前的做法只取优先级最高的一个，其余折成"另有 N 个"——
        # 这对"我现在同时交代了三件事"毫无帮助，所以改成逐行列出。
        self.rows: list[st.Slot] = []
        self._row_keys: list[tuple] = []
        self._rows_shown = 0
        self.style = self.settings.get("style", STYLE_STANDARD)
        if self.style not in BASE_SIZE:
            self.style = STYLE_STANDARD
        self.scale = float(self.settings.get("scale", 1.0))
        self._phase = 0.0
        self._state_t0 = time.time()
        self._prev_state = None
        self._drag_from: QPoint | None = None
        self._demo = demo
        self._demo_idx = 0
        self._demo_t0 = time.time()

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowTitle("AI 工作信号灯")

        # 系统主题变化：Qt 信号负责即时通知，_tick 里的注册表轮询兜底
        # （部分 Windows 版本 / 高对比度场景下 Qt 信号不触发）。
        try:
            hints = QGuiApplication.instance().styleHints()
            hints.colorSchemeChanged.connect(self._on_system_scheme)
        except Exception:
            pass

        self._build_tray()
        self._apply_size()

        # 位置：默认右下角；恢复旧位置后校正，防止副屏拔掉导致窗口跑到屏幕外
        if self.settings.get("x") is not None:
            self.move(int(self.settings["x"]), int(self.settings["y"]))
        else:
            self._park_bottom_right()
        self._ensure_on_screen()

        self.setWindowOpacity(float(self.settings.get("opacity", 0.96)))

        # 动画 ~30fps
        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)
        self._anim.start(33)

        # 资源采样 1s
        self._res = QTimer(self)
        self._res.timeout.connect(self._sample_metrics)
        self._res.start(1000)
        self._sample_metrics()

        # 状态轮询 400ms
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._poll_state)
        self._poll.start(400)
        self._poll_state()

        # 散热会话轮询 500ms：读会话文件 + 维持 UI 心跳
        self._cool = QTimer(self)
        self._cool.timeout.connect(self._poll_cooling)
        self._cool.start(500)
        self._poll_cooling()

        # 启动对账放后台：万一上次是崩溃退出，这里要拉起提权进程还原电源，
        # 而 UAC 对话框会阻塞调用线程，不能卡在首屏上。
        threading.Thread(target=self.cooling.reconcile, name="cool-reconcile", daemon=True).start()

        if demo:
            self._demo_t0 = time.time()
            self._push_demo()

    # ---------------- 生命周期 ----------------

    def _base_size(self) -> tuple[float, float]:
        """基准尺寸（不含缩放）。

        高度不是常量，而是按"当前显示了哪些区块"推出来的：
        关掉任务区，卡片自然缩到只剩指标；关掉指标区，只剩任务。
        两个区块都关不合法，set_show 会拦住，load_settings 也会兜底。
        """
        w = BASE_SIZE[self.style][0]
        task_on = self._task_on()
        n = len(self._metric_keys())
        strip = self.cool is not None

        if self.style == STYLE_BAR:
            # 横条没有单独的散热行，散热只占一个 × 热区，不影响高度
            if not task_on:
                return w, BAR_ONLY_METRICS_H
            return w, 64.0 + self._bar_extra_h()

        if self.style == STYLE_STACK:
            h = 16.0 + (self._stack_task_h() if task_on else 0.0)
            if n:
                h += (STACK_GAP_TASK if task_on else STACK_GAP_HEAD) + STACK_ROW_H * n
            h += 4.0
            if strip:
                h += STRIP_STACK
            return w, h

        h = 16.0 + (self._std_task_h() if task_on else 0.0)
        if n:
            h += STD_METRICS_H
        h += 4.0
        if strip:
            h += STRIP_STANDARD
        return w, h

    def _fit_rows(self) -> None:
        """可见任务行数变了才动窗口大小，否则每次心跳都会重设尺寸。"""
        if not self._task_on():
            # 任务区关着，行数不影响尺寸 —— 别让新任务把窗口"重新摆"一次
            self.update()
            return
        n = min(len(self.rows), MAX_ROWS)
        if n != self._rows_shown:
            self._rows_shown = n
            self._apply_size(keep_center=True)
        self.update()

    def _apply_size(self, keep_center: bool = False) -> None:
        # 尺寸一变就把行数基线同步掉：换版式时高度是按当前 rows 重算的，
        # 不同步的话 _fit_rows 会以为"行数没变"，之后再也不肯调高度。
        self._rows_shown = min(len(self.rows), MAX_ROWS)
        bw, bh = self._base_size()
        k = self.scale
        nw, nh = int(round(bw * k)), int(round(bh * k))
        center = self.geometry().center() if keep_center and self.isVisible() else None
        self.setFixedSize(nw, nh)
        if center is not None:
            # 换档后以原中心为锚点，否则窗口会向右下角"长出去"
            self.move(center.x() - nw // 2, center.y() - nh // 2)
            self._ensure_on_screen()

    def _park_bottom_right(self) -> None:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.right() - self.width() - 32, geo.bottom() - self.height() - 48)

    def _ensure_on_screen(self) -> None:
        """恢复的位置若不在任何屏幕内（副屏拔掉/分辨率变化），退回主屏右下角。"""
        r = self.frameGeometry()
        for sc in QGuiApplication.screens():
            if sc.availableGeometry().intersects(r):
                return
        self._park_bottom_right()

    def _sample_metrics(self) -> None:
        self.metrics.sample()

    def _tick(self) -> None:
        self._phase += 0.10
        if self._theme_pref == "auto":
            now = time.time()
            if now - self._theme_check_t > 1.0:
                self._theme_check_t = now
                self._apply_theme(theme_mod.resolve("auto"), save=False)
        if self._demo:
            now = time.time()
            if now - self._demo_t0 > 3.0:
                self._demo_t0 = now
                self._demo_idx = (self._demo_idx + 1) % DEMO_STEPS
                self._push_demo()
        self.update()

    def _push_demo(self) -> None:
        # 三元组顺序与真实数据一致：工作空间 / 对话名 / 本轮指令。
        # 最后一步故意放三个并行任务，用来检查多行布局与高度自动扩张。
        seq = [
            (st.RUNNING, "AI学习", "完善信号灯任务名称", "优化：显示的任务名称，改为工作空间 + 对话名"),
            (st.DONE, "AI学习", "完善信号灯任务名称", "已完成"),
            (st.WAITING, "工作-202602-Alexa提问采集", "采集竞品价格", "等待你确认是否覆盖同名文件"),
            (st.FAILED, "消费理财", "抓取竞品价格", "接口返回 429"),
            (st.IDLE, "", "", ""),
        ]
        self.reader._last_mtime = 1e18  # 阻断真实文件覆盖

        if self._demo_idx >= len(seq):
            now = time.time()
            self.rows = [
                st.Slot("workbuddy", "d1", st.RUNNING, "完善信号灯任务名称", "加多任务行显示",
                        now - 95, 999, now, "AI学习"),
                st.Slot("workbuddy", "d2", st.WAITING, "采集竞品价格", "等待确认覆盖同名文件",
                        now - 260, 999, now, "工作-202602-Alexa提问采集"),
                st.Slot("workbuddy", "d3", st.RUNNING, "月度账单归类", "读取 3 月流水",
                        now - 20, 999, now, "消费理财"),
            ]
            self._row_keys = [_row_key(s) for s in self.rows]
            self.status = st.compose(self.rows, st.Status())
        else:
            s, work, title, detail = seq[self._demo_idx]
            self.rows = []
            self._row_keys = []
            self.reader._last = st.Status(
                s, title, detail, time.time(), 999, False, 0.0, workspace=work,
            )
            self.status = self.reader._last
        # 两个分支各自决定了 status，这里只做一次收尾。
        # 早先这里多了一行无条件 self.status = reader._last，
        # 把多任务那步 compose 出来的状态覆盖回上一条单任务状态。
        self._on_state_change()

    def _poll_state(self) -> None:
        if self._demo:
            return
        # Codex 监听器自己写槽位，这里只需在它之后统一重读
        self.codex.poll()
        slots = st.read_slots()
        single = self.reader.read()
        new = st.compose(slots, single)
        keys = [_row_key(s) for s in slots]
        # 用 key 而不是对象比较：槽位每次心跳都会变 beat，
        # 直接比对象会让"没变化"也判成变化，白白重设窗口。
        if (new.state != self.status.state
                or new.ts != self.status.ts
                or new.peers != self.status.peers
                or keys != self._row_keys):
            self.status = new
            self.rows = slots
            self._row_keys = keys
            self._on_state_change()

    def _on_state_change(self) -> None:
        if self.status.state != self._prev_state:
            self._state_t0 = time.time()
            self._prev_state = self.status.state
        # 任务数变了要重算窗口高度；文案变了只需重绘
        self._fit_rows()
        # peers 变化也要刷新：托盘提示里会带出"另有 N 个"
        self._update_tray()

    # ---------------- 散热 ----------------

    def _poll_cooling(self) -> None:
        prev = self.cool
        self.cool = self.cooling.poll()
        note = self.cooling.take_notice()
        if note:
            self._notify(note)
        if (prev is None) != (self.cool is None):
            # 卡片高度变了：以中心为锚点缩放，避免"往上蹦"一下
            if self.cool is None:
                # 会话结束：立刻作废停止热区。不能只等下一次重绘 ——
                # 窗口被托盘隐藏时根本不会重绘，那时热区还留着就是幽灵按钮。
                self._stop_rect = QRectF()
            self._apply_size(keep_center=True)
            self.update()
            self._update_tray()
        elif prev is not None and self.cool is not None and (prev.mode != self.cool.mode):
            self._update_tray()

    def _notify(self, text: str, title: str = "AI 工作信号灯 · 散热") -> None:
        try:
            self.tray.showMessage(title, text, QSystemTrayIcon.Information, 4000)
        except Exception:  # noqa: BLE001
            pass

    def start_cooling(self, mode: str) -> None:
        ok, msg = self.cooling.start(mode)
        self._notify(msg)
        if ok:
            QTimer.singleShot(600, self._poll_cooling)
        self.update()

    def stop_cooling(self) -> None:
        _ok, msg = self.cooling.stop()
        self._notify(msg)

    def toggle_panel(self) -> None:
        if self.panel is None:
            self.panel = CoolingPanel(
                self.cooling, self.metrics, self.thermal, parent=None,
                on_change=self._poll_cooling,
            )
        if self.panel.isVisible():
            self.panel.raise_()
            self.panel.activateWindow()
        else:
            self.panel.show()
        self.panel.raise_()

    # ---------------- 绘制 ----------------

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)

        # 每帧重置热区：切到横条后不再画"停止"，热区必须跟着失效，
        # 否则会留下一块看不见但点得动的幽灵按钮。
        self._stop_rect = QRectF()

        k = self.scale
        # 整张卡片等比缩放：所有绘制都写在"基准坐标"里，缩放交给画笔变换。
        # 这样字号、圆角、进度条会一起缩放，不会出现某个元素没跟着变的情况。
        p.scale(k, k)
        bw, bh = self._base_size()
        vw = min(bw, self.width() / k)
        vh = min(bh, self.height() / k)
        card = QRectF(MARGIN, MARGIN, vw - 2 * MARGIN, vh - 2 * MARGIN)

        self._paint_card(p, card, 14.0 if self.style == STYLE_BAR else 16.0)

        if self.style == STYLE_BAR:
            self._paint_bar(p, card)
        elif self.style == STYLE_STACK:
            self._paint_stack(p, card)
        else:
            self._paint_standard(p, card)

    def _paint_card(self, p: QPainter, card: QRectF, radius: float) -> None:
        # 投影（三层近似）
        p.setPen(Qt.NoPen)
        for i, a in enumerate(PAL.shadow_layers):
            g = i * 1.6
            p.setBrush(QColor(0, 0, 0, a))
            p.drawRoundedRect(card.adjusted(-g, -g * 0.5, g, g), radius + 2, radius + 2)

        p.setBrush(PAL.card_bg)
        p.setPen(QPen(PAL.card_border, 1))
        p.drawRoundedRect(card, radius, radius)
        p.setPen(Qt.NoPen)

    def _status_color(self) -> QColor:
        c = PAL.state(self.status.state)
        if self.status.stale:
            c = QColor(c.red(), c.green(), c.blue(), 110)
        return c

    def _status_label(self) -> str:
        return "信号中断" if self.status.stale else self.status.label

    # ---- 显示元素 ----
    def _task_on(self) -> bool:
        return bool(self.settings.get("show_task", True))

    def _metric_keys(self) -> tuple[str, ...]:
        """生效的指标列，顺序即绘制顺序。总开关关掉就返回空。"""
        return st.metric_keys_of(self.settings)

    # ---- 多任务：行数与高度 ----
    def _std_task_h(self) -> float:
        """标准卡任务区高度：单任务 56（到分隔线），多任务 16+30n。"""
        rows, more = self._visible_rows()
        if len(rows) <= 1 and not more:
            return 56.0
        return 16.0 + 30.0 * (len(rows) + (1 if more else 0))

    def _stack_task_h(self) -> float:
        """竖版任务区高度：单任务 73（到分隔线），每多一行 +30。"""
        rows, more = self._visible_rows()
        if len(rows) <= 1 and not more:
            return 73.0
        return 73.0 + 30.0 * (len(rows) + (1 if more else 0) - 1)

    def _bar_extra_h(self) -> float:
        """横条多任务带来的额外高度。n=1 必须为 0，否则单任务尺寸会跟着漂。"""
        n = min(len(self.rows), MAX_ROWS)
        return 22.0 * (n - 1) if n > 1 else 0.0

    def _visible_rows(self) -> tuple[list, int]:
        """返回 (要画的任务, 未画出的数量)。

        超过上限时留最后一格做汇总，而不是把第 N+1 个挤进半行 ——
        半行内容既读不清又占高度，不如明确告诉用户"还有几个"。
        """
        n = len(self.rows)
        if n <= MAX_ROWS:
            return self.rows, 0
        keep = MAX_ROWS - 1
        return self.rows[:keep], n - keep

    def _task_line(self, p: QPainter, x: float, y: float, w: float, it,
                   size: float, label_w: float, elapsed: bool = True,
                   dot: bool = False, lamp: bool = False,
                   row_h: float = 30.0) -> None:
        """画一行任务：状态灯 + 状态词（窄版式省略）+ 工作空间·对话名 + 耗时。

        lamp=True 是多任务专用：行首给一枚带光晕的大灯，执行中的任务再叠加
        淡色行高亮与左侧强调条，其余任务整体降一级，主次一眼可分。
        dot=True 且没有 lamp 时才是原来那个 6px 小色点（保留给极窄布局）。
        """
        color = PAL.state(it.state)
        running = it.state == st.RUNNING
        prefix = 0.0

        if lamp:
            if running:
                # 高亮必须跟着版式行高走：横条只有 22px，写死 29 会盖到下一行。
                hi = QColor(color)
                hi.setAlpha(18 if PAL.name == "dark" else 24)
                p.setPen(Qt.NoPen)
                p.setBrush(hi)
                p.drawRoundedRect(QRectF(x - 5, y - 1, w + 10, row_h - 1), 6, 6)
                acc = QColor(color)
                acc.setAlpha(220)
                p.setBrush(acc)
                p.drawRoundedRect(QRectF(x - 7, y + 2, ROW_ACCENT_W, row_h - 7), 1.5, 1.5)
            r = TASK_LAMP_R_RUNNING if running else TASK_LAMP_R
            self._row_lamp(p, x + TASK_LAMP_W / 2.0, y + 7.5, it.state, color, r)
            prefix += TASK_LAMP_W

        if lamp and dot:
            pass  # 窄版式：有灯就够了，状态词会吃掉 20% 宽度
        elif dot:
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(QRectF(x + 2, y + 5, 6, 6))
        else:
            f = _f(size + (0.5 if running and lamp else 0), QFont.DemiBold)
            p.setFont(f)
            p.setPen(color if it.state != st.IDLE else PAL.txt_dim)
            label = st.LABELS.get(it.state, it.state)
            p.drawText(QRectF(x + prefix, y,
                              max(label_w, QFontMetrics(f).horizontalAdvance(label) + 2), 15),
                       Qt.AlignLeft | Qt.AlignVCenter, label)
            # +3 是状态词与名称之间的呼吸位：宽度正好贴住时两个不同颜色的词
            # 会黏成一句，读起来像"进行中AI学习"。
            prefix += label_w + 3

        tw = 0.0
        if elapsed and it.state in (st.RUNNING, st.WAITING, st.FAILED) and it.ts:
            el = fmt_elapsed(time.time() - it.ts)
            if el:
                tw = 44.0
                p.setFont(_f(size - 1, QFont.Normal, FONT_MONO))
                p.setPen(PAL.txt_faint)
                p.drawText(QRectF(x + w - tw, y, tw, 15),
                           Qt.AlignRight | Qt.AlignVCenter, el)

        name_x = x + prefix
        self._draw_name(p, QRectF(name_x, y, max(20.0, x + w - tw - 4 - name_x), 15),
                        size, it.workspace, it.title,
                        emphasis=running and lamp, dim=lamp and not running)

    def _more_line(self, p: QPainter, x: float, y: float, w: float,
                   more: int, size: float) -> None:
        p.setFont(_f(size))
        p.setPen(PAL.txt_faint)
        p.drawText(QRectF(x, y, w, 15), Qt.AlignLeft | Qt.AlignVCenter,
                   f"另有 {more} 个任务在跑（右键 → 任务清单）")

    def _task_detail(self, p: QPainter, x: float, y: float, w: float, it,
                     size: float, dim: bool = False) -> None:
        detail = (it.detail or "").strip()
        if not detail:
            return
        self._draw_elided(p, detail, _f(size + (0.0 if dim else 0.5)),
                          PAL.txt_faint if dim else PAL.txt_dim,
                          QRectF(x, y, w, 14))

    # ---- 标准卡：400×138（开满时的尺寸，关区块会缩）----
    def _paint_standard(self, p: QPainter, card: QRectF) -> None:
        keys = self._metric_keys()
        if not self._task_on():
            # 只剩指标区：不画分隔线，也不留状态灯的位置
            y = self._paint_metrics_std(p, card, card.top(), keys, divider=False)
            self._paint_strip(p, card, y)
            return
        rows, more = self._visible_rows()
        if len(rows) > 1 or more:
            self._paint_standard_multi(p, card, rows, more, keys)
            return
        stt = self.status
        color = self._status_color()
        pad = 14.0

        self._draw_lamp(p, card.left() + pad + 7, card.top() + 21, stt.state, color)

        text_x = card.left() + pad + 27
        row1_y = card.top() + 11
        row2_y = card.top() + 31

        f_label = _f(10.5 if stt.state == st.RUNNING else 10, QFont.DemiBold)
        p.setFont(f_label)
        p.setPen(color if stt.state != st.IDLE else PAL.txt_dim)
        label = self._status_label()
        p.drawText(QRectF(text_x, row1_y, 92, 19), Qt.AlignLeft | Qt.AlignVCenter, label)
        lw = QFontMetrics(f_label).horizontalAdvance(label)

        # 第一行右：任务名称（工作空间 - 对话名，空状态不放假占位）
        if stt.workspace or stt.title:
            self._draw_task_name(
                p,
                QRectF(text_x + lw + 10, row1_y, card.right() - pad - text_x - lw - 10, 19),
                9, align_right=True, emphasis=stt.state == st.RUNNING,
            )

        # 第二行左：细节（多源并发时带出其余任务数）
        detail = stt.detail or ("等待任务信号" if stt.state == st.IDLE else "")
        if stt.peers > 0:
            detail = f"{detail} · 另有 {stt.peers} 个" if detail else f"另有 {stt.peers} 个任务"
        elapsed_w = 62.0
        self._draw_elided(
            p, detail, _f(8.5), PAL.txt_dim,
            QRectF(text_x, row2_y, card.right() - pad - text_x - elapsed_w, 16),
        )

        # 第二行右：已持续时间（DONE 不显示，"刚刚"没有信息量）
        if stt.state in (st.RUNNING, st.WAITING, st.FAILED) and stt.ts:
            el = fmt_elapsed(time.time() - stt.ts)
            if el:
                p.setFont(_f(8, QFont.Normal, FONT_MONO))
                p.setPen(PAL.txt_faint)
                p.drawText(
                    QRectF(card.right() - pad - elapsed_w, row2_y, elapsed_w, 16),
                    Qt.AlignRight | Qt.AlignVCenter, el,
                )

        y = self._paint_metrics_std(p, card, card.top() + 56.0, keys)
        self._paint_strip(p, card, y)

    def _paint_metrics_std(self, p: QPainter, card: QRectF, y: float,
                           keys: tuple[str, ...], divider: bool = True) -> float:
        """画指标列，返回指标区下沿的 y。

        divider=True 在 y 处画分隔线；没有任务区时不画线，但内容同样从 y+8 起，
        否则文字会贴着卡片圆角。
        """
        if not keys:
            return y
        pad = 14.0
        if divider:
            p.setPen(QPen(PAL.divider, 1))
            p.drawLine(
                QPoint(int(card.left() + pad), int(y)),
                QPoint(int(card.right() - pad), int(y)),
            )
        y += 8.0
        m = self.metrics
        col_w = (card.width() - pad * 2) / len(keys)
        for i, k in enumerate(keys):
            x = card.left() + pad + col_w * i
            if k == "cpu":
                self._metric_col(
                    p, x, y, col_w, "CPU", f"{m.cpu:.0f}%",
                    f"{m.cores} 核" if m.cores else "", m.cpu,
                )
            elif k == "mem":
                self._metric_col(
                    p, x, y, col_w, "内存", f"{m.mem:.0f}%", m.mem_gb, m.mem,
                )
            elif k == "temp":
                self._metric_col(
                    p, x, y, col_w, "温度", self._temp_text(),
                    self._temp_note(), self.thermal.temp, TEMP_WARN, TEMP_HOT,
                )
            else:
                self._net_col(p, x, y, col_w)
        return y + (STD_METRICS_H - 8.0)

    def _paint_standard_multi(self, p: QPainter, card: QRectF,
                              rows: list, more: int,
                              keys: tuple[str, ...]) -> None:
        """多任务：每个任务两行（标识行 + 指令行），不再画大灯。

        大灯表达的是"唯一状态"，多任务同时存在 running / waiting 时它没有意义，
        换成一行的状态标签 + 各自的耗时，信息更准。
        """
        pad = 14.0
        x = card.left() + pad
        inner = card.width() - pad * 2
        # 状态词宽度 + 3px 间隙；指令行缩进与名称起点对齐。
        # 多一行首灯位后，名称/detail 都要整体右移，否则会压在灯上。
        indent = TASK_LAMP_W + 40.0 + 3.0
        y = card.top() + 11.0
        for it in rows:
            self._task_line(p, x, y, inner, it, 9, 40.0, lamp=True)
            self._task_detail(p, x + indent, y + 15, inner - indent - 44, it, 8.5,
                              dim=it.state != st.RUNNING)
            y += 30.0
        if more:
            self._more_line(p, x, y, inner, more, 8.5)

        y = self._paint_metrics_std(p, card, card.top() + self._std_task_h(), keys)
        self._paint_strip(p, card, y)

    # ---- 散热条（标准卡底部，仅在有会话时出现）----
    def _paint_strip(self, p: QPainter, card: QRectF, y: float) -> None:
        s = self.cool
        if s is None:
            self._stop_rect = QRectF()
            return

        pad = 14.0
        p.setPen(QPen(PAL.divider, 1))
        p.drawLine(
            QPoint(int(card.left() + pad), int(y)),
            QPoint(int(card.right() - pad), int(y)),
        )

        # 呼吸点：用信号灯同一套视觉语言，表示"有东西正被主动控制"
        ph = self._phase
        alpha = 120 + 90 * (0.5 + 0.5 * math.sin(ph * 2.2))
        cx, cy = card.left() + pad + 4.0, y + 14.0
        p.setPen(Qt.NoPen)
        halo = QColor(PAL.amber)
        halo.setAlpha(int(alpha * 0.35))
        p.setBrush(halo)
        p.drawEllipse(QRectF(cx - 5, cy - 5, 10, 10))
        core = QColor(PAL.amber)
        core.setAlpha(int(alpha))
        p.setBrush(core)
        p.drawEllipse(QRectF(cx - 2.4, cy - 2.4, 4.8, 4.8))

        tx = cx + 9
        f_label = _f(8.5, QFont.DemiBold)
        p.setFont(f_label)
        p.setPen(PAL.amber)
        p.drawText(QRectF(tx, y + 5, 78, 18), Qt.AlignLeft | Qt.AlignVCenter, s.label)
        lw = QFontMetrics(f_label).horizontalAdvance(s.label)

        if s.has_deadline():
            left = f"{fmt_dur(s.elapsed())} · 剩 {fmt_dur(s.remaining())}"
        else:
            left = f"已运行 {fmt_dur(s.elapsed())}"
        p.setFont(_f(7.5, QFont.Normal, FONT_MONO))
        p.setPen(PAL.txt_dim)
        p.drawText(QRectF(tx + lw + 8, y + 5, 130, 18), Qt.AlignLeft | Qt.AlignVCenter, left)

        # 停止热区：画出来就要能点，所以矩形存在这里给 mousePressEvent 用
        bw, bh = 44.0, 18.0
        r = QRectF(card.right() - pad - bw, y + 5, bw, bh)
        self._stop_rect = r
        border = QColor(PAL.amber)
        border.setAlpha(120)
        p.setBrush(theme_mod.tint(PAL.amber, 26))
        p.setPen(QPen(border, 1))
        p.drawRoundedRect(r, 9, 9)
        p.setPen(PAL.amber)
        p.setFont(_f(7.5, QFont.DemiBold))
        p.drawText(r, Qt.AlignCenter, "停止")

    # ---- 横条：320×64 ----
    def _paint_bar(self, p: QPainter, card: QRectF) -> None:
        if not self._task_on():
            self._paint_bar_metrics_only(p, card)
            return
        rows, more = self._visible_rows()
        if len(rows) > 1 or more:
            self._paint_bar_multi(p, card, rows, more)
            return
        stt = self.status
        color = self._status_color()
        pad = 12.0

        self._draw_lamp(p, card.left() + pad + 6, card.top() + 17, stt.state, color)

        text_x = card.left() + pad + 26
        f_label = _f(10 if stt.state == st.RUNNING else 9.5, QFont.DemiBold)
        p.setFont(f_label)
        p.setPen(color if stt.state != st.IDLE else PAL.txt_dim)
        label = self._status_label()
        p.drawText(QRectF(text_x, card.top() + 7, 60, 19), Qt.AlignLeft | Qt.AlignVCenter, label)
        lw = QFontMetrics(f_label).horizontalAdvance(label)

        if stt.workspace or stt.title:
            self._draw_task_name(
                p,
                QRectF(text_x + lw + 8, card.top() + 7, card.right() - pad - text_x - lw - 8, 19),
                8.5, align_right=True, emphasis=stt.state == st.RUNNING,
            )

        # 第二行：左详情 + 右指标串 +（散热中时）一个停止按钮
        micro = self._micro_metrics()
        mw = (QFontMetrics(_f(7, QFont.Normal, FONT_MONO)).horizontalAdvance(micro)
              if micro else 0.0)
        # 横条只有 64px 高，塞不下散热条，但不能因此没有停止入口 ——
        # 用户的主力版式可能就是横条，只能右键停等于把"停止"藏起来了。
        stop_w = 20.0 if self.cool is not None else 0.0
        detail = stt.detail or ("等待任务信号" if stt.state == st.IDLE else "")
        if stt.peers > 0:
            detail = f"{detail} · 另有 {stt.peers} 个" if detail else f"另有 {stt.peers} 个任务"
        self._draw_elided(
            p, detail, _f(8), PAL.txt_dim,
            QRectF(text_x, card.top() + 26,
                   card.right() - pad - text_x - mw - stop_w - 10, 16),
        )
        if micro:
            p.setFont(_f(7, QFont.Normal, FONT_MONO))
            p.setPen(PAL.metric_label)
            p.drawText(
                QRectF(card.right() - pad - stop_w - mw - 2, card.top() + 26, mw + 2, 16),
                Qt.AlignRight | Qt.AlignVCenter, micro,
            )
        if stop_w:
            r = QRectF(card.right() - pad - 13, card.top() + 27, 13, 13)
            self._stop_rect = r
            border = QColor(PAL.amber)
            border.setAlpha(130)
            p.setBrush(theme_mod.tint(PAL.amber, 30))
            p.setPen(QPen(border, 1))
            p.drawRoundedRect(r, 3.5, 3.5)
            p.setPen(PAL.amber)
            p.setFont(_f(8.5, QFont.DemiBold))
            p.drawText(r, Qt.AlignCenter, "×")

    def _paint_bar_metrics_only(self, p: QPainter, card: QRectF) -> None:
        """任务区关掉后的横条：一条只有硬件的细条。

        高度只剩 24px，没地方放状态词，也不该放 —— 这时候它不再是"信号灯"，
        就是一块贴边的系统监视条。
        """
        pad = 12.0
        micro = self._micro_metrics()
        stop_w = 20.0 if self.cool is not None else 0.0
        self._draw_elided(
            p, micro, _f(8, QFont.Normal, FONT_MONO), PAL.metric_value,
            QRectF(card.left() + pad, card.top(),
                   card.width() - pad * 2 - stop_w, card.height()),
        )
        if stop_w:
            r = QRectF(card.right() - pad - 13,
                       card.top() + (card.height() - 13) / 2.0, 13, 13)
            self._stop_rect = r
            border = QColor(PAL.amber)
            border.setAlpha(130)
            p.setBrush(theme_mod.tint(PAL.amber, 30))
            p.setPen(QPen(border, 1))
            p.drawRoundedRect(r, 3.5, 3.5)
            p.setPen(PAL.amber)
            p.setFont(_f(8.5, QFont.DemiBold))
            p.drawText(r, Qt.AlignCenter, "×")

    def _paint_bar_multi(self, p: QPainter, card: QRectF,
                         rows: list, more: int) -> None:
        """多任务横条：一个任务一行，指标串压到最后一行。

        横条本来就只给 64px，多任务时不再显示 detail —— 在 320px 宽里
        "谁在跑"比"跑的是哪一条指令"更需要一眼看清。
        """
        pad = 12.0
        x = card.left() + pad
        inner = card.width() - pad * 2
        y = card.top() + 6.0
        for it in rows:
            self._task_line(p, x, y, inner, it, 8.5, 36.0, lamp=True, row_h=22.0)
            y += 22.0
        if more:
            self._more_line(p, x, y, inner, more, 8.0)
            y += 22.0

        micro = self._micro_metrics()
        mw = (QFontMetrics(_f(7, QFont.Normal, FONT_MONO)).horizontalAdvance(micro)
              if micro else 0.0)
        stop_w = 20.0 if self.cool is not None else 0.0
        if micro:
            p.setFont(_f(7, QFont.Normal, FONT_MONO))
            p.setPen(PAL.metric_label)
            p.drawText(QRectF(card.right() - pad - stop_w - mw - 2, y, mw + 2, 16),
                       Qt.AlignRight | Qt.AlignVCenter, micro)
        if stop_w:
            r = QRectF(card.right() - pad - 13, y + 1, 13, 13)
            self._stop_rect = r
            border = QColor(PAL.amber)
            border.setAlpha(130)
            p.setBrush(theme_mod.tint(PAL.amber, 30))
            p.setPen(QPen(border, 1))
            p.drawRoundedRect(r, 3.5, 3.5)
            p.setPen(PAL.amber)
            p.setFont(_f(8.5, QFont.DemiBold))
            p.drawText(r, Qt.AlignCenter, "×")

    def _micro_metrics(self) -> str:
        """横条上的指标串，按当前开启的指标列拼。列全关时返回空串。

        标签省到最短，靠符号自解释：CPU 有名字（不然 33% 不知道是什么），
        内存跟在后面省略、温度只给度数、网络用箭头。
        散热进行中时额外前置剩余时间 —— 横条只有 274px，
        硬塞会和左侧任务详情打架，所以散热态下不再显示内存和上行。
        """
        keys = self._metric_keys()
        if not keys:
            return ""
        m = self.metrics
        parts = []
        if self.cool is not None:
            parts.append(
                f"散热{fmt_short(self.cool.remaining())}"
                if self.cool.has_deadline() else "散热中"
            )
        if "cpu" in keys:
            parts.append(f"CPU {m.cpu:.0f}%")
        if "mem" in keys:
            parts.append(f"{m.mem:.0f}%")
        if "temp" in keys:
            t = self.thermal.temp
            parts.append(f"{t:.0f}°" if t is not None else "—")
        if "net" in keys:
            parts.append("↓" + metrics_mod.fmt_rate(m.down))
        return " · ".join(parts)

    # ---- 竖版：200×248（开满时）----
    def _paint_stack(self, p: QPainter, card: QRectF) -> None:
        keys = self._metric_keys()
        task_on = self._task_on()
        rows, more = self._visible_rows()
        multi = task_on and (len(rows) > 1 or more)
        stt = self.status
        color = self._status_color()
        pad = 14.0
        inner_w = card.width() - pad * 2
        m = self.metrics

        if task_on:
            # 多任务时每行自带色点，大灯再占一块纯属浪费 —— 而且会和
            # 第一行的圆点挤在一起，看起来像两个灯叠着。
            if not multi:
                self._draw_lamp(p, card.left() + pad + 7, card.top() + 22, stt.state, color)

            text_x = card.left() + pad + (0.0 if multi else 24.0)
            p.setFont(_f(10.5 if stt.state == st.RUNNING else 10, QFont.DemiBold))
            p.setPen(color if stt.state != st.IDLE else PAL.txt_dim)
            p.drawText(
                QRectF(text_x, card.top() + 12, card.right() - pad - text_x, 19),
                Qt.AlignLeft | Qt.AlignVCenter, self._status_label(),
            )

            if multi:
                y = card.top() + 35.0
                for it in rows:
                    self._task_line(p, card.left() + pad, y, inner_w, it, 8.5, 12.0,
                                    dot=True, lamp=True)
                    self._task_detail(p, card.left() + pad + TASK_LAMP_W + 3.0, y + 15,
                                      inner_w - TASK_LAMP_W - 3.0, it, 7.5,
                                      dim=it.state != st.RUNNING)
                    y += 30.0
                if more:
                    self._more_line(p, card.left() + pad, y, inner_w, more, 7.5)
            else:
                if stt.workspace or stt.title:
                    self._draw_task_name(
                        p, QRectF(card.left() + pad, card.top() + 35, inner_w, 15),
                        8.5, emphasis=stt.state == st.RUNNING,
                    )
                detail = stt.detail or ("等待任务信号" if stt.state == st.IDLE else "")
                if stt.peers > 0:
                    detail = f"{detail} · 另有 {stt.peers} 个" if detail else f"另有 {stt.peers} 个任务"
                if stt.state in (st.RUNNING, st.WAITING, st.FAILED) and stt.ts:
                    el = fmt_elapsed(time.time() - stt.ts)
                    if el:
                        detail = f"{detail} · {el}" if detail else el
                self._draw_elided(
                    p, detail, _f(7.5), PAL.txt_faint,
                    QRectF(card.left() + pad, card.top() + 51, inner_w, 14),
                )

            p.setPen(QPen(PAL.divider, 1))
            dy = card.top() + self._stack_task_h()
            p.drawLine(
                QPoint(int(card.left() + pad), int(dy)),
                QPoint(int(card.right() - pad), int(dy)),
            )

        base = card.top() + (self._stack_task_h() if task_on else 0.0)
        my = base + (STACK_GAP_TASK if task_on else STACK_GAP_HEAD)
        for i, k in enumerate(keys):
            y = my + STACK_ROW_H * i
            if k == "cpu":
                self._stack_row(p, card.left() + pad, y, inner_w, "CPU",
                                f"{m.cpu:.0f}%", pct=m.cpu,
                                sub=f"{m.cores} 核" if m.cores else "")
            elif k == "mem":
                self._stack_row(p, card.left() + pad, y, inner_w, "内存",
                                f"{m.mem:.0f}%", pct=m.mem, sub=m.mem_gb)
            elif k == "temp":
                self._stack_row(p, card.left() + pad, y, inner_w, "温度",
                                self._temp_text(), pct=self.thermal.temp,
                                sub=self._temp_note(), warn=TEMP_WARN, hot=TEMP_HOT)
            else:
                self._stack_row(p, card.left() + pad, y, inner_w, "网络",
                                "↓" + metrics_mod.fmt_rate(m.down),
                                sub="↑" + metrics_mod.fmt_rate(m.up))

        if self.cool is not None:
            end = my + STACK_ROW_H * len(keys)
            # 有指标行时上提 2px：最后一行的小字会越出 36px 名义行高一丁点，
            # 不让开就会和分隔线叠在一起。
            self._paint_stack_strip(p, card, end - (2.0 if keys else 0.0))

    def _paint_stack_strip(self, p: QPainter, card: QRectF, y: float) -> None:
        s = self.cool
        pad = 14.0
        # 四行指标最后一行的副文字（↑上行）落在上一行下沿，分隔线必须让开，
        # 否则"↑5.2K"会和"风扇全速"叠在一起 —— 这个位置是量出来的，不是估的。
        p.setPen(QPen(PAL.divider, 1))
        p.drawLine(
            QPoint(int(card.left() + pad), int(y)),
            QPoint(int(card.right() - pad), int(y)),
        )

        cx, cy = card.left() + pad + 3.0, y + 11.0
        alpha = 120 + 90 * (0.5 + 0.5 * math.sin(self._phase * 2.2))
        p.setPen(Qt.NoPen)
        core = QColor(PAL.amber)
        core.setAlpha(int(alpha))
        p.setBrush(core)
        p.drawEllipse(QRectF(cx - 2.4, cy - 2.4, 4.8, 4.8))

        p.setFont(_f(8, QFont.DemiBold))
        p.setPen(PAL.amber)
        p.drawText(QRectF(cx + 9, y + 3, 76, 16), Qt.AlignLeft | Qt.AlignVCenter, s.label)

        bw, bh = 40.0, 15.0
        r = QRectF(card.right() - pad - bw, y + 3, bw, bh)
        self._stop_rect = r
        border = QColor(PAL.amber)
        border.setAlpha(110)
        p.setBrush(theme_mod.tint(PAL.amber, 22))
        p.setPen(QPen(border, 1))
        p.drawRoundedRect(r, 7.5, 7.5)
        p.setPen(PAL.amber)
        p.setFont(_f(7, QFont.DemiBold))
        p.drawText(r, Qt.AlignCenter, "停止")

        tip = f"剩 {fmt_dur(s.remaining())}" if s.has_deadline() else f"已运行 {fmt_dur(s.elapsed())}"
        p.setFont(_f(7, QFont.Normal, FONT_MONO))
        p.setPen(PAL.txt_dim)
        p.drawText(
            QRectF(card.left() + pad, y + 21, card.width() - pad * 2, 12),
            Qt.AlignLeft | Qt.AlignVCenter, tip,
        )

    def _stack_row(
        self, p: QPainter, x: float, y: float, w: float, label: str, value: str,
        pct: float | None = None, sub: str = "", warn: float = 75.0, hot: float = 90.0,
    ) -> None:
        alert = pct is not None and pct >= warn
        p.setFont(_f(7))
        p.setPen(PAL.metric_label)
        p.drawText(QRectF(x, y, w * 0.45, 12), Qt.AlignLeft | Qt.AlignVCenter, label)

        vcol, bcol = self._level_color(pct, warn, hot)
        p.setFont(_f(9.5, QFont.Normal, FONT_MONO))
        p.setPen(vcol if alert else PAL.metric_value)
        p.drawText(
            QRectF(x + w * 0.45, y - 3, w * 0.55, 18),
            Qt.AlignRight | Qt.AlignVCenter, value,
        )
        if pct is not None:
            self._bar(p, x, y + 17, w, 2.5, pct / 100.0,
                      bcol if alert else PAL.metric_bar)
        elif sub:
            p.setFont(_f(7))
            p.setPen(PAL.metric_label)
            p.drawText(
                QRectF(x + w * 0.55, y + 15, w * 0.45, 12),
                Qt.AlignRight | Qt.AlignVCenter, sub,
            )

    def _metric_col(
        self, p: QPainter, x: float, y: float, w: float, label: str,
        value: str, sub: str, pct: float | None, warn: float = 75.0, hot: float = 90.0,
    ) -> None:
        # 硬件指标整体退一档：字号、字重、颜色都低于任务区。
        # 但告警（>= warn）必须保留原来的黄/红色，安全信息不能被"弱化"掉。
        alert = pct is not None and pct >= warn
        vcol, bcol = self._level_color(pct, warn, hot)
        p.setFont(_f(7))
        p.setPen(PAL.metric_label)
        p.drawText(QRectF(x, y, w, 12), Qt.AlignLeft | Qt.AlignVCenter, label)

        p.setFont(_f(9.5, QFont.Normal, FONT_MONO))
        p.setPen(vcol if alert else PAL.metric_value)
        p.drawText(QRectF(x, y + 13, w, 18), Qt.AlignLeft | Qt.AlignVCenter, value)

        if sub:
            p.setFont(_f(7))
            p.setPen(PAL.metric_label)
            p.drawText(QRectF(x, y + 32, w, 12), Qt.AlignLeft | Qt.AlignVCenter, sub)

        self._bar(p, x, y + 46, w - 6, 2.5, (pct or 0.0) / 100.0,
                  bcol if alert else PAL.metric_bar)

    def _net_col(self, p: QPainter, x: float, y: float, w: float) -> None:
        p.setFont(_f(7))
        p.setPen(PAL.metric_label)
        p.drawText(QRectF(x, y, w, 12), Qt.AlignLeft | Qt.AlignVCenter, "网络")

        p.setFont(_f(9.5, QFont.Normal, FONT_MONO))
        p.setPen(PAL.metric_value)
        p.drawText(
            QRectF(x, y + 13, w, 18), Qt.AlignLeft | Qt.AlignVCenter,
            "↓" + metrics_mod.fmt_rate(self.metrics.down),
        )
        p.setFont(_f(7))
        p.setPen(PAL.metric_label)
        p.drawText(
            QRectF(x, y + 32, w, 12), Qt.AlignLeft | Qt.AlignVCenter,
            "↑" + metrics_mod.fmt_rate(self.metrics.up),
        )

    def _level_color(self, pct: float | None, warn: float, hot: float) -> tuple[QColor, QColor]:
        if pct is None:
            return PAL.txt_faint, PAL.bar_blue
        if pct >= hot:
            return PAL.state(st.FAILED), PAL.state(st.FAILED)
        if pct >= warn:
            return PAL.state(st.WAITING), PAL.state(st.WAITING)
        return PAL.txt, PAL.bar_blue

    def _bar(self, p: QPainter, x: float, y: float, w: float, h: float,
             frac: float, color: QColor) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(PAL.track)
        p.drawRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
        fw = max(0.0, min(1.0, frac)) * w
        if fw > 0.5:
            p.setBrush(color)
            p.drawRoundedRect(QRectF(x, y, max(h, fw), h), h / 2, h / 2)

    def _temp_text(self) -> str:
        t = self.thermal.temp
        return f"{t:.0f}°" if t is not None else "—"

    def _temp_note(self) -> str:
        if self.thermal.temp is not None:
            return self.thermal.label or "设备"
        return "无传感器" if self.thermal.probed else ""

    # 任务名称 = 工作空间 - 对话名。
    # 为什么不合成一个字符串丢给 elidedText：宽度不够时它从右往左截，
    # 恰好先砍掉对话名（信息主体），留下工作空间（长期不变的上下文）。
    # 所以这里按真实可用宽度做决策：放得下就分段画，放不下只留对话名。
    def _draw_name(self, p: QPainter, rect: QRectF, size: float, work: str, name: str,
                   align_right: bool = False, emphasis: bool = False,
                   dim: bool = False) -> None:
        work = (work or "").strip()
        name = (name or "").strip()
        if not work and not name:
            return
        # emphasis：执行中的任务名加粗提亮；dim：同一列表里其它任务降一级。
        f = _f(size + (0.5 if emphasis else 0),
               QFont.DemiBold if emphasis else QFont.Normal)
        p.setFont(f)
        fm = QFontMetrics(f)
        h = rect.height()
        side = Qt.AlignRight if align_right else Qt.AlignLeft
        main_col = PAL.txt_dim if dim else PAL.txt
        sub_col = PAL.txt_faint if dim else PAL.txt_dim

        # 只有一段（工作空间或对话名）：直接画，省掉分隔符
        if not work or not name:
            p.setPen(main_col if name else sub_col)
            p.drawText(rect, side | Qt.AlignVCenter,
                       fm.elidedText(name or work, Qt.ElideRight, int(rect.width())))
            return

        w_work = fm.horizontalAdvance(work)
        w_sep = fm.horizontalAdvance(TASK_SEP)
        w_name = fm.horizontalAdvance(name)
        if w_work + w_sep + w_name > rect.width():
            # 窄行里也要尽量保住「工作空间 - 对话名」这个结构：先给工作空间
            # 留位，剩下的宽度才对对话名做省略，别一超宽就把工作空间整段丢掉。
            keep = rect.width() - w_work - w_sep - 2
            if not align_right and keep >= 26:
                p.setPen(sub_col)
                p.drawText(QRectF(rect.left(), rect.top(), w_work + 1, h),
                           Qt.AlignLeft | Qt.AlignVCenter, work)
                p.setPen(PAL.txt_faint)
                p.drawText(QRectF(rect.left() + w_work, rect.top(), w_sep + 1, h),
                           Qt.AlignLeft | Qt.AlignVCenter, TASK_SEP)
                p.setPen(main_col)
                p.drawText(QRectF(rect.left() + w_work + w_sep, rect.top(), keep, h),
                           Qt.AlignLeft | Qt.AlignVCenter,
                           fm.elidedText(name, Qt.ElideRight, int(keep)))
                return
            p.setPen(main_col)
            p.drawText(rect, side | Qt.AlignVCenter,
                       fm.elidedText(name, Qt.ElideRight, int(rect.width())))
            return

        x = rect.right() - (w_work + w_sep + w_name) if align_right else rect.left()
        p.setPen(sub_col)
        p.drawText(QRectF(x, rect.top(), w_work + 1, h), Qt.AlignLeft | Qt.AlignVCenter, work)
        x += w_work
        p.setPen(PAL.txt_faint)
        p.drawText(QRectF(x, rect.top(), w_sep + 1, h), Qt.AlignLeft | Qt.AlignVCenter, TASK_SEP)
        x += w_sep
        p.setPen(main_col)
        p.drawText(QRectF(x, rect.top(), w_name + 1, h), Qt.AlignLeft | Qt.AlignVCenter, name)

    def _draw_task_name(self, p: QPainter, rect: QRectF, size: float,
                        align_right: bool = False, emphasis: bool = False) -> None:
        self._draw_name(p, rect, size, self.status.workspace, self.status.title,
                        align_right, emphasis=emphasis)

    def _draw_elided(self, p: QPainter, text: str, font: QFont, color: QColor,
                     rect: QRectF, align_right: bool = False) -> None:
        if not text:
            return
        p.setFont(font)
        p.setPen(color)
        fm = QFontMetrics(font)
        elided = fm.elidedText(text, Qt.ElideRight, int(rect.width()))
        p.drawText(rect, (Qt.AlignRight if align_right else Qt.AlignLeft) | Qt.AlignVCenter, elided)

    def _row_lamp(self, p: QPainter, cx: float, cy: float, state: str,
                  color: QColor, r: float) -> None:
        """多任务行状态灯：带光晕的实心灯，直径 12~14px。

        原来每行只有一个 6×6 的小色点，三行并排时几乎看不出状态；
        这枚灯和单任务大灯是同一套视觉语言（光晕 + 灯芯 + 高光），
        只是不做旋转动画，避免多行同时闪成一团。
        """
        p.setPen(Qt.NoPen)
        glow = 95.0 if state == st.RUNNING else 72.0
        outer = r * 2.4
        gr = QRadialGradient(cx, cy, outer)
        c0 = QColor(color)
        c0.setAlpha(int(glow))
        c1 = QColor(color)
        c1.setAlpha(int(glow * 0.30))
        c2 = QColor(color)
        c2.setAlpha(0)
        gr.setColorAt(0.0, c0)
        gr.setColorAt(0.45, c1)
        gr.setColorAt(1.0, c2)
        p.setBrush(gr)
        p.drawEllipse(QRectF(cx - outer, cy - outer, outer * 2, outer * 2))

        if state == st.RUNNING:
            ring = QColor(color)
            ring.setAlpha(150)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ring, 1.2, Qt.SolidLine, Qt.RoundCap))
            rr = r + 2.5
            p.drawEllipse(QRectF(cx - rr, cy - rr, rr * 2, rr * 2))
            p.setPen(Qt.NoPen)

        core = QColor(color)
        if state == st.IDLE:
            core.setAlpha(190)
        p.setBrush(core)
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        p.setBrush(QColor(255, 255, 255, 70))
        p.drawEllipse(QRectF(cx - r * 0.42, cy - r * 0.62, r * 0.62, r * 0.5))

    def _draw_lamp(self, p: QPainter, cx: float, cy: float, state: str, color: QColor) -> None:
        base = LAMP_BASE
        ph = self._phase
        t_since = time.time() - self._state_t0
        r = base
        glow = 60.0

        if state == st.IDLE:
            glow = 26 + 10 * (0.5 + 0.5 * math.sin(ph * 0.55))
        elif state == st.RUNNING:
            r = base + 0.7 * math.sin(ph * 2.0)
            glow = 95 + 38 * (0.5 + 0.5 * math.sin(ph * 2.0))
        elif state == st.WAITING:
            blink = 0.5 + 0.5 * math.sin(ph * 2.6)
            glow = 60 + 90 * blink
            r = base + 1.4 * blink
        elif state == st.DONE:
            pulse = max(0.0, 1.0 - (t_since % 1.6) / 1.6)
            glow = 110 + 80 * pulse
            r = base + 0.9 * pulse
        elif state == st.FAILED:
            glow = 150 + 45 * (0.5 + 0.5 * math.sin(ph * 1.3))
            r = base

        # 外发光（半径 22：灯芯加大后要跟着涨一点，再大会压到右侧状态文字）
        p.setPen(Qt.NoPen)
        gr = QRadialGradient(cx, cy, 22)
        c0 = QColor(color)
        c0.setAlpha(int(max(0, min(255, glow))))
        c1 = QColor(color)
        c1.setAlpha(int(max(0, min(255, glow * 0.30))))
        c2 = QColor(color)
        c2.setAlpha(0)
        gr.setColorAt(0.0, c0)
        gr.setColorAt(0.45, c1)
        gr.setColorAt(1.0, c2)
        p.setBrush(gr)
        p.drawEllipse(QRectF(cx - 22, cy - 22, 44, 44))

        # running: 旋转弧
        if state == st.RUNNING:
            p.setBrush(Qt.NoBrush)
            ring = QColor(color)
            ring.setAlpha(170)
            p.setPen(QPen(ring, 1.6, Qt.SolidLine, Qt.RoundCap))
            rect = QRectF(cx - 14, cy - 14, 28, 28)
            start = int((math.degrees(ph * 3.2)) % 360)
            p.drawArc(rect, start * 16, 70 * 16)

        # done: 扩散一环
        if state == st.DONE and t_since < 1.1:
            k = t_since / 1.1
            p.setBrush(Qt.NoBrush)
            rc = QColor(color)
            rc.setAlpha(int(170 * (1 - k)))
            p.setPen(QPen(rc, 1.6))
            rr = 8 + 16 * k
            p.drawEllipse(QRectF(cx - rr, cy - rr, rr * 2, rr * 2))

        # 灯芯
        p.setPen(Qt.NoPen)
        core = QColor(color)
        if state == st.IDLE:
            core.setAlpha(190)
        p.setBrush(core)
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # 高光
        p.setBrush(QColor(255, 255, 255, 70))
        p.drawEllipse(QRectF(cx - r * 0.42, cy - r * 0.62, r * 0.62, r * 0.5))

    # ---------------- 交互 ----------------

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton:
            # 散热条上的"停止"是真按钮：命中它就不再进入拖拽，
            # 否则会出现"想点停止结果把窗口拖走了"
            if self._stop_rect.isValid():
                pos = e.position()
                base = (pos.x() / self.scale, pos.y() / self.scale)
                if self._stop_rect.contains(QPointF(*base)):
                    self.stop_cooling()
                    e.accept()
                    return
            self._drag_from = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._drag_from is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_from)
            e.accept()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._drag_from is not None:
            self._drag_from = None
            self.settings["x"] = self.x()
            self.settings["y"] = self.y()
            st.save_settings(self.settings)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton:
            self.toggle_collapse()

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        self._menu().exec(e.globalPos())

    def toggle_collapse(self) -> None:
        """双击在标准卡与横条之间切换，保留原来的"折叠"手感。"""
        self.set_style(STYLE_STANDARD if self.style == STYLE_BAR else STYLE_BAR)

    def set_style(self, style: str) -> None:
        if style not in BASE_SIZE:
            return
        self.style = style
        self.settings["style"] = style
        self.settings["collapsed"] = style == STYLE_BAR  # 兼容旧版本的字段
        st.save_settings(self.settings)
        self._apply_size(keep_center=True)
        self.update()

    def set_scale(self, scale: float) -> None:
        self.scale = float(scale)
        self.settings["scale"] = self.scale
        st.save_settings(self.settings)
        self._apply_size(keep_center=True)
        self.update()

    def set_on_top(self, on: bool) -> None:
        self.settings["on_top"] = on
        st.save_settings(self.settings)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        self.show()

    # ---------------- 主题 ----------------
    def _apply_theme(self, tid: str, save: bool = True) -> bool:
        """落到具体主题。返回是否真的变了，避免每帧无谓重绘。"""
        if tid == self._theme_id:
            return False
        self._theme_id = tid
        PAL.set(tid)
        panel = getattr(self, "panel", None)
        if panel is not None:
            panel.apply_theme()
        if save:
            self.settings["theme"] = self._theme_pref
            st.save_settings(self.settings)
        if getattr(self, "tray", None) is not None:
            self._update_tray()
        self.update()
        return True

    def set_theme(self, pref: str) -> None:
        self._theme_pref = theme_mod.sanitize(pref)
        self.settings["theme"] = self._theme_pref
        st.save_settings(self.settings)
        self._apply_theme(theme_mod.resolve(self._theme_pref), save=False)
        self.update()

    def _on_system_scheme(self, *_args) -> None:
        if self._theme_pref == "auto":
            self._apply_theme(theme_mod.windows_scheme(), save=False)

    def set_show(self, key: str, value: bool) -> None:
        """切换某个显示元素。

        唯一需要拦的情况是"两个区块都关"——那会剩一张空卡片，
        既没有状态也没有数据，纯属占屏幕。拦下来并提示，比让它变成
        一个不知道是什么的矩形要好。
        """
        if key not in st.SHOW_KEYS:
            return
        old = self.settings.get(key)
        self.settings[key] = bool(value)
        if not (self._task_on() or self._metric_keys()):
            self.settings[key] = old
            self._notify("两个区块不能都关掉\n至少保留一个显示元素", "AI 工作信号灯")
            return
        st.save_settings(self.settings)
        self._apply_size(keep_center=True)
        self.update()

    def _menu(self) -> QMenu:
        m = QMenu(self)
        m.setStyleSheet(theme_mod.menu_stylesheet(PAL))
        a_top = QAction("窗口置顶", m)
        a_top.setCheckable(True)
        a_top.setChecked(bool(self.settings.get("on_top", True)))
        a_top.triggered.connect(lambda v: self._toggle_flag("on_top", v))
        m.addAction(a_top)

        m.addSeparator()

        # 样式（三选一）
        sm = m.addMenu("样式")
        g1 = QActionGroup(sm)
        g1.setExclusive(True)
        for sid in STYLE_ORDER:
            a = QAction(f"{STYLE_NAMES[sid]}   {STYLE_HINTS[sid]}", sm)
            a.setCheckable(True)
            a.setChecked(self.style == sid)
            a.triggered.connect(lambda _c=False, s=sid: self.set_style(s))
            g1.addAction(a)
            sm.addAction(a)

        # 尺寸（四档）
        zm = m.addMenu("尺寸")
        g2 = QActionGroup(zm)
        g2.setExclusive(True)
        for val, name in SCALE_STEPS:
            a = QAction(f"{name}   {int(round(val * 100))}%", zm)
            a.setCheckable(True)
            a.setChecked(abs(self.scale - val) < 0.01)
            a.triggered.connect(lambda _c=False, v=val: self.set_scale(v))
            g2.addAction(a)
            zm.addAction(a)

        # 显示元素：两个区块 + 四个指标列。关到空壳会被 set_show 挡住。
        vm = m.addMenu("显示元素")
        a_task = QAction("AI 任务（状态与明细）", vm)
        a_task.setCheckable(True)
        a_task.setChecked(self._task_on())
        a_task.triggered.connect(lambda v: self.set_show("show_task", v))
        vm.addAction(a_task)

        vm.addSeparator()
        mm = vm.addMenu("硬件指标")
        n_on = len(self._metric_keys())
        a_blk = QAction("整个区块" + (f"（{n_on} 项）" if n_on else "（已关）"), mm)
        a_blk.setCheckable(True)
        a_blk.setChecked(bool(self.settings.get("show_metrics", True)))
        a_blk.triggered.connect(lambda v: self.set_show("show_metrics", v))
        mm.addAction(a_blk)
        mm.addSeparator()
        for k in st.METRIC_KEYS:
            a = QAction(METRIC_NAMES[k], mm)
            a.setCheckable(True)
            a.setChecked(bool(self.settings.get("show_" + k, True)))
            a.triggered.connect(lambda v, key=k: self.set_show("show_" + key, v))
            mm.addAction(a)

        # 主题：跟随系统 / 浅色 / 深色
        thm = m.addMenu("主题")
        g3 = QActionGroup(thm)
        g3.setExclusive(True)
        for tid, label in (("auto", "跟随系统"), ("light", "浅色"), ("dark", "深色")):
            a_t = QAction(label, thm)
            a_t.setCheckable(True)
            a_t.setChecked(self._theme_pref == tid)
            a_t.triggered.connect(lambda _c=False, t=tid: self.set_theme(t))
            g3.addAction(a_t)
            thm.addAction(a_t)
        thm.addSeparator()
        a_now = QAction(
            f"当前：{'浅色' if PAL.name == 'light' else '深色'}"
            + ("（跟随系统）" if self._theme_pref == "auto" else "（手动）"),
            thm,
        )
        a_now.setEnabled(False)
        thm.addAction(a_now)

        # 温度来源
        tm = m.addMenu("温度")
        info = QAction(self._temp_menu_text(), tm)
        info.setEnabled(False)
        tm.addAction(info)
        a_re = QAction("重新检测传感器", tm)
        a_re.triggered.connect(self._reprobe_temp)
        tm.addAction(a_re)
        a_cmd = QAction("自定义温度命令…", tm)
        a_cmd.triggered.connect(self._ask_temp_cmd)
        tm.addAction(a_cmd)

        # 散热：原来独立的 FanSpin 脚本，现在是挂件里的一个模块
        m.addMenu(self._cooling_menu(m))

        # 任务清单：卡片上最多 3 行，其余的必须有个地方能看到。
        # 全部做成不可点击的展示项 —— 这里只回答"现在有几个、分别是什么"。
        tl = m.addMenu(f"任务清单（{len(self.rows)}）")
        if not self.rows:
            a_none = QAction("当前没有任务", tl)
            a_none.setEnabled(False)
            tl.addAction(a_none)
        for it in self.rows[:8]:
            nm = " - ".join(x for x in (it.workspace, it.title) if x) or "任务"
            a_it = QAction(f"{st.LABELS.get(it.state, it.state)} · {nm}", tl)
            a_it.setEnabled(False)
            tl.addAction(a_it)
        if len(self.rows) > 8:
            a_more = QAction(f"…还有 {len(self.rows) - 8} 个", tl)
            a_more.setEnabled(False)
            tl.addAction(a_more)

        m.addSeparator()
        a_boot = QAction("开机自启", m)
        a_boot.setCheckable(True)
        a_boot.setChecked(self._autostart_enabled())
        a_boot.triggered.connect(lambda v: self._set_autostart(v))
        m.addAction(a_boot)

        a_dir = QAction("打开状态目录", m)
        a_dir.triggered.connect(self._open_dir)
        m.addAction(a_dir)

        a_demo = QAction("演示五种状态", m)
        a_demo.triggered.connect(self._start_demo)
        m.addAction(a_demo)

        m.addSeparator()
        a_quit = QAction("退出", m)
        a_quit.triggered.connect(QApplication.quit)
        m.addAction(a_quit)
        return m

    def _toggle_flag(self, key: str, value: bool) -> None:
        if key == "on_top":
            self.set_on_top(value)

    def _cooling_menu(self, parent: QMenu) -> QMenu:
        """散热菜单。所有不可用项都带原因，且不隐藏 ——
        藏起来用户会以为功能丢了，显示"不可用（本机无此接口）"才是有效信息。"""
        cap = self.cooling.cap
        cm = QMenu("散热", parent)
        s = self.cool

        if s is not None:
            info = f"当前：{s.label} · {fmt_dur(s.elapsed())}"
            if s.has_deadline():
                info += f" · 剩 {fmt_dur(s.remaining())}"
            a = QAction(info, cm)
            a.setEnabled(False)
            cm.addAction(a)
        else:
            a = QAction(f"当前：{cap.summary()}", cm)
            a.setEnabled(False)
            cm.addAction(a)

        cm.addSeparator()

        a_panel = QAction("散热面板…", cm)
        a_panel.triggered.connect(self.toggle_panel)
        cm.addAction(a_panel)

        cm.addSeparator()

        for mode, label in (
            (cooling_mod.MODE_EC, "风扇全速（EC 直控）"),
            (cooling_mod.MODE_BURN, "持续负载（烧 CPU）"),
            (cooling_mod.MODE_SPIN, "电源锁定（最小 100%）"),
        ):
            ok, why = cap.mode_state(mode)
            text = label if ok else f"{label} · 不可用"
            a = QAction(text, cm)
            a.setEnabled(ok and s is None)
            if not ok:
                a.setToolTip(why)
            elif mode == cooling_mod.MODE_EC:
                a.setToolTip(cooling_mod.MODE_HINTS[mode])
            a.triggered.connect(lambda _c=False, m=mode: self.start_cooling(m))
            cm.addAction(a)

        cm.addSeparator()

        a_stop = QAction("停止并恢复", cm)
        a_stop.setEnabled(s is not None)
        a_stop.triggered.connect(self.stop_cooling)
        cm.addAction(a_stop)

        if cooling_mod.BACKUP_FILE.exists():
            a_rest = QAction("还原电源设置（需管理员）", cm)
            a_rest.triggered.connect(self._restore_power_async)
            cm.addAction(a_rest)

        a_re = QAction("重新检测散热能力", cm)
        a_re.triggered.connect(self._reprobe_cooling)
        cm.addAction(a_re)
        return cm

    def _reprobe_cooling(self) -> None:
        self.cooling.cap.refresh(deep=True)
        self._notify(self.cooling.cap.summary())

    def _restore_power_async(self) -> None:
        """还原电源要提权，UAC 对话框会阻塞调用线程 —— 不能让它卡住 30fps 绘制。"""
        def run() -> None:
            _ok, msg = self.cooling.restore_power()
            self.cooling.post_notice(msg)

        threading.Thread(target=run, name="cool-restore", daemon=True).start()

    def _temp_menu_text(self) -> str:
        t = self.thermal
        if t.temp is not None:
            src = SRC_NAMES.get(t.source, t.source or "?")
            return f"当前：{t.label or '设备'} {t.temp:.0f}°（{src}）"
        return "当前：检测中…" if not t.probed else "当前：无可用传感器"

    def _reprobe_temp(self) -> None:
        self.thermal.reprobe()

    def _ask_temp_cmd(self) -> None:
        cur = str(self.settings.get("temp_cmd") or "")
        # 手动搭对话框而不是用 QInputDialog.getText：挂件是置顶的无边框窗口，
        # 随手弹出的模态框可能被压在别的窗口后面，用户只能去任务栏找。
        dlg = QInputDialog(self)
        dlg.setStyleSheet(theme_mod.panel_stylesheet(PAL))
        dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dlg.setInputMode(QInputDialog.TextInput)
        dlg.setWindowTitle("自定义温度命令")
        dlg.setLabelText("命令输出里第一个 15~115 的数字会当作温度（留空 = 自动检测）：")
        dlg.setTextValue(cur)
        dlg.resize(520, 130)
        self.activateWindow()
        if dlg.exec() != QDialog.Accepted:
            return
        cmd = dlg.textValue().strip()
        self.settings["temp_cmd"] = cmd
        st.save_settings(self.settings)
        self.thermal.stop()
        self.thermal = metrics_mod.Thermal(cmd=cmd)
        self.update()

    # ---------------- 系统集成 ----------------

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("AI 工作信号灯")
        self.tray.setIcon(self._make_icon(PAL.state(st.IDLE)))
        self.tray.activated.connect(self._tray_click)
        self.tray.show()

    def _tray_click(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.raise_()

    def _update_tray(self) -> None:
        color = PAL.state(self.status.state)
        self.tray.setIcon(self._make_icon(color))
        tip = "AI 工作信号灯\n" + self.status.label
        rows, more = self._visible_rows()
        if len(self.rows) > 1:
            # 多任务：逐个列出来。托盘提示是唯一没有宽度限制的地方，
            # 卡片上挤不下的信息在这里补全。
            tip += f"\n{len(self.rows)} 个任务并行"
            for it in rows:
                nm = " - ".join(x for x in (it.workspace, it.title) if x) or "任务"
                tip += f"\n  {st.LABELS.get(it.state, it.state)} · {nm}"
            if more:
                tip += f"\n  另有 {more} 个"
        else:
            # 第一行是「工作空间 - 对话名」，托盘里也按同样顺序给全，避免两处对不上
            parts = [x for x in (self.status.workspace, self.status.title) if x]
            if parts:
                tip += " · " + " · ".join(parts)
        if self.cool is not None:
            line = f"散热：{self.cool.label}"
            if self.cool.has_deadline():
                line += f" · 剩 {fmt_dur(self.cool.remaining())}"
            tip += "\n" + line
        if self.thermal.temp is not None:
            tip += f"\n{self.thermal.label or '温度'} {self.thermal.temp:.0f}°"
        self.tray.setToolTip(tip)

    @staticmethod
    def _make_icon(color: QColor) -> QIcon:
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        g = QColor(color)
        g.setAlpha(70)
        p.setBrush(g)
        p.drawEllipse(QRectF(4, 4, 56, 56))
        p.setBrush(color)
        p.drawEllipse(QRectF(14, 14, 36, 36))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(PAL.tray_rim, 1.4))
        p.drawEllipse(QRectF(14, 14, 36, 36))
        p.end()
        return QIcon(pm)

    def _open_dir(self) -> None:
        st.ensure_dir()
        os.startfile(str(st.APP_DIR))  # noqa: S606

    def _start_demo(self) -> None:
        self._demo = True
        self._demo_idx = 0
        self._demo_t0 = time.time()
        self._push_demo()

    def _autostart_key(self):
        import winreg

        return winreg, r"Software\Microsoft\Windows\CurrentVersion\Run"

    def _autostart_enabled(self) -> bool:
        try:
            winreg, path = self._autostart_key()
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                winreg.QueryValueEx(k, "AISignalLight")
            return True
        except Exception:
            return False

    def _set_autostart(self, on: bool) -> None:
        try:
            winreg, path = self._autostart_key()
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
                if on:
                    if getattr(sys, "frozen", False):
                        # 打包后 sys.executable 就是 exe 本身，
                        # __file__ 指向临时解压目录，绝不能写进自启命令
                        cmd = f'"{sys.executable}"'
                    else:
                        pyw = Path(sys.executable).with_name("pythonw.exe")
                        exe = pyw if pyw.exists() else Path(sys.executable)
                        cmd = f'"{exe}" "{Path(__file__).resolve()}"'
                    winreg.SetValueEx(k, "AISignalLight", 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(k, "AISignalLight")
                    except FileNotFoundError:
                        pass
        except Exception:
            pass


def _single_instance() -> bool:
    """同一会话只允许一盏灯。

    没有这道锁时，重复双击 exe 会叠出两盏：两张卡片的数字错开几个像素，
    看起来像"重影"，实测踩过一次。
    """
    global _MUTEX
    if os.name != "nt":
        return True
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        _MUTEX = k32.CreateMutexW(None, 0, "Local\\AISignalLight_Widget")
        # 句柄刻意不释放：进程退出时系统自动回收
        return k32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:  # noqa: BLE001
        return True


def main() -> int:
    demo = "--demo" in sys.argv
    if not _single_instance():
        return 0
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())
    w = SignalLight(demo=demo)
    w.setWindowIcon(app_icon())
    w.show()

    def _shutdown() -> None:
        # 退出前必须发停止请求：agent 会自己还原风扇/电源，
        # 但"发请求"这一步只有界面能做。不发的话要等 90 秒 UI 心跳超时。
        if w.cool is not None:
            cooling_mod.request_stop()
        w.thermal.stop()
        w.metrics.close()

    app.aboutToQuit.connect(_shutdown)
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        st.ensure_dir()
        (st.APP_DIR / "app.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
