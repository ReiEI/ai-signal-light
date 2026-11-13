# -*- coding: utf-8 -*-
"""散热会话层：能力探测、执行体拉起、状态回读、崩溃后对账。

为什么要把「控制」和「界面」拆开（这是整个模块的核心设计）：

  * 电源策略与 EC 设备都是**特权操作**，而挂件是一个常驻、置顶、可拖拽的
    桌面小工具。让它以管理员身份常驻，等于给一个装饰性窗口开了全机写权限。
  * 界面进程必须能随时被杀而不留下后遗症。所以真正的施力方是独立的
    `fan_agent` 进程，界面只负责「发意图、看结果」。

通信不用管道/socket，用三个文件：会话文件（agent→UI，含心跳）、停止标志
（UI→agent）、UI 心跳（agent 判断界面是否还活着）。理由是进程间文件是
**唯一在任一方崩溃后仍然可读**的通道 —— 崩溃恢复恰恰是这个功能最需要的性质。

三条不可让步的约束：
  1. 任何持久化改动（电源策略）必须先落盘备份，且备份带截止时间。
  2. agent 每 1 秒写心跳；心跳对不上就视为进程已死，界面不能装作在跑。
  3. 界面消失后 agent 必须自行收手（UI 心跳超时），不能让风扇/频率无人看管。
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import ecfan
import powerplan
import state as st

COOL_DIR = st.APP_DIR / "cooling"
SESSION_FILE = COOL_DIR / "session.json"
STOP_FILE = COOL_DIR / "stop.flag"
BEAT_FILE = COOL_DIR / "ui.beat"
BACKUP_FILE = COOL_DIR / "power_backup.json"
LAST_FILE = COOL_DIR / "last.json"

MODE_EC = "ec"
MODE_BURN = "burn"
MODE_SPIN = "spin"
MODE_RESTORE = "restore"

MODE_LABELS = {
    MODE_EC: "风扇全速",
    MODE_BURN: "持续负载",
    MODE_SPIN: "电源锁定",
    MODE_RESTORE: "还原电源",
}
MODE_HINTS = {
    MODE_EC: "直接接管 EC，风扇满速，CPU 不必发热",
    MODE_BURN: "制造真实 CPU 负载，一定有效，但机器会烫",
    MODE_SPIN: "锁最小处理器状态 100%，靠发热带动风扇",
}

# 会话心跳超过这个秒数就认为 agent 已死。agent 每 1s 写一次，留 8 倍余量。
SESSION_TIMEOUT = 8.0
# UI 心跳超过这个秒数，agent 自行停止（界面关了就不该继续控风扇）
UI_TIMEOUT = 90.0

DEFAULT_MINUTES = 30
MAX_MINUTES = 180

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ---------------- 会话模型 ----------------

class Session:
    """手写类，不用 dataclass：本模块会被 hook 之外的进程导入，保持轻量习惯。"""

    def __init__(
        self,
        mode: str = "",
        pid: int = 0,
        started: float = 0.0,
        deadline: float = 0.0,
        beat: float = 0.0,
        cycles: int = 0,
        workers: int = 0,
        note: str = "",
        error: str = "",
    ) -> None:
        self.mode = mode
        self.pid = pid
        self.started = started
        self.deadline = deadline
        self.beat = beat
        self.cycles = cycles
        self.workers = workers
        self.note = note
        self.error = error

    @property
    def label(self) -> str:
        return MODE_LABELS.get(self.mode, self.mode or "散热")

    def elapsed(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        return max(0.0, now - self.started) if self.started else 0.0

    def remaining(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        return max(0.0, self.deadline - now) if self.deadline else 0.0

    def has_deadline(self) -> bool:
        return bool(self.deadline and self.deadline - self.started > 1)

    def live(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return bool(self.beat) and (now - self.beat) <= SESSION_TIMEOUT

    def __repr__(self) -> str:
        return (
            f"Session(mode={self.mode!r}, pid={self.pid}, elapsed={self.elapsed():.0f}s, "
            f"cycles={self.cycles}, live={self.live()})"
        )


def ensure_dir() -> None:
    try:
        COOL_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def read_session() -> Session | None:
    try:
        d = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None

    def num(key: str, default: float = 0.0) -> float:
        try:
            return float(d.get(key) or default)
        except (TypeError, ValueError):
            return default

    return Session(
        mode=str(d.get("mode") or ""),
        pid=int(num("pid")),
        started=num("started"),
        deadline=num("deadline"),
        beat=num("beat"),
        cycles=int(num("cycles")),
        workers=int(num("workers")),
        note=str(d.get("note") or ""),
        error=str(d.get("error") or ""),
    )


def is_live(s: Session | None, now: float | None = None) -> bool:
    return s is not None and s.live(now)


def request_stop() -> None:
    """写停止标志。agent 每 0.4s 看一次，不需要杀进程 ——
    提权进程本来也杀不掉（非同一完整性级别），只能协商。"""
    ensure_dir()
    try:
        STOP_FILE.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def touch_ui() -> None:
    ensure_dir()
    try:
        BEAT_FILE.write_text(f"{time.time():.3f}", encoding="utf-8")
    except OSError:
        pass


def clear_runtime_files() -> None:
    for p in (SESSION_FILE, STOP_FILE, LAST_FILE):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def read_last() -> dict | None:
    """读 agent 上次退出的说明。用于把「启动失败」的原因显示出来。"""
    try:
        d = json.loads(LAST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


# ---------------- 能力探测 ----------------

class Capability:
    """本机到底能做什么。探测一次，缓存结果，可手动重探。

    界面绝不能出现「按钮点下去才发现不可用」—— 所有禁用态都来自这里。
    """

    def __init__(self, probe: bool = True) -> None:
        self.ec = ecfan.MISSING
        self.ec_detail = "未探测"
        self.admin = False
        self.power = {}
        self.error = ""
        self.probed_ms = 0
        if probe:
            self.refresh()

    def refresh(self, deep: bool = False) -> None:
        """deep=False 只探 EC 与权限（约 1ms）；deep=True 才去读电源策略。

        为什么分开：`powercfg` 是三次子进程调用（约 250ms），放在挂件启动路径上
        会让首屏明显变慢，而冷启动时唯一需要知道的是"菜单里的项该不该灰"。
        电源详情等面板打开时再读。
        """
        started = time.time()
        try:
            self.ec, self.ec_detail = ecfan.EcFan().probe()
        except Exception as e:  # noqa: BLE001
            self.ec, self.ec_detail = ecfan.ERROR, repr(e)
        self.admin = powerplan.is_admin()
        if deep or self.power:
            try:
                self.power = powerplan.read_state()
            except Exception as e:  # noqa: BLE001
                self.power = {}
                self.error = repr(e)
        self.probed_ms = int((time.time() - started) * 1000)

    @property
    def ec_ok(self) -> bool:
        return self.ec == ecfan.OK

    @property
    def ec_needs_admin(self) -> bool:
        return self.ec == ecfan.DENIED

    def mode_state(self, mode: str) -> tuple[bool, str]:
        """返回 (可用?, 不可用原因)。不可用原因直接显示给用户，不写"暂不可用"。"""
        if mode == MODE_EC:
            if self.ec == ecfan.OK:
                return True, "EC 接口可读写"
            if self.ec == ecfan.DENIED:
                return True, "需要管理员权限（会弹 UAC）"
            return False, ecfan.STATUS_LABELS.get(self.ec, ecfan.STATUS_LABELS[ecfan.ERROR])
        if mode == MODE_SPIN:
            # 电源详情没读时就按"标准设置一定存在"处理，真读不到会在启动后报错
            if self.power and self.power.get("min") is None:
                return False, "读不到处理器电源策略"
            if not self.admin:
                return True, "需要管理员权限（会弹 UAC）"
            return True, "可直接写入电源策略"
        if mode == MODE_BURN:
            return True, "无需特权"
        return False, "未知模式"

    def needs_admin(self, mode: str) -> bool:
        if mode == MODE_SPIN:
            return True
        if mode == MODE_EC:
            return self.ec != ecfan.OK
        return False

    def summary(self) -> str:
        ok, why = self.mode_state(MODE_EC)
        head = "EC 可用" if ok else f"EC 不可用（{why}）"
        return head + ("·管理员" if self.admin else "·普通用户")


# ---------------- 控制器 ----------------

class Cooling:
    """界面侧的散热控制器。所有方法都不阻塞 UI（拉起 UAC 放到线程里）。"""

    def __init__(self) -> None:
        self.cap = Capability(probe=True)
        self.session: Session | None = None
        self.notice = ""            # 一次性提示，UI 取走后清空
        self._notice_ts = 0.0
        self._pending_since = 0.0
        self._pending_mode = ""
        self._launch_error = ""
        self._lock = threading.Lock()
        self._last_beat = 0.0
        self._stale_since = 0.0

    # ---- 提示 ----

    def _notify(self, text: str) -> None:
        self.notice = text
        self._notice_ts = time.time()

    def post_notice(self, text: str) -> None:
        """从后台线程投递一条提示，交给界面下一次 poll 取走。
        直接操作 Qt 控件是跨线程错误的，这里只写字符串。"""
        self._notify(text)

    def take_notice(self) -> str:
        """取走一次性提示。超过 12 秒的旧提示不再显示，避免界面重绘时反复弹。"""
        if self.notice and time.time() - self._notice_ts < 12.0:
            t = self.notice
            self.notice = ""
            return t
        self.notice = ""
        return ""

    # ---- 轮询 ----

    def poll(self) -> Session | None:
        """每次界面刷新调用。负责：读会话、写 UI 心跳、识别启动失败。"""
        ensure_dir()
        s = read_session()
        live = is_live(s)
        self.session = s if live else None

        # 会话文件在但没有心跳：agent 被强杀了。留 30 秒再删，
        # 避免一次 IO 卡顿就把它当尸体清掉（那样下次启动会以为现场是干净的）。
        if s is not None and not live:
            if self._stale_since <= 0:
                self._stale_since = time.time()
            elif time.time() - self._stale_since > 30.0:
                clear_runtime_files()
                self._stale_since = 0.0
        else:
            self._stale_since = 0.0

        if self.session is not None:
            # UI 心跳决定 agent 会不会在界面消失后自行收手。3 秒一次足够，
            # 每帧都写文件是没必要的磁盘 IO。
            now = time.time()
            if now - self._last_beat > 3.0:
                self._last_beat = now
                touch_ui()
            self._pending_since = 0.0
        elif self._pending_since:
            # 拉起之后迟迟没有会话：要么被 UAC 拒了，要么 agent 一启动就失败
            if time.time() - self._pending_since > 2.5:
                mode, self._pending_mode = self._pending_mode, ""
                self._pending_since = 0.0
                msg = self._launch_error
                last = read_last()
                if not msg and last and last.get("message"):
                    msg = str(last["message"])
                self._notify(msg or f"{MODE_LABELS.get(mode, mode)}启动失败")
                clear_runtime_files()
        return self.session

    # ---- 启动 ----

    def _agent_target(self) -> tuple[str, list[str]] | None:
        """返回 (可执行文件, 前置参数)。

        frozen：dist/fan_agent.exe（独立小体积执行体，不含 PySide6）。
        源码：当前解释器 + fan_agent.py。
        """
        root = Path(__file__).resolve().parent.parent
        if getattr(sys, "frozen", False):
            exe = Path(sys.executable).with_name("fan_agent.exe")
            if exe.exists():
                return str(exe), []
            return None
        py = Path(sys.executable)
        script = root / "fan_agent.py"
        if not script.exists():
            return None
        return str(py), [str(script)]

    def _agent_args(self, mode: str, minutes: int, workers: int) -> list[str]:
        args = [
            "--mode", mode,
            "--minutes", str(int(minutes)),
            "--home", str(st.APP_DIR),
            "--cycle", f"{float(st.load_settings().get('cool_cycle') or 8.9):.2f}",
            "--watch-ui",
        ]
        if workers:
            args += ["--workers", str(int(workers))]
        return args

    def start(self, mode: str, minutes: int | None = None, workers: int | None = None) -> tuple[bool, str]:
        if mode == MODE_RESTORE:
            return self.restore_power()
        ok, why = self.cap.mode_state(mode)
        if not ok:
            return False, why
        if self.session is not None:
            return False, f"已有散热会话在运行（{self.session.label}）"

        settings = st.load_settings()
        minutes = int(minutes if minutes is not None else settings.get("cool_minutes") or DEFAULT_MINUTES)
        minutes = max(1, min(MAX_MINUTES, minutes))
        workers = int(workers if workers is not None else settings.get("burn_workers") or 0)

        target = self._agent_target()
        if target is None:
            return False, "散热组件缺失（fan_agent.exe）"
        exe, prefix = target

        ensure_dir()
        try:
            STOP_FILE.unlink(missing_ok=True)
            LAST_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        touch_ui()

        args = prefix + self._agent_args(mode, minutes, workers)
        need_admin = self.cap.needs_admin(mode)
        self._pending_since = time.time()
        self._pending_mode = mode
        self._launch_error = ""

        def run() -> None:
            try:
                if need_admin:
                    ok2, msg = _shell_runas(exe, args)
                else:
                    subprocess.Popen(
                        [exe, *args], cwd=str(Path(exe).parent),
                        creationflags=_NO_WINDOW, close_fds=True,
                    )
                    ok2, msg = True, ""
            except Exception as e:  # noqa: BLE001
                ok2, msg = False, f"启动失败：{e}"
            if not ok2:
                with self._lock:
                    self._launch_error = msg
                    self._pending_since = 0.0
                self._notify(msg)

        threading.Thread(target=run, name="cool-launch", daemon=True).start()
        hint = "需要确认管理员权限…" if need_admin else "正在启动…"
        return True, f"{MODE_LABELS.get(mode, mode)} · {hint}"

    def stop(self) -> tuple[bool, str]:
        if self.session is None:
            s = read_session()
            if not is_live(s):
                clear_runtime_files()
                return False, "当前没有散热会话"
        request_stop()
        return True, "正在停止…"

    def restore_power(self) -> tuple[bool, str]:
        """还原处理器电源设置。已在管理员下则直接做，否则拉起提权执行体。"""
        if not BACKUP_FILE.exists():
            return False, "没有需要还原的电源设置"
        if powerplan.is_admin():
            ok, msg = powerplan.restore(BACKUP_FILE)
            self.cap.refresh()
            return ok, msg
        target = self._agent_target()
        if target is None:
            return False, "散热组件缺失（fan_agent.exe）"
        exe, prefix = target
        args = prefix + ["--mode", MODE_RESTORE, "--home", str(st.APP_DIR)]
        ok, msg = _shell_runas(exe, args)
        return ok, msg if not ok else "已请求还原（需要管理员确认）"

    # ---- 启动对账 ----

    def reconcile(self) -> str:
        """界面启动时调用，处理上一次没收尾的现场。

        判断依据只有一条：**有没有活着的会话**。
        没有会话但留着电源备份 = 上次是被强杀/断电，必须还原。
        这不是"可能出问题"，是 CPU 会被永久钉在 100% 最小状态。
        """
        s = read_session()
        if is_live(s):
            self.session = s
            touch_ui()
            return f"adopt:{s.mode}"

        if s is not None:
            clear_runtime_files()

        if BACKUP_FILE.exists():
            ok, msg = self.restore_power()
            if ok:
                self._notify(f"发现未收尾的电源改动，{msg}")
                return f"restored:{msg}"
            self._notify(f"电源改动未还原：{msg}")
            return f"restore_failed:{msg}"
        return "clean"


def _shell_runas(exe: str, args: list[str]) -> tuple[bool, str]:
    """以管理员身份拉起执行体。返回 (成功?, 说明)。

    ShellExecuteW 的返回值 >32 才算成功；5 = 用户在 UAC 上点了"否"。
    这里不尝试用 CreateProcess 绕过 —— 提权必须经过系统同意，没有捷径。
    """
    if os.name != "nt":
        return False, "仅支持 Windows"
    try:
        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteW.restype = ctypes.c_void_p
        params = " ".join(f'"{a}"' if " " in a else a for a in args)
        ret = shell32.ShellExecuteW(None, "runas", exe, params, str(Path(exe).parent), 1)
    except Exception as e:  # noqa: BLE001
        return False, f"提权失败：{e}"
    code = int(ret or 0)
    if code > 32:
        return True, ""
    if code == 5:
        return False, "已取消管理员授权"
    return False, f"无法启动执行体（ShellExecute={code}）"
