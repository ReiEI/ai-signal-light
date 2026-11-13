# -*- coding: utf-8 -*-
"""散热执行体（fan_agent）—— 真正动手的那个进程。

它被界面以两种方式拉起：
    普通权限  持续负载（不需要任何特权，只是烧 CPU）
    管理员    电源锁定 / EC 全速（EC 被拒时）

设计约束（每一条都对应一种真实的失败方式）：

  1. **不能用 GUI 进程做这件事。** 挂件是常驻置顶小工具，让它常驻管理员权限
     等于给一个装饰窗口开全机写权限。所以控制逻辑放在这个独立进程里，
     生命周期短、职责单一、随时可以被抛弃。
  2. **必须能被界面"温和地"叫停。** 提权进程和非提权界面不在同一完整性级别，
     界面杀不掉它。所以停止只能靠 `stop.flag` 文件协商。
  3. **必须在界面消失后自己收手。** 界面每 3 秒写一次 `ui.beat`；这里超过 90 秒
     读不到新心跳就认为界面已死，自行还原退出。否则关掉挂件后风扇会一直转。
  4. **必须在任何退出路径上还原。** try/finally + 信号处理 + atexit 三重兜底，
     包括 Ctrl+C、异常、截止时间到、被要求停止。
  5. **必须能被下一个实例识别。** 退出时删会话文件，异常退出也留 `last.json`
     说明原因，界面据此显示"为什么没起来"，而不是干瞪着没反应。

退出码：
    0 正常结束（含到期、被要求停止）
    2 参数错误
    3 EC 设备不存在
    4 EC 设备拒绝访问（需要提权）
    5 电源策略写入失败
    6 已有散热会话在运行
    7 其它初始化失败
"""

from __future__ import annotations

import argparse
import atexit
import ctypes
import hashlib
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))

import ecfan  # noqa: E402
import powerplan  # noqa: E402

MODE_EC = "ec"
MODE_BURN = "burn"
MODE_SPIN = "spin"
MODE_RESTORE = "restore"

EXIT_OK = 0
EXIT_ARGS = 2
EXIT_NO_DEVICE = 3
EXIT_DENIED = 4
EXIT_POWER = 5
EXIT_BUSY = 6
EXIT_INIT = 7

SESSION_TIMEOUT = 8.0
UI_TIMEOUT = 90.0
UI_GRACE = 25.0      # 刚启动时界面可能还没开始写心跳，给一段宽限
TICK = 0.4           # 主循环周期：决定"按下停止"到"真的停"的延迟上限
BEAT_EVERY = 1.0

_MUTEX = None


# ---------------- 文件协议 ----------------

class Paths:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.dir = home / "cooling"
        self.session = self.dir / "session.json"
        self.stop = self.dir / "stop.flag"
        self.ui = self.dir / "ui.beat"
        self.backup = self.dir / "power_backup.json"
        self.last = self.dir / "last.json"

    def ensure(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


class SessionWriter:
    """每 1 秒覆盖一次会话文件。界面靠 beat 判断这个进程是否还活着。"""

    def __init__(self, paths: Paths, mode: str) -> None:
        self.paths = paths
        self.mode = mode
        self.pid = os.getpid()
        self.started = time.time()
        self.deadline = 0.0
        self.cycles = 0
        self.workers = 0
        self.note = ""
        self.error = ""

    def write(self) -> None:
        payload = {
            "mode": self.mode,
            "pid": self.pid,
            "started": self.started,
            "deadline": self.deadline,
            "beat": time.time(),
            "cycles": self.cycles,
            "workers": self.workers,
            "note": self.note,
            "error": self.error,
        }
        try:
            self.paths.ensure()
            tmp = self.paths.session.with_suffix(f".{self.pid}.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.paths.session)
        except OSError:
            pass

    def clear(self) -> None:
        try:
            self.paths.session.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            self.paths.stop.unlink(missing_ok=True)
        except OSError:
            pass


def _write_last(paths: Paths, mode: str, code: int, message: str) -> None:
    try:
        paths.ensure()
        paths.last.write_text(
            json.dumps(
                {"mode": mode, "code": code, "message": message, "ts": time.time()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def _read_session(paths: Paths) -> dict | None:
    try:
        d = json.loads(paths.session.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _ui_alive(paths: Paths, since: float) -> bool:
    try:
        raw = paths.ui.read_text(encoding="utf-8").strip()
        beat = float(raw)
    except (OSError, ValueError):
        # 没有心跳文件：可能是命令行直接启动的，前 25 秒放行，之后从严
        return time.time() - since < UI_GRACE
    return (time.time() - beat) <= UI_TIMEOUT


# ---------------- 三种施力方式 ----------------

class EcRunner:
    """EC 全速。核心是每 cycle 秒重置一次除尘计时，否则 9 秒后风扇自己停。"""

    def __init__(self, cycle: float = 8.9) -> None:
        self.cycle = max(2.0, min(30.0, float(cycle)))
        self.fan = ecfan.EcFan()
        self.next_at = 0.0
        self.fail = 0
        self.cycles = 0
        self.error = ""

    def prepare(self) -> tuple[bool, int, str]:
        status, detail = self.fan.probe()
        if status == ecfan.OK:
            return True, EXIT_OK, detail
        if status == ecfan.DENIED:
            return False, EXIT_DENIED, f"EC 接口需要管理员权限（{detail}）"
        if status == ecfan.MISSING:
            return False, EXIT_NO_DEVICE, "本机没有 EnergyDrv 风扇接口"
        return False, EXIT_NO_DEVICE, f"EC 接口不可用（{detail}）"

    def tick(self, now: float) -> None:
        if now < self.next_at:
            return
        self.next_at = now + self.cycle
        # 先回正常再切全速：这两步就是"重置 EC 除尘计时"的全部内容
        self.fan.set_mode(ecfan.MODE_NORMAL)
        time.sleep(0.01)
        ok, msg = self.fan.set_mode(ecfan.MODE_FAST)
        if ok:
            self.fail = 0
            self.cycles += 1
        else:
            self.fail += 1
            self.error = msg
            if self.fail >= 3:
                raise RuntimeError(f"EC 连续三次控制失败：{msg}")

    def restore(self) -> str:
        ok, msg = self.fan.set_mode(ecfan.MODE_NORMAL)
        return "风扇已交回 EC 自动调速" if ok else f"风扇还原失败（{msg}），约 9 秒后 EC 会自行收停"


class BurnRunner:
    """持续负载：一个进程 N 个线程。

    为什么线程能跑满多核：Python 的 GIL 在 hashlib 这类 C 实现里会释放，
    所以 N 个线程各自做 sha256 就能真的占住 N 个核（本机实测 8 线程 ≈ 7 倍
    单线程吞吐）。用 multiprocessing 反而糟糕：PyInstaller 单文件每次子进程
    都要重新解包整个运行时。
    """

    def __init__(self, workers: int) -> None:
        self.workers = max(1, min(32, int(workers)))
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.iters = 0
        self.cycles = 0

    def prepare(self) -> tuple[bool, int, str]:
        return True, EXIT_OK, f"{self.workers} 线程"

    def start(self) -> None:
        for _ in range(self.workers):
            t = threading.Thread(target=self._work, daemon=True)
            t.start()
            self._threads.append(t)

    def _work(self) -> None:
        # 降一级优先级：让机器还能正常用，空闲时照样吃满
        try:
            k32 = ctypes.windll.kernel32
            k32.SetThreadPriority(k32.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL
        except Exception:  # noqa: BLE001
            pass
        # 每个线程独占一块缓冲区，避免 8 个线程抢同一块 cache line
        buf = os.urandom(1 << 20)
        n = 0
        while not self._stop.is_set():
            hashlib.sha256(buf).digest()
            n += 1
            if n % 32 == 0:
                self.iters = n

    def tick(self, now: float) -> None:
        return

    def restore(self) -> str:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=0.6)
        self._threads.clear()
        return "负载线程已停止"


class SpinRunner:
    """电源锁定：把最小处理器状态顶到 100%，靠持续发热带动风扇。

    比 EC 直控差：它不提高风量，只提高发热。所以只作为没有 EC 接口时的兜底。
    必须在改之前落盘备份 —— 这是唯一会跨进程存活的副作用。
    """

    def __init__(self, paths: Paths, duration: float) -> None:
        self.paths = paths
        self.duration = duration
        self.applied = False
        self.cycles = 0
        self.original_min: dict | None = None
        self.original_max: dict | None = None

    def prepare(self) -> tuple[bool, int, str]:
        self.original_min = powerplan.query(powerplan.SET_MIN)
        self.original_max = powerplan.query(powerplan.SET_MAX)
        if self.original_min is None:
            return False, EXIT_POWER, "读不到当前的最小处理器状态，已放弃（不盲改）"
        backup = {
            "created": time.time(),
            "deadline": time.time() + max(60.0, self.duration),
            "scheme": powerplan.scheme(),
            "min": self.original_min,
            "max": self.original_max,
        }
        if not powerplan.save_backup(self.paths.backup, backup):
            return False, EXIT_POWER, "备份写入失败，已放弃（不敢不留后路就改电源）"
        try:
            powerplan.set_value(powerplan.SET_MIN, 100, 100)
            powerplan.activate()
            self.applied = True
        except powerplan.PowerError as e:
            self.restore()
            return False, EXIT_POWER, str(e)
        return True, EXIT_OK, "最小处理器状态 = 100%"

    def tick(self, now: float) -> None:
        return

    def restore(self) -> str:
        if not self.applied and not self.paths.backup.exists():
            return "无需还原"
        ok, msg = powerplan.restore(self.paths.backup)
        self.applied = False
        return msg if ok else f"电源还原失败：{msg}"


# ---------------- 主流程 ----------------

class Agent:
    def __init__(self, paths: Paths, mode: str, minutes: float, workers: int, cycle: float, watch_ui: bool) -> None:
        self.paths = paths
        self.mode = mode
        self.watch_ui = watch_ui
        self.minutes = max(0.0, float(minutes))
        self.workers = workers
        self.cycle = cycle
        self.runner = None
        self.sw = SessionWriter(paths, mode)
        self._stopping = threading.Event()
        self._restored = False

    # ---- 准备 ----

    def prepare(self) -> tuple[bool, int, str]:
        duration = self.minutes * 60.0
        if self.mode == MODE_EC:
            self.runner = EcRunner(self.cycle)
        elif self.mode == MODE_BURN:
            self.runner = BurnRunner(self.workers or max(4, (os.cpu_count() or 8) // 2))
        elif self.mode == MODE_SPIN:
            if not powerplan.is_admin():
                return False, EXIT_DENIED, "电源策略写入需要管理员权限"
            self.runner = SpinRunner(self.paths, duration)
        else:
            return False, EXIT_ARGS, f"未知模式 {self.mode}"

        ok, code, msg = self.runner.prepare()
        if not ok:
            return False, code, msg

        self.sw.deadline = time.time() + duration if duration > 0 else 0.0
        self.sw.note = msg
        self.sw.workers = getattr(self.runner, "workers", 0)
        if hasattr(self.runner, "start"):
            self.runner.start()
        self.sw.write()
        return True, EXIT_OK, msg

    # ---- 主循环 ----

    def run(self) -> tuple[int, str]:
        ok, code, msg = self.prepare()
        if not ok:
            _write_last(self.paths, self.mode, code, msg)
            return code, msg

        self.sw.write()
        last_beat = time.time()
        reason = "到期自动停止"
        try:
            while True:
                now = time.time()
                if self._stopping.is_set():
                    reason = "已收到停止请求"
                    break
                if self.paths.stop.exists():
                    reason = "已收到停止请求"
                    break
                if self.sw.deadline and now >= self.sw.deadline:
                    reason = "已到设定时长"
                    break
                if self.watch_ui and not _ui_alive(self.paths, self.sw.started):
                    reason = "界面已退出，自动收手"
                    break

                self.runner.tick(now)

                if now - last_beat >= BEAT_EVERY:
                    last_beat = now
                    self.sw.cycles = getattr(self.runner, "cycles", 0)
                    self.sw.write()

                time.sleep(TICK)
        except KeyboardInterrupt:
            reason = "已被 Ctrl+C 中断"
        except Exception as e:  # noqa: BLE001
            self.sw.error = repr(e)
            reason = f"异常终止：{e}"
            _write_last(self.paths, self.mode, EXIT_INIT, reason)
            return EXIT_INIT, reason
        finally:
            self.restore()

        _write_last(self.paths, self.mode, EXIT_OK, reason)
        return EXIT_OK, reason

    def restore(self) -> None:
        if self._restored:
            return
        self._restored = True
        msg = ""
        try:
            if self.runner is not None:
                msg = self.runner.restore()
        except Exception as e:  # noqa: BLE001
            msg = f"还原异常：{e}"
        self.sw.clear()
        if msg:
            print(msg, flush=True)

    def stop(self) -> None:
        self._stopping.set()


def _single_instance() -> bool:
    """同一时刻只允许一个执行体。两个 agent 抢 EC 会让风扇在两个模式间抖。"""
    global _MUTEX
    if os.name != "nt":
        return True
    try:
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        _MUTEX = k32.CreateMutexW(None, 0, "Local\\AISignalLight_Agent")
        return k32.GetLastError() != 183
    except Exception:  # noqa: BLE001
        return True


def _existing_session(paths: Paths) -> bool:
    d = _read_session(paths)
    if not d:
        return False
    try:
        beat = float(d.get("beat") or 0.0)
    except (TypeError, ValueError):
        return False
    if beat and time.time() - beat <= SESSION_TIMEOUT:
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(add_help=True, description="AI 工作信号灯 · 散热执行体")
    ap.add_argument("--mode", required=True, choices=[MODE_EC, MODE_BURN, MODE_SPIN, MODE_RESTORE])
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--cycle", type=float, default=8.9)
    ap.add_argument("--home", default="")
    ap.add_argument("--watch-ui", action="store_true")
    args = ap.parse_args(argv)

    home = Path(args.home) if args.home else (
        Path(os.environ.get("AISIGNAL_HOME") or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AIStatusLight")
    )
    paths = Paths(home)
    paths.ensure()

    # 只还原电源这一件事，不需要单实例保护，也不长驻
    if args.mode == MODE_RESTORE:
        ok, msg = powerplan.restore(paths.backup)
        _write_last(paths, MODE_RESTORE, EXIT_OK if ok else EXIT_POWER, msg)
        return EXIT_OK if ok else EXIT_POWER

    if not _single_instance() or _existing_session(paths):
        msg = "已有散热会话在运行"
        _write_last(paths, args.mode, EXIT_BUSY, msg)
        return EXIT_BUSY

    # 清掉上一次的残留标志，否则新会话一启动就被自己的 stop 文件叫停
    try:
        paths.stop.unlink(missing_ok=True)
    except OSError:
        pass

    agent = Agent(paths, args.mode, args.minutes, args.workers, args.cycle, args.watch_ui)
    atexit.register(agent.restore)

    def _on_signal(_sig, _frm):
        agent.stop()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        fn = getattr(signal, name, None)
        if fn is not None:
            try:
                signal.signal(fn, _on_signal)
            except (OSError, ValueError):
                pass

    code, msg = agent.run()
    return code


if __name__ == "__main__":
    sys.exit(main())
