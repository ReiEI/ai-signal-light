# -*- coding: utf-8 -*-
"""散热面板 —— 把原 FanSpin 脚本的全部能力收进挂件里。

和原脚本的区别不是"把菜单搬进 GUI"，而是补上了脚本缺的那半环：
    脚本能下发命令，但**无法判断有没有效果**（本机 EC 不回读状态，
    作者原话是"只能靠听"）。这里把「性能比 + 温度趋势」放在同一屏，
    让"风扇到底有没有转起来"变成一个可以看的问题。

面板不持有任何控制权：按钮只是向 Cooling 发意图，真正的动作在 fan_agent。
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import cooling as cool
import powerplan
import state
import theme

# 面板与卡片共用一套调色板；主题切换时由 apply_theme() 重刷 QSS。
PAL = theme.PAL

FONT = "Microsoft YaHei UI"
MONO = "Consolas"


def _font(size: float, weight: int = QFont.Normal, family: str = FONT) -> QFont:
    f = QFont(family)
    f.setPointSizeF(float(size))
    f.setWeight(QFont.Weight(weight))
    return f


def fmt_dur(sec: float) -> str:
    sec = max(0, int(sec))
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m:02d}:{s:02d}"
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}"


def fmt_short(sec: float) -> str:
    sec = max(0, int(sec))
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    return f"{sec / 3600:.1f}h"


class Sparkline(QWidget):
    """温度趋势。存在的唯一理由：让人看见"开了风扇之后温度往哪走"。"""

    def __init__(self, capacity: int = 180, parent=None) -> None:
        super().__init__(parent)
        self._cap = capacity
        self._vals: list[float | None] = []
        self.setMinimumHeight(64)

    def push(self, v: float | None) -> None:
        self._vals.append(v)
        if len(self._vals) > self._cap:
            del self._vals[: len(self._vals) - self._cap]
        self.update()

    def clear(self) -> None:
        self._vals.clear()
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(0, 0, self.width(), self.height())
        p.setPen(Qt.NoPen)
        p.setBrush(PAL.track)
        p.drawRoundedRect(r, 8, 8)

        vals = [v for v in self._vals if v is not None]
        if len(vals) < 2:
            p.setPen(PAL.txt_faint)
            p.setFont(_font(7.5))
            p.drawText(r, Qt.AlignCenter, "采集中…")
            return

        lo = min(vals)
        hi = max(vals)
        if hi - lo < 6:  # 波动很小时别把噪声放大成山谷
            mid = (hi + lo) / 2
            lo, hi = mid - 3, mid + 3
        pad = 6.0
        w = r.width() - pad * 2
        h = r.height() - pad * 2
        step = w / max(1, len(self._vals) - 1)

        pts: list[QPointF] = []
        for i, v in enumerate(self._vals):
            if v is None:
                continue
            x = pad + i * step
            y = pad + h - (v - lo) / (hi - lo) * h
            pts.append(QPointF(x, y))
        if len(pts) < 2:
            return

        path = QPainterPath()
        path.moveTo(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)

        area = QPainterPath(path)
        area.lineTo(pts[-1].x(), pad + h)
        area.lineTo(pts[0].x(), pad + h)
        area.closeSubpath()
        grad = PAL.amber
        grad.setAlpha(46)
        p.setBrush(grad)
        p.setPen(Qt.NoPen)
        p.drawPath(area)

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(PAL.amber, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)

        last = pts[-1]
        p.setPen(Qt.NoPen)
        p.setBrush(PAL.amber)
        p.drawEllipse(QRectF(last.x() - 2.4, last.y() - 2.4, 4.8, 4.8))

        p.setFont(_font(7, QFont.Normal, MONO))
        p.setPen(PAL.txt_faint)
        p.drawText(QRectF(pad, 2, 40, 12), Qt.AlignLeft | Qt.AlignVCenter, f"{hi:.0f}°")
        p.drawText(QRectF(pad, r.height() - 14, 40, 12), Qt.AlignLeft | Qt.AlignVCenter, f"{lo:.0f}°")


class _PowerPoller:
    """powercfg 每次查询要拉一个子进程（~80ms），三条就是 240ms。
    放主线程会让面板掉帧，所以后台线程取、UI 只读缓存。"""

    def __init__(self) -> None:
        self.data: dict = {}
        self._busy = False

    def request(self) -> None:
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.data = powerplan.read_state()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._busy = False


class CoolingPanel(QDialog):
    def __init__(self, cooling, metrics, thermal, parent=None, on_change=None) -> None:
        super().__init__(parent)
        self.cooling = cooling
        self.metrics = metrics
        self.thermal = thermal
        self.on_change = on_change
        self._poller = _PowerPoller()
        self._restore_msg = ""
        self._last_power_req = 0.0
        self._spark_t = 0.0

        self.setWindowTitle("散热控制 · AI 工作信号灯")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setMinimumWidth(468)
        self.resize(492, 528)
        self.setStyleSheet(theme.panel_stylesheet(PAL))

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # ---- 标题 ----
        head = QHBoxLayout()
        title = QLabel("散热控制")
        title.setFont(_font(13, QFont.DemiBold))
        head.addWidget(title)
        head.addStretch(1)
        self.lb_cap = QLabel("")
        self.lb_cap.setObjectName("hint")
        head.addWidget(self.lb_cap)
        root.addLayout(head)

        # ---- 实时指标 ----
        block1 = QFrame()
        block1.setObjectName("block")
        l1 = QVBoxLayout(block1)
        l1.setContentsMargins(12, 10, 12, 10)
        l1.setSpacing(6)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.lb_cpu = QLabel("—")
        self.lb_perf = QLabel("—")
        self.lb_temp = QLabel("—")
        for i, (name, w) in enumerate((("CPU 占用", self.lb_cpu), ("性能比", self.lb_perf), ("设备温度", self.lb_temp))):
            k = QLabel(name)
            k.setObjectName("k")
            grid.addWidget(k, 0, i)
            w.setObjectName("big")
            grid.addWidget(w, 1, i)
        l1.addLayout(grid)
        self.spark = Sparkline()
        self.spark.setFixedHeight(66)
        l1.addWidget(self.spark)
        self.lb_temp_src = QLabel("")
        self.lb_temp_src.setObjectName("hint")
        l1.addWidget(self.lb_temp_src)
        root.addWidget(block1)

        # ---- 会话 ----
        block2 = QFrame()
        block2.setObjectName("block")
        l2 = QVBoxLayout(block2)
        l2.setContentsMargins(12, 10, 12, 10)
        l2.setSpacing(6)
        self.lb_sess = QLabel("当前没有散热会话")
        self.lb_sess.setFont(_font(12, QFont.DemiBold))
        l2.addWidget(self.lb_sess)
        self.lb_sess2 = QLabel("风扇交回 EC 自动调速")
        self.lb_sess2.setObjectName("hint")
        l2.addWidget(self.lb_sess2)

        row = QHBoxLayout()
        self.btn_stop = QPushButton("停止并恢复")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.clicked.connect(self._stop)
        self.btn_restore = QPushButton("还原电源设置")
        self.btn_restore.clicked.connect(self._restore)
        row.addWidget(self.btn_stop)
        row.addWidget(self.btn_restore)
        row.addStretch(1)
        l2.addLayout(row)
        root.addWidget(block2)

        # ---- 电源策略 ----
        block3 = QFrame()
        block3.setObjectName("block")
        l3 = QGridLayout(block3)
        l3.setContentsMargins(12, 10, 12, 10)
        l3.setHorizontalSpacing(18)
        l3.setVerticalSpacing(4)
        self.lb_scheme = QLabel("—")
        self.lb_minmax = QLabel("—")
        self.lb_coolpol = QLabel("—")
        for i, (name, w) in enumerate((
            ("电源方案", self.lb_scheme), ("处理器状态 最小/最大", self.lb_minmax), ("系统散热方式", self.lb_coolpol)
        )):
            k = QLabel(name)
            k.setObjectName("k")
            l3.addWidget(k, 0, i)
            w.setObjectName("v")
            l3.addWidget(w, 1, i)
        root.addWidget(block3)

        # ---- 动作 ----
        acts = QHBoxLayout()
        self.btn_ec = QPushButton("风扇全速")
        self.btn_ec.setObjectName("primary")
        self.btn_ec.clicked.connect(lambda: self._start(cool.MODE_EC))
        self.btn_burn = QPushButton("持续负载")
        self.btn_burn.clicked.connect(lambda: self._start(cool.MODE_BURN))
        self.btn_spin = QPushButton("电源锁定")
        self.btn_spin.clicked.connect(lambda: self._start(cool.MODE_SPIN))
        for b in (self.btn_ec, self.btn_burn, self.btn_spin):
            acts.addWidget(b)
        root.addLayout(acts)

        opts = QHBoxLayout()
        k1 = QLabel("时长")
        k1.setObjectName("k")
        opts.addWidget(k1)
        self.cb_min = QComboBox()
        for m in (10, 20, 30, 60, 120):
            self.cb_min.addItem(f"{m} 分钟", m)
        opts.addWidget(self.cb_min)
        k2 = QLabel("负载线程")
        k2.setObjectName("k")
        opts.addWidget(k2)
        self.sp_workers = QSpinBox()
        self.sp_workers.setRange(1, 32)
        opts.addWidget(self.sp_workers)
        opts.addStretch(1)
        root.addLayout(opts)

        # 三个按钮并列，必须当场说清"该点哪个"，否则用户只能一个个试
        tip = QLabel(
            "风扇全速 = 直接接管 EC，CPU 不必发热（首选）；"
            "持续负载 = 烧 CPU 换热风量（一定有效，但机器会烫）；"
            "电源锁定 = 把最小处理器状态顶到 100%（没有 EC 接口时的兜底）。"
        )
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        root.addWidget(tip)

        self.lb_msg = QLabel("")
        self.lb_msg.setObjectName("hint")
        self.lb_msg.setWordWrap(True)
        root.addWidget(self.lb_msg)

        # ---- 定时刷新 ----
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    # ---------- 生命周期 ----------

    def apply_theme(self) -> None:
        """主程序检测到系统深浅色变化后调用：QSS 不会自动跟着调色板变。"""
        self.setStyleSheet(theme.panel_stylesheet(PAL))
        self.update()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self.cooling.cap.refresh()
        self._poller.request()
        self._sync_controls()
        self._load_defaults()
        if not self._timer.isActive():
            self._timer.start(1000)
        self._tick()

    def _load_defaults(self) -> None:
        s = state.load_settings()
        want = int(s.get("cool_minutes") or cool.DEFAULT_MINUTES)
        idx = self.cb_min.findData(want)
        if idx < 0:
            idx = self.cb_min.findData(cool.DEFAULT_MINUTES)
        self.cb_min.setCurrentIndex(max(0, idx))
        workers = int(s.get("burn_workers") or 0)
        if workers <= 0:
            workers = max(4, (self.metrics.cores or 8) // 2)
        self.sp_workers.setValue(max(1, min(32, workers)))

    # ---------- 刷新 ----------

    def _sync_controls(self) -> None:
        cap = self.cooling.cap
        ok, why = cap.mode_state(cool.MODE_EC)
        self.btn_ec.setEnabled(ok and self.cooling.session is None)
        self.btn_ec.setToolTip(why if not ok else "直接接管 EC，风扇满速")
        self.btn_ec.setText("风扇全速" if ok else "风扇全速 · 不可用")

        for btn, mode in ((self.btn_burn, cool.MODE_BURN), (self.btn_spin, cool.MODE_SPIN)):
            ok2, why2 = cap.mode_state(mode)
            btn.setEnabled(ok2 and self.cooling.session is None)
            btn.setToolTip(why2)

        busy = self.cooling.session is not None
        self.btn_stop.setEnabled(busy)
        self.btn_restore.setEnabled(cool.BACKUP_FILE.exists())
        self.lb_cap.setText(f"{cap.ec_detail}　·　{'管理员' if cap.admin else '普通用户'}")

        if not busy:
            self.lb_sess.setText("当前没有散热会话")
            self.lb_sess2.setText("风扇与电源策略均处于系统默认")

    def _tick(self) -> None:
        m = self.metrics
        self.lb_cpu.setText(f"{m.cpu:.0f}%")
        perf = m.perf_pct
        self.lb_perf.setText(f"{perf:.0f}%" if perf else "—")
        t = self.thermal.temp
        self.lb_temp.setText(f"{t:.0f}°" if t is not None else "—")
        self.lb_temp_src.setText(
            f"温度来源：{self.thermal.label or '—'}" + (f"（{self.thermal.source}）" if self.thermal.source else "")
        )
        now = time.time()
        if now - self._spark_t >= 1.0:
            self._spark_t = now
            self.spark.push(t)
        if now - self._last_power_req >= 8.0:
            self._last_power_req = now
            self._poller.request()

        p = self._poller.data
        if p:
            self.lb_scheme.setText(p.get("scheme") or "—")
            mn, mx = p.get("min"), p.get("max")
            self.lb_minmax.setText(
                f"{mn['ac']}% / {mx['ac']}%" if isinstance(mn, dict) and isinstance(mx, dict) else "—"
            )
            coolpol = p.get("cool")
            if isinstance(coolpol, dict):
                self.lb_coolpol.setText("主动（优先风扇）" if coolpol.get("ac") == 1 else "被动（优先降频）")
                self.lb_coolpol.setStyleSheet(f"color:{theme.hex_str(PAL.txt)}")
            else:
                self.lb_coolpol.setText("本机未暴露")
                self.lb_coolpol.setStyleSheet(f"color:{theme.hex_str(PAL.txt_faint)}")

        if self._restore_msg:
            self.lb_msg.setText(self._restore_msg)
            self._restore_msg = ""
            self._poller.request()
            self._after_action()

        s = self.cooling.session
        if s:
            total = s.elapsed()
            if s.has_deadline():
                self.lb_sess.setText(f"{s.label} · 已运行 {fmt_dur(total)} · 剩余 {fmt_dur(s.remaining())}")
            else:
                self.lb_sess.setText(f"{s.label} · 已运行 {fmt_dur(total)}")
            extra = s.note or ""
            if s.mode == cool.MODE_EC and s.cycles:
                extra = f"{extra} · 已重置 {s.cycles} 次".strip(" ·")
            if s.mode == cool.MODE_BURN and s.workers:
                extra = f"{s.workers} 线程 · 优先级低于正常"
            if s.error:
                extra = f"{extra} · {s.error}"
            self.lb_sess2.setText(extra or "运行中")
        self._sync_controls()

    # ---------- 动作 ----------

    def _start(self, mode: str) -> None:
        minutes = int(self.cb_min.currentData() or 30)
        workers = int(self.sp_workers.value())
        ok, msg = self.cooling.start(mode, minutes, workers)
        self.lb_msg.setText(msg)
        self._after_action()

    def _stop(self) -> None:
        ok, msg = self.cooling.stop()
        self.lb_msg.setText(msg)
        self._after_action()

    def _restore(self) -> None:
        # 还原电源要提权，UAC 会阻塞调用线程；放后台，结果由 _tick 取
        self.lb_msg.setText("正在还原电源设置…")
        self.btn_restore.setEnabled(False)
        self._restore_msg = ""

        def run() -> None:
            self._restore_msg = self.cooling.restore_power()[1]

        threading.Thread(target=run, daemon=True).start()

    def _after_action(self) -> None:
        self._poller.request()
        self._sync_controls()
        if self.on_change:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001
                pass

    def closeEvent(self, e) -> None:  # noqa: N802
        """关面板不停止会话 —— 这是两件事。会话在挂件上仍有入口（右键/散热条）。"""
        self._timer.stop()
        super().closeEvent(e)
